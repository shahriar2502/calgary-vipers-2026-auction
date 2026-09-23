"""Tests for services/app_logging.py: the bounded diagnostic log used to
debug the windowed (console-less) packaged build without VS Code.

Every test isolates `services.runtime_paths.WRITABLE_ROOT` to a
`tmp_path` via monkeypatch — never the real repository root — so
running this suite never writes to a real `logs/` directory, and each
test gets its own fresh logger state (the module caches its logger
instance, so tests also reset that cache).
"""

from __future__ import annotations

import logging
import logging.handlers

import pytest

from services import app_logging
from services import runtime_paths as rp


@pytest.fixture(autouse=True)
def isolated_log(tmp_path, monkeypatch):
    monkeypatch.setattr(rp, "WRITABLE_ROOT", tmp_path)
    # Reset the module-level cached logger (and detach only *our own*
    # RotatingFileHandler, never pytest's own log-capture handler on this
    # same named logger) so each test gets a fresh, isolated log file
    # rather than accumulating handlers/messages across the whole run.
    logger = logging.getLogger(app_logging._LOGGER_NAME)
    for handler in list(logger.handlers):
        if isinstance(handler, logging.handlers.RotatingFileHandler):
            logger.removeHandler(handler)
            handler.close()
    monkeypatch.setattr(app_logging, "_logger", None)
    return tmp_path


def test_log_event_creates_log_file_under_writable_root(isolated_log) -> None:
    app_logging.log_event("test message")
    log_path = isolated_log / "logs" / "calgary_vipers_auction.log"
    assert log_path.is_file()
    assert "test message" in log_path.read_text(encoding="utf-8")


def test_log_event_never_raises_on_repeated_calls(isolated_log) -> None:
    for i in range(5):
        app_logging.log_event(f"event {i}")
    log_path = isolated_log / "logs" / "calgary_vipers_auction.log"
    content = log_path.read_text(encoding="utf-8")
    for i in range(5):
        assert f"event {i}" in content


def test_get_logger_does_not_double_attach_handlers(isolated_log) -> None:
    """Counts specifically our own RotatingFileHandler — pytest's own log
    capture plugin may also attach an unrelated handler to this same
    named logger, which must never be mistaken for one of ours (see
    services/app_logging.py's own docstring on this exact point)."""
    import logging.handlers

    first = app_logging.get_logger()
    second = app_logging.get_logger()
    assert first is second
    own_handlers = [h for h in first.handlers if isinstance(h, logging.handlers.RotatingFileHandler)]
    assert len(own_handlers) == 1


def test_log_never_contains_a_pin_after_a_real_login(isolated_log) -> None:
    """Integration check, not a static heuristic: actually generate real
    PINs, log in with one (the real production authentication path),
    then assert none of the four real generated PIN values ever appear
    anywhere in the resulting log file content."""
    from services.captain_auth_service import CaptainAuthService

    auth = CaptainAuthService(config_path=isolated_log / "captain_bidding.json")
    pins = list(auth.get_pins().values())
    assert len(pins) == 4

    auth.login(pins[0])
    app_logging.log_event("Manual test event to guarantee the log file exists")

    log_path = isolated_log / "logs" / "calgary_vipers_auction.log"
    content = log_path.read_text(encoding="utf-8") if log_path.is_file() else ""
    for pin in pins:
        assert pin not in content


def test_log_never_contains_a_pin_or_token_from_real_server_lifecycle(isolated_log) -> None:
    """The real START/STOP/RUNNING/FAILED log lines from an actual server
    lifecycle (plus a real captain login through the HTTP API) never
    mention any PIN or auth token."""
    import socket
    import time

    from fastapi.testclient import TestClient

    from services.auction_session_service import AuctionSession
    from services.captain_auth_service import CaptainAuthService
    from services.captain_bidding_server import CaptainBiddingServer, ServerLifecycleState

    auth = CaptainAuthService(config_path=isolated_log / "captain_bidding.json")
    pins = list(auth.get_pins().values())

    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.bind(("127.0.0.1", 0))
    free_port = probe.getsockname()[1]
    probe.close()

    session = AuctionSession()
    session.autosave = None
    session.start(seed=1)
    server = CaptainBiddingServer(session, host="127.0.0.1", port=free_port, auth=auth)

    with TestClient(server.app) as client:
        login_response = client.post("/api/login", json={"pin": pins[0]})
        token = login_response.json()["token"]

    server.start()
    deadline = time.time() + 5
    while time.time() < deadline and server.state == ServerLifecycleState.STARTING:
        time.sleep(0.02)
    server.stop()

    log_path = isolated_log / "logs" / "calgary_vipers_auction.log"
    content = log_path.read_text(encoding="utf-8")
    assert "START requested" in content
    assert "RUNNING" in content or "FAILED" in content
    for pin in pins:
        assert pin not in content
    assert token not in content
