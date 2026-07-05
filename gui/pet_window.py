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
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QPoint, QRect, QSize, Qt, Signal
from PySide6.QtGui import QGuiApplication, QMouseEvent, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QApplication, QLabel, QMenu, QVBoxLayout, QWidget

from .dialog_box import DialogBox

if TYPE_CHECKING:
    from ..config import DesktopPetConfig


class SvgPetLabel(QLabel):
    """用 QSvgRenderer 绘制 SVG 的 QLabel 子类。

    兼容现有调用方（`pixmap()`/`setPixmap()`）：
    - setPixmap(svg_bytes) 接收 SVG 字节流并加载到 QSvgRenderer
    - pixmap() 返回一个 QSize 占位对象供现有定位逻辑使用，
      其中 width/height 与 widget 尺寸一致（SVG 矢量图无固定像素尺寸）
    - paintEvent 用 QSvgRenderer 渲染到 widget 的物理像素，
      在高 DPI 下保持锐利
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._svg_renderer: QSvgRenderer | None = None
        self._fallback_pixmap: QPixmap | None = None
        self._logical_size: QSize = QSize(0, 0)

    def setPixmap(self, source: bytes | QPixmap) -> None:  # type: ignore[override]
        """接收 SVG 字节流或 QPixmap 兜底图。

        Args:
            source: SVG 文件的字节流，或一张 QPixmap（兜底/占位用）。
        """
        if isinstance(source, (bytes, bytearray)):
            renderer = QSvgRenderer(bytes(source))
            if renderer.isValid():
                self._svg_renderer = renderer
                self._fallback_pixmap = None
                self.update()
                return
            # SVG 无效则丢弃
            self._svg_renderer = None

        if isinstance(source, QPixmap):
            self._svg_renderer = None
            self._fallback_pixmap = source
            self.update()
            return

        self._svg_renderer = None
        self._fallback_pixmap = None
        self.update()

    def pixmap(self) -> QPixmap:  # type: ignore[override]
        """返回占位 QPixmap，尺寸与当前 widget 逻辑尺寸一致。

        现有 _position_dialog() 通过 pixmap().width()/height() 计算
        居中偏移；SVG 矢量图填满整个 label，因此返回 widget 尺寸即可。
        """
        if self._fallback_pixmap is not None and not self._fallback_pixmap.isNull():
            return self._fallback_pixmap
        # SVG 模式：返回与 widget 同尺寸的空 pixmap，仅为定位逻辑服务
        return QPixmap(self._logical_size)

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        self._logical_size = self.size()
        super().resizeEvent(event)

    def paintEvent(self, event) -> None:  # type: ignore[override]
        """用 QSvgRenderer 渲染到 widget 物理像素。"""
        if self._svg_renderer is not None:
            painter = QPainter(self)
            # 计算 aspect ratio 保持的居中目标矩形
            w = max(1, self.width())
            h = max(1, self.height())
            viewBox = self._svg_renderer.defaultSize()
            vw = max(1, viewBox.width())
            vh = max(1, viewBox.height())
            scale = min(w / vw, h / vh)
            dw = int(vw * scale)
            dh = int(vh * scale)
            dx = (w - dw) // 2
            dy = (h - dh) // 2
            self._svg_renderer.render(painter, QRect(dx, dy, dw, dh))
            return

        if self._fallback_pixmap is not None and not self._fallback_pixmap.isNull():
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            w = max(1, self.width())
            h = max(1, self.height())
            pm = self._fallback_pixmap.scaled(
                w, h,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self._paint_pixmap_centered(painter, pm)
            return

        super().paintEvent(event)

    def _paint_pixmap_centered(self, painter: QPainter, pm: QPixmap) -> None:
        x = (self.width() - pm.width()) // 2
        y = (self.height() - pm.height()) // 2
        painter.drawPixmap(x, y, pm)


class PetWindow(QWidget):
    """主宠物窗口——透明、可拖拽、置顶。"""

    # 硬编码窗口属性（运行时不可配置）
    WIN_TITLE = "MoFox 桌面宠物"
    ALWAYS_ON_TOP = True
    FRAMELESS = True
    CLICK_THROUGH = False

    chat_requested = Signal()
    pet_moved = Signal()
    pet_moved_delta = Signal(QPoint)

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

        # 按主屏面积 1% 重新计算窗口尺寸（覆盖配置值，保证跨分辨率观感一致）
        # area = W * H * 0.01，正方形边长 = sqrt(area)
        self._apply_screen_area_ratio(0.01)

        # --- 拖拽状态 ---
        self._drag_position: QPoint | None = None

        # --- 控件 ---
        self._pet_label: QLabel | None = None
        self._dialog_box: DialogBox | None = None

        # --- 右键菜单关联的 TrayManager（由外部 set_tray_manager 注入）---
        self._tray_manager: Any = None

        # --- 构建 ---
        self._build_window()

    # ---- 屏幕比例尺寸 ----

    def _apply_screen_area_ratio(self, ratio: float) -> None:
        """按主屏可用面积的指定比例重新计算窗口边长（正方形）。

        area = screen_w * screen_h * ratio
        side = sqrt(area)  # 取整数像素

        Args:
            ratio: 占屏幕面积的比例，例如 0.01 表示 1%。
        """
        screen = QApplication.primaryScreen()
        if screen is None:
            return  # 无屏幕信息，保留配置值
        geo = screen.availableGeometry()
        if geo.width() <= 0 or geo.height() <= 0:
            return
        area = geo.width() * geo.height() * ratio
        if area <= 0:
            return
        import math
        side = max(64, int(math.sqrt(area)))  # 不小于 64px
        self._win_w = side
        self._win_h = side

    # ---- 窗口设置 ----

    def _build_window(self) -> None:
        """构建窗口和子控件。"""
        # 窗口标志
        # 使用 Qt.WindowType.Tool：窗口不进入任务栏，但保留托盘图标
        flags = Qt.WindowType.Tool
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

        # 宠物图片标签（支持 SVG 矢量渲染，高 DPI 下保持锐利）
        self._pet_label = SvgPetLabel(self)
        self._pet_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._load_pet_image()
        layout.addWidget(self._pet_label)

        # 对话气泡（窗口子控件，通过 move() 定位）
        self._dialog_box = DialogBox(self, self._config)
        self._position_dialog()

    def _load_pet_image(self) -> None:
        """加载并显示宠物角色图片。

        SVG 文件用 QSvgRenderer 矢量渲染（高 DPI 下保持锐利）；
        其它位图格式走 QPixmap 路径作为兼容兜底。
        """
        # 优先尝试 normal1，然后使用 default_image 作为后备
        img_path = self._normal1_image or self._default_image
        path = Path(img_path)
        if not path.is_absolute():
            full_path = Path(__file__).resolve().parent.parent / path
        else:
            full_path = path

        suffix = full_path.suffix.lower()
        if suffix == ".svg" and full_path.exists():
            # SVG 矢量渲染
            with open(full_path, "rb") as f:
                svg_bytes = f.read()
            self._pet_label.setText("")
            self._pet_label.setStyleSheet("")
            self._pet_label.setPixmap(svg_bytes)
        else:
            # 位图路径（兼容旧配置）
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
                self._pet_label.setText("")
                self._pet_label.setStyleSheet("")
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

        screen = QGuiApplication.screenAt(self.geometry().center())
        if screen is None:
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

    def position_chat_window_default(self, chat_window: QWidget) -> None:
        """将聊天窗口定位到桌宠左右侧（右侧优先，空间不足时左侧）。
        垂直居中对齐桌宠，垂直和水平位置均钳制在桌宠中心所在屏幕的可视区域内。
        """
        pet_global = self.mapToGlobal(QPoint(0, 0))
        chat_w = chat_window.width()
        chat_h = chat_window.height()
        screen = QGuiApplication.screenAt(self.geometry().center())
        if screen is None:
            screen = QApplication.primaryScreen()
        if screen:
            avail = screen.availableGeometry()
            # 右侧优先，空间不足时左侧
            right_space = avail.right() - (pet_global.x() + self.width())
            if right_space >= chat_w + 10:
                x = pet_global.x() + self.width() + 5
            else:
                x = pet_global.x() - chat_w - 5
            # 垂直居中对齐桌宠
            y = pet_global.y() + (self.height() - chat_h) // 2
            # 钳制到屏幕可视区域
            x = max(avail.left(), min(x, avail.right() - chat_w))
            y = max(avail.top(), min(y, avail.bottom() - chat_h))
        else:
            x = pet_global.x() + self.width() + 5
            y = pet_global.y() + (self.height() - chat_h) // 2
        chat_window.move(x, y)

    def move_chat_by_delta(self, chat_window: QWidget, delta: QPoint) -> None:
        """按 delta 平移聊天窗口，钳制到桌宠中心所在屏幕的可视区域。

        Args:
            chat_window: 聊天窗口实例。
            delta: 桌宠位移量（屏幕坐标）。
        """
        if delta.isNull():
            return
        new_pos = chat_window.pos() + delta
        chat_w = chat_window.width()
        chat_h = chat_window.height()
        # 用 chat_window 中心点定位屏幕（拖动后中心点所在屏）
        target_center = new_pos + QPoint(chat_w // 2, chat_h // 2)
        screen = QGuiApplication.screenAt(target_center)
        if screen is None:
            screen = QApplication.primaryScreen()
        if screen:
            avail = screen.availableGeometry()
            x = max(avail.left(), min(new_pos.x(), avail.right() - chat_w))
            y = max(avail.top(), min(new_pos.y(), avail.bottom() - chat_h))
        else:
            x, y = new_pos.x(), new_pos.y()
        chat_window.move(x, y)

    def _take_screenshot(self) -> QPixmap | None:
        """截取桌宠中心所在屏的当前画面。

        Returns:
            QPixmap 或 None（无屏幕时）。
        """
        screen = QGuiApplication.screenAt(self.geometry().center())
        if screen is None:
            screen = QApplication.primaryScreen()
        if screen is None:
            return None
        return screen.grabWindow(0)

    # 兼容旧调用名
    def position_chat_window(self, chat_window: QWidget) -> None:
        """向后兼容：等价于 position_chat_window_default。"""
        self.position_chat_window_default(chat_window)

    def reload_image(self) -> None:
        """重新加载宠物图片（例如配置更改后）。"""
        self._load_pet_image()

    # ---- 透明度 ----

    def set_opacity(self, opacity: float) -> None:
        """设置窗口透明度。

        Args:
            opacity: 0.0 完全透明 ~ 1.0 完全不透明。
        """
        self.setWindowOpacity(max(0.1, min(1.0, opacity)))

    # ---- 右键菜单 ----

    def set_tray_manager(self, tray_manager: Any) -> None:
        """注入 TrayManager 实例，用于复用其菜单构造逻辑。

        Args:
            tray_manager: TrayManager 实例。
        """
        self._tray_manager = tray_manager

    def contextMenuEvent(self, event) -> None:
        """右键桌宠时弹出与托盘一致的菜单（含透明度、Pet/Chat 子菜单）。"""
        if self._tray_manager is None:
            super().contextMenuEvent(event)
            return
        menu = QMenu(self)
        # 复用 TrayManager.build_menu 填充菜单项
        self._tray_manager.build_menu(menu, with_quit_confirm=True)
        # QContextMenuEvent.globalPos() 已是 QPoint，无需 toPoint()
        menu.exec(event.globalPos())
        event.accept()

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
            # 钳制到桌宠中心所在屏幕的可视区域
            screen = QGuiApplication.screenAt(new_pos + QPoint(self.width() // 2, self.height() // 2))
            if screen is None:
                screen = QApplication.primaryScreen()
            if screen:
                avail = screen.availableGeometry()
                clamped_x = max(avail.left(), min(new_pos.x(), avail.right() - self.width()))
                clamped_y = max(avail.top(), min(new_pos.y(), avail.bottom() - self.height()))
                new_pos = QPoint(clamped_x, clamped_y)
            old_pos = self.pos()
            self.move(new_pos)
            self._position_dialog()
            self.pet_moved.emit()
            # 计算 delta 通知聊天窗口跟随
            delta = new_pos - old_pos
            if not delta.isNull():
                self.pet_moved_delta.emit(delta)
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        """释放鼠标时清除拖拽状态。"""
        self._drag_position = None
        event.accept()
        super().mouseReleaseEvent(event)
