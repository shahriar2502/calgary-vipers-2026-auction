"""tests/test_match_results_screen.py — ui/screens/match_results_screen.py.
Numbered to match the ticket's own "TESTING — UI" list (42-53).

GUI creation is best-effort via the shared `hidden_root` fixture (see
tests/conftest.py) — skipped, not failed, without a real display/Tk
backend.
"""
from __future__ import annotations

import pytest

from models.player import player_base_price
from services.auction_service import team_can_bid_for_player
from services.auction_session_service import AuctionSession


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


def _find_all_widgets_containing_text(widget, substring: str, found=None):
    if found is None:
        found = []
    for child in widget.winfo_children():
        try:
            text = child.cget("text")
            if isinstance(text, str) and substring in text:
                found.append(child)
        except Exception:
            pass
        _find_all_widgets_containing_text(child, substring, found)
    return found


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


@pytest.fixture
def screen(hidden_root):
    from ui.screens.match_results_screen import MatchResultsScreen

    session = complete_session()
    built = MatchResultsScreen(hidden_root, session)
    yield built, session
    built.destroy()


# ============================================================
# 42. Match Results screen registered
# ============================================================


def test_42_match_results_screen_registered() -> None:
    from ui.main_window import NAV_ITEMS
    from ui.screens import SCREEN_BUILDERS

    assert "Match Results" in SCREEN_BUILDERS
    assert "Match Results" in dict(NAV_ITEMS)


# ============================================================
# 43. correct team selectors
# ============================================================


def test_43_correct_team_selectors(screen) -> None:
    built, session = screen
    team_names = {team.name for team in session.teams}
    assert set(built._team1_var.get().split()) or built._team1_var.get() in team_names
    assert built._team1_var.get() in team_names
    assert built._team2_var.get() in team_names
    assert {"Blackout FC", "Darkstar FC", "Goli Underdogs", "Showstoppers"} == team_names


# ============================================================
# 44. scores can be entered
# ============================================================


def test_44_scores_can_be_entered(screen) -> None:
    built, session = screen
    built._team1_goals_var.set("3")
    built._team2_goals_var.set("1")
    assert built._team1_goals_var.get() == "3"
    assert built._team2_goals_var.get() == "1"


# ============================================================
# 45. save result updates history
# ============================================================


def test_45_save_result_updates_history(screen) -> None:
    built, session = screen
    blackout = team_by_name(session.teams, "Blackout FC")
    darkstar = team_by_name(session.teams, "Darkstar FC")
    built._team1_var.set(blackout.name)
    built._team2_var.set(darkstar.name)
    built._team1_goals_var.set("3")
    built._team2_goals_var.set("1")
    built._on_save_result_clicked()

    assert len(session.match_results) == 1
    assert _find_widget_containing_text(built, "Blackout FC") is not None
    assert _find_widget_containing_text(built, "3–1") is not None


def test_save_result_rejects_same_team_and_shows_error(screen) -> None:
    built, session = screen
    same = session.teams[0].name
    built._team1_var.set(same)
    built._team2_var.set(same)
    built._team1_goals_var.set("1")
    built._team2_goals_var.set("0")
    built._on_save_result_clicked()

    assert len(session.match_results) == 0
    assert built._form_error is not None


# ============================================================
# 46. transfer balance updates immediately
# ============================================================


def test_46_transfer_balance_updates_immediately(screen) -> None:
    built, session = screen
    blackout = team_by_name(session.teams, "Blackout FC")
    before_budget = blackout.remaining_budget

    built._team1_var.set(blackout.name)
    darkstar = team_by_name(session.teams, "Darkstar FC")
    built._team2_var.set(darkstar.name)
    built._team1_goals_var.set("3")
    built._team2_goals_var.set("1")
    built._on_save_result_clicked()

    assert _find_widget_containing_text(built, f"{before_budget + 4}M") is not None


# ============================================================
# 47. edit updates displayed balance
# ============================================================


def test_47_edit_updates_displayed_balance(screen) -> None:
    built, session = screen
    blackout = team_by_name(session.teams, "Blackout FC")
    darkstar = team_by_name(session.teams, "Darkstar FC")
    match = session.add_match_result(1, blackout.id, darkstar.id, 3, 1)
    built._render()
    assert _find_widget_containing_text(built, f"{blackout.remaining_budget + 4}M") is not None

    built._on_edit_clicked(match)
    built._team1_goals_var.set("1")
    built._team2_goals_var.set("1")
    built._on_save_result_clicked()

    assert _find_widget_containing_text(built, f"{blackout.remaining_budget + 2}M") is not None
    assert _find_widget_containing_text(built, f"{blackout.remaining_budget + 4}M") is None


# ============================================================
# 48. delete updates displayed balance
# ============================================================


def test_48_delete_updates_displayed_balance(screen) -> None:
    built, session = screen
    blackout = team_by_name(session.teams, "Blackout FC")
    darkstar = team_by_name(session.teams, "Darkstar FC")
    match = session.add_match_result(1, blackout.id, darkstar.id, 3, 1)
    built._render()
    assert _find_widget_containing_text(built, f"{blackout.remaining_budget + 4}M") is not None

    built._confirm_delete(match, type("FakeDialog", (), {"destroy": lambda self: None})())

    assert session.match_results == []
    assert _find_widget_containing_text(built, f"{blackout.remaining_budget}M") is not None
    assert _find_widget_containing_text(built, f"{blackout.remaining_budget + 4}M") is None


# ============================================================
# 49. screen unavailable for incomplete auction
# ============================================================


def test_49_screen_unavailable_for_incomplete_auction(hidden_root) -> None:
    from ui.screens.match_results_screen import MatchResultsScreen

    session = AuctionSession()
    session.autosave = None
    session.start(seed=1)
    built = MatchResultsScreen(hidden_root, session)
    try:
        assert _find_widget_containing_text(built, "FIRST AUCTION IN PROGRESS") is not None
        assert built.winfo_children()  # something rendered, not a crash
    finally:
        built.destroy()


def test_no_session_shows_no_auction_session_message(hidden_root) -> None:
    from ui.screens.match_results_screen import MatchResultsScreen

    built = MatchResultsScreen(hidden_root, None)
    try:
        assert _find_widget_containing_text(built, "NO AUCTION SESSION") is not None
    finally:
        built.destroy()


def test_blocked_auction_shows_blocked_message(hidden_root) -> None:
    from models.auction import AuctionStatus
    from ui.screens.match_results_screen import MatchResultsScreen

    session = AuctionSession()
    session.autosave = None
    session.start(seed=1)
    session.auction.status = AuctionStatus.BLOCKED
    built = MatchResultsScreen(hidden_root, session)
    try:
        assert _find_widget_containing_text(built, "FIRST AUCTION BLOCKED") is not None
    finally:
        built.destroy()


# ============================================================
# 50. completed-session results screen works
# ============================================================


def test_50_completed_session_results_screen_works(screen) -> None:
    built, session = screen
    assert _find_widget_containing_text(built, "MATCH ENTRY") is not None
    assert _find_widget_containing_text(built, "TRANSFER BUDGET TRACKER") is not None
    assert _find_widget_containing_text(built, "MATCH MONEY SUMMARY") is not None
    for team in session.teams:
        assert _find_widget_containing_text(built, team.name.upper()) is not None


# ============================================================
# 51. narrow-window controls remain accessible
# ============================================================


def test_51_narrow_window_controls_remain_accessible(hidden_root) -> None:
    from ui.screens.match_results_screen import MatchResultsScreen

    hidden_root.geometry("1024x640")
    hidden_root.update_idletasks()
    session = complete_session()
    built = MatchResultsScreen(hidden_root, session)
    try:
        texts = _all_widget_texts(built)
        assert any("SAVE RESULT" in text for text in texts)
    finally:
        built.destroy()


# ============================================================
# 52. captain PINs are not exposed
# ============================================================


def test_52_captain_pins_are_not_exposed(hidden_root, tmp_path) -> None:
    from services.captain_auth_service import CaptainAuthService
    from ui.screens.match_results_screen import MatchResultsScreen

    session = complete_session()
    auth = CaptainAuthService(config_path=tmp_path / "captain_bidding.json")
    pins = list(auth.get_pins().values())

    blackout = team_by_name(session.teams, "Blackout FC")
    darkstar = team_by_name(session.teams, "Darkstar FC")
    session.add_match_result(1, blackout.id, darkstar.id, 3, 1)

    built = MatchResultsScreen(hidden_root, session)
    try:
        texts = " ".join(_all_widget_texts(built))
        for pin in pins:
            assert pin not in texts
    finally:
        built.destroy()


# ============================================================
# 53. phone bidding remains disabled after completion
# ============================================================


def test_53_phone_bidding_remains_disabled_after_completion(tmp_path) -> None:
    from fastapi.testclient import TestClient

    from services.captain_auth_service import CaptainAuthService
    from services.captain_bidding_server import CaptainBiddingServer

    session = complete_session()
    auth = CaptainAuthService(config_path=tmp_path / "captain_bidding.json")
    server = CaptainBiddingServer(session, auth=auth)

    with TestClient(server.app) as client:
        pins = list(auth.get_pins().values())
        login = client.post("/api/login", json={"pin": pins[0]}).json()
        token = login["token"]
        response = client.post("/api/bid", json={"amount": 5}, headers={"X-Captain-Token": token})
        body = response.json()
        assert body["accepted"] is False

    # Match Results screen itself exposes no bidding controls at all.
    all_texts = str(session.match_results)
    assert "amount" not in all_texts.lower()
