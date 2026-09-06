import pytest

from models.player import Position
from services.player_service import (
    filter_players,
    load_players,
    load_teams,
    player_status_label,
    validate_setup,
)

CAPTAIN_TEAMS = {
    "Samin Haque": "Blackout FC",
    "Sabit Khan": "Darkstar FC",
    "Arafatul Mamur": "Goli Underdogs",
    "Riyad Zaman": "Showstoppers",
}


def test_load_players_returns_32_players() -> None:
    assert len(load_players()) == 32


def test_load_players_captain_count() -> None:
    assert sum(player.is_captain for player in load_players()) == 4


def test_load_players_auction_eligible_count() -> None:
    assert sum(player.auction_eligible for player in load_players()) == 28


def test_no_captain_is_auction_eligible() -> None:
    assert all(not player.auction_eligible for player in load_players() if player.is_captain)


def test_captain_assignments_are_correct() -> None:
    captains = {player.full_name: player.assigned_team for player in load_players() if player.is_captain}
    assert captains == CAPTAIN_TEAMS


def test_player_ids_are_unique() -> None:
    ids = [player.id for player in load_players()]
    assert len(ids) == len(set(ids))


def test_canonical_ordering_preserved() -> None:
    ids = [player.id for player in load_players()]
    assert ids == list(range(1, 33))


def test_player_status_label() -> None:
    players = {player.full_name: player for player in load_players()}
    assert player_status_label(players["Samin Haque"]) == "CAPTAIN"
    assert player_status_label(players["Rahmat Ullah"]) == "AUCTION"


def test_full_name_search() -> None:
    results = filter_players(load_players(), search="Shahriar Anwar Khan")
    assert [player.full_name for player in results] == ["Shahriar Anwar Khan"]


def test_short_name_search() -> None:
    results = filter_players(load_players(), search="arik")
    assert [player.short_name for player in results] == ["Arik"]


def test_search_is_case_insensitive() -> None:
    lower = filter_players(load_players(), search="rizvi")
    upper = filter_players(load_players(), search="RIZVI")
    assert lower == upper
    assert [player.full_name for player in lower] == ["Rizvi Ibrahim"]


def test_partial_search_matches_substrings() -> None:
    results = filter_players(load_players(), search="rah")
    names = {player.full_name for player in results}
    assert "Rahmat Ullah" in names
    assert "Ishmam Rahman" in names
    assert "Navid Rahman" in names
    assert "Taqi Rahman" in names


@pytest.mark.parametrize(
    "position",
    [Position.GK, Position.DEF, Position.MID, Position.ATT],
)
def test_position_filter(position: Position) -> None:
    players = load_players()
    results = filter_players(players, position=position)
    assert results
    assert all(player.position == position for player in results)
    expected_count = sum(1 for player in players if player.position == position)
    assert len(results) == expected_count


def test_position_filter_all_returns_everyone() -> None:
    players = load_players()
    assert filter_players(players, position="All") == players


def test_search_and_position_filter_combine() -> None:
    players = load_players()
    results = filter_players(players, search="rah", position=Position.GK)
    assert results
    assert all(player.position == Position.GK for player in results)
    assert all("rah" in player.full_name.lower() or "rah" in player.short_name.lower() for player in results)


def test_status_filter_captains_and_auction() -> None:
    players = load_players()
    captains = filter_players(players, status="CAPTAIN")
    auction = filter_players(players, status="AUCTION")
    assert len(captains) == 4
    assert len(auction) == 28
    assert all(player.is_captain for player in captains)
    assert all(not player.is_captain for player in auction)


def test_clearing_search_and_filters_restores_all_32_in_order() -> None:
    players = load_players()
    restored = filter_players(players, search="", position="All", status="All Players")
    assert restored == players
    assert len(restored) == 32


def test_filtering_preserves_canonical_order() -> None:
    players = load_players()
    results = filter_players(players, position=Position.MID)
    result_ids = [player.id for player in results]
    assert result_ids == sorted(result_ids)


def test_validate_setup_is_ready_for_canonical_data() -> None:
    result = validate_setup(load_players(), load_teams())
    assert result.is_ready is True
    assert result.failures == []


def test_validate_setup_reports_wrong_total_player_count() -> None:
    players = load_players()[:-1]
    result = validate_setup(players, load_teams())
    assert result.is_ready is False
    failed_labels = {check.label for check in result.failures}
    assert "Total players" in failed_labels


def test_validate_setup_reports_captain_marked_eligible() -> None:
    players = load_players()
    captain = next(player for player in players if player.full_name == "Samin Haque")
    captain.auction_eligible = True  # simulate corrupted in-memory state
    result = validate_setup(players, load_teams())
    assert result.is_ready is False
    failed_labels = {check.label for check in result.failures}
    assert "No captain is auction-eligible" in failed_labels


def test_validate_setup_reports_broken_captain_link() -> None:
    teams = load_teams()
    teams[0].captain_name = "Someone Else"
    result = validate_setup(load_players(), teams)
    assert result.is_ready is False
    failed_labels = {check.label for check in result.failures}
    assert "Captain assignments are valid" in failed_labels
