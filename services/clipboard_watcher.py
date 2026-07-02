"""桌面宠物的剪贴板监控服务。

监视系统剪贴板的文本变化并记录新内容。
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, cast

from src.core.components.base import BaseService
from src.app.plugin_system.api.log_api import get_logger

if TYPE_CHECKING:
    from src.core.components.base.plugin import BasePlugin
    from ..config import DesktopPetConfig


class ClipboardWatcherService(BaseService):
    """剪贴板监控服务。

    定期检查系统剪贴板的文本内容，检测变化并记录日志。
    """

    service_name = "clipboard_watcher"
    service_description = "桌面宠物的剪贴板变化检测服务"
    version = "0.1.0"

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
                    self._last_text = current[:max_len] if len(current) > max_len else current
                    preview = self._last_text[:50].replace("\n", " ")
                    self._log.info("Clipboard changed: %s...", preview)
            except Exception:
                self._log.exception("Clipboard watch check failed")
