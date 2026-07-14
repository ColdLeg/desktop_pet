# -*- coding: utf-8 -*-
"""桌面宠物的系统托盘管理器（MD3 风格 + 内置 SVG 图标）。

提供 QSystemTrayIcon 及右键上下文菜单，包含：
- 显示/隐藏 切换
- 配色方案切换（运行时切换主题预设）
- 聊天位置模式切换
- 透明度
- 退出

图标优先用配置的 default_image（SVG），否则用内置 TRAY_ICON_SVG，彻底消除 PNG 依赖。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, Signal, Qt
from PySide6.QtGui import QAction, QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon, QMessageBox

from .svg_assets import TRAY_ICON_SVG
from .theme import PRESET_NAMES

if TYPE_CHECKING:
    from ..config import DesktopPetConfig


class TrayManager(QObject):
    """系统托盘图标和上下文菜单管理器。

    信号：
        action_show / action_hide: 显示/隐藏宠物窗口
        action_chat / action_chat_hide: 聊天窗口开关
        action_toggle_theme: 切换配色主题（参数：preset 名）
        action_quit: 退出
        action_set_opacity(float): 透明度
        action_set_chat_position_mode(str): 聊天位置模式
    """

    action_show = Signal()
    action_hide = Signal()
    action_chat = Signal()
    action_chat_hide = Signal()
    action_toggle_theme = Signal(str)
    action_set_font_scale = Signal(float)
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
        super().__init__(parent)
        self._config = config

        self._tray_icon: QSystemTrayIcon | None = None

        if self.SHOW_ICON:
            self._build_tray()

    # ---- 设置 ----

    def _build_tray(self) -> None:
        """构建系统托盘图标和上下文菜单。"""
        icon = self._create_icon()
        self._tray_icon = QSystemTrayIcon(icon, self)
        self._tray_icon.setToolTip(self.TOOLTIP)

        # 菜单在每次即将显示时重建，确保切换配置后勾选状态实时刷新
        # （透明度/字号/配色/位置模式等 setCheckable 项的 checked 状态）
        menu = QMenu()
        self._tray_icon.setContextMenu(menu)
        menu.aboutToShow.connect(lambda: self._rebuild_context_menu(menu))

        self._tray_icon.activated.connect(self._on_tray_activated)

    def _rebuild_context_menu(self, menu: QMenu) -> None:
        """aboutToShow 时清空并重建菜单，使勾选状态反映最新配置。"""
        menu.clear()
        self.build_menu(menu, with_quit_confirm=True)

    def build_menu(self, menu: QMenu, *, with_quit_confirm: bool = True) -> None:
        """把共用菜单项填入指定 menu。

        托盘菜单和桌宠右键菜单都复用此方法。子菜单：透明度、Pet/Chat、配色。
        """
        # 聊天
        chat_action = QAction("聊天...", self)
        chat_action.triggered.connect(self.action_chat.emit)
        menu.addAction(chat_action)

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

        # 配色子菜单
        theme_menu = menu.addMenu("配色方案")
        current_preset = self._current_theme_preset()
        for name in PRESET_NAMES:
            label = self._theme_label(name)
            act = QAction(label, theme_menu)
            act.setCheckable(True)
            act.setChecked(name == current_preset)
            act.triggered.connect(lambda checked=False, n=name: self.action_toggle_theme.emit(n))
            theme_menu.addAction(act)
        # 自定义（打开说明）
        custom_act = QAction("自定义配色（编辑配置）", theme_menu)
        custom_act.setCheckable(True)
        custom_act.setChecked(current_preset == "custom")
        custom_act.triggered.connect(lambda checked=False: self.action_toggle_theme.emit("custom"))
        theme_menu.addAction(custom_act)

        # 字号子菜单（热切换，用户自定义大小）
        font_menu = menu.addMenu("字号大小")
        current_scale = self._current_font_scale()
        for pct, label in [(80, "小 (80%)"), (90, "较小 (90%)"), (100, "默认 (100%)"), (110, "较大 (110%)"), (125, "大 (125%)"), (150, "特大 (150%)")]:
            act = QAction(label, font_menu)
            act.setCheckable(True)
            act.setChecked(abs(current_scale - pct / 100.0) < 0.01)
            act.triggered.connect(lambda checked=False, p=pct: self.action_set_font_scale.emit(p / 100.0))
            font_menu.addAction(act)

        menu.addSeparator()

        # 退出
        quit_action = QAction("退出", self)
        if with_quit_confirm:
            quit_action.triggered.connect(self._on_quit)
        else:
            quit_action.triggered.connect(self.action_quit.emit)
            quit_action.triggered.connect(QApplication.quit)
        menu.addAction(quit_action)

    def _theme_label(self, name: str) -> str:
        """配色预设的中文名。"""
        return {
            "mofox_blue": "MoFox 淡蓝（默认）",
            "mofox_blue_light": "MoFox 淡蓝-浅色",
            "ocean": "海洋深蓝",
            "forest": "森林绿",
            "sunset": "日落橙",
            "aurora": "星空极光",
            "cyber_neon": "赛博霓虹",
            "amethyst": "紫晶幻境",
            "amber": "琥珀暮光",
            "emerald": "翡翠琉璃",
            "rose_dawn": "玫瑰晨曦",
        }.get(name, name)

    def _current_theme_preset(self) -> str:
        """读取当前配色预设。"""
        try:
            if self._config and getattr(self._config, "theme", None):
                return getattr(self._config.theme, "preset", "mofox_blue") or "mofox_blue"
        except Exception:
            pass
        return "mofox_blue"

    def _current_font_scale(self) -> float:
        """读取当前字号缩放因子。"""
        try:
            if self._config and getattr(self._config, "theme", None):
                return float(getattr(self._config.theme, "font_size_scale", 1.0))
        except Exception:
            pass
        return 1.0

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

        优先级：配置 default_image(SVG) → 内置 TRAY_ICON_SVG → 位图兜底 → 空图标。
        全程无 PNG 依赖。
        """
        # 1) 配置的 SVG 文件
        icon_path = self._resolve_icon_path()
        if icon_path:
            suffix = icon_path.lower().rsplit(".", 1)[-1] if "." in icon_path else ""
            if suffix == "svg":
                icon = QIcon()
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

        # 2) 内置 SVG（无文件依赖）
        icon = QIcon()
        for px in (16, 24, 32, 48, 64):
            pm = self._render_svg_bytes_to_pixmap(TRAY_ICON_SVG, px)
            if pm and not pm.isNull():
                icon.addPixmap(pm, QIcon.Mode.Normal, QIcon.State.Off)
        if not icon.isNull():
            return icon

        return QIcon()

    @staticmethod
    def _render_svg_to_pixmap(path: str, size_px: int) -> QPixmap | None:
        """将 SVG 文件渲染为指定物理像素的 QPixmap。"""
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

    @staticmethod
    def _render_svg_bytes_to_pixmap(svg_bytes: bytes, size_px: int) -> QPixmap | None:
        """将 SVG 字节流渲染为指定物理像素的 QPixmap。"""
        try:
            renderer = QSvgRenderer(bytes(svg_bytes))
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
        """从配置的 default_image 解析托盘图标路径。"""
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
        """显示托盘图标。"""
        if self._tray_icon:
            self._tray_icon.show()

    def hide(self) -> None:
        """隐藏托盘图标。"""
        if self._tray_icon:
            self._tray_icon.hide()

    def set_tooltip(self, text: str) -> None:
        """更新工具提示文本。"""
        if self._tray_icon:
            self._tray_icon.setToolTip(text)

    def show_notification(self, title: str, message: str) -> None:
        """显示系统托盘气泡通知。"""
        if self._tray_icon and self._tray_icon.supportsMessages():
            self._tray_icon.showMessage(
                title,
                message,
                QSystemTrayIcon.MessageIcon.Information,
                5000,
            )

    # ---- 内部方法 ----

    def _on_tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        """处理托盘图标激活事件。"""
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
