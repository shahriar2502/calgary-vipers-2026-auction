"""Captain Phone Bidding — Phase 1: a lightweight local FastAPI/uvicorn
server so a captain's phone browser can view the current player and the
live bid, and submit bids — running on the local Wi-Fi network alongside
the CustomTkinter desktop app.

The organizer laptop remains the sole authority: this server can only
read `AuctionSession` state and call `AuctionSession.place_live_bid`
(the exact same entry point the desktop's own bid buttons use — see
`services/live_bid_service.py`). It has no route that sells, marks
unsold, advances the queue, or otherwise mutates a Team/Player directly;
those remain organizer-only actions performed from the desktop UI via
`services/auction_service.py`.

Runs in a background thread (uvicorn's own documented "serve in a
thread" pattern: build a `Server`, disable its signal handlers since
only the main thread may install those, run it in a daemon thread, and
flip `should_exit` to stop it) so it never blocks Tk's mainloop and never
touches a CTk/Tk widget directly — the Live Auction screen instead polls
`AuctionSession`/this server's own state via `after(...)`, matching the
project's existing "never call widget.configure from a non-Tk thread"
rule.
"""

from __future__ import annotations

import socket
import threading
import time
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel

from models.auction import AuctionStatus
from models.team import Team
from services.auction_service import AuctionTransactionError
from services.auction_session_service import AuctionSession
from services.config_service import ROOT_DIR

DEFAULT_PORT = 8765

# A client is "connected" if its most recent /api/state poll happened
# within this window — comfortably more than a couple of missed polls at
# the phone page's 750ms interval, so a brief network hiccup doesn't make
# a still-open phone look disconnected.
_ACTIVE_CLIENT_WINDOW_SECONDS = 5.0

_PLAYERS_PHOTO_DIR = (ROOT_DIR / "assets" / "players").resolve()
_PHONE_PAGE_PATH = ROOT_DIR / "assets" / "captain_bidding" / "phone.html"


def get_lan_ip() -> str:
    """Best-effort detection of this machine's LAN-facing IP address — the
    address a phone on the same Wi-Fi would actually reach. Never raises:
    opening a UDP "connection" to a public address never actually sends a
    packet (UDP is connectionless), it just asks the OS to pick the local
    interface/IP that would be used, which is exactly the LAN IP a router
    routes phone traffic to. Falls back to "127.0.0.1" if no network
    interface is available at all (e.g. Wi-Fi is off)."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        sock.close()


def _fpl_display(points: int | None) -> str:
    """Mirrors ui/player_card_art.fpl_display's exact rule (None -> N/A,
    a real value including 0 -> str(value)) — duplicated as one line
    rather than importing from ui/, which would invert this project's
    "services never depend on ui" layering for a single-line helper."""
    return "N/A" if points is None else str(points)


class BidRequest(BaseModel):
    team_id: int
    amount: int


class SelectTeamRequest(BaseModel):
    team_id: int


class CaptainBiddingServer:
    """Owns the FastAPI app plus the uvicorn server/thread lifecycle for
    one running app instance. Every route reads `self.session` fresh on
    each request — nothing here caches auction state, so a phone always
    sees whatever the organizer's desktop most recently did, and vice
    versa (same shared `AuctionSession`, never a copy)."""

    def __init__(self, session: AuctionSession, host: str = "0.0.0.0", port: int = DEFAULT_PORT) -> None:
        self.session = session
        self.host = host
        self.port = port
        self._server: uvicorn.Server | None = None
        self._thread: threading.Thread | None = None
        self._clients_lock = threading.Lock()
        self._last_seen: dict[str, float] = {}
        self.app = self._build_app()

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def lan_url(self) -> str:
        return f"http://{get_lan_ip()}:{self.port}"

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Idempotent: calling START while already running does nothing —
        never spawns a second server bound to the same port."""
        if self.is_running:
            return
        config = uvicorn.Config(self.app, host=self.host, port=self.port, log_level="warning")
        server = uvicorn.Server(config)
        server.install_signal_handlers = lambda: None  # only the main thread may install signal handlers
        thread = threading.Thread(target=server.run, daemon=True, name="captain-bidding-server")
        self._server = server
        self._thread = thread
        thread.start()
        # Give the server a moment to actually bind/start before returning,
        # so a caller checking `is_running` immediately after `start()`
        # gets an accurate answer rather than a race against thread startup.
        for _ in range(50):  # up to ~2.5s
            if server.started or not thread.is_alive():
                return
            time.sleep(0.05)

    def stop(self) -> None:
        """Idempotent: safe to call repeatedly, including when the server
        was never started — used both from Settings' STOP button and on
        app exit."""
        if self._server is not None:
            self._server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._server = None
        self._thread = None

    # ------------------------------------------------------------------
    # Connected-device tracking (a lightweight heartbeat, not exact
    # per-team presence — see PROJECT_CONTEXT.md's Phase 1 scope note)
    # ------------------------------------------------------------------

    def touch_client(self, client_id: str | None) -> None:
        if not client_id:
            return
        with self._clients_lock:
            self._last_seen[client_id] = time.time()

    def connected_device_count(self) -> int:
        cutoff = time.time() - _ACTIVE_CLIENT_WINDOW_SECONDS
        with self._clients_lock:
            stale = [client_id for client_id, last_seen in self._last_seen.items() if last_seen < cutoff]
            for client_id in stale:
                del self._last_seen[client_id]
            return len(self._last_seen)

    # ------------------------------------------------------------------
    # State serialization — only what the phone needs, never save-file
    # internals or unrelated app state.
    # ------------------------------------------------------------------

    @staticmethod
    def _team_payload(team: Team) -> dict:
        return {
            "id": team.id,
            "name": team.name,
            "remaining_budget": team.remaining_budget,
            "roster_size": team.roster_size,
            "max_squad_size": team.max_squad_size,
            "maximum_legal_bid": max(team.maximum_legal_bid, 0),
        }

    def build_state(self, team_id: int | None) -> dict:
        session = self.session
        if not session.started:
            return {"no_active_session": True}

        auction = session.auction
        current_player = session.current_player
        leading_team = session.leading_team

        current_player_payload = None
        if current_player is not None:
            photo_url = f"/photos/{Path(current_player.photo_path).name}" if current_player.photo_path else None
            current_player_payload = {
                "full_name": current_player.full_name,
                "position": current_player.position.value,
                "overall_rating": current_player.overall_rating,
                "fpl_display": _fpl_display(current_player.last_season_fpl_points),
                "photo_url": photo_url,
            }

        selected_team_payload = None
        if team_id is not None:
            selected_team = next((team for team in session.teams if team.id == team_id), None)
            if selected_team is not None:
                selected_team_payload = self._team_payload(selected_team)

        return {
            "no_active_session": False,
            "session_mode": session.mode.value if session.mode else None,
            "auction_status": auction.status.value,
            "round_number": session.round_number,
            "bidding_enabled": auction.status == AuctionStatus.IN_PROGRESS and current_player is not None,
            "current_player": current_player_payload,
            "current_bid": auction.current_bid,
            "leading_team_name": leading_team.name if leading_team is not None else None,
            "teams": [self._team_payload(team) for team in session.teams],
            "selected_team": selected_team_payload,
        }

    # ------------------------------------------------------------------
    # FastAPI app / routes
    # ------------------------------------------------------------------

    def _build_app(self) -> FastAPI:
        app = FastAPI(title="Calgary Vipers Captain Bidding", docs_url=None, redoc_url=None)

        @app.get("/health")
        def health() -> dict:
            return {"ok": True}

        @app.get("/api/state")
        def api_state(team_id: int | None = None, client_id: str | None = None) -> JSONResponse:
            self.touch_client(client_id)
            return JSONResponse(self.build_state(team_id))

        @app.post("/api/select-team")
        def api_select_team(payload: SelectTeamRequest) -> dict:
            if not self.session.started:
                raise HTTPException(status_code=409, detail="No active auction session.")
            team = next((t for t in self.session.teams if t.id == payload.team_id), None)
            if team is None:
                raise HTTPException(status_code=404, detail="Team not found.")
            return {"ok": True, "team_id": team.id, "team_name": team.name}

        @app.post("/api/bid")
        def api_bid(payload: BidRequest) -> dict:
            if not self.session.started:
                raise HTTPException(status_code=409, detail="No active auction session.")
            try:
                result = self.session.place_live_bid(payload.team_id, payload.amount)
            except AuctionTransactionError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            return {
                "accepted": result.accepted,
                "reason": result.reason,
                "current_bid": result.current_bid,
                "leading_team_id": result.leading_team_id,
            }

        @app.get("/photos/{filename}")
        def get_photo(filename: str) -> FileResponse:
            # Only ever serves a file that both (a) has no path-separator
            # component (rejects "../../secret") and (b) resolves to
            # somewhere inside assets/players/ specifically — never
            # arbitrary filesystem access.
            safe_name = Path(filename).name
            if not safe_name or safe_name != filename:
                raise HTTPException(status_code=404)
            photo_path = (_PLAYERS_PHOTO_DIR / safe_name).resolve()
            if not photo_path.is_relative_to(_PLAYERS_PHOTO_DIR) or not photo_path.is_file():
                raise HTTPException(status_code=404)
            return FileResponse(photo_path)

        @app.get("/", response_class=HTMLResponse)
        def index() -> str:
            try:
                return _PHONE_PAGE_PATH.read_text(encoding="utf-8")
            except OSError:
                return "<h1>Captain Bidding page not found.</h1>"

        return app


def build_server(session: AuctionSession, host: str = "0.0.0.0", port: int = DEFAULT_PORT) -> CaptainBiddingServer:
    return CaptainBiddingServer(session, host=host, port=port)
