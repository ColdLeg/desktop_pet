# -*- coding: utf-8 -*-
"""打字机风格对话气泡控件（MD3 风格）。

提供 MD3 风格的对话气泡，具有：
- 打字机效果（逐字显示）
- 字体大小、最大宽度从 config.theme 读取，回退硬编码默认
- 可配置超时后自动隐藏（config.chat.dialog_auto_hide_sec）
- 圆角矩形 + 主题配色（与 ChatWindow 同源 theme token）
- 等宽 + Ubuntu 中文字体
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QPropertyAnimation, QTimer, Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from .theme import get_font_family, get_font_size_scale, get_theme

if TYPE_CHECKING:
    from ..config import DesktopPetConfig


class DialogBox(QWidget):
    """打字机风格的 MD3 对话气泡，超时后自动淡出隐藏。

    配色从 config.theme 读取（与 ChatWindow 共享 token）；
    字体采用等宽编程字体（JetBrains Mono）+ Ubuntu 中文字体组合。
    """

    # 硬编码默认值（config 无 theme 时回退）
    # 统一用 px 单位（与 chat_window 一致），12px 紧凑清晰
    FONT_SIZE = 12
    MAX_WIDTH = 280
    TYPING_SPEED_MS = 45
    AUTO_HIDE_SEC = 10.0

    def __init__(
        self,
        parent: QWidget | None = None,
        config: DesktopPetConfig | None = None,
    ) -> None:
        """初始化对话气泡。

        Args:
            parent: 父级控件（通常是 PetWindow）。
            config: 用于读取 theme（配色/字体）与 dialog_auto_hide_sec。
        """
        super().__init__(parent)
        self._config = config

        # 主题
        self._theme = get_theme(config)
        self._font_family = get_font_family(config, kind="bubble")
        # 字号缩放（用户可配置，热切换）
        self._font_scale = get_font_size_scale(config)
        self._font_size_px = max(8, int(self.FONT_SIZE * self._font_scale))

        # 自动隐藏时间
        if config:
            self._auto_hide_sec: float = float(
                getattr(config.chat, "dialog_auto_hide_sec", self.AUTO_HIDE_SEC)
            )
        else:
            self._auto_hide_sec = self.AUTO_HIDE_SEC

        # --- 状态 ---
        self._full_text: str = ""
        self._current_index: int = 0
        self._typing_timer: QTimer | None = None
        self._hide_timer: QTimer | None = None
        self._fade_animation: QPropertyAnimation | None = None
        self._accelerated: bool = False

        self._build_ui()

    def _build_ui(self) -> None:
        """构建对话框控件。"""
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)

        self._label = QLabel(self)
        self._label.setWordWrap(True)
        self._label.setMaximumWidth(self.MAX_WIDTH)
        self._label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)

        font = QFont()
        font.setFamilies(self._font_family.split(","))
        font.setPixelSize(self._font_size_px)
        self._label.setFont(font)

        t = self._theme
        # MD3 风格：圆角 16px，surface 容器色，文字 on_surface
        self._label.setStyleSheet(
            f"background-color: {t.dialog_bg};"
            f"color: {t.dialog_fg};"
            "padding: 10px 14px;"
            "border-radius: 16px;"
            f"border: 1px solid {t.outline_variant};"
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.addWidget(self._label)

        self.adjustSize()
        self.hide()

    # ---- 主题刷新 ----

    def apply_theme(self, config: "DesktopPetConfig | None") -> None:
        """运行时切换主题时重新应用配色与字体。"""
        self._config = config
        self._theme = get_theme(config)
        self._font_family = get_font_family(config, kind="bubble")
        self._font_scale = get_font_size_scale(config)
        self._font_size_px = max(8, int(self.FONT_SIZE * self._font_scale))
        t = self._theme
        font = QFont()
        font.setFamilies(self._font_family.split(","))
        font.setPixelSize(self._font_size_px)
        self._label.setFont(font)
        self._label.setStyleSheet(
            f"background-color: {t.dialog_bg};"
            f"color: {t.dialog_fg};"
            "padding: 10px 14px;"
            "border-radius: 16px;"
            f"border: 1px solid {t.outline_variant};"
        )
        self.update()

    # ---- 公开 API ----

    def show_text(self, text: str) -> None:
        """以打字机效果显示文本。"""
        self._stop_all_timers()

        self._full_text = text
        self._current_index = 0
        self._label.setText("")

        self.show()
        self.raise_()

        if self.TYPING_SPEED_MS > 0 and len(text) > 0:
            self._typing_timer = QTimer(self)
            self._typing_timer.timeout.connect(self._type_next_char)
            self._typing_timer.start(self.TYPING_SPEED_MS)
        else:
            self._label.setText(text)
            self.adjustSize()

    def hide_with_fade(self, duration_ms: int = 400) -> None:
        """在指定毫秒数内淡出对话框。"""
        if self._fade_animation and self._fade_animation.state() == QPropertyAnimation.State.Running:
            self._fade_animation.stop()

        self._fade_animation = QPropertyAnimation(self, b"windowOpacity")
        self._fade_animation.setDuration(duration_ms)
        self._fade_animation.setStartValue(1.0)
        self._fade_animation.setEndValue(0.0)
        self._fade_animation.finished.connect(self._on_fade_finished)
        self._fade_animation.start()

    def skip_typing(self) -> None:
        """立即显示完整文本，跳过打字机延迟。"""
        if self._typing_timer and self._typing_timer.isActive():
            self._typing_timer.stop()
        self._label.setText(self._full_text)
        self._current_index = len(self._full_text)
        self.adjustSize()

    def hide_immediately(self) -> None:
        """无动画立即隐藏对话框。"""
        self._stop_all_timers()
        self.setWindowOpacity(1.0)
        self.hide()

    @property
    def current_text(self) -> str:
        """返回当前完整文本（用于跨窗口 copy）。"""
        return self._full_text

    def is_outputting(self) -> bool:
        """是否正在打字机输出或等待隐藏。"""
        return (
            (self._typing_timer is not None and self._typing_timer.isActive())
            or (self._hide_timer is not None and self._hide_timer.isActive())
            or self.isVisible()
        )

    def accelerate_hide(self) -> None:
        """加速隐藏：把剩余 auto_hide 时间减半。

        用于 chat_window 打开时，让 pet 气泡更快消失。
        """
        if not self.isVisible():
            return
        self._accelerated = True
        if self._typing_timer and self._typing_timer.isActive():
            self._typing_timer.stop()
            self._label.setText(self._full_text)
            self._current_index = len(self._full_text)
            self.adjustSize()
        if self._auto_hide_sec > 0:
            if self._hide_timer and self._hide_timer.isActive():
                self._hide_timer.stop()
            short = max(0.5, self._auto_hide_sec * 0.5)
            self._hide_timer = QTimer(self)
            self._hide_timer.setSingleShot(True)
            self._hide_timer.timeout.connect(lambda: self.hide_with_fade())
            self._hide_timer.start(int(short * 1000))

    # ---- 内部辅助方法 ----

    def _type_next_char(self) -> None:
        """显示下一个字符（由打字计时器调用）。"""
        if self._current_index >= len(self._full_text):
            if self._typing_timer:
                self._typing_timer.stop()
            self._start_hide_timer()
            return

        self._current_index += 1
        self._label.setText(self._full_text[: self._current_index])
        self.adjustSize()

    def _start_hide_timer(self) -> None:
        """打字完成后启动自动隐藏倒计时。"""
        if self._auto_hide_sec > 0:
            self._hide_timer = QTimer(self)
            self._hide_timer.setSingleShot(True)
            self._hide_timer.timeout.connect(lambda: self.hide_with_fade())
            self._hide_timer.start(int(self._auto_hide_sec * 1000))

    def _on_fade_finished(self) -> None:
        """淡出动画完成时调用。"""
        self.hide()
        self.setWindowOpacity(1.0)

    def _stop_all_timers(self) -> None:
        """停止所有正在运行的计时器和动画。"""
        if self._typing_timer and self._typing_timer.isActive():
            self._typing_timer.stop()
        self._typing_timer = None

        if self._hide_timer and self._hide_timer.isActive():
            self._hide_timer.stop()
        self._hide_timer = None

        if self._fade_animation and self._fade_animation.state() == QPropertyAnimation.State.Running:
            self._fade_animation.stop()
        self._fade_animation = None

        self.setWindowOpacity(1.0)

    def mousePressEvent(self, event) -> None:
        """点击对话框可跳过打字或关闭对话框。"""
        if self._current_index < len(self._full_text):
            self.skip_typing()
        else:
            self.hide_with_fade()
        super().mousePressEvent(event)
