# -*- coding: utf-8 -*-
"""桌宠 MD3 配色方案与字体系统。

提供：
- 多套 Material Design 3 配色预设（以 #9EF6FF 淡蓝 + 纯白为主色调的默认方案）
- 自定义配色支持（用户提供 primary/surface 关键色，自动推导其余 token）
- 字体方案：英文等宽编程字体（JetBrains Mono，GitHub 高 star）+ 中文 Ubuntu 字体

所有 GUI 模块通过 get_theme(config) 获取当前配色 token，实现统一风格与运行时切换。
"""

from __future__ import annotations

import colorsys
import re
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..config import DesktopPetConfig


# ============================================================================
# 字体方案
# ============================================================================

# 英文等宽编程字体：JetBrains Mono（GitHub star 数高的开源等宽编程字体，支持连字）
# 回退链覆盖 Windows/Linux/macOS 常见等宽字体
MONO_FONT_FAMILY: str = (
    '"JetBrains Mono", "Cascadia Code", "Fira Code", "Consolas", '
    '"DejaVu Sans Mono", "Liberation Mono", "Courier New", monospace'
)

# 中文字体：Ubuntu（用户指定）+ 常见中文回退
CJK_FONT_FAMILY: str = (
    '"Ubuntu", "Ubuntu Mono", "Microsoft YaHei", "Microsoft YaHei UI", '
    '"PingFang SC", "Noto Sans CJK SC", "Source Han Sans SC", sans-serif'
)

# UI 组合字体链：英文等宽优先，中文 Ubuntu 回退（Qt 按字符自动选择可用字体）
UI_FONT_FAMILY: str = (
    '"JetBrains Mono", "Ubuntu", "Microsoft YaHei", "Noto Sans CJK SC", monospace'
)

# 气泡/正文用组合字体（等宽 + Ubuntu 中文）
BUBBLE_FONT_FAMILY: str = UI_FONT_FAMILY


# ============================================================================
# MD3 配色 Token
# ============================================================================


@dataclass(frozen=True)
class ColorTokens:
    """Material Design 3 配色 token 集合。

    命名与 chat_window 原 C_* 常量保持一致，便于迁移。
    """

    primary: str
    on_primary: str
    primary_container: str
    on_primary_container: str

    surface: str
    on_surface: str
    surface_container_low: str
    surface_container: str
    surface_container_high: str
    surface_container_highest: str
    on_surface_variant: str

    outline: str
    outline_variant: str

    error: str
    on_error: str

    # 气泡专用（user/bot 气泡背景，可独立于 surface_container）
    bubble_user_bg: str
    bubble_user_fg: str
    bubble_bot_bg: str
    bubble_bot_fg: str
    bubble_system_bg: str
    bubble_system_fg: str

    # 对话气泡（pet DialogBox）配色
    dialog_bg: str
    dialog_fg: str

    # 标记色：回复引用、链接
    accent: str

    # 双色强调：第二个强调色（用于 scrollbar hover、input focus border 等）
    accent_secondary: str = ""

    # 渐变色：标题栏/发送按钮的渐变起止色（空串=不使用渐变，回退纯色）
    gradient_from: str = ""
    gradient_to: str = ""

    @property
    def is_dark(self) -> bool:
        """推测是否为暗色主题（基于 surface 亮度）。"""
        return _luminance(self.surface) < 0.5


# ============================================================================
# 配色预设
# ============================================================================

# 默认方案：#9EF6FF 淡蓝（print_all_logs 边框色）+ 纯白文字 + 深色 surface
_PRESET_MOFOX_BLUE: ColorTokens = ColorTokens(
    primary="#9EF6FF",
    on_primary="#002833",
    primary_container="#003B4A",
    on_primary_container="#CFF4FF",
    surface="#0F1416",
    on_surface="#FFFFFF",
    surface_container_low="#161C1F",
    surface_container="#1B2124",
    surface_container_high="#252B2E",
    surface_container_highest="#303639",
    on_surface_variant="#BFC9CC",
    outline="#839499",
    outline_variant="#344246",
    error="#FFB4AB",
    on_error="#690005",
    bubble_user_bg="#003B4A",
    bubble_user_fg="#CFF4FF",
    bubble_bot_bg="#252B2E",
    bubble_bot_fg="#FFFFFF",
    bubble_system_bg="#303639",
    bubble_system_fg="#BFC9CC",
    dialog_bg="rgba(27, 33, 36, 0.96)",
    dialog_fg="#FFFFFF",
    accent="#9EF6FF",
)

# 浅色版：#9EF6FF 系，浅色 surface
_PRESET_MOFOX_BLUE_LIGHT: ColorTokens = ColorTokens(
    primary="#006779",
    on_primary="#FFFFFF",
    primary_container="#B4EBFF",
    on_primary_container="#001F26",
    surface="#F5FAFC",
    on_surface="#191C1F",
    surface_container_low="#EFF3F6",
    surface_container="#E9EEF1",
    surface_container_high="#E3E8EC",
    surface_container_highest="#DDE3E7",
    on_surface_variant="#41484D",
    outline="#71787D",
    outline_variant="#C1C7CD",
    error="#BA1A1A",
    on_error="#FFFFFF",
    bubble_user_bg="#B4EBFF",
    bubble_user_fg="#001F26",
    bubble_bot_bg="#E3E8EC",
    bubble_bot_fg="#191C1F",
    bubble_system_bg="#DDE3E7",
    bubble_system_fg="#41484D",
    dialog_bg="rgba(233, 238, 241, 0.98)",
    dialog_fg="#191C1F",
    accent="#006779",
)

# 海洋深蓝
_PRESET_OCEAN: ColorTokens = ColorTokens(
    primary="#7CC7FF",
    on_primary="#00344D",
    primary_container="#004B6E",
    on_primary_container="#C7E5FF",
    surface="#0E1620",
    on_surface="#FFFFFF",
    surface_container_low="#151D27",
    surface_container="#1A2230",
    surface_container_high="#242C3A",
    surface_container_highest="#2F3745",
    on_surface_variant="#BBD0E0",
    outline="#7C92A6",
    outline_variant="#334455",
    error="#FFB4AB",
    on_error="#690005",
    bubble_user_bg="#004B6E",
    bubble_user_fg="#C7E5FF",
    bubble_bot_bg="#242C3A",
    bubble_bot_fg="#FFFFFF",
    bubble_system_bg="#2F3745",
    bubble_system_fg="#BBD0E0",
    dialog_bg="rgba(26, 34, 48, 0.96)",
    dialog_fg="#FFFFFF",
    accent="#7CC7FF",
)

# 森林绿
_PRESET_FOREST: ColorTokens = ColorTokens(
    primary="#6FE9A8",
    on_primary="#003920",
    primary_container="#005234",
    on_primary_container="#8DF8C2",
    surface="#0E1411",
    on_surface="#FFFFFF",
    surface_container_low="#151C18",
    surface_container="#1A211D",
    surface_container_high="#242B27",
    surface_container_highest="#2F3631",
    on_surface_variant="#BCC9C2",
    outline="#7C938A",
    outline_variant="#33433C",
    error="#FFB4AB",
    on_error="#690005",
    bubble_user_bg="#005234",
    bubble_user_fg="#8DF8C2",
    bubble_bot_bg="#242B27",
    bubble_bot_fg="#FFFFFF",
    bubble_system_bg="#2F3631",
    bubble_system_fg="#BCC9C2",
    dialog_bg="rgba(26, 33, 29, 0.96)",
    dialog_fg="#FFFFFF",
    accent="#6FE9A8",
)

# 日落橙
_PRESET_SUNSET: ColorTokens = ColorTokens(
    primary="#FFB59E",
    on_primary="#5B1A05",
    primary_container="#7B2C12",
    on_primary_container="#FFDBCF",
    surface="#14100E",
    on_surface="#FFFFFF",
    surface_container_low="#1B1715",
    surface_container="#201C19",
    surface_container_high="#2A2623",
    surface_container_highest="#35312D",
    on_surface_variant="#E4C6BD",
    outline="#9E8982",
    outline_variant="#4E3B35",
    error="#FFB4AB",
    on_error="#690005",
    bubble_user_bg="#7B2C12",
    bubble_user_fg="#FFDBCF",
    bubble_bot_bg="#2A2623",
    bubble_bot_fg="#FFFFFF",
    bubble_system_bg="#35312D",
    bubble_system_fg="#E4C6BD",
    dialog_bg="rgba(32, 28, 25, 0.96)",
    dialog_fg="#FFFFFF",
    accent="#FFB59E",
)

# ---- 新增6套配色预设（参考 galaxy UI 风格） ----

# 星空极光：宇宙渐变 + 极光绿
_PRESET_AURORA: ColorTokens = ColorTokens(
    primary="#6FE9A8",
    on_primary="#003920",
    primary_container="#1A3B2E",
    on_primary_container="#8DF8C2",
    surface="#0F0C29",
    on_surface="#FFFFFF",
    surface_container_low="#161331",
    surface_container="#1B1838",
    surface_container_high="#252244",
    surface_container_highest="#302D52",
    on_surface_variant="#C5C2D6",
    outline="#8B88A6",
    outline_variant="#3D3A55",
    error="#FFB4AB",
    on_error="#690005",
    bubble_user_bg="#1A3B2E",
    bubble_user_fg="#8DF8C2",
    bubble_bot_bg="#252244",
    bubble_bot_fg="#FFFFFF",
    bubble_system_bg="#302D52",
    bubble_system_fg="#C5C2D6",
    dialog_bg="rgba(27, 24, 56, 0.96)",
    dialog_fg="#FFFFFF",
    accent="#6FE9A8",
    accent_secondary="#7C3AED",
    gradient_from="#302B63",
    gradient_to="#0F0C29",
)

# 赛博霓虹：霓虹发光
_PRESET_CYBER_NEON: ColorTokens = ColorTokens(
    primary="#FF206E",
    on_primary="#FFFFFF",
    primary_container="#3D0D1E",
    on_primary_container="#FF80A8",
    surface="#1A1A1A",
    on_surface="#FFFFFF",
    surface_container_low="#222222",
    surface_container="#282828",
    surface_container_high="#333333",
    surface_container_highest="#3E3E3E",
    on_surface_variant="#CCCCCC",
    outline="#888888",
    outline_variant="#383838",
    error="#FF4444",
    on_error="#FFFFFF",
    bubble_user_bg="#3D0D1E",
    bubble_user_fg="#FF80A8",
    bubble_bot_bg="#333333",
    bubble_bot_fg="#FFFFFF",
    bubble_system_bg="#3E3E3E",
    bubble_system_fg="#CCCCCC",
    dialog_bg="rgba(40, 40, 40, 0.96)",
    dialog_fg="#FFFFFF",
    accent="#FF206E",
    accent_secondary="#00F5FF",
    gradient_from="#FF206E",
    gradient_to="#1A1A1A",
)

# 紫晶幻境：紫渐变
_PRESET_AMETHYST: ColorTokens = ColorTokens(
    primary="#8D49FD",
    on_primary="#FFFFFF",
    primary_container="#2D1A4D",
    on_primary_container="#E9D1FF",
    surface="#1A0E2E",
    on_surface="#FFFFFF",
    surface_container_low="#221636",
    surface_container="#281C3E",
    surface_container_high="#33274C",
    surface_container_highest="#3E325A",
    on_surface_variant="#D4C5E8",
    outline="#9888B8",
    outline_variant="#43385A",
    error="#FFB4AB",
    on_error="#690005",
    bubble_user_bg="#2D1A4D",
    bubble_user_fg="#E9D1FF",
    bubble_bot_bg="#33274C",
    bubble_bot_fg="#FFFFFF",
    bubble_system_bg="#3E325A",
    bubble_system_fg="#D4C5E8",
    dialog_bg="rgba(40, 28, 62, 0.96)",
    dialog_fg="#FFFFFF",
    accent="#8D49FD",
    accent_secondary="#5691F3",
    gradient_from="#8D49FD",
    gradient_to="#5691F3",
)

# 琥珀暮光：暖色琥珀/金
_PRESET_AMBER: ColorTokens = ColorTokens(
    primary="#FFB347",
    on_primary="#3D1F00",
    primary_container="#4A2C0A",
    on_primary_container="#FFD699",
    surface="#1C1410",
    on_surface="#FFFFFF",
    surface_container_low="#241B16",
    surface_container="#2A211C",
    surface_container_high="#352B26",
    surface_container_highest="#403631",
    on_surface_variant="#E4D0BD",
    outline="#9E8B7A",
    outline_variant="#4E3B35",
    error="#FFB4AB",
    on_error="#690005",
    bubble_user_bg="#4A2C0A",
    bubble_user_fg="#FFD699",
    bubble_bot_bg="#352B26",
    bubble_bot_fg="#FFFFFF",
    bubble_system_bg="#403631",
    bubble_system_fg="#E4D0BD",
    dialog_bg="rgba(42, 33, 28, 0.96)",
    dialog_fg="#FFFFFF",
    accent="#FFB347",
    accent_secondary="#FF6B35",
    gradient_from="#FFB347",
    gradient_to="#FF6B35",
)

# 翡翠琉璃：翡翠/青绿系
_PRESET_EMERALD: ColorTokens = ColorTokens(
    primary="#10B981",
    on_primary="#003D2A",
    primary_container="#0A3D2E",
    on_primary_container="#5EEAD4",
    surface="#0A1F1C",
    on_surface="#FFFFFF",
    surface_container_low="#122824",
    surface_container="#182E2A",
    surface_container_high="#223A35",
    surface_container_highest="#2D4640",
    on_surface_variant="#B0D8CE",
    outline="#7AA89E",
    outline_variant="#2D4640",
    error="#FFB4AB",
    on_error="#690005",
    bubble_user_bg="#0A3D2E",
    bubble_user_fg="#5EEAD4",
    bubble_bot_bg="#223A35",
    bubble_bot_fg="#FFFFFF",
    bubble_system_bg="#2D4640",
    bubble_system_fg="#B0D8CE",
    dialog_bg="rgba(24, 46, 42, 0.96)",
    dialog_fg="#FFFFFF",
    accent="#10B981",
    accent_secondary="#06B6D4",
    gradient_from="#10B981",
    gradient_to="#06B6D4",
)

# 玫瑰晨曦（浅色主题）：柔色优雅暖白
_PRESET_ROSE_DAWN: ColorTokens = ColorTokens(
    primary="#C77D8A",
    on_primary="#FFFFFF",
    primary_container="#F3D5DB",
    on_primary_container="#5C1A2A",
    surface="#FFF5F5",
    on_surface="#2D1A1F",
    surface_container_low="#F9EDED",
    surface_container="#F3E5E7",
    surface_container_high="#EDDCDE",
    surface_container_highest="#E7D2D5",
    on_surface_variant="#6B5558",
    outline="#9E8588",
    outline_variant="#E0CDD0",
    error="#BA1A1A",
    on_error="#FFFFFF",
    bubble_user_bg="#F3D5DB",
    bubble_user_fg="#5C1A2A",
    bubble_bot_bg="#EDDCDE",
    bubble_bot_fg="#2D1A1F",
    bubble_system_bg="#E7D2D5",
    bubble_system_fg="#6B5558",
    dialog_bg="rgba(243, 229, 231, 0.98)",
    dialog_fg="#2D1A1F",
    accent="#C77D8A",
    accent_secondary="#D4A574",
    gradient_from="#C77D8A",
    gradient_to="#D4A574",
)

PRESETS: dict[str, ColorTokens] = {
    "mofox_blue": _PRESET_MOFOX_BLUE,
    "mofox_blue_light": _PRESET_MOFOX_BLUE_LIGHT,
    "ocean": _PRESET_OCEAN,
    "forest": _PRESET_FOREST,
    "sunset": _PRESET_SUNSET,
    "aurora": _PRESET_AURORA,
    "cyber_neon": _PRESET_CYBER_NEON,
    "amethyst": _PRESET_AMETHYST,
    "amber": _PRESET_AMBER,
    "emerald": _PRESET_EMERALD,
    "rose_dawn": _PRESET_ROSE_DAWN,
}

PRESET_NAMES: list[str] = list(PRESETS.keys())


# ============================================================================
# 自定义配色推导
# ============================================================================


def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    """解析 #RRGGBB 或 #RGB 为 (r, g, b)。"""
    s = hex_color.strip().lstrip("#")
    if len(s) == 3:
        s = "".join(c * 2 for c in s)
    if len(s) != 6:
        raise ValueError(f"Invalid hex color: {hex_color}")
    return int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)


def _rgb_to_hex(r: int, g: int, b: int) -> str:
    r = max(0, min(255, int(round(r))))
    g = max(0, min(255, int(round(g))))
    b = max(0, min(255, int(round(b))))
    return f"#{r:02X}{g:02X}{b:02X}"


def _luminance(hex_color: str) -> float:
    """计算相对亮度（0~1）。"""
    try:
        r, g, b = _hex_to_rgb(hex_color)
    except Exception:
        return 0.0
    h, l, s = colorsys.rgb_to_hls(r / 255.0, g / 255.0, b / 255.0)
    return l


def _adjust(hex_color: str, delta_l: float, delta_s: float = 0.0) -> str:
    """调整亮度（delta_l）和饱和度（delta_s），返回 #RRGGBB。"""
    try:
        r, g, b = _hex_to_rgb(hex_color)
    except Exception:
        return hex_color
    h, l, s = colorsys.rgb_to_hls(r / 255.0, g / 255.0, b / 255.0)
    l = max(0.0, min(1.0, l + delta_l))
    s = max(0.0, min(1.0, s + delta_s))
    r2, g2, b2 = colorsys.hls_to_rgb(h, l, s)
    return _rgb_to_hex(r2 * 255, g2 * 255, b2 * 255)


def _on_color(bg_hex: str) -> str:
    """根据背景亮度返回黑/白前景色（保证对比度）。"""
    return "#FFFFFF" if _luminance(bg_hex) < 0.55 else "#000000"


def derive_custom_theme(primary: str, surface: str) -> ColorTokens:
    """根据用户提供的 primary 和 surface 推导完整 MD3 token 集。

    Args:
        primary: 主色 #RRGGBB。
        surface: 背景 surface #RRGGBB。

    Returns:
        推导出的 ColorTokens。
    """
    is_dark = _luminance(surface) < 0.5

    on_primary = _on_color(primary)
    primary_container = _adjust(primary, -0.25 if is_dark else 0.20)
    on_primary_container = _on_color(primary_container)

    on_surface = "#FFFFFF" if is_dark else "#1A1C1F"
    # 容器色阶：在 surface 基础上逐级提亮（暗色）或压暗（浅色）
    step = 0.04 if is_dark else -0.04
    sc_low = _adjust(surface, step)
    sc = _adjust(surface, step * 2)
    sc_high = _adjust(surface, step * 3)
    sc_highest = _adjust(surface, step * 4)
    on_surface_variant = _adjust(on_surface, -0.30 if is_dark else -0.35)

    outline = _adjust(on_surface_variant, 0.10)
    outline_variant = _adjust(surface, step * 1.5)

    error = "#FFB4AB" if is_dark else "#BA1A1A"
    on_error = _on_color(error)

    fg = "#FFFFFF" if is_dark else "#1A1C1F"
    variant_fg = on_surface_variant

    return ColorTokens(
        primary=primary,
        on_primary=on_primary,
        primary_container=primary_container,
        on_primary_container=on_primary_container,
        surface=surface,
        on_surface=on_surface,
        surface_container_low=sc_low,
        surface_container=sc,
        surface_container_high=sc_high,
        surface_container_highest=sc_highest,
        on_surface_variant=on_surface_variant,
        outline=outline,
        outline_variant=outline_variant,
        error=error,
        on_error=on_error,
        bubble_user_bg=primary_container,
        bubble_user_fg=on_primary_container,
        bubble_bot_bg=sc_high,
        bubble_bot_fg=fg,
        bubble_system_bg=sc_highest,
        bubble_system_fg=variant_fg,
        dialog_bg=_with_alpha(sc, 0.96),
        dialog_fg=fg,
        accent=primary,
        accent_secondary="",
        gradient_from="",
        gradient_to="",
    )


def _with_alpha(hex_color: str, alpha: float) -> str:
    """把 #RRGGBB 转为 rgba(r,g,b,a) 字符串。"""
    try:
        r, g, b = _hex_to_rgb(hex_color)
    except Exception:
        return hex_color
    return f"rgba({r}, {g}, {b}, {alpha})"


# ============================================================================
# 主题获取入口
# ============================================================================


def _normalize_hex(s: str) -> str:
    """规整用户输入的颜色字符串，校验为 #RRGGBB；非法返回空串。"""
    if not isinstance(s, str):
        return ""
    s = s.strip()
    if not s:
        return ""
    if not re.match(r"^#([0-9A-Fa-f]{3}|[0-9A-Fa-f]{6})$", s):
        return ""
    if len(s) == 4:  # #RGB -> #RRGGBB
        s = "#" + "".join(c * 2 for c in s[1:])
    return s.upper()


def get_theme(config: "DesktopPetConfig | None") -> ColorTokens:
    """根据配置返回当前配色 token。

    优先级：preset 名 → 若为 custom 则用 custom_primary/custom_surface 推导。
    config 为 None 时返回默认 mofox_blue。
    """
    if config is None:
        return _PRESET_MOFOX_BLUE
    try:
        theme_cfg = getattr(config, "theme", None)
    except Exception:
        theme_cfg = None
    if theme_cfg is None:
        return _PRESET_MOFOX_BLUE

    preset = getattr(theme_cfg, "preset", "mofox_blue") or "mofox_blue"
    if preset == "custom":
        primary = _normalize_hex(getattr(theme_cfg, "custom_primary", "")) or "#9EF6FF"
        surface = _normalize_hex(getattr(theme_cfg, "custom_surface", "")) or "#0F1416"
        return derive_custom_theme(primary, surface)
    return PRESETS.get(preset, _PRESET_MOFOX_BLUE)


def get_font_family(config: "DesktopPetConfig | None", *, kind: str = "ui") -> str:
    """返回字体族字符串（用于 QSS font-family）。

    kind: "ui"(默认组合) / "mono"(纯等宽) / "cjk"(纯中文) / "bubble"(气泡正文)
    """
    default = {
        "ui": UI_FONT_FAMILY,
        "mono": MONO_FONT_FAMILY,
        "cjk": CJK_FONT_FAMILY,
        "bubble": BUBBLE_FONT_FAMILY,
    }.get(kind, UI_FONT_FAMILY)
    if config is None:
        return default
    try:
        theme_cfg = getattr(config, "theme", None)
    except Exception:
        theme_cfg = None
    if theme_cfg is None:
        return default
    if kind == "mono":
        custom = getattr(theme_cfg, "font_family_mono", "")
        return custom.strip() or default
    if kind == "cjk":
        custom = getattr(theme_cfg, "font_family_cjk", "")
        return custom.strip() or default
    # ui / bubble 用组合链，优先用户自定义的 mono + cjk
    mono = (getattr(theme_cfg, "font_family_mono", "") or "").strip()
    cjk = (getattr(theme_cfg, "font_family_cjk", "") or "").strip()
    if mono or cjk:
        parts = []
        if mono:
            parts.append(mono)
        if cjk:
            parts.append(cjk)
        parts.append("monospace")
        return ", ".join(parts)
    return default


def get_font_size_scale(config: "DesktopPetConfig | None") -> float:
    """返回字号缩放因子（用户可配置，热切换）。

    默认 1.0；范围 0.5~2.0。与屏幕比例缩放因子 s 叠加作用于最终字号。
    """
    if config is None:
        return 1.0
    try:
        theme_cfg = getattr(config, "theme", None)
    except Exception:
        return 1.0
    if theme_cfg is None:
        return 1.0
    val = getattr(theme_cfg, "font_size_scale", 1.0)
    try:
        v = float(val)
    except Exception:
        return 1.0
    return max(0.5, min(2.0, v))
