"""tests/test_second_auction_milestone3_ui.py — the Second Auction LIVE/
COMPLETE screens, the Teams screen's phase-aware refresh, and the
Settings screen's "Auction Phase" indicator (Second Auction — Milestone
3). Numbered to match the ticket's own "UI TESTING" list (78-91).

GUI creation is best-effort via the shared `hidden_root` fixture (see
tests/conftest.py) — skipped, not failed, without a real display/Tk
backend.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from models.player import OUTFIELD_BASE_PRICE, player_base_price
from services.auction_service import team_can_bid_for_player
from services.auction_session_service import AuctionSession
from services.captain_auth_service import CaptainAuthService
from services.captain_bidding_server import CaptainBiddingServer
from services.second_auction_bidding_service import team_can_bid_for_player as sa_team_can_bid_for_player
from services.second_auction_service import is_locked_player


def pick_eligible_team(player, teams, players):
    eligible = [team for team in teams if team_can_bid_for_player(team, player, players)]
    if not eligible:
        return None
    return min(eligible, key=lambda team: team.roster_size)


def non_locked_roster_ids(team, players_by_id) -> list[int]:
    return [pid for pid in team.roster if pid in players_by_id and not is_locked_player(players_by_id[pid])]


def completed_first_auction_session(seed: int = 1) -> AuctionSession:
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


def ready_session(seed: int = 1) -> AuctionSession:
    session = completed_first_auction_session(seed)
    players_by_id = {player.id: player for player in session.players}
    for team in session.teams:
        for player_id in non_locked_roster_ids(team, players_by_id)[:4]:
            session.toggle_second_auction_release(team.id, player_id)
    session.confirm_second_auction_release_plan()
    return session


def live_session(seed: int = 1) -> AuctionSession:
    session = ready_session(seed)
    session.start_second_auction()
    return session


def complete_second_auction(session: AuctionSession) -> None:
    state = session.second_auction_state
    players_by_id = {player.id: player for player in session.players}
    guard = 0
    while state.status.value == "IN_PROGRESS" and guard < 200:
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


def _find_button_with_text(widget, text: str):
    import customtkinter as ctk

    for child in widget.winfo_children():
        if isinstance(child, ctk.CTkButton):
            try:
                if child.cget("text") == text:
                    return child
            except Exception:
                pass
        found = _find_button_with_text(child, text)
        if found is not None:
            return found
    return None


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
def auth(tmp_path) -> CaptainAuthService:
    return CaptainAuthService(config_path=tmp_path / "captain_bidding.json")


# ============================================================
# 78. Settings shows second-auction captain bidding state correctly
# ============================================================


def test_78_settings_shows_second_auction_phase(hidden_root, auth) -> None:
    from ui.screens.settings_screen import SettingsScreen

    session = live_session()
    server = CaptainBiddingServer(session, auth=auth)
    hidden_root.captain_bidding_server = server
    built = SettingsScreen(hidden_root, session)
    try:
        assert _find_widget_containing_text(built, "Second Auction") is not None
    finally:
        built.destroy()
        del hidden_root.captain_bidding_server


def test_78b_settings_shows_first_auction_phase_before_second_starts(hidden_root, auth) -> None:
    from ui.screens.settings_screen import SettingsScreen

    session = ready_session()
    server = CaptainBiddingServer(session, auth=auth)
    hidden_root.captain_bidding_server = server
    built = SettingsScreen(hidden_root, session)
    try:
        assert _find_widget_containing_text(built, "First Auction") is not None
    finally:
        built.destroy()
        del hidden_root.captain_bidding_server


# ============================================================
# 79. phone server can start while second auction IN_PROGRESS
# ============================================================


def test_79_phone_server_can_start_while_second_auction_in_progress(auth) -> None:
    import socket
    import time

    from services.captain_bidding_server import ServerLifecycleState

    def _free_port() -> int:
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
        probe.close()
        return port

    session = live_session()
    server = CaptainBiddingServer(session, host="127.0.0.1", port=_free_port(), auth=auth)
    server.start()
    deadline = time.time() + 5
    while time.time() < deadline and server.state == ServerLifecycleState.STARTING:
        time.sleep(0.02)
    assert server.state == ServerLifecycleState.RUNNING
    server.stop()


# ============================================================
# 80. no misleading "active bidding" state shown once COMPLETE
# ============================================================


def test_80_no_active_bidding_implied_after_complete(hidden_root, auth) -> None:
    from ui.screens.second_auction_screen import SecondAuctionScreen

    session = live_session()
    complete_second_auction(session)
    server = CaptainBiddingServer(session, auth=auth)
    with TestClient(server.app) as client:
        token_resp = client.post("/api/login", json={"pin": next(iter(auth.get_pins().values()))})
        token = token_resp.json()["token"]
        bid_resp = client.post("/api/bid", json={"amount": OUTFIELD_BASE_PRICE}, headers={"X-Captain-Token": token})
        assert bid_resp.json()["accepted"] is False

    hidden_root.captain_bidding_server = server
    built = SecondAuctionScreen(hidden_root, session)
    try:
        assert _find_widget_containing_text(built, "SECOND AUCTION COMPLETE") is not None
        assert _find_button_with_text(built, "SOLD") is None  # no SOLD/UNSOLD buttons on the completion view
        assert _find_button_with_text(built, "UNSOLD") is None
    finally:
        built.destroy()
        del hidden_root.captain_bidding_server


# ============================================================
# 81. Second Auction live view reflects phone leading bid
# ============================================================


def test_81_live_view_reflects_phone_leading_bid(hidden_root, auth) -> None:
    from ui.screens.second_auction_screen import SecondAuctionScreen

    session = live_session()
    server = CaptainBiddingServer(session, auth=auth)
    hidden_root.captain_bidding_server = server
    team_id, pin = next(iter(auth.get_pins().items()))
    team = next(t for t in session.teams if t.id == team_id)
    with TestClient(server.app) as client:
        token = client.post("/api/login", json={"pin": pin}).json()["token"]
        client.post("/api/bid", json={"amount": OUTFIELD_BASE_PRICE}, headers={"X-Captain-Token": token})

        built = SecondAuctionScreen(hidden_root, session)
        try:
            assert _find_widget_containing_text(built, f"{OUTFIELD_BASE_PRICE}M") is not None
            assert _find_widget_containing_text(built, team.name.upper()) is not None
        finally:
            built.destroy()
    del hidden_root.captain_bidding_server


# ============================================================
# 82. Teams screen updates immediately after second-auction SOLD
# ============================================================


def test_82_teams_screen_updates_immediately_after_second_auction_sold(hidden_root) -> None:
    from ui.screens.teams_screen import TeamsScreen

    session = live_session()
    state = session.second_auction_state
    players_by_id = {player.id: player for player in session.players}
    team = session.teams[0]
    before = TeamsScreen(hidden_root, session)
    before_texts = _all_widget_texts(before)
    before.destroy()

    current_player = players_by_id[state.current_player_id]
    session.sell_second_auction_player(winning_team=team.id, sale_price=player_base_price(current_player))

    after = TeamsScreen(hidden_root, session)
    try:
        assert _find_widget_containing_text(after, current_player.full_name) is not None
    finally:
        after.destroy()


# ============================================================
# 83-86. current player / previous team / original price / current bid /
# leading team / progress render
# ============================================================


def test_83_current_player_section_renders_required_data(hidden_root) -> None:
    from ui.screens.second_auction_screen import SecondAuctionScreen

    session = live_session()
    players_by_id = {player.id: player for player in session.players}
    current_player = players_by_id[session.second_auction_state.current_player_id]
    built = SecondAuctionScreen(hidden_root, session)
    try:
        assert _find_widget_containing_text(built, current_player.full_name.upper()) is not None
        assert _find_widget_containing_text(built, current_player.position.value) is not None
    finally:
        built.destroy()


def test_84_previous_team_original_price_fields_render(hidden_root) -> None:
    from ui.screens.second_auction_screen import SecondAuctionScreen

    session = live_session()
    built = SecondAuctionScreen(hidden_root, session)
    try:
        assert _find_widget_containing_text(built, "PREVIOUS TEAM") is not None
        assert _find_widget_containing_text(built, "ORIGINAL PRICE") is not None
    finally:
        built.destroy()


def test_85_current_bid_leading_team_render(hidden_root) -> None:
    from ui.screens.second_auction_screen import SecondAuctionScreen

    session = live_session()
    team = session.teams[0]
    session.place_second_auction_live_bid(team.id, OUTFIELD_BASE_PRICE)
    built = SecondAuctionScreen(hidden_root, session)
    try:
        assert _find_widget_containing_text(built, "CURRENT BID") is not None
        assert _find_widget_containing_text(built, "LEADING") is not None
        assert _find_widget_containing_text(built, f"{OUTFIELD_BASE_PRICE}M") is not None
        assert _find_widget_containing_text(built, team.name.upper()) is not None
    finally:
        built.destroy()


def test_86_progress_indicators_render(hidden_root) -> None:
    from ui.screens.second_auction_screen import SecondAuctionScreen

    session = live_session()
    built = SecondAuctionScreen(hidden_root, session)
    try:
        assert _find_widget_containing_text(built, "Round 1") is not None
        assert _find_widget_containing_text(built, "resolved this round") is not None
        assert _find_widget_containing_text(built, "SOLD") is not None
    finally:
        built.destroy()


# ============================================================
# 87-88. captain connection indicators render; 4/4 connected
# ============================================================


def test_87_all_four_captain_connection_indicators_render(hidden_root, auth) -> None:
    from ui.screens.second_auction_screen import SecondAuctionScreen

    session = live_session()
    server = CaptainBiddingServer(session, auth=auth)
    hidden_root.captain_bidding_server = server
    built = SecondAuctionScreen(hidden_root, session)
    try:
        for team in session.teams:
            assert _find_widget_containing_text(built, team.name) is not None
    finally:
        built.destroy()
        del hidden_root.captain_bidding_server


def test_88_four_of_four_connected_state_renders_correctly(hidden_root, auth) -> None:
    from ui.screens.second_auction_screen import SecondAuctionScreen

    session = live_session()
    server = CaptainBiddingServer(session, auth=auth)
    hidden_root.captain_bidding_server = server
    with TestClient(server.app) as client:
        for _team_id, pin in auth.get_pins().items():
            token = client.post("/api/login", json={"pin": pin}).json()["token"]
            client.get("/api/state", headers={"X-Captain-Token": token})

        built = SecondAuctionScreen(hidden_root, session)
        try:
            assert _find_widget_containing_text(built, "4 / 4 CONNECTED") is not None
        finally:
            built.destroy()
    del hidden_root.captain_bidding_server


# ============================================================
# 89. final completion view renders
# ============================================================


def test_89_final_completion_view_renders(hidden_root) -> None:
    from ui.screens.second_auction_screen import SecondAuctionScreen

    session = live_session()
    complete_second_auction(session)
    built = SecondAuctionScreen(hidden_root, session)
    try:
        assert _find_widget_containing_text(built, "SECOND AUCTION COMPLETE") is not None
        assert _find_widget_containing_text(built, "PLAYERS SOLD") is not None
        assert _find_widget_containing_text(built, "FINAL SQUADS LOCKED") is not None
    finally:
        built.destroy()


# ============================================================
# 90. narrow layout remains usable
# ============================================================


def test_90_narrow_layout_remains_usable(hidden_root) -> None:
    from ui.screens.second_auction_screen import SecondAuctionScreen

    hidden_root.geometry("1024x640")
    hidden_root.update_idletasks()
    session = live_session()
    built = SecondAuctionScreen(hidden_root, session)
    try:
        texts = _all_widget_texts(built)
        assert any(text == "SOLD" for text in texts)
        assert any(text == "UNSOLD" for text in texts)
        assert _find_widget_containing_text(built, "Captain Connections".upper()) is not None
    finally:
        built.destroy()


# ============================================================
# 91. no captain PIN accidentally rendered outside Settings
# ============================================================


def test_91_no_pin_rendered_on_second_auction_screen(hidden_root, auth) -> None:
    from ui.screens.second_auction_screen import SecondAuctionScreen

    session = live_session()
    server = CaptainBiddingServer(session, auth=auth)
    hidden_root.captain_bidding_server = server
    pins = list(auth.get_pins().values())
    built = SecondAuctionScreen(hidden_root, session)
    try:
        texts = " ".join(_all_widget_texts(built))
        for pin in pins:
            assert pin not in texts
    finally:
        built.destroy()
        del hidden_root.captain_bidding_server


def test_91b_no_pin_rendered_on_teams_screen(hidden_root, auth) -> None:
    from ui.screens.teams_screen import TeamsScreen

    session = live_session()
    server = CaptainBiddingServer(session, auth=auth)
    hidden_root.captain_bidding_server = server
    pins = list(auth.get_pins().values())
    built = TeamsScreen(hidden_root, session)
    try:
        texts = " ".join(_all_widget_texts(built))
        for pin in pins:
            assert pin not in texts
    finally:
        built.destroy()
        del hidden_root.captain_bidding_server
