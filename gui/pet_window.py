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

from PySide6.QtCore import QPoint, QRect, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QGuiApplication, QMouseEvent, QMovie, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QApplication, QLabel, QMenu, QSizeGrip, QVBoxLayout, QWidget

from .dialog_box import DialogBox
from .svg_assets import PET_DEFAULT_SVG
from .theme import get_theme

if TYPE_CHECKING:
    from ..config import DesktopPetConfig


class SvgPetLabel(QLabel):
    """用 QSvgRenderer 绘制 SVG / QMovie 播放 GIF / QPixmap 位图的 QLabel 子类。

    兼容现有调用方（`pixmap()`/`setPixmap()`）：
    - setPixmap(svg_bytes) 接收 SVG 字节流并加载到 QSvgRenderer
    - setPixmap(QMovie) 接收 QMovie 并播放 GIF 动画
    - setPixmap(QPixmap) 接收静态位图
    - paintEvent 根据当前来源类型渲染
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._svg_renderer: QSvgRenderer | None = None
        self._fallback_pixmap: QPixmap | None = None
        self._movie: QMovie | None = None
        self._logical_size: QSize = QSize(0, 0)

    def setPixmap(self, source: bytes | QPixmap | QMovie) -> None:  # type: ignore[override]
        """接收 SVG 字节流、QMovie（GIF 动画）或 QPixmap 兜底图。"""
        # 停止旧 movie
        self.stop_movie()

        if isinstance(source, QMovie):
            self._svg_renderer = None
            self._fallback_pixmap = None
            self._movie = source
            self._movie.setParent(self)
            self._movie.frameChanged.connect(self._on_movie_frame)
            self._movie.start()
            return

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

    def stop_movie(self) -> None:
        """停止当前 GIF 动画播放。"""
        if self._movie is not None:
            try:
                self._movie.frameChanged.disconnect(self._on_movie_frame)
            except Exception:
                pass
            self._movie.stop()
            self._movie = None

    def _on_movie_frame(self, _frame: int) -> None:
        """QMovie 帧更新时触发重绘。"""
        self.update()

    def pixmap(self) -> QPixmap:  # type: ignore[override]
        """返回占位 QPixmap，尺寸与当前 widget 逻辑尺寸一致。"""
        if self._movie is not None and self._movie.isValid():
            return self._movie.currentPixmap()
        if self._fallback_pixmap is not None and not self._fallback_pixmap.isNull():
            return self._fallback_pixmap
        return QPixmap(self._logical_size)

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        self._logical_size = self.size()
        super().resizeEvent(event)

    def _render_target_rect(self) -> QRect:
        """计算实际渲染内容在 widget 内的目标矩形（居中、保持纵横比）。

        SVG 矢量、QMovie GIF、位图共用此算法；paintEvent 据此绘制，
        rendered_rect 据此对外暴露，确保两者永不发散。
        """
        w = max(1, self.width())
        h = max(1, self.height())
        if self._svg_renderer is not None:
            vb = self._svg_renderer.defaultSize()
            vw = max(1, vb.width())
            vh = max(1, vb.height())
            scale = min(w / vw, h / vh)
            dw = int(vw * scale)
            dh = int(vh * scale)
            return QRect((w - dw) // 2, (h - dh) // 2, dw, dh)
        if self._movie is not None and self._movie.isValid():
            sz = self._movie.currentPixmap().size()
            pw = max(1, sz.width())
            ph = max(1, sz.height())
            scale = min(w / pw, h / ph)
            dw = int(pw * scale)
            dh = int(ph * scale)
            return QRect((w - dw) // 2, (h - dh) // 2, dw, dh)
        if self._fallback_pixmap is not None and not self._fallback_pixmap.isNull():
            pm = self._fallback_pixmap
            pw = max(1, pm.width())
            ph = max(1, pm.height())
            scale = min(w / pw, h / ph)
            dw = int(pw * scale)
            dh = int(ph * scale)
            return QRect((w - dw) // 2, (h - dh) // 2, dw, dh)
        return QRect(0, 0, w, h)

    def rendered_rect(self) -> QRect:
        """对外暴露的实际渲染矩形（widget 局部坐标）。

        供 PetWindow._position_dialog 取 SVG/位图实际边界，使气泡紧贴角色
        图像边缘而非整个 widget 边框（矢量图居中缩放后不填满窗口）。
        """
        return self._render_target_rect()

    def paintEvent(self, event) -> None:  # type: ignore[override]
        """根据当前内容类型渲染：SVG 矢量 / GIF 动画 / 静态位图。"""
        if self._svg_renderer is not None:
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            self._svg_renderer.render(painter, self._render_target_rect())
            return

        if self._movie is not None and self._movie.isValid():
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            r = self._render_target_rect()
            pm = self._movie.currentPixmap().scaled(
                r.width(), r.height(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            painter.drawPixmap(r.topLeft(), pm)
            return

        if self._fallback_pixmap is not None and not self._fallback_pixmap.isNull():
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            r = self._render_target_rect()
            pm = self._fallback_pixmap.scaled(
                r.width(), r.height(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            painter.drawPixmap(r.topLeft(), pm)
            return

        super().paintEvent(event)


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
    chat_toggled = Signal()
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
            self._default_image_dir = getattr(config.pet, "default_image_dir", "assets/default") or "assets/default"
            self._sleep_image_dir = getattr(config.pet, "sleep_image_dir", "assets/sleep") or "assets/sleep"
            self._switch_interval = float(getattr(config.pet, "image_switch_interval", 3.0) or 3.0)
        else:
            self._win_w = 200
            self._win_h = 200
            self._default_image = ""
            self._normal1_image = ""
            self._normal2_image = ""
            self._default_image_dir = "assets/default"
            self._sleep_image_dir = "assets/sleep"
            self._switch_interval = 3.0

        # --- 图片轮播状态 ---
        self._default_images: list[QPixmap | bytes | QMovie] = []
        self._sleep_images: list[QPixmap | bytes | QMovie] = []
        self._current_image_index: int = 0
        self._image_switch_timer: QTimer | None = None
        self._is_sleeping: bool = False
        # 主动休眠（用户通过菜单切换；与定时休眠并行，任一生效即显示睡眠图组）
        self._manual_sleeping: bool = False

        # --- 调整大小模式 ---
        self._resize_mode: bool = False
        self._size_grip: QSizeGrip | None = None

        # --- 单击检测（单击切换聊天窗口，与双击调整大小区分） ---
        self._click_timer: QTimer | None = None
        self._press_global_pos: QPoint | None = None

        # 按主屏面积 1% 重新计算窗口尺寸（仅当用户未自定义尺寸时）
        _custom_w = self._win_w
        _custom_h = self._win_h
        self._apply_screen_area_ratio(0.01)
        # 若用户已自定义尺寸（非默认 200），恢复自定义值
        if _custom_w != 200 or _custom_h != 200:
            self._win_w = _custom_w
            self._win_h = _custom_h

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
        self.setMinimumSize(64, 64)
        self.resize(self._win_w, self._win_h)

        if self.CLICK_THROUGH:
            self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._pet_label = SvgPetLabel(self)
        self._pet_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._load_pet_image()
        self._start_image_rotation()
        layout.addWidget(self._pet_label)

        self._dialog_box = DialogBox(self, self._config)
        self._position_dialog()

    # ---- 图片目录加载与轮播 ----

    @staticmethod
    def _plugin_root() -> Path:
        """返回插件根目录（pet_window.py 上两级）。"""
        return Path(__file__).resolve().parent.parent

    _SUPPORTED_IMAGE_EXTENSIONS: tuple[str, ...] = (".png", ".jpg", ".jpeg", ".gif", ".svg", ".bmp", ".webp")

    def _load_images_from_dir(self, dir_path: str) -> list[QPixmap | bytes | QMovie]:
        """从指定目录加载所有图片文件，按文件名自然排序。

        目录路径相对于插件根目录；绝对路径直接使用。
        GIF 文件返回 QMovie（动画），SVG 返回 bytes，其他返回预缩放 QPixmap。
        目录为空或不存在时返回空列表。

        Args:
            dir_path: 图片目录路径（相对或绝对）。

        Returns:
            图片源列表（QPixmap / SVG bytes / QMovie）。
        """
        if not dir_path:
            return []
        path = Path(dir_path)
        if not path.is_absolute():
            path = self._plugin_root() / path
        if not path.is_dir():
            return []

        # 收集支持扩展名的文件，按文件名自然排序
        import re
        files: list[Path] = []
        for f in path.iterdir():
            if f.is_file() and f.suffix.lower() in self._SUPPORTED_IMAGE_EXTENSIONS:
                files.append(f)
        if not files:
            return []

        # 自然排序：default2.png 排在 default10.png 前面
        def _natural_key(name: str) -> list:
            parts = re.split(r"(\d+)", name)
            return [int(p) if p.isdigit() else p.lower() for p in parts]

        files.sort(key=lambda f: _natural_key(f.name))

        result: list[QPixmap | bytes | QMovie] = []
        win_w = max(1, self._win_w)
        win_h = max(1, self._win_h)
        for f in files:
            # GIF 文件：使用 QMovie 播放动画（缩放由 paintEvent 统一处理）
            if f.suffix.lower() == ".gif":
                try:
                    movie = QMovie(str(f))
                    if movie.isValid():
                        result.append(movie)
                except Exception:
                    pass
                continue

            # SVG 文件：直接存储原始字节，由 SvgPetLabel.setPixmap() 处理
            if f.suffix.lower() == ".svg":
                try:
                    result.append(f.read_bytes())
                except Exception:
                    pass
                continue

            pixmap = QPixmap(str(f))
            if not pixmap.isNull():
                scaled = pixmap.scaled(
                    win_w, win_h,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                result.append(scaled)
        return result

    # ---- 主动休眠（切换睡眠图组，与定时休眠并行） ----

    def set_manual_sleep(self, sleeping: bool) -> None:
        """设置/解除主动休眠。

        生效期间桌宠显示 sleep 图组（assets/sleep）；
        与定时休眠（按时间表）相互独立，任一生效即显示睡眠图组。

        Args:
            sleeping: True 进入主动休眠，False 解除。
        """
        old = self._manual_sleeping
        self._manual_sleeping = sleeping
        if old != sleeping:
            # 状态变化：立即切换图组并重置索引
            self._current_image_index = 0
            if sleeping and self._sleep_images:
                self._set_pet_pixmap(self._sleep_images[0])
            elif not sleeping and not self._is_sleeping and self._default_images:
                # 仅当定时休眠也不在生效时才回到默认图组
                self._set_pet_pixmap(self._default_images[0])

    @property
    def manual_sleeping(self) -> bool:
        """当前是否处于主动休眠。"""
        return self._manual_sleeping

    def _check_day_night(self) -> bool:
        """根据当前时间和配置判断是否处于睡眠模式。

        与 DayNightService._update_mode() 逻辑一致：
        白天：wake_start_hour <= hour < sleep_start_hour
        夜晚：其他情况。

        Returns:
            True 表示睡眠（夜晚）模式。
        """
        if not self._config:
            return False
        try:
            cfg = self._config
            if not cfg.sleep.enabled:
                return False
            from datetime import datetime
            hour = datetime.now().hour
            wake = int(cfg.sleep.wake_start_hour)
            sleep = int(cfg.sleep.sleep_start_hour)
            # 白天：wake <= hour < sleep
            return not (wake <= hour < sleep)
        except Exception:
            return False

    def _start_image_rotation(self) -> None:
        """启动图片轮播定时器。"""
        if self._image_switch_timer is not None:
            self._image_switch_timer.stop()
        self._image_switch_timer = QTimer(self)
        self._image_switch_timer.timeout.connect(self._switch_to_next_image)
        interval_ms = int(self._switch_interval * 1000)
        self._image_switch_timer.start(interval_ms)

    def _switch_to_next_image(self) -> None:
        """切换到下一张图片，同时检测休眠状态变化。

        休眠判定：主动休眠 或 定时休眠时段（任一生效即显示 sleep 图组）。
        """
        # 检查休眠状态变化（主动 + 定时合并判定）
        was_sleeping = self._is_sleeping
        self._is_sleeping = self._manual_sleeping or self._check_day_night()
        if self._is_sleeping != was_sleeping:
            # 休眠状态变化：切换图片组并重置索引
            self._current_image_index = 0
            if self._is_sleeping and self._sleep_images:
                self._set_pet_pixmap(self._sleep_images[0])
            elif not self._is_sleeping and self._default_images:
                self._set_pet_pixmap(self._default_images[0])
            # 若新列表为空，保持当前图片不变
            return

        # 选择当前活跃的图片列表
        images = self._sleep_images if self._is_sleeping else self._default_images
        if not images:
            return
        if len(images) <= 1:
            return

        self._current_image_index = (self._current_image_index + 1) % len(images)
        self._set_pet_pixmap(images[self._current_image_index])

    def _set_pet_pixmap(self, source: QPixmap | bytes | QMovie) -> None:
        """设置宠物的显示图片，同时更新气泡位置。

        支持 SVG bytes、QMovie（GIF 动画）和普通 QPixmap。
        注意：不调用 adjustSize()，窗口为固定尺寸，标签在布局中自动填充。
        """
        if not self._pet_label:
            return
        self._pet_label.setText("")
        self._pet_label.setStyleSheet("")
        # SvgPetLabel.setPixmap 统一处理 bytes / QPixmap / QMovie
        self._pet_label.setPixmap(source)
        self._position_dialog()

    # ---- 宠物图片加载 ----

    def _load_pet_image(self) -> None:
        """加载并显示宠物角色图片。

        优先级：目录轮播 → 单文件兜底 → 内置 SVG。
        先尝试从 default_image_dir / sleep_image_dir 加载图片列表，
        若目录为空则回退到 normal1_image → default_image → 内置 SVG。
        休眠判定：主动休眠 或 定时休眠时段（任一生效即用 sleep 图组）。
        """
        # 0) 判断当前休眠状态（主动 + 定时合并）
        self._is_sleeping = self._manual_sleeping or self._check_day_night()

        # 1) 预加载图片列表
        self._default_images = self._load_images_from_dir(self._default_image_dir)
        self._sleep_images = self._load_images_from_dir(self._sleep_image_dir)

        # 2) 选择当前活跃的图片列表
        active_list = self._sleep_images if self._is_sleeping else self._default_images
        if active_list:
            self._current_image_index = 0
            self._set_pet_pixmap(active_list[0])
            return

        # 3) 目录为空 — 回退到单文件兜底
        img_path = self._normal1_image or self._default_image
        path = Path(img_path) if img_path else None
        full_path: Path | None = None
        if path:
            if not path.is_absolute():
                full_path = self._plugin_root() / path
            else:
                full_path = path

        # 3a) 优先 SVG 文件
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

        # 3b) 位图文件兜底
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

        # 3c) 内置 SVG（无文件依赖，始终可用）
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
        ref_rect: QRect | None = None,
    ) -> tuple[_Placement, QRect]:
        """计算目标窗口相对桌宠的最佳放置方位与目标矩形。

        策略（prefer="auto"）：
        - 同时评估上下/左右四个方向的可用空间
        - 一轴能容纳且另一轴不足 → 选能容纳的轴
        - 两轴都能容纳 → 选空间更宽裕的轴（修复：原实现恒等式导致从不比较）
        - 全部不足 → 选剩余空间最大的一侧并钳制

        水平布局方向（审美）：chat（大窗口）贴屏幕边框、pet 在内侧，
        即优先把 chat 放到「靠近边框（空间小）」一侧；若该侧空间不足以
        容纳 chat 则回退到「空间大」一侧，避免重叠。
        垂直布局方向：保持「空间大的一侧」，上下无对调需求。

        Args:
            ref_rect: 可选基准全局矩形。默认用桌宠 widget 全局几何；
                传 pixmap 实际渲染矩形时，气泡会紧贴角色边缘而非 widget 边框。

        prefer 可强制 "horizontal" / "vertical"。

        Returns:
            (placement, target_global_rect)
        """
        screen = self._resolve_screen()
        pet_global = self.mapToGlobal(QPoint(0, 0))
        if ref_rect is not None and not ref_rect.isNull():
            ref_top_left = ref_rect.topLeft()
            pw = ref_rect.width()
            ph = ref_rect.height()
        else:
            ref_top_left = pet_global
            pw = self.width()
            ph = self.height()

        if screen is None:
            # 无屏幕信息：默认右侧
            p = _Placement("horizontal", "right")
            x = ref_top_left.x() + pw + margin
            y = ref_top_left.y() + (ph - target_h) // 2
            return p, QRect(x, y, target_w, target_h)

        avail = screen.availableGeometry()

        # 四方向可用空间
        right_space = avail.right() - (ref_top_left.x() + pw)
        left_space = ref_top_left.x() - avail.left()
        bottom_space = avail.bottom() - (ref_top_left.y() + ph)
        top_space = ref_top_left.y() - avail.top()

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

        if h_can and v_can:
            # 两轴都能容纳：选空间更宽裕的轴（修复恒等式缺陷）
            h_best = max(right_space, left_space)
            v_best = max(bottom_space, top_space)
            axis = "horizontal" if h_best >= v_best else "vertical"
        elif h_can:
            axis = "horizontal"
        elif v_can:
            axis = "vertical"
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

        # 选定轴上的具体侧
        if axis == "vertical":
            if v_ok_bottom and (not v_ok_top or bottom_space >= top_space):
                side = "bottom"
            elif v_ok_top:
                side = "top"
            else:
                side = "bottom"
        else:  # horizontal
            # 审美对调：优先把 chat 放到靠近边框（空间小）的一侧，使大窗口贴边框、
            # pet 被推到内侧。仅当该侧空间足以容纳 chat 时才对调，否则回退空间大的一侧。
            smaller_side = "left" if left_space <= right_space else "right"
            smaller_space = left_space if smaller_side == "left" else right_space
            larger_side = "right" if smaller_side == "left" else "left"
            larger_ok = h_ok_right if larger_side == "right" else h_ok_left
            if smaller_space >= target_w + margin:
                side = smaller_side
            elif larger_ok:
                side = larger_side
            else:
                side = smaller_side

        # 计算坐标
        if axis == "vertical":
            # 水平居中对齐桌宠
            x = ref_top_left.x() + (pw - target_w) // 2
            if side == "bottom":
                y = ref_top_left.y() + ph + margin
            else:  # top
                y = ref_top_left.y() - target_h - margin
        else:  # horizontal
            # 垂直居中对齐桌宠
            y = ref_top_left.y() + (ph - target_h) // 2
            if side == "right":
                x = ref_top_left.x() + pw + margin
            else:  # left
                x = ref_top_left.x() - target_w - margin

        # 钳制到屏幕可视区域
        x = max(avail.left(), min(x, avail.right() - target_w))
        y = max(avail.top(), min(y, avail.bottom() - target_h))

        return _Placement(axis, side), QRect(x, y, target_w, target_h)

    # ---- Dialog 气泡定位 ----

    def _position_dialog(self) -> None:
        """将对话气泡定位到桌宠角色旁（智能上下/左右）。

        基准为 SVG/位图实际渲染区域（rendered_rect），使气泡紧贴角色边缘
        而非整个 widget 边框。复用 _compute_placement，消除原重复算法。
        """
        if not self._dialog_box or not self._pet_label:
            return

        # 取角色实际渲染矩形（widget 局部）→ 转全局
        local = self._pet_label.rendered_rect()
        pix_global_top_left = self._pet_label.mapToGlobal(local.topLeft())
        ref_rect = QRect(pix_global_top_left, local.size())

        dialog_w = max(1, self._dialog_box.width())
        dialog_h = max(1, self._dialog_box.height())

        _placement, rect = self._compute_placement(
            dialog_w, dialog_h, prefer="auto", margin=6, ref_rect=ref_rect
        )
        self._dialog_box.move(rect.topLeft())

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
        """将聊天窗口定位到桌宠上方或下方。

        策略：始终垂直布局（上/下），避免水平布局时互相遮挡。
        水平居中对齐桌宠；全部钳制到桌宠中心所在屏幕的可视区域。
        """
        chat_w = chat_window.width()
        chat_h = chat_window.height()
        placement, rect = self._compute_placement(chat_w, chat_h, prefer="vertical", margin=10)
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

    def follow_move_chat(self, chat_window: QWidget, delta: QPoint) -> None:
        """follow 模式下拖动桌宠时移动聊天窗口。

        目标布局：chat 在 pet 上方或下方，两者不重叠。
        步骤：
        1. 按 delta 平移 chat 保持相对位置（带屏幕钳制）
        2. 基于当前 pet 位置用 _compute_placement 垂直布局重新算 chat 最佳位置
        3. 若 chat 与 pet 重叠，则把 pet 推到 chat 内侧，允许 pet 跳动
        """
        if not delta.isNull():
            # 先按 delta 平移保持相对位置（内部带屏幕钳制）
            self.move_chat_by_delta(chat_window, delta)

        # 基于当前 pet 位置重新智能布局 chat（垂直：上/下）
        chat_w = chat_window.width()
        chat_h = chat_window.height()
        placement, rect = self._compute_placement(chat_w, chat_h, prefer="vertical", margin=10)
        chat_window.move(rect.topLeft())

        # 检测 pet 与 chat 是否重叠；重叠则把 pet 推到 chat 内侧
        pet_rect = QRect(self.mapToGlobal(QPoint(0, 0)), self.size())
        chat_rect = QRect(chat_window.pos(), chat_window.size())
        if not pet_rect.intersects(chat_rect):
            return  # 不重叠，布局已完成

        # 计算把 pet 推到 chat 内侧的目标坐标
        screen = self._resolve_screen()
        avail = screen.availableGeometry() if screen else None
        margin = 10
        if placement.axis == "horizontal":
            if placement.side == "left":
                # chat 在 pet 左侧（贴左边框）→ pet 推到 chat 右侧
                new_pet_x = chat_rect.right() + margin + 1
            else:  # right
                # chat 在 pet 右侧（贴右边框）→ pet 推到 chat 左侧
                new_pet_x = chat_rect.left() - pet_rect.width() - margin - 1
            new_pet_y = pet_rect.y()
        else:  # vertical
            if placement.side == "top":
                # chat 在 pet 上方（贴上边框）→ pet 推到 chat 下方
                new_pet_y = chat_rect.bottom() + margin + 1
            else:  # bottom
                # chat 在 pet 下方（贴下边框）→ pet 推到 chat 上方
                new_pet_y = chat_rect.top() - pet_rect.height() - margin - 1
            new_pet_x = pet_rect.x()

        # pet 推动后钳制到屏幕可视区域
        if avail is not None:
            new_pet_x = max(avail.left(), min(new_pet_x, avail.right() - pet_rect.width()))
            new_pet_y = max(avail.top(), min(new_pet_y, avail.bottom() - pet_rect.height()))

        self.move(new_pet_x, new_pet_y)
        # pet 移动后气泡也要跟随重定位
        self._position_dialog()

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
        self._start_image_rotation()

    # ---- 透明度 ----

    def set_opacity(self, opacity: float) -> None:
        """设置窗口透明度。"""
        self.setWindowOpacity(max(0.1, min(1.0, opacity)))

    # ---- 右键菜单 ----

    def set_tray_manager(self, tray_manager: Any) -> None:
        """注入 TrayManager 实例，用于复用其菜单构造逻辑。"""
        self._tray_manager = tray_manager

    def contextMenuEvent(self, event) -> None:
        """右键桌宠时弹出与托盘同款子菜单（含主题 QSS 美化）。"""
        if self._tray_manager is None:
            super().contextMenuEvent(event)
            return
        menu = QMenu(self)
        # 应用与托盘一致的主题美化（背景/条目/选中态/分隔线）
        try:
            qss = self._tray_manager._menu_qss()
            if qss:
                menu.setStyleSheet(qss)
        except Exception:
            pass
        self._tray_manager.build_menu(menu, with_quit_confirm=True)
        menu.exec(event.globalPos())
        event.accept()

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        """双击宠物窗口时切换调整大小模式（并取消挂起的单击信号）。"""
        if event.button() == Qt.MouseButton.LeftButton:
            # 取消双击第一次释放挂起的单击
            if self._click_timer is not None and self._click_timer.isActive():
                self._click_timer.stop()
            self._toggle_resize_mode()
            event.accept()
        else:
            super().mouseDoubleClickEvent(event)

    def paintEvent(self, event) -> None:
        """绘制窗口：在调整大小模式下显示淡蓝色边框。"""
        super().paintEvent(event)
        if self._resize_mode:
            painter = QPainter(self)
            painter.setPen(QColor("#9EF6FF"))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(0, 0, self.width() - 1, self.height() - 1)

    # ---- 调整大小模式 ----

    def _toggle_resize_mode(self) -> None:
        """切换调整大小模式。"""
        if self._resize_mode:
            self._exit_resize_mode()
        else:
            self._enter_resize_mode()

    def _enter_resize_mode(self) -> None:
        """进入调整大小模式：显示边框和 QSizeGrip。"""
        self._resize_mode = True
        # 添加右下角缩放手柄
        if self._size_grip is None:
            self._size_grip = QSizeGrip(self)
            gs = 20
            self._size_grip.setGeometry(
                self.width() - gs, self.height() - gs, gs, gs
            )
            self._size_grip.setStyleSheet(
                "QSizeGrip { background-color: rgba(158, 246, 255, 60); "
                "border: 1px solid #9EF6FF; }"
            )
        self._size_grip.show()
        self._size_grip.raise_()
        self.update()

    def resizeEvent(self, event) -> None:
        """窗口大小变化时保持 1:1 宽高比，并更新 QSizeGrip 位置。"""
        w = event.size().width()
        h = event.size().height()
        if w != h:
            # 强制等比例：取较大边作为正方形边长
            s = max(w, h)
            self.resize(s, s)
            return
        super().resizeEvent(event)
        if self._size_grip and self._resize_mode:
            gs = 20
            self._size_grip.setGeometry(
                self.width() - gs, self.height() - gs, gs, gs
            )
        # 气泡位置也需要更新
        self._position_dialog()

    def _exit_resize_mode(self) -> None:
        """退出调整大小模式：隐藏边框和手柄，保存新尺寸到配置。"""
        self._resize_mode = False
        if self._size_grip:
            self._size_grip.hide()
        self.update()
        # 更新内部尺寸记录并持久化到配置
        self._win_w = self.width()
        self._win_h = self.height()
        self._save_pet_size()
        # 重新加载图片以适应新尺寸
        self._default_images = self._load_images_from_dir(self._default_image_dir)
        self._sleep_images = self._load_images_from_dir(self._sleep_image_dir)
        if self._is_sleeping and self._sleep_images:
            self._current_image_index = 0
            self._set_pet_pixmap(self._sleep_images[0])
        elif not self._is_sleeping and self._default_images:
            self._current_image_index = 0
            self._set_pet_pixmap(self._default_images[0])
        self._position_dialog()

    def _save_pet_size(self) -> None:
        """将当前窗口尺寸保存到配置。"""
        if not self._config:
            return
        try:
            self._config.pet.pet_width = self.width()
            self._config.pet.pet_height = self.height()
        except Exception:
            pass

    # ---- 鼠标拖拽 ----

    def mousePressEvent(self, event: QMouseEvent) -> None:
        """记录拖拽起始位置和按下坐标（用于单击判定）。"""
        if event.button() == Qt.MouseButton.LeftButton and not self.CLICK_THROUGH:
            self._drag_position = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            self._press_global_pos = event.globalPosition().toPoint()
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
        """释放鼠标时清除拖拽状态；未发生移动的左键释放判定为单击。

        单击 → 触发 chat_toggled（切换聊天窗口开/关），取代菜单栏的聊天选项。
        双击（调整大小模式）由 Qt 的双击事件序列处理：单击计时器在双击的
        第二次 press 到达前不会超时，避免误触发。
        """
        if (
            event.button() == Qt.MouseButton.LeftButton
            and self._press_global_pos is not None
            and not self.CLICK_THROUGH
        ):
            # 位移在阈值内视为单击（拖拽超过阈值不算）
            release_pos = event.globalPosition().toPoint()
            moved = (release_pos - self._press_global_pos).manhattanLength()
            if moved <= 4:
                self._emit_single_click()
        self._drag_position = None
        self._press_global_pos = None
        event.accept()
        super().mouseReleaseEvent(event)

    def _emit_single_click(self) -> None:
        """延迟发射单击信号（双击时取消，避免与调整大小模式冲突）。

        Qt 事件序列：单击 = press/release；双击 = press/release/press/双击事件/release。
        用 QApplication.doubleClickInterval 做延迟窗口，若期间来了双击则取消单击。
        """
        if self._click_timer is not None and self._click_timer.isActive():
            # 已有待处理的单击（双击的第一次释放）→ 取消
            self._click_timer.stop()
            return
        self._click_timer = QTimer(self)
        self._click_timer.setSingleShot(True)
        self._click_timer.setInterval(QApplication.doubleClickInterval())
        self._click_timer.timeout.connect(self.chat_toggled.emit)
        self._click_timer.start()
