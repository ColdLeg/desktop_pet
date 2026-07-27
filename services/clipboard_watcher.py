"""桌面宠物的剪贴板监控服务。

监视系统剪贴板的文本变化，仅作为**被动上下文**注入 system reminder，
不主动触发 LLM 调用。

设计原则（被动上下文，非主动触发）：
- 剪贴板变化 → 注入 desktop_pet_clipboard reminder（ONCE 消费）
- 不投递主动消息到 in_queue，不触发 sub/actor 链路
- 当真正的主动触发源（如截图定时循环、用户发消息）调用 LLM 时，
  LLM 在那次调用中会读取到剪贴板 reminder 作为上下文
- 这样避免"用户每复制一次就触发一次 LLM"的扰民与浪费

与截图（source="screenshot"，定时主动触发）严格区分职责。
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
    """剪贴板监控服务（被动上下文注入，不主动触发 LLM）。

    定期检查系统剪贴板的文本内容，检测变化时注入 system reminder。
    reminder 标注来源为剪贴板，供后续 LLM 调用读取，但自身不触发调用。
    """

    name = "clipboard_watcher"
    service_description = "桌面宠物的剪贴板变化检测服务（被动上下文）"
    version = "1.0.0"

    def __init__(self, plugin: BasePlugin) -> None:
        """初始化剪贴板监控服务。"""
        super().__init__(plugin)
        self._config: DesktopPetConfig | None = None
        self._task: asyncio.Task | None = None
        self._last_text: str = ""
        self._log = get_logger("desktop_pet.clipboard")
        # 保留 adapter 引用接口以兼容注入调用，但不再用于主动消息投递
        self._adapter: Any = None

    def bind_adapter(self, adapter: Any) -> None:
        """注入 DesktopPetAdapter 实例（兼容接口，当前不主动投递消息）。

        剪贴板服务只注入被动 reminder，不调用 adapter 投递主动消息。
        保留此方法仅为未来扩展（如需"读取剪贴板"工具调用时复用）。
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
            self._log.info("Clipboard watcher started (passive context only)")
        else:
            self._log.info("Clipboard watcher disabled, skipping")

    async def stop(self) -> None:
        """停止剪贴板监控循环。"""
        if self._task:
            self._task.cancel()
            self._task = None
            self._log.info("Clipboard watcher stopped")

    async def _watch_loop(self) -> None:
        """主剪贴板监控循环（仅注入被动 reminder，不触发 LLM）。"""
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
                    # 仅注入被动 system reminder（ONCE 消费）
                    # 不投递主动消息——剪贴板是"被读取"的上下文，不是"触发读取"的事件
                    # 当截图定时循环或用户发消息触发 LLM 时，LLM 会读取到此 reminder
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
            except Exception:
                self._log.error("Clipboard watch check failed", exc_info=True)
