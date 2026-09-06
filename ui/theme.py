"""Dark sports-tech visual constants for the CustomTkinter UI.

Tournament-configurable values (branding text, logo path, per-position
color names) live in data/settings.json and are loaded through
services/config_service.py. This module only maps those semantic color
names to concrete hex values and defines pure-presentation layout
constants that are not tournament data.
"""

from __future__ import annotations

import customtkinter as ctk

BACKGROUND = "#0d0f12"
SURFACE = "#15181d"
SURFACE_ALT = "#1c2027"
BORDER = "#262b33"

TEXT_PRIMARY = "#f2f4f7"
TEXT_SECONDARY = "#9aa4b2"

ACCENT_GREEN = "#28c76f"
ACCENT_GREEN_HOVER = "#22a860"

SOLD_GREEN = "#28c76f"
UNSOLD_RED = "#e5484d"

# A warm gold/tan accent for premium card treatments (e.g. Player Cards'
# sports-card styling) — not a status color like the ones above, so it's
# named for what it *is* rather than what it means.
GOLD_ACCENT = "#e3c274"

# Maps data/settings.json's position_colors *names* to concrete hex values.
POSITION_COLOR_HEX = {
    "purple": "#9b6bff",
    "blue": "#4c8dff",
    "green": "#28c76f",
    "orange": "#ff9f43",
}

WINDOW_DEFAULT_SIZE = "1366x768"
WINDOW_MIN_SIZE = (1024, 640)

FONT_FAMILY = "Segoe UI"


def heading_font(size: int = 20, weight: str = "bold") -> ctk.CTkFont:
    return ctk.CTkFont(family=FONT_FAMILY, size=size, weight=weight)


def body_font(size: int = 14, weight: str = "normal") -> ctk.CTkFont:
    return ctk.CTkFont(family=FONT_FAMILY, size=size, weight=weight)


def apply_appearance() -> None:
    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("green")
