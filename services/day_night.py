"""桌面宠物的日/夜循环服务。

使用睡眠时间配置分区判断日/夜：
- 白天：wake_start_hour <= 当前小时 < sleep_start_hour
- 夜晚：其他情况
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, cast

from src.core.components.base import BaseService

if TYPE_CHECKING:
    from src.core.components.base.plugin import BasePlugin
    from ..config import DesktopPetConfig


class DayNightService(BaseService):
    """日/夜循环服务。

    根据当前时间和睡眠时间配置（sleep 分区）判断日/夜模式。
    支持手动模式覆盖。
    """

    service_name = "day_night"
    service_description = "桌面宠物的日/夜循环与问候服务"
    version = "0.1.0"

    def __init__(self, plugin: BasePlugin) -> None:
        """初始化日/夜服务。

        Args:
            plugin: 父插件实例。
        """
        super().__init__(plugin)
        self._mode: str = "day"
        self._manual_override: bool = False
        self._update_mode()

    def _get_config(self) -> DesktopPetConfig | None:
        """从插件获取桌面宠物配置。"""
        if self.plugin and hasattr(self.plugin, "config"):
            return cast("DesktopPetConfig", self.plugin.config)
        return None

    def _update_mode(self) -> None:
        """根据当前时间更新模式，除非已手动覆盖。"""
        if self._manual_override:
            return

        config = self._get_config()
        if config is None:
            # 默认：白天 7-22，夜晚 23-6
            wake_hour = 7
            sleep_hour = 23
        else:
            wake_hour = config.sleep.wake_start_hour
            sleep_hour = config.sleep.sleep_start_hour

        hour = datetime.now().hour

        # 白天：wake_start_hour <= hour < sleep_start_hour
        if wake_hour <= hour < sleep_hour:
            self._mode = "day"
        else:
            self._mode = "night"

    @property
    def is_day(self) -> bool:
        """当前是否为白天模式。"""
        self._update_mode()
        return self._mode == "day"

    @property
    def is_night(self) -> bool:
        """当前是否为夜晚模式。"""
        return not self.is_day

    @property
    def mode(self) -> str:
        """当前模式：'day' 或 'night'。"""
        self._update_mode()
        return self._mode

    def get_greeting(self) -> str:
        """根据当前模式获取合适的问候语。

        Returns:
            str: 适合白天或夜晚的问候语字符串。
        """
        self._update_mode()
        if self._mode == "day":
            return "早上好！准备好迎接新的一天！"
        else:
            return "晚上好！该放松一下了！"

    def toggle_mode(self) -> str:
        """在白天和夜晚模式之间切换（手动覆盖）。

        激活手动覆盖以防止自动按时间切换。

        Returns:
            str: 切换后的新模式。
        """
        self._manual_override = True
        self._mode = "night" if self._mode == "day" else "day"
        return self._mode

    def reset_auto_mode(self) -> None:
        """重置为自动按时间检测模式。"""
        self._manual_override = False
        self._update_mode()

    async def start(self) -> None:
        """启动日/夜服务（被动服务，无需后台循环）。"""
        self._update_mode()

    async def stop(self) -> None:
        """停止日/夜服务（被动服务，无需清理）。"""
        pass
