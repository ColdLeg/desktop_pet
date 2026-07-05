# -*- coding: utf-8 -*-
"""桌宠独立聊天窗口 — Material Design 3 暗色毛玻璃风格。

提供输入框和发送按钮，用户输入通过 signal 传递给适配器。
当 show_chat_messages 开启时，同时显示消息气泡。
采用 MD3 暗色色板与圆角设计，无框半透明窗口，标题栏可拖拽。
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

if TYPE_CHECKING:
    from ..config import DesktopPetConfig


class ChatWindow(QWidget):
    """独立聊天窗口 — MD3 暗色毛玻璃风格。

    无框半透明窗口，圆角暗色容器，自定义标题栏可拖拽。
    show_chat_messages=True 时显示消息气泡，False 时仅输入栏。
    桌宠回复同时通过 DialogBox 弹出气泡显示。

    信号：
        message_sent: 用户发送消息时触发，携带消息文本。
    """

    message_sent = Signal(str)
    offset_changed = Signal(QPoint)
    visibility_changed = Signal(bool)

    WIN_TITLE = "MoFox 桌宠"
    WIN_WIDTH = 380
    WIN_HEIGHT = 88
    WIN_HEIGHT_FULL = 480

    # ---- MD3 暗色色板 ----
    C_SURFACE = "#111318"
    C_ON_SURFACE = "#e2e2e9"
    C_SURFACE_CONTAINER = "#1e2024"
    C_SURFACE_CONTAINER_HIGH = "#282a2f"
    C_SURFACE_CONTAINER_HIGHEST = "#33343a"
    C_SURFACE_CONTAINER_LOW = "#1a1b20"
    C_PRIMARY = "#aec6ff"
    C_ON_PRIMARY = "#002e68"
    C_PRIMARY_CONTAINER = "#004494"
    C_ON_PRIMARY_CONTAINER = "#d9e2ff"
    C_ON_SURFACE_VARIANT = "#c4c7cf"
    C_OUTLINE_VARIANT = "#44474e"
    C_OUTLINE = "#8e9099"
    C_ERROR = "#ffb4ab"

    QSS = f"""
        #chat_container {{
            background-color: rgba(17, 19, 24, 0.96);
            border-radius: 16px;
            border: 1px solid {C_SURFACE_CONTAINER_HIGHEST};
        }}
        #title_bar {{
            background-color: rgba(26, 27, 32, 0.8);
            border-top-left-radius: 16px;
            border-top-right-radius: 16px;
            border-bottom: 1px solid {C_SURFACE_CONTAINER_HIGHEST};
        }}
        #title_label {{
            color: {C_ON_SURFACE};
            font-size: 14px;
            font-weight: 600;
            background: transparent;
        }}
        #close_btn {{
            background: transparent;
            border: none;
            color: {C_OUTLINE};
            font-size: 16px;
            border-radius: 6px;
        }}
        #close_btn:hover {{
            background-color: {C_OUTLINE_VARIANT};
            color: {C_ERROR};
        }}
        #message_scroll {{
            background: transparent;
            border: none;
        }}
        #scroll_content {{
            background: transparent;
        }}
        #input_bar {{
            background-color: rgba(26, 27, 32, 0.8);
            border-bottom-left-radius: 16px;
            border-bottom-right-radius: 16px;
            border-top: 1px solid {C_SURFACE_CONTAINER_HIGHEST};
        }}
        #input_field {{
            background-color: {C_SURFACE_CONTAINER};
            border: 1px solid {C_OUTLINE_VARIANT};
            border-radius: 12px;
            color: {C_ON_SURFACE};
            padding: 8px 12px;
            font-size: 14px;
        }}
        #input_field:focus {{
            border: 1px solid {C_PRIMARY};
        }}
        #send_btn {{
            background-color: {C_PRIMARY};
            color: {C_ON_PRIMARY};
            border: none;
            border-radius: 18px;
            font-size: 14px;
            font-weight: 600;
        }}
        #send_btn:hover {{
            background-color: {C_ON_PRIMARY_CONTAINER};
        }}
        #send_btn:pressed {{
            background-color: {C_PRIMARY_CONTAINER};
            color: {C_ON_PRIMARY_CONTAINER};
        }}
        QScrollBar:vertical {{
            background: transparent;
            width: 6px;
            margin: 0;
        }}
        QScrollBar::handle:vertical {{
            background: {C_OUTLINE_VARIANT};
            border-radius: 3px;
            min-height: 30px;
        }}
        QScrollBar::handle:vertical:hover {{
            background: {C_OUTLINE};
        }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
            height: 0;
        }}
        QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
            background: transparent;
        }}
    """

    def __init__(
        self,
        config: DesktopPetConfig | None = None,
        parent: QWidget | None = None,
    ) -> None:
        """初始化聊天窗口。

        Args:
            config: 插件配置，用于读取桌宠名称和用户名称。
            parent: 父级控件。
        """
        super().__init__(parent)
        self._config = config
        self._drag_position: QPoint | None = None

        # 是否显示消息气泡显示区
        self._show_messages: bool = bool(
            getattr(config.chat, "show_chat_messages", False)
        ) if config else False

        # 消息区是否已延迟构建（_show_messages=False 时初始不构建，
        # 收到第一条 bot 消息时临时构建并切换窗口尺寸）
        self._messages_built: bool = self._show_messages
        self._size_anim: QPropertyAnimation | None = None

        self.setWindowTitle(self.WIN_TITLE)
        # 按主屏面积 1.8% 计算缩放因子（与 PetWindow 同算法，基准 200x200）
        # PetWindow 用 1%，ChatWindow 略大一些
        self._scale = self._compute_scale(0.018)
        win_h_base = self.WIN_HEIGHT_FULL if self._show_messages else self.WIN_HEIGHT
        self._win_w = int(self.WIN_WIDTH * self._scale)
        self._win_h = int(win_h_base * self._scale)
        # 初始用固定尺寸；延迟构建消息区时会用动画过渡到完整高度
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
            sound_path = getattr(
                self._config.chat, "notification_sound", ""
            )
            if sound_path and os.path.isfile(sound_path):
                self._sound_effect = QSoundEffect(self)
                self._sound_effect.setSource(
                    QUrl.fromLocalFile(sound_path)
                )
                self._sound_effect.setVolume(0.8)
                self._sound_effect.setLoopCount(1)

    # ---- 屏幕比例尺寸 ----

    BASE_PET_SIDE = 200  # 与 PetWindow 默认边长一致，用作缩放基准

    @classmethod
    def _compute_scale(cls, area_ratio: float) -> float:
        """按主屏可用面积比例计算缩放因子。

        factor = sqrt(screen_w * screen_h * ratio) / BASE_PET_SIDE

        Args:
            area_ratio: 占屏幕面积的比例，与 PetWindow 保持一致。

        Returns:
            缩放因子（无屏幕信息时返回 1.0）。
        """
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
        # 限制最小缩放因子，避免子控件被压缩到不可见
        return max(0.5, side / cls.BASE_PET_SIDE)

    def _build_ui(self) -> None:
        """构建聊天界面布局。"""
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # --- 暗色圆角容器 ---
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
        from PySide6.QtGui import QFont
        title_font = QFont()
        title_font.setPointSize(max(9, int(14 * self._scale)))
        title_font.setBold(True)
        self._title_label.setFont(title_font)
        title_layout.addWidget(self._title_label)
        title_layout.addStretch()

        self._close_btn = QPushButton("✕")
        self._close_btn.setObjectName("close_btn")
        self._close_btn.setFixedSize(int(28 * self._scale), int(28 * self._scale))
        self._close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        # 字号随缩放调整（基准 16px），用 font 而非内联 stylesheet 以保留 QSS 伪类
        from PySide6.QtGui import QFont
        close_font = QFont()
        close_font.setPointSize(max(10, int(16 * self._scale)))
        self._close_btn.setFont(close_font)
        self._close_btn.clicked.connect(self.hide)
        title_layout.addWidget(self._close_btn)

        container_layout.addWidget(self._title_bar)

        # 保存 container_layout 以便延迟构建消息区
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
        input_layout.addWidget(self._input, stretch=1)

        self._send_button = QPushButton("发送")
        self._send_button.setObjectName("send_btn")
        self._send_button.setFixedSize(int(64 * self._scale), int(36 * self._scale))
        self._send_button.setCursor(Qt.CursorShape.PointingHandCursor)
        input_layout.addWidget(self._send_button)

        container_layout.addWidget(input_bar)

        main_layout.addWidget(container)

        # --- 信号连接 ---
        self._input.returnPressed.connect(self._on_send)
        self._send_button.clicked.connect(self._on_send)

        # --- 应用 QSS（字号、padding 按缩放因子调整）---
        qss = self.QSS
        s = self._scale
        qss = qss.replace("font-size: 14px;", f"font-size: {max(9, int(14 * s))}px;")
        qss = qss.replace("font-size: 16px;", f"font-size: {max(10, int(16 * s))}px;")
        qss = qss.replace("font-size: 12px;", f"font-size: {max(8, int(12 * s))}px;")
        qss = qss.replace("font-size: 11px;", f"font-size: {max(8, int(11 * s))}px;")
        qss = qss.replace("padding: 8px 12px;", f"padding: {int(8 * s)}px {int(12 * s)}px;")
        qss = qss.replace("border-radius: 12px;", f"border-radius: {int(12 * s)}px;")
        qss = qss.replace("border-radius: 18px;", f"border-radius: {int(18 * s)}px;")
        qss = qss.replace("border-radius: 16px;", f"border-radius: {int(16 * s)}px;")
        qss = qss.replace("border-radius: 6px;", f"border-radius: {int(6 * s)}px;")
        qss = qss.replace("border-radius: 8px;", f"border-radius: {int(8 * s)}px;")
        self.setStyleSheet(qss)

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
        # 插入到标题栏之后、输入栏之前
        container_layout.insertWidget(1, self._message_scroll, stretch=1)

    def _ensure_messages_built(self) -> None:
        """_show_messages=False 时收到 bot 消息触发延迟构建消息区，
        并以动画过渡到完整高度。
        """
        if self._messages_built:
            return
        self._messages_built = True
        self._build_message_scroll(self._container_layout)
        # 动画过渡到 WIN_HEIGHT_FULL
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
        - user/system 消息忽略（无显示区）
        - bot 消息触发延迟构建消息区，并以动画过渡到完整高度
        当 show_chat_messages=True 时正常追加气泡。

        Args:
            role: "user" 右对齐蓝色气泡，"system" 居中灰色气泡，
                  其他左对齐深灰气泡。label 从 config 读取。
            text: 消息文本内容。
            reply_to: 可选，被回复消息的 ID（用于显示"↩ 回复某条消息"标记）。
        """
        # 未开启显示区时，任意非 system 消息触发延迟构建
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
        """创建单条消息气泡 widget。

        Args:
            role: 消息角色，决定气泡颜色和对齐。
            label: 发送者名称（系统消息为空）。
            text: 消息文本内容。
            reply_to: 可选，被回复消息 ID（用于显示"↩ 回复某条消息"标记）。
            emoji_bytes: 可选，emoji 图片字节（GIF/PNG），渲染为图片标签。

        Returns:
            配置好样式的 QFrame 气泡。
        """
        bubble = QFrame()
        layout = QVBoxLayout(bubble)
        layout.setContentsMargins(10, 6, 10, 6)
        layout.setSpacing(2)

        # 回复标记（仅 bot 气泡且带 reply_to 时显示）
        if reply_to and role not in ("user", "system"):
            reply_label = QLabel(f"↩ 回复消息 {reply_to[:8]}")
            reply_label.setStyleSheet(
                f"color: {self.C_PRIMARY}; font-size: 10px; background: transparent;"
            )
            layout.addWidget(reply_label)

        if role == "system":
            msg = QLabel(text)
            msg.setWordWrap(True)
            msg.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.MinimumExpanding)
            msg.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            msg.setTextFormat(Qt.TextFormat.PlainText)
            msg.setStyleSheet(
                f"color: {self.C_ON_SURFACE_VARIANT}; background: transparent;"
                " font-size: 12px; font-style: italic;"
            )
            layout.addWidget(msg)
            bubble.setStyleSheet(
                f"background-color: {self.C_SURFACE_CONTAINER_HIGHEST};"
                " border-radius: 8px;"
            )
            bubble.setFixedWidth(340)
        else:
            sender = QLabel(label)
            sender.setStyleSheet(
                "font-size: 11px; font-weight: 600; background: transparent;"
            )

            layout.addWidget(sender)

            # 文本（若有）
            if text:
                msg = QLabel(text)
                msg.setWordWrap(True)
                msg.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.MinimumExpanding)
                msg.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                msg.setTextFormat(Qt.TextFormat.PlainText)
                msg.setStyleSheet("font-size: 14px; background: transparent;")
                layout.addWidget(msg)

            # emoji 图片（若有）
            if emoji_bytes:
                emoji_label = QLabel()
                emoji_label.setStyleSheet("background: transparent;")
                from PySide6.QtGui import QPixmap
                pm = QPixmap()
                pm.loadFromData(emoji_bytes)
                if not pm.isNull():
                    # 限制最大显示尺寸，等比缩放
                    max_side = int(120 * self._scale)
                    scaled = pm.scaled(
                        max_side, max_side,
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                    emoji_label.setPixmap(scaled)
                    layout.addWidget(emoji_label)

            if role == "user":
                bubble.setStyleSheet(
                    f"background-color: {self.C_PRIMARY_CONTAINER};"
                    " border-radius: 12px;"
                )
                sender.setStyleSheet(
                    f"color: {self.C_PRIMARY}; font-size: 11px;"
                    " font-weight: 600; background: transparent;"
                )
                if text:
                    msg.setStyleSheet(
                        f"color: {self.C_ON_PRIMARY_CONTAINER}; font-size: 14px;"
                        " background: transparent;"
                    )
            else:
                bubble.setStyleSheet(
                    f"background-color: {self.C_SURFACE_CONTAINER_HIGH};"
                    " border-radius: 12px;"
                )
                sender.setStyleSheet(
                    f"color: {self.C_ON_SURFACE_VARIANT}; font-size: 11px;"
                    " font-weight: 600; background: transparent;"
                )
                if text:
                    msg.setStyleSheet(
                        f"color: {self.C_ON_SURFACE}; font-size: 14px;"
                        " background: transparent;"
                    )
            bubble.setFixedWidth(300)

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
            # 通知父组件（pet_window）计算相对偏移
            # 此处 emit 一个相对全局原点的 QPoint，由 plugin 层减去 pet 全局坐标
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

        Args:
            messages: 历史消息列表，每项为 dict {"role": "user"/"bot"/"system", "text": str}。
        """
        if not self._show_messages or not messages:
            return
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
            # 直接调 _create_bubble + insertWidget，跳过提示音和滚动延迟
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
        """检查 widget 是否属于标题栏。

        Args:
            widget: 被点击的子控件。

        Returns:
            该控件是否在标题栏的 widget 树中。
        """
        current = widget
        while current is not None:
            if current is self._title_bar:
                return True
            current = current.parentWidget()
        return False