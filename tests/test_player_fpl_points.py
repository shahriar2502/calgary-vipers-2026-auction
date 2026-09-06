"""Player Cards + Last-Season FPL points (September 2026).

Covers the canonical last_season_fpl_points field: correct mapping to the
organizer's previous-season result sheet, None (never 0) for players with
no usable last-season result, and backward compatibility with Player
records/persistence snapshots saved before this field existed.
"""
import json

import pytest

from models.player import Player, Position
from services.player_service import load_players

EXPECTED_FPL_POINTS = {
    "Samin Haque": 80,
    "Rahmat Ullah": 83,
    "Sabit Khan": 47,
    "Shahriar Anwar Khan": 70,
    "K M Chisty": 55,
    "Ishmam Rahman": 65,
    "Md Rafiu Hossain": 50,
    "Hasan Mahtab": 74,
    "Minhaz Hamim": 79,
    "Nabil Shahriar": 42,
    "Adeeb Ahmed": 70,
    "Arafatul Mamur": 42,
    "Aafeef Kabir": 54,
    "Aiman Nawar Chowdhury": 57,
    "Navid Rahman": 88,
    "Rizvi Ibrahim": 111,
    "Fairooz Abir": 76,
    "Riyad Zaman": 62,
    "Faiad Rehman": 74,
    "Azmi": 64,
    "Mubasshir": 57,
    "Taqi Rahman": 37,
}

NO_LAST_SEASON_DATA = {
    "Hussain Yeasin",
    "Masrur Rahman",
    "Sajid Khalid",
    "Rayhan",
    "Munem",
    "Farhan Labib",
    "Jawad",
    "Hasnan Siddique Sunve",
    "Farhan Mahin",
    "Tahsin Islam",
}


def _by_name() -> dict[str, Player]:
    return {p.full_name: p for p in load_players()}


def test_all_32_players_have_an_fpl_field_present() -> None:
    players = load_players()
    assert len(players) == 32
    for player in players:
        assert hasattr(player, "last_season_fpl_points")


@pytest.mark.parametrize("full_name,expected_points", sorted(EXPECTED_FPL_POINTS.items()))
def test_known_fpl_value_maps_correctly(full_name: str, expected_points: int) -> None:
    assert _by_name()[full_name].last_season_fpl_points == expected_points


def test_rizvi_is_111() -> None:
    assert _by_name()["Rizvi Ibrahim"].last_season_fpl_points == 111


def test_navid_is_88() -> None:
    assert _by_name()["Navid Rahman"].last_season_fpl_points == 88


def test_rahmat_is_83() -> None:
    assert _by_name()["Rahmat Ullah"].last_season_fpl_points == 83


def test_samin_is_80() -> None:
    assert _by_name()["Samin Haque"].last_season_fpl_points == 80


def test_arik_is_70() -> None:
    assert _by_name()["Shahriar Anwar Khan"].last_season_fpl_points == 70


def test_taqi_is_37() -> None:
    assert _by_name()["Taqi Rahman"].last_season_fpl_points == 37


@pytest.mark.parametrize("full_name", sorted(NO_LAST_SEASON_DATA))
def test_missing_data_player_is_none_not_zero(full_name: str) -> None:
    points = _by_name()[full_name].last_season_fpl_points
    assert points is None
    assert points != 0


def test_all_ten_no_data_players_have_none() -> None:
    by_name = _by_name()
    for full_name in NO_LAST_SEASON_DATA:
        assert by_name[full_name].last_season_fpl_points is None


def test_every_player_is_either_explicit_or_no_data() -> None:
    by_name = _by_name()
    assert set(EXPECTED_FPL_POINTS) | NO_LAST_SEASON_DATA == set(by_name)


def test_no_fpl_value_is_zero_for_missing_players() -> None:
    """Zero is a real, distinct FPL score; missing data must be None."""
    by_name = _by_name()
    for full_name in NO_LAST_SEASON_DATA:
        assert by_name[full_name].last_season_fpl_points is not 0  # noqa: F632 - explicit identity check


# ----------------------------------------------------------------------
# Backward compatibility: legacy Player records / persistence snapshots
# ----------------------------------------------------------------------


def test_legacy_player_from_dict_without_fpl_field_works() -> None:
    legacy_dict = {
        "id": 901,
        "full_name": "Legacy Player",
        "short_name": "Legacy",
        "position": "MID",
        "overall_rating": 80,
        # no "last_season_fpl_points" key at all, as a pre-Sept-2026 save would have
    }
    player = Player.from_dict(legacy_dict)
    assert player.last_season_fpl_points is None


def test_legacy_players_json_style_round_trip() -> None:
    """Simulates an old on-disk players.json record (dict, no FPL key)
    surviving a load -> Player -> to_dict() -> Player round trip."""
    legacy_dict = {
        "id": 902,
        "full_name": "Legacy Round Trip",
        "short_name": "LRT",
        "position": "DEF",
        "overall_rating": 75,
    }
    player = Player.from_dict(legacy_dict)
    serialized = player.to_dict()
    assert serialized["last_season_fpl_points"] is None
    reloaded = Player.from_dict(serialized)
    assert reloaded.last_season_fpl_points is None
    assert reloaded == player


def test_explicit_fpl_value_round_trips_through_to_dict_from_dict() -> None:
    player = Player(id=903, full_name="FPL Star", short_name="Star", position=Position.ATT, overall_rating=85, last_season_fpl_points=111)
    reloaded = Player.from_dict(player.to_dict())
    assert reloaded.last_season_fpl_points == 111


def test_negative_fpl_points_rejected() -> None:
    with pytest.raises(ValueError, match="last_season_fpl_points cannot be negative"):
        Player(id=904, full_name="Bad FPL", short_name="Bad", position=Position.MID, overall_rating=80, last_season_fpl_points=-1)


def test_zero_fpl_points_is_accepted_as_a_real_score() -> None:
    """0 is a legitimate season score, distinct from None (no data)."""
    player = Player(id=905, full_name="Zero Score", short_name="Zero", position=Position.MID, overall_rating=80, last_season_fpl_points=0)
    assert player.last_season_fpl_points == 0
