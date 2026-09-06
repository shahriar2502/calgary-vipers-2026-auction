"""Auction History screen: read-only transaction timeline, filters,
search, sort, and summary cards for the currently loaded session.

GUI creation is best-effort via the shared `hidden_root` fixture (see
tests/conftest.py) — skipped, not failed, without a real display/Tk
backend.
"""

from __future__ import annotations

import copy

import pytest

from models.auction import AuctionStatus
from models.player import Player, Position
from models.team import Team
from services.auction_service import get_current_player, process_sale, process_unsold, team_can_bid_for_player
from services.auction_session_service import AuctionSession, SessionMode


def team_by_name(teams: list[Team], name: str) -> Team:
    return next(team for team in teams if team.name == name)


def pick_eligible_team(player: Player, teams: list[Team], players: list[Player]) -> Team | None:
    eligible = [team for team in teams if team_can_bid_for_player(team, player, players)]
    if not eligible:
        return None
    return min(eligible, key=lambda team: team.roster_size)


def sell_or_unsold(session: AuctionSession, sale_price: int = 1):
    player = session.current_player
    team = pick_eligible_team(player, session.teams, session.players)
    if team is None:
        return session.mark_current_player_unsold()
    return session.sell_current_player(winning_team=team.id, sale_price=sale_price)


def force_one_non_gk_unsold_then_resolve_round_one(session: AuctionSession) -> int:
    """Same technique as tests/test_auction_service.py's helper of the same
    name — deterministically produce a round-1 UNSOLD → round-2 re-auction
    without depending on a seed happening to produce one naturally."""
    forced_id: int | None = None
    auction = session.auction
    while auction.round_number == 1 and not session.is_complete and not session.is_blocked:
        current = session.current_player
        if forced_id is None and current.position != Position.GK:
            forced_id = current.id
            session.mark_current_player_unsold()
            continue
        sell_or_unsold(session)
    assert forced_id is not None
    return forced_id


@pytest.fixture
def session() -> AuctionSession:
    return AuctionSession()


@pytest.fixture
def started_session() -> AuctionSession:
    session = AuctionSession()
    session.start(seed=1)
    return session


def build_screen(hidden_root, session_obj: AuctionSession | None):
    from ui.screens.auction_history_screen import AuctionHistoryScreen

    return AuctionHistoryScreen(hidden_root, session_obj)


# ============================================================
# 1-2. NO SESSION / EMPTY HISTORY
# ============================================================


def test_screen_loads_with_no_active_session(hidden_root) -> None:
    screen = build_screen(hidden_root, None)
    try:
        assert _find_text(screen, "NO ACTIVE AUCTION SESSION") is not None
    finally:
        screen.destroy()


def test_screen_loads_with_a_session_object_that_has_not_started(hidden_root, session) -> None:
    screen = build_screen(hidden_root, session)
    try:
        assert _find_text(screen, "NO ACTIVE AUCTION SESSION") is not None
    finally:
        screen.destroy()


def test_new_session_with_no_history_shows_empty_state(hidden_root, started_session) -> None:
    screen = build_screen(hidden_root, started_session)
    try:
        assert _find_text(screen, "NO TRANSACTIONS YET") is not None
    finally:
        screen.destroy()


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


# ============================================================
# 3-9. ROW CONTENT / RE-AUCTION BEHAVIOR
# ============================================================


def test_sold_transaction_appears(hidden_root, started_session) -> None:
    blackout = team_by_name(started_session.teams, "Blackout FC")
    current = started_session.current_player
    started_session.sell_current_player(winning_team=blackout.id, sale_price=10)

    screen = build_screen(hidden_root, started_session)
    try:
        rows = screen._visible_rows()
        assert len(rows) == 1
        assert rows[0].player_id == current.id
        assert rows[0].status == "SOLD"
    finally:
        screen.destroy()


def test_unsold_transaction_appears(hidden_root, started_session) -> None:
    current = started_session.current_player
    started_session.mark_current_player_unsold()

    screen = build_screen(hidden_root, started_session)
    try:
        rows = screen._visible_rows()
        assert len(rows) == 1
        assert rows[0].player_id == current.id
        assert rows[0].status == "UNSOLD"
    finally:
        screen.destroy()


def test_buying_team_displays_correctly(hidden_root, started_session) -> None:
    blackout = team_by_name(started_session.teams, "Blackout FC")
    started_session.sell_current_player(winning_team=blackout.id, sale_price=12)

    screen = build_screen(hidden_root, started_session)
    try:
        assert screen._visible_rows()[0].team == "Blackout FC"
    finally:
        screen.destroy()


def test_sold_price_displays_correctly(hidden_root, started_session) -> None:
    blackout = team_by_name(started_session.teams, "Blackout FC")
    started_session.sell_current_player(winning_team=blackout.id, sale_price=17)

    screen = build_screen(hidden_root, started_session)
    try:
        assert screen._visible_rows()[0].sold_price == 17
    finally:
        screen.destroy()


def test_unsold_has_no_team_or_price(hidden_root, started_session) -> None:
    started_session.mark_current_player_unsold()

    screen = build_screen(hidden_root, started_session)
    try:
        row = screen._visible_rows()[0]
        assert row.team is None
        assert row.sold_price is None
    finally:
        screen.destroy()


def test_chronological_sequence_preserved(hidden_root, started_session) -> None:
    for _ in range(5):
        sell_or_unsold(started_session)

    screen = build_screen(hidden_root, started_session)
    try:
        sequences = [row.auction_sequence for row in screen._visible_rows()]
        assert sequences == sorted(sequences)
    finally:
        screen.destroy()


def test_multiple_attempts_for_same_player_all_appear(hidden_root, started_session) -> None:
    forced_id = force_one_non_gk_unsold_then_resolve_round_one(started_session)
    assert started_session.round_number == 2

    # Resolve round 2 until the forced player is sold.
    for _ in range(100):
        current = started_session.current_player
        if current.id == forced_id:
            team = pick_eligible_team(current, started_session.teams, started_session.players)
            started_session.sell_current_player(winning_team=team.id, sale_price=1)
            break
        sell_or_unsold(started_session)

    screen = build_screen(hidden_root, started_session)
    try:
        entries_for_player = [row for row in screen._visible_rows() if row.player_id == forced_id]
        assert [entry.status for entry in entries_for_player] == ["UNSOLD", "SOLD"]
    finally:
        screen.destroy()


def test_round_number_displays_correctly(hidden_root, started_session) -> None:
    blackout = team_by_name(started_session.teams, "Blackout FC")
    started_session.sell_current_player(winning_team=blackout.id, sale_price=5)

    screen = build_screen(hidden_root, started_session)
    try:
        assert screen._visible_rows()[0].round_number == 1
    finally:
        screen.destroy()


def test_round_2_reauction_transaction_appears(hidden_root, started_session) -> None:
    force_one_non_gk_unsold_then_resolve_round_one(started_session)
    assert started_session.round_number == 2

    # Round 2 hasn't had any transactions yet at this point — resolve one
    # and confirm it shows up tagged with round 2.
    sell_or_unsold(started_session)

    screen2 = build_screen(hidden_root, started_session)
    try:
        round_two_rows = [row for row in screen2._visible_rows() if row.round_number == 2]
        assert len(round_two_rows) == 1
    finally:
        screen2.destroy()


def test_round_3_transaction_appears_if_present(hidden_root, started_session) -> None:
    """Force a player through UNSOLD (round 1) -> UNSOLD (round 2) ->
    SOLD (round 3) and confirm the round-3 entry renders correctly."""
    forced_id = force_one_non_gk_unsold_then_resolve_round_one(started_session)
    assert started_session.round_number == 2

    # In round 2, force the SAME player unsold again, then resolve the
    # rest of round 2 normally so a round 3 starts with just that player.
    while True:
        current = started_session.current_player
        if current.id == forced_id:
            started_session.mark_current_player_unsold()
            break
        sell_or_unsold(started_session)
    while started_session.round_number == 2 and not started_session.is_complete and not started_session.is_blocked:
        sell_or_unsold(started_session)

    if started_session.round_number < 3:
        pytest.skip("This seed did not leave the forced player eligible for a third round.")

    team = pick_eligible_team(started_session.current_player, started_session.teams, started_session.players)
    assert started_session.current_player.id == forced_id
    started_session.sell_current_player(winning_team=team.id, sale_price=1)

    screen = build_screen(hidden_root, started_session)
    try:
        round_three_rows = [row for row in screen._visible_rows() if row.round_number == 3]
        assert len(round_three_rows) == 1
        assert round_three_rows[0].status == "SOLD"
    finally:
        screen.destroy()


# ============================================================
# 13-17. FILTERS
# ============================================================


def test_sold_filter(hidden_root, started_session) -> None:
    blackout = team_by_name(started_session.teams, "Blackout FC")
    started_session.sell_current_player(winning_team=blackout.id, sale_price=5)
    started_session.mark_current_player_unsold()

    screen = build_screen(hidden_root, started_session)
    try:
        screen._result_var.set("SOLD")
        screen._refresh_table()
        rows = screen._visible_rows()
        assert len(rows) == 1
        assert rows[0].status == "SOLD"
    finally:
        screen.destroy()


def test_unsold_filter(hidden_root, started_session) -> None:
    blackout = team_by_name(started_session.teams, "Blackout FC")
    started_session.sell_current_player(winning_team=blackout.id, sale_price=5)
    started_session.mark_current_player_unsold()

    screen = build_screen(hidden_root, started_session)
    try:
        screen._result_var.set("UNSOLD")
        screen._refresh_table()
        rows = screen._visible_rows()
        assert len(rows) == 1
        assert rows[0].status == "UNSOLD"
    finally:
        screen.destroy()


def test_all_filter_shows_every_row(hidden_root, started_session) -> None:
    blackout = team_by_name(started_session.teams, "Blackout FC")
    started_session.sell_current_player(winning_team=blackout.id, sale_price=5)
    started_session.mark_current_player_unsold()

    screen = build_screen(hidden_root, started_session)
    try:
        screen._result_var.set("ALL")
        screen._refresh_table()
        assert len(screen._visible_rows()) == 2
    finally:
        screen.destroy()


def test_round_filter(hidden_root, started_session) -> None:
    force_one_non_gk_unsold_then_resolve_round_one(started_session)
    assert started_session.round_number == 2

    screen = build_screen(hidden_root, started_session)
    try:
        screen._round_var.set("Round 1")
        screen._refresh_table()
        rows = screen._visible_rows()
        assert rows  # sanity
        assert all(row.round_number == 1 for row in rows)
    finally:
        screen.destroy()


def test_team_filter(hidden_root, started_session) -> None:
    blackout = team_by_name(started_session.teams, "Blackout FC")
    started_session.sell_current_player(winning_team=blackout.id, sale_price=5)
    darkstar = team_by_name(started_session.teams, "Darkstar FC")
    started_session.sell_current_player(winning_team=darkstar.id, sale_price=6)

    screen = build_screen(hidden_root, started_session)
    try:
        screen._team_var.set("Blackout FC")
        screen._refresh_table()
        rows = screen._visible_rows()
        assert len(rows) == 1
        assert rows[0].team == "Blackout FC"
    finally:
        screen.destroy()


# ============================================================
# 18-19. SEARCH
# ============================================================


def test_search_full_name(hidden_root, started_session) -> None:
    current = started_session.current_player
    started_session.mark_current_player_unsold()

    screen = build_screen(hidden_root, started_session)
    try:
        screen._search_var.set(current.full_name.split()[0].lower())
        screen._refresh_table()
        rows = screen._visible_rows()
        assert len(rows) == 1
        assert rows[0].player_id == current.id
    finally:
        screen.destroy()


def test_search_short_name(hidden_root, started_session) -> None:
    current = started_session.current_player
    started_session.mark_current_player_unsold()

    screen = build_screen(hidden_root, started_session)
    try:
        screen._search_var.set(current.short_name.lower())
        screen._refresh_table()
        rows = screen._visible_rows()
        assert len(rows) == 1
        assert rows[0].player_id == current.id
    finally:
        screen.destroy()


# ============================================================
# 20-21. SORT
# ============================================================


def test_oldest_newest_sort_is_default(hidden_root, started_session) -> None:
    for _ in range(4):
        sell_or_unsold(started_session)

    screen = build_screen(hidden_root, started_session)
    try:
        sequences = [row.auction_sequence for row in screen._visible_rows()]
        assert sequences == sorted(sequences)
    finally:
        screen.destroy()


def test_newest_oldest_sort(hidden_root, started_session) -> None:
    for _ in range(4):
        sell_or_unsold(started_session)

    screen = build_screen(hidden_root, started_session)
    try:
        screen._sort_var.set("Newest → Oldest")
        screen._refresh_table()
        sequences = [row.auction_sequence for row in screen._visible_rows()]
        assert sequences == sorted(sequences, reverse=True)
    finally:
        screen.destroy()


# ============================================================
# 22-25. SUMMARY CARDS
# ============================================================


def test_total_attempts_correct(hidden_root, started_session) -> None:
    for _ in range(4):
        sell_or_unsold(started_session)

    screen = build_screen(hidden_root, started_session)
    try:
        from services.auction_history_service import summarize_history

        summary = summarize_history(started_session.auction.history)
        assert summary.total_attempts == 4
    finally:
        screen.destroy()


def test_sold_summary_count_correct(hidden_root, started_session) -> None:
    blackout = team_by_name(started_session.teams, "Blackout FC")
    started_session.sell_current_player(winning_team=blackout.id, sale_price=5)
    started_session.mark_current_player_unsold()

    from services.auction_history_service import summarize_history

    summary = summarize_history(started_session.auction.history)
    assert summary.sold_count == 1


def test_unsold_attempt_summary_count_counts_every_attempt(hidden_root, started_session) -> None:
    forced_id = force_one_non_gk_unsold_then_resolve_round_one(started_session)
    assert started_session.round_number == 2
    # Force the SAME player unsold again in round 2 for a second attempt.
    while started_session.current_player.id != forced_id:
        sell_or_unsold(started_session)
    started_session.mark_current_player_unsold()

    from services.auction_history_service import summarize_history

    summary = summarize_history(started_session.auction.history)
    unsold_attempts_for_player = sum(
        1 for entry in started_session.auction.history if entry.player_id == forced_id and entry.status == "UNSOLD"
    )
    assert unsold_attempts_for_player == 2
    assert summary.unsold_count >= 2


def test_total_spent_correct(hidden_root, started_session) -> None:
    blackout = team_by_name(started_session.teams, "Blackout FC")
    darkstar = team_by_name(started_session.teams, "Darkstar FC")
    started_session.sell_current_player(winning_team=blackout.id, sale_price=5)
    started_session.sell_current_player(winning_team=darkstar.id, sale_price=7)
    started_session.mark_current_player_unsold()

    from services.auction_history_service import summarize_history

    summary = summarize_history(started_session.auction.history)
    assert summary.total_spent == 12


# ============================================================
# 26-27. PERSISTENCE / SESSION SWITCHING
# ============================================================


def test_resumed_saved_session_shows_identical_history(hidden_root, tmp_path, monkeypatch) -> None:
    from services import persistence_service as ps

    mocks_dir = tmp_path / "mocks"
    live_dir = tmp_path / "live"
    monkeypatch.setattr(ps, "SAVES_DIR", tmp_path)
    monkeypatch.setattr(ps, "MOCKS_DIR", mocks_dir)
    monkeypatch.setattr(ps, "LIVE_DIR", live_dir)
    monkeypatch.setattr(ps, "LIVE_ACTIVE_PATH", live_dir / "live_active.json")

    original = AuctionSession()
    original.start(seed=1, mode=SessionMode.MOCK, name="History Test")
    blackout = team_by_name(original.teams, "Blackout FC")
    original.sell_current_player(winning_team=blackout.id, sale_price=9)
    original.mark_current_player_unsold()

    save_path = ps.save_session(original)
    restored = ps.load_session(save_path)

    screen_original = build_screen(hidden_root, original)
    screen_restored = build_screen(hidden_root, restored)
    try:
        original_rows = [(r.player_id, r.status, r.round_number, r.team, r.sold_price) for r in screen_original._visible_rows()]
        restored_rows = [(r.player_id, r.status, r.round_number, r.team, r.sold_price) for r in screen_restored._visible_rows()]
        assert original_rows == restored_rows
    finally:
        screen_original.destroy()
        screen_restored.destroy()


def test_switching_session_refreshes_history(hidden_root) -> None:
    session_a = AuctionSession()
    session_a.start(seed=1)
    blackout = team_by_name(session_a.teams, "Blackout FC")
    session_a.sell_current_player(winning_team=blackout.id, sale_price=5)

    session_b = AuctionSession()
    session_b.start(seed=2)

    screen_a = build_screen(hidden_root, session_a)
    screen_b = build_screen(hidden_root, session_b)
    try:
        assert len(screen_a._visible_rows()) == 1
        assert len(screen_b._visible_rows()) == 0
    finally:
        screen_a.destroy()
        screen_b.destroy()


# ============================================================
# 28-29. COMPLETE / BLOCKED SESSIONS
# ============================================================


def test_complete_session_is_readable(hidden_root, started_session) -> None:
    guard = 0
    while not started_session.is_complete and not started_session.is_blocked and guard < 200:
        sell_or_unsold(started_session)
        guard += 1

    screen = build_screen(hidden_root, started_session)
    try:
        # Must not raise, and every history entry must still be visible.
        assert len(screen._visible_rows()) == len(started_session.auction.history)
    finally:
        screen.destroy()


def test_blocked_session_is_readable(hidden_root) -> None:
    # Seed 7 deterministically reaches BLOCKED under this "fewest roster
    # size" team-selection strategy (verified directly; not every seed
    # does — most complete cleanly, which is exercised by the other tests
    # in this file).
    session = AuctionSession()
    session.start(seed=7)
    guard = 0
    while session.auction.status != AuctionStatus.BLOCKED and guard < 200:
        assert not session.is_complete, "Expected seed 7 to reach BLOCKED, not COMPLETE."
        sell_or_unsold(session)
        guard += 1
    assert session.auction.status == AuctionStatus.BLOCKED

    screen = build_screen(hidden_root, session)
    try:
        assert len(screen._visible_rows()) == len(session.auction.history)
        # Filters/search must still work without raising.
        screen._result_var.set("SOLD")
        screen._refresh_table()
    finally:
        screen.destroy()


# ============================================================
# 30. READ-ONLY GUARANTEE
# ============================================================


def test_screen_does_not_mutate_auction_session(hidden_root, started_session) -> None:
    blackout = team_by_name(started_session.teams, "Blackout FC")
    started_session.sell_current_player(winning_team=blackout.id, sale_price=5)
    started_session.mark_current_player_unsold()

    snapshot_history = copy.deepcopy(started_session.auction.history)
    snapshot_teams = copy.deepcopy(started_session.teams)
    snapshot_players = copy.deepcopy(started_session.players)

    screen = build_screen(hidden_root, started_session)
    try:
        screen._search_var.set("a")
        screen._result_var.set("SOLD")
        screen._refresh_table()
        screen._result_var.set("ALL")
        screen._sort_var.set("Newest → Oldest")
        screen._refresh_table()
    finally:
        screen.destroy()

    assert started_session.auction.history == snapshot_history
    assert started_session.teams == snapshot_teams
    assert started_session.players == snapshot_players
