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

logger = get_logger("desktop_pet")


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
        self._proactive_task_info: Any | None = None

        # --- 消息队列（桥接异步适配器 <-> GUI 线程） ---
        self._in_queue: queue.Queue[dict[str, Any]] = queue.Queue()
        self._out_queue: queue.Queue[dict[str, Any]] = queue.Queue()

        # --- GUI 线程 ---
        self._gui_thread: threading.Thread | None = None
        self._gui_ready = threading.Event()

        # --- 服务 ---
        self._day_night_service: Any = None
        self._system_monitor_service: Any = None
        self._clipboard_service: Any = None
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

        # 调用父类启动（触发 on_adapter_loaded 钩子，启动健康检查）
        await super().start()

        # 启动 in_queue 轮询任务
        tm = get_task_manager()
        self._in_queue_task_info = tm.create_task(
            self._poll_in_queue(),
            name="desktop_pet_in_queue_poll",
            daemon=True,
        )

        # 启动主动聊天定时任务（仅在启用时创建）
        if self._config and self._config.proactive.enabled:
            self._proactive_task_info = tm.create_task(
                self._proactive_loop(),
                name="desktop_pet_proactive",
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

        # 取消主动聊天定时任务
        if self._proactive_task_info:
            tm = get_task_manager()
            try:
                tm.cancel_task(self._proactive_task_info.task_id)
            except Exception:
                pass
            self._proactive_task_info = None

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

            # 连接托盘信号到 GUI 动作
            tray_manager.action_show.connect(pet_window.show)
            tray_manager.action_hide.connect(pet_window.hide)
            tray_manager.action_quit.connect(app.quit)

            # 构建聊天窗口（初始隐藏）
            chat_window = ChatWindow(config=self._config)

            # 连接聊天窗口打开信号（显示前先定位）
            def _show_chat_window() -> None:
                pet_window.position_chat_window(chat_window)
                chat_window.show()

            tray_manager.action_chat.connect(_show_chat_window)
            pet_window.chat_requested.connect(_show_chat_window)

            # 连接聊天窗口消息发送信号
            def _on_message_sent(text: str) -> None:
                if text.strip():
                    self._in_queue.put({"text": text})
                    chat_window.append_message("user", text)

            chat_window.message_sent.connect(_on_message_sent)

            # 聊天窗口跟随桌宠移动
            pet_window.pet_moved.connect(
                lambda: pet_window.position_chat_window(chat_window)
                if chat_window.isVisible() else None
            )

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
                    chat_window.append_message(msg.get("role", "bot"), msg.get("text", ""))
        except queue.Empty:
            pass

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
            except Exception:
                logger.exception("Error polling in_queue")
                await asyncio.sleep(0.5)

    async def _forward_to_core(self, msg: dict[str, Any] | str) -> None:
        """将消息转发到核心处理管道。

        Args:
            msg: 消息字典或字符串。字典会传递完整信息给 from_platform_message。
        """
        if isinstance(msg, dict):
            text = msg.get("text", "")
            if text.strip():
                await self.on_platform_message(msg)
        else:
            text = str(msg)
            if text.strip():
                await self.on_platform_message(text)

    async def _proactive_loop(self) -> None:
        """定时主动聊天循环。

        按配置的间隔检查是否满足条件：
        - proactive.enabled 为 True
        - 当前不是夜晚模式
        - 配置的 prompt 非空

        满足条件时发送主动聊天提示词到核心处理管道。
        """
        while self._running:
            try:
                # 等待配置的间隔时间
                await asyncio.sleep(self._config.proactive.interval)

                # 检查是否启用
                if not self._config.proactive.enabled:
                    continue

                # 检查睡眠模式是否启用且当前为夜间
                if self._config.sleep.enabled and self._day_night_service and self._day_night_service.is_night:
                    continue

                # 发送主动聊天消息
                prompt = self._config.proactive.prompt.strip()
                if prompt:
                    logger.info("Sending proactive chat message")
                    # 在 ChatWindow 中显示系统触发消息
                    self._out_queue.put({"action": "append_chat", "role": "system", "text": prompt})
                    await self.on_platform_message({
                        "text": prompt,
                        "is_proactive": True,
                    })
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Error in proactive loop")
                await asyncio.sleep(5)

    # ---- 消息转换 ----

    async def from_platform_message(self, raw: Any) -> MessageEnvelope | None:
        """将用户输入文本转换为 MessageEnvelope（私聊环境，无 group_info）。

        不调用 from_group()，核心通过 infer_chat_type() 识别为私聊，
        跳过群聊专属的 sub 决策流程。

        支持 dict 格式的消息，可包含以下字段：
        - text: 消息文本
        - nickname: 用户显示名称（可选，默认从 chat.user_name 读取）
        - is_proactive: 是否为系统主动触发（可选，默认 False）
        """
        text = ""
        nickname = getattr(self._config.chat, "user_name", "用户") if self._config else "用户"
        is_proactive = False

        if isinstance(raw, dict):
            text = raw.get("text", "")
            nickname = raw.get("nickname", nickname)
            is_proactive = raw.get("is_proactive", False)
        else:
            text = str(raw)

        text = text.strip()
        if not text:
            return None

        if is_proactive:
            user_id = "desktop_pet_system"
            nickname = "系统提醒"
        else:
            user_id = "local_user"

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
        """将出站消息发送到 GUI 显示。"""
        seg = envelope.get("message_segment")
        text = ""
        if isinstance(seg, dict):
            if seg.get("type") == "text":
                text = str(seg.get("data") or "")
        elif isinstance(seg, list):
            for item in seg:
                if isinstance(item, dict) and item.get("type") == "text":
                    text += str(item.get("data") or "")

        if text:
            self._out_queue.put({"action": "show_dialog", "text": text})
            self._out_queue.put({"action": "append_chat", "role": "bot", "text": text})

    # ---- 服务生命周期 ----

    async def _start_services(self) -> None:
        """创建并启动所有后台服务。"""
        from .services.day_night import DayNightService
        from .services.system_monitor import SystemMonitorService
        from .services.clipboard_watcher import ClipboardWatcherService

        self._day_night_service = DayNightService(self._plugin)
        self._system_monitor_service = SystemMonitorService(self._plugin)
        self._clipboard_service = ClipboardWatcherService(self._plugin)

        await self._day_night_service.start()
        await self._system_monitor_service.start()
        await self._clipboard_service.start()
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
        self._services_started = False
        logger.info("All services stopped")


# ---- 插件注册 ----


@register_plugin
class DesktopPetPlugin(BasePlugin):
    """桌面宠物插件入口点。"""

    plugin_name = "desktop_pet"
    plugin_description = "具有对话、系统监控和剪贴板集成功能的桌面宠物"
    plugin_version = "0.1.0"
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
