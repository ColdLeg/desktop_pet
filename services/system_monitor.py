"""桌面宠物的系统资源监控服务。

使用 psutil 监控 CPU 和内存使用率，当超过用户配置的阈值时记录告警日志。
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, cast

from src.core.components.base import BaseService
from src.app.plugin_system.api.log_api import get_logger

if TYPE_CHECKING:
    from src.core.components.base.plugin import BasePlugin
    from ..config import DesktopPetConfig


class SystemMonitorService(BaseService):
    """系统资源监控服务。

    定期检查 CPU 和内存使用率。当超过（config.system_monitor 中的）阈值时记录告警日志。
    """

    service_name = "system_monitor"
    service_description = "桌面宠物的 CPU/内存使用率监控服务"
    version = "0.1.0"

    def __init__(self, plugin: BasePlugin) -> None:
        """初始化系统监控服务。

        Args:
            plugin: 父插件实例。
        """
        super().__init__(plugin)
        self._config: DesktopPetConfig | None = None
        self._task: asyncio.Task | None = None
        self._log = get_logger("desktop_pet.system_monitor")

    def _get_config(self) -> DesktopPetConfig | None:
        """从插件获取桌面宠物配置。"""
        if self.plugin and hasattr(self.plugin, "config"):
            return cast("DesktopPetConfig", self.plugin.config)
        return None

    async def start(self) -> None:
        """如果配置中启用了监控，则启动监控循环。"""
        self._config = self._get_config()
        if self._config and self._config.system_monitor.enabled:
            self._task = asyncio.create_task(self._monitor_loop())
            self._log.info("System monitor started")
        else:
            self._log.info("System monitor disabled, skipping")

    async def stop(self) -> None:
        """停止监控循环。"""
        if self._task:
            self._task.cancel()
            self._task = None
            self._log.info("System monitor stopped")

    async def _monitor_loop(self) -> None:
        """主监控循环。"""
        try:
            import psutil
        except ImportError:
            self._log.warning("psutil not installed, system monitor disabled")
            return

        config = self._config
        if not config:
            return

        interval = config.system_monitor.check_interval
        cpu_thresh = config.system_monitor.cpu_threshold
        mem_thresh = config.system_monitor.memory_threshold

        while True:
            try:
                cpu = psutil.cpu_percent(interval=1)
                mem = psutil.virtual_memory().percent

                if cpu > cpu_thresh:
                    self._log.warning("High CPU: %.1f%% (threshold: %.1f%%)", cpu, cpu_thresh)
                if mem > mem_thresh:
                    self._log.warning("High memory: %.1f%% (threshold: %.1f%%)", mem, mem_thresh)
            except Exception:
                self._log.exception("System monitor check failed")

            await asyncio.sleep(interval)
