"""Reports screen: read-only auction summary/analytics dashboard.

GUI creation is best-effort via the shared `hidden_root` fixture (see
tests/conftest.py) — skipped, not failed, without a real display/Tk
backend. Most calculation correctness lives in
tests/test_report_service.py; this file focuses on screen-level behavior:
empty states, session switching, persistence recovery, and the
read-only/no-crash guarantees across IN_PROGRESS/COMPLETE/BLOCKED.
"""

from __future__ import annotations

import copy

import pytest

from models.auction import AuctionStatus
from services.auction_service import team_can_bid_for_player
from services.auction_session_service import AuctionSession, SessionMode


def team_by_name(teams, name: str):
    return next(team for team in teams if team.name == name)


def pick_eligible_team(player, teams, players):
    eligible = [team for team in teams if team_can_bid_for_player(team, player, players)]
    if not eligible:
        return None
    return min(eligible, key=lambda team: team.roster_size)


def sell_or_unsold(session: AuctionSession, price: int = 5):
    player = session.current_player
    team = pick_eligible_team(player, session.teams, session.players)
    if team is None:
        return session.mark_current_player_unsold()
    return session.sell_current_player(winning_team=team.id, sale_price=price)


def build_screen(hidden_root, session_obj):
    from ui.screens.reports_screen import ReportsScreen

    return ReportsScreen(hidden_root, session_obj)


def _find_text(widget, substring: str):
    for child in widget.winfo_children():
        try:
            text = child.cget("text")
            if isinstance(text, str) and substring in text:
                return child
        except Exception:
            pass
        found = _find_text(child, substring)
        if found is not None:
            return found
    return None


@pytest.fixture
def started_session() -> AuctionSession:
    session = AuctionSession()
    session.start(seed=1)
    return session


# ============================================================
# 1-2. EMPTY STATES
# ============================================================


def test_no_active_session_shows_empty_state(hidden_root) -> None:
    screen = build_screen(hidden_root, None)
    try:
        assert _find_text(screen, "NO ACTIVE AUCTION SESSION") is not None
    finally:
        screen.destroy()


def test_session_object_not_started_shows_empty_state(hidden_root) -> None:
    screen = build_screen(hidden_root, AuctionSession())
    try:
        assert _find_text(screen, "NO ACTIVE AUCTION SESSION") is not None
    finally:
        screen.destroy()


def test_zero_transaction_session_renders_without_crash(hidden_root, started_session) -> None:
    screen = build_screen(hidden_root, started_session)
    try:
        assert screen._report.summary.sold_count == 0
    finally:
        screen.destroy()


# ============================================================
# RENDERING WITH REAL TRANSACTIONS
# ============================================================


def test_screen_reflects_a_sale(hidden_root, started_session) -> None:
    blackout = team_by_name(started_session.teams, "Blackout FC")
    started_session.sell_current_player(winning_team=blackout.id, sale_price=15)

    screen = build_screen(hidden_root, started_session)
    try:
        assert screen._report.summary.sold_count == 1
        assert screen._report.summary.price_stats.total_spent == 15
    finally:
        screen.destroy()


def test_screen_reflects_team_spend(hidden_root, started_session) -> None:
    blackout = team_by_name(started_session.teams, "Blackout FC")
    started_session.sell_current_player(winning_team=blackout.id, sale_price=15)

    screen = build_screen(hidden_root, started_session)
    try:
        blackout_report = next(t for t in screen._report.team_reports if t.team.name == "Blackout FC")
        assert blackout_report.total_spent == 15
    finally:
        screen.destroy()


# ============================================================
# IN_PROGRESS / COMPLETE / BLOCKED
# ============================================================


def test_in_progress_report_is_readable(hidden_root, started_session) -> None:
    started_session.mark_current_player_unsold()
    screen = build_screen(hidden_root, started_session)
    try:
        assert started_session.auction.status == AuctionStatus.IN_PROGRESS
        assert screen._report is not None
    finally:
        screen.destroy()


def test_complete_report_is_readable(hidden_root) -> None:
    session = AuctionSession()
    session.start(seed=1)
    guard = 0
    while not session.is_complete and not session.is_blocked and guard < 200:
        sell_or_unsold(session)
        guard += 1
    assert session.is_complete

    screen = build_screen(hidden_root, session)
    try:
        assert screen._report.summary.sold_count == 28
        for team_report in screen._report.team_reports:
            assert team_report.squad_size == 8
    finally:
        screen.destroy()


def test_blocked_report_is_readable(hidden_root) -> None:
    session = AuctionSession()
    session.start(seed=7)  # deterministically reaches BLOCKED (see test_auction_history_screen.py)
    guard = 0
    while session.auction.status != AuctionStatus.BLOCKED and guard < 200:
        assert not session.is_complete
        sell_or_unsold(session)
        guard += 1
    assert session.auction.status == AuctionStatus.BLOCKED

    screen = build_screen(hidden_root, session)
    try:
        assert _find_text(screen, "BLOCKED") is not None
        assert screen._report is not None
    finally:
        screen.destroy()


# ============================================================
# SESSION SWITCHING / PERSISTENCE RECOVERY
# ============================================================


def test_switching_session_refreshes_report(hidden_root) -> None:
    session_a = AuctionSession()
    session_a.start(seed=1)
    blackout = team_by_name(session_a.teams, "Blackout FC")
    session_a.sell_current_player(winning_team=blackout.id, sale_price=15)

    session_b = AuctionSession()
    session_b.start(seed=2)

    screen_a = build_screen(hidden_root, session_a)
    screen_b = build_screen(hidden_root, session_b)
    try:
        assert screen_a._report.summary.sold_count == 1
        assert screen_b._report.summary.sold_count == 0
    finally:
        screen_a.destroy()
        screen_b.destroy()


def test_resumed_saved_session_shows_identical_report(hidden_root, tmp_path, monkeypatch) -> None:
    from services import persistence_service as ps

    monkeypatch.setattr(ps, "SAVES_DIR", tmp_path)
    monkeypatch.setattr(ps, "MOCKS_DIR", tmp_path / "mocks")
    monkeypatch.setattr(ps, "LIVE_DIR", tmp_path / "live")
    monkeypatch.setattr(ps, "LIVE_ACTIVE_PATH", tmp_path / "live" / "live_active.json")

    original = AuctionSession()
    original.start(seed=1, mode=SessionMode.MOCK, name="Report Recovery Test")
    blackout = team_by_name(original.teams, "Blackout FC")
    original.sell_current_player(winning_team=blackout.id, sale_price=9)
    original.mark_current_player_unsold()

    save_path = ps.save_session(original)
    restored = ps.load_session(save_path)

    screen_original = build_screen(hidden_root, original)
    screen_restored = build_screen(hidden_root, restored)
    try:
        assert screen_original._report.summary.sold_count == screen_restored._report.summary.sold_count
        assert (
            screen_original._report.summary.price_stats.total_spent
            == screen_restored._report.summary.price_stats.total_spent
        )
        original_spends = [t.total_spent for t in screen_original._report.team_reports]
        restored_spends = [t.total_spent for t in screen_restored._report.team_reports]
        assert original_spends == restored_spends
    finally:
        screen_original.destroy()
        screen_restored.destroy()


def test_metrics_update_immediately_after_a_new_sale(hidden_root, tmp_path, monkeypatch) -> None:
    from services import persistence_service as ps

    monkeypatch.setattr(ps, "SAVES_DIR", tmp_path)
    monkeypatch.setattr(ps, "MOCKS_DIR", tmp_path / "mocks")
    monkeypatch.setattr(ps, "LIVE_DIR", tmp_path / "live")
    monkeypatch.setattr(ps, "LIVE_ACTIVE_PATH", tmp_path / "live" / "live_active.json")

    session = AuctionSession()
    session.start(seed=1, mode=SessionMode.MOCK)
    blackout = team_by_name(session.teams, "Blackout FC")
    session.sell_current_player(winning_team=blackout.id, sale_price=9)

    screen_before = build_screen(hidden_root, session)
    sold_before = screen_before._report.summary.sold_count
    screen_before.destroy()

    darkstar = team_by_name(session.teams, "Darkstar FC")
    session.sell_current_player(winning_team=darkstar.id, sale_price=11)

    screen_after = build_screen(hidden_root, session)
    try:
        assert screen_after._report.summary.sold_count == sold_before + 1
        assert screen_after._report.summary.price_stats.total_spent == 20
    finally:
        screen_after.destroy()


# ============================================================
# READ-ONLY GUARANTEE
# ============================================================


def test_screen_does_not_mutate_auction_session(hidden_root, started_session) -> None:
    blackout = team_by_name(started_session.teams, "Blackout FC")
    started_session.sell_current_player(winning_team=blackout.id, sale_price=15)
    started_session.mark_current_player_unsold()

    history_snapshot = copy.deepcopy(started_session.auction.history)
    teams_snapshot = copy.deepcopy(started_session.teams)
    players_snapshot = copy.deepcopy(started_session.players)

    screen = build_screen(hidden_root, started_session)
    screen.destroy()
    screen2 = build_screen(hidden_root, started_session)
    screen2.destroy()

    assert started_session.auction.history == history_snapshot
    assert started_session.teams == teams_snapshot
    assert started_session.players == players_snapshot
