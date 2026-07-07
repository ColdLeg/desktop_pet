"""桌面宠物的剪贴板监控服务。

监视系统剪贴板的文本变化并记录新内容。
变化时注入 desktop_pet_clipboard system reminder（明确标注来源为剪贴板），
与截图（source="screenshot"）来源严格区分，避免消息路由混淆。

若注入 adapter，剪贴板变化还会作为 source="clipboard" 的主动消息投递到
in_queue，走完整 sub+actor 链路；from_platform_message 据此分流。
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, cast

from src.core.components.base import BaseService
from src.app.plugin_system.api.log_api import get_logger
from src.core.prompt import (
    SystemReminderBucket,
    SystemReminderConsumeType,
    SystemReminderInsertType,
    get_system_reminder_store,
)

if TYPE_CHECKING:
    from src.core.components.base.plugin import BasePlugin
    from ..config import DesktopPetConfig


class ClipboardWatcherService(BaseService):
    """剪贴板监控服务。

    定期检查系统剪贴板的文本内容，检测变化并记录日志。
    变化时注入 system reminder（标注来源为剪贴板），可选投递主动消息。
    """

    service_name = "clipboard_watcher"
    service_description = "桌面宠物的剪贴板变化检测服务"
    version = "0.2.0"

    def __init__(self, plugin: BasePlugin) -> None:
        """初始化剪贴板监控服务。

        Args:
            plugin: 父插件实例。
        """
        super().__init__(plugin)
        self._config: DesktopPetConfig | None = None
        self._task: asyncio.Task | None = None
        self._last_text: str = ""
        self._log = get_logger("desktop_pet.clipboard")
        # 由 adapter 注入：用于把剪贴板变化作为主动消息投递（可选）
        self._adapter: Any = None

    def bind_adapter(self, adapter: Any) -> None:
        """注入 DesktopPetAdapter 实例（可选）。

        注入后，剪贴板变化会同时作为 source="clipboard" 的主动消息
        投递到 in_queue，走完整 sub+actor 链路，与截图（source="screenshot"）
        在 from_platform_message 中严格区分路由。
        """
        self._adapter = adapter

    def _get_config(self) -> DesktopPetConfig | None:
        """从插件获取桌面宠物配置。"""
        if self.plugin and hasattr(self.plugin, "config"):
            return cast("DesktopPetConfig", self.plugin.config)
        return None

    async def start(self) -> None:
        """如果配置中启用了监控，则启动剪贴板监控循环。"""
        self._config = self._get_config()
        if self._config and self._config.clipboard.enabled:
            self._task = asyncio.create_task(self._watch_loop())
            self._log.info("Clipboard watcher started")
        else:
            self._log.info("Clipboard watcher disabled, skipping")

    async def stop(self) -> None:
        """停止剪贴板监控循环。"""
        if self._task:
            self._task.cancel()
            self._task = None
            self._log.info("Clipboard watcher stopped")

    async def _watch_loop(self) -> None:
        """主剪贴板监控循环。"""
        try:
            import pyperclip
        except ImportError:
            self._log.warning("pyperclip not installed, clipboard watcher disabled")
            return

        config = self._config
        if not config:
            return

        max_len = config.clipboard.max_content_length

        # 初始化当前剪贴板内容
        try:
            self._last_text = pyperclip.paste() or ""
        except Exception:
            self._last_text = ""

        while True:
            await asyncio.sleep(1)
            try:
                current = pyperclip.paste() or ""
                if current != self._last_text:
                    # 先保存完整内容用于下次比较，再截断用于显示和注入
                    self._last_text = current
                    display_text = current[:max_len] if len(current) > max_len else current
                    preview = display_text[:50].replace("\n", " ")
                    self._log.info(f"Clipboard changed: {preview}...")
                    # 注入 system reminder（明确标注来源为剪贴板，非截图）
                    try:
                        store = get_system_reminder_store()
                        store.set(
                            bucket=SystemReminderBucket.ACTOR,
                            name="desktop_pet_clipboard",
                            content=f"[来源：剪贴板] 用户刚刚复制了内容：{display_text}",
                            insert_type=SystemReminderInsertType.DYNAMIC,
                            consume=SystemReminderConsumeType.ONCE,
                        )
                    except Exception:
                        self._log.error("Failed to inject clipboard reminder", exc_info=True)
                    # 可选：作为主动消息投递（走 sub+actor 链路）
                    # 仅当 adapter 已注入且内容非空时
                    if self._adapter is not None and display_text.strip():
                        try:
                            self._adapter.enqueue_proactive_message(
                                text=display_text,
                                source="clipboard",
                            )
                        except Exception:
                            self._log.error(
                                "Failed to enqueue clipboard proactive message",
                                exc_info=True,
                            )
            except Exception:
                self._log.error("Clipboard watch check failed", exc_info=True)
