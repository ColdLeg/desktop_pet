# -*- coding: utf-8 -*-
"""桌宠内置 SVG 图标资源。

提供托盘图标、桌宠默认形象等 SVG 资源的内存字节流，避免运行时依赖 PNG 文件。
所有 SVG 已优化：viewBox 标准化、去除冗余元数据。
主色固定为 #9EF6FF 系（与默认主题一致）。
"""

from __future__ import annotations

# 桌宠默认形象（简化矢量版圆角猫咪，主色 #9EF6FF 与默认主题一致）
_PET_DEFAULT_SVG_STR = """<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 240 240" width="240" height="240">
  <defs><linearGradient id="petBody" x1="0" y1="0" x2="0" y2="1">
    <stop offset="0%" stop-color="#9EF6FF"/><stop offset="100%" stop-color="#5BD6E8"/>
  </linearGradient></defs>
  <ellipse cx="120" cy="135" rx="78" ry="70" fill="url(#petBody)" stroke="#2BA9BD" stroke-width="3"/>
  <circle cx="120" cy="110" r="58" fill="url(#petBody)" stroke="#2BA9BD" stroke-width="3"/>
  <polygon points="74,70 90,30 110,68" fill="url(#petBody)" stroke="#2BA9BD" stroke-width="3" stroke-linejoin="round"/>
  <polygon points="166,70 150,30 130,68" fill="url(#petBody)" stroke="#2BA9BD" stroke-width="3" stroke-linejoin="round"/>
  <polygon points="85,62 92,44 103,60" fill="#C8FAFF"/>
  <polygon points="155,62 148,44 137,60" fill="#C8FAFF"/>
  <ellipse cx="100" cy="108" rx="9" ry="11" fill="#0F1416"/>
  <ellipse cx="140" cy="108" rx="9" ry="11" fill="#0F1416"/>
  <circle cx="103" cy="104" r="3" fill="#FFFFFF"/>
  <circle cx="143" cy="104" r="3" fill="#FFFFFF"/>
  <polygon points="116,124 124,124 120,130" fill="#2BA9BD"/>
  <path d="M120 130 Q110 142 100 136" fill="none" stroke="#0F1416" stroke-width="2.5" stroke-linecap="round"/>
  <path d="M120 130 Q130 142 140 136" fill="none" stroke="#0F1416" stroke-width="2.5" stroke-linecap="round"/>
  <circle cx="82" cy="128" r="8" fill="#FF9EB3" opacity="0.55"/>
  <circle cx="158" cy="128" r="8" fill="#FF9EB3" opacity="0.55"/>
  <path d="M195 150 Q220 140 215 175 Q213 185 200 180" fill="url(#petBody)" stroke="#2BA9BD" stroke-width="3" stroke-linejoin="round"/>
  <ellipse cx="92" cy="195" rx="16" ry="11" fill="url(#petBody)" stroke="#2BA9BD" stroke-width="2.5"/>
  <ellipse cx="148" cy="195" rx="16" ry="11" fill="url(#petBody)" stroke="#2BA9BD" stroke-width="2.5"/>
</svg>"""

# 托盘图标（简洁蓝白猫咪头像，主色 #9EF6FF 与默认主题一致）
_TRAY_ICON_SVG_STR = """<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" width="64" height="64">
  <defs><linearGradient id="trayG" x1="0" y1="0" x2="0" y2="1">
    <stop offset="0%" stop-color="#9EF6FF"/><stop offset="100%" stop-color="#5BD6E8"/>
  </linearGradient></defs>
  <circle cx="32" cy="34" r="22" fill="url(#trayG)" stroke="#2BA9BD" stroke-width="2"/>
  <polygon points="16,20 20,8 28,18" fill="url(#trayG)" stroke="#2BA9BD" stroke-width="1.8" stroke-linejoin="round"/>
  <polygon points="48,20 44,8 36,18" fill="url(#trayG)" stroke="#2BA9BD" stroke-width="1.8" stroke-linejoin="round"/>
  <ellipse cx="25" cy="32" rx="3" ry="4" fill="#0F1416"/>
  <ellipse cx="39" cy="32" rx="3" ry="4" fill="#0F1416"/>
  <circle cx="26" cy="30.5" r="1" fill="#fff"/>
  <circle cx="40" cy="30.5" r="1" fill="#fff"/>
  <path d="M32 36 Q28 42 24 39" fill="none" stroke="#0F1416" stroke-width="1.5" stroke-linecap="round"/>
  <path d="M32 36 Q36 42 40 39" fill="none" stroke="#0F1416" stroke-width="1.5" stroke-linecap="round"/>
</svg>"""

# 编码为 bytes（UTF-8，SVG 仅含 ASCII，安全）
PET_DEFAULT_SVG: bytes = _PET_DEFAULT_SVG_STR.encode("utf-8")
TRAY_ICON_SVG: bytes = _TRAY_ICON_SVG_STR.encode("utf-8")

# SVG 资源表
SVG_RESOURCES: dict[str, bytes] = {
    "pet_default": PET_DEFAULT_SVG,
    "tray_icon": TRAY_ICON_SVG,
}


def get_svg(name: str) -> bytes:
    """按名取 SVG 字节流；不存在返回 pet_default 兜底。"""
    return SVG_RESOURCES.get(name, PET_DEFAULT_SVG)
