# -*- coding: utf-8 -*-
"""桌面宠物的系统托盘管理器。

提供 QSystemTrayIcon 及右键上下文菜单，包含：
- 显示/隐藏 切换
- 日/夜模式切换（占位）
- 设置快捷方式（占位）
- 退出

同时通过 Qt 信号广播托盘事件供适配器消费。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, Signal, Qt
from PySide6.QtGui import QAction, QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer
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
    action_chat_hide = Signal()
    action_toggle_daynight = Signal()
    action_show_info = Signal()
    action_quit = Signal()
    action_set_opacity = Signal(float)
    action_set_chat_position_mode = Signal(str)

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
        self.build_menu(menu, with_quit_confirm=True)

        self._tray_icon.setContextMenu(menu)
        self._tray_icon.activated.connect(self._on_tray_activated)

    def build_menu(self, menu: QMenu, *, with_quit_confirm: bool = True) -> None:
        """把共用菜单项填入指定 menu。

        托盘菜单和桌宠右键菜单都复用此方法，保证菜单项一致。
        子菜单：透明度、Pet/Chat 也一并加入。

        Args:
            menu: 要填充的 QMenu。
            with_quit_confirm: 退出时是否弹确认对话框（托盘场景通常需要，
                右键桌宠场景也可保持一致）。
        """
        # 聊天
        chat_action = QAction("聊天...", self)
        chat_action.triggered.connect(self.action_chat.emit)
        menu.addAction(chat_action)

        hide_chat_action = QAction("隐藏聊天窗口", self)
        hide_chat_action.triggered.connect(self.action_chat_hide.emit)
        menu.addAction(hide_chat_action)

        menu.addSeparator()

        # 透明度子菜单
        opacity_menu = menu.addMenu("透明度")
        for pct in (25, 50, 75, 100):
            act = QAction(f"{pct}%", opacity_menu)
            act.triggered.connect(lambda checked=False, p=pct: self.action_set_opacity.emit(p / 100.0))
            opacity_menu.addAction(act)

        # Pet/Chat 子菜单
        petchat_menu = menu.addMenu("Pet/Chat")
        pet_show = QAction("显示桌宠", petchat_menu)
        pet_show.triggered.connect(self.action_show.emit)
        petchat_menu.addAction(pet_show)
        pet_hide = QAction("隐藏桌宠", petchat_menu)
        pet_hide.triggered.connect(self.action_hide.emit)
        petchat_menu.addAction(pet_hide)
        petchat_menu.addSeparator()
        chat_show = QAction("显示聊天", petchat_menu)
        chat_show.triggered.connect(self.action_chat.emit)
        petchat_menu.addAction(chat_show)
        chat_hide = QAction("隐藏聊天", petchat_menu)
        chat_hide.triggered.connect(self.action_chat_hide.emit)
        petchat_menu.addAction(chat_hide)
        petchat_menu.addSeparator()
        # 聊天位置模式
        mode_independent = QAction("聊天独立位置", petchat_menu)
        mode_independent.setCheckable(True)
        mode_independent.setChecked(self._current_chat_position_mode() == "independent")
        mode_independent.triggered.connect(
            lambda checked=False: self.action_set_chat_position_mode.emit("independent")
        )
        petchat_menu.addAction(mode_independent)
        mode_follow = QAction("聊天跟随桌宠", petchat_menu)
        mode_follow.setCheckable(True)
        mode_follow.setChecked(self._current_chat_position_mode() == "follow")
        mode_follow.triggered.connect(
            lambda checked=False: self.action_set_chat_position_mode.emit("follow")
        )
        petchat_menu.addAction(mode_follow)

        menu.addSeparator()

        # 日夜模式
        daynight_action = QAction("切换日/夜模式", self)
        daynight_action.triggered.connect(self.action_toggle_daynight.emit)
        menu.addAction(daynight_action)

        menu.addSeparator()

        # 退出
        quit_action = QAction("退出", self)
        if with_quit_confirm:
            quit_action.triggered.connect(self._on_quit)
        else:
            quit_action.triggered.connect(self.action_quit.emit)
            quit_action.triggered.connect(QApplication.quit)
        menu.addAction(quit_action)

    def _current_chat_position_mode(self) -> str:
        """读取当前聊天位置模式配置。"""
        try:
            if self._config and getattr(self._config, "chat", None):
                mode = getattr(self._config.chat, "chat_position_mode", "independent")
                return mode if mode in ("independent", "follow") else "independent"
        except Exception:
            pass
        return "independent"

    def _create_icon(self) -> QIcon:
        """创建托盘图标。

        SVG 走矢量渲染，位图走 QPixmap 兜底；失败则返回空图标。

        Returns:
            QIcon 实例。
        """
        icon_path = self._resolve_icon_path()
        if icon_path:
            suffix = icon_path.lower().rsplit(".", 1)[-1] if "." in icon_path else ""
            if suffix == "svg":
                icon = QIcon()
                # 多档尺寸让 QIcon 按需选择，避免高 DPI 下模糊
                for px in (16, 24, 32, 48, 64):
                    pm = self._render_svg_to_pixmap(icon_path, px)
                    if pm and not pm.isNull():
                        icon.addPixmap(pm, QIcon.Mode.Normal, QIcon.State.Off)
                if not icon.isNull():
                    return icon
            else:
                pixmap = QPixmap(str(icon_path))
                if not pixmap.isNull():
                    return QIcon(pixmap)
        return QIcon()

    @staticmethod
    def _render_svg_to_pixmap(path: str, size_px: int) -> QPixmap | None:
        """将 SVG 文件渲染为指定物理像素的 QPixmap。

        Args:
            path: SVG 文件绝对路径。
            size_px: 目标像素尺寸（宽=高）。

        Returns:
            QPixmap 或 None（渲染失败时）。
        """
        try:
            renderer = QSvgRenderer(path)
            if not renderer.isValid():
                return None
            pm = QPixmap(size_px, size_px)
            pm.fill(Qt.GlobalColor.transparent)
            painter = QPainter(pm)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            renderer.render(painter)
            painter.end()
            return pm
        except Exception:
            return None

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