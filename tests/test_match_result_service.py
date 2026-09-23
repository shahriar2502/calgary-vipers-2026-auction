"""tests/test_match_result_service.py — services/match_result_service.py:
validation, add/update/delete, and the derived budget ledger. Numbered to
match the ticket's own "TESTING — BUDGET TRACKER" list (16-27) plus the
service-level validation cases from "TESTING — MATCH RESULTS".
"""
import pytest

from models.match_result import MatchResult
from models.team import Team
from services.match_result_service import (
    MatchResultError,
    add_match_result,
    build_all_ledgers,
    build_team_ledger,
    delete_match_result,
    generate_match_id,
    update_match_result,
    validate_match_input,
)


def _team(team_id: int, name: str, remaining_budget: int) -> Team:
    return Team(
        id=team_id, name=name, short_name=name[:3], captain_player_id=team_id, captain_name=f"Cap{team_id}",
        remaining_budget=remaining_budget, auction_spending=100 - remaining_budget,
    )


def _teams() -> list[Team]:
    return [
        _team(1, "Blackout FC", 18),
        _team(2, "Darkstar FC", 5),
        _team(3, "Goli Underdogs", 30),
        _team(4, "Showstoppers", 0),
    ]


# ============================================================
# VALIDATION (service-level "invalid team rejected")
# ============================================================


def test_invalid_team_rejected() -> None:
    teams = _teams()
    with pytest.raises(MatchResultError, match="Team 1 is not"):
        validate_match_input(999, 2, 3, 1, teams)
    with pytest.raises(MatchResultError, match="Team 2 is not"):
        validate_match_input(1, 999, 3, 1, teams)


def test_same_team_fixture_rejected_at_service_layer() -> None:
    teams = _teams()
    with pytest.raises(MatchResultError, match="must be different teams"):
        validate_match_input(1, 1, 3, 1, teams)


def test_blank_and_negative_and_decimal_scores_rejected_at_service_layer() -> None:
    teams = _teams()
    with pytest.raises(MatchResultError, match="cannot be blank"):
        validate_match_input(1, 2, None, 1, teams)
    with pytest.raises(MatchResultError, match="cannot be negative"):
        validate_match_input(1, 2, -1, 1, teams)
    with pytest.raises(MatchResultError, match="whole number"):
        validate_match_input(1, 2, 2.5, 1, teams)


# ============================================================
# unique match IDs / repeat fixtures / add-update-delete
# ============================================================


def test_generate_match_id_produces_unique_ids() -> None:
    ids = {generate_match_id() for _ in range(50)}
    assert len(ids) == 50


def test_add_match_result_appends_and_returns_the_new_record() -> None:
    teams = _teams()
    results: list[MatchResult] = []
    result = add_match_result(results, teams, 1, 1, 2, 3, 1)
    assert results == [result]
    assert result.team1_goals == 3 and result.team2_goals == 1


def test_repeat_fixture_creates_a_second_distinct_record() -> None:
    teams = _teams()
    results: list[MatchResult] = []
    first = add_match_result(results, teams, 1, 1, 2, 3, 1)
    second = add_match_result(results, teams, 2, 1, 2, 0, 0)
    assert len(results) == 2
    assert first.id != second.id


def test_update_match_result_replaces_score_without_duplicating() -> None:
    teams = _teams()
    results: list[MatchResult] = []
    original = add_match_result(results, teams, 1, 1, 2, 3, 1)
    updated = update_match_result(results, teams, original.id, 1, 1, 2, 1, 1)
    assert len(results) == 1
    assert results[0] is updated
    assert updated.id == original.id
    assert updated.team1_goals == 1 and updated.team2_goals == 1


def test_update_nonexistent_match_raises() -> None:
    teams = _teams()
    with pytest.raises(MatchResultError, match="no longer exists"):
        update_match_result([], teams, "does-not-exist", 1, 1, 2, 1, 1)


def test_delete_match_result_removes_it() -> None:
    teams = _teams()
    results: list[MatchResult] = []
    match = add_match_result(results, teams, 1, 1, 2, 3, 1)
    delete_match_result(results, match.id)
    assert results == []


def test_delete_nonexistent_match_raises() -> None:
    with pytest.raises(MatchResultError, match="no longer exists"):
        delete_match_result([], "does-not-exist")


# ============================================================
# TESTING — BUDGET TRACKER (16-27)
# ============================================================


def test_16_actual_auction_remainder_used_as_baseline() -> None:
    blackout = _team(1, "Blackout FC", 18)
    ledger = build_team_ledger(blackout, [])
    assert ledger.first_auction_remaining == 18


def test_17_baseline_is_never_reset_to_100m() -> None:
    showstoppers = _team(4, "Showstoppers", 0)
    ledger = build_team_ledger(showstoppers, [])
    assert ledger.first_auction_remaining == 0
    assert ledger.first_auction_remaining != 100


def test_18_zero_remaining_budget_supported() -> None:
    showstoppers = _team(4, "Showstoppers", 0)
    match = MatchResult(
        id="m1", match_number=1, team1_id=4, team2_id=1, team1_goals=2, team2_goals=0,
        created_at="2026-01-01T00:00:00+00:00",
    )
    ledger = build_team_ledger(showstoppers, [match])
    assert ledger.first_auction_remaining == 0
    assert ledger.current_transfer_budget == 4  # win award only


def test_19_match_earnings_accumulated_correctly() -> None:
    blackout = _team(1, "Blackout FC", 18)
    matches = [
        MatchResult(id="m1", match_number=1, team1_id=1, team2_id=2, team1_goals=3, team2_goals=1, created_at="2026-01-01T00:00:00+00:00"),  # win +4
        MatchResult(id="m2", match_number=2, team1_id=1, team2_id=3, team1_goals=2, team2_goals=2, created_at="2026-01-02T00:00:00+00:00"),  # draw +2
        MatchResult(id="m3", match_number=3, team1_id=4, team2_id=1, team1_goals=3, team2_goals=1, created_at="2026-01-03T00:00:00+00:00"),  # loss +1
    ]
    ledger = build_team_ledger(blackout, matches)
    assert ledger.total_match_earnings == 7
    assert ledger.matches_played == 3
    assert ledger.wins == 1 and ledger.draws == 1 and ledger.losses == 1


def test_20_current_balance_calculated_correctly() -> None:
    blackout = _team(1, "Blackout FC", 18)
    matches = [
        MatchResult(id="m1", match_number=1, team1_id=1, team2_id=2, team1_goals=3, team2_goals=1, created_at="2026-01-01T00:00:00+00:00"),
        MatchResult(id="m2", match_number=2, team1_id=1, team2_id=3, team1_goals=2, team2_goals=2, created_at="2026-01-02T00:00:00+00:00"),
        MatchResult(id="m3", match_number=3, team1_id=4, team2_id=1, team1_goals=3, team2_goals=1, created_at="2026-01-03T00:00:00+00:00"),
    ]
    ledger = build_team_ledger(blackout, matches)
    assert ledger.current_transfer_budget == 18 + 7 == 25


def test_21_editing_result_recalculates_awards() -> None:
    teams = _teams()
    results: list[MatchResult] = []
    match = add_match_result(results, teams, 1, 1, 2, 3, 1)  # Blackout win +4, Darkstar loss +1
    blackout_before = build_team_ledger(teams[0], results).total_match_earnings
    assert blackout_before == 4

    update_match_result(results, teams, match.id, 1, 1, 2, 1, 1)  # now a draw +2/+2
    blackout_after = build_team_ledger(teams[0], results).total_match_earnings
    darkstar_after = build_team_ledger(teams[1], results).total_match_earnings
    assert blackout_after == 2
    assert darkstar_after == 2


def test_22_editing_participating_teams_reallocates_awards_correctly() -> None:
    teams = _teams()
    results: list[MatchResult] = []
    match = add_match_result(results, teams, 1, 1, 2, 3, 1)  # Blackout vs Darkstar

    # Correct a data-entry mistake: it was actually Goli vs Showstoppers.
    update_match_result(results, teams, match.id, 1, 3, 4, 3, 1)

    blackout_earnings = build_team_ledger(teams[0], results).total_match_earnings
    darkstar_earnings = build_team_ledger(teams[1], results).total_match_earnings
    goli_earnings = build_team_ledger(teams[2], results).total_match_earnings
    showstoppers_earnings = build_team_ledger(teams[3], results).total_match_earnings
    assert blackout_earnings == 0
    assert darkstar_earnings == 0
    assert goli_earnings == 4
    assert showstoppers_earnings == 1


def test_23_deleting_result_removes_awards() -> None:
    teams = _teams()
    results: list[MatchResult] = []
    match = add_match_result(results, teams, 1, 1, 2, 3, 1)
    assert build_team_ledger(teams[0], results).total_match_earnings == 4

    delete_match_result(results, match.id)
    assert build_team_ledger(teams[0], results).total_match_earnings == 0


def test_24_repeated_editing_does_not_duplicate_money() -> None:
    teams = _teams()
    results: list[MatchResult] = []
    match = add_match_result(results, teams, 1, 1, 2, 3, 1)
    for _ in range(5):
        update_match_result(results, teams, match.id, 1, 1, 2, 1, 1)
    assert len(results) == 1
    assert build_team_ledger(teams[0], results).total_match_earnings == 2


def test_25_original_first_auction_budget_unchanged_by_match_accounting() -> None:
    blackout = _team(1, "Blackout FC", 18)
    results: list[MatchResult] = []
    add_match_result(results, _teams(), 1, 1, 2, 3, 1)
    build_team_ledger(blackout, results)
    assert blackout.remaining_budget == 18  # never mutated


def test_26_historical_sold_transactions_unchanged() -> None:
    """Adding/editing/deleting match results never touches auction_spending
    or players_purchased -- only Team.remaining_budget's own frozen value
    is ever read."""
    blackout = _team(1, "Blackout FC", 18)
    spending_before = blackout.auction_spending
    purchased_before = blackout.players_purchased
    results: list[MatchResult] = []
    match = add_match_result(results, _teams(), 1, 1, 2, 3, 1)
    update_match_result(results, _teams(), match.id, 1, 1, 2, 1, 1)
    delete_match_result(results, match.id)
    assert blackout.auction_spending == spending_before
    assert blackout.players_purchased == purchased_before


def test_27_budget_ledger_matches_match_history() -> None:
    teams = _teams()
    results: list[MatchResult] = []
    add_match_result(results, teams, 1, 1, 2, 3, 1)
    add_match_result(results, teams, 2, 3, 4, 2, 2)

    ledgers = build_all_ledgers(teams, results)
    ledgers_by_team = {ledger.team.id: ledger for ledger in ledgers}
    for match in results:
        for team_id, award in match.awards().items():
            assert any(entry.match.id == match.id and entry.award == award for entry in ledgers_by_team[team_id].entries)
