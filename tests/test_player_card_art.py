"""Tests for ui/player_card_art.py: the shared premium Calgary Vipers
card-art system reused by both the Player Cards gallery and the Live
Auction current-player card. Pure PIL/logic — no GUI needed.
"""

from models.player import Player, PlayerAuctionStatus, Position
from ui import player_card_art


def _player(id=1, name="Test Player", fpl=None, captain=False, photo_path=None):
    return Player(
        id=id,
        full_name=name,
        short_name=name[:4],
        position=Position.MID,
        overall_rating=80,
        last_season_fpl_points=fpl,
        is_captain=captain,
        assigned_team="Blackout FC" if captain else None,
        auction_eligible=not captain,
        auction_status=PlayerAuctionStatus.PRE_ASSIGNED if captain else PlayerAuctionStatus.AVAILABLE,
        photo_path=photo_path,
    )


def test_hex_to_rgb() -> None:
    assert player_card_art.hex_to_rgb("#28c76f") == (0x28, 0xC7, 0x6F)


def test_card_variant_is_deterministic_and_binary() -> None:
    even_player = _player(id=2)
    odd_player = _player(id=3)
    assert player_card_art.card_variant(even_player) == 0
    assert player_card_art.card_variant(odd_player) == 1
    assert player_card_art.card_variant(even_player) == player_card_art.card_variant(even_player)


def test_fpl_display_none_is_na() -> None:
    assert player_card_art.fpl_display(None) == "N/A"


def test_fpl_display_real_value() -> None:
    assert player_card_art.fpl_display(70) == "70"


def test_fpl_display_real_zero_is_not_na() -> None:
    """0 is a real, distinct score — never conflated with None/N/A."""
    assert player_card_art.fpl_display(0) == "0"


def test_build_card_background_returns_requested_size() -> None:
    image = player_card_art.build_card_background(300, 460, variant=0, is_captain=False)
    assert image.size == (300, 460)


def test_build_card_background_is_cached_per_key() -> None:
    first = player_card_art.build_card_background(200, 300, variant=0, is_captain=False)
    second = player_card_art.build_card_background(200, 300, variant=0, is_captain=False)
    # Different objects (each call returns a fresh copy so callers can
    # safely mutate/composite onto it), but pixel-identical content.
    assert first is not second
    assert list(first.getdata()) == list(second.getdata())


def test_build_card_background_differs_by_size() -> None:
    small = player_card_art.build_card_background(150, 200, variant=0, is_captain=False)
    large = player_card_art.build_card_background(300, 460, variant=0, is_captain=False)
    assert small.size != large.size


def test_build_card_background_differs_by_variant() -> None:
    variant_0 = player_card_art.build_card_background(200, 300, variant=0, is_captain=False)
    variant_1 = player_card_art.build_card_background(200, 300, variant=1, is_captain=False)
    assert list(variant_0.getdata()) != list(variant_1.getdata())


def test_build_card_background_differs_for_captain() -> None:
    normal = player_card_art.build_card_background(200, 300, variant=0, is_captain=False)
    captain = player_card_art.build_card_background(200, 300, variant=0, is_captain=True)
    assert list(normal.getdata()) != list(captain.getdata())


def test_frame_photo_preserves_size() -> None:
    from PIL import Image

    photo = Image.new("RGB", (100, 120), (255, 0, 0))
    framed = player_card_art.frame_photo(photo, (40, 200, 80))
    assert framed.size == (100, 120)


def test_photo_placeholder_panel_returns_requested_size() -> None:
    panel = player_card_art.photo_placeholder_panel((100, 120), (40, 200, 80))
    assert panel.size == (100, 120)


def test_build_player_card_image_with_missing_photo_path() -> None:
    player = _player(photo_path=None)
    image, has_photo = player_card_art.build_player_card_image(
        player, background_size=(200, 300), photo_size=(160, 180), photo_offset=(10, 10)
    )
    assert has_photo is False
    assert image.size == (200, 300)


def test_build_player_card_image_with_missing_photo_file() -> None:
    player = _player(photo_path="assets/players/does_not_exist.jpg")
    image, has_photo = player_card_art.build_player_card_image(
        player, background_size=(200, 300), photo_size=(160, 180), photo_offset=(10, 10)
    )
    assert has_photo is False
    assert image.size == (200, 300)


def test_build_player_card_image_with_real_photo() -> None:
    # Every canonical player has a real photo asset (September 2026 photo
    # integration) — Rizvi Ibrahim (id 20) is a stable, always-present one.
    player = _player(id=20, name="Rizvi Ibrahim", photo_path="assets/players/rizvi_ibrahim.jpg")
    image, has_photo = player_card_art.build_player_card_image(
        player, background_size=(200, 300), photo_size=(160, 180), photo_offset=(10, 10)
    )
    assert has_photo is True
    assert image.size == (200, 300)


def test_variant_colors_are_two_alternating_pairs() -> None:
    assert len(player_card_art.VARIANT_COLORS) == 2
    assert player_card_art.VARIANT_COLORS[0] != player_card_art.VARIANT_COLORS[1]
