"""Shared premium Calgary Vipers player-card visual system.

One PIL-composited "card art" implementation (decorative green/red
diagonal background + a framed real player photo, or a placeholder panel)
reused by every screen that shows a player as a sports-card-style tile —
today that's the Player Cards gallery (`ui/screens/player_cards_screen.py`)
and the Live Auction current-player reveal
(`ui/screens/live_auction_screen.py`). Each caller supplies its own size
and photo-offset (a gallery tile and a large projector-readable Live
Auction card are different variants of one visual language, not the same
fixed layout) and still places its own real `CTkLabel`/badge widgets for
text on top — this module only ever produces the backdrop image.

Deliberately UI-layer, not a service: this holds no auction/business logic
and only reads `Player` fields that already exist (photo_path, id,
is_captain) — the same "pure presentation, reused rather than duplicated"
role `ui/widgets.py` and `ui/theme.py` already play.
"""

from __future__ import annotations

from PIL import Image, ImageDraw

from models.player import Player
from services.config_service import ROOT_DIR
from ui import theme
from ui.widgets import cover_fit_pil_image

_LOGO_PATH = ROOT_DIR / "assets" / "branding" / "calgary_vipers_logo.png"

# Two alternating accent themes (chosen deterministically per player, see
# `card_variant`) so a set of cards reads as lively rather than one
# repeated pattern — never tied to team/position data, purely decorative.
VARIANT_COLORS = [
    (theme.ACCENT_GREEN, theme.UNSOLD_RED),
    (theme.UNSOLD_RED, theme.ACCENT_GREEN),
]

# The player-independent part of the art (gradient, diagonal sweep, gold
# captain stripe, watermark) is cached per (width, height, corner_radius,
# variant, is_captain) — a small, finite set of combinations across every
# screen that uses this module — so repeatedly showing/re-showing players
# (a 32-card gallery grid, or Live Auction advancing through 28 reveals)
# regenerates each distinct background at most once, not on every call.
_BACKGROUND_CACHE: dict[tuple[int, int, int, int, bool], Image.Image] = {}


def hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    hex_color = hex_color.lstrip("#")
    return int(hex_color[0:2], 16), int(hex_color[2:4], 16), int(hex_color[4:6], 16)


def card_variant(player: Player) -> int:
    return player.id % 2


def fpl_display(points: int | None) -> str:
    """`None` (no usable last-season result) renders as N/A, never 0 — a
    real, distinct score. See models/player.py's `last_season_fpl_points`."""
    return "N/A" if points is None else str(points)


def build_card_background(
    width: int, height: int, variant: int, is_captain: bool, corner_radius: int = 20
) -> Image.Image:
    """The decorative Vipers-branded backdrop only (no photo) — a fresh
    copy of a cached image, safe for the caller to further composite onto
    without mutating the cached original."""
    cache_key = (width, height, corner_radius, variant, is_captain)
    cached = _BACKGROUND_CACHE.get(cache_key)
    if cached is not None:
        return cached.copy()

    primary_hex, secondary_hex = VARIANT_COLORS[variant]
    primary = hex_to_rgb(primary_hex)
    secondary = hex_to_rgb(secondary_hex)
    gold = hex_to_rgb(theme.GOLD_ACCENT)

    base = Image.new("RGBA", (width, height), (*hex_to_rgb(theme.SURFACE), 255))

    # A soft top-to-mid vertical fade (slightly lighter at the very top)
    # for a sense of depth behind the diagonal sweep, without touching the
    # flatter lower half where text will sit.
    fade_height = int(height * 0.55)
    depth = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    depth_draw = ImageDraw.Draw(depth)
    for y in range(fade_height):
        alpha = max(0, 34 - int(y * 34 / fade_height))
        if alpha:
            depth_draw.line([(0, y), (width, y)], fill=(255, 255, 255, alpha))
    base = Image.alpha_composite(base, depth)

    # The two-tone diagonal "sweep" motif: a primary-color band from the
    # top-left, a smaller secondary-color band from the bottom-right —
    # proportional to width/height, so it scales cleanly to any card size.
    accents = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    adraw = ImageDraw.Draw(accents)
    adraw.polygon(
        [(0, 0), (width * 0.78, 0), (width * 0.30, height * 0.34), (0, height * 0.34)],
        fill=(*primary, 58),
    )
    adraw.polygon(
        [(width, height * 0.62), (width, height), (width * 0.55, height), (width * 0.80, height * 0.62)],
        fill=(*secondary, 42),
    )
    base = Image.alpha_composite(base, accents)

    if is_captain:
        stripe_height = max(6, int(height * 0.02))
        gold_wash = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        ImageDraw.Draw(gold_wash).rectangle([0, 0, width, stripe_height], fill=(*gold, 200))
        base = Image.alpha_composite(base, gold_wash)

    # A faint branding watermark tucked into the bottom-right corner —
    # small and low-opacity enough to sit behind text without competing
    # with it; a missing/unreadable logo file never blocks a card from
    # rendering (same never-crash philosophy as every image helper here).
    try:
        logo = Image.open(_LOGO_PATH).convert("RGBA")
        logo.thumbnail((int(width * 0.42), int(width * 0.42)))
        faded_alpha = logo.split()[3].point(lambda a: int(a * 0.06))
        logo.putalpha(faded_alpha)
        base.alpha_composite(logo, (width - logo.width + 14, height - logo.height + 14))
    except (FileNotFoundError, OSError):
        pass

    # Round the whole card art's corners to match the outer CTkFrame's
    # `corner_radius`, so the image reads as filling a rounded card rather
    # than a rectangle poking out past rounded edges.
    mask = Image.new("L", (width, height), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, width - 1, height - 1], radius=corner_radius, fill=255)
    rounded = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    rounded.paste(base, (0, 0), mask)

    _BACKGROUND_CACHE[cache_key] = rounded
    return rounded.copy()


def frame_photo(
    photo: Image.Image, accent_rgb: tuple[int, int, int], corner_radius: int = 14, border_width: int = 3
) -> Image.Image:
    """Round a cover-fit player photo's corners and add a colored border
    ring, so it reads as a deliberately framed panel rather than a pasted
    rectangle."""
    w, h = photo.size
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, w - 1, h - 1], radius=corner_radius, fill=255)

    framed = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    framed.paste(photo.convert("RGBA"), (0, 0), mask)
    ImageDraw.Draw(framed).rounded_rectangle(
        [1, 1, w - 2, h - 2], radius=corner_radius, outline=(*accent_rgb, 230), width=border_width
    )
    return framed


def photo_placeholder_panel(
    size: tuple[int, int], accent_rgb: tuple[int, int, int], corner_radius: int = 14, border_width: int = 3
) -> Image.Image:
    """The photo-shaped panel drawn when a player has no loadable photo —
    same rounded/bordered frame as a real photo, just filled with the
    app's existing placeholder tone. Initials text is added separately by
    the caller as a CTkLabel, not baked in here."""
    w, h = size
    panel = Image.new("RGBA", (w, h), (*hex_to_rgb(theme.SURFACE_ALT), 255))
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, w - 1, h - 1], radius=corner_radius, fill=255)
    rounded = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    rounded.paste(panel, (0, 0), mask)
    ImageDraw.Draw(rounded).rounded_rectangle(
        [1, 1, w - 2, h - 2], radius=corner_radius, outline=(*accent_rgb, 230), width=border_width
    )
    return rounded


def build_player_card_image(
    player: Player,
    background_size: tuple[int, int],
    photo_size: tuple[int, int],
    photo_offset: tuple[int, int],
    corner_radius: int = 20,
    photo_corner_radius: int = 14,
    photo_border_width: int = 3,
) -> tuple[Image.Image, bool]:
    """The full composited card art for one player at one caller-chosen
    size: a cached decorative background plus this player's own framed
    photo (or placeholder panel) pasted at `photo_offset`.

    Returns (image, has_real_photo) — the caller decides whether to also
    draw an initials label and whether to count this card toward any
    "real photo loaded" bookkeeping.
    """
    variant = card_variant(player)
    primary_rgb = hex_to_rgb(VARIANT_COLORS[variant][0])

    art = build_card_background(background_size[0], background_size[1], variant, player.is_captain, corner_radius)
    pil_photo = cover_fit_pil_image(ROOT_DIR / player.photo_path, photo_size) if player.photo_path else None

    if pil_photo is not None:
        panel = frame_photo(pil_photo, primary_rgb, photo_corner_radius, photo_border_width)
    else:
        panel = photo_placeholder_panel(photo_size, primary_rgb, photo_corner_radius, photo_border_width)
    art.alpha_composite(panel, photo_offset)

    return art, pil_photo is not None
