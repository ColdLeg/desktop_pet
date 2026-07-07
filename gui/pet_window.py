# -*- coding: utf-8 -*-
"""桌面宠物主窗口（MD3 风格）。

提供透明、无边框、置顶的窗口，用于渲染宠物角色和承载对话气泡。

功能特性：
- 透明背景，支持逐像素 Alpha 通道
- 无边框、置顶窗口（始终置顶，不可配置）
- 可通过鼠标拖拽（按住并拖动）
- 宠物图片渲染：SVG 矢量优先（高 DPI 锐利），位图兜底
- 承载 DialogBox 实例用于显示对话气泡
- 位置算法：智能上下/左右布局 chat 窗口与 dialog 气泡
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QPoint, QRect, QSize, Qt, Signal
from PySide6.QtGui import QGuiApplication, QMouseEvent, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QApplication, QLabel, QMenu, QVBoxLayout, QWidget

from .dialog_box import DialogBox
from .svg_assets import PET_DEFAULT_SVG
from .theme import get_theme

if TYPE_CHECKING:
    from ..config import DesktopPetConfig


class SvgPetLabel(QLabel):
    """用 QSvgRenderer 绘制 SVG 的 QLabel 子类。

    兼容现有调用方（`pixmap()`/`setPixmap()`）：
    - setPixmap(svg_bytes) 接收 SVG 字节流并加载到 QSvgRenderer
    - pixmap() 返回一个 QSize 占位对象供现有定位逻辑使用
    - paintEvent 用 QSvgRenderer 渲染到 widget 的物理像素，高 DPI 锐利
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._svg_renderer: QSvgRenderer | None = None
        self._fallback_pixmap: QPixmap | None = None
        self._logical_size: QSize = QSize(0, 0)

    def setPixmap(self, source: bytes | QPixmap) -> None:  # type: ignore[override]
        """接收 SVG 字节流或 QPixmap 兜底图。"""
        if isinstance(source, (bytes, bytearray)):
            renderer = QSvgRenderer(bytes(source))
            if renderer.isValid():
                self._svg_renderer = renderer
                self._fallback_pixmap = None
                self.update()
                return
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
        """返回占位 QPixmap，尺寸与当前 widget 逻辑尺寸一致。"""
        if self._fallback_pixmap is not None and not self._fallback_pixmap.isNull():
            return self._fallback_pixmap
        return QPixmap(self._logical_size)

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        self._logical_size = self.size()
        super().resizeEvent(event)

    def paintEvent(self, event) -> None:  # type: ignore[override]
        """用 QSvgRenderer 渲染到 widget 物理像素。"""
        if self._svg_renderer is not None:
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
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


# ----------------------------------------------------------------------------
# 布局方位枚举（用于 chat 窗口与 dialog 气泡的智能定位）
# ----------------------------------------------------------------------------
class _Placement:
    """计算出的放置方位。

    axis: "horizontal"（chat 在 pet 左/右）或 "vertical"（chat 在 pet 上/下）
    side: 在该轴上的具体侧（"left"/"right"/"top"/"bottom"）
    """
    __slots__ = ("axis", "side")

    def __init__(self, axis: str, side: str) -> None:
        self.axis = axis
        self.side = side


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
        """初始化宠物窗口。"""
        super().__init__(parent)
        self._config = config
        self._theme = get_theme(config)

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
            self._default_image = ""
            self._normal1_image = ""
            self._normal2_image = ""

        # 按主屏面积 1% 重新计算窗口尺寸
        self._apply_screen_area_ratio(0.01)

        # --- 拖拽状态 ---
        self._drag_position: QPoint | None = None

        # --- 控件 ---
        self._pet_label: QLabel | None = None
        self._dialog_box: DialogBox | None = None

        # --- 右键菜单关联的 TrayManager ---
        self._tray_manager: Any = None

        self._build_window()

    # ---- 屏幕比例尺寸 ----

    def _apply_screen_area_ratio(self, ratio: float) -> None:
        """按主屏可用面积的指定比例重新计算窗口边长（正方形）。"""
        screen = QApplication.primaryScreen()
        if screen is None:
            return
        geo = screen.availableGeometry()
        if geo.width() <= 0 or geo.height() <= 0:
            return
        area = geo.width() * geo.height() * ratio
        if area <= 0:
            return
        import math
        side = max(64, int(math.sqrt(area)))
        self._win_w = side
        self._win_h = side

    # ---- 窗口设置 ----

    def _build_window(self) -> None:
        """构建窗口和子控件。"""
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

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._pet_label = SvgPetLabel(self)
        self._pet_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._load_pet_image()
        layout.addWidget(self._pet_label)

        self._dialog_box = DialogBox(self, self._config)
        self._position_dialog()

    def _load_pet_image(self) -> None:
        """加载并显示宠物角色图片。

        优先级：normal1_image → default_image → 内置 SVG（PET_DEFAULT_SVG）。
        SVG 用 QSvgRenderer 矢量渲染；位图走 QPixmap 兜底。
        内置 SVG 保证无任何图片文件时也有默认形象。
        """
        img_path = self._normal1_image or self._default_image
        path = Path(img_path) if img_path else None
        full_path: Path | None = None
        if path:
            if not path.is_absolute():
                full_path = Path(__file__).resolve().parent.parent / path
            else:
                full_path = path

        # 1) 优先 SVG 文件
        if full_path and full_path.suffix.lower() == ".svg" and full_path.exists():
            try:
                with open(full_path, "rb") as f:
                    svg_bytes = f.read()
                self._pet_label.setText("")
                self._pet_label.setStyleSheet("")
                self._pet_label.setPixmap(svg_bytes)
                self._pet_label.adjustSize()
                return
            except Exception:
                pass  # 读失败则继续尝试位图/内置

        # 2) 位图文件兜底
        if full_path and full_path.exists() and full_path.suffix.lower() != ".svg":
            pixmap = QPixmap(str(full_path))
            if not pixmap.isNull():
                scaled = pixmap.scaled(
                    self._win_w, self._win_h,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                self._pet_label.setText("")
                self._pet_label.setStyleSheet("")
                self._pet_label.setPixmap(scaled)
                self._pet_label.adjustSize()
                return

        # 3) 内置 SVG（无文件依赖，始终可用）
        self._pet_label.setText("")
        self._pet_label.setStyleSheet("")
        self._pet_label.setPixmap(PET_DEFAULT_SVG)
        self._pet_label.adjustSize()

    # ---- 主题刷新 ----

    def apply_theme(self, config: DesktopPetConfig | None) -> None:
        """运行时切换主题。"""
        self._config = config
        self._theme = get_theme(config)
        if self._dialog_box:
            self._dialog_box.apply_theme(config)
        self.update()

    # ---- 智能布局算法 ----

    def _resolve_screen(self) -> Any:
        """获取桌宠中心所在屏；无则主屏；再无则 None。"""
        screen = QGuiApplication.screenAt(self.geometry().center())
        if screen is None:
            screen = QApplication.primaryScreen()
        return screen

    def _compute_placement(
        self,
        target_w: int,
        target_h: int,
        *,
        prefer: str = "auto",
        margin: int = 10,
    ) -> tuple[_Placement, QRect]:
        """计算目标窗口相对桌宠的最佳放置方位与目标矩形。

        策略（prefer="auto"）：
        - 同时评估上下/左右四个方向的可用空间
        - 若上下方向能容纳（垂直空间充足）且左右任一侧不足，优先上下（垂直布局）
        - 若左右方向能容纳且上下不足，优先左右（水平布局）
        - 两侧都能容纳时，选择空间更宽裕的轴
        - 全部不足时，选剩余空间最大的一侧并钳制

        prefer 可强制 "horizontal" / "vertical"。

        Returns:
            (placement, target_global_rect)
        """
        screen = self._resolve_screen()
        pet_global = self.mapToGlobal(QPoint(0, 0))
        pw = self.width()
        ph = self.height()

        if screen is None:
            # 无屏幕信息：默认右侧
            p = _Placement("horizontal", "right")
            x = pet_global.x() + pw + margin
            y = pet_global.y() + (ph - target_h) // 2
            return p, QRect(x, y, target_w, target_h)

        avail = screen.availableGeometry()

        # 四方向可用空间
        right_space = avail.right() - (pet_global.x() + pw)
        left_space = (pet_global.x() - avail.left())
        bottom_space = avail.bottom() - (pet_global.y() + ph)
        top_space = (pet_global.y() - avail.top())

        # 水平方向能否容纳
        h_ok_right = right_space >= target_w + margin
        h_ok_left = left_space >= target_w + margin
        # 垂直方向能否容纳
        v_ok_bottom = bottom_space >= target_h + margin
        v_ok_top = top_space >= target_h + margin

        h_can = h_ok_right or h_ok_left
        v_can = v_ok_bottom or v_ok_top

        # 强制偏好
        if prefer == "horizontal":
            v_can = False
        elif prefer == "vertical":
            h_can = False

        axis: str
        side: str

        if v_can and (not h_can or (prefer == "auto" and v_can and not h_can)):
            # 优先垂直
            axis = "vertical"
            if v_ok_bottom and (not v_ok_top or bottom_space >= top_space):
                side = "bottom"
            elif v_ok_top:
                side = "top"
            else:
                side = "bottom"
        elif h_can:
            # 水平
            axis = "horizontal"
            if h_ok_right and (not h_ok_left or right_space >= left_space):
                side = "right"
            elif h_ok_left:
                side = "left"
            else:
                side = "right"
        else:
            # 都不足：选剩余空间最大的一侧，钳制
            spaces = [
                ("right", right_space, "horizontal"),
                ("left", left_space, "horizontal"),
                ("bottom", bottom_space, "vertical"),
                ("top", top_space, "vertical"),
            ]
            spaces.sort(key=lambda t: t[1], reverse=True)
            side = spaces[0][0]
            axis = spaces[0][2]

        # 计算坐标
        if axis == "vertical":
            # 水平居中对齐桌宠
            x = pet_global.x() + (pw - target_w) // 2
            if side == "bottom":
                y = pet_global.y() + ph + margin
            else:  # top
                y = pet_global.y() - target_h - margin
        else:  # horizontal
            # 垂直居中对齐桌宠
            y = pet_global.y() + (ph - target_h) // 2
            if side == "right":
                x = pet_global.x() + pw + margin
            else:  # left
                x = pet_global.x() - target_w - margin

        # 钳制到屏幕可视区域
        x = max(avail.left(), min(x, avail.right() - target_w))
        y = max(avail.top(), min(y, avail.bottom() - target_h))

        return _Placement(axis, side), QRect(x, y, target_w, target_h)

    # ---- Dialog 气泡定位 ----

    def _position_dialog(self) -> None:
        """将对话气泡定位到桌宠旁（智能上下/左右）。"""
        if not self._dialog_box or not self._pet_label:
            return

        pixmap = self._pet_label.pixmap()
        if pixmap and not pixmap.isNull():
            pix_w = pixmap.width()
            pix_h = pixmap.height()
        else:
            pix_w, pix_h = self._win_w, self._win_h

        label_w = self._pet_label.width()
        label_h = self._pet_label.height()
        offset_x = max(0, (label_w - pix_w) // 2)
        offset_y = max(0, (label_h - pix_h) // 2)

        # dialog 相对 pixmap 边缘定位；用 dialog 当前尺寸
        dialog_w = max(1, self._dialog_box.width())
        dialog_h = max(1, self._dialog_box.height())

        # 把桌宠当作 pixmap 区域来算放置
        # 临时把 pet 中心对齐到 pixmap 中心计算
        pix_global_top_left = self._pet_label.mapToGlobal(QPoint(offset_x, offset_y))
        # 构造一个以 pixmap 为中心的虚拟 pet 矩形
        # 复用 _compute_placement：把 self 的几何临时视作 pixmap 区域
        # 这里直接用 pixmap 全局矩形作为基准
        screen = self._resolve_screen()
        if screen is None:
            self._dialog_box.move(pix_global_top_left.x() + pix_w + 5,
                                  pix_global_top_left.y())
            return
        avail = screen.availableGeometry()

        right_space = avail.right() - (pix_global_top_left.x() + pix_w)
        left_space = pix_global_top_left.x() - avail.left()
        bottom_space = avail.bottom() - (pix_global_top_left.y() + pix_h)
        top_space = pix_global_top_left.y() - avail.top()

        h_can = right_space >= dialog_w + 10 or left_space >= dialog_w + 10
        v_can = bottom_space >= dialog_h + 10 or top_space >= dialog_h + 10

        margin = 6
        if v_can and (not h_can or bottom_space >= right_space):
            # 垂直优先（上下空间充足或左右不足）
            x = pix_global_top_left.x() + (pix_w - dialog_w) // 2
            if bottom_space >= top_space and bottom_space >= dialog_h + margin:
                y = pix_global_top_left.y() + pix_h + margin
            else:
                y = pix_global_top_left.y() - dialog_h - margin
        else:
            # 水平
            y = pix_global_top_left.y() + (pix_h - dialog_h) // 2
            if right_space >= left_space and right_space >= dialog_w + margin:
                x = pix_global_top_left.x() + pix_w + margin
            else:
                x = pix_global_top_left.x() - dialog_w - margin

        x = max(avail.left(), min(x, avail.right() - dialog_w))
        y = max(avail.top(), min(y, avail.bottom() - dialog_h))
        self._dialog_box.move(QPoint(x, y))

    # ---- 公开 API ----

    def show_dialog(self, text: str) -> None:
        """显示包含指定文本的对话气泡。"""
        if self._dialog_box:
            self._dialog_box.show_text(text)
            self._position_dialog()

    def hide_dialog(self) -> None:
        """立即隐藏当前对话气泡。"""
        if self._dialog_box:
            self._dialog_box.hide_immediately()

    def position_chat_window_default(self, chat_window: QWidget) -> None:
        """将聊天窗口定位到桌宠上下/左右（智能自适应）。

        策略：上下空间充足且左右不足→垂直（上/下）；否则水平（左/右）。
        垂直时水平居中对齐桌宠；水平时垂直居中对齐桌宠。
        全部钳制到桌宠中心所在屏幕的可视区域。
        """
        chat_w = chat_window.width()
        chat_h = chat_window.height()
        placement, rect = self._compute_placement(chat_w, chat_h, prefer="auto", margin=10)
        chat_window.move(rect.topLeft())

    def move_chat_by_delta(self, chat_window: QWidget, delta: QPoint) -> None:
        """按 delta 平移聊天窗口，钳制到桌宠中心所在屏幕的可视区域。"""
        if delta.isNull():
            return
        new_pos = chat_window.pos() + delta
        chat_w = chat_window.width()
        chat_h = chat_window.height()
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
        """截取桌宠中心所在屏的当前画面。"""
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
        """设置窗口透明度。"""
        self.setWindowOpacity(max(0.1, min(1.0, opacity)))

    # ---- 右键菜单 ----

    def set_tray_manager(self, tray_manager: Any) -> None:
        """注入 TrayManager 实例，用于复用其菜单构造逻辑。"""
        self._tray_manager = tray_manager

    def contextMenuEvent(self, event) -> None:
        """右键桌宠时弹出与托盘一致的菜单。"""
        if self._tray_manager is None:
            super().contextMenuEvent(event)
            return
        menu = QMenu(self)
        self._tray_manager.build_menu(menu, with_quit_confirm=True)
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
