"""tests/test_second_auction_screen.py — ui/screens/second_auction_screen.py.
Numbered to match the ticket's own "TESTING — UI" list (44-54) plus
items 1-2/6 from "TESTING — RELEASE RULES".

GUI creation is best-effort via the shared `hidden_root` fixture (see
tests/conftest.py) — skipped, not failed, without a real display/Tk
backend.
"""
from __future__ import annotations

import pytest

from models.player import player_base_price
from services.auction_service import team_can_bid_for_player
from services.auction_session_service import AuctionSession
from services.second_auction_service import is_locked_player


def team_by_name(teams, name: str):
    return next(team for team in teams if team.name == name)


def pick_eligible_team(player, teams, players):
    eligible = [team for team in teams if team_can_bid_for_player(team, player, players)]
    if not eligible:
        return None
    return min(eligible, key=lambda team: team.roster_size)


def complete_session(seed: int = 1) -> AuctionSession:
    session = AuctionSession()
    session.autosave = None
    session.start(seed=seed)
    guard = 0
    while session.auction.status.value not in ("COMPLETE", "BLOCKED") and guard < 300:
        guard += 1
        current = session.current_player
        team = pick_eligible_team(current, session.teams, session.players)
        if team is None:
            session.mark_current_player_unsold()
        else:
            session.sell_current_player(winning_team=team.id, sale_price=player_base_price(current))
    assert session.auction.status.value == "COMPLETE"
    return session


def _non_locked_roster_ids(team, players_by_id) -> list[int]:
    return [pid for pid in team.roster if pid in players_by_id and not is_locked_player(players_by_id[pid])]


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


@pytest.fixture
def screen(hidden_root):
    from ui.screens.second_auction_screen import SecondAuctionScreen

    session = complete_session()
    built = SecondAuctionScreen(hidden_root, session)
    yield built, session
    built.destroy()


# ============================================================
# 1-2. Second Auction screen route registered + sidebar order
# ============================================================


def test_1_second_auction_screen_registered() -> None:
    from ui.main_window import NAV_ITEMS
    from ui.screens import SCREEN_BUILDERS

    assert "Second Auction" in SCREEN_BUILDERS
    assert "Second Auction" in dict(NAV_ITEMS)


def test_2_sidebar_order_correct() -> None:
    from ui.main_window import NAV_ITEMS

    names = [name for name, _description in NAV_ITEMS]
    assert names == [
        "Live Auction",
        "Second Auction",
        "Player Cards",
        "Players & Setup",
        "Teams",
        "Auction History",
        "Reports",
        "Match Results",
        "Settings",
    ]


# ============================================================
# 44-45. placement relative to Live Auction / Player Cards
# ============================================================


def test_44_second_auction_appears_under_live_auction() -> None:
    from ui.main_window import NAV_ITEMS

    names = [name for name, _description in NAV_ITEMS]
    assert names.index("Second Auction") == names.index("Live Auction") + 1


def test_45_player_cards_remains_below_second_auction() -> None:
    from ui.main_window import NAV_ITEMS

    names = [name for name, _description in NAV_ITEMS]
    assert names.index("Player Cards") == names.index("Second Auction") + 1


# ============================================================
# 6. no-session state handled
# ============================================================


def test_6_no_session_state_handled(hidden_root) -> None:
    from ui.screens.second_auction_screen import SecondAuctionScreen

    built = SecondAuctionScreen(hidden_root, None)
    try:
        assert _find_widget_containing_text(built, "NO AUCTION SESSION") is not None
    finally:
        built.destroy()


def test_incomplete_auction_shows_in_progress_message(hidden_root) -> None:
    from ui.screens.second_auction_screen import SecondAuctionScreen

    session = AuctionSession()
    session.autosave = None
    session.start(seed=1)
    built = SecondAuctionScreen(hidden_root, session)
    try:
        assert _find_widget_containing_text(built, "FIRST AUCTION IN PROGRESS") is not None
    finally:
        built.destroy()


def test_blocked_auction_shows_blocked_message(hidden_root) -> None:
    from models.auction import AuctionStatus
    from ui.screens.second_auction_screen import SecondAuctionScreen

    session = AuctionSession()
    session.autosave = None
    session.start(seed=1)
    session.auction.status = AuctionStatus.BLOCKED
    built = SecondAuctionScreen(hidden_root, session)
    try:
        assert _find_widget_containing_text(built, "FIRST AUCTION BLOCKED") is not None
    finally:
        built.destroy()


# ============================================================
# 46. transfer budget cards render
# ============================================================


def test_46_transfer_budget_cards_render(screen) -> None:
    built, session = screen
    for team in session.teams:
        assert _find_widget_containing_text(built, team.name.upper()) is not None
    assert _find_widget_containing_text(built, "Current Transfer Budget") is not None


# ============================================================
# 47. team roster release selectors render
# ============================================================


def test_47_team_roster_release_selectors_render(screen) -> None:
    built, session = screen
    texts = _all_widget_texts(built)
    assert any("RELEASE" in text for text in texts)


# ============================================================
# 48-49. locked captain / GK visible
# ============================================================


def test_48_locked_captain_visible(screen) -> None:
    built, session = screen
    texts = _all_widget_texts(built)
    assert any("LOCKED" in text for text in texts)
    team = session.teams[0]
    captain_name = next(p.full_name for p in session.players if p.id == team.captain_player_id)
    assert any(captain_name in text for text in texts)


def test_49_locked_gk_visible(screen) -> None:
    built, session = screen
    from models.player import Position

    team = session.teams[0]
    players_by_id = {p.id: p for p in session.players}
    gk = next((players_by_id[pid] for pid in team.roster if players_by_id[pid].position == Position.GK), None)
    assert gk is not None
    texts = _all_widget_texts(built)
    assert any(gk.full_name in text for text in texts)


# ============================================================
# 50. refund preview visible
# ============================================================


def test_50_refund_preview_visible(screen) -> None:
    built, session = screen
    assert _find_widget_containing_text(built, "REFUND PREVIEW") is not None
    assert _find_widget_containing_text(built, "SECOND AUCTION STARTING BUDGET") is not None


def test_refund_preview_updates_after_selection(hidden_root) -> None:
    from ui.screens.second_auction_screen import SecondAuctionScreen

    session = complete_session()
    built = SecondAuctionScreen(hidden_root, session)
    try:
        team = session.teams[0]
        players_by_id = {p.id: p for p in session.players}
        player_id = _non_locked_roster_ids(team, players_by_id)[0]
        price = players_by_id[player_id].sold_price
        session.toggle_second_auction_release(team.id, player_id)
        built._render()
        assert _find_widget_containing_text(built, f"{price}M") is not None
    finally:
        built.destroy()


# ============================================================
# 51. release pool summary visible when valid
# ============================================================


def test_51_release_pool_summary_visible_when_valid(hidden_root) -> None:
    from ui.screens.second_auction_screen import SecondAuctionScreen

    session = complete_session()
    players_by_id = {p.id: p for p in session.players}
    built = SecondAuctionScreen(hidden_root, session)
    try:
        assert _find_widget_containing_text(built, "PLAYER POOL") is None  # not yet valid
        for team in session.teams:
            for player_id in _non_locked_roster_ids(team, players_by_id)[:4]:
                session.toggle_second_auction_release(team.id, player_id)
        built._render()
        assert _find_widget_containing_text(built, "16 PLAYERS") is not None
    finally:
        built.destroy()


# ============================================================
# 52. confirm dialog required
# ============================================================


def test_52_confirm_dialog_required(hidden_root) -> None:
    from ui.screens.second_auction_screen import SecondAuctionScreen

    session = complete_session()
    players_by_id = {p.id: p for p in session.players}
    for team in session.teams:
        for player_id in _non_locked_roster_ids(team, players_by_id)[:4]:
            session.toggle_second_auction_release(team.id, player_id)
    built = SecondAuctionScreen(hidden_root, session)
    try:
        built._on_confirm_clicked()
        assert session.second_auction_setup.is_confirmed is False  # dialog shown, not yet confirmed
    finally:
        built.destroy()


# ============================================================
# 53. confirmed state read-only
# ============================================================


def test_53_confirmed_state_read_only(hidden_root) -> None:
    from ui.screens.second_auction_screen import SecondAuctionScreen

    session = complete_session()
    players_by_id = {p.id: p for p in session.players}
    for team in session.teams:
        for player_id in _non_locked_roster_ids(team, players_by_id)[:4]:
            session.toggle_second_auction_release(team.id, player_id)
    session.confirm_second_auction_release_plan()

    built = SecondAuctionScreen(hidden_root, session)
    try:
        assert _find_widget_containing_text(built, "RELEASE PLAN CONFIRMED") is not None
        assert _find_widget_containing_text(built, "SECOND AUCTION NOT STARTED") is not None
        # A confirmed plan cannot be toggled further.
        team = session.teams[0]
        other_player_id = next(pid for pid in team.roster if pid not in session.second_auction_setup.selections_for(team.id))
        from services.second_auction_service import SecondAuctionError
        with pytest.raises(SecondAuctionError):
            session.toggle_second_auction_release(team.id, other_player_id)
    finally:
        built.destroy()


def test_edit_release_plan_unlocks(hidden_root) -> None:
    from ui.screens.second_auction_screen import SecondAuctionScreen

    session = complete_session()
    players_by_id = {p.id: p for p in session.players}
    for team in session.teams:
        for player_id in _non_locked_roster_ids(team, players_by_id)[:4]:
            session.toggle_second_auction_release(team.id, player_id)
    session.confirm_second_auction_release_plan()

    built = SecondAuctionScreen(hidden_root, session)
    try:
        built._confirm_edit_plan(type("FakeDialog", (), {"destroy": lambda self: None})())
        assert session.second_auction_setup.is_confirmed is False
        assert _find_widget_containing_text(built, "DRAFT") is not None
    finally:
        built.destroy()


# ============================================================
# 54. narrow-window controls accessible
# ============================================================


def test_54_narrow_window_controls_accessible(hidden_root) -> None:
    from ui.screens.second_auction_screen import SecondAuctionScreen

    hidden_root.geometry("1024x640")
    hidden_root.update_idletasks()
    session = complete_session()
    built = SecondAuctionScreen(hidden_root, session)
    try:
        texts = _all_widget_texts(built)
        assert any("CONFIRM RELEASE LISTS" in text for text in texts)
    finally:
        built.destroy()
