"""tests/test_first_auction_phone_regression_m3.py — Second Auction —
Milestone 3's explicit regression requirement: adding second-auction
phone support must never break first-auction captain phone bidding.
Numbered to match the ticket's own "TESTING — FIRST-AUCTION REGRESSION"
list (56-61).

These are deliberately independent, fresh checks (not just a rerun of
tests/test_captain_bidding_server.py, which already covers this ground
in depth) — each one explicitly builds a session where the second
auction has NOT been started, so `auction_phase` stays "FIRST_AUCTION"
and every pre-Milestone-3 code path is exercised unchanged.
"""
from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from services.auction_service import maximum_legal_bid
from services.auction_session_service import AuctionSession
from services.captain_auth_service import CaptainAuthService
from services.captain_bidding_server import CaptainBiddingServer, ServerLifecycleState


def team_by_name(teams, name: str):
    return next(team for team in teams if team.name == name)


def _free_port() -> int:
    import socket

    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    return port


def wait_for_state(server: CaptainBiddingServer, target: ServerLifecycleState, timeout: float = 5.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if server.state == target or server.state not in (ServerLifecycleState.OFF, ServerLifecycleState.STARTING):
            break
        time.sleep(0.02)
    assert server.state == target


@pytest.fixture
def auth(tmp_path) -> CaptainAuthService:
    return CaptainAuthService(config_path=tmp_path / "captain_bidding.json")


@pytest.fixture
def first_auction_session() -> AuctionSession:
    session = AuctionSession()
    session.autosave = None
    session.start(seed=1)
    return session


@pytest.fixture
def client(first_auction_session, auth) -> TestClient:
    server = CaptainBiddingServer(first_auction_session, auth=auth)
    with TestClient(server.app) as test_client:
        yield test_client


def login(client: TestClient, auth: CaptainAuthService, team_name: str) -> str:
    team_id = next(tid for tid in auth.team_ids if auth.team_name(tid) == team_name)
    pin = auth.get_pin(team_id)
    response = client.post("/api/login", json={"pin": pin})
    return response.json()["token"]


# ============================================================
# 56. first-auction PIN login still works
# ============================================================


def test_56_first_auction_pin_login_still_works(client, auth) -> None:
    _, pin = next(iter(auth.get_pins().items()))
    response = client.post("/api/login", json={"pin": pin})
    assert response.status_code == 200
    assert response.json()["accepted"] is True


# ============================================================
# 57. first-auction phone state still works
# ============================================================


def test_57_first_auction_phone_state_still_works(client, first_auction_session) -> None:
    data = client.get("/api/state").json()
    assert data["auction_phase"] == "FIRST_AUCTION"
    assert data["session_mode"] == "MOCK"
    assert data["current_player"]["full_name"] == first_auction_session.current_player.full_name
    # Original first-auction state shape is completely unchanged.
    assert "progress" not in data
    assert "previous_team_name" not in (data["current_player"] or {})


# ============================================================
# 58. first-auction phone bid still works
# ============================================================


def test_58_first_auction_phone_bid_still_works(client, first_auction_session, auth) -> None:
    token = login(client, auth, "Blackout FC")
    response = client.post("/api/bid", json={"amount": 5}, headers={"X-Captain-Token": token})
    assert response.json()["accepted"] is True
    assert first_auction_session.auction.current_bid == 5


# ============================================================
# 59. first-auction max-bid rule unchanged
# ============================================================


def test_59_first_auction_max_bid_rule_unchanged(client, first_auction_session, auth) -> None:
    blackout = team_by_name(first_auction_session.teams, "Blackout FC")
    current_player = first_auction_session.current_player
    token = login(client, auth, "Blackout FC")
    data = client.get("/api/state", headers={"X-Captain-Token": token}).json()
    expected = max(maximum_legal_bid(blackout, current_player, first_auction_session.players), 0)
    assert data["own_team"]["maximum_legal_bid"] == expected


# ============================================================
# 60. first-auction phone/manual mixed mode unchanged
# ============================================================


def test_60_first_auction_mixed_mode_unchanged(client, first_auction_session, auth) -> None:
    token = login(client, auth, "Blackout FC")
    client.post("/api/bid", json={"amount": 5}, headers={"X-Captain-Token": token})
    darkstar = team_by_name(first_auction_session.teams, "Darkstar FC")
    result = first_auction_session.place_live_bid(darkstar.id, 6)
    assert result.accepted is True


# ============================================================
# 61. captain server startup/shutdown unchanged
# ============================================================


def test_61_captain_server_startup_shutdown_unchanged(auth) -> None:
    server = CaptainBiddingServer(AuctionSession(), host="127.0.0.1", port=_free_port(), auth=auth)
    assert server.state == ServerLifecycleState.OFF
    server.start()
    wait_for_state(server, ServerLifecycleState.RUNNING)
    assert server.is_running is True
    server.stop()
    assert server.state == ServerLifecycleState.OFF
