"""Tests for services/captain_bidding_server.py: the Captain Phone
Bidding local web server (FastAPI app + uvicorn lifecycle), Phase 2's
PIN-login + token-authenticated API.

API-shape tests use FastAPI's in-process TestClient (no real socket, no
real thread) for speed; the lifecycle tests (start/stop/idempotency) bind
a real port to verify the actual uvicorn-in-a-thread mechanics.

Every server/auth pair here is built with an isolated, `tmp_path`-scoped
PIN config file — never the real `config/captain_bidding.json` — so
running this suite never disturbs an organizer's actual saved PINs.
"""

from __future__ import annotations

import asyncio
import socket
import time
import urllib.request

import pytest
import uvicorn
from fastapi.testclient import TestClient

from services.auction_service import maximum_legal_bid
from services.auction_session_service import AuctionSession, SessionMode
from services.captain_auth_service import CaptainAuthService
from services.captain_bidding_server import CaptainBiddingServer, ServerLifecycleState, get_lan_ip


def team_by_name(teams, name: str):
    return next(team for team in teams if team.name == name)


def _free_port() -> int:
    """A genuinely free ephemeral port, discovered by binding to port 0
    and letting the OS pick — used as a dynamic base for every port-
    collision test below instead of a hardcoded literal, so these tests
    can never race each other (or any other test) over the same port."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    return port


def _read_trace_log() -> str:
    """The real on-disk content of the RC2 diagnostic log — found via our
    own `RotatingFileHandler` specifically (not `handlers[0]`, which can
    just as easily be pytest's own `LogCaptureHandler` depending on
    attachment order — the exact same "who attached a handler to this
    named logger first" gotcha `services/app_logging.py`'s own docstring
    and test suite already documents)."""
    import logging.handlers

    from services.app_logging import get_logger

    logger = get_logger()
    for handler in logger.handlers:
        if isinstance(handler, logging.handlers.RotatingFileHandler):
            with open(handler.baseFilename, encoding="utf-8") as file_handle:
                return file_handle.read()
    raise AssertionError("no RotatingFileHandler attached to the diagnostic logger")


def wait_for_state(server: CaptainBiddingServer, target: ServerLifecycleState, timeout: float = 5.0) -> None:
    """`CaptainBiddingServer.start()` is deliberately non-blocking (RC1
    stabilization ticket) — it returns immediately with `state ==
    STARTING`, transitioning to RUNNING/FAILED on a short-lived
    background watcher thread. Tests that need to observe the outcome
    poll for it here instead of asserting synchronously right after
    `start()`, exactly like Settings/Live Auction do in the real app."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if server.state == target or server.state not in (
            ServerLifecycleState.OFF,
            ServerLifecycleState.STARTING,
        ):
            break
        time.sleep(0.02)
    assert server.state == target, f"expected {target}, got {server.state} (last_error={server.last_error!r})"


@pytest.fixture
def auth(tmp_path) -> CaptainAuthService:
    return CaptainAuthService(config_path=tmp_path / "captain_bidding.json")


@pytest.fixture
def started_session() -> AuctionSession:
    session = AuctionSession()
    session.autosave = None
    session.start(seed=1)
    return session


@pytest.fixture
def client(started_session, auth) -> TestClient:
    """A `with`-managed TestClient: without it, `TestClient.__init__`
    lazily starts a background anyio portal thread on first request that
    is only guaranteed to be torn down on `__exit__` — across this
    file's ~40 tests, leaving dozens of those threads alive at once was
    observed to starve a later *real* uvicorn server's asyncio event
    loop of scheduling time badly enough to fail an HTTP request within
    its 2s timeout when the full ~900-test suite ran end to end (though
    never when this file ran alone). Yielding from inside the `with`
    block guarantees each test's portal thread is fully closed before
    the next test starts."""
    server = CaptainBiddingServer(started_session, auth=auth)
    with TestClient(server.app) as test_client:
        yield test_client


def login(client: TestClient, auth: CaptainAuthService, team_name: str) -> str:
    """Logs in as `team_name` through the real HTTP endpoint and returns
    the issued token."""
    team_id = next(tid for tid in auth.team_ids if auth.team_name(tid) == team_name)
    pin = auth.get_pin(team_id)
    response = client.post("/api/login", json={"pin": pin})
    assert response.status_code == 200
    return response.json()["token"]


def auth_headers(token: str) -> dict:
    return {"X-Captain-Token": token}


# ============================================================
# HEALTH
# ============================================================


def test_health_endpoint(auth) -> None:
    server = CaptainBiddingServer(AuctionSession(), auth=auth)
    with TestClient(server.app) as client:
        response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"ok": True}


# ============================================================
# STATE — SESSION PRESENCE / MODE
# ============================================================


def test_state_endpoint_with_no_session(auth) -> None:
    server = CaptainBiddingServer(AuctionSession(), auth=auth)
    with TestClient(server.app) as client:
        data = client.get("/api/state").json()
    assert data == {"no_active_session": True}


def test_state_endpoint_with_active_mock(client) -> None:
    data = client.get("/api/state").json()
    assert data["no_active_session"] is False
    assert data["session_mode"] == "MOCK"


def test_state_endpoint_with_live(auth) -> None:
    session = AuctionSession()
    session.autosave = None
    session.start(seed=1, mode=SessionMode.LIVE)
    server = CaptainBiddingServer(session, auth=auth)
    with TestClient(server.app) as client:
        data = client.get("/api/state").json()
    assert data["session_mode"] == "LIVE"


def test_state_endpoint_public_fields_require_no_login(client) -> None:
    """current_player/current_bid/leading_team/mode/status are public,
    projector-equivalent auction state — visible with no token at all."""
    data = client.get("/api/state").json()
    assert data["authenticated"] is False
    assert data["own_team"] is None
    assert data["current_player"] is not None
    assert "auction_status" in data


# ============================================================
# STATE CONTENT CORRECTNESS
# ============================================================


def test_current_player_data_correct(client, started_session) -> None:
    current = started_session.current_player
    data = client.get("/api/state").json()
    player_payload = data["current_player"]
    assert player_payload["full_name"] == current.full_name
    assert player_payload["position"] == current.position.value
    assert player_payload["overall_rating"] == current.overall_rating


def test_fpl_known_value_correct(started_session, auth) -> None:
    rizvi = next(p for p in started_session.players if p.full_name == "Rizvi Ibrahim")
    started_session.auction.queue[started_session.auction.current_queue_position] = rizvi.id
    server = CaptainBiddingServer(started_session, auth=auth)
    with TestClient(server.app) as client:
        data = client.get("/api/state").json()
    assert data["current_player"]["fpl_display"] == "111"


def test_fpl_none_correct(started_session, auth) -> None:
    munem = next(p for p in started_session.players if p.full_name == "Munem Morshed")
    assert munem.last_season_fpl_points is None
    started_session.auction.queue[started_session.auction.current_queue_position] = munem.id
    server = CaptainBiddingServer(started_session, auth=auth)
    with TestClient(server.app) as client:
        data = client.get("/api/state").json()
    assert data["current_player"]["fpl_display"] == "N/A"


def test_own_team_data_correct_after_login(client, started_session, auth) -> None:
    blackout = team_by_name(started_session.teams, "Blackout FC")
    token = login(client, auth, "Blackout FC")
    data = client.get("/api/state", headers=auth_headers(token)).json()
    own_team = data["own_team"]
    assert own_team["id"] == blackout.id
    assert own_team["name"] == "Blackout FC"
    assert own_team["remaining_budget"] == blackout.remaining_budget
    assert own_team["roster_size"] == blackout.roster_size
    assert data["team_id"] == blackout.id
    assert data["team_name"] == "Blackout FC"
    assert data["authenticated"] is True


def test_max_legal_bid_correct(client, started_session, auth) -> None:
    blackout = team_by_name(started_session.teams, "Blackout FC")
    current_player = started_session.current_player
    token = login(client, auth, "Blackout FC")
    data = client.get("/api/state", headers=auth_headers(token)).json()
    expected = max(maximum_legal_bid(blackout, current_player, started_session.players), 0)
    assert data["own_team"]["maximum_legal_bid"] == expected


def test_state_never_exposes_other_teams_data(client, auth) -> None:
    token = login(client, auth, "Blackout FC")
    data = client.get("/api/state", headers=auth_headers(token)).json()
    assert "teams" not in data
    assert "selected_team" not in data


def test_state_never_exposes_any_pin(client, auth) -> None:
    token = login(client, auth, "Blackout FC")
    data = client.get("/api/state", headers=auth_headers(token)).json()
    pins = list(auth.get_pins().values())
    payload_text = str(data)
    for pin in pins:
        assert pin not in payload_text


# ============================================================
# LOGIN
# ============================================================


def test_login_with_valid_pin_succeeds(client, auth) -> None:
    _, pin = next(iter(auth.get_pins().items()))
    response = client.post("/api/login", json={"pin": pin})
    assert response.status_code == 200
    body = response.json()
    assert body["accepted"] is True
    assert body["token"] is not None
    assert body["team_name"] in {"Blackout FC", "Darkstar FC", "Goli Underdogs", "Showstoppers"}


def test_login_with_invalid_pin_rejected(client) -> None:
    response = client.post("/api/login", json={"pin": "0000"})
    assert response.status_code in (401, 429)
    assert response.json()["accepted"] is False


def test_login_bad_payload_rejected_safely(client) -> None:
    response = client.post("/api/login", json={})
    assert response.status_code == 422


def test_second_login_same_team_rejected(client, auth) -> None:
    _, pin = next(iter(auth.get_pins().items()))
    first = client.post("/api/login", json={"pin": pin})
    assert first.json()["accepted"] is True
    second = client.post("/api/login", json={"pin": pin})
    assert second.status_code == 409
    assert second.json()["accepted"] is False
    assert second.json()["error_code"] == "already_connected"


def test_logout_frees_the_slot(client, auth) -> None:
    _, pin = next(iter(auth.get_pins().items()))
    token = client.post("/api/login", json={"pin": pin}).json()["token"]
    logout_response = client.post("/api/logout", headers=auth_headers(token))
    assert logout_response.status_code == 200
    second = client.post("/api/login", json={"pin": pin})
    assert second.json()["accepted"] is True


# ============================================================
# BID SUBMISSION VIA THE API
# ============================================================


def test_accepted_api_bid(client, started_session, auth) -> None:
    token = login(client, auth, "Blackout FC")
    response = client.post("/api/bid", json={"amount": 5}, headers=auth_headers(token))
    data = response.json()
    assert response.status_code == 200
    assert data["accepted"] is True
    assert data["current_bid"] == 5
    assert started_session.auction.current_bid == 5


def test_rejected_api_bid(client, auth) -> None:
    blackout_token = login(client, auth, "Blackout FC")
    client.post("/api/bid", json={"amount": 5}, headers=auth_headers(blackout_token))
    darkstar_token = login(client, auth, "Darkstar FC")
    response = client.post("/api/bid", json={"amount": 5}, headers=auth_headers(darkstar_token))
    data = response.json()
    assert data["accepted"] is False
    assert "exceed" in data["reason"].lower()


def test_bid_without_token_rejected(client) -> None:
    response = client.post("/api/bid", json={"amount": 5})
    assert response.status_code == 401


def test_bid_with_unknown_token_rejected(client) -> None:
    response = client.post("/api/bid", json={"amount": 5}, headers=auth_headers("garbage-token"))
    assert response.status_code == 401


def test_bid_payload_cannot_override_team_identity(client, started_session, auth) -> None:
    """Security requirement: a Darkstar-authenticated client sending a
    forged team_id in the body must still bid as Darkstar, never as the
    team named in the payload."""
    darkstar = team_by_name(started_session.teams, "Darkstar FC")
    blackout = team_by_name(started_session.teams, "Blackout FC")
    token = login(client, auth, "Darkstar FC")

    response = client.post("/api/bid", json={"amount": 5, "team_id": blackout.id}, headers=auth_headers(token))
    assert response.status_code == 200
    assert response.json()["accepted"] is True
    assert started_session.auction.leading_team_id == darkstar.id
    assert started_session.auction.leading_team_id != blackout.id


def test_bad_payload_rejected_safely(client, auth) -> None:
    token = login(client, auth, "Blackout FC")
    response = client.post("/api/bid", json={"amount": "not-a-number"}, headers=auth_headers(token))
    assert response.status_code == 422  # FastAPI/pydantic validation error, never a 500


def test_bad_payload_missing_fields_rejected_safely(client, auth) -> None:
    token = login(client, auth, "Blackout FC")
    response = client.post("/api/bid", json={}, headers=auth_headers(token))
    assert response.status_code == 422


def test_bid_with_no_active_session_returns_409(auth) -> None:
    server = CaptainBiddingServer(AuctionSession(), auth=auth)
    with TestClient(server.app) as client:
        response = client.post("/api/bid", json={"amount": 5})
    assert response.status_code in (401, 409)


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


def test_index_page_shows_pin_login_not_the_old_team_selector(client) -> None:
    """Phase 2 replaced the unrestricted Phase 1 team selector with PIN
    login — the served page must reflect that, not the old flow."""
    response = client.get("/")
    assert "ENTER TEAM PIN" in response.text
    assert "SELECT YOUR TEAM" not in response.text
    assert "NO TEAM AUTHENTICATION" not in response.text


# ============================================================
# SERVER LIFECYCLE (real port, real thread)
# ============================================================


@pytest.fixture
def live_server(started_session, auth):
    # A dynamic, genuinely-free port (see _free_port) rather than a
    # hardcoded literal: this fixture is reused by many tests in this
    # file, and a fixed port number showed rare timing-sensitive
    # collisions under full-file load (the same category of issue
    # already fixed elsewhere in this file — see _free_port's docstring).
    server = CaptainBiddingServer(started_session, host="127.0.0.1", port=_free_port(), auth=auth)
    yield server
    server.stop()  # always clean up, even if a test fails mid-way


def test_server_start(live_server) -> None:
    assert live_server.is_running is False
    live_server.start()
    wait_for_state(live_server, ServerLifecycleState.RUNNING)
    assert live_server.is_running is True
    response = urllib.request.urlopen(f"http://127.0.0.1:{live_server.actual_port}/health", timeout=2)
    assert response.status == 200


def test_server_stop(live_server) -> None:
    live_server.start()
    live_server.stop()
    assert live_server.is_running is False


def test_repeated_start_is_safe_and_does_not_duplicate(live_server) -> None:
    live_server.start()
    wait_for_state(live_server, ServerLifecycleState.RUNNING)
    first_thread = live_server._thread
    live_server.start()  # already RUNNING -> must be a no-op, not a second server
    second_thread = live_server._thread
    assert live_server.is_running is True
    assert first_thread is second_thread  # no second server/thread was spawned


def test_repeated_stop_is_safe(live_server) -> None:
    live_server.start()
    live_server.stop()
    live_server.stop()  # must not raise
    assert live_server.is_running is False


def test_restart_is_safe_and_auth_state_survives(live_server, auth) -> None:
    """A STOP followed by another START is not an app restart: the same
    CaptainAuthService instance keeps running underneath, so an
    authenticated captain's token is still valid afterward."""
    _, pin = next(iter(auth.get_pins().items()))
    live_server.start()
    wait_for_state(live_server, ServerLifecycleState.RUNNING)
    login_response = urllib.request.urlopen(
        urllib.request.Request(
            f"http://127.0.0.1:{live_server.actual_port}/api/login",
            data=f'{{"pin": "{pin}"}}'.encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        ),
        timeout=2,
    )
    import json

    token = json.loads(login_response.read())["token"]

    live_server.stop()
    live_server.start()

    assert auth.authenticate(token) is not None


def test_stop_without_ever_starting_is_safe(auth) -> None:
    server = CaptainBiddingServer(AuctionSession(), host="127.0.0.1", port=_free_port(), auth=auth)
    server.stop()  # must not raise
    assert server.is_running is False


def test_app_exit_cleanup_calls_stop_and_is_idempotent(live_server) -> None:
    live_server.start()
    live_server.stop()
    live_server.stop()
    live_server.stop()
    assert live_server.is_running is False


def test_get_lan_ip_never_raises() -> None:
    """`get_lan_ip` returns `None` (never raises, never "127.0.0.1") when
    no usable candidate is found — in this environment a real address
    should be found, but the contract itself is what matters here."""
    ip = get_lan_ip()
    assert ip is None or (isinstance(ip, str) and len(ip) > 0 and not ip.startswith("127."))


# ============================================================
# CONCURRENCY (through the real HTTP API)
# ============================================================


def test_four_authenticated_teams_can_each_submit_a_bid(client, started_session, auth) -> None:
    tokens = {name: login(client, auth, name) for name in ("Blackout FC", "Darkstar FC", "Goli Underdogs", "Showstoppers")}
    # First Auction Rules V2: seed=1's first current player is a GK (base
    # price 4M) — start comfortably above any possible base price.
    bid = 10
    for name, token in tokens.items():
        response = client.post("/api/bid", json={"amount": bid}, headers=auth_headers(token))
        assert response.json()["accepted"] is True
        bid += 1


def test_simultaneous_equal_bids_exactly_one_accepted_through_api(client, started_session, auth) -> None:
    import threading

    blackout_token = login(client, auth, "Blackout FC")
    darkstar_token = login(client, auth, "Darkstar FC")
    client.post("/api/bid", json={"amount": 10}, headers=auth_headers(blackout_token))

    results = []
    barrier = threading.Barrier(2)

    def submit(token):
        barrier.wait(timeout=5)
        response = client.post("/api/bid", json={"amount": 11}, headers=auth_headers(token))
        results.append(response.json()["accepted"])

    threads = [
        threading.Thread(target=submit, args=(blackout_token,)),
        threading.Thread(target=submit, args=(darkstar_token,)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    assert sum(1 for accepted in results if accepted) == 1
    assert started_session.auction.current_bid == 11


# ============================================================
# WINDOWS RC0 PACKAGED-RUNTIME STABILIZATION: server lifecycle states
# ============================================================


def test_initial_state_is_off(auth) -> None:
    server = CaptainBiddingServer(AuctionSession(), auth=auth)
    assert server.state == ServerLifecycleState.OFF
    assert server.last_error is None


def test_start_transitions_off_to_starting_then_running(live_server) -> None:
    assert live_server.state == ServerLifecycleState.OFF
    live_server.start()
    # Immediately after start() returns, the server must not yet have
    # blocked the caller waiting for confirmation (see ticket #15).
    assert live_server.state in (ServerLifecycleState.STARTING, ServerLifecycleState.RUNNING)
    wait_for_state(live_server, ServerLifecycleState.RUNNING)


def test_stop_returns_state_to_off(live_server) -> None:
    live_server.start()
    wait_for_state(live_server, ServerLifecycleState.RUNNING)
    live_server.stop()
    assert live_server.state == ServerLifecycleState.OFF
    assert live_server.last_error is None


def test_port_already_in_use_surfaces_failed_state_with_clear_error(auth) -> None:
    """Root cause reproduced directly: uvicorn signals a bind failure by
    raising SystemExit from inside the server thread — a BaseException a
    plain `except Exception` never catches — which previously left the
    thread dying silently with Settings stuck showing OFF forever. This
    exhausts the whole fallback port range so no automatic recovery can
    mask the failure, proving it's reported instead of hidden."""
    blockers = []
    base_port = _free_port()
    try:
        for port in range(base_port, base_port + 12):
            blocker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            blocker.bind(("127.0.0.1", port))
            blocker.listen(1)
            blockers.append(blocker)

        server = CaptainBiddingServer(AuctionSession(), host="127.0.0.1", port=base_port, auth=auth)
        server.start()
        wait_for_state(server, ServerLifecycleState.FAILED)
        assert server.last_error is not None
        assert "port" in server.last_error.lower()
        assert server.is_running is False
    finally:
        for blocker in blockers:
            blocker.close()


def test_port_collision_falls_back_to_next_available_port(auth) -> None:
    blocked_port = _free_port()
    blocker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    blocker.bind(("127.0.0.1", blocked_port))
    blocker.listen(1)
    try:
        server = CaptainBiddingServer(AuctionSession(), host="127.0.0.1", port=blocked_port, auth=auth)
        server.start()
        wait_for_state(server, ServerLifecycleState.RUNNING)
        assert server.actual_port != blocked_port
        assert blocked_port < server.actual_port <= blocked_port + 10
        assert server.lan_url.endswith(f":{server.actual_port}")
        server.stop()
    finally:
        blocker.close()


def test_no_duplicate_server_spawned_across_repeated_start_calls(live_server) -> None:
    live_server.start()
    wait_for_state(live_server, ServerLifecycleState.RUNNING)
    first_thread = live_server._thread
    for _ in range(3):
        live_server.start()
    assert live_server._thread is first_thread
    assert live_server.state == ServerLifecycleState.RUNNING


def test_server_thread_stays_alive_and_referenced_while_running(live_server) -> None:
    live_server.start()
    wait_for_state(live_server, ServerLifecycleState.RUNNING)
    assert live_server._thread is not None
    assert live_server._thread.is_alive() is True


def test_desktop_bidding_unaffected_by_server_never_starting(auth) -> None:
    """Manual bidding never depends on the captain-bidding server —
    verified with the server left completely OFF (not even attempted)."""
    session = AuctionSession()
    session.autosave = None
    session.start(seed=1)
    server = CaptainBiddingServer(session, auth=auth)
    assert server.state == ServerLifecycleState.OFF

    blackout = team_by_name(session.teams, "Blackout FC")
    result = session.place_live_bid(blackout.id, 5)
    assert result.accepted is True


def test_desktop_bidding_unaffected_by_server_start_failure(auth) -> None:
    blockers = []
    base_port = _free_port()
    try:
        for port in range(base_port, base_port + 12):
            blocker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            blocker.bind(("127.0.0.1", port))
            blocker.listen(1)
            blockers.append(blocker)

        session = AuctionSession()
        session.autosave = None
        session.start(seed=1)
        server = CaptainBiddingServer(session, host="127.0.0.1", port=base_port, auth=auth)
        server.start()
        wait_for_state(server, ServerLifecycleState.FAILED)

        blackout = team_by_name(session.teams, "Blackout FC")
        result = session.place_live_bid(blackout.id, 5)
        assert result.accepted is True
    finally:
        for blocker in blockers:
            blocker.close()


# ============================================================
# WINDOWS RC0 PACKAGED-RUNTIME STABILIZATION: LAN IP detection
# ============================================================


def test_is_private_ipv4_accepts_rfc1918_ranges() -> None:
    from services.captain_bidding_server import _is_private_ipv4

    assert _is_private_ipv4("192.168.1.42") is True
    assert _is_private_ipv4("10.0.0.8") is True
    assert _is_private_ipv4("172.16.0.1") is True
    assert _is_private_ipv4("172.31.255.255") is True


def test_is_private_ipv4_rejects_public_and_malformed_addresses() -> None:
    from services.captain_bidding_server import _is_private_ipv4

    assert _is_private_ipv4("8.8.8.8") is False
    assert _is_private_ipv4("172.32.0.1") is False  # just outside 172.16-31
    assert _is_private_ipv4("172.15.0.1") is False
    assert _is_private_ipv4("not-an-ip") is False
    assert _is_private_ipv4("1.2.3") is False


def test_get_lan_ip_prefers_private_address_over_public_candidate(monkeypatch) -> None:
    import services.captain_bidding_server as server_module

    monkeypatch.setattr(server_module, "_candidate_lan_ips", lambda: ["203.0.113.5", "192.168.1.20"])
    assert server_module.get_lan_ip() == "192.168.1.20"


def test_get_lan_ip_falls_back_to_any_non_loopback_when_no_private_address(monkeypatch) -> None:
    import services.captain_bidding_server as server_module

    monkeypatch.setattr(server_module, "_candidate_lan_ips", lambda: ["203.0.113.5"])
    assert server_module.get_lan_ip() == "203.0.113.5"


def test_get_lan_ip_returns_none_when_no_candidates_at_all(monkeypatch) -> None:
    import services.captain_bidding_server as server_module

    monkeypatch.setattr(server_module, "_candidate_lan_ips", lambda: [])
    assert server_module.get_lan_ip() is None


def test_candidate_lan_ips_falls_back_to_hostname_resolution_when_udp_trick_fails(monkeypatch) -> None:
    """The UDP-route trick failing outright (no default route at all —
    e.g. certain VPN/virtual-adapter configurations) must not leave the
    organizer with no address when the hostname-based fallback would
    have found one."""
    import services.captain_bidding_server as server_module

    class _FailingSocket:
        def connect(self, *_args, **_kwargs):
            raise OSError("no route to host")

        def close(self):
            pass

    monkeypatch.setattr(server_module.socket, "socket", lambda *_a, **_kw: _FailingSocket())
    monkeypatch.setattr(
        server_module.socket, "gethostbyname_ex", lambda _host: ("host", [], ["127.0.1.1", "192.168.50.7"])
    )
    candidates = server_module._candidate_lan_ips()
    assert "192.168.50.7" in candidates
    assert all(not c.startswith("127.") for c in candidates)


def test_lan_url_is_none_when_no_lan_ip_available(monkeypatch) -> None:
    import services.captain_bidding_server as server_module

    server = CaptainBiddingServer(AuctionSession(), auth=CaptainAuthService())
    monkeypatch.setattr(server_module, "get_lan_ip", lambda: None)
    assert server.lan_url is None


def test_server_start_succeeds_even_when_lan_ip_detection_fails(auth, monkeypatch) -> None:
    """Server bind success/failure and LAN-IP-detection success/failure
    are independent concerns — a server that bound its port successfully
    must report RUNNING regardless of whether a LAN address could be
    found for display."""
    import services.captain_bidding_server as server_module

    monkeypatch.setattr(server_module, "get_lan_ip", lambda: None)
    server = CaptainBiddingServer(AuctionSession(), host="127.0.0.1", port=_free_port(), auth=auth)
    server.start()
    wait_for_state(server, ServerLifecycleState.RUNNING)
    assert server.is_running is True
    assert server.lan_url is None
    server.stop()


# ============================================================
# WINDOWS RC0 -> RC1: threaded server event loop (root cause of the
# real "START CAPTAIN BIDDING does not work" packaged-runtime report)
# ============================================================


def test_threaded_server_event_loop_is_selector_not_proactor() -> None:
    """Root cause, confirmed against a real frozen .exe (not guessed):
    uvicorn's Windows default is `asyncio.ProactorEventLoop`, which was
    found to hang the captain-bidding server's background thread
    indefinitely after this app had already done meaningful CustomTkinter/
    PIL work on the main thread — `uvicorn.Server.started` never became
    `True` even after 60+ real seconds in the reproduction, though the
    thread stayed alive throughout. Switching to `SelectorEventLoop`
    (uvicorn's own alternative, with no functional gap for this server's
    plain-TCP use case) fixed it: confirmed starting in ~50ms in the
    exact same reproduction. This locks in the loop type so a future
    change can't silently reintroduce the hang."""
    import services.captain_bidding_server as server_module

    loop = server_module._threaded_server_event_loop()
    try:
        assert isinstance(loop, asyncio.SelectorEventLoop)
        assert not isinstance(loop, asyncio.ProactorEventLoop)
    finally:
        loop.close()


def test_uvicorn_config_uses_the_threaded_event_loop_factory(auth) -> None:
    """The actual `uvicorn.Config` built by `start()` must resolve back
    to our own factory — not silently fall through to uvicorn's own
    "auto" default (which would reintroduce the Windows hang)."""
    server = CaptainBiddingServer(AuctionSession(), host="127.0.0.1", port=_free_port(), auth=auth)
    config = uvicorn.Config(
        server.app,
        host=server.host,
        port=server.port,
        log_level="warning",
        loop="services.captain_bidding_server:_threaded_server_event_loop",
    )
    resolved_factory = config.get_loop_factory()
    from services.captain_bidding_server import _threaded_server_event_loop

    assert resolved_factory is _threaded_server_event_loop


def test_server_starts_quickly_even_after_simulated_heavy_prior_work(auth) -> None:
    """A lighter-weight regression guard for the real root cause: even
    after deliberately holding the GIL busy on the main thread for a
    noticeable stretch (simulating the heavy PIL/Tk work that preceded
    the real hang), the server must still reach RUNNING quickly — this
    would have caught a ProactorEventLoop regression without needing a
    full frozen-executable rebuild for every test run."""
    server = CaptainBiddingServer(AuctionSession(), host="127.0.0.1", port=_free_port(), auth=auth)

    # Deliberately busy the interpreter briefly, similar in spirit to
    # several hundred milliseconds of PIL compositing.
    busy_until = time.time() + 0.3
    total = 0
    while time.time() < busy_until:
        total += 1

    start_time = time.time()
    server.start()
    wait_for_state(server, ServerLifecycleState.RUNNING, timeout=3.0)
    elapsed = time.time() - start_time
    assert elapsed < 3.0
    server.stop()


# ============================================================
# RC2 TARGETED DEBUG PASS: fine-grained startup tracing, a bulletproof
# main-thread-driven timeout backstop, and BaseException capture — added
# after a real organizer laptop's log showed STARTING for ~2 minutes
# with no further log line at all (not even the background watcher's
# own timeout message), proving the RC1 fix alone was not sufficient on
# every real machine.
# ============================================================


def test_startup_trace_reaches_expected_stages(auth) -> None:
    """Every numbered marker from SERVER_START_01 through the server
    actually reporting RUNNING must appear, in order — this is the exact
    trace an organizer's real log is expected to show for a healthy
    startup, and what a stuck real machine's log would stop short of."""
    server = CaptainBiddingServer(AuctionSession(), host="127.0.0.1", port=_free_port(), auth=auth)
    server.start()
    wait_for_state(server, ServerLifecycleState.RUNNING)

    expected_prefixes = [
        "SERVER_START_01",
        "SERVER_START_02",
        "SERVER_START_03",
        "SERVER_START_04",
        "SERVER_START_05",
        "SERVER_START_06",
        "SERVER_THREAD_01",
        "SERVER_THREAD_02",
        "SERVER_THREAD_03",
        "SERVER_THREAD_04",
        "SERVER_THREAD_05",
        "SERVER_THREAD_06",
        "SERVER_THREAD_07",
        "SERVER_THREAD_08",
        "SERVER_THREAD_09",
        "SERVER_THREAD_10",
        "WATCHER_01",
        "WATCHER_03",
    ]
    # `_last_stage` only ever holds the most recent marker, so this
    # asserts on the *log file* (the durable record of every stage
    # reached), not just the final snapshot.
    content = _read_trace_log()
    for prefix in expected_prefixes:
        assert prefix in content, f"missing trace marker {prefix}"
    server.stop()


def test_startup_timeout_transitions_starting_to_failed(auth, monkeypatch) -> None:
    import services.captain_bidding_server as server_module

    monkeypatch.setattr(server_module, "_STARTUP_TIMEOUT_SECONDS", 0.2)
    server = CaptainBiddingServer(AuctionSession(), auth=auth)
    with server._state_lock:
        server._state = ServerLifecycleState.STARTING
        server._starting_since = time.monotonic()
        server._last_stage = "SERVER_THREAD_09 before server.serve()"
    time.sleep(0.3)
    server.check_startup_timeout()
    assert server.state == ServerLifecycleState.FAILED


def test_timeout_error_message_includes_last_completed_stage(auth) -> None:
    server = CaptainBiddingServer(AuctionSession(), auth=auth)
    with server._state_lock:
        server._state = ServerLifecycleState.STARTING
        server._starting_since = time.monotonic() - 999
        server._last_stage = "SERVER_THREAD_07 constructing uvicorn Server"
    server.check_startup_timeout()
    assert server.state == ServerLifecycleState.FAILED
    assert "SERVER_THREAD_07 constructing uvicorn Server" in server.last_error


def test_check_startup_timeout_is_a_cheap_noop_when_not_starting(auth) -> None:
    server = CaptainBiddingServer(AuctionSession(), auth=auth)
    assert server.state == ServerLifecycleState.OFF
    server.check_startup_timeout()  # must not raise or change state
    assert server.state == ServerLifecycleState.OFF


def test_check_startup_timeout_does_not_fire_before_the_budget_elapses(auth) -> None:
    server = CaptainBiddingServer(AuctionSession(), auth=auth)
    with server._state_lock:
        server._state = ServerLifecycleState.STARTING
        server._starting_since = time.monotonic()  # just started
    server.check_startup_timeout()
    assert server.state == ServerLifecycleState.STARTING


def test_base_exception_in_thread_becomes_failed(auth, monkeypatch) -> None:
    """Widened from `except Exception` to `except BaseException`
    specifically so a `SystemExit` (uvicorn's own bind-failure signal)
    or anything more exotic can never kill the thread silently with
    nothing recorded — the real organizer report's core symptom."""
    import services.captain_bidding_server as server_module

    def exploding_loop_factory():
        raise KeyboardInterrupt("simulated exotic startup failure")

    monkeypatch.setattr(server_module, "_threaded_server_event_loop", exploding_loop_factory)
    server = CaptainBiddingServer(AuctionSession(), host="127.0.0.1", port=_free_port(), auth=auth)
    server.start()
    wait_for_state(server, ServerLifecycleState.FAILED)
    assert "KeyboardInterrupt" in server.last_error


def test_event_loop_is_explicitly_selector_on_windows(auth) -> None:
    """Verified at *runtime*, not just by inspecting source: the actual
    event loop object the server thread creates really is a
    SelectorEventLoop, confirming the RC1 fix is genuinely wired in."""
    server = CaptainBiddingServer(AuctionSession(), host="127.0.0.1", port=_free_port(), auth=auth)
    server.start()
    wait_for_state(server, ServerLifecycleState.RUNNING)
    content = _read_trace_log()
    assert "SelectorEventLoop" in content
    assert "ProactorEventLoop" not in content
    server.stop()


def test_repeated_start_after_failed_can_retry(auth) -> None:
    blockers = []
    base_port = _free_port()
    try:
        for port in range(base_port, base_port + 12):
            blocker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            blocker.bind(("127.0.0.1", port))
            blocker.listen(1)
            blockers.append(blocker)

        server = CaptainBiddingServer(AuctionSession(), host="127.0.0.1", port=base_port, auth=auth)
        server.start()
        wait_for_state(server, ServerLifecycleState.FAILED)
    finally:
        for blocker in blockers:
            blocker.close()

    # Ports are free now -- retrying must succeed.
    server.start()
    wait_for_state(server, ServerLifecycleState.RUNNING)
    server.stop()


def test_stop_from_failed_is_safe(auth) -> None:
    server = CaptainBiddingServer(AuctionSession(), auth=auth)
    with server._state_lock:
        server._state = ServerLifecycleState.FAILED
        server._last_error = "simulated failure"
    server.stop()  # must not raise
    assert server.state == ServerLifecycleState.OFF


def test_manual_desktop_auction_works_while_server_failed(auth) -> None:
    session = AuctionSession()
    session.autosave = None
    session.start(seed=1)
    server = CaptainBiddingServer(session, auth=auth)
    with server._state_lock:
        server._state = ServerLifecycleState.FAILED
        server._last_error = "simulated failure"

    blackout = team_by_name(session.teams, "Blackout FC")
    result = session.place_live_bid(blackout.id, 5)
    assert result.accepted is True


def test_trace_log_never_contains_a_pin_or_token(auth) -> None:
    server = CaptainBiddingServer(AuctionSession(), host="127.0.0.1", port=_free_port(), auth=auth)
    pins = list(auth.get_pins().values())
    server.start()
    wait_for_state(server, ServerLifecycleState.RUNNING)

    with TestClient(server.app) as client:
        login_response = client.post("/api/login", json={"pin": pins[0]})
        token = login_response.json()["token"]

    content = _read_trace_log()
    for pin in pins:
        assert pin not in content
    assert token not in content
    server.stop()
