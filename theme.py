"""
主題模組

深色主題的色彩與字型設定。
"""

import tkinter.font as tkfont

import customtkinter as ctk

APPEARANCE_MODE = "dark"

# ── 色彩配置（黑白灰單色系） ─────────────────────────
COLORS = {
    "bg":            "#111111",
    "card":          "#1b1b1b",
    "subtle":        "#262626",
    "subtle_hover":  "#303030",
    "accent":        "#e8e8e8",   # 主要按鈕、選取中的裝置
    "accent_hover":  "#ffffff",
    "accent_text":   "#d0d0d0",   # 強調文字（儲存路徑、傳送目標、速度）
    "accent_soft":   "#555555",   # 選取中裝置的 IP 文字
    "on_accent":     "#111111",   # 淺色底上的文字
    "success":       "#3ccf8e",
    "warning":       "#e6b450",
    "warning_soft":  "#262217",
    "danger":        "#f06464",
    "danger_soft":   "#3a2222",
    "text":          "#ececec",
    "text_secondary":"#9a9a9a",
    "text_dim":      "#5c5c5c",
    "border":        "#2c2c2c",
    "progress_bg":   "#2a2a2a",
    "send":          "#e8e8e8",
    "receive":       "#3ccf8e",
}

# ── 字型 ─────────────────────────────────────────────
# 依序使用第一個已安裝的字型（Noto Sans TC 即思源黑體）
FONT_CANDIDATES = ("Noto Sans TC", "Microsoft JhengHei UI", "Microsoft JhengHei")

_font_family: str | None = None


def _resolve_family() -> str | None:
    global _font_family
    if _font_family is None:
        installed = set(tkfont.families())
        _font_family = next((f for f in FONT_CANDIDATES if f in installed), "")
    return _font_family or None


def font(size: int = 13, weight: str = "normal") -> ctk.CTkFont:
    """建立統一字型（需在主視窗建立之後呼叫）"""
    family = _resolve_family()
    if family:
        return ctk.CTkFont(family=family, size=size, weight=weight)
    return ctk.CTkFont(size=size, weight=weight)
