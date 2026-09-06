"""Small reusable CustomTkinter widget helpers shared across screens.

Kept separate from ui/theme.py (pure color/font/size constants) so screens
share one implementation of common presentational widgets — a colored
pill/badge label, most notably — instead of each screen re-declaring its
own copy.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import customtkinter as ctk
from PIL import Image, UnidentifiedImageError

from models.player import Position
from ui import theme


def load_image_safely(path: Path, max_size: tuple[int, int]) -> ctk.CTkImage | None:
    """Load an image file, preserving aspect ratio, resized to fit max_size.

    Never raises: a missing, unreadable, or corrupt file returns None so
    callers can fall back to a placeholder instead of crashing.
    """
    try:
        image = Image.open(path)
        image.load()
    except (FileNotFoundError, OSError, UnidentifiedImageError):
        return None

    image = image.convert("RGBA")
    image.thumbnail(max_size, Image.Resampling.LANCZOS)
    return ctk.CTkImage(light_image=image, dark_image=image, size=image.size)


def cover_fit_pil_image(path: Path, display_size: tuple[int, int], top_bias: float = 0.25) -> Image.Image | None:
    """Load an image file and cover-fit it to exactly `display_size`,
    returning a raw PIL Image (RGB) rather than a CTkImage.

    Unlike `load_image_safely` (which shrinks to fit within a box, leaving
    empty space for a non-matching aspect ratio), this crops so the result
    exactly fills `display_size` with no letterboxing and no stretching —
    used for player photo cards where source photos arrive in wildly
    different resolutions/aspect ratios (square headshots, tall portraits,
    full-body shots) but must render as one consistent card size.

    The crop favors the upper portion of a source that's relatively taller
    than the target (`top_bias` < 0.5 keeps more of the top than the
    bottom), since a portrait subject's face is usually near the top —
    a plain center-crop would risk cutting it off on some source photos.

    Never raises: mirrors `load_image_safely`'s fallback-to-None behavior
    for a missing, unreadable, or corrupt file. Returning the raw PIL
    image (rather than a CTkImage) lets a caller post-process it further
    (e.g. compositing it into a larger decorative card background) before
    ever wrapping it for display — see `load_cover_fit_image` below for
    the direct-to-CTkImage convenience wrapper most callers want.
    """
    try:
        image = Image.open(path)
        image.load()
    except (FileNotFoundError, OSError, UnidentifiedImageError):
        return None

    image = image.convert("RGB")
    target_w, target_h = display_size
    src_w, src_h = image.size
    target_ratio = target_w / target_h
    src_ratio = src_w / src_h

    if src_ratio > target_ratio:
        # Source is relatively wider than the target: crop width, centered.
        crop_w = min(src_w, round(src_h * target_ratio))
        left = (src_w - crop_w) // 2
        box = (left, 0, left + crop_w, src_h)
    else:
        # Source is relatively taller than the target: crop height, biased
        # toward the top so a face near the top of a portrait survives.
        crop_h = min(src_h, round(src_w / target_ratio))
        top = max(0, min(round((src_h - crop_h) * top_bias), src_h - crop_h))
        box = (0, top, src_w, top + crop_h)

    return image.crop(box).resize(display_size, Image.Resampling.LANCZOS)


def load_cover_fit_image(path: Path, display_size: tuple[int, int], top_bias: float = 0.25) -> ctk.CTkImage | None:
    """Load an image file and cover-fit it to exactly `display_size` (see
    `cover_fit_pil_image`), wrapped as a ready-to-display `CTkImage`.
    Never raises — returns `None` for a missing, unreadable, or corrupt file.
    """
    cropped = cover_fit_pil_image(path, display_size, top_bias)
    if cropped is None:
        return None
    return ctk.CTkImage(light_image=cropped, dark_image=cropped, size=display_size)


def pill_badge(
    parent: ctk.CTkBaseClass,
    text: str,
    fg_color: str,
    text_color: str,
    width: int = 88,
    height: int = 22,
    font_size: int = 11,
) -> ctk.CTkLabel:
    """A small rounded, colored label used for status/position badges."""
    return ctk.CTkLabel(
        parent,
        text=text,
        font=theme.body_font(size=font_size, weight="bold"),
        fg_color=fg_color,
        text_color=text_color,
        corner_radius=6,
        width=width,
        height=height,
    )


def position_badge(parent: ctk.CTkBaseClass, position: Position, position_colors: dict[str, str]) -> ctk.CTkLabel:
    """A badge showing a player's position, colored per data/settings.json."""
    color_name = position_colors.get(position.value, "")
    hex_color = theme.POSITION_COLOR_HEX.get(color_name, theme.SURFACE_ALT)
    return pill_badge(parent, position.value, fg_color=hex_color, text_color=theme.BACKGROUND, width=48)


def captain_badge(parent: ctk.CTkBaseClass) -> ctk.CTkLabel:
    return pill_badge(parent, "CAPTAIN", fg_color=theme.ACCENT_GREEN, text_color=theme.BACKGROUND, width=88)


def auction_status_badge(parent: ctk.CTkBaseClass) -> ctk.CTkLabel:
    return pill_badge(parent, "AUCTION", fg_color=theme.SURFACE_ALT, text_color=theme.TEXT_SECONDARY, width=88)


def format_timestamp(iso_str: str | None) -> str:
    """A short, human-readable rendering of a stored UTC ISO timestamp —
    shared by the Live Auction session launcher/save-status indicator and
    the Settings screen's session/save diagnostics. Falls back to the raw
    string (rather than raising) for anything that doesn't parse, since
    this is display-only and must never crash a screen."""
    if not iso_str:
        return "—"
    try:
        parsed = datetime.fromisoformat(iso_str)
    except ValueError:
        return iso_str
    suffix = " UTC" if parsed.tzinfo is not None else ""
    # strftime always zero-pads day/hour; " 0" only ever precedes one of
    # those two fields (minutes are preceded by ":", not " "), so this
    # strips both leading zeros without touching the minutes field.
    return parsed.strftime("%b %d, %Y %I:%M %p").replace(" 0", " ") + suffix
