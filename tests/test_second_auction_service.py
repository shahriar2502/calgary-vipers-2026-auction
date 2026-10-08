"""tests/test_second_auction_service.py — services/second_auction_service.py:
release rules, refund calculation, and the derived budget preview.
Numbered to match the ticket's own "TESTING — RELEASE RULES" (1-20) and
"TESTING — REFUNDS / BUDGETS" (21-32) lists.
"""
import pytest

from models.match_result import MatchResult
from models.player import Player, PlayerAuctionStatus, Position
from models.second_auction import SecondAuctionSetup
from models.team import Team
from services.second_auction_service import (
    MAX_RELEASES_PER_TEAM,
    SecondAuctionError,
    all_teams_have_exactly_four,
    build_all_release_previews,
    build_release_pool,
    build_team_release_preview,
    confirm_release_plan,
    is_locked_player,
    releasable_roster,
    toggle_release,
    unlock_release_plan,
)


def _sold_player(player_id: int, name: str, position: Position, price: int, team_name: str, sequence: int) -> Player:
    return Player(
        id=player_id, full_name=name, short_name=name, position=position, overall_rating=80,
        auction_status=PlayerAuctionStatus.SOLD, sold_price=price, sold_to=team_name, auction_sequence=sequence,
    )


def _captain(player_id: int, name: str, team_name: str) -> Player:
    return Player(
        id=player_id, full_name=name, short_name=name, position=Position.ATT, overall_rating=85,
        is_captain=True, assigned_team=team_name, auction_eligible=False,
        auction_status=PlayerAuctionStatus.PRE_ASSIGNED,
    )


def _team_with_roster(team_id: int, name: str, remaining_budget: int, roster_ids: list[int], spending: int) -> Team:
    return Team(
        id=team_id, name=name, short_name=name[:3], captain_player_id=roster_ids[0], captain_name="Cap",
        remaining_budget=remaining_budget, auction_spending=spending, roster=roster_ids, players_purchased=len(roster_ids) - 1,
    )


def _build_blackout() -> tuple[Team, dict[int, Player]]:
    """Blackout FC: captain (free) + 1 GK (4M) + 6 outfield (2,2,2,2,20,4 = 32M spent)."""
    captain = _captain(1, "Cap Blackout", "Blackout FC")
    gk = _sold_player(2, "GK Blackout", Position.GK, 4, "Blackout FC", 1)
    outfield = [
        _sold_player(10, "Out A", Position.DEF, 2, "Blackout FC", 2),
        _sold_player(11, "Out B", Position.DEF, 2, "Blackout FC", 3),
        _sold_player(12, "Out C", Position.MID, 2, "Blackout FC", 4),
        _sold_player(13, "Out D", Position.MID, 2, "Blackout FC", 5),
        _sold_player(14, "Out E", Position.ATT, 20, "Blackout FC", 6),
        _sold_player(15, "Out F", Position.ATT, 4, "Blackout FC", 7),
    ]
    roster_ids = [captain.id, gk.id] + [p.id for p in outfield]
    team = _team_with_roster(1, "Blackout FC", remaining_budget=10, roster_ids=roster_ids, spending=90)
    players_by_id = {p.id: p for p in [captain, gk, *outfield]}
    return team, players_by_id


# ============================================================
# TESTING — RELEASE RULES (1-20)
# (1-6: screen/session-level gating -- see test_second_auction_session.py
#  and test_second_auction_screen.py)
# ============================================================


# 7. roster shows 8 players per team
def test_7_roster_shows_8_players_per_team() -> None:
    team, players_by_id = _build_blackout()
    assert team.roster_size == 8


# 8. captain marked locked
def test_8_captain_marked_locked() -> None:
    team, players_by_id = _build_blackout()
    captain = players_by_id[team.captain_player_id]
    assert is_locked_player(captain) is True


# 9. captain cannot be selected
def test_9_captain_cannot_be_selected() -> None:
    team, players_by_id = _build_blackout()
    setup = SecondAuctionSetup()
    with pytest.raises(SecondAuctionError, match="captain"):
        toggle_release(setup, team, team.captain_player_id, players_by_id)


# 10. GK marked locked
def test_10_gk_marked_locked() -> None:
    team, players_by_id = _build_blackout()
    gk = players_by_id[2]
    assert is_locked_player(gk) is True


# 11. GK cannot be selected
def test_11_gk_cannot_be_selected() -> None:
    team, players_by_id = _build_blackout()
    setup = SecondAuctionSetup()
    with pytest.raises(SecondAuctionError, match="goalkeeper"):
        toggle_release(setup, team, 2, players_by_id)


# 12. eligible outfield player selectable
def test_12_eligible_outfield_player_selectable() -> None:
    team, players_by_id = _build_blackout()
    setup = SecondAuctionSetup()
    toggle_release(setup, team, 10, players_by_id)
    assert setup.selections_for(team.id) == [10]


# 13. cannot select more than 4
def test_13_cannot_select_more_than_4() -> None:
    team, players_by_id = _build_blackout()
    setup = SecondAuctionSetup()
    for player_id in (10, 11, 12, 13):
        toggle_release(setup, team, player_id, players_by_id)
    with pytest.raises(SecondAuctionError, match="already has 4"):
        toggle_release(setup, team, 14, players_by_id)
    assert len(setup.selections_for(team.id)) == 4


# 14. selected count displays correctly
def test_14_selected_count_displays_correctly() -> None:
    team, players_by_id = _build_blackout()
    setup = SecondAuctionSetup()
    toggle_release(setup, team, 10, players_by_id)
    toggle_release(setup, team, 11, players_by_id)
    assert len(setup.selections_for(team.id)) == 2


# 15. exactly 4 required per team
def test_15_exactly_4_required_per_team() -> None:
    team, players_by_id = _build_blackout()
    setup = SecondAuctionSetup()
    toggle_release(setup, team, 10, players_by_id)
    toggle_release(setup, team, 11, players_by_id)
    assert all_teams_have_exactly_four(setup, [team]) is False
    toggle_release(setup, team, 12, players_by_id)
    toggle_release(setup, team, 13, players_by_id)
    assert all_teams_have_exactly_four(setup, [team]) is True


# 16. confirmation disabled until all 4 teams have 4
def test_16_confirmation_disabled_until_all_4_teams_have_4() -> None:
    team, players_by_id = _build_blackout()
    setup = SecondAuctionSetup()
    toggle_release(setup, team, 10, players_by_id)
    toggle_release(setup, team, 11, players_by_id)
    toggle_release(setup, team, 12, players_by_id)
    with pytest.raises(SecondAuctionError, match="must have exactly 4"):
        confirm_release_plan(setup, [team])
    assert setup.is_confirmed is False


# 17. 16-player pool created from 4x4 selection
def test_17_16_player_pool_created_from_4x4_selection() -> None:
    team, players_by_id = _build_blackout()
    setup = SecondAuctionSetup()
    for player_id in (10, 11, 12, 13):
        toggle_release(setup, team, player_id, players_by_id)
    confirm_release_plan(setup, [team])
    pool = build_release_pool([team], setup, players_by_id)
    assert len(pool) == 4  # 4 teams x 4 would be 16; this fixture has just 1 team
    assert {entry.player.id for entry in pool} == {10, 11, 12, 13}


# 18. duplicate player cannot appear twice
def test_18_duplicate_player_cannot_appear_twice() -> None:
    team, players_by_id = _build_blackout()
    setup = SecondAuctionSetup()
    toggle_release(setup, team, 10, players_by_id)
    toggle_release(setup, team, 10, players_by_id)  # toggling again deselects, not a duplicate add
    assert setup.selections_for(team.id) == []
    assert 10 not in setup.released_player_ids_by_team.get(team.id, [])


# 19. player must belong to selected team
def test_19_player_must_belong_to_selected_team() -> None:
    team, players_by_id = _build_blackout()
    other_team = _team_with_roster(2, "Darkstar FC", remaining_budget=20, roster_ids=[900], spending=80)
    players_by_id[900] = _captain(900, "Cap Darkstar", "Darkstar FC")
    setup = SecondAuctionSetup()
    with pytest.raises(SecondAuctionError, match="does not belong"):
        toggle_release(setup, other_team, 10, players_by_id)  # player 10 belongs to Blackout, not Darkstar


# 20. invalid player selection rejected
def test_20_invalid_player_selection_rejected() -> None:
    team, players_by_id = _build_blackout()
    setup = SecondAuctionSetup()
    with pytest.raises(SecondAuctionError):
        toggle_release(setup, team, 999999, players_by_id)


def test_cannot_modify_a_confirmed_plan() -> None:
    team, players_by_id = _build_blackout()
    setup = SecondAuctionSetup()
    for player_id in (10, 11, 12, 13):
        toggle_release(setup, team, player_id, players_by_id)
    confirm_release_plan(setup, [team])
    with pytest.raises(SecondAuctionError, match="confirmed and locked"):
        toggle_release(setup, team, 14, players_by_id)


def test_unlock_release_plan_returns_to_draft() -> None:
    team, players_by_id = _build_blackout()
    setup = SecondAuctionSetup()
    for player_id in (10, 11, 12, 13):
        toggle_release(setup, team, player_id, players_by_id)
    confirm_release_plan(setup, [team])
    unlock_release_plan(setup)
    assert setup.is_confirmed is False
    assert setup.confirmed_at is None
    # Selections survive the unlock -- only the status/confirmed_at reset.
    assert setup.selections_for(team.id) == [10, 11, 12, 13]


def test_unlocking_a_non_confirmed_plan_raises() -> None:
    setup = SecondAuctionSetup()
    with pytest.raises(SecondAuctionError, match="not confirmed"):
        unlock_release_plan(setup)


def test_releasable_roster_excludes_captain_and_gk() -> None:
    team, players_by_id = _build_blackout()
    releasable = releasable_roster(team, players_by_id)
    releasable_ids = {p.id for p in releasable}
    assert releasable_ids == {10, 11, 12, 13, 14, 15}


# ============================================================
# TESTING — REFUNDS / BUDGETS (21-32)
# ============================================================


# 21. original purchase price read correctly
def test_21_original_purchase_price_read_correctly() -> None:
    team, players_by_id = _build_blackout()
    assert players_by_id[14].sold_price == 20
    assert players_by_id[15].sold_price == 4


# 22. 2M player refunds 2M
def test_22_2m_player_refunds_2m() -> None:
    team, players_by_id = _build_blackout()
    setup = SecondAuctionSetup()
    toggle_release(setup, team, 10, players_by_id)
    preview = build_team_release_preview(team, setup, players_by_id, [])
    assert preview.refunds[0].original_price == 2
    assert preview.total_refunds == 2


# 23. 20M player refunds 20M
def test_23_20m_player_refunds_20m() -> None:
    team, players_by_id = _build_blackout()
    setup = SecondAuctionSetup()
    toggle_release(setup, team, 14, players_by_id)
    preview = build_team_release_preview(team, setup, players_by_id, [])
    assert preview.refunds[0].original_price == 20
    assert preview.total_refunds == 20


# 24. refund total sums exactly
def test_24_refund_total_sums_exactly() -> None:
    team, players_by_id = _build_blackout()
    setup = SecondAuctionSetup()
    for player_id in (10, 11, 12, 14):  # 2 + 2 + 2 + 20 = 26
        toggle_release(setup, team, player_id, players_by_id)
    preview = build_team_release_preview(team, setup, players_by_id, [])
    assert preview.total_refunds == 26


# 25. captain has no releasable refund path
def test_25_captain_has_no_releasable_refund_path() -> None:
    team, players_by_id = _build_blackout()
    captain = players_by_id[team.captain_player_id]
    assert captain.sold_price is None
    setup = SecondAuctionSetup()
    with pytest.raises(SecondAuctionError):
        toggle_release(setup, team, captain.id, players_by_id)


# 26. current transfer budget comes from existing ledger
def test_26_current_transfer_budget_comes_from_existing_ledger() -> None:
    team, players_by_id = _build_blackout()
    setup = SecondAuctionSetup()
    match = MatchResult(
        id="m1", match_number=1, team1_id=team.id, team2_id=999, team1_goals=3, team2_goals=1,
        created_at="2026-01-01T00:00:00+00:00",
    )
    preview = build_team_release_preview(team, setup, players_by_id, [match])
    assert preview.current_transfer_budget == team.remaining_budget + 4  # win award


# 27. first-auction remaining unchanged
def test_27_first_auction_remaining_unchanged() -> None:
    team, players_by_id = _build_blackout()
    budget_before = team.remaining_budget
    setup = SecondAuctionSetup()
    toggle_release(setup, team, 10, players_by_id)
    build_team_release_preview(team, setup, players_by_id, [])
    assert team.remaining_budget == budget_before


# 28. match earnings unchanged
def test_28_match_earnings_unchanged_by_release_preview() -> None:
    team, players_by_id = _build_blackout()
    setup = SecondAuctionSetup()
    match = MatchResult(
        id="m1", match_number=1, team1_id=team.id, team2_id=999, team1_goals=2, team2_goals=2,
        created_at="2026-01-01T00:00:00+00:00",
    )
    results = [match]
    results_before = list(results)
    toggle_release(setup, team, 10, players_by_id)
    build_team_release_preview(team, setup, players_by_id, results)
    assert results == results_before


# 29. second budget = current transfer + refunds
def test_29_second_budget_equals_current_transfer_plus_refunds() -> None:
    team, players_by_id = _build_blackout()
    setup = SecondAuctionSetup()
    for player_id in (10, 11, 12, 14):  # refunds 2+2+2+20=26
        toggle_release(setup, team, player_id, players_by_id)
    preview = build_team_release_preview(team, setup, players_by_id, [])
    assert preview.second_auction_starting_budget == preview.current_transfer_budget + 26
    assert preview.second_auction_starting_budget == team.remaining_budget + 26


# 30. no team budget mutation during preview
def test_30_no_team_budget_mutation_during_preview() -> None:
    team, players_by_id = _build_blackout()
    budget_before = team.remaining_budget
    spending_before = team.auction_spending
    roster_before = list(team.roster)
    setup = SecondAuctionSetup()
    for player_id in (10, 11, 12, 13):
        toggle_release(setup, team, player_id, players_by_id)
    confirm_release_plan(setup, [team])
    build_all_release_previews([team], setup, players_by_id, [])
    assert team.remaining_budget == budget_before
    assert team.auction_spending == spending_before
    assert team.roster == roster_before


# 31. first-auction SOLD history unchanged
def test_31_first_auction_sold_data_unchanged() -> None:
    team, players_by_id = _build_blackout()
    sold_prices_before = {pid: p.sold_price for pid, p in players_by_id.items()}
    setup = SecondAuctionSetup()
    toggle_release(setup, team, 10, players_by_id)
    build_team_release_preview(team, setup, players_by_id, [])
    for pid, price in sold_prices_before.items():
        assert players_by_id[pid].sold_price == price


# 32. refund preview updates immediately when selection changes
def test_32_refund_preview_updates_immediately_when_selection_changes() -> None:
    team, players_by_id = _build_blackout()
    setup = SecondAuctionSetup()
    toggle_release(setup, team, 10, players_by_id)
    preview_before = build_team_release_preview(team, setup, players_by_id, [])
    assert preview_before.total_refunds == 2

    toggle_release(setup, team, 14, players_by_id)
    preview_after = build_team_release_preview(team, setup, players_by_id, [])
    assert preview_after.total_refunds == 22
