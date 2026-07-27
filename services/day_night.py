"""桌面宠物的日/夜循环服务。

使用睡眠时间配置分区判断日/夜：
- 白天：wake_start_hour <= 当前小时 < sleep_start_hour
- 夜晚：其他情况

修复：添加 asyncio 定时循环，每 CHECK_INTERVAL_SEC 秒主动检查昼夜切换，
避免应用跨过 sleep_start_hour 边界运行时无人查询导致 reminder 不注入。
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import TYPE_CHECKING, cast

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


class DayNightService(BaseService):
    """日/夜循环服务。

    根据当前时间和睡眠时间配置（sleep 分区）判断日/夜模式。
    支持手动模式覆盖。
    通过定时循环主动检测切换，无需依赖外部查询。
    """

    name = "day_night"
    service_description = "桌面宠物的日/夜循环与问候服务"
    version = "1.0.0"

    # 定时检查间隔（秒）。600 秒 = 10 分钟，足够捕捉小时级切换。
    CHECK_INTERVAL_SEC = 600

    def __init__(self, plugin: BasePlugin) -> None:
        """初始化日/夜服务。

        Args:
            plugin: 父插件实例。
        """
        super().__init__(plugin)
        self._mode: str = "day"
        self._manual_override: bool = False
        self._log = get_logger("desktop_pet.day_night")
        self._task: asyncio.Task | None = None
        self._update_mode()

    def _inject_reminder(self) -> None:
        """把昼夜状态注入 actor system reminder。"""
        try:
            store = get_system_reminder_store()
            content = "当前是白天" if self._mode == "day" else "当前是夜晚"
            store.set(
                bucket=SystemReminderBucket.ACTOR,
                name="desktop_pet_day_night",
                content=content,
                insert_type=SystemReminderInsertType.DYNAMIC,
                consume=SystemReminderConsumeType.ONCE,
            )
        except Exception:
            self._log.error("Failed to inject day_night reminder", exc_info=True)

    def _get_config(self) -> DesktopPetConfig | None:
        """从插件获取桌面宠物配置。"""
        if self.plugin and hasattr(self.plugin, "config"):
            return cast("DesktopPetConfig", self.plugin.config)
        return None

    def _update_mode(self) -> None:
        """根据当前时间更新模式，除非已手动覆盖。

        模式切换时注入 reminder。
        """
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
        old_mode = self._mode

        # 白天：wake_start_hour <= hour < sleep_start_hour
        if wake_hour <= hour < sleep_hour:
            self._mode = "day"
        else:
            self._mode = "night"

        # 模式切换时注入 reminder
        if old_mode != self._mode:
            self._log.info(f"Day/night mode changed: {old_mode} -> {self._mode}")
            self._inject_reminder()

    async def _watch_loop(self) -> None:
        """定时检查昼夜切换的后台循环。"""
        try:
            while True:
                await asyncio.sleep(self.CHECK_INTERVAL_SEC)
                # 配置可能运行时变更，每次重新读取
                self._update_mode()
        except asyncio.CancelledError:
            pass
        except Exception:
            self._log.error("Day/night watch loop error", exc_info=True)

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
        """根据当前模式获取合适的问候语。"""
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
        self._inject_reminder()  # 手动切换也注入
        return self._mode

    def reset_auto_mode(self) -> None:
        """重置为自动按时间检测模式。"""
        self._manual_override = False
        self._update_mode()

    async def start(self) -> None:
        """启动日/夜服务：注入初始状态 + 启动定时检查循环。"""
        self._update_mode()
        # 启动时注入初始状态
        self._inject_reminder()
        # 启动定时检查循环（主动检测跨边界切换）
        self._task = asyncio.create_task(self._watch_loop())
        self._log.info("Day/night service started with periodic check")

    async def stop(self) -> None:
        """停止日/夜服务：取消定时循环。"""
        if self._task:
            self._task.cancel()
            self._task = None
            self._log.info("Day/night service stopped")
