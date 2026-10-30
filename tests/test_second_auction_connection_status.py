"""tests/test_second_auction_connection_status.py — the Second Auction
LIVE screen's permanent "Captain Connections" panel (Milestone 3).
Numbered to match the ticket's own "TESTING — CONNECTION STATUS" list
(47-55).

GUI creation is best-effort via the shared `hidden_root` fixture (see
tests/conftest.py) — skipped, not failed, without a real display/Tk
backend.
"""
from __future__ import annotations

import pytest

from models.player import OUTFIELD_BASE_PRICE, player_base_price
from services.auction_service import team_can_bid_for_player
from services.auction_session_service import AuctionSession
from services.captain_auth_service import CaptainAuthService
from services.captain_bidding_server import CaptainBiddingServer
from services.second_auction_service import is_locked_player


def team_by_name(teams, name: str):
    return next(team for team in teams if team.name == name)


def pick_eligible_team(player, teams, players):
    eligible = [team for team in teams if team_can_bid_for_player(team, player, players)]
    if not eligible:
        return None
    return min(eligible, key=lambda team: team.roster_size)


def non_locked_roster_ids(team, players_by_id) -> list[int]:
    return [pid for pid in team.roster if pid in players_by_id and not is_locked_player(players_by_id[pid])]


def live_second_auction_session(seed: int = 1) -> AuctionSession:
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

    players_by_id = {player.id: player for player in session.players}
    for team in session.teams:
        for player_id in non_locked_roster_ids(team, players_by_id)[:4]:
            session.toggle_second_auction_release(team.id, player_id)
    session.confirm_second_auction_release_plan()
    session.start_second_auction()
    return session


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
def auth(tmp_path) -> CaptainAuthService:
    return CaptainAuthService(config_path=tmp_path / "captain_bidding.json")


# ============================================================
# 47. Second Auction screen shows all four team connection states
# ============================================================


def test_47_shows_all_four_team_connection_states(hidden_root, auth) -> None:
    from ui.screens.second_auction_screen import SecondAuctionScreen

    session = live_second_auction_session()
    server = CaptainBiddingServer(session, auth=auth)
    hidden_root.captain_bidding_server = server
    built = SecondAuctionScreen(hidden_root, session)
    try:
        for team in session.teams:
            assert _find_widget_containing_text(built, team.name) is not None
        assert _find_widget_containing_text(built, "Captain Connections".upper()) is not None
    finally:
        built.destroy()
        del hidden_root.captain_bidding_server


# ============================================================
# 48-50. one / multiple / all-four connected display correctly
# ============================================================


def test_48_one_connected_captain_displays_correctly(hidden_root, auth) -> None:
    from ui.screens.second_auction_screen import SecondAuctionScreen

    from fastapi.testclient import TestClient

    session = live_second_auction_session()
    server = CaptainBiddingServer(session, auth=auth)
    hidden_root.captain_bidding_server = server
    with TestClient(server.app) as client:
        _, pin = next(iter(auth.get_pins().items()))
        token = client.post("/api/login", json={"pin": pin}).json()["token"]
        client.get("/api/state", headers={"X-Captain-Token": token})

        built = SecondAuctionScreen(hidden_root, session)
        try:
            assert _find_widget_containing_text(built, "1 / 4 CONNECTED") is not None
        finally:
            built.destroy()
    del hidden_root.captain_bidding_server


def test_49_multiple_connected_captains_display_correctly(hidden_root, auth) -> None:
    from fastapi.testclient import TestClient

    from ui.screens.second_auction_screen import SecondAuctionScreen

    session = live_second_auction_session()
    server = CaptainBiddingServer(session, auth=auth)
    hidden_root.captain_bidding_server = server
    with TestClient(server.app) as client:
        pins = list(auth.get_pins().items())
        tokens = []
        for _team_id, pin in pins[:2]:
            token = client.post("/api/login", json={"pin": pin}).json()["token"]
            tokens.append(token)
            client.get("/api/state", headers={"X-Captain-Token": token})

        built = SecondAuctionScreen(hidden_root, session)
        try:
            assert _find_widget_containing_text(built, "2 / 4 CONNECTED") is not None
        finally:
            built.destroy()
    del hidden_root.captain_bidding_server


def test_50_all_four_connected_displays_4_of_4(hidden_root, auth) -> None:
    from fastapi.testclient import TestClient

    from ui.screens.second_auction_screen import SecondAuctionScreen

    session = live_second_auction_session()
    server = CaptainBiddingServer(session, auth=auth)
    hidden_root.captain_bidding_server = server
    with TestClient(server.app) as client:
        for _team_id, pin in auth.get_pins().items():
            token = client.post("/api/login", json={"pin": pin}).json()["token"]
            client.get("/api/state", headers={"X-Captain-Token": token})

        built = SecondAuctionScreen(hidden_root, session)
        try:
            assert _find_widget_containing_text(built, "4 / 4 CONNECTED") is not None
            texts = _all_widget_texts(built)
            assert sum(1 for text in texts if text == "CONNECTED") >= 4
        finally:
            built.destroy()
    del hidden_root.captain_bidding_server


# ============================================================
# 51. offline/manual team displays correctly
# ============================================================


def test_51_offline_manual_team_displays_correctly(hidden_root, auth) -> None:
    from ui.screens.second_auction_screen import SecondAuctionScreen

    session = live_second_auction_session()
    server = CaptainBiddingServer(session, auth=auth)
    hidden_root.captain_bidding_server = server
    built = SecondAuctionScreen(hidden_root, session)
    try:
        texts = _all_widget_texts(built)
        assert sum(1 for text in texts if text == "MANUAL / OFFLINE") >= 4
        assert _find_widget_containing_text(built, "0 / 4 CONNECTED") is not None
    finally:
        built.destroy()
        del hidden_root.captain_bidding_server


# ============================================================
# 52-53. connection display never affects eligibility or manual bidding
# ============================================================


def test_52_connection_display_does_not_affect_eligibility(hidden_root, auth) -> None:
    from ui.screens.second_auction_screen import SecondAuctionScreen

    session = live_second_auction_session()
    server = CaptainBiddingServer(session, auth=auth)
    hidden_root.captain_bidding_server = server
    built = SecondAuctionScreen(hidden_root, session)
    try:
        team = session.teams[0]
        result = session.place_second_auction_live_bid(team.id, OUTFIELD_BASE_PRICE)
        assert result.accepted is True  # fully eligible despite 0/4 connected
    finally:
        built.destroy()
        del hidden_root.captain_bidding_server


def test_53_connection_display_does_not_disable_manual_organizer_bidding(hidden_root, auth) -> None:
    from ui.screens.second_auction_screen import SecondAuctionScreen

    session = live_second_auction_session()
    server = CaptainBiddingServer(session, auth=auth)
    hidden_root.captain_bidding_server = server
    built = SecondAuctionScreen(hidden_root, session)
    try:
        state = session.second_auction_state
        players_by_id = {player.id: player for player in session.players}
        team = session.teams[0]
        result = session.sell_second_auction_player(
            winning_team=team.id, sale_price=player_base_price(players_by_id[state.current_player_id])
        )
        assert result.outcome == "SOLD"
    finally:
        built.destroy()
        del hidden_root.captain_bidding_server


# ============================================================
# 54-55. reconnect updates status; disconnect/offline never corrupts state
# ============================================================


def test_54_reconnect_updates_displayed_status(hidden_root, auth) -> None:
    from fastapi.testclient import TestClient

    from ui.screens.second_auction_screen import SecondAuctionScreen

    session = live_second_auction_session()
    server = CaptainBiddingServer(session, auth=auth)
    hidden_root.captain_bidding_server = server
    with TestClient(server.app) as client:
        built = SecondAuctionScreen(hidden_root, session)
        assert _find_widget_containing_text(built, "0 / 4 CONNECTED") is not None
        built.destroy()

        _, pin = next(iter(auth.get_pins().items()))
        token = client.post("/api/login", json={"pin": pin}).json()["token"]
        client.get("/api/state", headers={"X-Captain-Token": token})

        built2 = SecondAuctionScreen(hidden_root, session)
        try:
            assert _find_widget_containing_text(built2, "1 / 4 CONNECTED") is not None
        finally:
            built2.destroy()
    del hidden_root.captain_bidding_server


def test_55_disconnect_transition_does_not_corrupt_auction_state(hidden_root, auth) -> None:
    from fastapi.testclient import TestClient

    from ui.screens.second_auction_screen import SecondAuctionScreen

    session = live_second_auction_session()
    server = CaptainBiddingServer(session, auth=auth)
    hidden_root.captain_bidding_server = server
    history_len_before = len(session.second_auction_state.history)
    with TestClient(server.app) as client:
        team_id, pin = next(iter(auth.get_pins().items()))
        token = client.post("/api/login", json={"pin": pin}).json()["token"]
        client.get("/api/state", headers={"X-Captain-Token": token})
        auth.reset_team_session(team_id)  # simulate a disconnect

        built = SecondAuctionScreen(hidden_root, session)
        try:
            assert _find_widget_containing_text(built, "0 / 4 CONNECTED") is not None
        finally:
            built.destroy()
    assert len(session.second_auction_state.history) == history_len_before
    assert session.second_auction_state.status.value == "IN_PROGRESS"
    del hidden_root.captain_bidding_server
