"""tests/test_second_auction_session.py — AuctionSession's second-auction
release-plan methods (gating, autosave) and their persistence round-trip.
Numbered to match the ticket's own "TESTING — PERSISTENCE" list (33-43)
plus the session-level gating cases from "TESTING — RELEASE RULES"
(3-6).
"""
import pytest

from models.auction import AuctionStatus
from services import persistence_service as ps
from services.auction_service import team_can_bid_for_player
from services.auction_session_service import AuctionSession, SessionMode
from services.match_result_service import build_all_ledgers
from services.second_auction_service import SecondAuctionError


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


def team_by_name(teams, name: str):
    return next(team for team in teams if team.name == name)


def pick_eligible_team(player, teams, players):
    eligible = [team for team in teams if team_can_bid_for_player(team, player, players)]
    if not eligible:
        return None
    return min(eligible, key=lambda team: team.roster_size)


def _complete_a_fresh_auction(session: AuctionSession, seed: int = 1) -> None:
    from models.player import player_base_price

    guard = 0
    while session.auction.status not in (AuctionStatus.COMPLETE, AuctionStatus.BLOCKED) and guard < 300:
        guard += 1
        current = session.current_player
        team = pick_eligible_team(current, session.teams, session.players)
        if team is None:
            session.mark_current_player_unsold()
        else:
            session.sell_current_player(winning_team=team.id, sale_price=player_base_price(current))
    assert session.auction.status == AuctionStatus.COMPLETE, "expected this seed to complete cleanly"


def new_completed_session(mode: SessionMode = SessionMode.MOCK, seed: int = 1) -> AuctionSession:
    session = AuctionSession()
    session.autosave = ps.autosave_hook
    session.start(seed=seed, mode=mode)
    _complete_a_fresh_auction(session, seed=seed)
    return session


def _non_locked_roster_ids(team, players_by_id) -> list[int]:
    from services.second_auction_service import is_locked_player

    return [pid for pid in team.roster if pid in players_by_id and not is_locked_player(players_by_id[pid])]


# ============================================================
# 3-6. gating (session level)
# ============================================================


def test_3_completed_auction_enables_setup(isolated_saves) -> None:
    session = new_completed_session()
    players_by_id = {p.id: p for p in session.players}
    team = session.teams[0]
    player_id = _non_locked_roster_ids(team, players_by_id)[0]
    session.toggle_second_auction_release(team.id, player_id)  # must not raise
    assert player_id in session.second_auction_setup.selections_for(team.id)


def test_4_incomplete_auction_blocks_setup() -> None:
    session = AuctionSession()
    session.autosave = None
    session.start(seed=1)
    assert session.auction.status == AuctionStatus.IN_PROGRESS
    with pytest.raises(SecondAuctionError, match="completed first auction"):
        session.toggle_second_auction_release(session.teams[0].id, session.teams[0].roster[0])


def test_5_blocked_auction_blocks_setup() -> None:
    session = AuctionSession()
    session.autosave = None
    session.start(seed=1)
    session.auction.status = AuctionStatus.BLOCKED
    with pytest.raises(SecondAuctionError, match="completed first auction"):
        session.toggle_second_auction_release(session.teams[0].id, session.teams[0].roster[0])


def test_6_no_session_state_handled() -> None:
    session = AuctionSession()
    with pytest.raises(SecondAuctionError, match="No auction session exists"):
        session.toggle_second_auction_release(1, 1)
    with pytest.raises(SecondAuctionError, match="No auction session exists"):
        session.confirm_second_auction_release_plan()
    with pytest.raises(SecondAuctionError, match="No auction session exists"):
        session.unlock_second_auction_release_plan()


# ============================================================
# 33. old save loads with empty second-auction setup
# ============================================================


def test_33_old_save_missing_second_auction_setup_loads_empty_draft(isolated_saves) -> None:
    session = new_completed_session()
    path = ps.session_save_path(session.mode, session.session_id)
    raw = ps.build_snapshot(session, updated_at="2026-01-01T00:00:00+00:00")
    del raw["second_auction_setup"]  # simulate a pre-existing save from before this feature
    ps.atomic_write_json(path, raw)

    restored = ps.load_session(path)
    assert restored.second_auction_setup.is_confirmed is False
    assert restored.second_auction_setup.released_player_ids_by_team == {}


# ============================================================
# 34-35. draft / confirmed release plan persist
# ============================================================


def test_34_draft_selections_persist(isolated_saves) -> None:
    session = new_completed_session()
    players_by_id = {p.id: p for p in session.players}
    team = session.teams[0]
    releasable = _non_locked_roster_ids(team, players_by_id)[:2]
    for player_id in releasable:
        session.toggle_second_auction_release(team.id, player_id)

    restored = ps.load_session(ps.session_save_path(session.mode, session.session_id))
    assert sorted(restored.second_auction_setup.selections_for(team.id)) == sorted(releasable)
    assert restored.second_auction_setup.is_confirmed is False


def test_35_confirmed_release_plan_persists(isolated_saves) -> None:
    session = new_completed_session()
    players_by_id = {p.id: p for p in session.players}
    for team in session.teams:
        for player_id in _non_locked_roster_ids(team, players_by_id)[:4]:
            session.toggle_second_auction_release(team.id, player_id)
    session.confirm_second_auction_release_plan()

    restored = ps.load_session(ps.session_save_path(session.mode, session.session_id))
    assert restored.second_auction_setup.is_confirmed is True
    assert restored.second_auction_setup.confirmed_at is not None


# ============================================================
# 36-37. close/reopen retains plan; confirmed stays confirmed
# ============================================================


def test_36_close_reopen_same_mock_retains_release_plan(isolated_saves) -> None:
    session = new_completed_session()
    players_by_id = {p.id: p for p in session.players}
    team = session.teams[0]
    selections = _non_locked_roster_ids(team, players_by_id)[:4]
    for player_id in selections:
        session.toggle_second_auction_release(team.id, player_id)

    path = ps.session_save_path(session.mode, session.session_id)
    del session
    reopened = ps.load_session(path)
    assert sorted(reopened.second_auction_setup.selections_for(team.id)) == sorted(selections)


def test_37_confirmed_state_remains_confirmed_after_reload(isolated_saves) -> None:
    session = new_completed_session()
    players_by_id = {p.id: p for p in session.players}
    for team in session.teams:
        for player_id in _non_locked_roster_ids(team, players_by_id)[:4]:
            session.toggle_second_auction_release(team.id, player_id)
    session.confirm_second_auction_release_plan()
    path = ps.session_save_path(session.mode, session.session_id)

    reopened = ps.load_session(path)
    assert reopened.second_auction_setup.is_confirmed is True
    reopened_again = ps.load_session(path)
    assert reopened_again.second_auction_setup.is_confirmed is True


# ============================================================
# 38. edit-release-plan unlocks safely
# ============================================================


def test_38_edit_release_plan_unlocks_safely(isolated_saves) -> None:
    session = new_completed_session()
    players_by_id = {p.id: p for p in session.players}
    for team in session.teams:
        for player_id in _non_locked_roster_ids(team, players_by_id)[:4]:
            session.toggle_second_auction_release(team.id, player_id)
    session.confirm_second_auction_release_plan()

    session.unlock_second_auction_release_plan()
    assert session.second_auction_setup.is_confirmed is False

    restored = ps.load_session(ps.session_save_path(session.mode, session.session_id))
    assert restored.second_auction_setup.is_confirmed is False
    # Selections themselves survive the unlock.
    team = session.teams[0]
    assert len(restored.second_auction_setup.selections_for(team.id)) == 4


# ============================================================
# 39. first auction status remains COMPLETE
# ============================================================


def test_39_first_auction_status_remains_complete(isolated_saves) -> None:
    session = new_completed_session()
    players_by_id = {p.id: p for p in session.players}
    for team in session.teams:
        for player_id in _non_locked_roster_ids(team, players_by_id)[:4]:
            session.toggle_second_auction_release(team.id, player_id)
    session.confirm_second_auction_release_plan()
    assert session.auction.status == AuctionStatus.COMPLETE

    restored = ps.load_session(ps.session_save_path(session.mode, session.session_id))
    assert restored.auction.status == AuctionStatus.COMPLETE


# ============================================================
# 40. MOCK/LIVE isolation preserved
# ============================================================


def test_40_mock_and_live_release_plans_remain_isolated(isolated_saves) -> None:
    mock_session = new_completed_session(mode=SessionMode.MOCK, seed=1)
    live_session = new_completed_session(mode=SessionMode.LIVE, seed=2)

    mock_players_by_id = {p.id: p for p in mock_session.players}
    mock_team = mock_session.teams[0]
    for player_id in _non_locked_roster_ids(mock_team, mock_players_by_id)[:4]:
        mock_session.toggle_second_auction_release(mock_team.id, player_id)

    live_players_by_id = {p.id: p for p in live_session.players}
    live_team = live_session.teams[1]
    for player_id in _non_locked_roster_ids(live_team, live_players_by_id)[:4]:
        live_session.toggle_second_auction_release(live_team.id, player_id)

    restored_mock = ps.load_session(ps.session_save_path(SessionMode.MOCK, mock_session.session_id))
    restored_live = ps.load_session(ps.LIVE_ACTIVE_PATH)

    assert len(restored_mock.second_auction_setup.selections_for(mock_team.id)) == 4
    assert restored_mock.second_auction_setup.selections_for(live_team.id) == []
    assert len(restored_live.second_auction_setup.selections_for(live_team.id)) == 4
    assert restored_live.second_auction_setup.selections_for(mock_team.id) == []


# ============================================================
# 41-42. existing match results / auction history persist
# ============================================================


def test_41_existing_match_results_persist_alongside_release_plan(isolated_saves) -> None:
    session = new_completed_session()
    blackout = team_by_name(session.teams, "Blackout FC")
    darkstar = team_by_name(session.teams, "Darkstar FC")
    session.add_match_result(1, blackout.id, darkstar.id, 3, 1)

    players_by_id = {p.id: p for p in session.players}
    for player_id in _non_locked_roster_ids(blackout, players_by_id)[:4]:
        session.toggle_second_auction_release(blackout.id, player_id)

    restored = ps.load_session(ps.session_save_path(session.mode, session.session_id))
    assert len(restored.match_results) == 1
    assert len(restored.second_auction_setup.selections_for(blackout.id)) == 4


def test_42_existing_auction_history_persists_alongside_release_plan(isolated_saves) -> None:
    session = new_completed_session()
    history_before = list(session.auction.history)
    players_by_id = {p.id: p for p in session.players}
    team = session.teams[0]
    for player_id in _non_locked_roster_ids(team, players_by_id)[:4]:
        session.toggle_second_auction_release(team.id, player_id)

    restored = ps.load_session(ps.session_save_path(session.mode, session.session_id))
    assert len(restored.auction.history) == len(history_before)


# ============================================================
# 43. current budgets recalculate correctly after reload
# ============================================================


def test_43_current_budgets_recalculate_correctly_after_reload(isolated_saves) -> None:
    session = new_completed_session()
    blackout = team_by_name(session.teams, "Blackout FC")
    darkstar = team_by_name(session.teams, "Darkstar FC")
    session.add_match_result(1, blackout.id, darkstar.id, 3, 1)

    restored = ps.load_session(ps.session_save_path(session.mode, session.session_id))
    restored_blackout = team_by_name(restored.teams, "Blackout FC")
    ledgers = {ledger.team.id: ledger for ledger in build_all_ledgers(restored.teams, restored.match_results)}
    assert ledgers[restored_blackout.id].current_transfer_budget == restored_blackout.remaining_budget + 4
