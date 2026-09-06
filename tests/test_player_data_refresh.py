"""Locks in the September 2026 canonical player-data refresh.

Covers the "MINIMUM ASSERTIONS" list from the player-data-refresh ticket:
removed players (Aldeen/Mirza/Sarim), the relocated/rerated Munem, the three
new players (Sunve/Mahin/Tahsin), the Samin MID->ATT position change, and
every explicitly organizer-approved rating.
"""
import json
from pathlib import Path

import pytest

from models.player import Player, Position
from services.player_service import load_players, load_teams, validate_setup
from services.randomization_service import create_auction_queue

ROOT = Path(__file__).resolve().parents[1]

EXPECTED_RATINGS = {
    "Samin Haque": 87,
    "Rahmat Ullah": 88,
    "Sabit Khan": 77,
    "Shahriar Anwar Khan": 84,
    "K M Chisty": 79,
    "Hussain Yeasin": 84,
    "Ishmam Rahman": 83,
    "Md Rafiu Hossain": 78,
    "Hasan Mahtab": 85,
    "Minhaz Hamim": 87,
    "Nabil Shahriar": 76,
    "Adeeb Ahmed": 84,
    "Masrur Rahman": 88,
    "Arafatul Mamur": 76,
    "Aafeef Kabir": 79,
    "Aiman Nawar Chowdhury": 80,
    "Sajid Khalid": 75,
    "Rayhan": 84,
    "Navid Rahman": 89,
    "Rizvi Ibrahim": 90,
    "Fairooz Abir": 86,
    "Riyad Zaman": 82,
    "Faiad Rehman": 85,
    "Munem": 89,
    "Azmi": 82,
    "Mubasshir": 80,
    "Farhan Labib": 85,
    "Jawad": 87,
    "Taqi Rahman": 75,
    "Hasnan Siddique Sunve": 82,
    "Farhan Mahin": 78,
    "Tahsin Islam": 87,
}

REMOVED_PLAYER_NAMES = {"Aldeen", "Mirza", "Sarim"}

EXPECTED_CAPTAIN_TEAMS = {
    "Samin Haque": "Blackout FC",
    "Sabit Khan": "Darkstar FC",
    "Arafatul Mamur": "Goli Underdogs",
    "Riyad Zaman": "Showstoppers",
}

EXPECTED_GK_NAMES = {"Nabil Shahriar", "Masrur Rahman", "Rayhan", "Jawad"}


def _load_raw_players() -> list[dict]:
    return json.loads((ROOT / "data" / "players.json").read_text(encoding="utf-8"))


def _players() -> list[Player]:
    return [Player.from_dict(item) for item in _load_raw_players()]


def _by_name(players: list[Player]) -> dict[str, Player]:
    return {player.full_name: player for player in players}


def test_exactly_32_total_players() -> None:
    assert len(_players()) == 32


def test_exactly_4_captains() -> None:
    assert sum(player.is_captain for player in _players()) == 4


def test_exactly_28_auction_players() -> None:
    assert sum(player.auction_eligible for player in _players()) == 28


def test_player_ids_exactly_1_to_32() -> None:
    ids = sorted(player.id for player in _players())
    assert ids == list(range(1, 33))


def test_player_ids_unique() -> None:
    players = _players()
    assert len({player.id for player in players}) == len(players)


def test_full_names_unique() -> None:
    players = _players()
    assert len({player.full_name for player in players}) == len(players)


def test_short_names_unique() -> None:
    players = _players()
    assert len({player.short_name for player in players}) == len(players)


@pytest.mark.parametrize("removed_name", sorted(REMOVED_PLAYER_NAMES))
def test_removed_player_is_absent(removed_name: str) -> None:
    names = {player.full_name for player in _players()}
    assert removed_name not in names


def test_munem_present_exactly_once() -> None:
    matches = [player for player in _players() if player.full_name == "Munem"]
    assert len(matches) == 1


def test_munem_id_position_and_rating() -> None:
    munem = _by_name(_players())["Munem"]
    assert munem.id == 24
    assert munem.position == Position.MID
    assert munem.overall_rating == 89


def test_sunve_id_position_and_rating() -> None:
    sunve = _by_name(_players())["Hasnan Siddique Sunve"]
    assert sunve.id == 30
    assert sunve.position == Position.DEF
    assert sunve.overall_rating == 82


def test_mahin_id_position_and_rating() -> None:
    mahin = _by_name(_players())["Farhan Mahin"]
    assert mahin.id == 31
    assert mahin.position == Position.ATT
    assert mahin.overall_rating == 78


def test_tahsin_id_position_and_rating() -> None:
    tahsin = _by_name(_players())["Tahsin Islam"]
    assert tahsin.id == 32
    assert tahsin.position == Position.ATT
    assert tahsin.overall_rating == 87


def test_samin_is_now_attacker_rated_87() -> None:
    samin = _by_name(_players())["Samin Haque"]
    assert samin.position == Position.ATT
    assert samin.overall_rating == 87


def test_rizvi_navid_rahmat_masrur_headline_ratings() -> None:
    by_name = _by_name(_players())
    assert by_name["Rizvi Ibrahim"].overall_rating == 90
    assert by_name["Navid Rahman"].overall_rating == 89
    assert by_name["Rahmat Ullah"].overall_rating == 88
    assert by_name["Masrur Rahman"].overall_rating == 88


def test_sajid_khalid_is_midfielder_rated_75() -> None:
    sajid = _by_name(_players())["Sajid Khalid"]
    assert sajid.position == Position.MID
    assert sajid.overall_rating == 75


def test_goalkeeper_positions_and_ratings() -> None:
    by_name = _by_name(_players())
    assert by_name["Nabil Shahriar"].position == Position.GK
    assert by_name["Nabil Shahriar"].overall_rating == 76
    assert by_name["Masrur Rahman"].position == Position.GK
    assert by_name["Masrur Rahman"].overall_rating == 88
    assert by_name["Rayhan"].position == Position.GK
    assert by_name["Rayhan"].overall_rating == 84
    assert by_name["Jawad"].position == Position.GK
    assert by_name["Jawad"].overall_rating == 87


def test_exactly_4_goalkeepers() -> None:
    players = _players()
    gk_names = {player.full_name for player in players if player.position == Position.GK}
    assert gk_names == EXPECTED_GK_NAMES
    assert len(gk_names) == 4


def test_captain_mappings_correct() -> None:
    captains = {player.full_name: player.assigned_team for player in _players() if player.is_captain}
    assert captains == EXPECTED_CAPTAIN_TEAMS


def test_captains_not_auction_eligible() -> None:
    assert all(not player.auction_eligible for player in _players() if player.is_captain)


def test_all_non_captains_auction_eligible() -> None:
    assert all(player.auction_eligible for player in _players() if not player.is_captain)


def test_random_queue_contains_28_players() -> None:
    players = _players()
    queue = create_auction_queue(players, seed=1)
    assert len(queue) == 28
    assert len(set(queue)) == 28


def test_no_captain_enters_queue() -> None:
    players = _players()
    queue = create_auction_queue(players, seed=1)
    captain_ids = {player.id for player in players if player.is_captain}
    assert not (captain_ids & set(queue))


def test_gk_coverage_setup_check_passes() -> None:
    result = validate_setup(load_players(), load_teams())
    labels = {check.label: check.passed for check in result.checks}
    assert labels["Sufficient goalkeeper coverage"] is True


def test_teams_initialize_correctly() -> None:
    teams = load_teams()
    assert len(teams) == 4
    for team in teams:
        assert team.starting_budget == 100
        assert team.remaining_budget == 100
        assert team.max_squad_size == 8
        assert len(team.roster) == 1


def test_calgary_vipers_is_not_an_auction_team() -> None:
    teams = load_teams()
    assert all(team.name != "Calgary Vipers" for team in teams)


@pytest.mark.parametrize("full_name,expected_rating", sorted(EXPECTED_RATINGS.items()))
def test_every_explicit_rating_is_exactly_correct(full_name: str, expected_rating: int) -> None:
    player = _by_name(_players())[full_name]
    assert player.overall_rating == expected_rating


def test_full_setup_reports_ready() -> None:
    result = validate_setup(load_players(), load_teams())
    assert result.is_ready is True
    assert result.failures == []
