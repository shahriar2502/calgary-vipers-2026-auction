"""Tests for services/captain_bidding_server.py: the Captain Phone
Bidding Phase 1 local web server (FastAPI app + uvicorn lifecycle).

API-shape tests use FastAPI's in-process TestClient (no real socket, no
real thread) for speed; the lifecycle tests (start/stop/idempotency) bind
a real port to verify the actual uvicorn-in-a-thread mechanics.
"""

from __future__ import annotations

import urllib.request

import pytest
from fastapi.testclient import TestClient

from services.auction_session_service import AuctionSession, SessionMode
from services.captain_bidding_server import CaptainBiddingServer, get_lan_ip


def team_by_name(teams, name: str):
    return next(team for team in teams if team.name == name)


@pytest.fixture
def started_session() -> AuctionSession:
    session = AuctionSession()
    session.autosave = None
    session.start(seed=1)
    return session


@pytest.fixture
def client(started_session) -> TestClient:
    server = CaptainBiddingServer(started_session)
    return TestClient(server.app)


# ============================================================
# 19. HEALTH
# ============================================================


def test_health_endpoint() -> None:
    server = CaptainBiddingServer(AuctionSession())
    client = TestClient(server.app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"ok": True}


# ============================================================
# 20-22. STATE — SESSION PRESENCE / MODE
# ============================================================


def test_state_endpoint_with_no_session() -> None:
    server = CaptainBiddingServer(AuctionSession())
    client = TestClient(server.app)
    data = client.get("/api/state").json()
    assert data == {"no_active_session": True}


def test_state_endpoint_with_active_mock(client) -> None:
    data = client.get("/api/state").json()
    assert data["no_active_session"] is False
    assert data["session_mode"] == "MOCK"


def test_state_endpoint_with_live() -> None:
    session = AuctionSession()
    session.autosave = None
    session.start(seed=1, mode=SessionMode.LIVE)
    server = CaptainBiddingServer(session)
    client = TestClient(server.app)
    data = client.get("/api/state").json()
    assert data["session_mode"] == "LIVE"


# ============================================================
# 23-27. STATE CONTENT CORRECTNESS
# ============================================================


def test_current_player_data_correct(client, started_session) -> None:
    current = started_session.current_player
    data = client.get("/api/state").json()
    player_payload = data["current_player"]
    assert player_payload["full_name"] == current.full_name
    assert player_payload["position"] == current.position.value
    assert player_payload["overall_rating"] == current.overall_rating


def test_fpl_known_value_correct(started_session) -> None:
    rizvi = next(p for p in started_session.players if p.full_name == "Rizvi Ibrahim")
    started_session.auction.queue[started_session.auction.current_queue_position] = rizvi.id
    server = CaptainBiddingServer(started_session)
    client = TestClient(server.app)
    data = client.get("/api/state").json()
    assert data["current_player"]["fpl_display"] == "111"


def test_fpl_none_correct(started_session) -> None:
    munem = next(p for p in started_session.players if p.full_name == "Munem")
    assert munem.last_season_fpl_points is None
    started_session.auction.queue[started_session.auction.current_queue_position] = munem.id
    server = CaptainBiddingServer(started_session)
    client = TestClient(server.app)
    data = client.get("/api/state").json()
    assert data["current_player"]["fpl_display"] == "N/A"


def test_team_info_correct(client, started_session) -> None:
    blackout = team_by_name(started_session.teams, "Blackout FC")
    data = client.get("/api/state", params={"team_id": blackout.id}).json()
    selected = data["selected_team"]
    assert selected["id"] == blackout.id
    assert selected["name"] == "Blackout FC"
    assert selected["remaining_budget"] == blackout.remaining_budget
    assert selected["roster_size"] == blackout.roster_size


def test_max_legal_bid_correct(client, started_session) -> None:
    blackout = team_by_name(started_session.teams, "Blackout FC")
    data = client.get("/api/state", params={"team_id": blackout.id}).json()
    assert data["selected_team"]["maximum_legal_bid"] == blackout.maximum_legal_bid


# ============================================================
# 28-30. BID SUBMISSION VIA THE API
# ============================================================


def test_accepted_api_bid(client, started_session) -> None:
    blackout = team_by_name(started_session.teams, "Blackout FC")
    response = client.post("/api/bid", json={"team_id": blackout.id, "amount": 5})
    data = response.json()
    assert response.status_code == 200
    assert data["accepted"] is True
    assert data["current_bid"] == 5
    assert started_session.auction.current_bid == 5


def test_rejected_api_bid(client, started_session) -> None:
    blackout = team_by_name(started_session.teams, "Blackout FC")
    client.post("/api/bid", json={"team_id": blackout.id, "amount": 5})
    darkstar = team_by_name(started_session.teams, "Darkstar FC")
    response = client.post("/api/bid", json={"team_id": darkstar.id, "amount": 5})
    data = response.json()
    assert data["accepted"] is False
    assert "exceed" in data["reason"].lower()


def test_bad_payload_rejected_safely(client) -> None:
    response = client.post("/api/bid", json={"team_id": "not-a-number", "amount": "also-not-a-number"})
    assert response.status_code == 422  # FastAPI/pydantic validation error, never a 500


def test_bad_payload_missing_fields_rejected_safely(client) -> None:
    response = client.post("/api/bid", json={})
    assert response.status_code == 422


def test_bid_with_no_active_session_returns_409() -> None:
    server = CaptainBiddingServer(AuctionSession())
    client = TestClient(server.app)
    response = client.post("/api/bid", json={"team_id": 1, "amount": 5})
    assert response.status_code == 409


# ============================================================
# SELECT-TEAM
# ============================================================


def test_select_team_success(client, started_session) -> None:
    blackout = team_by_name(started_session.teams, "Blackout FC")
    response = client.post("/api/select-team", json={"team_id": blackout.id})
    assert response.status_code == 200
    assert response.json() == {"ok": True, "team_id": blackout.id, "team_name": "Blackout FC"}


def test_select_team_unknown_id_returns_404(client) -> None:
    response = client.post("/api/select-team", json={"team_id": 9999})
    assert response.status_code == 404


# ============================================================
# PHOTO SERVING SAFETY
# ============================================================


def test_photo_serves_a_real_canonical_photo(client, started_session) -> None:
    from pathlib import Path

    current = started_session.current_player
    assert current.photo_path
    filename = Path(current.photo_path).name
    response = client.get(f"/photos/{filename}")
    assert response.status_code == 200
    assert len(response.content) > 0


def test_photo_path_traversal_rejected(client) -> None:
    response = client.get("/photos/..%2F..%2Fmain.py")
    assert response.status_code == 404


def test_photo_nonexistent_file_rejected(client) -> None:
    response = client.get("/photos/does_not_exist.jpg")
    assert response.status_code == 404


def test_index_page_serves(client) -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert "CAPTAIN BIDDING" in response.text


# ============================================================
# 31-34. SERVER LIFECYCLE (real port, real thread)
# ============================================================


@pytest.fixture
def live_server(started_session):
    server = CaptainBiddingServer(started_session, host="127.0.0.1", port=18766)
    yield server
    server.stop()  # always clean up, even if a test fails mid-way


def test_server_start(live_server) -> None:
    assert live_server.is_running is False
    live_server.start()
    assert live_server.is_running is True
    response = urllib.request.urlopen("http://127.0.0.1:18766/health", timeout=2)
    assert response.status == 200


def test_server_stop(live_server) -> None:
    live_server.start()
    live_server.stop()
    assert live_server.is_running is False


def test_repeated_start_is_safe_and_does_not_duplicate(live_server) -> None:
    live_server.start()
    first_thread = live_server._thread
    live_server.start()
    second_thread = live_server._thread
    assert live_server.is_running is True
    assert first_thread is second_thread  # no second server/thread was spawned


def test_repeated_stop_is_safe(live_server) -> None:
    live_server.start()
    live_server.stop()
    live_server.stop()  # must not raise
    assert live_server.is_running is False


def test_stop_without_ever_starting_is_safe() -> None:
    server = CaptainBiddingServer(AuctionSession(), host="127.0.0.1", port=18767)
    server.stop()  # must not raise
    assert server.is_running is False


def test_get_lan_ip_never_raises() -> None:
    ip = get_lan_ip()
    assert isinstance(ip, str)
    assert len(ip) > 0
