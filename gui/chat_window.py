# -*- coding: utf-8 -*-
"""桌宠独立聊天窗口 — Material Design 3 风格。

配色与字体均从 config.theme 读取（见 gui/theme.py），支持运行时切换。
提供输入框和发送按钮，用户输入通过 signal 传递给适配器。
show_chat_messages 开启时同时显示消息气泡；关闭时收到 bot 消息临时展开。
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from PySide6.QtCore import (
    QPropertyAnimation,
    QEasingCurve,
    QPoint,
    QSize,
    Qt,
    Signal,
    QTimer,
    QUrl,
)
from PySide6.QtGui import QFont, QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

try:
    from PySide6.QtMultimedia import QSoundEffect
    _HAS_QSOUND = True
except ImportError:
    _HAS_QSOUND = False

from .theme import get_font_family, get_theme, ColorTokens

if TYPE_CHECKING:
    from ..config import DesktopPetConfig


class ChatWindow(QWidget):
    """独立聊天窗口 — MD3 风格。

    无框半透明窗口，圆角暗色容器，自定义标题栏可拖拽。
    show_chat_messages=True 时显示消息气泡，False 时仅输入栏（收到 bot 消息临时展开）。
    """

    message_sent = Signal(str)
    offset_changed = Signal(QPoint)
    visibility_changed = Signal(bool)

    WIN_TITLE = "MoFox 桌宠"
    WIN_WIDTH = 380
    WIN_HEIGHT = 88
    WIN_HEIGHT_FULL = 480

    def __init__(
        self,
        config: DesktopPetConfig | None = None,
        parent: QWidget | None = None,
    ) -> None:
        """初始化聊天窗口。"""
        super().__init__(parent)
        self._config = config
        self._drag_position: QPoint | None = None

        # 主题
        self._theme: ColorTokens = get_theme(config)
        self._font_ui = get_font_family(config, kind="ui")
        self._font_mono = get_font_family(config, kind="mono")

        self._show_messages: bool = bool(
            getattr(config.chat, "show_chat_messages", False)
        ) if config else False

        self._messages_built: bool = self._show_messages
        self._size_anim: QPropertyAnimation | None = None

        self.setWindowTitle(self.WIN_TITLE)
        self._scale = self._compute_scale(0.018)
        win_h_base = self.WIN_HEIGHT_FULL if self._show_messages else self.WIN_HEIGHT
        self._win_w = int(self.WIN_WIDTH * self._scale)
        self._win_h = int(win_h_base * self._scale)
        self.setMinimumSize(0, 0)
        self.resize(self._win_w, self._win_h)
        self.setWindowFlags(
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        self._build_ui()

        self._sound_effect = None
        if _HAS_QSOUND and self._config:
            sound_path = getattr(self._config.chat, "notification_sound", "")
            if sound_path and os.path.isfile(sound_path):
                self._sound_effect = QSoundEffect(self)
                self._sound_effect.setSource(QUrl.fromLocalFile(sound_path))
                self._sound_effect.setVolume(0.8)
                self._sound_effect.setLoopCount(1)

    # ---- 屏幕比例尺寸 ----

    BASE_PET_SIDE = 200  # 与 PetWindow 默认边长一致，用作缩放基准

    @classmethod
    def _compute_scale(cls, area_ratio: float) -> float:
        """按主屏可用面积比例计算缩放因子。"""
        from PySide6.QtWidgets import QApplication
        import math
        screen = QApplication.primaryScreen()
        if screen is None:
            return 1.0
        geo = screen.availableGeometry()
        if geo.width() <= 0 or geo.height() <= 0:
            return 1.0
        area = geo.width() * geo.height() * area_ratio
        if area <= 0:
            return 1.0
        side = math.sqrt(area)
        return max(0.5, side / cls.BASE_PET_SIDE)

    # ---- 主题 ----

    def _build_qss(self) -> str:
        """根据当前 theme token 生成 QSS（字号/padding 按缩放因子调整）。"""
        t = self._theme
        s = self._scale
        ff_ui = self._font_ui
        ff_mono = self._font_mono
        # 用 {f} 占位，便于整体替换字体族
        qss = f"""
        * {{
            font-family: {ff_ui};
        }}
        #chat_container {{
            background-color: rgba({_hex_to_rgb_tuple(t.surface)}, 0.96);
            border-radius: {int(16 * s)}px;
            border: 1px solid {t.surface_container_highest};
        }}
        #title_bar {{
            background-color: rgba({_hex_to_rgb_tuple(t.surface_container_low)}, 0.8);
            border-top-left-radius: {int(16 * s)}px;
            border-top-right-radius: {int(16 * s)}px;
            border-bottom: 1px solid {t.surface_container_highest};
        }}
        #title_label {{
            color: {t.on_surface};
            font-size: {max(9, int(14 * s))}px;
            font-weight: 600;
            background: transparent;
        }}
        #close_btn {{
            background: transparent;
            border: none;
            color: {t.outline};
            font-size: {max(10, int(16 * s))}px;
            border-radius: {int(6 * s)}px;
        }}
        #close_btn:hover {{
            background-color: {t.outline_variant};
            color: {t.error};
        }}
        #message_scroll {{
            background: transparent;
            border: none;
        }}
        #scroll_content {{
            background: transparent;
        }}
        #input_bar {{
            background-color: rgba({_hex_to_rgb_tuple(t.surface_container_low)}, 0.8);
            border-bottom-left-radius: {int(16 * s)}px;
            border-bottom-right-radius: {int(16 * s)}px;
            border-top: 1px solid {t.surface_container_highest};
        }}
        #input_field {{
            background-color: {t.surface_container};
            border: 1px solid {t.outline_variant};
            border-radius: {int(12 * s)}px;
            color: {t.on_surface};
            padding: {int(8 * s)}px {int(12 * s)}px;
            font-size: {max(9, int(14 * s))}px;
            font-family: {ff_mono};
        }}
        #input_field:focus {{
            border: 1px solid {t.primary};
        }}
        #send_btn {{
            background-color: {t.primary};
            color: {t.on_primary};
            border: none;
            border-radius: {int(18 * s)}px;
            font-size: {max(9, int(14 * s))}px;
            font-weight: 600;
        }}
        #send_btn:hover {{
            background-color: {t.on_primary_container};
        }}
        #send_btn:pressed {{
            background-color: {t.primary_container};
            color: {t.on_primary_container};
        }}
        QScrollBar:vertical {{
            background: transparent;
            width: 6px;
            margin: 0;
        }}
        QScrollBar::handle:vertical {{
            background: {t.outline_variant};
            border-radius: 3px;
            min-height: 30px;
        }}
        QScrollBar::handle:vertical:hover {{
            background: {t.outline};
        }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
            height: 0;
        }}
        QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
            background: transparent;
        }}
        """
        return qss

    def apply_theme(self, config: "DesktopPetConfig | None") -> None:
        """运行时切换主题：重新读取 token/字体并重绘 QSS。"""
        self._config = config
        self._theme = get_theme(config)
        self._font_ui = get_font_family(config, kind="ui")
        self._font_mono = get_font_family(config, kind="mono")
        # 重应用 QSS
        self.setStyleSheet(self._build_qss())
        # 标题字体
        if hasattr(self, "_title_label") and self._title_label:
            f = QFont()
            f.setFamilies(self._font_ui.split(","))
            f.setPointSize(max(9, int(14 * self._scale)))
            f.setBold(True)
            self._title_label.setFont(f)
        if hasattr(self, "_close_btn") and self._close_btn:
            f = QFont()
            f.setFamilies(self._font_ui.split(","))
            f.setPointSize(max(10, int(16 * self._scale)))
            self._close_btn.setFont(f)
        self.update()

    # ---- 构建 UI ----

    def _build_ui(self) -> None:
        """构建聊天界面布局。"""
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        container = QFrame()
        container.setObjectName("chat_container")
        container_layout = QVBoxLayout(container)
        container_layout.setContentsMargins(0, 0, 0, 0)
        container_layout.setSpacing(0)

        # --- 标题栏 ---
        self._title_bar = QFrame()
        self._title_bar.setObjectName("title_bar")
        self._title_bar.setFixedHeight(int(36 * self._scale))
        title_layout = QHBoxLayout(self._title_bar)
        title_layout.setContentsMargins(int(12 * self._scale), 0, int(8 * self._scale), 0)
        title_layout.setSpacing(int(8 * self._scale))

        title_text = (
            getattr(self._config.chat, "pet_name", "MoFox 桌宠")
            if self._config else "MoFox 桌宠"
        )
        self._title_label = QLabel(title_text)
        self._title_label.setObjectName("title_label")
        title_font = QFont()
        title_font.setFamilies(self._font_ui.split(","))
        title_font.setPointSize(max(9, int(14 * self._scale)))
        title_font.setBold(True)
        self._title_label.setFont(title_font)
        title_layout.addWidget(self._title_label)
        title_layout.addStretch()

        self._close_btn = QPushButton("✕")
        self._close_btn.setObjectName("close_btn")
        self._close_btn.setFixedSize(int(28 * self._scale), int(28 * self._scale))
        self._close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_font = QFont()
        close_font.setFamilies(self._font_ui.split(","))
        close_font.setPointSize(max(10, int(16 * self._scale)))
        self._close_btn.setFont(close_font)
        self._close_btn.clicked.connect(self.hide)
        title_layout.addWidget(self._close_btn)

        container_layout.addWidget(self._title_bar)

        self._container_layout = container_layout

        # --- 消息滚动区（仅在开启消息显示时构建）---
        if self._show_messages:
            self._build_message_scroll(container_layout)

        # --- 输入区 ---
        input_bar = QFrame()
        input_bar.setObjectName("input_bar")
        input_bar.setFixedHeight(int(52 * self._scale))
        input_layout = QHBoxLayout(input_bar)
        m2 = int(10 * self._scale)
        input_layout.setContentsMargins(m2, 0, m2, 0)
        input_layout.setSpacing(int(8 * self._scale))

        self._input = QLineEdit()
        self._input.setObjectName("input_field")
        self._input.setPlaceholderText("输入消息...")
        input_font = QFont()
        input_font.setFamilies(self._font_mono.split(","))
        input_font.setPointSize(max(9, int(14 * self._scale)))
        self._input.setFont(input_font)
        input_layout.addWidget(self._input, stretch=1)

        self._send_button = QPushButton("发送")
        self._send_button.setObjectName("send_btn")
        self._send_button.setFixedSize(int(64 * self._scale), int(36 * self._scale))
        self._send_button.setCursor(Qt.CursorShape.PointingHandCursor)
        input_layout.addWidget(self._send_button)

        container_layout.addWidget(input_bar)

        main_layout.addWidget(container)

        self._input.returnPressed.connect(self._on_send)
        self._send_button.clicked.connect(self._on_send)

        self.setStyleSheet(self._build_qss())

    def _build_message_scroll(self, container_layout: QVBoxLayout) -> None:
        """构建消息滚动区并添加到 container_layout。"""
        self._message_scroll = QScrollArea()
        self._message_scroll.setObjectName("message_scroll")
        self._message_scroll.setWidgetResizable(True)
        self._message_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self._message_scroll.setFrameShape(QFrame.Shape.NoFrame)

        scroll_content = QWidget()
        scroll_content.setObjectName("scroll_content")
        self._message_layout = QVBoxLayout(scroll_content)
        m = int(10 * self._scale)
        self._message_layout.setContentsMargins(m, m, m, m)
        self._message_layout.setSpacing(int(8 * self._scale))
        self._message_layout.addStretch()

        self._message_scroll.setWidget(scroll_content)
        container_layout.insertWidget(1, self._message_scroll, stretch=1)

    def _ensure_messages_built(self) -> None:
        """_show_messages=False 时收到 bot 消息触发延迟构建消息区，
        并以动画过渡到完整高度。"""
        if self._messages_built:
            return
        self._messages_built = True
        self._build_message_scroll(self._container_layout)
        target_h = int(self.WIN_HEIGHT_FULL * self._scale)
        self._size_anim = QPropertyAnimation(self, b"size")
        self._size_anim.setDuration(220)
        self._size_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._size_anim.setStartValue(QSize(self._win_w, self._win_h))
        self._size_anim.setEndValue(QSize(self._win_w, target_h))
        self._size_anim.start()
        self._win_h = target_h

    def _on_send(self) -> None:
        """处理发送动作：发射消息信号并清空输入框。"""
        text = self._input.text()
        if text:
            self.message_sent.emit(text)
            self._input.clear()

    def append_message(
        self,
        role: str,
        text: str,
        reply_to: str = "",
        emoji_bytes: bytes = b"",
    ) -> None:
        """向消息历史追加一条消息气泡。

        当 show_chat_messages=False 时：
        - system 消息忽略（无显示区）
        - 任意非 system 消息触发延迟构建消息区，并以动画过渡到完整高度
        当 show_chat_messages=True 时正常追加气泡。
        """
        if not self._messages_built:
            if role == "system":
                return
            self._ensure_messages_built()

        if role == "user":
            label = (
                getattr(self._config.chat, "user_name", "用户")
                if self._config else "用户"
            )
        elif role == "system":
            label = ""
        else:
            label = (
                getattr(self._config.chat, "pet_name", "桌宠")
                if self._config else "桌宠"
            )

        bubble = self._create_bubble(role, label, text, reply_to=reply_to, emoji_bytes=emoji_bytes)

        if role not in ("user", "system") and self._sound_effect:
            self._sound_effect.play()

        if role == "user":
            self._message_layout.insertWidget(
                self._message_layout.count() - 1, bubble, alignment=Qt.AlignmentFlag.AlignRight
            )
        elif role == "system":
            self._message_layout.insertWidget(
                self._message_layout.count() - 1, bubble, alignment=Qt.AlignmentFlag.AlignCenter
            )
        else:
            self._message_layout.insertWidget(
                self._message_layout.count() - 1, bubble, alignment=Qt.AlignmentFlag.AlignLeft
            )

        def _deferred_scroll() -> None:
            self._message_layout.invalidate()
            self._message_layout.activate()
            self._scroll_to_bottom()

        QTimer.singleShot(0, _deferred_scroll)

    def _create_bubble(
        self,
        role: str,
        label: str,
        text: str,
        reply_to: str = "",
        emoji_bytes: bytes = b"",
    ) -> QFrame:
        """创建单条消息气泡 widget（MD3 风格，主题配色 + 等宽/Ubuntu 字体）。"""
        t = self._theme
        s = self._scale
        bubble = QFrame()
        layout = QVBoxLayout(bubble)
        layout.setContentsMargins(10, 6, 10, 6)
        layout.setSpacing(2)

        # 回复标记（仅 bot 气泡且带 reply_to 时显示）
        if reply_to and role not in ("user", "system"):
            reply_label = QLabel(f"↩ 回复消息 {reply_to[:8]}")
            reply_label.setStyleSheet(
                f"color: {t.accent}; font-size: {max(8, int(10 * s))}px;"
                f" background: transparent; font-family: {self._font_ui};"
            )
            layout.addWidget(reply_label)

        if role == "system":
            msg = QLabel(text)
            msg.setWordWrap(True)
            msg.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.MinimumExpanding)
            msg.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            msg.setTextFormat(Qt.TextFormat.PlainText)
            sys_font = QFont()
            sys_font.setFamilies(self._font_ui.split(","))
            sys_font.setPointSize(max(8, int(12 * s)))
            msg.setFont(sys_font)
            msg.setStyleSheet(
                f"color: {t.bubble_system_fg}; background: transparent;"
                f" font-style: italic;"
            )
            layout.addWidget(msg)
            bubble.setStyleSheet(
                f"background-color: {t.bubble_system_bg};"
                f" border-radius: {int(8 * s)}px;"
            )
            bubble.setFixedWidth(int(340 * s))
        else:
            sender = QLabel(label)
            sender_font = QFont()
            sender_font.setFamilies(self._font_ui.split(","))
            sender_font.setPointSize(max(8, int(11 * s)))
            sender_font.setBold(True)
            sender.setFont(sender_font)
            sender.setStyleSheet(
                "background: transparent;"
            )
            layout.addWidget(sender)

            # 文本（若有）
            if text:
                msg = QLabel(text)
                msg.setWordWrap(True)
                msg.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.MinimumExpanding)
                msg.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                msg.setTextFormat(Qt.TextFormat.PlainText)
                txt_font = QFont()
                txt_font.setFamilies(self._font_mono.split(","))
                txt_font.setPointSize(max(9, int(14 * s)))
                msg.setFont(txt_font)
                msg.setStyleSheet("background: transparent;")
                layout.addWidget(msg)

            # emoji 图片（若有）
            if emoji_bytes:
                emoji_label = QLabel()
                emoji_label.setStyleSheet("background: transparent;")
                pm = QPixmap()
                pm.loadFromData(emoji_bytes)
                if not pm.isNull():
                    max_side = int(120 * s)
                    scaled = pm.scaled(
                        max_side, max_side,
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                    emoji_label.setPixmap(scaled)
                    layout.addWidget(emoji_label)

            if role == "user":
                bubble.setStyleSheet(
                    f"background-color: {t.bubble_user_bg};"
                    f" border-radius: {int(12 * s)}px;"
                )
                sender.setStyleSheet(
                    f"color: {t.primary}; background: transparent;"
                )
                if text:
                    msg.setStyleSheet(
                        f"color: {t.bubble_user_fg}; background: transparent;"
                    )
            else:
                bubble.setStyleSheet(
                    f"background-color: {t.bubble_bot_bg};"
                    f" border-radius: {int(12 * s)}px;"
                )
                sender.setStyleSheet(
                    f"color: {t.on_surface_variant}; background: transparent;"
                )
                if text:
                    msg.setStyleSheet(
                        f"color: {t.bubble_bot_fg}; background: transparent;"
                    )
            bubble.setFixedWidth(int(300 * s))

        return bubble

    def _scroll_to_bottom(self) -> None:
        """滚动消息区到底部。"""
        scrollbar = self._message_scroll.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    # ---- 窗口拖拽 ----

    def mousePressEvent(self, event) -> None:
        """点击标题栏区域时开始拖拽窗口。"""
        if event.button() == Qt.MouseButton.LeftButton:
            child = self.childAt(event.position().toPoint())
            if child and self._is_in_title_bar(child):
                self._drag_position = (
                    event.globalPosition().toPoint()
                    - self.frameGeometry().topLeft()
                )
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        """拖拽窗口移动。"""
        if (
            event.buttons() == Qt.MouseButton.LeftButton
            and self._drag_position is not None
        ):
            self.move(event.globalPosition().toPoint() - self._drag_position)
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        """释放鼠标时清除拖拽状态并 emit 偏移变化。"""
        if self._drag_position is not None:
            self.offset_changed.emit(self.pos())
            self._drag_position = None
        super().mouseReleaseEvent(event)

    def showEvent(self, event) -> None:
        """窗口显示时通知可见性变化。"""
        super().showEvent(event)
        self.visibility_changed.emit(True)

    def hideEvent(self, event) -> None:
        """窗口隐藏时通知可见性变化。"""
        super().hideEvent(event)
        self.visibility_changed.emit(False)

    def load_history(self, messages: list) -> None:
        """批量加载历史消息并渲染。

        修复：show_chat_messages=False 时也触发延迟构建消息区，
        使历史可见（原实现直接 return 导致历史丢失）。

        Args:
            messages: 历史消息列表，每项为 dict {"role": ..., "text": ...}。
        """
        if not messages:
            return
        # 即使 _show_messages=False，只要有历史也展开消息区
        if not self._messages_built:
            self._ensure_messages_built()
        # 清空已有消息气泡（保留末尾的 stretch）
        while self._message_layout.count() > 1:
            item = self._message_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        for msg in messages:
            role = msg.get("role", "bot")
            text = msg.get("text", "")
            reply_to = msg.get("reply_to", "")
            if not text:
                continue
            emoji_bytes = msg.get("emoji_bytes", b"") or b""
            if role == "user":
                label = (
                    getattr(self._config.chat, "user_name", "用户")
                    if self._config else "用户"
                )
            elif role == "system":
                label = ""
            else:
                label = (
                    getattr(self._config.chat, "pet_name", "桌宠")
                    if self._config else "桌宠"
                )
            bubble = self._create_bubble(role, label, text, reply_to=reply_to, emoji_bytes=emoji_bytes)
            if role == "user":
                self._message_layout.insertWidget(self._message_layout.count() - 1, bubble, alignment=Qt.AlignmentFlag.AlignRight)
            elif role == "system":
                self._message_layout.insertWidget(self._message_layout.count() - 1, bubble, alignment=Qt.AlignmentFlag.AlignCenter)
            else:
                self._message_layout.insertWidget(self._message_layout.count() - 1, bubble, alignment=Qt.AlignmentFlag.AlignLeft)
        QTimer.singleShot(0, self._scroll_to_bottom)

    def _is_in_title_bar(self, widget: QWidget) -> bool:
        """检查 widget 是否属于标题栏。"""
        current = widget
        while current is not None:
            if current is self._title_bar:
                return True
            current = current.parentWidget()
        return False


# 模块级辅助：把 #RRGGBB 转 "r, g, b" 用于 QSS rgba()
def _hex_to_rgb_tuple(hex_color: str) -> str:
    s = hex_color.strip().lstrip("#")
    if len(s) == 3:
        s = "".join(c * 2 for c in s)
    if len(s) != 6:
        return "0, 0, 0"
    try:
        r = int(s[0:2], 16)
        g = int(s[2:4], 16)
        b = int(s[4:6], 16)
        return f"{r}, {g}, {b}"
    except Exception:
        return "0, 0, 0"
