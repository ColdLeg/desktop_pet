"""打字机风格对话气泡控件。

提供带对话气泡样式的对话框，具有：
- 打字机效果（逐字显示）
- 可配置的字体大小、最大宽度和打字速度（硬编码默认值）
- 可配置超时后自动隐藏（从 config 读取 dialog_auto_hide_sec）
- 半透明圆角矩形背景
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QPropertyAnimation, QTimer, Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

if TYPE_CHECKING:
    from ..config import DesktopPetConfig


class DialogBox(QWidget):
    """打字机风格的对话气泡，超时后自动隐藏。

    逐字显示文本，然后在可配置的延迟后淡出。
    自动隐藏时间从 config.chat.dialog_auto_hide_sec 读取。
    """

    # 硬编码默认值（不从配置加载）
    FONT_SIZE = 14
    MAX_WIDTH = 250
    TYPING_SPEED_MS = 50
    AUTO_HIDE_SEC = 5.0

    def __init__(
        self,
        parent: QWidget | None = None,
        config: DesktopPetConfig | None = None,
    ) -> None:
        """初始化对话气泡。

        Args:
            parent: 父级控件（通常是 PetWindow）。
            config: 用于读取 dialog_auto_hide_sec 配置；为 None 时使用硬编码默认值。
        """
        super().__init__(parent)
        self._config = config

        # 从 config 读取自动隐藏时间，回退到硬编码默认值
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

        # --- 设置 ---
        self._build_ui()

    def _build_ui(self) -> None:
        """构建对话框控件。"""
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)

        self._label = QLabel(self)
        self._label.setWordWrap(True)
        self._label.setMaximumWidth(self.MAX_WIDTH)
        self._label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)

        font = QFont("Microsoft YaHei", self.FONT_SIZE)
        self._label.setFont(font)

        # 样式：白色文字，半透明深色背景
        self._label.setStyleSheet(
            "background-color: rgba(40, 40, 50, 200);"
            "color: white;"
            "padding: 10px;"
            "border-radius: 8px;"
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._label)

        self.adjustSize()
        self.hide()

    # ---- 公开 API ----

    def show_text(self, text: str) -> None:
        """以打字机效果显示文本。

        开始前重置所有正在进行的打字或隐藏计时器。

        Args:
            text: 要显示的文本。
        """
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

    def hide_with_fade(self, duration_ms: int = 500) -> None:
        """在指定毫秒数内淡出对话框。

        Args:
            duration_ms: 淡出持续时间（毫秒）。
        """
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
