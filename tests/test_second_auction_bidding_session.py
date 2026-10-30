"""tests/test_second_auction_bidding_session.py — AuctionSession's
second-auction bidding methods (`start_second_auction`/
`sell_second_auction_player`/`mark_second_auction_player_unsold`) and
their persistence round-trip. Numbered to match the ticket's own
"TESTING — PERSISTENCE" list (58-70).
"""
from __future__ import annotations

import pytest

from models.auction import AuctionStatus
from models.player import OUTFIELD_BASE_PRICE, player_base_price
from models.second_auction import SecondAuctionStatus
from services import persistence_service as ps
from services.auction_service import team_can_bid_for_player
from services.auction_session_service import AuctionSession, SessionMode
from services.second_auction_bidding_service import team_can_bid_for_player as sa_team_can_bid_for_player
from services.second_auction_service import is_locked_player


@pytest.fixture
def isolated_saves(tmp_path, monkeypatch):
    mocks_dir = tmp_path / "mocks"
    live_dir = tmp_path / "live"
    live_active = live_dir / "live_active.json"
    monkeypatch.setattr(ps, "SAVES_DIR", tmp_path)
    monkeypatch.setattr(ps, "MOCKS_DIR", mocks_dir)
    monkeypatch.setattr(ps, "LIVE_DIR", live_dir)
    monkeypatch.setattr(ps, "LIVE_ACTIVE_PATH", live_active)
    return tmp_path


def _pick_eligible_team(player, teams, players):
    eligible = [team for team in teams if team_can_bid_for_player(team, player, players)]
    if not eligible:
        return None
    return min(eligible, key=lambda team: team.roster_size)


def _non_locked_roster_ids(team, players_by_id) -> list[int]:
    return [pid for pid in team.roster if pid in players_by_id and not is_locked_player(players_by_id[pid])]


def new_ready_session(mode: SessionMode = SessionMode.MOCK, seed: int = 1) -> AuctionSession:
    """A completed first auction with a confirmed 16-player release plan
    (second auction not started yet), autosaving on every call."""
    session = AuctionSession()
    session.autosave = ps.autosave_hook
    session.start(seed=seed, mode=mode)
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

    players_by_id = {player.id: player for player in session.players}
    for team in session.teams:
        for player_id in _non_locked_roster_ids(team, players_by_id)[:4]:
            session.toggle_second_auction_release(team.id, player_id)
    session.confirm_second_auction_release_plan()
    return session


def _run_to_completion_or_blocked(session: AuctionSession, max_steps: int = 200) -> None:
    state = session.second_auction_state
    players_by_id = {player.id: player for player in session.players}
    guard = 0
    while state.status == SecondAuctionStatus.IN_PROGRESS and guard < max_steps:
        guard += 1
        current_player = players_by_id[state.current_player_id]
        eligible = [team for team in session.teams if sa_team_can_bid_for_player(state, team, current_player)]
        if not eligible:
            session.mark_second_auction_player_unsold()
            continue
        team = min(eligible, key=lambda team: len(state.active_rosters_by_team[team.id]))
        session.sell_second_auction_player(winning_team=team.id, sale_price=player_base_price(current_player))


# ============================================================
# 58. old save loads without second-auction data
# ============================================================


def test_58_old_save_missing_second_auction_state_loads_not_started(isolated_saves) -> None:
    session = new_ready_session()
    session.start_second_auction()
    path = ps.session_save_path(session.mode, session.session_id)
    raw = ps.build_snapshot(session, updated_at="2026-01-01T00:00:00+00:00")
    del raw["second_auction_state"]  # simulate a pre-Milestone-2 save
    ps.atomic_write_json(path, raw)

    restored = ps.load_session(path)
    assert restored.second_auction_state.status == SecondAuctionStatus.NOT_STARTED
    assert restored.second_auction_state.active_rosters_by_team == {}
    # The release plan itself (Milestone 1 data) survives untouched.
    assert restored.second_auction_setup.is_confirmed is True


# ============================================================
# 59-61. initialized state / queue / current player persist
# ============================================================


def test_59_initialized_second_auction_persists(isolated_saves) -> None:
    session = new_ready_session()
    session.start_second_auction(seed=7)
    restored = ps.load_session(ps.session_save_path(session.mode, session.session_id))
    assert restored.second_auction_state.status == SecondAuctionStatus.IN_PROGRESS
    assert restored.second_auction_state.random_seed == 7


def test_60_active_queue_persists(isolated_saves) -> None:
    session = new_ready_session()
    session.start_second_auction()
    restored = ps.load_session(ps.session_save_path(session.mode, session.session_id))
    assert restored.second_auction_state.queue == session.second_auction_state.queue


def test_61_current_player_persists(isolated_saves) -> None:
    session = new_ready_session()
    session.start_second_auction()
    restored = ps.load_session(ps.session_save_path(session.mode, session.session_id))
    assert restored.second_auction_state.current_player_id == session.second_auction_state.current_player_id


# ============================================================
# 62. current bid persists — superseded by Milestone 3 (captain phone
# bidding needs a real, persisted live-bid/leading-team concept, exactly
# mirroring the first auction's Auction.current_bid/leading_team_id)
# ============================================================


def test_62_current_bid_and_leading_team_persist(isolated_saves) -> None:
    session = new_ready_session()
    session.start_second_auction()
    team = session.teams[0]
    result = session.place_second_auction_live_bid(team.id, OUTFIELD_BASE_PRICE)
    assert result.accepted is True

    restored = ps.load_session(ps.session_save_path(session.mode, session.session_id))
    assert restored.second_auction_state.current_bid == OUTFIELD_BASE_PRICE
    assert restored.second_auction_state.leading_team_id == team.id


# ============================================================
# 63-64. budgets / rosters persist
# ============================================================


def test_63_second_auction_budgets_persist(isolated_saves) -> None:
    session = new_ready_session()
    session.start_second_auction()
    team = session.teams[0]
    session.sell_second_auction_player(winning_team=team.id, sale_price=OUTFIELD_BASE_PRICE + 1)
    restored = ps.load_session(ps.session_save_path(session.mode, session.session_id))
    assert restored.second_auction_state.remaining_budgets_by_team == session.second_auction_state.remaining_budgets_by_team
    assert restored.second_auction_state.starting_budgets_by_team == session.second_auction_state.starting_budgets_by_team


def test_64_active_rosters_persist(isolated_saves) -> None:
    session = new_ready_session()
    session.start_second_auction()
    team = session.teams[0]
    session.sell_second_auction_player(winning_team=team.id, sale_price=OUTFIELD_BASE_PRICE)
    restored = ps.load_session(ps.session_save_path(session.mode, session.session_id))
    assert restored.second_auction_state.active_rosters_by_team == session.second_auction_state.active_rosters_by_team


# ============================================================
# 65-66. history / re-auction round persist
# ============================================================


def test_65_history_persists(isolated_saves) -> None:
    session = new_ready_session()
    session.start_second_auction()
    session.mark_second_auction_player_unsold()
    session.sell_second_auction_player(winning_team=session.teams[0].id, sale_price=OUTFIELD_BASE_PRICE)
    restored = ps.load_session(ps.session_save_path(session.mode, session.session_id))
    assert len(restored.second_auction_state.history) == 2
    assert [e.status for e in restored.second_auction_state.history] == ["UNSOLD", "SOLD"]


def test_66_reauction_round_persists(isolated_saves) -> None:
    session = new_ready_session()
    session.start_second_auction()
    state = session.second_auction_state
    for _ in range(len(state.queue)):
        session.mark_second_auction_player_unsold()
    assert state.round_number == 2
    restored = ps.load_session(ps.session_save_path(session.mode, session.session_id))
    assert restored.second_auction_state.round_number == 2


# ============================================================
# 67-68. COMPLETE state persists and does not reopen on reload
# ============================================================


def test_67_complete_state_persists(isolated_saves) -> None:
    session = new_ready_session()
    session.start_second_auction()
    _run_to_completion_or_blocked(session)
    assert session.second_auction_state.status == SecondAuctionStatus.COMPLETE
    restored = ps.load_session(ps.session_save_path(session.mode, session.session_id))
    assert restored.second_auction_state.status == SecondAuctionStatus.COMPLETE
    assert restored.second_auction_state.completed_at is not None


def test_68_reload_does_not_reopen_completed_second_auction(isolated_saves) -> None:
    session = new_ready_session()
    session.start_second_auction()
    _run_to_completion_or_blocked(session)
    path = ps.session_save_path(session.mode, session.session_id)

    restored_once = ps.load_session(path)
    assert restored_once.second_auction_state.status == SecondAuctionStatus.COMPLETE
    restored_twice = ps.load_session(path)
    assert restored_twice.second_auction_state.status == SecondAuctionStatus.COMPLETE
    for team in restored_twice.teams:
        assert len(restored_twice.second_auction_state.active_rosters_by_team[team.id]) == 8


# ============================================================
# 69. MOCK/LIVE isolation preserved
# ============================================================


def test_69_mock_and_live_second_auction_states_remain_isolated(isolated_saves) -> None:
    mock_session = new_ready_session(mode=SessionMode.MOCK, seed=1)
    mock_session.start_second_auction(seed=11)

    live_session = new_ready_session(mode=SessionMode.LIVE, seed=2)
    live_session.start_second_auction(seed=22)

    restored_mock = ps.load_session(ps.session_save_path(SessionMode.MOCK, mock_session.session_id))
    restored_live = ps.load_session(ps.LIVE_ACTIVE_PATH)

    assert restored_mock.second_auction_state.random_seed == 11
    assert restored_live.second_auction_state.random_seed == 22
    assert len(restored_mock.second_auction_state.released_player_ids) == 16
    assert len(restored_live.second_auction_state.released_player_ids) == 16
    assert restored_mock.second_auction_state.active_rosters_by_team != restored_live.second_auction_state.active_rosters_by_team


# ============================================================
# 70. first-auction data identical after reload
# ============================================================


def test_70_first_auction_data_identical_after_reload(isolated_saves) -> None:
    session = new_ready_session()
    budgets_before = {team.id: team.remaining_budget for team in session.teams}
    rosters_before = {team.id: list(team.roster) for team in session.teams}
    history_before = list(session.auction.history)

    session.start_second_auction()
    _run_to_completion_or_blocked(session)

    restored = ps.load_session(ps.session_save_path(session.mode, session.session_id))
    for team in restored.teams:
        assert team.remaining_budget == budgets_before[team.id]
        assert team.roster == rosters_before[team.id]
    assert len(restored.auction.history) == len(history_before)
    assert restored.auction.status == AuctionStatus.COMPLETE


# ============================================================
# Additional: interruption/resume (ticket's "Also test interruption")
# ============================================================


def test_interruption_and_resume_continues_exactly_where_it_left_off(isolated_saves) -> None:
    session = new_ready_session()
    session.start_second_auction(seed=33)
    for _ in range(3):
        state = session.second_auction_state
        players_by_id = {player.id: player for player in session.players}
        current_player = players_by_id[state.current_player_id]
        team = next(t for t in session.teams if sa_team_can_bid_for_player(state, t, current_player))
        session.sell_second_auction_player(winning_team=team.id, sale_price=player_base_price(current_player))

    path = ps.session_save_path(session.mode, session.session_id)
    history_len_before = len(session.second_auction_state.history)
    del session

    reopened = ps.load_session(path)
    reopened.autosave = ps.autosave_hook
    assert len(reopened.second_auction_state.history) == history_len_before
    _run_to_completion_or_blocked(reopened)
    assert reopened.second_auction_state.status == SecondAuctionStatus.COMPLETE
    assert len(reopened.second_auction_state.history) >= 16


# ============================================================
# Session-level gating (not explicitly numbered, but required by the
# "First Auction Immutability Test" / safety sections)
# ============================================================


def test_cannot_unlock_release_plan_once_second_auction_started(isolated_saves) -> None:
    from services.second_auction_service import SecondAuctionError

    session = new_ready_session()
    session.start_second_auction()
    with pytest.raises(SecondAuctionError, match="cannot be edited once the second auction has started"):
        session.unlock_second_auction_release_plan()


def test_cannot_start_second_auction_twice(isolated_saves) -> None:
    from services.second_auction_bidding_service import SecondAuctionBiddingError

    session = new_ready_session()
    session.start_second_auction()
    with pytest.raises(SecondAuctionBiddingError, match="already been started"):
        session.start_second_auction()
