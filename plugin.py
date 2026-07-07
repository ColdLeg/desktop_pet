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
from typing import Any

from src.app.plugin_system.api.log_api import get_logger
from src.app.plugin_system.base import BasePlugin, BaseAdapter
from src.core.components.loader import register_plugin
from src.core.prompt import (
    SystemReminderBucket,
    SystemReminderConsumeType,
    SystemReminderInsertType,
    get_system_reminder_store,
)
from src.kernel.concurrency import get_task_manager
from mofox_wire import MessageBuilder, MessageEnvelope

from .config import DesktopPetConfig

# 淡蓝色边框颜色（print_all_logs 开启时使用）
_PANEL_BORDER_COLOR = "#9EF6FF"


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

    adapter_name = "desktop_pet_adapter"
    adapter_description = "具有系统感知能力的桌面宠物 GUI 适配器"
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

        # --- 服务 ---
        self._day_night_service: Any = None
        self._system_monitor_service: Any = None
        self._clipboard_service: Any = None
        self._screen_watcher_service: Any = None
        self._services_started: bool = False

    # ---- 适配器生命周期 ----

    async def start(self) -> None:
        """启动适配器：加载配置、启动 GUI 线程、启动服务。"""
        logger.info("DesktopPetAdapter starting")
        self._loop = asyncio.get_running_loop()

        # 加载配置
        if hasattr(self._plugin, "config") and isinstance(self._plugin.config, DesktopPetConfig):
            self._config = self._plugin.config
        else:
            self._config = DesktopPetConfig()
            logger.warning("No DesktopPetConfig found on plugin, using defaults")

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

        # 注入桌宠身份 system reminder
        if self._config and self._config.chat.system_prompt.strip():
            try:
                store = get_system_reminder_store()
                store.set(
                    bucket=SystemReminderBucket.ACTOR,
                    name="desktop_pet_identity",
                    content=self._config.chat.system_prompt,
                    insert_type=SystemReminderInsertType.FIXED,
                    consume=SystemReminderConsumeType.FOREVER,
                )
                logger.info("System reminder 'desktop_pet_identity' injected")
            except Exception:
                logger.exception("Failed to inject system reminder")

        # 注入用户 QQ 号 system reminder
        if self._config and self._config.chat.user_qq_id.strip():
            try:
                store = get_system_reminder_store()
                store.set(
                    bucket=SystemReminderBucket.ACTOR,
                    name="desktop_pet_user_qq",
                    content=f"当前触发消息用户的 QQ 号是 {self._config.chat.user_qq_id.strip()}",
                    insert_type=SystemReminderInsertType.FIXED,
                    consume=SystemReminderConsumeType.FOREVER,
                )
                logger.info("System reminder 'desktop_pet_user_qq' injected")
            except Exception:
                logger.exception("Failed to inject user_qq system reminder")

        # 调用父类启动（触发 on_adapter_loaded 钩子，启动健康检查）
        await super().start()

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

        # 清理桌宠身份 system reminder
        try:
            store = get_system_reminder_store()
            store.delete(bucket=SystemReminderBucket.ACTOR, name="desktop_pet_identity")
            logger.info("System reminder 'desktop_pet_identity' cleaned up")
        except Exception:
            logger.exception("Failed to cleanup system reminder")

        # 清理用户 QQ 号 system reminder
        try:
            store = get_system_reminder_store()
            store.delete(bucket=SystemReminderBucket.ACTOR, name="desktop_pet_user_qq")
        except Exception:
            logger.exception("Failed to cleanup user_qq system reminder")

        # 通知 GUI 线程退出
        self._out_queue.put({"action": "quit"})

        await self._stop_services()

        if self._gui_thread and self._gui_thread.is_alive():
            self._gui_thread.join(timeout=5)

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
            pet_window.show()

            # 构建托盘
            tray_manager = TrayManager(config=self._config)
            tray_manager.show()

            # 构建聊天窗口（初始隐藏）
            chat_window = ChatWindow(config=self._config)

            # 连接托盘信号到 GUI 动作
            tray_manager.action_show.connect(pet_window.show)
            tray_manager.action_hide.connect(pet_window.hide)
            tray_manager.action_quit.connect(app.quit)

            # 注入 TrayManager 给 PetWindow 以支持右键菜单
            pet_window.set_tray_manager(tray_manager)

            # 透明度（同时应用到桌宠和聊天窗口）
            def _apply_opacity(opacity: float) -> None:
                pet_window.set_opacity(opacity)
                chat_window.setWindowOpacity(opacity)
            tray_manager.action_set_opacity.connect(_apply_opacity)

            # 聊天位置模式（运行时切换配置）
            def _apply_chat_position_mode(mode: str) -> None:
                try:
                    if self._config and getattr(self._config, "chat", None):
                        self._config.chat.chat_position_mode = mode
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
                    try:
                        if hasattr(self._plugin, "save_config"):
                            self._plugin.save_config()
                    except Exception:
                        pass
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
                    try:
                        if hasattr(self._plugin, "save_config"):
                            self._plugin.save_config()
                    except Exception:
                        pass
                    logger.info(f"Font scale set to: {scale}")
                except Exception:
                    logger.exception("Failed to apply font scale")
            tray_manager.action_set_font_scale.connect(_apply_font_scale)

            # 连接聊天窗口打开信号（显示前先定位）
            def _show_chat_window() -> None:
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

            tray_manager.action_chat.connect(_show_chat_window)
            pet_window.chat_requested.connect(_show_chat_window)
            tray_manager.action_chat_hide.connect(chat_window.hide)

            # 连接聊天窗口消息发送信号
            def _on_message_sent(text: str) -> None:
                if text.strip():
                    self._in_queue.put({"text": text})
                    chat_window.append_message("user", text)

            chat_window.message_sent.connect(_on_message_sent)

            # 聊天窗口可见性变化 -> 同步适配器 + 触发历史回读 + 加速 pet 输出
            def _on_visibility_changed(visible: bool) -> None:
                if visible:
                    self._chat_visible.set()
                    # 加速 pet 当前输出并 copy 文本到 chat
                    dialog_box = getattr(pet_window, "_dialog_box", None)
                    if dialog_box is not None and dialog_box.is_outputting():
                        cur_text = dialog_box.current_text
                        if cur_text:
                            chat_window.append_message("bot", cur_text)
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
                        # 尝试持久化（如果 config 支持 save）
                        try:
                            if hasattr(self._plugin, "save_config"):
                                self._plugin.save_config()
                        except Exception:
                            pass
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
                pet_window.position_chat_window_default(chat_window)

            pet_window.pet_moved_delta.connect(_on_pet_moved_delta)

            # 定时器轮询 out_queue（适配器 -> GUI 消息）
            poll_timer = QTimer()
            poll_timer.timeout.connect(lambda: self._poll_out_queue(pet_window, tray_manager, chat_window))
            poll_timer.start(100)  # 100 毫秒轮询间隔

            # 标记 GUI 已就绪
            self._gui_ready.set()

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
                    pet_window.show_dialog(msg.get("text", ""))
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
                    )
                elif action == "load_chat_history":
                    chat_window.load_history(msg.get("messages", []))
                elif action == "take_screenshot":
                    self._handle_take_screenshot(pet_window, msg.get("seq", 0))
        except queue.Empty:
            pass

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
    ) -> None:
        """根据 chat_window 可见性路由消息到正确窗口。

        chat 可见 → 进 chat 历史（若未开启显示区，由 chat_window 自动临时显示）
        chat 不可见 → 进 pet 气泡（emoji 在 pet 上降级为占位文本）

        Args:
            text: 消息文本。
            role: "bot"/"system"/"error"。
            reply_to: 可选，被回复消息 ID（仅 chat_window 显示）。
            emoji_bytes: 可选，emoji 段的图片字节（GIF/PNG）。
        """
        if not text and not emoji_bytes:
            return
        target = "chat" if self._chat_visible.is_set() else "pet"
        logger.info(f"_route_out_message -> {target}, role={role}, text={text[:50]!r}, emoji={len(emoji_bytes)}B")
        if self._chat_visible.is_set():
            self._out_queue.put({
                "action": "append_chat",
                "role": role,
                "text": text,
                "reply_to": reply_to,
                "emoji_bytes": emoji_bytes,
            })
        else:
            # pet 气泡不支持图片，emoji 降级为占位文本
            display = text
            if emoji_bytes and not text:
                display = "[表情包]"
            self._out_queue.put({"action": "show_dialog", "text": display})
        if role == "error":
            logger.exception(text)

    @staticmethod
    def _decode_emoji_data(data: Any) -> bytes:
        """把 message_segment 中 emoji 段的 data 解码为图片字节。

        支持的输入：
        - data URL: "data:image/gif;base64,XXXX"
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
        if s.startswith("data:"):
            # data URL 格式
            try:
                _, b64 = s.split(",", 1)
                import base64
                return base64.b64decode(b64)
            except Exception:
                return b""
        # 纯 base64
        try:
            import base64
            return base64.b64decode(s)
        except Exception:
            return b""

    def _request_chat_history(self) -> None:
        """异步拉取私聊 stream 历史并通过 out_queue 推给 chat_window。"""
        if not self._loop:
            return
        self._loop.create_task(self._fetch_and_send_history())

    async def _fetch_and_send_history(self) -> None:
        """从 StreamManager 拉历史消息，封装成 chat_window 可识别的格式。"""
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
            # 拉最近 50 条
            try:
                result = sm.get_stream_messages(stream_id, limit=50, offset=0)
                # 兼容协程与同步两种实现
                if hasattr(result, "__await__"):
                    msgs = await result
                else:
                    msgs = result
            except Exception:
                logger.exception("Failed to fetch stream messages")
                return
            if not msgs:
                return
            history: list[dict[str, Any]] = []
            for m in msgs:
                # 字段名按主程序 Messages 表结构
                role = "bot" if getattr(m, "sender_role", "") == "bot" else "user"
                content = getattr(m, "content", "") or getattr(m, "text", "") or ""
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
            if history:
                self._out_queue.put({"action": "load_chat_history", "messages": history})
        except Exception:
            logger.exception("Failed to fetch chat history")

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
            user_id = "desktop_pet_system"
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
            .build()
        )
        return envelope

    async def _send_platform_message(self, envelope: MessageEnvelope) -> None:
        """将出站消息发送到 GUI 显示。

        解析 message_segment 提取文本、emoji、reply 段，按 chat_window 可见性路由。
        emoji 段携带 base64 编码的图片（GIF/PNG），转换为 QPixmap 渲染。
        若消息来自截图 stream（metadata.source="screenshot"），同步把回复写回用户私聊 stream 历史。
        """
        logger.info(f"_send_platform_message called, chat_visible={self._chat_visible.is_set()}, envelope={envelope}")
        seg = envelope.get("message_segment")
        text = ""
        reply_to = ""
        emoji_bytes: bytes = b""
        if isinstance(seg, dict):
            t = seg.get("type")
            d = seg.get("data") or ""
            if t == "text":
                text = str(d)
            elif t == "reply":
                reply_to = str(d)
            elif t == "emoji":
                emoji_bytes = self._decode_emoji_data(d)
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

        if not text and not emoji_bytes:
            return

        # 检测消息来源是否为截图 stream
        meta = envelope.get("metadata") or {}
        source = ""
        if isinstance(meta, dict):
            source = meta.get("source", "")

        # 路由到 chat_window 或 pet 气泡
        self._route_out_message(text, role="bot", reply_to=reply_to, emoji_bytes=emoji_bytes)

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
            sm.add_sent_message_to_history(
                stream_id=stream_id,
                text=text,
                bot_id=bot_id,
            )
        else:
            logger.warning("StreamManager has no add_sent_message_to_history; skip write-back")

    # ---- 主动消息投递（供 services 调用） ----

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
        self._services_started = False
        logger.info("All services stopped")


# ---- 插件注册 ----


@register_plugin
class DesktopPetPlugin(BasePlugin):
    """桌面宠物插件入口点。"""

    plugin_name = "desktop_pet"
    plugin_description = "具有对话、系统监控和剪贴板集成功能的桌面宠物"
    plugin_version = "0.2.0"
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
