# -*- coding: utf-8 -*-
"""桌面宠物的系统托盘管理器。

提供 QSystemTrayIcon 及右键上下文菜单，包含：
- 显示/隐藏 切换
- 日/夜模式切换（占位）
- 系统信息显示（占位）
- 设置快捷方式（占位）
- 退出

同时通过 Qt 信号广播托盘事件供适配器消费。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QAction, QIcon, QPixmap
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon, QMessageBox

if TYPE_CHECKING:
    from ..config import DesktopPetConfig


class TrayManager(QObject):
    """系统托盘图标和上下文菜单管理器。

    信号：
        action_show: 用户想要显示宠物窗口时触发。
        action_hide: 用户想要隐藏宠物窗口时触发。
        action_chat: 用户请求打开聊天窗口时触发。
        action_toggle_daynight: 用户切换日/夜模式时触发。
        action_show_info: 用户请求系统信息时触发。
        action_quit: 用户选择退出时触发。
    """

    action_show = Signal()
    action_hide = Signal()
    action_chat = Signal()
    action_toggle_daynight = Signal()
    action_show_info = Signal()
    action_quit = Signal()

    # 硬编码托盘属性
    TOOLTIP = "MoFox 桌面宠物"
    SHOW_ICON = True
    CONFIRM_EXIT = True

    def __init__(
        self,
        config: DesktopPetConfig | None = None,
        parent: QObject | None = None,
    ) -> None:
        """初始化托盘管理器。

        Args:
            config: 插件配置；用于图标路径解析。
            parent: 父级 QObject。
        """
        super().__init__(parent)
        self._config = config

        # --- 状态 ---
        self._tray_icon: QSystemTrayIcon | None = None

        if self.SHOW_ICON:
            self._build_tray()

    # ---- 设置 ----

    def _build_tray(self) -> None:
        """构建系统托盘图标和上下文菜单。"""
        icon = self._create_icon()
        self._tray_icon = QSystemTrayIcon(icon, self)
        self._tray_icon.setToolTip(self.TOOLTIP)

        menu = QMenu()

        self._show_action = QAction("显示宠物", self)
        self._show_action.triggered.connect(self.action_show.emit)
        menu.addAction(self._show_action)

        self._hide_action = QAction("隐藏宠物", self)
        self._hide_action.triggered.connect(self.action_hide.emit)
        menu.addAction(self._hide_action)

        chat_action = QAction("聊天...", self)
        chat_action.triggered.connect(self.action_chat.emit)
        menu.addAction(chat_action)

        menu.addSeparator()

        daynight_action = QAction("切换日/夜模式", self)
        daynight_action.triggered.connect(self.action_toggle_daynight.emit)
        menu.addAction(daynight_action)

        info_action = QAction("系统信息...", self)
        info_action.triggered.connect(self.action_show_info.emit)
        menu.addAction(info_action)

        menu.addSeparator()

        quit_action = QAction("退出", self)
        quit_action.triggered.connect(self._on_quit)
        menu.addAction(quit_action)

        self._tray_icon.setContextMenu(menu)
        self._tray_icon.activated.connect(self._on_tray_activated)

    def _create_icon(self) -> QIcon:
        """创建托盘图标。

        尝试从配置的 default_image 加载；回退到空图标。

        Returns:
            QIcon 实例。
        """
        icon_path = self._resolve_icon_path()
        if icon_path:
            pixmap = QPixmap(str(icon_path))
            if not pixmap.isNull():
                return QIcon(pixmap)
        return QIcon()

    def _resolve_icon_path(self) -> str | None:
        """从配置的 default_image 解析托盘图标路径。

        Returns:
            绝对路径字符串，或 None。
        """
        if self._config and self._config.pet.default_image:
            from pathlib import Path

            img = self._config.pet.default_image
            path = Path(img)
            if not path.is_absolute():
                path = Path(__file__).resolve().parent.parent / img
            return str(path) if path.exists() else None
        return None

    # ---- 公开 API ----

    def show(self) -> None:
        """如果托盘图标已隐藏则显示。"""
        if self._tray_icon:
            self._tray_icon.show()

    def hide(self) -> None:
        """隐藏托盘图标。"""
        if self._tray_icon:
            self._tray_icon.hide()

    def set_tooltip(self, text: str) -> None:
        """更新工具提示文本。

        Args:
            text: 新的工具提示文本。
        """
        if self._tray_icon:
            self._tray_icon.setToolTip(text)

    def show_notification(self, title: str, message: str) -> None:
        """显示系统托盘气泡通知。

        Args:
            title: 通知标题。
            message: 通知正文。
        """
        if self._tray_icon and self._tray_icon.supportsMessages():
            self._tray_icon.showMessage(
                title,
                message,
                QSystemTrayIcon.MessageIcon.Information,
                5000,
            )

    # ---- 内部方法 ----

    def _on_tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        """处理托盘图标激活事件（例如左键单击）。"""
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self.action_show.emit()

    def _on_quit(self) -> None:
        """处理退出动作，带可选的确认对话框。"""
        if self.CONFIRM_EXIT:
            reply = QMessageBox.question(
                None,
                "退出",
                "确定要退出桌面宠物吗？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return

        self.action_quit.emit()
        QApplication.quit()