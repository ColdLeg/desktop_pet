# -*- coding: utf-8 -*-
"""桌面宠物主窗口。

提供透明、无边框、置顶的窗口，用于渲染宠物角色和承载对话气泡。

功能特性：
- 透明背景，支持逐像素 Alpha 通道
- 无边框、置顶窗口（始终置顶，不可配置）
- 可通过鼠标拖拽（按住并拖动）
- 宠物图片渲染，支持缩放
- 承载 DialogBox 实例用于显示对话气泡
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import QMouseEvent, QPixmap
from PySide6.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget

from .dialog_box import DialogBox

if TYPE_CHECKING:
    from ..config import DesktopPetConfig

class PetWindow(QWidget):
    """主宠物窗口——透明、可拖拽、置顶。"""

    # 硬编码窗口属性（运行时不可配置）
    WIN_TITLE = "MoFox 桌面宠物"
    ALWAYS_ON_TOP = True
    FRAMELESS = True
    CLICK_THROUGH = False

    chat_requested = Signal()
    pet_moved = Signal()

    def __init__(
        self,
        config: DesktopPetConfig | None = None,
        parent: QWidget | None = None,
    ) -> None:
        """初始化宠物窗口。

        Args:
            config: 插件配置；如果为 None 则使用默认值。
            parent: 父级控件。
        """
        super().__init__(parent)
        self._config = config

        # --- 从配置读取的值 ---
        if config:
            self._win_w = config.pet.pet_width
            self._win_h = config.pet.pet_height
            self._default_image = config.pet.default_image
            self._normal1_image = config.pet.normal1_image
            self._normal2_image = config.pet.normal2_image
        else:
            self._win_w = 200
            self._win_h = 200
            self._default_image = "docs/logo.png"
            self._normal1_image = ""
            self._normal2_image = ""

        # --- 拖拽状态 ---
        self._drag_position: QPoint | None = None

        # --- 控件 ---
        self._pet_label: QLabel | None = None
        self._dialog_box: DialogBox | None = None

        # --- 构建 ---
        self._build_window()

    # ---- 窗口设置 ----

    def _build_window(self) -> None:
        """构建窗口和子控件。"""
        # 窗口标志
        flags = Qt.WindowType.Window
        if self.FRAMELESS:
            flags |= Qt.WindowType.FramelessWindowHint
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        if self.ALWAYS_ON_TOP:
            flags |= Qt.WindowType.WindowStaysOnTopHint

        self.setWindowFlags(flags)
        self.setWindowTitle(self.WIN_TITLE)
        self.setFixedSize(self._win_w, self._win_h)

        if self.CLICK_THROUGH:
            self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

        # 布局
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # 宠物图片标签
        self._pet_label = QLabel(self)
        self._pet_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._load_pet_image()
        layout.addWidget(self._pet_label)

        # 对话气泡（窗口子控件，通过 move() 定位）
        self._dialog_box = DialogBox(self, self._config)
        self._position_dialog()

    def _load_pet_image(self) -> None:
        """加载并缩放宠物角色图片。"""
        # 优先尝试 normal1，然后使用 default_image 作为后备
        img_path = self._normal1_image or self._default_image
        path = Path(img_path)
        if not path.is_absolute():
            full_path = Path(__file__).resolve().parent.parent / path
        else:
            full_path = path

        pixmap = QPixmap(str(full_path))
        if pixmap.isNull():
            # 后备方案：创建占位图
            pixmap = QPixmap(self._win_w, self._win_h)
            pixmap.fill(Qt.GlobalColor.transparent)
            self._pet_label.setText("\U0001F431")
            self._pet_label.setStyleSheet("font-size: 72px; color: white;")
        else:
            # 缩放到适应窗口，保持宽高比
            scaled = pixmap.scaled(
                self._win_w,
                self._win_h,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self._pet_label.setPixmap(scaled)

        self._pet_label.adjustSize()

    def _position_dialog(self) -> None:
        """将对话气泡定位到桌宠图片旁（右侧优先，空间不足时左侧）。

        垂直位置钳制在屏幕可视区域内，确保不溢出底部。
        无屏幕信息时回退到原逻辑（右侧）。
        """
        if not self._dialog_box or not self._pet_label:
            return

        # 获取 pet_label 中实际 pixmap 在屏幕上的右上角和左上角位置
        # pet_label 充满窗口，pixmap 居中缩放显示
        pixmap = self._pet_label.pixmap()
        if pixmap and not pixmap.isNull():
            pix_w = pixmap.width()
            pix_h = pixmap.height()
        else:
            pix_w, pix_h = self._win_w, self._win_h

        # 计算 pixmap 在 pet_label 中的居中偏移
        label_w = self._pet_label.width()
        label_h = self._pet_label.height()
        offset_x = max(0, (label_w - pix_w) // 2)
        offset_y = max(0, (label_h - pix_h) // 2)

        # pixmap 右上角和左上角在 pet_label 中的局部坐标
        local_right_x = offset_x + pix_w
        local_left_x = offset_x
        local_y = offset_y

        # 映射到屏幕坐标
        global_top_right = self._pet_label.mapToGlobal(QPoint(local_right_x, local_y))
        global_top_left = self._pet_label.mapToGlobal(QPoint(local_left_x, local_y))

        dialog_w = self._dialog_box.width()
        dialog_h = self._dialog_box.height()

        screen = QApplication.primaryScreen()
        if screen:
            avail = screen.availableGeometry()
            right_space = avail.right() - global_top_right.x()
            # 右侧空间不足时放左侧，否则放右侧
            if right_space < dialog_w + 10:
                dialog_x = global_top_left.x() - dialog_w - 5
            else:
                dialog_x = global_top_right.x() + 5
            # 垂直位置钳制在屏幕内
            dialog_y = max(avail.top(), min(global_top_right.y(), avail.bottom() - dialog_h))
        else:
            # 无屏幕信息时回退到原逻辑
            dialog_x = global_top_right.x() + 5
            dialog_y = global_top_right.y()

        # DialogBox 有 Qt.WindowType.Tool 标志，是顶层窗口，move() 使用屏幕坐标
        self._dialog_box.move(QPoint(dialog_x, dialog_y))

    # ---- 公开 API ----

    def show_dialog(self, text: str) -> None:
        """显示包含指定文本的对话气泡。

        Args:
            text: 要在对话框中显示的文本。
        """
        if self._dialog_box:
            self._dialog_box.show_text(text)
            self._position_dialog()

    def hide_dialog(self) -> None:
        """立即隐藏当前对话气泡。"""
        if self._dialog_box:
            self._dialog_box.hide_immediately()

    def position_chat_window(self, chat_window: QWidget) -> None:
        """将聊天窗口定位到桌宠上下方（下方优先，空间不足时上方）。
        水平居中对齐桌宠，垂直和水平位置均钳制在屏幕可视区域内。
        """
        pet_global = self.mapToGlobal(QPoint(0, 0))
        chat_w = chat_window.width()
        chat_h = chat_window.height()
        screen = QApplication.primaryScreen()
        if screen:
            avail = screen.availableGeometry()
            # 下方优先，空间不足时上方
            bottom_space = avail.bottom() - (pet_global.y() + self.height())
            if bottom_space >= chat_h + 10:
                y = pet_global.y() + self.height() + 5
            else:
                y = pet_global.y() - chat_h - 5
            # 水平居中对齐桌宠
            x = pet_global.x() + (self.width() - chat_w) // 2
            # 钳制到屏幕可视区域
            x = max(avail.left(), min(x, avail.right() - chat_w))
            y = max(avail.top(), min(y, avail.bottom() - chat_h))
        else:
            x = pet_global.x() + (self.width() - chat_w) // 2
            y = pet_global.y() + self.height() + 5
        chat_window.move(x, y)

    def reload_image(self) -> None:
        """重新加载宠物图片（例如配置更改后）。"""
        self._load_pet_image()

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        """双击宠物窗口时请求打开聊天窗口。"""
        if event.button() == Qt.MouseButton.LeftButton:
            self.chat_requested.emit()
            event.accept()
        else:
            super().mouseDoubleClickEvent(event)

    # ---- 鼠标拖拽 ----

    def mousePressEvent(self, event: QMouseEvent) -> None:
        """记录拖拽起始位置。"""
        if event.button() == Qt.MouseButton.LeftButton and not self.CLICK_THROUGH:
            self._drag_position = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        """按住鼠标按钮移动时拖拽窗口，限制在屏幕可视区域内。"""
        if event.buttons() == Qt.MouseButton.LeftButton and self._drag_position is not None and not self.CLICK_THROUGH:
            new_pos = event.globalPosition().toPoint() - self._drag_position
            # 钳制到屏幕可视区域
            screen = QApplication.primaryScreen()
            if screen:
                avail = screen.availableGeometry()
                clamped_x = max(avail.left(), min(new_pos.x(), avail.right() - self.width()))
                clamped_y = max(avail.top(), min(new_pos.y(), avail.bottom() - self.height()))
                new_pos = QPoint(clamped_x, clamped_y)
            self.move(new_pos)
            self._position_dialog()
            self.pet_moved.emit()
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        """释放鼠标时清除拖拽状态。"""
        self._drag_position = None
        event.accept()
        super().mouseReleaseEvent(event)
