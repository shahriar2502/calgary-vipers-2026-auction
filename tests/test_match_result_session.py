"""tests/test_match_result_session.py — AuctionSession's match-result
methods (gating, autosave) and their persistence round-trip. Numbered to
match the ticket's own "TESTING — SESSION/PERSISTENCE" list (28-41).
"""
import pytest

from models.auction import AuctionStatus
from services import persistence_service as ps
from services.auction_service import AuctionTransactionError, team_can_bid_for_player
from services.auction_session_service import AuctionSession, SessionMode
from services.match_result_service import MatchResultError
from services.player_service import load_players, load_teams
from services.randomization_service import create_auction


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
    """Drive `session` (already `.start()`ed) all the way to COMPLETE using
    each current player's own base price -- identical technique to every
    other completed-auction test added for First Auction Rules V2."""
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


# ============================================================
# 28. old saves missing match data load
# ============================================================


def test_28_old_save_missing_match_data_loads_with_empty_list(isolated_saves) -> None:
    session = new_completed_session()
    path = ps.session_save_path(session.mode, session.session_id)
    raw = ps.build_snapshot(session, updated_at="2026-01-01T00:00:00+00:00")
    del raw["match_results"]  # simulate a pre-existing save from before this feature
    ps.atomic_write_json(path, raw)

    restored = ps.load_session(path)
    assert restored.match_results == []


# ============================================================
# 29-30. completed MOCK / LIVE can accept results
# ============================================================


def test_29_completed_mock_can_accept_results(isolated_saves) -> None:
    session = new_completed_session(mode=SessionMode.MOCK)
    blackout = team_by_name(session.teams, "Blackout FC")
    darkstar = team_by_name(session.teams, "Darkstar FC")
    result = session.add_match_result(1, blackout.id, darkstar.id, 3, 1)
    assert result in session.match_results


def test_30_completed_live_can_accept_results(isolated_saves) -> None:
    session = new_completed_session(mode=SessionMode.LIVE)
    blackout = team_by_name(session.teams, "Blackout FC")
    darkstar = team_by_name(session.teams, "Darkstar FC")
    result = session.add_match_result(1, blackout.id, darkstar.id, 3, 1)
    assert result in session.match_results


# ============================================================
# 31-32. incomplete / blocked auctions cannot accept official results
# ============================================================


def test_31_incomplete_auction_cannot_accept_official_results() -> None:
    session = AuctionSession()
    session.autosave = None
    session.start(seed=1)
    assert session.auction.status == AuctionStatus.IN_PROGRESS
    with pytest.raises(MatchResultError, match="completed first auction"):
        session.add_match_result(1, session.teams[0].id, session.teams[1].id, 1, 0)


def test_32_blocked_auction_cannot_accept_results() -> None:
    session = AuctionSession()
    session.autosave = None
    session.start(seed=1)
    session.auction.status = AuctionStatus.BLOCKED
    with pytest.raises(MatchResultError, match="completed first auction"):
        session.add_match_result(1, session.teams[0].id, session.teams[1].id, 1, 0)


def test_no_session_cannot_accept_results() -> None:
    session = AuctionSession()
    with pytest.raises(MatchResultError, match="No auction session exists"):
        session.add_match_result(1, 1, 2, 1, 0)


# ============================================================
# 33. match results persist across app restart
# ============================================================


def test_33_match_results_persist_across_restart(isolated_saves) -> None:
    session = new_completed_session()
    blackout = team_by_name(session.teams, "Blackout FC")
    darkstar = team_by_name(session.teams, "Darkstar FC")
    session.add_match_result(1, blackout.id, darkstar.id, 3, 1)

    path = ps.session_save_path(session.mode, session.session_id)
    restored = ps.load_session(path)
    assert len(restored.match_results) == 1
    assert restored.match_results[0].team1_goals == 3
    assert restored.match_results[0].team2_goals == 1


# ============================================================
# 34. completed LIVE reopens in post-auction mode
# ============================================================


def test_34_completed_live_reopens_in_post_auction_mode(isolated_saves) -> None:
    session = new_completed_session(mode=SessionMode.LIVE)
    path = ps.session_save_path(session.mode, session.session_id)

    reopened = ps.load_session(path)
    assert reopened.is_complete is True
    # The reopened session accepts match results exactly like the original.
    blackout = team_by_name(reopened.teams, "Blackout FC")
    darkstar = team_by_name(reopened.teams, "Darkstar FC")
    reopened.autosave = ps.autosave_hook
    result = reopened.add_match_result(1, blackout.id, darkstar.id, 2, 0)
    assert result in reopened.match_results


# ============================================================
# 35-36. completed auction stays COMPLETE / no bidding after completion
# ============================================================


def test_35_completed_auction_stays_complete_after_match_entry(isolated_saves) -> None:
    session = new_completed_session()
    blackout = team_by_name(session.teams, "Blackout FC")
    darkstar = team_by_name(session.teams, "Darkstar FC")
    session.add_match_result(1, blackout.id, darkstar.id, 3, 1)
    assert session.auction.status == AuctionStatus.COMPLETE


def test_36_no_bidding_after_completion(isolated_saves) -> None:
    session = new_completed_session()
    blackout = team_by_name(session.teams, "Blackout FC")
    with pytest.raises(AuctionTransactionError, match="already complete"):
        session.sell_current_player(winning_team=blackout.id, sale_price=2)
    with pytest.raises(AuctionTransactionError, match="already complete"):
        session.mark_current_player_unsold()
    bid_result = session.place_live_bid(blackout.id, 2)
    assert bid_result.accepted is False


# ============================================================
# 37. MOCK/LIVE data remain isolated
# ============================================================


def test_37_mock_and_live_match_results_remain_isolated(isolated_saves) -> None:
    mock_session = new_completed_session(mode=SessionMode.MOCK, seed=1)
    live_session = new_completed_session(mode=SessionMode.LIVE, seed=2)

    mock_blackout = team_by_name(mock_session.teams, "Blackout FC")
    mock_darkstar = team_by_name(mock_session.teams, "Darkstar FC")
    mock_session.add_match_result(1, mock_blackout.id, mock_darkstar.id, 5, 0)

    live_goli = team_by_name(live_session.teams, "Goli Underdogs")
    live_showstoppers = team_by_name(live_session.teams, "Showstoppers")
    live_session.add_match_result(1, live_goli.id, live_showstoppers.id, 1, 1)

    restored_mock = ps.load_session(ps.session_save_path(SessionMode.MOCK, mock_session.session_id))
    restored_live = ps.load_session(ps.LIVE_ACTIVE_PATH)

    assert len(restored_mock.match_results) == 1
    assert restored_mock.match_results[0].team1_goals == 5
    assert len(restored_live.match_results) == 1
    assert restored_live.match_results[0].team1_goals == 1
    assert restored_mock.match_results[0].id != restored_live.match_results[0].id


# ============================================================
# 38-39. successful edit/deletion autosaves
# ============================================================


def test_38_successful_edit_autosaves(isolated_saves) -> None:
    session = new_completed_session()
    blackout = team_by_name(session.teams, "Blackout FC")
    darkstar = team_by_name(session.teams, "Darkstar FC")
    match = session.add_match_result(1, blackout.id, darkstar.id, 3, 1)

    session.update_match_result(match.id, 1, blackout.id, darkstar.id, 1, 1)

    restored = ps.load_session(ps.session_save_path(session.mode, session.session_id))
    assert restored.match_results[0].team1_goals == 1
    assert restored.match_results[0].team2_goals == 1


def test_39_successful_deletion_autosaves(isolated_saves) -> None:
    session = new_completed_session()
    blackout = team_by_name(session.teams, "Blackout FC")
    darkstar = team_by_name(session.teams, "Darkstar FC")
    match = session.add_match_result(1, blackout.id, darkstar.id, 3, 1)

    session.delete_match_result(match.id)

    restored = ps.load_session(ps.session_save_path(session.mode, session.session_id))
    assert restored.match_results == []


# ============================================================
# 40. save failure handled safely
# ============================================================


def test_40_save_failure_handled_safely() -> None:
    session = AuctionSession()
    session.autosave = None
    session.start(seed=1)
    _complete_a_fresh_auction(session)

    def _failing_autosave(_session: AuctionSession) -> None:
        raise OSError("simulated disk failure")

    session.autosave = _failing_autosave
    blackout = team_by_name(session.teams, "Blackout FC")
    darkstar = team_by_name(session.teams, "Darkstar FC")

    # The in-memory result is never silently dropped even though the save failed.
    result = session.add_match_result(1, blackout.id, darkstar.id, 3, 1)
    assert result in session.match_results
    assert session.last_save_error is not None
    assert "simulated disk failure" in session.last_save_error


# ============================================================
# 41. existing unfinished LIVE protection unchanged
# ============================================================


def test_41_unfinished_live_protection_unchanged(isolated_saves) -> None:
    """Adding match_results to the snapshot must not weaken the existing
    guarantee that a MOCK session is never written to the LIVE slot, and
    that an in-progress LIVE save is still detected before being replaced."""
    live_session = AuctionSession()
    live_session.autosave = ps.autosave_hook
    live_session.start(seed=1, mode=SessionMode.LIVE)  # still IN_PROGRESS

    assert ps.detect_active_live_session() is not None

    mock_session = AuctionSession()
    mock_session.autosave = ps.autosave_hook
    mock_session.start(seed=2, mode=SessionMode.MOCK)
    mock_path = ps.session_save_path(mock_session.mode, mock_session.session_id)
    assert mock_path != ps.LIVE_ACTIVE_PATH
    assert mock_path.parent == ps.MOCKS_DIR
