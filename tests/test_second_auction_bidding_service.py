"""tests/test_second_auction_bidding_service.py — services/second_auction_
bidding_service.py. Numbered to match the ticket's own "TESTING —
INITIALIZATION" (1-15), "TESTING — BIDDING" (16-30), "TESTING — UNSOLD /
RE-AUCTION" (31-39), "TESTING — HISTORY" (40-48), and "TESTING —
COMPLETION" (49-57) lists.

Builds a real completed first auction via `AuctionSession` (same pattern
as tests/test_second_auction_session.py) rather than hand-built Team/
Player fixtures, since Milestone 2's initialization reads real rosters/
budgets/match-result ledgers end to end.
"""
from __future__ import annotations

import pytest

from models.auction import AuctionStatus
from models.player import OUTFIELD_BASE_PRICE, Position, player_base_price
from models.second_auction import SecondAuctionStatus
from services.auction_service import team_can_bid_for_player
from services.auction_session_service import AuctionSession
from services.second_auction_bidding_service import (
    SecondAuctionBiddingError,
    maximum_legal_bid,
    process_second_auction_sale,
    sold_player_ids,
    team_can_bid_for_player as sa_team_can_bid_for_player,
)
from services.second_auction_service import build_all_release_previews, is_locked_player


def _pick_eligible_team(player, teams, players):
    eligible = [team for team in teams if team_can_bid_for_player(team, player, players)]
    if not eligible:
        return None
    return min(eligible, key=lambda team: team.roster_size)


def _non_locked_roster_ids(team, players_by_id) -> list[int]:
    return [pid for pid in team.roster if pid in players_by_id and not is_locked_player(players_by_id[pid])]


def completed_session(seed: int = 1) -> AuctionSession:
    session = AuctionSession()
    session.autosave = None
    session.start(seed=seed)
    guard = 0
    while session.auction.status not in (AuctionStatus.COMPLETE, AuctionStatus.BLOCKED) and guard < 300:
        guard += 1
        current = session.current_player
        team = _pick_eligible_team(current, session.teams, session.players)
        if team is None:
            session.mark_current_player_unsold()
        else:
            session.sell_current_player(winning_team=team.id, sale_price=player_base_price(current))
    assert session.auction.status == AuctionStatus.COMPLETE, "expected this seed to complete cleanly"
    return session


def confirm_full_release_plan(session: AuctionSession) -> None:
    players_by_id = {player.id: player for player in session.players}
    for team in session.teams:
        for player_id in _non_locked_roster_ids(team, players_by_id)[:4]:
            session.toggle_second_auction_release(team.id, player_id)
    session.confirm_second_auction_release_plan()


def ready_session(seed: int = 1) -> AuctionSession:
    session = completed_session(seed=seed)
    confirm_full_release_plan(session)
    return session


def run_second_auction_to_completion_or_blocked(session: AuctionSession, max_steps: int = 200) -> None:
    state = session.second_auction_state
    players_by_id = {player.id: player for player in session.players}
    guard = 0
    while state.status == SecondAuctionStatus.IN_PROGRESS and guard < max_steps:
        guard += 1
        current_player = players_by_id[state.current_player_id]
        eligible = [
            team for team in session.teams
            if sa_team_can_bid_for_player(state, team, current_player)
        ]
        if not eligible:
            session.mark_second_auction_player_unsold()
            continue
        team = min(eligible, key=lambda team: len(state.active_rosters_by_team[team.id]))
        session.sell_second_auction_player(winning_team=team.id, sale_price=player_base_price(current_player))


# ============================================================
# TESTING — INITIALIZATION (1-15)
# ============================================================


def test_1_cannot_start_without_completed_first_auction() -> None:
    session = AuctionSession()
    session.autosave = None
    session.start(seed=1)
    with pytest.raises(SecondAuctionBiddingError, match="completed first auction"):
        session.start_second_auction()


def test_2_cannot_start_without_confirmed_release_plan() -> None:
    session = completed_session()
    with pytest.raises(SecondAuctionBiddingError, match="confirmed release plan"):
        session.start_second_auction()


def test_3_cannot_start_with_fewer_than_16_releases() -> None:
    session = completed_session()
    players_by_id = {player.id: player for player in session.players}
    team = session.teams[0]
    player_id = _non_locked_roster_ids(team, players_by_id)[0]
    session.toggle_second_auction_release(team.id, player_id)  # only 1 of 16, never confirmed
    assert session.second_auction_setup.is_confirmed is False
    with pytest.raises(SecondAuctionBiddingError, match="confirmed release plan"):
        session.start_second_auction()


def test_4_cannot_start_with_captain_released() -> None:
    session = ready_session()
    team = session.teams[0]
    # Bypass toggle_release's own protection to exercise the bidding
    # service's own defensive re-check directly.
    session.second_auction_setup.released_player_ids_by_team[team.id][0] = team.captain_player_id
    with pytest.raises(SecondAuctionBiddingError, match="captain"):
        session.start_second_auction()


def test_5_cannot_start_with_gk_released() -> None:
    session = ready_session()
    players_by_id = {player.id: player for player in session.players}
    team = session.teams[0]
    gk_id = next(pid for pid in team.roster if players_by_id[pid].position == Position.GK)
    session.second_auction_setup.released_player_ids_by_team[team.id][0] = gk_id
    with pytest.raises(SecondAuctionBiddingError, match="goalkeeper"):
        session.start_second_auction()


def test_6_exactly_16_player_pool_created() -> None:
    session = ready_session()
    session.start_second_auction()
    assert len(session.second_auction_state.released_player_ids) == 16
    assert len(session.second_auction_state.queue) == 16


def test_7_each_team_begins_with_4_retained_players() -> None:
    session = ready_session()
    session.start_second_auction()
    state = session.second_auction_state
    for team in session.teams:
        assert len(state.active_rosters_by_team[team.id]) == 4


def test_8_captain_retained() -> None:
    session = ready_session()
    session.start_second_auction()
    state = session.second_auction_state
    for team in session.teams:
        assert team.captain_player_id in state.active_rosters_by_team[team.id]


def test_9_gk_retained() -> None:
    session = ready_session()
    players_by_id = {player.id: player for player in session.players}
    session.start_second_auction()
    state = session.second_auction_state
    for team in session.teams:
        roster = state.active_rosters_by_team[team.id]
        assert any(players_by_id[pid].position == Position.GK for pid in roster)


def test_10_starting_budget_matches_preview() -> None:
    session = ready_session()
    players_by_id = {player.id: player for player in session.players}
    previews = {
        preview.team.id: preview
        for preview in build_all_release_previews(
            session.teams, session.second_auction_setup, players_by_id, session.match_results
        )
    }
    session.start_second_auction()
    state = session.second_auction_state
    for team in session.teams:
        assert state.starting_budgets_by_team[team.id] == previews[team.id].second_auction_starting_budget
        assert state.remaining_budgets_by_team[team.id] == previews[team.id].second_auction_starting_budget


def test_11_starting_budget_freezes_at_initialization() -> None:
    """Adding a match result after the second auction has started must
    never retroactively change its already-frozen starting budgets."""
    session = ready_session()
    session.start_second_auction()
    state = session.second_auction_state
    frozen = dict(state.starting_budgets_by_team)
    blackout = next(team for team in session.teams if team.name == "Blackout FC")
    darkstar = next(team for team in session.teams if team.name == "Darkstar FC")
    session.add_match_result(1, blackout.id, darkstar.id, 5, 0)
    assert state.starting_budgets_by_team == frozen
    assert state.remaining_budgets_by_team[blackout.id] == frozen[blackout.id]


def test_12_first_auction_budget_unchanged() -> None:
    session = ready_session()
    budgets_before = {team.id: team.remaining_budget for team in session.teams}
    session.start_second_auction()
    run_second_auction_to_completion_or_blocked(session)
    for team in session.teams:
        assert team.remaining_budget == budgets_before[team.id]


def test_13_first_auction_history_unchanged() -> None:
    session = ready_session()
    history_before = list(session.auction.history)
    session.start_second_auction()
    run_second_auction_to_completion_or_blocked(session)
    assert session.auction.history == history_before


def test_14_first_auction_sold_prices_unchanged() -> None:
    session = ready_session()
    players_by_id = {player.id: player for player in session.players}
    sold_prices_before = {
        player.id: player.sold_price for player in session.players if player.sold_price is not None
    }
    session.start_second_auction()
    run_second_auction_to_completion_or_blocked(session)
    for player_id, price in sold_prices_before.items():
        assert players_by_id[player_id].sold_price == price


def test_15_second_auction_status_becomes_in_progress() -> None:
    session = ready_session()
    assert session.second_auction_state.status == SecondAuctionStatus.NOT_STARTED
    session.start_second_auction()
    assert session.second_auction_state.status == SecondAuctionStatus.IN_PROGRESS


# ============================================================
# TESTING — BIDDING (16-30)
# ============================================================


def test_16_minimum_base_bid_enforced() -> None:
    session = ready_session()
    session.start_second_auction()
    state = session.second_auction_state
    players_by_id = {player.id: player for player in session.players}
    current_player = players_by_id[state.current_player_id]
    team = session.teams[0]
    base_price = player_base_price(current_player)
    assert base_price == OUTFIELD_BASE_PRICE  # released players are never GKs
    session.sell_second_auction_player(winning_team=team.id, sale_price=base_price)
    assert state.history[-1].sold_price == base_price


def test_17_sub_base_bid_rejected() -> None:
    session = ready_session()
    session.start_second_auction()
    team = session.teams[0]
    with pytest.raises(SecondAuctionBiddingError, match="minimum price"):
        session.sell_second_auction_player(winning_team=team.id, sale_price=1)


def test_18_eligible_team_can_bid() -> None:
    session = ready_session()
    session.start_second_auction()
    state = session.second_auction_state
    players_by_id = {player.id: player for player in session.players}
    current_player = players_by_id[state.current_player_id]
    team = session.teams[0]
    assert sa_team_can_bid_for_player(state, team, current_player) is True
    session.sell_second_auction_player(winning_team=team.id, sale_price=OUTFIELD_BASE_PRICE)
    assert team.id in {entry.team_id for entry in state.history}


def test_19_full_team_cannot_bid() -> None:
    session = ready_session()
    session.start_second_auction()
    state = session.second_auction_state
    team = session.teams[0]
    for team_id in state.active_rosters_by_team:
        if team_id != team.id:
            state.active_rosters_by_team[team_id] = state.active_rosters_by_team[team_id][:0]
    state.active_rosters_by_team[team.id] = list(range(1000, 1000 + team.max_squad_size))  # fake full roster
    players_by_id = {player.id: player for player in session.players}
    current_player = players_by_id[state.current_player_id]
    assert sa_team_can_bid_for_player(state, team, current_player) is False
    with pytest.raises(SecondAuctionBiddingError, match="roster is full"):
        session.sell_second_auction_player(winning_team=team.id, sale_price=OUTFIELD_BASE_PRICE)


def test_20_insufficient_budget_rejected() -> None:
    session = ready_session()
    session.start_second_auction()
    state = session.second_auction_state
    team = session.teams[0]
    state.remaining_budgets_by_team[team.id] = 0
    with pytest.raises(SecondAuctionBiddingError):
        session.sell_second_auction_player(winning_team=team.id, sale_price=OUTFIELD_BASE_PRICE)


def test_21_max_legal_bid_calculated_correctly() -> None:
    # Ticket's own worked example: 4/8 roster, 20M remaining -> reserve
    # 3 x 2M = 6M -> max bid 14M.
    assert maximum_legal_bid(remaining_budget=20, roster_size=4, max_squad_size=8) == 14
    # 7/8 roster: final slot may spend everything (reserve = 0).
    assert maximum_legal_bid(remaining_budget=9, roster_size=7, max_squad_size=8) == 9


def test_22_reserve_rule_preserves_funds_for_remaining_slots() -> None:
    session = ready_session()
    session.start_second_auction()
    state = session.second_auction_state
    team = session.teams[0]
    state.remaining_budgets_by_team[team.id] = 20
    state.active_rosters_by_team[team.id] = state.active_rosters_by_team[team.id][:4]
    players_by_id = {player.id: player for player in session.players}
    current_player = players_by_id[state.current_player_id]
    with pytest.raises(SecondAuctionBiddingError, match="reserve"):
        session.sell_second_auction_player(winning_team=team.id, sale_price=15)
    session.sell_second_auction_player(winning_team=team.id, sale_price=14)  # exactly at the max is legal
    assert state.remaining_budgets_by_team[team.id] == 6


def test_23_final_slot_may_spend_remaining_legal_budget() -> None:
    session = ready_session()
    session.start_second_auction()
    state = session.second_auction_state
    team = session.teams[0]
    # 7/8 roster (one slot left): the entire remaining budget is legal.
    state.active_rosters_by_team[team.id] = (state.active_rosters_by_team[team.id] + [111, 112, 113])[:7]
    state.remaining_budgets_by_team[team.id] = 9
    session.sell_second_auction_player(winning_team=team.id, sale_price=9)
    assert state.remaining_budgets_by_team[team.id] == 0


def test_24_sold_deducts_second_auction_budget() -> None:
    session = ready_session()
    session.start_second_auction()
    state = session.second_auction_state
    team = session.teams[0]
    before = state.remaining_budgets_by_team[team.id]
    session.sell_second_auction_player(winning_team=team.id, sale_price=OUTFIELD_BASE_PRICE)
    assert state.remaining_budgets_by_team[team.id] == before - OUTFIELD_BASE_PRICE


def test_25_sold_adds_player_to_current_roster() -> None:
    session = ready_session()
    session.start_second_auction()
    state = session.second_auction_state
    team = session.teams[0]
    player_id = state.current_player_id
    session.sell_second_auction_player(winning_team=team.id, sale_price=OUTFIELD_BASE_PRICE)
    assert player_id in state.active_rosters_by_team[team.id]


def test_26_sold_does_not_overwrite_first_auction_sold_price() -> None:
    session = ready_session()
    players_by_id = {player.id: player for player in session.players}
    session.start_second_auction()
    state = session.second_auction_state
    player_id = state.current_player_id
    original_price = players_by_id[player_id].sold_price
    team = session.teams[0]
    session.sell_second_auction_player(winning_team=team.id, sale_price=OUTFIELD_BASE_PRICE + 3)
    assert players_by_id[player_id].sold_price == original_price


def test_27_sold_records_separate_second_auction_price() -> None:
    session = ready_session()
    session.start_second_auction()
    state = session.second_auction_state
    team = session.teams[0]
    session.sell_second_auction_player(winning_team=team.id, sale_price=OUTFIELD_BASE_PRICE + 3)
    assert state.history[-1].sold_price == OUTFIELD_BASE_PRICE + 3


def test_28_player_cannot_be_sold_twice() -> None:
    session = ready_session()
    session.start_second_auction()
    state = session.second_auction_state
    players_by_id = {player.id: player for player in session.players}
    team = session.teams[0]
    player_id = state.current_player_id
    session.sell_second_auction_player(winning_team=team.id, sale_price=OUTFIELD_BASE_PRICE)

    # Rewind the queue position back onto the already-SOLD player and
    # attempt to sell it again directly through the service function —
    # it must be rejected as already resolved, never sold twice.
    state.current_queue_position = state.queue.index(player_id)
    with pytest.raises(SecondAuctionBiddingError, match="already been resolved"):
        process_second_auction_sale(
            state, session.second_auction_setup, session.teams, players_by_id, team.id, OUTFIELD_BASE_PRICE
        )


def test_29_resolved_player_cannot_be_bid_again() -> None:
    session = ready_session()
    session.start_second_auction()
    state = session.second_auction_state
    team = session.teams[0]
    player_id = state.current_player_id
    session.sell_second_auction_player(winning_team=team.id, sale_price=OUTFIELD_BASE_PRICE)
    assert player_id in sold_player_ids(state)
    assert state.current_player_id != player_id


def test_30_organizer_manual_bidding_works() -> None:
    session = ready_session()
    session.start_second_auction()
    state = session.second_auction_state
    team = session.teams[0]
    result = session.sell_second_auction_player(winning_team=team.id, sale_price=OUTFIELD_BASE_PRICE)
    assert result.outcome == "SOLD"
    assert result.team.id == team.id


# ============================================================
# TESTING — UNSOLD / RE-AUCTION (31-39)
# ============================================================


def test_31_unsold_creates_history_entry() -> None:
    session = ready_session()
    session.start_second_auction()
    state = session.second_auction_state
    session.mark_second_auction_player_unsold()
    assert len(state.history) == 1
    assert state.history[0].status == "UNSOLD"


def test_32_unsold_does_not_change_budgets() -> None:
    session = ready_session()
    session.start_second_auction()
    state = session.second_auction_state
    budgets_before = dict(state.remaining_budgets_by_team)
    session.mark_second_auction_player_unsold()
    assert state.remaining_budgets_by_team == budgets_before


def test_33_unsold_does_not_change_rosters() -> None:
    session = ready_session()
    session.start_second_auction()
    state = session.second_auction_state
    rosters_before = {tid: list(r) for tid, r in state.active_rosters_by_team.items()}
    session.mark_second_auction_player_unsold()
    assert state.active_rosters_by_team == rosters_before


def test_34_unsold_player_enters_reauction() -> None:
    session = ready_session()
    session.start_second_auction()
    state = session.second_auction_state
    unsold_id = state.current_player_id
    for _ in range(len(state.queue)):  # unsell every player in round 1
        session.mark_second_auction_player_unsold()
    assert state.round_number == 2
    assert unsold_id in state.queue


def test_35_round_increments_correctly() -> None:
    session = ready_session()
    session.start_second_auction()
    state = session.second_auction_state
    assert state.round_number == 1
    for _ in range(len(state.queue)):
        session.mark_second_auction_player_unsold()
    assert state.round_number == 2


def test_36_reauction_player_can_later_be_sold() -> None:
    session = ready_session()
    session.start_second_auction()
    state = session.second_auction_state
    players_by_id = {player.id: player for player in session.players}
    unsold_id = state.current_player_id
    for _ in range(len(state.queue)):
        session.mark_second_auction_player_unsold()
    assert unsold_id in state.queue
    while state.current_player_id != unsold_id:
        session.mark_second_auction_player_unsold()
    team = session.teams[0]
    session.sell_second_auction_player(winning_team=team.id, sale_price=player_base_price(players_by_id[unsold_id]))
    assert unsold_id in sold_player_ids(state)


def test_37_no_duplicate_pool_entries() -> None:
    session = ready_session()
    session.start_second_auction()
    state = session.second_auction_state
    assert len(state.released_player_ids) == len(set(state.released_player_ids))
    assert len(state.queue) == len(set(state.queue))


def test_38_completion_waits_for_all_16_sold() -> None:
    session = ready_session()
    session.start_second_auction()
    state = session.second_auction_state
    session.sell_second_auction_player(winning_team=session.teams[0].id, sale_price=OUTFIELD_BASE_PRICE)
    assert state.status == SecondAuctionStatus.IN_PROGRESS
    assert len(sold_player_ids(state)) == 1


def test_39_blocked_state_detected_safely() -> None:
    session = ready_session()
    session.start_second_auction()
    state = session.second_auction_state
    for team_id in state.remaining_budgets_by_team:
        state.remaining_budgets_by_team[team_id] = 0
    guard = 0
    while state.status == SecondAuctionStatus.IN_PROGRESS and guard < 50:
        guard += 1
        session.mark_second_auction_player_unsold()
    assert state.status == SecondAuctionStatus.BLOCKED
    assert guard < 50  # no infinite loop


# ============================================================
# TESTING — HISTORY (40-48)
# ============================================================


def test_40_first_auction_history_remains_unchanged() -> None:
    session = ready_session()
    history_before = list(session.auction.history)
    session.start_second_auction()
    session.sell_second_auction_player(winning_team=session.teams[0].id, sale_price=OUTFIELD_BASE_PRICE)
    assert session.auction.history == history_before


def test_41_second_auction_history_independent() -> None:
    session = ready_session()
    session.start_second_auction()
    state = session.second_auction_state
    session.sell_second_auction_player(winning_team=session.teams[0].id, sale_price=OUTFIELD_BASE_PRICE)
    assert len(state.history) == 1
    assert len(session.auction.history) > 0  # the first auction's own history, untouched and separate


def test_42_second_auction_sold_record_correct() -> None:
    session = ready_session()
    session.start_second_auction()
    state = session.second_auction_state
    team = session.teams[0]
    player_id = state.current_player_id
    session.sell_second_auction_player(winning_team=team.id, sale_price=OUTFIELD_BASE_PRICE + 1)
    entry = state.history[-1]
    assert entry.player_id == player_id
    assert entry.status == "SOLD"
    assert entry.team_id == team.id
    assert entry.sold_price == OUTFIELD_BASE_PRICE + 1


def test_43_second_auction_unsold_record_correct() -> None:
    session = ready_session()
    session.start_second_auction()
    state = session.second_auction_state
    player_id = state.current_player_id
    session.mark_second_auction_player_unsold()
    entry = state.history[-1]
    assert entry.player_id == player_id
    assert entry.status == "UNSOLD"
    assert entry.team_id is None
    assert entry.sold_price is None


def test_44_previous_team_stored_correctly() -> None:
    session = ready_session()
    setup = session.second_auction_setup
    session.start_second_auction()
    state = session.second_auction_state
    player_id = state.current_player_id
    expected_team = next(team for team in session.teams if player_id in setup.selections_for(team.id))
    session.mark_second_auction_player_unsold()
    entry = state.history[-1]
    assert entry.previous_team_id == expected_team.id
    assert entry.previous_team_name == expected_team.name


def test_45_original_purchase_price_preserved() -> None:
    session = ready_session()
    players_by_id = {player.id: player for player in session.players}
    session.start_second_auction()
    state = session.second_auction_state
    player_id = state.current_player_id
    expected_price = players_by_id[player_id].sold_price
    session.mark_second_auction_player_unsold()
    entry = state.history[-1]
    assert entry.original_first_auction_price == expected_price


def test_46_new_second_auction_team_stored() -> None:
    session = ready_session()
    session.start_second_auction()
    state = session.second_auction_state
    team = session.teams[0]
    session.sell_second_auction_player(winning_team=team.id, sale_price=OUTFIELD_BASE_PRICE)
    assert state.history[-1].team_id == team.id
    assert state.history[-1].team_name == team.name


def test_47_new_second_auction_price_stored() -> None:
    session = ready_session()
    session.start_second_auction()
    state = session.second_auction_state
    team = session.teams[0]
    session.sell_second_auction_player(winning_team=team.id, sale_price=OUTFIELD_BASE_PRICE + 2)
    assert state.history[-1].sold_price == OUTFIELD_BASE_PRICE + 2


def test_48_history_order_stable() -> None:
    session = ready_session()
    session.start_second_auction()
    state = session.second_auction_state
    for _ in range(3):
        session.mark_second_auction_player_unsold()
    sequences = [entry.sequence for entry in state.history]
    assert sequences == sorted(sequences)
    assert sequences == [1, 2, 3]


# ============================================================
# TESTING — COMPLETION (49-57)
# ============================================================


def test_49_all_teams_end_at_8_players() -> None:
    session = ready_session()
    session.start_second_auction()
    run_second_auction_to_completion_or_blocked(session)
    state = session.second_auction_state
    assert state.status == SecondAuctionStatus.COMPLETE
    for team in session.teams:
        assert len(state.active_rosters_by_team[team.id]) == 8


def test_50_all_teams_retain_captain() -> None:
    session = ready_session()
    session.start_second_auction()
    run_second_auction_to_completion_or_blocked(session)
    state = session.second_auction_state
    for team in session.teams:
        assert team.captain_player_id in state.active_rosters_by_team[team.id]


def test_51_all_teams_retain_exactly_one_gk() -> None:
    session = ready_session()
    players_by_id = {player.id: player for player in session.players}
    session.start_second_auction()
    run_second_auction_to_completion_or_blocked(session)
    state = session.second_auction_state
    for team in session.teams:
        gks = [pid for pid in state.active_rosters_by_team[team.id] if players_by_id[pid].position == Position.GK]
        assert len(gks) == 1


def test_52_all_16_released_players_sold() -> None:
    session = ready_session()
    session.start_second_auction()
    run_second_auction_to_completion_or_blocked(session)
    state = session.second_auction_state
    assert sold_player_ids(state) == set(state.released_player_ids)


def test_53_second_auction_status_complete() -> None:
    session = ready_session()
    session.start_second_auction()
    run_second_auction_to_completion_or_blocked(session)
    assert session.second_auction_state.status == SecondAuctionStatus.COMPLETE


def test_54_first_auction_status_still_complete() -> None:
    session = ready_session()
    session.start_second_auction()
    run_second_auction_to_completion_or_blocked(session)
    assert session.auction.status == AuctionStatus.COMPLETE


def test_55_no_unresolved_released_players() -> None:
    session = ready_session()
    session.start_second_auction()
    run_second_auction_to_completion_or_blocked(session)
    state = session.second_auction_state
    pending = [pid for pid in state.released_player_ids if pid not in sold_player_ids(state)]
    assert pending == []


def test_56_no_team_exceeds_8_players() -> None:
    session = ready_session()
    session.start_second_auction()
    run_second_auction_to_completion_or_blocked(session)
    state = session.second_auction_state
    for team in session.teams:
        assert len(state.active_rosters_by_team[team.id]) <= team.max_squad_size


def test_57_second_auction_final_budgets_correct() -> None:
    session = ready_session()
    players_by_id = {player.id: player for player in session.players}
    session.start_second_auction()
    state = session.second_auction_state
    starting = dict(state.starting_budgets_by_team)
    run_second_auction_to_completion_or_blocked(session)
    spent_by_team: dict[int, int] = {team.id: 0 for team in session.teams}
    for entry in state.history:
        if entry.status == "SOLD":
            spent_by_team[entry.team_id] += entry.sold_price
    for team in session.teams:
        assert state.remaining_budgets_by_team[team.id] == starting[team.id] - spent_by_team[team.id]
