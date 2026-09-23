"""Auction History screen: read-only transaction timeline, filters,
search, sort, and summary cards for the currently loaded session.

GUI creation is best-effort via the shared `hidden_root` fixture (see
tests/conftest.py) — skipped, not failed, without a real display/Tk
backend.
"""

from __future__ import annotations

import copy

import pytest

from models.auction import Auction, AuctionStatus
from models.player import Player, Position, player_base_price
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


def sell_or_unsold(session: AuctionSession, sale_price: int | None = None):
    """Sells at `sale_price`, or (First Auction Rules V2: GK/non-GK base
    prices are no longer both satisfied by one flat fallback) the current
    player's own base price when not given explicitly."""
    player = session.current_player
    team = pick_eligible_team(player, session.teams, session.players)
    if team is None:
        return session.mark_current_player_unsold()
    price = sale_price if sale_price is not None else player_base_price(player)
    return session.sell_current_player(winning_team=team.id, sale_price=price)


def build_synthetic_blocked_session() -> AuctionSession:
    """A minimal, deterministic BLOCKED scenario for screen/report tests.

    First Auction Rules V2's "must keep the final roster slot open for a
    mandatory goalkeeper" rule means real 28-player canonical data can no
    longer reliably reach BLOCKED (exactly 4 GKs for 4 teams now
    guarantees eventual completion — verified directly: no seed in a wide
    search reached BLOCKED under any reasonable team-selection strategy).
    So instead: three teams are already full (8/8) with their own real
    GK; the fourth is stuck at 7/8 with no GK; the one player left in the
    queue is a non-GK, which the mandatory-GK rule makes illegal for
    every team regardless of budget -> BLOCKED.
    """
    from models.auction import AuctionHistoryEntry

    captains = [
        Player(
            id=i + 1,
            full_name=f"Cap{i + 1}",
            short_name=f"C{i + 1}",
            position=Position.ATT,
            overall_rating=80,
            is_captain=True,
            assigned_team=f"Team {chr(65 + i)}",
            auction_eligible=False,
            auction_status="PRE_ASSIGNED",
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
            auction_status="SOLD", sold_to=teams[i].name, sold_price=4, auction_sequence=sequence,
        )
        sequence += 1
        fillers = []
        for j in range(6):
            filler = Player(
                id=200 + i * 10 + j, full_name=f"F{i}_{j}", short_name=f"F{i}_{j}", position=Position.DEF,
                overall_rating=80, auction_status="SOLD", sold_to=teams[i].name, sold_price=2, auction_sequence=sequence,
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
            auction_status="SOLD", sold_to=teams[3].name, sold_price=2, auction_sequence=sequence,
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
            started_session.sell_current_player(winning_team=team.id, sale_price=player_base_price(current))
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
    started_session.sell_current_player(
        winning_team=team.id, sale_price=player_base_price(started_session.current_player)
    )

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
    session = build_synthetic_blocked_session()
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
