# -*- coding: utf-8 -*-
"""多帧拼图：把若干截图拼成一张网格图交给 VLM。

布局参考 ``plugins/dsh-see-now/src/sheet.js``，但做了大幅简化：
- dsh 用可变档位 DP 装箱（pack.js），桌宠帧数固定且少（≤6），固定网格足够；
- dsh 在 rawvideo RGB 缓冲上零图像库操作，这里直接用 PIL，代码量小一个数量级。

格子约定：
- 每格按 cover 方式裁切（保持比例，溢出裁掉），绝不拉伸变形；
- 格子之间留 1px 缝（画布底色透出）；
- 每格左上角烧「序号 + 时间偏移」标签。
"""

from __future__ import annotations

import io
import math
from typing import TYPE_CHECKING, Sequence

if TYPE_CHECKING:
    from PIL import Image


# 画布底色（与 dsh sheet.js 的 BACKGROUND 一致）
BACKGROUND = (18, 18, 20)
# 标签底色（半透明黑）与文字色
LABEL_BG = (0, 0, 0)
LABEL_FG = (255, 235, 59)
# 格子之间的缝（像素）
SEAM = 1


def _cover_resize(img: "Image.Image", tw: int, th: int) -> "Image.Image":
    """等比缩放到完全覆盖 (tw, th)，居中裁切溢出部分（不拉伸）。"""
    from PIL import Image

    src_w, src_h = img.size
    if src_w <= 0 or src_h <= 0:
        return Image.new("RGB", (tw, th), BACKGROUND)
    scale = max(tw / src_w, th / src_h)
    new_w = max(tw, int(math.ceil(src_w * scale)))
    new_h = max(th, int(math.ceil(src_h * scale)))
    resized = img.convert("RGB").resize((new_w, new_h), Image.LANCZOS)
    left = (new_w - tw) // 2
    top = (new_h - th) // 2
    return resized.crop((left, top, left + tw, top + th))


def _grid_shape(n: int) -> tuple[int, int]:
    """根据帧数选网格形状（列, 行）。

    - 1 帧：1×1
    - 2 帧：2×1（宽屏）或 1×2（竖屏，由调用方的 cell 比例决定，这里先给 2×1）
    - 3-4 帧：2×2
    - 5-6 帧：3×2
    - 更多：尽量接近正方形
    """
    if n <= 1:
        return 1, 1
    if n == 2:
        return 2, 1
    if n <= 4:
        return 2, 2
    if n <= 6:
        return 3, 2
    cols = int(math.ceil(math.sqrt(n)))
    rows = int(math.ceil(n / cols))
    return cols, rows


def _draw_label(img: "Image.Image", text: str, scale: float = 1.0) -> None:
    """在图像左上角烧一个「序号+时间」标签。

    使用 PIL 内置默认字体（无需外部字体文件），带半透明黑底保证可读性。
    """
    from PIL import ImageDraw, ImageFont

    draw = ImageDraw.Draw(img, "RGBA")
    try:
        font = ImageFont.load_default(size=max(11, int(14 * scale)))
    except TypeError:
        # 旧版 Pillow 的 load_default 不接受 size
        font = ImageFont.load_default()

    pad_x = max(3, int(4 * scale))
    pad_y = max(2, int(3 * scale))
    try:
        bbox = draw.textbbox((0, 0), text, font=font)
        tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]
    except Exception:
        tw, th = len(text) * 7, 11

    box_w = tw + pad_x * 2
    box_h = th + pad_y * 2
    draw.rectangle([(0, 0), (box_w, box_h)], fill=(*LABEL_BG, 170))
    draw.text((pad_x, pad_y), text, fill=LABEL_FG, font=font)


def build_sheet(
    frames: Sequence[tuple[str, "Image.Image"]],
    *,
    cell_width: int = 640,
    seam: int = SEAM,
    max_total_long_side: int = 2000,
) -> bytes:
    """把多帧拼成一张网格图，返回 PNG 字节。

    Args:
        frames: ``[(label, PIL.Image), ...]``，按时间顺序；label 会烧在左上角。
        cell_width: 单格目标宽度（高度按首帧宽高比推导）。
        seam: 格子之间的缝（像素）。
        max_total_long_side: 整图长边上限，超出按比例整体缩小。

    Returns:
        PNG 字节；输入为空时返回空 bytes。
    """
    from PIL import Image

    if not frames:
        return b""

    # 单格高度：按首帧比例算，保证所有格子同尺寸
    first_w, first_h = frames[0][1].size
    aspect = (first_h / first_w) if first_w > 0 else 9 / 16
    cell_w = max(64, int(cell_width))
    cell_h = max(36, int(round(cell_w * aspect)))

    n = len(frames)
    cols, rows = _grid_shape(n)

    canvas_w = cols * cell_w + (cols - 1) * seam
    canvas_h = rows * cell_h + (rows - 1) * seam

    canvas = Image.new("RGB", (canvas_w, canvas_h), BACKGROUND)

    # 标签字号随格子大小缩放（格子越小字越小）
    label_scale = max(0.6, min(1.4, cell_w / 640.0))

    for idx, (label, img) in enumerate(frames):
        r = idx // cols
        c = idx % cols
        x = c * (cell_w + seam)
        y = r * (cell_h + seam)
        cell = _cover_resize(img, cell_w, cell_h)
        if label:
            _draw_label(cell, label, scale=label_scale)
        canvas.paste(cell, (x, y))

    # 整图过大时按比例缩小（避免 VLM 侧 token 爆炸）
    long_side = max(canvas_w, canvas_h)
    if long_side > max_total_long_side:
        ratio = max_total_long_side / long_side
        canvas = canvas.resize(
            (max(1, int(canvas_w * ratio)), max(1, int(canvas_h * ratio))),
            Image.LANCZOS,
        )

    buf = io.BytesIO()
    canvas.save(buf, format="PNG", optimize=False, compress_level=6)
    return buf.getvalue()


__all__ = ["build_sheet", "SEAM", "BACKGROUND"]
