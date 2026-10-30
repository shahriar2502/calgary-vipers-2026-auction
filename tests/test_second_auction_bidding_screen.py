"""tests/test_second_auction_bidding_screen.py — ui/screens/
second_auction_screen.py's Milestone 2 views (SECOND AUCTION READY/
LIVE/COMPLETE/BLOCKED). Numbered to match the ticket's own "TESTING —
UI" list (71-84).

GUI creation is best-effort via the shared `hidden_root` fixture (see
tests/conftest.py) — skipped, not failed, without a real display/Tk
backend.
"""
from __future__ import annotations

import pytest

from models.player import OUTFIELD_BASE_PRICE, player_base_price
from models.second_auction import SecondAuctionStatus
from services.auction_service import team_can_bid_for_player
from services.auction_session_service import AuctionSession
from services.second_auction_bidding_service import team_can_bid_for_player as sa_team_can_bid_for_player
from services.second_auction_service import is_locked_player


def _pick_eligible_team(player, teams, players):
    eligible = [team for team in teams if team_can_bid_for_player(team, player, players)]
    if not eligible:
        return None
    return min(eligible, key=lambda team: team.roster_size)


def _non_locked_roster_ids(team, players_by_id) -> list[int]:
    return [pid for pid in team.roster if pid in players_by_id and not is_locked_player(players_by_id[pid])]


def ready_session(seed: int = 1) -> AuctionSession:
    """A completed first auction with a confirmed 16-player release plan
    — SECOND AUCTION READY, not yet started."""
    session = AuctionSession()
    session.autosave = None
    session.start(seed=seed)
    guard = 0
    while session.auction.status.value not in ("COMPLETE", "BLOCKED") and guard < 300:
        guard += 1
        current = session.current_player
        team = _pick_eligible_team(current, session.teams, session.players)
        if team is None:
            session.mark_current_player_unsold()
        else:
            session.sell_current_player(winning_team=team.id, sale_price=player_base_price(current))
    assert session.auction.status.value == "COMPLETE"

    players_by_id = {player.id: player for player in session.players}
    for team in session.teams:
        for player_id in _non_locked_roster_ids(team, players_by_id)[:4]:
            session.toggle_second_auction_release(team.id, player_id)
    session.confirm_second_auction_release_plan()
    return session


def live_session(seed: int = 1) -> AuctionSession:
    session = ready_session(seed=seed)
    session.start_second_auction()
    return session


def complete_second_auction(session: AuctionSession) -> None:
    state = session.second_auction_state
    players_by_id = {player.id: player for player in session.players}
    guard = 0
    while state.status == SecondAuctionStatus.IN_PROGRESS and guard < 200:
        guard += 1
        current_player = players_by_id[state.current_player_id]
        eligible = [team for team in session.teams if sa_team_can_bid_for_player(state, team, current_player)]
        if not eligible:
            session.mark_second_auction_player_unsold()
            continue
        team = min(eligible, key=lambda team: len(state.active_rosters_by_team[team.id]))
        session.sell_second_auction_player(winning_team=team.id, sale_price=player_base_price(current_player))


def _all_widget_texts(widget, collected=None):
    if collected is None:
        collected = []
    for child in widget.winfo_children():
        try:
            text = child.cget("text")
            if isinstance(text, str):
                collected.append(text)
        except Exception:
            pass
        _all_widget_texts(child, collected)
    return collected


def _find_widget_containing_text(widget, substring: str):
    for child in widget.winfo_children():
        try:
            text = child.cget("text")
            if isinstance(text, str) and substring in text:
                return child
        except Exception:
            pass
        found = _find_widget_containing_text(child, substring)
        if found is not None:
            return found
    return None


# ============================================================
# 71. SECOND AUCTION READY state shown
# ============================================================


def test_71_second_auction_ready_state_shown(hidden_root) -> None:
    from ui.screens.second_auction_screen import SecondAuctionScreen

    session = ready_session()
    built = SecondAuctionScreen(hidden_root, session)
    try:
        assert _find_widget_containing_text(built, "RELEASE PLAN CONFIRMED") is not None
        assert _find_widget_containing_text(built, "START SECOND AUCTION") is not None
    finally:
        built.destroy()


# ============================================================
# 72. START SECOND AUCTION confirmation required
# ============================================================


def test_72_start_second_auction_confirmation_required(hidden_root) -> None:
    from ui.screens.second_auction_screen import SecondAuctionScreen

    session = ready_session()
    built = SecondAuctionScreen(hidden_root, session)
    try:
        built._on_start_second_auction_clicked()  # opens a dialog; does not start by itself
        assert session.second_auction_state.status == SecondAuctionStatus.NOT_STARTED
    finally:
        built.destroy()


def test_start_second_auction_dialog_confirm_actually_starts(hidden_root) -> None:
    from ui.screens.second_auction_screen import SecondAuctionScreen

    session = ready_session()
    built = SecondAuctionScreen(hidden_root, session)
    try:
        fake_dialog = type("FakeDialog", (), {"destroy": lambda self: None})()
        built._confirm_start_second_auction(fake_dialog)
        assert session.second_auction_state.status == SecondAuctionStatus.IN_PROGRESS
    finally:
        built.destroy()


# ============================================================
# 73. live second-auction UI renders
# ============================================================


def test_73_live_second_auction_ui_renders(hidden_root) -> None:
    from ui.screens.second_auction_screen import SecondAuctionScreen

    session = live_session()
    built = SecondAuctionScreen(hidden_root, session)
    try:
        assert _find_widget_containing_text(built, "SECOND AUCTION — TRANSFER WINDOW") is not None
        assert _find_widget_containing_text(built, "Round 1") is not None
    finally:
        built.destroy()


# ============================================================
# 74-78. current player / original team / original price / budgets /
# roster counts displayed
# ============================================================


def test_74_current_player_displayed(hidden_root) -> None:
    from ui.screens.second_auction_screen import SecondAuctionScreen

    session = live_session()
    players_by_id = {player.id: player for player in session.players}
    current_player = players_by_id[session.second_auction_state.current_player_id]
    built = SecondAuctionScreen(hidden_root, session)
    try:
        assert _find_widget_containing_text(built, current_player.full_name.upper()) is not None
    finally:
        built.destroy()


def test_75_original_team_displayed(hidden_root) -> None:
    from ui.screens.second_auction_screen import SecondAuctionScreen

    session = live_session()
    built = SecondAuctionScreen(hidden_root, session)
    try:
        assert _find_widget_containing_text(built, "PREVIOUS TEAM") is not None
    finally:
        built.destroy()


def test_76_original_first_auction_price_displayed(hidden_root) -> None:
    """Milestone 3's premium visual redesign (Part C) relabeled this
    field from "ORIGINAL FIRST-AUCTION PRICE" to the shorter "ORIGINAL
    PRICE" shown directly under "PREVIOUS TEAM" — a deliberate styling
    change, not a regression; the underlying data is unchanged."""
    from ui.screens.second_auction_screen import SecondAuctionScreen

    session = live_session()
    built = SecondAuctionScreen(hidden_root, session)
    try:
        assert _find_widget_containing_text(built, "ORIGINAL PRICE") is not None
    finally:
        built.destroy()


def test_77_team_budgets_displayed(hidden_root) -> None:
    from ui.screens.second_auction_screen import SecondAuctionScreen

    session = live_session()
    built = SecondAuctionScreen(hidden_root, session)
    try:
        state = session.second_auction_state
        texts = _all_widget_texts(built)
        assert any(
            f"{state.remaining_budgets_by_team[team.id]}M" in text for team in session.teams for text in texts
        )
    finally:
        built.destroy()


def test_78_roster_counts_displayed(hidden_root) -> None:
    from ui.screens.second_auction_screen import SecondAuctionScreen

    session = live_session()
    built = SecondAuctionScreen(hidden_root, session)
    try:
        texts = _all_widget_texts(built)
        assert any("4 / 8" in text for text in texts)
    finally:
        built.destroy()


# ============================================================
# 79-80. SOLD/UNSOLD buttons work through the safe service
# ============================================================


def test_79_sold_button_works_through_safe_service(hidden_root) -> None:
    from ui.screens.second_auction_screen import SecondAuctionScreen

    session = live_session()
    built = SecondAuctionScreen(hidden_root, session)
    try:
        state = session.second_auction_state
        players_by_id = {player.id: player for player in session.players}
        current_player = players_by_id[state.current_player_id]
        team = next(t for t in session.teams if sa_team_can_bid_for_player(state, t, current_player))

        built._on_second_auction_team_selected(team.id)
        built._price_var.set(str(player_base_price(current_player)))
        built._on_second_auction_sold_clicked()

        assert len(state.history) == 1
        assert state.history[0].status == "SOLD"
        assert built._last_result is not None and built._last_result.outcome == "SOLD"
    finally:
        built.destroy()


def test_80_unsold_button_works_through_safe_service(hidden_root) -> None:
    from ui.screens.second_auction_screen import SecondAuctionScreen

    session = live_session()
    built = SecondAuctionScreen(hidden_root, session)
    try:
        built._on_second_auction_unsold_clicked()
        state = session.second_auction_state
        assert len(state.history) == 1
        assert state.history[0].status == "UNSOLD"
        assert built._last_result is not None and built._last_result.outcome == "UNSOLD"
    finally:
        built.destroy()


# ============================================================
# 81-82. completion / blocked views render
# ============================================================


def test_81_completion_view_renders(hidden_root) -> None:
    from ui.screens.second_auction_screen import SecondAuctionScreen

    session = live_session()
    complete_second_auction(session)
    assert session.second_auction_state.status == SecondAuctionStatus.COMPLETE
    built = SecondAuctionScreen(hidden_root, session)
    try:
        assert _find_widget_containing_text(built, "SECOND AUCTION COMPLETE") is not None
        for team in session.teams:
            assert len(session.second_auction_state.active_rosters_by_team[team.id]) == 8
    finally:
        built.destroy()


def test_82_blocked_view_renders(hidden_root) -> None:
    from ui.screens.second_auction_screen import SecondAuctionScreen

    session = live_session()
    state = session.second_auction_state
    for team_id in state.remaining_budgets_by_team:
        state.remaining_budgets_by_team[team_id] = 0
    guard = 0
    while state.status == SecondAuctionStatus.IN_PROGRESS and guard < 50:
        guard += 1
        session.mark_second_auction_player_unsold()
    assert state.status == SecondAuctionStatus.BLOCKED

    built = SecondAuctionScreen(hidden_root, session)
    try:
        assert _find_widget_containing_text(built, "SECOND AUCTION BLOCKED") is not None
    finally:
        built.destroy()


# ============================================================
# 83. narrow-window controls remain accessible
# ============================================================


def test_83_narrow_window_controls_accessible(hidden_root) -> None:
    from ui.screens.second_auction_screen import SecondAuctionScreen

    hidden_root.geometry("1024x640")
    hidden_root.update_idletasks()
    session = live_session()
    built = SecondAuctionScreen(hidden_root, session)
    try:
        texts = _all_widget_texts(built)
        assert any(text == "SOLD" for text in texts)
        assert any(text == "UNSOLD" for text in texts)
    finally:
        built.destroy()


# ============================================================
# 84. phone bidding controls not shown for second auction
# ============================================================


def test_84_phone_bidding_controls_not_shown_for_second_auction(hidden_root) -> None:
    from ui.screens.second_auction_screen import SecondAuctionScreen

    session = live_session()
    built = SecondAuctionScreen(hidden_root, session)
    try:
        texts = _all_widget_texts(built)
        assert not any("CAPTAIN BIDDING" in text or "PHONE CONNECTED" in text for text in texts)
    finally:
        built.destroy()
