# -*- coding: utf-8 -*-
"""画面变化打分（纯函数，无状态）。

算法移植自 ``plugins/dsh-see-now/src/cuts.js``（AGPL-3.0），只保留桌宠需要的部分：
64×36 HSV 网格 + 加权绝对差 + 亮度归一。

与 dsh 的差异：
- dsh 处理 12fps 连续视频流，需要滚动窗、黑边补偿、运动补偿；
- 桌宠是「每次 tick 连拍 N 张」的稀疏采样，只保留最核心的两步：
  ``score_pair``（相邻帧差）与 ``normalize_score``（暗场亮度补偿）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from PIL import Image


# 扫描网格：与 dsh 的 cuts.js 完全一致（SCAN_W / SCAN_H）
SCAN_W = 64
SCAN_H = 36

# 参考亮度：dsh 实测影视内容常规亮度（cuts.js: REF_V）
REF_V = 96.0
# 亮度增益上限（cuts.js: GAIN_MAX）
GAIN_MAX = 1.85
# 亮度下限，防除零（cuts.js: V_FLOOR）
V_FLOOR = 40.0
# 噪声地板：低于它不放大，避免暗场编码噪声被顶成假变化（cuts.js: NOISE_FLOOR）
NOISE_FLOOR = 2.0


def grid_rgb(img: "Image.Image") -> list[float]:
    """把 PIL 图像降采样为 64×36 RGB 网格。

    每格取 4 个采样点（0.25/0.75 位置）求平均 —— 与 dsh 的 ``gridRgb`` 一致，
    既能降噪，又把"降采样"这一步顺手做完。

    Args:
        img: 任意尺寸的 PIL 图像（会被转成 RGB）。

    Returns:
        长度 ``SCAN_W * SCAN_H * 3`` 的浮点列表（0–255）。
    """
    small = img.convert("RGB").resize((SCAN_W, SCAN_H), resample=1)  # 1 = BILINEAR
    data = list(small.getdata())
    out: list[float] = []
    for r, g, b in data:
        out.append(float(r))
        out.append(float(g))
        out.append(float(b))
    return out


def rgb_to_hsv(rgb: list[float]) -> list[float]:
    """RGB → HSV（各通道归一到 0–255，色相按角度缩放）。

    与 dsh 的 ``rgbToHsv`` 同口径：H 归一到 0–255（环形），S/V 保持 0–255。

    Args:
        rgb: ``grid_rgb`` 的输出。

    Returns:
        长度相同的 HSV 列表。
    """
    out: list[float] = []
    for i in range(0, len(rgb), 3):
        rn = rgb[i] / 255.0
        gn = rgb[i + 1] / 255.0
        bn = rgb[i + 2] / 255.0
        mx = max(rn, gn, bn)
        mn = min(rn, gn, bn)
        d = mx - mn
        h = 0.0
        if d != 0.0:
            if mx == rn:
                h = ((gn - bn) / d) % 6.0
            elif mx == gn:
                h = (bn - rn) / d + 2.0
            else:
                h = (rn - gn) / d + 4.0
            h *= 60.0
            if h < 0:
                h += 360.0
        out.append((h / 360.0) * 255.0)
        out.append(0.0 if mx == 0.0 else (d / mx) * 255.0)
        out.append(mx * 255.0)
    return out


def score_pair(prev_hsv: list[float], cur_hsv: list[float]) -> float:
    """两帧 HSV 的加权绝对差均值（0–255）。

    色相按环形处理（取 ``min(dh, 255-dh)``），低饱和像素的色相降权
    —— 与 dsh 的 ``scorePair`` 逐项一致。

    Args:
        prev_hsv: 上一帧 HSV。
        cur_hsv: 当前帧 HSV（长度必须相同）。

    Returns:
        0–255 的变化分；长度不一致时返回 0。
    """
    n = len(cur_hsv)
    if n == 0 or len(prev_hsv) != n:
        return 0.0
    total = 0.0
    for i in range(0, n, 3):
        dh = abs(cur_hsv[i] - prev_hsv[i])
        hue_diff = min(dh, 255.0 - dh)
        hue_weight = min(cur_hsv[i + 1], prev_hsv[i + 1]) / 255.0
        total += (
            hue_diff * hue_weight
            + abs(cur_hsv[i + 1] - prev_hsv[i + 1])
            + abs(cur_hsv[i + 2] - prev_hsv[i + 2])
        )
    return total / ((n / 3) * 3)


def mean_value(hsv: list[float]) -> float:
    """HSV 列表的 V 通道均值（0–255）。"""
    if not hsv:
        return 0.0
    total = 0.0
    count = 0
    for i in range(2, len(hsv), 3):
        total += hsv[i]
        count += 1
    return total / count if count else 0.0


def brightness_gain(mean_v: float) -> float:
    """亮度归一增益：只在暗场 > 1，亮画面（V ≥ REF_V）恒等于 1。

    与 dsh 的 ``brightnessGain`` 同口径 —— 亮场逐位不变，是"不破坏现有体验"的安全阀。
    """
    if not (mean_v > 0.0):
        return 1.0
    return min(GAIN_MAX, max(1.0, REF_V / max(V_FLOOR, mean_v)))


def normalize_score(raw: float, mean_v: float) -> float:
    """把绝对分换算到「参考亮度」的刻度上。

    - 地板以下不放大：暗场的编码噪声 ×1.85 会变成假变化；
    - 亮场（V ≥ REF_V）原样返回。

    Args:
        raw: ``score_pair`` 的原始分。
        mean_v: 当前帧 V 均值。

    Returns:
        归一后的分数。
    """
    if raw <= 0.0:
        return raw
    if raw < NOISE_FLOOR:
        return raw
    gain = brightness_gain(mean_v)
    if gain == 1.0:
        return raw
    return raw * gain


def analyze_frame(img: "Image.Image") -> tuple[list[float], float]:
    """一次性算出 HSV 网格与 V 均值。

    Args:
        img: PIL 图像。

    Returns:
        (hsv, mean_v)
    """
    hsv = rgb_to_hsv(grid_rgb(img))
    return hsv, mean_value(hsv)


__all__ = [
    "SCAN_W",
    "SCAN_H",
    "REF_V",
    "GAIN_MAX",
    "V_FLOOR",
    "NOISE_FLOOR",
    "grid_rgb",
    "rgb_to_hsv",
    "score_pair",
    "mean_value",
    "brightness_gain",
    "normalize_score",
    "analyze_frame",
]
