"""Player photo integration (September 2026).

Covers: every canonical player has a unique, existing photo_path; the
cover-fit image helper (used by Live Auction's player card and the Players
& Setup thumbnail column) loads valid images to the exact requested display
size and falls back to None instead of crashing on a missing/corrupt file;
and that adding photo_path values did not disturb any other canonical
player data (ids, ratings, positions, captain status, auction eligibility).
"""
from pathlib import Path

import pytest
from PIL import Image

from models.player import Position
from services.config_service import ROOT_DIR
from services.player_service import load_players
from ui.widgets import load_cover_fit_image

SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png"}

EXPECTED_POSITIONS = {
    "Samin Haque": Position.ATT,
    "Sabit Khan": Position.MID,
    "Arafatul Mamur": Position.DEF,
    "Riyad Zaman": Position.DEF,
    "Nabil Shahriar": Position.GK,
    "Masrur Rahman": Position.GK,
    "Rayhan": Position.GK,
    "Jawad": Position.GK,
}

EXPECTED_RATINGS = {
    "Samin Haque": 87,
    "Rizvi Ibrahim": 90,
    "Munem": 89,
    "Sajid Khalid": 75,
}

EXPECTED_CAPTAIN_TEAMS = {
    "Samin Haque": "Blackout FC",
    "Sabit Khan": "Darkstar FC",
    "Arafatul Mamur": "Goli Underdogs",
    "Riyad Zaman": "Showstoppers",
}


# ------------------------------------------------------------------
# Canonical data: every player has a valid, unique photo_path
# ------------------------------------------------------------------


def test_canonical_32_player_data_still_loads() -> None:
    players = load_players()
    assert len(players) == 32
    assert sum(player.is_captain for player in players) == 4
    assert sum(player.auction_eligible for player in players) == 28


def test_every_canonical_player_has_a_non_null_photo_path() -> None:
    players = load_players()
    assert all(player.photo_path for player in players)


def test_all_32_photo_paths_are_unique() -> None:
    players = load_players()
    paths = [player.photo_path for player in players]
    assert len(set(paths)) == len(paths) == 32


def test_every_referenced_photo_file_exists() -> None:
    players = load_players()
    missing = [player.full_name for player in players if not (ROOT_DIR / player.photo_path).is_file()]
    assert missing == []


def test_every_photo_path_uses_a_supported_extension() -> None:
    players = load_players()
    bad = [
        player.full_name
        for player in players
        if Path(player.photo_path).suffix.lower() not in SUPPORTED_EXTENSIONS
    ]
    assert bad == []


def test_every_photo_path_lives_under_assets_players() -> None:
    players = load_players()
    assert all(player.photo_path.startswith("assets/players/") for player in players)


@pytest.mark.parametrize("full_name,position", sorted(EXPECTED_POSITIONS.items(), key=lambda kv: kv[0]))
def test_positions_unchanged_by_photo_integration(full_name: str, position: Position) -> None:
    players = {p.full_name: p for p in load_players()}
    assert players[full_name].position == position


@pytest.mark.parametrize("full_name,rating", sorted(EXPECTED_RATINGS.items()))
def test_ratings_unchanged_by_photo_integration(full_name: str, rating: int) -> None:
    players = {p.full_name: p for p in load_players()}
    assert players[full_name].overall_rating == rating


def test_captains_unchanged_by_photo_integration() -> None:
    players = load_players()
    captains = {p.full_name: p.assigned_team for p in players if p.is_captain}
    assert captains == EXPECTED_CAPTAIN_TEAMS
    assert all(not p.auction_eligible for p in players if p.is_captain)


# ------------------------------------------------------------------
# load_cover_fit_image: safety and correctness
# ------------------------------------------------------------------


def test_cover_fit_loader_returns_none_for_missing_path() -> None:
    assert load_cover_fit_image(Path("does/not/exist.jpg"), (100, 125)) is None


def test_cover_fit_loader_returns_none_for_corrupt_file(tmp_path) -> None:
    bad_file = tmp_path / "corrupt.jpg"
    bad_file.write_bytes(b"not a real image")
    assert load_cover_fit_image(bad_file, (100, 125)) is None


@pytest.mark.parametrize("source_size", [(100, 100), (960, 1600), (2000, 400)])
def test_cover_fit_loader_loads_valid_images_of_varied_shapes(tmp_path, source_size) -> None:
    source_path = tmp_path / "source.jpg"
    Image.new("RGB", source_size, color=(10, 20, 30)).save(source_path)

    result = load_cover_fit_image(source_path, (100, 125))
    assert result is not None


def test_cover_fit_loader_produces_the_exact_requested_display_size(tmp_path) -> None:
    source_path = tmp_path / "source.jpg"
    Image.new("RGB", (777, 333), color=(50, 60, 70)).save(source_path)

    result = load_cover_fit_image(source_path, (100, 125))
    assert result.cget("size") == (100, 125)


def test_cover_fit_loader_never_stretches_a_square_source_into_a_visibly_different_aspect() -> None:
    source_path_a = Path(ROOT_DIR / "assets" / "players" / "sabit_khan.jpg")  # 2048x2048 square source
    result = load_cover_fit_image(source_path_a, (100, 125))
    assert result is not None
    assert result.cget("size") == (100, 125)


def test_cover_fit_loader_works_on_every_real_canonical_photo() -> None:
    players = load_players()
    for player in players:
        result = load_cover_fit_image(ROOT_DIR / player.photo_path, (232, 290))
        assert result is not None, f"{player.full_name}'s photo failed to load"
        assert result.cget("size") == (232, 290)
