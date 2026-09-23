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

from models.auction import Auction, AuctionHistoryEntry, AuctionStatus
from models.player import Player, PlayerAuctionStatus, Position, player_base_price
from models.team import Team
from services.auction_service import process_unsold, team_can_bid_for_player
from services.auction_session_service import AuctionSession, SessionMode


def team_by_name(teams, name: str):
    return next(team for team in teams if team.name == name)


def pick_eligible_team(player, teams, players):
    eligible = [team for team in teams if team_can_bid_for_player(team, player, players)]
    if not eligible:
        return None
    return min(eligible, key=lambda team: team.roster_size)


def build_synthetic_blocked_session() -> AuctionSession:
    """A minimal, deterministic BLOCKED scenario (see the identical helper
    in tests/test_auction_history_screen.py for the full rationale: real
    28-player canonical data can no longer reliably reach BLOCKED under
    First Auction Rules V2's mandatory-goalkeeper-completion rule)."""
    captains = [
        Player(
            id=i + 1, full_name=f"Cap{i + 1}", short_name=f"C{i + 1}", position=Position.ATT, overall_rating=80,
            is_captain=True, assigned_team=f"Team {chr(65 + i)}", auction_eligible=False,
            auction_status=PlayerAuctionStatus.PRE_ASSIGNED,
        )
        for i in range(4)
    ]
    teams = [
        Team(id=i + 1, name=f"Team {chr(65 + i)}", short_name=f"T{i + 1}", captain_player_id=captains[i].id, captain_name=captains[i].full_name)
        for i in range(4)
    ]

    auction = Auction(queue=[999], status=AuctionStatus.IN_PROGRESS)
    all_players = list(captains)
    sequence = 1
    for i in range(3):
        gk = Player(
            id=100 + i, full_name=f"GK{i}", short_name=f"GK{i}", position=Position.GK, overall_rating=80,
            auction_status=PlayerAuctionStatus.SOLD, sold_to=teams[i].name, sold_price=4, auction_sequence=sequence,
        )
        sequence += 1
        fillers = []
        for j in range(6):
            filler = Player(
                id=200 + i * 10 + j, full_name=f"F{i}_{j}", short_name=f"F{i}_{j}", position=Position.DEF,
                overall_rating=80, auction_status=PlayerAuctionStatus.SOLD, sold_to=teams[i].name, sold_price=2,
                auction_sequence=sequence,
            )
            sequence += 1
            fillers.append(filler)
        all_players.extend([gk] + fillers)
        teams[i].roster = [captains[i].id, gk.id] + [f.id for f in fillers]
        teams[i].auction_spending = 4 + 6 * 2
        teams[i].remaining_budget = 100 - teams[i].auction_spending
        teams[i].players_purchased = 7
        for player in [gk] + fillers:
            auction.history.append(
                AuctionHistoryEntry(
                    auction_sequence=player.auction_sequence, player_id=player.id, player_name=player.full_name,
                    position=player.position.value, overall_rating=player.overall_rating, base_price=player.sold_price,
                    status="SOLD", team=player.sold_to, sold_price=player.sold_price, round_number=1,
                )
            )

    stuck_fillers = []
    for j in range(6):
        filler = Player(
            id=900 + j, full_name=f"SF{j}", short_name=f"SF{j}", position=Position.DEF, overall_rating=80,
            auction_status=PlayerAuctionStatus.SOLD, sold_to=teams[3].name, sold_price=2, auction_sequence=sequence,
        )
        sequence += 1
        stuck_fillers.append(filler)
        auction.history.append(
            AuctionHistoryEntry(
                auction_sequence=filler.auction_sequence, player_id=filler.id, player_name=filler.full_name,
                position=filler.position.value, overall_rating=filler.overall_rating, base_price=filler.sold_price,
                status="SOLD", team=filler.sold_to, sold_price=filler.sold_price, round_number=1,
            )
        )
    all_players.extend(stuck_fillers)
    teams[3].roster = [captains[3].id] + [f.id for f in stuck_fillers]
    teams[3].auction_spending = 6 * 2
    teams[3].remaining_budget = 100 - teams[3].auction_spending
    teams[3].players_purchased = 6

    stuck_player = Player(id=999, full_name="Stuck Player", short_name="Stuck", position=Position.DEF, overall_rating=80)
    all_players.append(stuck_player)

    session = AuctionSession()
    session.players = all_players
    session.teams = teams
    session.auction = auction
    session.mode = SessionMode.MOCK
    session.session_id = "synthetic_blocked"
    session.created_at = "2026-01-01T00:00:00+00:00"

    result = process_unsold(auction, all_players, teams)
    assert result.auction_status == AuctionStatus.BLOCKED
    return session


def sell_or_unsold(session: AuctionSession, price: int | None = None):
    """Sells at `price`, or (First Auction Rules V2: GK/non-GK base prices
    are no longer both satisfied by one flat fallback) the current
    player's own base price when not given explicitly."""
    player = session.current_player
    team = pick_eligible_team(player, session.teams, session.players)
    if team is None:
        return session.mark_current_player_unsold()
    sale_price = price if price is not None else player_base_price(player)
    return session.sell_current_player(winning_team=team.id, sale_price=sale_price)


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
    session = build_synthetic_blocked_session()
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
