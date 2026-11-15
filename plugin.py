# -*- coding: utf-8 -*-
"""桌面宠物适配器插件。

提供 DesktopPetAdapter 实现：
- GUI 线程管理（后台线程中的 QApplication）
- 双向消息转换（GUI <-> 核心）
- 与 DayNightService、SystemMonitorService、ClipboardWatcherService 集成
- 健康检查覆盖（无传输依赖，始终健康）
"""

from __future__ import annotations

import asyncio
import queue
import threading
from collections import deque
from typing import Any

from src.app.plugin_system.api.log_api import get_logger
from src.app.plugin_system.base import BasePlugin, BaseAdapter
from src.core.components.loader import register_plugin
from src.core.prompt import (
    STREAM_BUCKET_PREFIX,
    SystemReminderConsumeType,
    SystemReminderInsertType,
    get_system_reminder_store,
)
from src.kernel.concurrency import get_task_manager
from mofox_wire import MessageBuilder, MessageEnvelope

from .config import DesktopPetConfig

# 淡蓝色边框颜色（print_all_logs 开启时使用）
_PANEL_BORDER_COLOR = "#9EF6FF"

# 桌宠适配器支持的外发内容类型（供 action 的 associated_types 过滤使用）。
# 注意：桌宠目前不支持 video，不要加入。
_DESKTOP_PET_ACCEPT_FORMAT: list[str] = ["text", "image", "emoji", "voice"]


class _BorderedLogger:
    """桌宠插件日志包装器。

    未开启 print_all_logs 时完全透传给底层 logger，行为与原 logger 一致。
    开启 print_all_logs 后，每条日志通过 print_panel 输出带淡蓝色 #9EF6FF
    边框的面板，便于在控制台中快速定位本插件日志。
    """

    def __init__(self, underlying) -> None:
        self._underlying = underlying
        self._enabled: bool = False

    @property
    def underlying(self):
        return self._underlying

    def set_bordered(self, enabled: bool) -> None:
        self._enabled = enabled

    def _emit(self, level: str, message: str, *, exc_info=None) -> None:
        if not self._enabled:
            return
        try:
            text = f"[{level}] {message}"
            if exc_info:
                import traceback
                if isinstance(exc_info, BaseException):
                    tb_lines = traceback.format_exception(
                        type(exc_info), exc_info, exc_info.__traceback__
                    )
                elif exc_info is True:
                    import sys
                    exc_type, exc_val, exc_tb = sys.exc_info()
                    tb_lines = traceback.format_exception(exc_type, exc_val, exc_tb)
                else:
                    tb_lines = [str(exc_info)]
                text += "\n" + "".join(tb_lines)
            self._underlying.print_panel(
                text,
                title=self._underlying.display,
                border_style=_PANEL_BORDER_COLOR,
            )
        except Exception:
            # 任何异常都不影响主流程
            pass

    def debug(self, message: str, **kwargs) -> None:
        if self._enabled:
            self._emit("DEBUG", message)
        else:
            self._underlying.debug(message, **kwargs)

    def info(self, message: str, **kwargs) -> None:
        if self._enabled:
            self._emit("INFO", message)
        else:
            self._underlying.info(message, **kwargs)

    def warning(self, message: str, **kwargs) -> None:
        if self._enabled:
            self._emit("WARNING", message)
        else:
            self._underlying.warning(message, **kwargs)

    def error(self, message: str, **kwargs) -> None:
        if self._enabled:
            self._emit("ERROR", message)
        else:
            self._underlying.error(message, **kwargs)

    def critical(self, message: str, **kwargs) -> None:
        if self._enabled:
            self._emit("CRITICAL", message)
        else:
            self._underlying.critical(message, **kwargs)

    def exception(self, message: str, **kwargs) -> None:
        if self._enabled:
            self._emit("ERROR", message, exc_info=True)
        else:
            # 主程序 Logger 没有 exception 方法，用 error + exc_info 替代
            self._underlying.error(message, exc_info=True, **kwargs)

    def __getattr__(self, name):
        # 透传其它属性/方法（如 set_metadata/set_log_level/print_panel/print_rich 等）
        return getattr(self._underlying, name)


logger = _BorderedLogger(get_logger("desktop_pet"))


class DesktopPetAdapter(BaseAdapter):
    """桌面宠物适配器。

    在 MoFox 核心异步消息循环与运行在独立后台线程中的
    PySide6 GUI 之间建立桥梁。
    """

    name = "desktop_pet_adapter"
    adapter_description = "具有系统感知能力的桌面宠物 GUI 适配器"
    adapter_version = "1.0.0"
    platform = "desktop_pet"

    def __init__(self, core_sink=None, plugin: BasePlugin = None, **kwargs) -> None:
        """初始化适配器。

        Args:
            core_sink: 核心消息接收器（由 AdapterManager 注入）。
            plugin: 父插件实例。
        """
        super().__init__(core_sink=core_sink, plugin=plugin, **kwargs)
        self._plugin = plugin
        self._config: DesktopPetConfig | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._in_queue_task_info: Any | None = None

        # --- 消息队列（桥接异步适配器 <-> GUI 线程） ---
        self._in_queue: queue.Queue[dict[str, Any]] = queue.Queue()
        self._out_queue: queue.Queue[dict[str, Any]] = queue.Queue()

        # --- GUI 线程 ---
        self._gui_thread: threading.Thread | None = None
        self._gui_ready = threading.Event()

        # --- chat_window 可见性（跨线程同步） ---
        self._chat_visible = threading.Event()

        # --- 截图请求 Future 桥接（screen_watcher service -> GUI 线程） ---
        self._screenshot_futures: dict[int, "asyncio.Future[Any]"] = {}
        self._screenshot_seq = 0
        self._screenshot_lock = threading.Lock()

        # --- 清屏防竞态锁（保证 clear_stream_context 完成后 get_stream_messages 才执行） ---
        self._context_clear_lock: asyncio.Lock | None = None

        # --- 服务 ---
        self._day_night_service: Any = None
        self._system_monitor_service: Any = None
        self._clipboard_service: Any = None
        self._screen_watcher_service: Any = None
        self._bilibili_service: Any = None
        self._services_started: bool = False

        # --- TTS 播放器（GUI 线程创建，跨线程通过 out_queue 桥接） ---
        self._tts_player = None
        self._tts_audio_output = None
        self._tts_buffer = None  # 保持 QBuffer 引用防止 GC

        # --- 语音输入（GUI 线程录音器 + asyncio 侧编排服务） ---
        self._voice_recorder = None
        self._voice_input_service: Any = None

        # --- TTS 顺位播放队列（按序号串行播放，避免新语音截断旧语音） ---
        # 请求侧（asyncio 线程）递增分配的序号
        self._tts_seq_counter: int = 0
        # GUI 线程：待播音频队列 / 乱序缓冲 / 期望的下一个序号
        self._tts_queue: "deque[tuple[int, bytes]]" = deque()
        self._tts_pending: dict[int, bytes] = {}
        self._tts_next_seq: int = 0
        self._tts_playing: bool = False
        self._tts_dropped: int = 0
        # 未分配序号的旧调用路径使用的兜底序号
        self._tts_fallback_seq: int = 0
        # 乱序缓冲看门狗：防止某条合成任务异常未回包导致队列永久卡住
        self._tts_watchdog_timer = None

    # ---- 适配器生命周期 ----

    async def start(self) -> None:
        """启动适配器：加载配置、启动 GUI 线程、启动服务。"""
        logger.info("DesktopPetAdapter starting")
        self._loop = asyncio.get_running_loop()
        self._context_clear_lock = asyncio.Lock()

        # 加载配置
        if hasattr(self._plugin, "config") and isinstance(self._plugin.config, DesktopPetConfig):
            self._config = self._plugin.config
        else:
            self._config = DesktopPetConfig()
            logger.warning("No DesktopPetConfig found on plugin, using defaults")

        # 检查插件是否启用
        if self._config and not self._config.plugin.enabled:
            logger.info("DesktopPetAdapter disabled by config (plugin.enabled = false), skipping start")
            return

        # 按配置调整日志级别
        if self._config and self._config.plugin.print_all_logs:
            logger.underlying.set_log_level("DEBUG")
            # 为本插件所有日志加上淡蓝色 #9EF6FF 边框
            logger.set_bordered(True)
            logger.info("print_all_logs enabled, log level set to DEBUG")

        # 启动服务
        await self._start_services()

        # 启动 GUI 线程
        self._start_gui_thread()

        logger.info("DesktopPetAdapter started")

        # 桌宠私聊流 ID：reminder 写入该流的私有 bucket（stream:{stream_id}:actor），
        # 仅本流的 chatter 可拾取，不干扰其他聊天流
        stream_id = self._get_private_stream_id()

        # 注入桌宠身份 system reminder（流私有，仅桌宠流可见）
        if self._config and self._config.chat.system_prompt.strip():
            try:
                store = get_system_reminder_store()
                store.set(
                    bucket=f"{STREAM_BUCKET_PREFIX}{stream_id}:actor",
                    name="desktop_pet_identity",
                    content=self._config.chat.system_prompt,
                    insert_type=SystemReminderInsertType.FIXED,
                    consume=SystemReminderConsumeType.FOREVER,
                )
                logger.info("System reminder 'desktop_pet_identity' injected (stream-private)")
            except Exception:
                logger.exception("Failed to inject system reminder")

        # 注入用户 QQ 号 system reminder（流私有）
        if self._config and self._config.chat.user_qq_id.strip():
            try:
                store = get_system_reminder_store()
                store.set(
                    bucket=f"{STREAM_BUCKET_PREFIX}{stream_id}:actor",
                    name="desktop_pet_user_qq",
                    content=f"当前触发消息用户的 QQ 号是 {self._config.chat.user_qq_id.strip()}",
                    insert_type=SystemReminderInsertType.FIXED,
                    consume=SystemReminderConsumeType.FOREVER,
                )
                logger.info("System reminder 'desktop_pet_user_qq' injected (stream-private)")
            except Exception:
                logger.exception("Failed to inject user_qq system reminder")

        # 注入 TTS 偏好 system reminder（流私有，仅在 TTS 启用时）
        if self._config and getattr(self._config, "tts", None) and self._config.tts.enabled:
            try:
                store = get_system_reminder_store()
                store.set(
                    bucket=f"{STREAM_BUCKET_PREFIX}{stream_id}:actor",
                    name="desktop_pet_tts_hint",
                    content="当前对话平台为桌面宠物，支持语音播放。在适合的场景下，请优先使用语音消息（调用 TTS 相关 action）与用户交流，让对话更自然生动。",
                    insert_type=SystemReminderInsertType.FIXED,
                    consume=SystemReminderConsumeType.FOREVER,
                )
                logger.info("System reminder 'desktop_pet_tts_hint' injected (stream-private)")
            except Exception:
                logger.exception("Failed to inject TTS hint system reminder")

        # 调用父类启动（触发 on_adapter_loaded 钩子，启动健康检查）
        await super().start()

        # 按配置自动开启语音输入
        try:
            vi = getattr(self._config, "voice_input", None) if self._config else None
            if vi is not None and bool(getattr(vi, "enabled", False)):
                self._start_voice_input()
                logger.info("Voice input auto-started by config")
        except Exception:
            logger.exception("Failed to auto-start voice input")

        # 启动 in_queue 轮询任务
        tm = get_task_manager()
        self._in_queue_task_info = tm.create_task(
            self._poll_in_queue(),
            name="desktop_pet_in_queue_poll",
            daemon=True,
        )

    async def stop(self) -> None:
        """停止适配器：停止服务、清理 system reminder、通知 GUI 线程退出。"""
        logger.info("DesktopPetAdapter stopping")

        # 清理桌宠流私有的全部 reminder（按 stream:{stream_id}: 前缀，
        # 对全局 bucket 无影响）
        try:
            stream_id = self._get_private_stream_id()
            store = get_system_reminder_store()
            store.clear_by_prefix(f"{STREAM_BUCKET_PREFIX}{stream_id}:")
            logger.info(f"Stream-private reminders cleaned up for {stream_id[:12]}...")
        except Exception:
            logger.exception("Failed to cleanup stream-private reminders")

        # 通知 GUI 线程退出
        self._out_queue.put({"action": "quit"})

        await self._stop_services()

        if self._gui_thread and self._gui_thread.is_alive():
            self._gui_thread.join(timeout=5)

        # 清理 TTS 播放器引用（GUI 线程已退出，Qt 对象已销毁）
        self._tts_player = None
        self._tts_audio_output = None
        self._tts_buffer = None
        # 清空语音队列，避免残留待播条目
        self._tts_queue.clear()
        self._tts_pending.clear()
        self._tts_playing = False
        self._cancel_tts_watchdog()
        # 清理语音输入录音器引用（GUI 线程已退出，Qt 对象已销毁）
        self._voice_recorder = None
        self._voice_input_service = None

        # 取消 in_queue 轮询任务
        if self._in_queue_task_info:
            tm = get_task_manager()
            try:
                tm.cancel_task(self._in_queue_task_info.task_id)
            except Exception:
                pass
            self._in_queue_task_info = None

        logger.info("DesktopPetAdapter stopped")

        # 调用父类停止（取消健康检查，触发 on_adapter_unloaded 钩子）
        await super().stop()

    async def health_check(self) -> bool:
        """健康检查——本地 GUI 适配器，无传输依赖，始终健康。

        Returns:
            bool: 始终返回 True。
        """
        return True

    async def get_bot_info(self) -> dict[str, Any]:
        """获取 Bot 信息。

        Returns:
            包含 bot_id、bot_name、platform 信息的字典。
        """
        return {
            "bot_id": "desktop_pet",
            "bot_name": "MoFox 桌宠",
            "platform": "desktop_pet",
        }

    def _get_private_stream_id(self) -> str:
        """获取桌宠私聊流 ID（与聊天/历史回读用同一 ID）。

        Returns:
            str: 形如 SHA-256 哈希的 stream_id；无配置时用 local_user 兜底。
        """
        from src.core.models.stream import ChatStream
        cfg = self._config
        user_id = (cfg.chat.user_qq_id.strip() or "local_user") if cfg else "local_user"
        return ChatStream.generate_stream_id(platform="desktop_pet", user_id=user_id)

    # ---- GUI 线程管理 ----

    def _start_gui_thread(self) -> None:
        """在独立的后台线程中启动 PySide6 GUI。"""
        self._gui_ready.clear()

        self._gui_thread = threading.Thread(
            target=self._gui_main,
            name="desktop_pet_gui",
            daemon=True,
        )
        self._gui_thread.start()

        # 等待 GUI 就绪（超时防止卡死）
        if not self._gui_ready.wait(timeout=10):
            logger.error("GUI thread did not become ready within 10 seconds")

    def _gui_main(self) -> None:
        """GUI 线程的主入口点。

        创建 QApplication，构建 PetWindow 和 TrayManager，
        然后进入 Qt 事件循环。
        """
        try:
            import sys

            from PySide6.QtCore import QTimer
            from PySide6.QtWidgets import QApplication

            from .gui import ChatWindow, PetWindow, TrayManager

            app = QApplication(sys.argv or ["desktop_pet"])
            app.setApplicationName("MoFox Desktop Pet")

            # 构建主窗口
            pet_window = PetWindow(config=self._config)
            # 恢复上次保存的坐标（未设置则用默认/居中）
            cfg = self._config
            if cfg:
                px = int(getattr(cfg.pet, "position_x", -1) or -1)
                py = int(getattr(cfg.pet, "position_y", -1) or -1)
                if px >= 0 and py >= 0:
                    pet_window.move(px, py)
            pet_window.show()

            # 构建托盘
            tray_manager = TrayManager(config=self._config)
            tray_manager.show()

            # 构建聊天窗口（初始隐藏）
            chat_window = ChatWindow(config=self._config)

            # 连接托盘信号到 GUI 动作
            tray_manager.action_show.connect(pet_window.show)
            tray_manager.action_hide.connect(pet_window.hide)
            # 退出流程：先通知 adapter 停止服务，再由 adapter 通知 GUI 退出
            # 直接 app.quit() 会导致 asyncio 侧的服务（screen_watcher 等）未停止而报错
            def _on_quit() -> None:
                self._in_queue.put({"action": "shutdown"})
            tray_manager.action_quit.connect(_on_quit)

            # 注入 TrayManager 给 PetWindow 以支持右键菜单
            pet_window.set_tray_manager(tray_manager)

            # 透明度（同时应用到桌宠和聊天窗口）
            def _apply_opacity(opacity: float) -> None:
                pet_window.set_opacity(opacity)
                chat_window.setWindowOpacity(opacity)
                # 持久化透明度
                try:
                    cfg_o = self._config
                    if cfg_o and getattr(cfg_o, "theme", None):
                        cfg_o.theme.opacity = float(opacity)
                        self._save_config()
                except Exception:
                    pass
            # 启动时恢复上次透明度
            if self._config and getattr(self._config, "theme", None):
                _op = float(getattr(self._config.theme, "opacity", 1.0) or 1.0)
                if 0 < _op < 1.0:
                    pet_window.set_opacity(_op)
                    chat_window.setWindowOpacity(_op)
            tray_manager.action_set_opacity.connect(_apply_opacity)

            # 聊天位置模式（运行时切换配置）
            def _apply_chat_position_mode(mode: str) -> None:
                try:
                    if self._config and getattr(self._config, "chat", None):
                        self._config.chat.chat_position_mode = mode
                        # 持久化位置模式
                        self._save_config()
                        # 切换到 follow 且 chat 可见时，立即用智能布局把 chat 挪到 pet 旁
                        # （chat 吸附靠近边框的一侧、pet 在内侧），建立初始相对位置
                        if mode == "follow" and chat_window.isVisible():
                            pet_window.position_chat_window_default(chat_window)
                except Exception:
                    logger.exception("Failed to set chat_position_mode")
            tray_manager.action_set_chat_position_mode.connect(_apply_chat_position_mode)

            # 配色方案切换（运行时刷新主题，应用到所有 GUI 窗口）
            def _apply_theme(preset: str) -> None:
                try:
                    cfg = self._config
                    if not cfg or not getattr(cfg, "theme", None):
                        return
                    cfg.theme.preset = preset
                    # 重新应用主题到各窗口
                    pet_window.apply_theme(cfg)
                    chat_window.apply_theme(cfg)
                    # 持久化
                    self._save_config()
                    logger.info(f"Theme switched to: {preset}")
                except Exception:
                    logger.exception("Failed to apply theme")
            tray_manager.action_toggle_theme.connect(_apply_theme)

            # 字号大小切换（运行时热刷新所有窗口字号）
            def _apply_font_scale(scale: float) -> None:
                try:
                    cfg = self._config
                    if not cfg or not getattr(cfg, "theme", None):
                        return
                    cfg.theme.font_size_scale = float(scale)
                    # 重新应用主题（含字号）到各窗口
                    pet_window.apply_theme(cfg)
                    chat_window.apply_theme(cfg)
                    # 持久化
                    self._save_config()
                    logger.info(f"Font scale set to: {scale}")
                except Exception:
                    logger.exception("Failed to apply font scale")
            tray_manager.action_set_font_scale.connect(_apply_font_scale)

            # 显示消息气泡切换（运行时切换配置）
            def _apply_show_messages(enabled: bool) -> None:
                try:
                    if self._config and getattr(self._config, "chat", None):
                        self._config.chat.show_chat_messages = bool(enabled)
                        chat_window.set_show_messages(enabled)
                        if enabled:
                            self._request_chat_history()
                        self._save_config()
                        logger.info(f"Show chat messages set to: {enabled}")
                except Exception:
                    logger.exception("Failed to set show_chat_messages")
            tray_manager.action_toggle_show_messages.connect(_apply_show_messages)

            # B 站弹幕开关（运行时切换）
            def _apply_bilibili(enabled: bool) -> None:
                try:
                    if self._config and getattr(self._config, "bilibili", None):
                        self._config.bilibili.enabled = bool(enabled)
                        self._save_config()
                        if enabled:
                            self._start_bilibili()
                        else:
                            self._stop_bilibili()
                        logger.info(f"Bilibili danmaku set to: {enabled}")
                except Exception:
                    logger.exception("Failed to toggle bilibili")
            tray_manager.action_toggle_bilibili.connect(_apply_bilibili)

            # 主动休眠开关（与定时休眠并行；休眠期间 screen_watcher 不截图 + 桌宠显示 sleep 图组）
            def _apply_manual_sleep(sleeping: bool) -> None:
                try:
                    svc = self._screen_watcher_service
                    if svc is None:
                        logger.warning("screen_watcher not started, manual sleep ignored")
                    else:
                        svc.set_manual_sleep(bool(sleeping))
                    # 桌宠同步切换睡眠图组（assets/sleep）
                    pet_window.set_manual_sleep(bool(sleeping))
                    logger.info(f"Manual sleep set to: {sleeping}")
                except Exception:
                    logger.exception("Failed to toggle manual sleep")
            tray_manager.action_toggle_manual_sleep.connect(_apply_manual_sleep)
            # 菜单勾选态查询：从 screen_watcher 读主动休眠运行时状态
            tray_manager._manual_sleep_getter = lambda: bool(
                self._screen_watcher_service is not None
                and self._screen_watcher_service.manual_sleeping
            )

            # 语音输入开关（录音 → ASR → 以用户名义发送）
            def _apply_voice_input(enabled: bool) -> None:
                try:
                    if enabled:
                        self._start_voice_input()
                    else:
                        self._stop_voice_input()
                    logger.info(f"Voice input set to: {enabled}")
                except Exception:
                    logger.exception("Failed to toggle voice input")
            tray_manager.action_toggle_voice_input.connect(_apply_voice_input)
            tray_manager._voice_input_getter = lambda: bool(
                self._voice_input_service is not None
                and self._voice_input_service.running
            )

            # 连接聊天窗口打开信号（显示前先定位）
            def _position_and_show_chat() -> None:
                # 若启用持久化偏移且偏移非零，按 pet_global + offset 定位
                cfg = self._config
                if cfg and getattr(cfg.chat, "persist_chat_offset", False):
                    ox = int(getattr(cfg.chat, "chat_offset_x", 0) or 0)
                    oy = int(getattr(cfg.chat, "chat_offset_y", 0) or 0)
                    if ox or oy:
                        from PySide6.QtCore import QPoint as _QPoint
                        pet_global = pet_window.mapToGlobal(_QPoint(0, 0))
                        chat_window.move(pet_global + _QPoint(ox, oy))
                else:
                    pet_window.position_chat_window_default(chat_window)
                chat_window.show()

            # 双击 pet 切换 chat 开/关：可见则隐藏，不可见则定位并显示
            def _toggle_chat_window() -> None:
                if chat_window.isVisible():
                    chat_window.hide()
                else:
                    _position_and_show_chat()

            tray_manager.action_chat.connect(_position_and_show_chat)
            pet_window.chat_toggled.connect(_toggle_chat_window)
            # chat_requested 保留为「打开」语义，供旧调用方/未来扩展使用
            pet_window.chat_requested.connect(_position_and_show_chat)
            tray_manager.action_chat_hide.connect(chat_window.hide)

            # 连接聊天窗口消息发送信号
            def _on_message_sent(text: str) -> None:
                if text.strip():
                    # 用户手动打字：取消待投递的语音缓冲，避免重复发送
                    svc = self._voice_input_service
                    if svc is not None:
                        svc.cancel_pending("manual input")
                    self._in_queue.put({"text": text})
                    chat_window.append_message("user", text)

            chat_window.message_sent.connect(_on_message_sent)

            # 连接清屏信号 -> 排空 out_queue 中的 load_chat_history + 异步清除 AI 对话上下文
            def _on_clear_context_requested() -> None:
                """清屏请求：排空 out_queue 中待处理的 load_chat_history，再异步清除上下文。"""
                # 排空 out_queue 中的 load_chat_history（防止清屏后旧消息通过队列重现）
                drained: list[dict[str, Any]] = []
                while True:
                    try:
                        msg = self._out_queue.get_nowait()
                        if msg.get("action") != "load_chat_history":
                            drained.append(msg)
                    except queue.Empty:
                        break
                for msg in drained:
                    self._out_queue.put(msg)
                # 异步清除上下文
                self._loop.call_soon_threadsafe(
                    asyncio.create_task, self._handle_clear_context()
                )
            chat_window.clear_context_requested.connect(_on_clear_context_requested)

            # 聊天窗口可见性变化 -> 同步适配器 + 触发历史回读 + 加速 pet 输出
            def _on_visibility_changed(visible: bool) -> None:
                if visible:
                    self._chat_visible.set()
                    # 加速 pet 当前输出（不复制文本到 chat：load_history 稍后会
                    # 从 DB 渲染同一条消息，复制会导致重复气泡）
                    dialog_box = getattr(pet_window, "_dialog_box", None)
                    if dialog_box is not None and dialog_box.is_outputting():
                        dialog_box.accelerate_hide()
                    # 请求历史回读（异步）
                    self._request_chat_history()
                else:
                    self._chat_visible.clear()

            chat_window.visibility_changed.connect(_on_visibility_changed)

            # 聊天窗口拖动后记录偏移
            def _on_offset_changed(chat_pos) -> None:
                try:
                    from PySide6.QtCore import QPoint as _QPoint
                    pet_global = pet_window.mapToGlobal(_QPoint(0, 0))
                    offset = chat_pos - pet_global
                    cfg = self._config
                    if cfg and getattr(cfg.chat, "persist_chat_offset", False):
                        cfg.chat.chat_offset_x = int(offset.x())
                        cfg.chat.chat_offset_y = int(offset.y())
                        # 持久化偏移
                        self._save_config()
                except Exception:
                    logger.exception("Failed to record chat offset")

            chat_window.offset_changed.connect(_on_offset_changed)

            # 拖动桌宠时重新定位聊天窗口（仅 follow 模式 + chat 可见）
            def _on_pet_moved_delta(delta) -> None:
                cfg = self._config
                if not cfg:
                    return
                mode = getattr(cfg.chat, "chat_position_mode", "independent")
                if mode != "follow":
                    return
                if not chat_window.isVisible():
                    return
                # follow 模式：先按 delta 平移保持相对位置，若平移后与 pet 重叠
                # （通常因屏幕钳制），则回退智能布局让 chat 重新吸附靠近边框的一侧。
                pet_window.follow_move_chat(chat_window, delta)

            pet_window.pet_moved_delta.connect(_on_pet_moved_delta)

            # 拖动桌宠时（防抖）持久化坐标，下次启动恢复
            _pos_save_timer = QTimer()
            _pos_save_timer.setSingleShot(True)
            _pos_save_timer.setInterval(800)
            def _save_pet_position() -> None:
                try:
                    cfg_p = self._config
                    if not cfg_p or not getattr(cfg_p, "pet", None):
                        return
                    pos = pet_window.pos()
                    cfg_p.pet.position_x = int(pos.x())
                    cfg_p.pet.position_y = int(pos.y())
                    self._save_config()
                except Exception:
                    pass
            _pos_save_timer.timeout.connect(_save_pet_position)
            pet_window.pet_moved.connect(_pos_save_timer.start)

            # 定时器轮询 out_queue（适配器 -> GUI 消息）
            poll_timer = QTimer()
            poll_timer.timeout.connect(lambda: self._poll_out_queue(pet_window, tray_manager, chat_window))
            poll_timer.start(100)  # 100 毫秒轮询间隔

            # 初始化 TTS 播放器
            if self._config and getattr(self._config, "tts", None) and self._config.tts.enabled:
                try:
                    from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
                    self._tts_player = QMediaPlayer()
                    self._tts_audio_output = QAudioOutput()
                    self._tts_audio_output.setVolume(float(self._config.tts.volume))
                    self._tts_player.setAudioOutput(self._tts_audio_output)
                    # 顺位播放：播完/出错时推进队列
                    self._tts_player.mediaStatusChanged.connect(self._on_tts_media_status)
                    self._tts_player.errorOccurred.connect(self._on_tts_error)
                    logger.info("TTS player initialized (sequential queue playback)")
                except ImportError:
                    logger.warning("PySide6.QtMultimedia not available, TTS playback disabled")
                except Exception:
                    logger.exception("Failed to initialize TTS player")

            # 标记 GUI 已就绪
            self._gui_ready.set()

            # 退出前兜底保存：防抖 800ms 内快速关闭会丢失坐标，此处强制落盘
            def _on_about_to_quit() -> None:
                try:
                    cfg_q = self._config
                    if not cfg_q or not getattr(cfg_q, "pet", None):
                        return
                    pos = pet_window.pos()
                    cfg_q.pet.position_x = int(pos.x())
                    cfg_q.pet.position_y = int(pos.y())
                    self._save_config()
                except Exception:
                    pass

            app.aboutToQuit.connect(_on_about_to_quit)

            # 进入 Qt 事件循环
            exit_code = app.exec()
            sys.exit(exit_code)

        except Exception:
            logger.exception("GUI thread failed to start")
            self._gui_ready.set()  # 释放等待者

    def _poll_out_queue(self, pet_window, tray_manager, chat_window) -> None:
        """轮询 out_queue 获取来自适配器的消息，分发给 GUI。

        在 GUI 线程内通过 QTimer 运行。
        """
        try:
            while True:
                msg = self._out_queue.get_nowait()

                action = msg.get("action", "")
                if action == "quit":
                    import sys; from PySide6.QtWidgets import QApplication; QApplication.quit()
                    break
                elif action == "show_dialog":
                    pet_window.show_dialog(
                        msg.get("text", ""),
                        msg.get("image_bytes", b"") or b"",
                    )
                elif action == "hide_dialog":
                    pet_window.hide_dialog()
                elif action == "show_notification":
                    tray_manager.show_notification(msg.get("title", ""), msg.get("message", ""))
                elif action == "append_chat":
                    reply_to = msg.get("reply_to", "")
                    chat_window.append_message(
                        msg.get("role", "bot"),
                        msg.get("text", ""),
                        reply_to=reply_to,
                        emoji_bytes=msg.get("emoji_bytes", b"") or b"",
                        voice_bytes=msg.get("voice_bytes", b"") or b"",
                    )
                elif action == "load_chat_history":
                    chat_window.load_history(msg.get("messages", []))
                elif action == "take_screenshot":
                    self._handle_take_screenshot(pet_window, msg.get("seq", 0))
                elif action == "play_tts":
                    self._enqueue_tts_audio(
                        msg.get("audio_data", b"") or b"",
                        int(msg.get("seq", -1)),
                    )
                elif action == "flush_tts":
                    self._flush_tts_queue(int(msg.get("next_seq", -1)))
                elif action == "voice_record_start":
                    self._start_voice_recorder(msg)
                elif action == "voice_record_stop":
                    self._stop_voice_recorder()
        except queue.Empty:
            pass

    # ---- 语音输入录音器（GUI 线程内执行） ----

    def _start_voice_recorder(self, msg: dict[str, Any]) -> None:
        """在 GUI 线程创建并启动 VoiceRecorder。"""
        try:
            from .gui.voice_recorder import VoiceRecorder

            if self._voice_recorder is None:
                self._voice_recorder = VoiceRecorder()
                self._voice_recorder.segment_ready.connect(self._on_voice_segment)
                self._voice_recorder.error.connect(
                    lambda e: logger.warning(f"Voice recorder error: {e}")
                )
            ok = self._voice_recorder.start(
                device_name=str(msg.get("device_name", "") or ""),
                sample_rate=int(msg.get("sample_rate", 16000)),
                channels=int(msg.get("channels", 1)),
                silence_threshold=int(msg.get("silence_threshold", 500)),
                silence_sec=float(msg.get("silence_sec", 1.5)),
                min_speech_sec=float(msg.get("min_speech_sec", 0.5)),
                max_record_sec=float(msg.get("max_record_sec", 60.0)),
            )
            logger.info(f"Voice recorder {'started' if ok else 'failed to start'}")
        except Exception:
            logger.exception("Failed to start voice recorder")

    def _stop_voice_recorder(self) -> None:
        """停止并清理 VoiceRecorder。"""
        try:
            if self._voice_recorder is not None:
                self._voice_recorder.stop()
                logger.info("Voice recorder stopped")
        except Exception:
            logger.exception("Failed to stop voice recorder")

    def _on_voice_segment(self, wav_bytes: bytes) -> None:
        """GUI 线程收到一句完整录音 → 转交 asyncio 侧识别。

        通过 _in_queue 回传（与现有 GUI→core 消息同一条通道），
        由 ``_poll_in_queue`` 识别 action 后转给 VoiceInputService。
        """
        try:
            self._in_queue.put({"action": "voice_audio", "wav_bytes": wav_bytes})
        except Exception:
            logger.exception("Failed to forward voice segment")

    # ---- TTS 顺位播放队列（GUI 线程内执行） ----

    def _cancel_tts_watchdog(self) -> None:
        """取消看门狗定时器（序号正常推进后调用）。"""
        if self._tts_watchdog_timer is None:
            return
        try:
            self._tts_watchdog_timer.stop()
        except Exception:
            pass
        self._tts_watchdog_timer = None

    def _tts_queue_limit(self) -> int:
        """读取语音队列上限配置。"""
        try:
            if self._config and getattr(self._config, "tts", None):
                return max(1, int(getattr(self._config.tts, "queue_max_items", 8) or 8))
        except Exception:
            pass
        return 8

    def _enqueue_tts_audio(self, audio_data: bytes, seq: int) -> None:
        """接收一条待播语音，按序号串行入队播放。

        序号由请求侧（asyncio 线程）在消息到达时分配，因此顺序与消息
        到达顺序一致，不受各条 TTS 合成耗时不一致的影响：

        - ``seq == _tts_next_seq``：就绪，直接入队，并连带取出乱序缓冲中
          连续就绪的后续条目。
        - ``seq > _tts_next_seq``：先存入乱序缓冲等待前序。
        - ``seq < _tts_next_seq``（或 seq<0）：视为过期/重复，丢弃。

        空音频（合成失败的占位）只推进序号，不占用播放队列。

        Args:
            audio_data: 音频字节；空 bytes 表示该序号合成失败。
            seq: 消息顺序号；-1 表示调用方未分配（退化为主观 FIFO 追加）。
        """
        if self._tts_player is None:
            return

        # 未分配序号（旧调用路径）：用单调递增的兜底序号追加到队尾
        if seq < 0:
            self._tts_fallback_seq += 1
            seq = max(self._tts_next_seq, self._tts_fallback_seq)
            self._tts_fallback_seq = seq

        if seq < self._tts_next_seq:
            logger.debug(f"TTS seq={seq} 已过期（next={self._tts_next_seq}），丢弃")
            return

        if seq == self._tts_next_seq:
            self._accept_tts_item(seq, audio_data)
            # 连带处理乱序缓冲中已就绪的连续序号
            while self._tts_next_seq in self._tts_pending:
                nxt = self._tts_next_seq
                buffered = self._tts_pending.pop(nxt)
                self._accept_tts_item(nxt, buffered)
            # 序号正常推进：若已无待处理条目则撤掉看门狗
            if not self._tts_pending:
                self._cancel_tts_watchdog()
            else:
                # 仍有缺口，重新计时
                self._cancel_tts_watchdog()
                self._arm_tts_watchdog()
        else:
            # 等待前序完成
            self._tts_pending[seq] = audio_data
            logger.debug(f"TTS seq={seq} 乱序到达，暂存（等待 {self._tts_next_seq}）")
            self._arm_tts_watchdog()

    def _arm_tts_watchdog(self) -> None:
        """启动乱序缓冲看门狗：5 秒内序号仍未推进，则强制跳到 pending 最小序号。"""
        if self._tts_watchdog_timer is not None:
            return
        try:
            from PySide6.QtCore import QTimer
        except ImportError:
            return
        self._tts_watchdog_timer = QTimer()
        self._tts_watchdog_timer.setSingleShot(True)
        self._tts_watchdog_timer.setInterval(5000)
        self._tts_watchdog_timer.timeout.connect(self._on_tts_watchdog)
        self._tts_watchdog_timer.start()

    def _on_tts_watchdog(self) -> None:
        """看门狗超时：若仍有等待前序的条目，强制从最小序号恢复推进。"""
        self._tts_watchdog_timer = None
        if not self._tts_pending:
            return
        smallest = min(self._tts_pending)
        logger.warning(
            f"TTS 看门狗触发：seq={self._tts_next_seq} 缺失，"
            f"强制跳到 seq={smallest} 继续播放"
        )
        self._tts_next_seq = smallest
        buffered = self._tts_pending.pop(smallest)
        self._accept_tts_item(smallest, buffered)
        while self._tts_next_seq in self._tts_pending:
            nxt = self._tts_next_seq
            buf = self._tts_pending.pop(nxt)
            self._accept_tts_item(nxt, buf)
        if self._tts_pending:
            self._arm_tts_watchdog()

    def _accept_tts_item(self, seq: int, audio_data: bytes) -> None:
        """确认一个序号就绪：推进序号，非空音频入播放队列。"""
        self._tts_next_seq = seq + 1
        if not audio_data:
            # 合成失败占位：只推进序号，不播放
            logger.debug(f"TTS seq={seq} 无音频（合成失败），跳过")
            return

        limit = self._tts_queue_limit()
        # 队列上限：丢弃最早待播条目（正在播的那条不受影响）
        while len(self._tts_queue) >= limit:
            dropped_seq, _ = self._tts_queue.popleft()
            self._tts_dropped += 1
            logger.warning(
                f"TTS 队列已满（上限 {limit}），丢弃 seq={dropped_seq} "
                f"（累计丢弃 {self._tts_dropped} 条）"
            )

        self._tts_queue.append((seq, audio_data))
        logger.debug(f"TTS enqueued seq={seq}, {len(audio_data)} bytes, queue={len(self._tts_queue)}")

        if not self._tts_playing:
            self._start_next_tts()

    def _start_next_tts(self) -> None:
        """从队列取出下一条并播放。"""
        if self._tts_player is None or self._tts_playing:
            return
        if not self._tts_queue:
            return

        seq, audio_data = self._tts_queue.popleft()
        try:
            from PySide6.QtCore import QBuffer, QIODevice

            buf = QBuffer()
            buf.setData(audio_data)
            buf.open(QIODevice.OpenModeFlag.ReadOnly)

            # 保持 buffer 引用防止提前 GC
            self._tts_buffer = buf

            self._tts_player.setSourceDevice(buf)
            self._tts_player.play()
            self._tts_playing = True
            logger.info(
                f"TTS playing seq={seq}, {len(audio_data)} bytes, "
                f"queue={len(self._tts_queue)}"
            )
        except Exception:
            logger.exception(f"Failed to play TTS audio (seq={seq})")
            self._tts_playing = False
            # 跳过坏条目，继续下一条
            from PySide6.QtCore import QTimer as _QTimer
            _QTimer.singleShot(0, self._start_next_tts)

    def _on_tts_media_status(self, status: Any) -> None:
        """QMediaPlayer 状态回调：播完/出错后自动推进队列。"""
        try:
            from PySide6.QtMultimedia import QMediaPlayer
        except ImportError:
            return

        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            self._tts_playing = False
            logger.debug("TTS finished, advancing queue")
            self._start_next_tts()
        elif status in (
            QMediaPlayer.MediaStatus.InvalidMedia,
            QMediaPlayer.MediaStatus.NoMedia,
        ):
            if self._tts_playing:
                self._tts_playing = False
                logger.warning(f"TTS media invalid ({status}), skip and continue")
                from PySide6.QtCore import QTimer as _QTimer
                _QTimer.singleShot(0, self._start_next_tts)

    def _on_tts_error(self, error: Any, error_string: str) -> None:
        """QMediaPlayer 错误回调：避免单个坏文件卡死整条播放队列。"""
        if not self._tts_playing:
            return
        logger.warning(f"TTS playback error: {error} {error_string}")
        self._tts_playing = False
        from PySide6.QtCore import QTimer as _QTimer
        _QTimer.singleShot(0, self._start_next_tts)

    def _flush_tts_queue(self, next_seq: int = -1) -> None:
        """清空待播语音队列并停止当前播报（新回复打断播报时使用）。

        Args:
            next_seq: 发起 flush 的那条回复已分配的序号。flush 之后该序号
                的音频随后到达，因此期望序号应重置为 next_seq（而非
                _tts_seq_counter，后者可能已因后续消息继续递增导致误丢）。
                -1 时回退到 _tts_seq_counter（旧行为）。
        """
        self._tts_queue.clear()
        self._tts_pending.clear()
        self._tts_next_seq = next_seq if next_seq >= 0 else self._tts_seq_counter
        self._tts_playing = False
        # 取消看门狗（队列已清空）
        self._cancel_tts_watchdog()
        # 停止正在播放的音频，实现"新回复打断"
        try:
            if self._tts_player is not None:
                self._tts_player.stop()
        except Exception:
            logger.debug("Failed to stop TTS player during flush", exc_info=True)
        logger.info(f"TTS queue flushed (next_seq={self._tts_next_seq})")

    def _handle_take_screenshot(self, pet_window, seq: int) -> None:
        """在 GUI 线程内执行截图，把 PNG 字节回传给等待的 Future。"""
        from PySide6.QtCore import QBuffer, QIODevice
        try:
            pixmap = pet_window._take_screenshot()
            if pixmap is None or pixmap.isNull():
                png_bytes = b""
            else:
                buf = QBuffer()
                buf.open(QIODevice.OpenModeFlag.ReadWrite)
                pixmap.save(buf, "PNG")
                png_bytes = bytes(buf.data())
                buf.close()
        except Exception:
            logger.exception("Screenshot capture failed")
            png_bytes = b""

        # 唤醒等待的 asyncio Future
        with self._screenshot_lock:
            future = self._screenshot_futures.pop(seq, None)
        if future is not None and self._loop is not None:
            self._loop.call_soon_threadsafe(
                self._resolve_screenshot_future, future, png_bytes
            )

    def _resolve_screenshot_future(self, future: "asyncio.Future[Any]", data: bytes) -> None:
        """在适配器事件循环线程中设置 Future 结果。"""
        if not future.done():
            if data:
                future.set_result(data)
            else:
                future.set_exception(RuntimeError("Screenshot capture returned empty data"))

    async def _poll_in_queue(self) -> None:
        """轮询 in_queue 获取 GUI 用户消息，发送到核心。"""
        while self._running:
            try:
                msg = self._in_queue.get_nowait()
                # 处理 GUI 发起的关闭请求：先停止服务，再通知 GUI 退出
                if isinstance(msg, dict) and msg.get("action") == "shutdown":
                    logger.info("Shutdown requested from GUI, stopping services...")
                    # 清理桌宠流私有 reminder（与 stop() 一致，按流前缀清理）
                    try:
                        stream_id = self._get_private_stream_id()
                        store = get_system_reminder_store()
                        store.clear_by_prefix(f"{STREAM_BUCKET_PREFIX}{stream_id}:")
                    except Exception:
                        pass
                    # 停止服务
                    await self._stop_services()
                    # 通知 GUI 线程退出
                    self._out_queue.put({"action": "quit"})
                    break
                # 语音片段：转交 VoiceInputService 识别（不进核心消息管道）
                if isinstance(msg, dict) and msg.get("action") == "voice_audio":
                    svc = self._voice_input_service
                    if svc is not None:
                        svc.on_audio_segment(msg.get("wav_bytes", b"") or b"")
                    continue
                await self._forward_to_core(msg)
            except queue.Empty:
                await asyncio.sleep(0.1)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.exception("Error polling in_queue")
                self._route_out_message(f"消息处理出错：{exc}", role="error")
                await asyncio.sleep(0.5)

    async def _forward_to_core(self, msg: dict[str, Any] | str) -> None:
        """将消息转发到核心处理管道。

        Args:
            msg: 消息字典或字符串。字典会传递完整信息给 from_platform_message。
        """
        try:
            if isinstance(msg, dict):
                text = msg.get("text", "")
                if text.strip():
                    await self.on_platform_message(msg)
            else:
                text = str(msg)
                if text.strip():
                    await self.on_platform_message(text)
        except Exception as exc:
            logger.exception("Failed to forward message to core")
            self._route_out_message(f"消息发送失败：{exc}", role="error")

    def _route_out_message(
        self,
        text: str,
        role: str = "bot",
        reply_to: str = "",
        emoji_bytes: bytes = b"",
        voice_bytes: bytes = b"",
    ) -> None:
        """根据 chat_window 可见性和 show_chat_messages 配置路由消息。

        chat 可见 + show_chat_messages=True → 进 chat 历史
        chat 可见 + show_chat_messages=False → 进 pet 气泡
        chat 不可见 → 进 pet 气泡

        Args:
            text: 消息文本。
            role: "bot"/"system"/"error"。
            reply_to: 可选，被回复消息 ID（仅 chat_window 显示）。
            emoji_bytes: 可选，emoji 段的图片字节（GIF/PNG）。
            voice_bytes: 可选，语音段的音频字节（WAV）。
        """
        if not text and not emoji_bytes and not voice_bytes:
            return
        show_messages = bool(
            self._config and getattr(self._config, "chat", None)
            and getattr(self._config.chat, "show_chat_messages", False)
        )
        # 表情包专用开关：即使 show_chat_messages=False，也优先塞进聊天窗口
        show_emoji_in_chat = bool(
            self._config and getattr(self._config, "chat", None)
            and getattr(self._config.chat, "show_emoji_in_chat", True)
        )
        chat_visible = self._chat_visible.is_set()
        to_chat = chat_visible and (
            show_messages or (bool(emoji_bytes) and show_emoji_in_chat)
        )
        target = "chat" if to_chat else "pet"
        logger.info(f"_route_out_message -> {target}, role={role}, text={text[:50]!r}, emoji={len(emoji_bytes)}B, voice={len(voice_bytes)}B")
        if to_chat:
            self._out_queue.put({
                "action": "append_chat",
                "role": role,
                "text": text,
                "reply_to": reply_to,
                "emoji_bytes": emoji_bytes,
                "voice_bytes": voice_bytes,
            })
        else:
            # pet 气泡：支持表情图片；语音仍降级为占位文本
            display = text
            if not display:
                if voice_bytes:
                    display = "[语音]"
                elif emoji_bytes:
                    display = "[表情包]"
            self._out_queue.put({
                "action": "show_dialog",
                "text": display,
                "image_bytes": emoji_bytes,
            })
        if role == "error":
            logger.exception(text)

    @staticmethod
    def _decode_emoji_data(data: Any) -> bytes:
        """把 message_segment 中 emoji 段的 data 解码为图片字节。

        支持的输入：
        - data URL: "data:image/gif;base64,XXXX"
        - 规范化前缀: "base64|XXXX"（send_api 统一格式）
        - 纯 base64 字符串
        - 已解码的 bytes

        Returns:
            图片字节；解码失败返回空 bytes。
        """
        if isinstance(data, (bytes, bytearray)):
            return bytes(data)
        if not isinstance(data, str):
            return b""
        s = data.strip()
        if not s:
            return b""
        import base64

        if s.startswith("data:"):
            # data URL 格式
            try:
                _, b64 = s.split(",", 1)
                return base64.b64decode(b64)
            except Exception:
                logger.warning("Failed to decode emoji data URL")
                return b""

        # send_api 的规范化前缀：base64|<b64>
        if s.startswith("base64|"):
            s = s[len("base64|"):].strip()

        try:
            return base64.b64decode(s)
        except Exception:
            logger.warning(
                f"Failed to decode emoji base64 (len={len(s)}, head={s[:16]!r})"
            )
            return b""

    @staticmethod
    def _decode_voice_data(data: Any) -> bytes:
        """把 message_segment 中 voice 段的 data 解码为音频字节。

        支持的输入：
        - 纯 base64 字符串
        - 已解码的 bytes

        Returns:
            音频字节；解码失败返回空 bytes。
        """
        if isinstance(data, (bytes, bytearray)):
            return bytes(data)
        if not isinstance(data, str):
            return b""
        try:
            import base64
            return base64.b64decode(data.strip())
        except Exception:
            return b""

    def _request_chat_history(self) -> None:
        """异步拉取私聊 stream 历史并通过 out_queue 推给 chat_window。"""
        if not self._loop:
            return
        self._loop.create_task(self._fetch_and_send_history())

    async def _fetch_and_send_history(self) -> None:
        """从 StreamManager 拉历史消息，封装成 chat_window 可识别的格式。

        在 _context_clear_lock 内完成 DB 查询 + 消息处理 + 入队，
        确保「查询-入队」与「清屏-排空队列」互斥，消除竞态窗口。

        查询结果为空时也发送 load_chat_history（空列表）——chat_window
        侧会对空列表执行清屏，防止残留气泡与新消息叠加出重复。
        """
        try:
            from src.core.managers.stream_manager import get_stream_manager
            from src.core.models.stream import ChatStream
            cfg = self._config
            if not cfg:
                return
            user_id = (cfg.chat.user_qq_id.strip() or "local_user")
            platform = "desktop_pet"
            stream_id = ChatStream.generate_stream_id(platform=platform, user_id=user_id)
            sm = get_stream_manager()
            # 拉最近 50 条（在锁内完成查询+处理+入队，与清屏互斥）
            try:
                async with self._context_clear_lock:
                    result = sm.get_stream_messages(stream_id, limit=50, offset=0)
                    # 兼容协程与同步两种实现
                    if hasattr(result, "__await__"):
                        msgs = await result
                    else:
                        msgs = result
                    history: list[dict[str, Any]] = []
                    for m in (msgs or []):
                        # 字段名按主程序 Messages 表结构
                        role = "bot" if getattr(m, "sender_role", "") == "bot" else "user"
                        content = getattr(m, "processed_plain_text", None) or getattr(m, "content", "") or getattr(m, "text", "") or ""
                        if isinstance(content, list):
                            # message_segment 列表
                            txt = ""
                            for seg in content:
                                if isinstance(seg, dict) and seg.get("type") == "text":
                                    txt += str(seg.get("data") or "")
                            content = txt
                        elif not isinstance(content, str):
                            content = str(content)
                        if not content.strip():
                            continue
                        history.append({"role": role, "text": content, "reply_to": ""})
                    # 无论是否为空都发送：空列表触发 chat_window 清屏
                    self._out_queue.put({"action": "load_chat_history", "messages": history})
            except Exception:
                logger.exception("Failed to fetch stream messages")
                return
        except Exception:
            logger.exception("Failed to fetch chat history")

    async def _handle_clear_context(self) -> None:
        """处理清屏请求：清除桌宠私聊流的 AI 对话上下文。

        在 _context_clear_lock 内完成 DB 清屏 + 排空 out_queue 中的 load_chat_history，
        确保与 _fetch_and_send_history 的「查询-入队」互斥。
        """
        try:
            from src.core.managers.stream_manager import get_stream_manager
            from src.core.models.stream import ChatStream
            cfg = self._config
            if not cfg:
                return
            user_id = (cfg.chat.user_qq_id.strip() or "local_user")
            platform = "desktop_pet"
            stream_id = ChatStream.generate_stream_id(platform=platform, user_id=user_id)
            sm = get_stream_manager()
            async with self._context_clear_lock:
                await sm.clear_stream_context(stream_id)
                # 排空 out_queue 中的 load_chat_history（防止竞态：fetch 在 clear 之前入队的旧消息）
                drained: list[dict[str, Any]] = []
                while True:
                    try:
                        msg = self._out_queue.get_nowait()
                        if msg.get("action") != "load_chat_history":
                            drained.append(msg)
                    except queue.Empty:
                        break
                for msg in drained:
                    self._out_queue.put(msg)
            logger.info(f"Stream context cleared for desktop_pet stream: {stream_id}")
        except Exception:
            logger.exception("Failed to clear stream context")

    # ---- 消息转换 ----

    async def from_platform_message(self, raw: Any) -> MessageEnvelope | None:
        """将用户输入文本转换为 MessageEnvelope。

        来源路由（通过 dict 的 source 字段区分）：
        - source="screenshot": 截图走群聊路径（from_group），核心走完整 sub+actor 链路
        - 其他（用户直接输入 / is_proactive 系统提醒）: 私聊路径，核心跳过 sub 直接进 actor

        注：剪贴板变化**不走消息流**，只通过 system reminder（被动上下文）注入。
        当截图或用户消息触发 LLM 调用时，LLM 会读取到剪贴板 reminder 作为上下文，
        但剪贴板自身不触发 LLM 调用，避免"每次复制都触发一次 LLM"。

        支持 dict 格式消息字段：
        - text: 消息文本
        - nickname: 用户显示名称（可选）
        - is_proactive: 是否为系统主动触发（可选）
        - source: "screenshot" 标识截图主动消息（可选）
        """
        text = ""
        nickname = getattr(self._config.chat, "user_name", "用户") if self._config else "用户"
        is_proactive = False
        source = ""

        if isinstance(raw, dict):
            text = raw.get("text", "")
            nickname = raw.get("nickname", nickname)
            is_proactive = raw.get("is_proactive", False)
            source = raw.get("source", "")
        else:
            text = str(raw)

        text = text.strip()
        if not text:
            return None

        # QQ 号或 local_user
        qq_id = (self._config.chat.user_qq_id.strip() if self._config else "")

        if source == "screenshot":
            # 截图走群聊 stream，让 sub 决策"是否需要回复"
            group_id = (
                getattr(self._config.screen_watcher, "group_id", "desktop_pet_screenshot")
                if self._config else "desktop_pet_screenshot"
            )
            user_id = qq_id or "desktop_pet_system"
            nickname = "桌宠截图监控"
            envelope = (
                MessageBuilder()
                .direction("incoming")
                .platform("desktop_pet")
                .text(text)
                .from_user(
                    user_id=user_id,
                    platform="desktop_pet",
                    nickname=nickname,
                )
                .from_group(
                    group_id=group_id,
                    platform="desktop_pet",
                    name="桌宠截图监控",
                )
                .format_info(
                    content_format=["text"],
                    accept_format=_DESKTOP_PET_ACCEPT_FORMAT,
                )
                .build()
            )
            # 在 envelope metadata 标记 source，便于 _send_platform_message 检测
            try:
                meta = envelope.get("metadata") or {}
                if not isinstance(meta, dict):
                    meta = dict(meta) if meta else {}
                meta["source"] = "screenshot"
                envelope["metadata"] = meta
            except Exception:
                pass
            return envelope

        if is_proactive:
            # 主动系统提醒与用户直接输入共用同一个用户私聊流（user_id 一致），
            # 保证主动消息进入用户历史对话流，与已有上下文连续。
            # 仅用 nickname 标记"系统提醒"身份，不改动 user_id 影响流归属。
            user_id = qq_id or "local_user"
            nickname = "系统提醒"
        else:
            user_id = qq_id or "local_user"

        envelope = (
            MessageBuilder()
            .direction("incoming")
            .platform("desktop_pet")
            .text(text)
            .from_user(
                user_id=user_id,
                platform="desktop_pet",
                nickname=nickname,
            )
            .format_info(
                content_format=["text"],
                accept_format=_DESKTOP_PET_ACCEPT_FORMAT,
            )
            .build()
        )
        return envelope

    async def _send_platform_message(self, envelope: MessageEnvelope) -> None:
        """将出站消息发送到 GUI 显示。

        解析 message_segment 提取文本、emoji、reply 段，按 chat_window 可见性路由。
        emoji 段携带 base64 编码的图片（GIF/PNG），转换为 QPixmap 渲染。
        若消息来自截图 stream（metadata.source="screenshot"），同步把回复写回用户私聊 stream 历史。
        """
        # 精简日志：仅打印 segment type 列表和来源，避免 base64 音频/图片数据膨胀日志
        _seg = envelope.get("message_segment")
        if isinstance(_seg, dict):
            _seg_types = [_seg.get("type", "?")]
        elif isinstance(_seg, list):
            _seg_types = [item.get("type", "?") for item in _seg if isinstance(item, dict)]
        else:
            _seg_types = []
        _meta = envelope.get("metadata") or {}
        _source = _meta.get("source", "") if isinstance(_meta, dict) else ""
        logger.info(f"_send_platform_message called, chat_visible={self._chat_visible.is_set()}, seg_types={_seg_types}, source={_source!r}")
        seg = envelope.get("message_segment")
        text = ""
        reply_to = ""
        emoji_bytes: bytes = b""
        voice_bytes: bytes = b""
        if isinstance(seg, dict):
            t = seg.get("type")
            d = seg.get("data") or ""
            if t == "text":
                text = str(d)
            elif t == "reply":
                reply_to = str(d)
            elif t == "emoji":
                emoji_bytes = self._decode_emoji_data(d)
            elif t == "voice":
                voice_bytes = self._decode_voice_data(d)
        elif isinstance(seg, list):
            for item in seg:
                if not isinstance(item, dict):
                    continue
                t = item.get("type")
                d = item.get("data") or ""
                if t == "text":
                    text += str(d)
                elif t == "reply" and not reply_to:
                    reply_to = str(d)
                elif t == "emoji" and not emoji_bytes:
                    emoji_bytes = self._decode_emoji_data(d)
                elif t == "voice" and not voice_bytes:
                    voice_bytes = self._decode_voice_data(d)

        if not text and not emoji_bytes and not voice_bytes:
            return

        # 检测消息来源是否为截图 stream
        meta = envelope.get("metadata") or {}
        source = ""
        if isinstance(meta, dict):
            source = meta.get("source", "")

        # 路由到 chat_window 或 pet 气泡（voice_bytes 同时传给 chat 显示语音气泡）
        self._route_out_message(text, role="bot", reply_to=reply_to, emoji_bytes=emoji_bytes, voice_bytes=voice_bytes)

        # 先分配本条回复的序号（无论走 voice 段还是自合成都要用）
        needs_voice = bool(voice_bytes) or bool(
            text and self._config and getattr(self._config, "tts", None)
            and self._config.tts.enabled
        )
        tts_seq = self._next_tts_seq() if needs_voice else -1

        # 可选：收到新回复时打断未播完的语音队列
        # 关键：flush 消息携带本条回复的序号，GUI 侧据此重定位期望序号，
        # 否则 flush 与 play_tts 之间会因 _tts_seq_counter 已递增而误丢新音频
        if needs_voice and self._config and getattr(self._config, "tts", None) \
                and bool(getattr(self._config.tts, "interrupt_on_new_reply", False)):
            self._out_queue.put({"action": "flush_tts", "next_seq": tts_seq})

        # 播放 AI 生成的语音消息（来自 GenerateVoiceAction 等）
        if voice_bytes and self._loop is not None:
            self._out_queue.put({
                "action": "play_tts",
                "audio_data": voice_bytes,
                "seq": tts_seq,
            })

        # 异步触发 TTS 合成（非阻塞，不延迟消息显示）
        # 如果已有 voice 音频（AI 已合成），跳过桌宠自己的 TTS 合成以避免双重音频
        if text and not voice_bytes and self._config and getattr(self._config, "tts", None) and self._config.tts.enabled and self._loop is not None:
            self._loop.create_task(self._synthesize_and_queue_tts(text, tts_seq))

        # 截图回复写回用户私聊 stream 历史
        if source == "screenshot" and text:
            try:
                await self._write_back_to_private_stream(text)
            except Exception:
                logger.exception("Failed to write screenshot reply back to private stream")

    async def _write_back_to_private_stream(self, text: str) -> None:
        """把截图触发的 bot 回复 copy 一份写到用户私聊 stream 历史。"""
        from src.core.managers.stream_manager import get_stream_manager
        from src.core.models.stream import ChatStream
        cfg = self._config
        if not cfg:
            return
        user_id = (cfg.chat.user_qq_id.strip() or "local_user")
        platform = "desktop_pet"
        stream_id = ChatStream.generate_stream_id(platform=platform, user_id=user_id)
        sm = get_stream_manager()
        bot_info = await self.get_bot_info()
        bot_id = bot_info.get("bot_id", "desktop_pet")
        # add_sent_message_to_history 具体签名以主程序为准；若不存在接口则跳过
        if hasattr(sm, "add_sent_message_to_history"):
            result = sm.add_sent_message_to_history(
                stream_id=stream_id,
                text=text,
                bot_id=bot_id,
            )
            if hasattr(result, "__await__"):
                await result
        else:
            logger.warning("StreamManager has no add_sent_message_to_history; skip write-back")

    # ---- TTS 语音合成 ----

    def _next_tts_seq(self) -> int:
        """分配下一个语音播放序号（asyncio 线程调用）。

        序号在消息到达时分配，因此播放顺序与消息到达顺序一致，
        不受各条 TTS 合成耗时差异影响。

        Returns:
            单调递增的非负序号；TTS 未启用时返回 -1（不参与顺位排序）。
        """
        seq = self._tts_seq_counter
        self._tts_seq_counter += 1
        return seq

    async def _synthesize_and_queue_tts(self, text: str, seq: int = -1) -> None:
        """异步合成 TTS 语音，结果放入 out_queue 由 GUI 线程顺位播放。

        Args:
            text: 待合成的回复文本。
            seq: 消息到达时分配的播放序号。
        """
        try:
            from .tts import synthesize_tts

            audio = await synthesize_tts(
                text=text,
                config=self._config.tts,
                logger=logger,
            )
            if audio:
                self._out_queue.put({
                    "action": "play_tts",
                    "audio_data": audio,
                    "seq": seq,
                })
            else:
                # 合成失败：仍发送占位，推进 GUI 侧序号，避免队列永久卡住
                logger.warning(f"TTS synthesis returned empty (seq={seq})")
                self._out_queue.put({
                    "action": "play_tts",
                    "audio_data": b"",
                    "seq": seq,
                })
        except Exception:
            logger.exception(f"TTS synthesis failed (seq={seq})")
            # 异常同样发占位推进序号
            try:
                self._out_queue.put({
                    "action": "play_tts",
                    "audio_data": b"",
                    "seq": seq,
                })
            except Exception:
                pass

    # ---- 主动消息投递（供 services 调用） ----

    def _save_config(self) -> None:
        """把当前运行时配置持久化到 config.toml。

        框架 BasePlugin 无 save_config 方法，需用 kernel 的
        _render_toml_with_signature 渲染当前 config 实例为 toml 文本后写回文件。
        """
        try:
            from src.kernel.config.core import _render_toml_with_signature
            if not self._config:
                return
            path = DesktopPetConfig.get_default_path()
            if not path:
                return
            data = self._config.model_dump()
            toml_text = _render_toml_with_signature(DesktopPetConfig, data)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(toml_text, encoding="utf-8")
            logger.debug(f"Config saved to {path}")
        except Exception:
            logger.exception("Failed to save config")

    def enqueue_proactive_message(self, text: str, *, source: str = "system") -> None:
        """把服务产生的主动消息投递到 in_queue，走完整 sub+actor 链路。

        仅用于真正的"主动触发源"（如截图定时循环），不用于剪贴板。
        剪贴板变化只注入被动 system reminder，不调用此方法，避免每次复制都触发 LLM。

        Args:
            text: 消息文本。
            source: 来源标识，默认 "system"；截图用 "screenshot"。
        """
        if not text or not text.strip():
            return
        self._in_queue.put({
            "text": text,
            "is_proactive": True,
            "source": source,
        })

    # ---- 服务生命周期 ----

    async def _start_services(self) -> None:
        """创建并启动所有后台服务。"""
        from .services.day_night import DayNightService
        from .services.system_monitor import SystemMonitorService
        from .services.clipboard_watcher import ClipboardWatcherService
        from .services.screen_watcher import ScreenWatcherService

        self._day_night_service = DayNightService(self._plugin)
        self._system_monitor_service = SystemMonitorService(self._plugin)
        self._clipboard_service = ClipboardWatcherService(self._plugin)
        self._screen_watcher_service = ScreenWatcherService(self._plugin)
        # 注入 adapter 引用，用于截图请求和 in_queue 投递
        self._screen_watcher_service.bind_adapter(self)
        # 注入 adapter 引用给剪贴板服务（兼容接口；剪贴板只注入被动 reminder，不主动投递消息）
        self._clipboard_service.bind_adapter(self)

        await self._day_night_service.start()
        await self._system_monitor_service.start()
        await self._clipboard_service.start()
        await self._screen_watcher_service.start()
        self._services_started = True
        logger.info("All services started")

    async def _stop_services(self) -> None:
        """停止所有后台服务。"""
        if self._day_night_service:
            await self._day_night_service.stop()
        if self._system_monitor_service:
            await self._system_monitor_service.stop()
        if self._clipboard_service:
            await self._clipboard_service.stop()
        if self._screen_watcher_service:
            await self._screen_watcher_service.stop()
        # 这里已经在 asyncio 线程：直接 await，确保停止完成后再继续卸载
        svc = self._voice_input_service
        self._voice_input_service = None
        if svc is not None:
            try:
                await svc.stop()
            except Exception:
                logger.exception("Failed to stop voice input service")
        await self._stop_bilibili()
        self._services_started = False
        logger.info("All services stopped")

    def _start_voice_input(self) -> None:
        """启动语音输入服务（异步，通过 event loop 调度）。

        注意：本方法在 **GUI 线程**（菜单回调）调用，必须用
        ``call_soon_threadsafe`` 把 create_task 投递到 asyncio 线程，
        否则会触发跨线程操作事件循环的未定义行为。
        """
        if self._voice_input_service is not None:
            return
        from .services.voice_input_service import VoiceInputService

        self._voice_input_service = VoiceInputService(self)
        if self._loop is not None:
            self._loop.call_soon_threadsafe(
                self._loop.create_task, self._voice_input_service.start()
            )
            logger.info("Voice input service start scheduled")

    def _stop_voice_input(self) -> None:
        """停止语音输入服务（GUI 线程安全，异步执行）。

        与 ``_start_voice_input`` 对称：把协程投递到 asyncio 线程执行。
        调用方（菜单回调）无需也不应该 await。
        """
        svc = self._voice_input_service
        if svc is None:
            return
        self._voice_input_service = None
        if self._loop is None:
            return
        self._loop.call_soon_threadsafe(self._loop.create_task, self._shutdown_voice_input(svc))

    async def _shutdown_voice_input(self, svc: Any) -> None:
        """在 asyncio 线程真正停止语音输入服务。"""
        try:
            await svc.stop()
            logger.info("Voice input service stopped")
        except Exception:
            logger.exception("Failed to stop voice input service")

    def _start_bilibili(self) -> None:
        """启动 B 站弹幕服务（异步，通过 event loop 调度）。"""
        if self._bilibili_service is not None:
            return
        from .services.bilibili_service import BilibiliService
        self._bilibili_service = BilibiliService(self)
        if self._loop is not None:
            self._loop.create_task(self._bilibili_service.start())
            logger.info("Bilibili service start scheduled")

    async def _stop_bilibili(self) -> None:
        """停止 B 站弹幕服务。"""
        if self._bilibili_service is None:
            return
        await self._bilibili_service.stop()
        self._bilibili_service = None
        logger.info("Bilibili service stopped")


# ---- 插件注册 ----


@register_plugin
class DesktopPetPlugin(BasePlugin):
    """桌面宠物插件入口点。"""

    plugin_name = "desktop_pet"
    plugin_description = "具有对话、系统监控和剪贴板集成功能的桌面宠物"
    plugin_version = "1.0.0"
    configs = [DesktopPetConfig]

    def get_components(self) -> list[type]:
        """获取插件内所有组件类。"""
        return [DesktopPetAdapter]

    async def on_plugin_loaded(self) -> None:
        """插件加载时回调。适配器由 AdapterManager 管理生命周期。"""
        logger.info("DesktopPetPlugin loaded")

    async def on_plugin_unloaded(self) -> None:
        """插件卸载时回调。适配器由 AdapterManager 管理生命周期。"""
        logger.info("DesktopPetPlugin unloaded")

