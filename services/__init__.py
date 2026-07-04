"""桌面宠物服务包。"""

from .clipboard_watcher import ClipboardWatcherService
from .day_night import DayNightService
from .screen_watcher import ScreenWatcherService
from .system_monitor import SystemMonitorService

__all__ = [
    "ClipboardWatcherService",
    "DayNightService",
    "ScreenWatcherService",
    "SystemMonitorService",
]
