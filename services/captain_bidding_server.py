"""Local server for captain phone bidding.

Captains authenticate with team-specific PINs and can submit bids from
phones connected to the same local network as the organizer laptop.

The server handles authentication, auction-state viewing, and bid submission.
SOLD and UNSOLD actions remain organizer-only desktop controls, and manual
desktop bidding remains available regardless of phone connectivity.
"""

from __future__ import annotations

import asyncio
import socket
import threading
import time
import traceback
from enum import Enum
from pathlib import Path

import uvicorn
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel

from models.auction import AuctionStatus
from models.player import Player, player_base_price
from models.second_auction import SecondAuctionState, SecondAuctionStatus
from models.team import Team
from services import second_auction_bidding_service as sabs
from services.app_logging import log_event
from services.auction_service import AuctionTransactionError, maximum_legal_bid
from services.auction_session_service import AuctionSession
from services.captain_auth_service import CaptainAuthService
from services.config_service import ROOT_DIR
from services.second_auction_bidding_service import SecondAuctionBiddingError

DEFAULT_PORT = 8765

# How many additional ports above DEFAULT_PORT/the configured port to try
# if the preferred one is already taken (e.g. by a previous instance of
# this app that didn't fully exit) — see CaptainBiddingServer._reserve_port.
PORT_FALLBACK_RANGE = 10

# Bound server startup so a failed launch cannot leave the UI permanently
# stuck in the STARTING state.
_STARTUP_TIMEOUT_SECONDS = 20.0

_PLAYERS_PHOTO_DIR = (ROOT_DIR / "assets" / "players").resolve()
_PHONE_PAGE_PATH = ROOT_DIR / "assets" / "captain_bidding" / "phone.html"

_LOGIN_ERROR_STATUS = {
    "invalid_pin": 401,
    "already_connected": 409,
    "rate_limited": 429,
}


class ServerLifecycleState(str, Enum):
    """The captain-bidding server's real, observable lifecycle — Settings
    reads this directly rather than inferring status from `is_running`
    alone, so a start failure is always visible and explained instead of
    silently looking identical to "never started" (see PROJECT_CONTEXT.md's
    "Windows RC0 Packaged-Runtime Stabilization")."""

    OFF = "OFF"
    STARTING = "STARTING"
    RUNNING = "RUNNING"
    FAILED = "FAILED"
    STOPPING = "STOPPING"


def _is_private_ipv4(address: str) -> bool:
    """True for an RFC1918 private-range IPv4 address — the kind a phone
    on the same home/venue Wi-Fi or a mobile hotspot would actually be
    handed, as opposed to, say, a virtual adapter's link-local or a
    container-bridge address that happens to also be non-loopback."""
    parts = address.split(".")
    if len(parts) != 4:
        return False
    try:
        octets = [int(part) for part in parts]
    except ValueError:
        return False
    if any(octet < 0 or octet > 255 for octet in octets):
        return False
    first, second = octets[0], octets[1]
    if first == 10:
        return True
    if first == 172 and 16 <= second <= 31:
        return True
    if first == 192 and second == 168:
        return True
    return False


def _candidate_lan_ips() -> list[str]:
    """Every non-loopback IPv4 address this machine could plausibly be
    reached on, gathered from two independent sources so a failure in
    one doesn't leave the organizer with no address at all:

    1. The UDP-route trick: ask the OS which local interface it would use
       to reach a public address, without ever sending a packet (UDP is
       connectionless) or requiring actual internet access — this is
       usually correct and is tried first.
    2. Every address the OS resolves for this machine's own hostname —
       a fallback that can succeed in environments where (1) fails
       outright (no default route at all, certain VPN/virtual-adapter
       configurations) since it doesn't depend on routing to an external
       address.
    """
    candidates: list[str] = []

    log_event("Captain bidding server: LAN_02 UDP route attempt")
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        candidates.append(sock.getsockname()[0])
    except OSError:
        pass
    finally:
        sock.close()

    log_event("Captain bidding server: LAN_03 hostname fallback")
    try:
        _hostname, _aliases, addresses = socket.gethostbyname_ex(socket.gethostname())
        candidates.extend(addresses)
    except OSError:
        pass

    # De-duplicate while preserving order, and never offer loopback as a
    # "LAN" address — a phone can never reach it.
    seen: set[str] = set()
    unique_non_loopback: list[str] = []
    for address in candidates:
        if address.startswith("127.") or address in seen:
            continue
        seen.add(address)
        unique_non_loopback.append(address)
    return unique_non_loopback


def _threaded_server_event_loop() -> asyncio.AbstractEventLoop:
    """The event loop uvicorn uses for its background server thread.

    On Windows, `asyncio`'s default (since 3.8) is `ProactorEventLoop`
    (built on IOCP) — uvicorn's own `loop="auto"`/`"asyncio"` factories
    pick it automatically. Root-caused during the RC1 stabilization
    ticket: after this app had already done meaningful CustomTkinter/PIL
    work (several screen switches, each compositing player-card images)
    on the main thread, starting the captain-bidding server's background
    thread with `ProactorEventLoop` would hang indefinitely — the thread
    stayed alive, but `uvicorn.Server.started` never flipped `True` even
    after 60+ real seconds, confirmed with a real frozen .exe, not
    guessed. `SelectorEventLoop` — uvicorn's own alternative, normally
    selected via its `use_subprocess` flag for constrained environments —
    doesn't depend on IOCP and started reliably (tens of milliseconds)
    in the exact same reproduction. This server never uses subprocesses,
    pipes, or `add_reader` on anything but plain TCP sockets, so
    `SelectorEventLoop` has no functional gap for our use case; only
    `ProactorEventLoop`-specific features (subprocess support) are
    unavailable, and this server never needs them.
    """
    return asyncio.SelectorEventLoop()


def get_lan_ip() -> str | None:
    """Best-effort detection of this machine's LAN-facing IPv4 address —
    the address a phone on the same Wi-Fi/hotspot would actually reach.

    Returns `None` — never the misleading `"127.0.0.1"` — when no usable
    candidate is found, so a caller can correctly show "LAN address
    unavailable" rather than an address that looks valid but no phone
    could ever actually reach. Prefers an RFC1918 private address when
    multiple candidates exist (the organizer's real Wi-Fi/hotspot
    address, not some unrelated virtual adapter). Never requires
    internet access — every candidate is discovered locally.
    """
    log_event("Captain bidding server: LAN_01 begin")
    candidates = _candidate_lan_ips()
    if not candidates:
        log_event("Captain bidding server: LAN_04 selected IP: none available")
        return None
    private = [address for address in candidates if _is_private_ipv4(address)]
    selected = private[0] if private else candidates[0]
    log_event(f"Captain bidding server: LAN_04 selected IP: {selected}")
    return selected


def _fpl_display(points: int | None) -> str:
    """Mirrors ui/player_card_art.fpl_display's exact rule (None -> N/A,
    a real value including 0 -> str(value)) — duplicated as one line
    rather than importing from ui/, which would invert this project's
    "services never depend on ui" layering for a single-line helper."""
    return "N/A" if points is None else str(points)


class LoginRequest(BaseModel):
    pin: str


class BidRequest(BaseModel):
    """Deliberately has no `team_id` field: which team a bid is for comes
    only from the caller's authenticated session token, never from the
    request body — see this module's docstring."""

    amount: int


class CaptainBiddingServer:
    """Owns the FastAPI app plus the uvicorn server/thread lifecycle for
    one running app instance. Every route reads `self.session` fresh on
    each request — nothing here caches auction state, so a phone always
    sees whatever the organizer's desktop most recently did, and vice
    versa (same shared `AuctionSession`, never a copy).

    `self.auth` (a `CaptainAuthService`) is a public attribute: Settings
    and Live Auction call it directly, in-process, for PIN display,
    connection status, and organizer reset/regenerate actions — never
    over HTTP.
    """

    def __init__(
        self,
        session: AuctionSession,
        host: str = "0.0.0.0",
        port: int = DEFAULT_PORT,
        auth: CaptainAuthService | None = None,
    ) -> None:
        self.session = session
        self.host = host
        self.port = port
        self.auth = auth if auth is not None else CaptainAuthService()
        self._server: uvicorn.Server | None = None
        self._thread: threading.Thread | None = None
        self._actual_port: int | None = None
        self._state_lock = threading.Lock()
        self._state = ServerLifecycleState.OFF
        self._last_error: str | None = None
        # RC2 targeted debug pass: when STARTING began (monotonic clock —
        # never affected by system clock changes), and the last trace
        # marker actually reached, so a timeout message can say exactly
        # where startup got stuck instead of just "it timed out."
        self._starting_since: float | None = None
        self._last_stage: str = "SERVER_START_00 (never started)"
        self.app = self._build_app()

    # ------------------------------------------------------------------
    # Fine-grained startup tracing (RC2 targeted debug pass) — every call
    # both logs immediately (so a console/log-tail shows it live) and
    # records the marker as `_last_stage`, so a forced-FAILED timeout
    # message can name the exact last stage reached.
    # ------------------------------------------------------------------

    def _trace(self, marker: str, detail: str = "") -> None:
        self._last_stage = f"{marker}{(' ' + detail) if detail else ''}"
        log_event(f"Captain bidding server: {self._last_stage}")

    # ------------------------------------------------------------------
    # Lifecycle state — a thread-safe snapshot Settings/Live Auction poll
    # directly, rather than inferring status from `is_running` alone.
    # ------------------------------------------------------------------

    @property
    def state(self) -> ServerLifecycleState:
        with self._state_lock:
            return self._state

    @property
    def last_error(self) -> str | None:
        with self._state_lock:
            return self._last_error

    @property
    def is_running(self) -> bool:
        """Kept for every existing caller that only ever needed a boolean
        (Live Auction's projector line, the "PHONE CONNECTED" indicator,
        `stop()`'s own guard) — equivalent to `state == RUNNING`."""
        return self.state == ServerLifecycleState.RUNNING

    @property
    def actual_port(self) -> int:
        """The port actually bound — may differ from `self.port` if the
        preferred port was taken and a fallback was used (see
        `_reserve_port`). Falls back to the configured port when the
        server has never successfully started, purely for display."""
        return self._actual_port if self._actual_port is not None else self.port

    @property
    def lan_url(self) -> str | None:
        """`None` — never a misleading address — when LAN IP detection
        finds nothing usable. This is deliberately independent of server
        state: a caller checks `state`/`is_running` for "is the server
        up" and `lan_url` only for "what address would a phone use,"
        since the two can fail independently (see PROJECT_CONTEXT.md's
        "Windows RC0 Packaged-Runtime Stabilization")."""
        ip = get_lan_ip()
        if ip is None:
            return None
        return f"http://{ip}:{self.actual_port}"

    # ------------------------------------------------------------------
    # Lifecycle actions
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Idempotent and non-blocking: calling START while already
        starting/running does nothing and never spawns a second server.
        Returns almost immediately — the caller (Settings) sees `state`
        transition STARTING -> RUNNING/FAILED over the next few poll
        ticks rather than the GUI thread blocking on a wait loop, so the
        window never freezes on a slow or failing start. Never touches
        `self.auth` — PINs, tokens, and connection state all survive a
        STOP followed by another START."""
        with self._state_lock:
            if self._state in (ServerLifecycleState.STARTING, ServerLifecycleState.RUNNING):
                return
            self._state = ServerLifecycleState.STARTING
            self._last_error = None
            self._actual_port = None
            self._starting_since = time.monotonic()

        log_event("Captain bidding server: START requested")
        self._trace("SERVER_START_01", "entered start()")
        self._trace("SERVER_START_02", "lifecycle set STARTING")

        self._trace("SERVER_START_03", "selecting port")
        port = self._reserve_port(self.port)
        if port is None:
            message = (
                f"No available port found in {self.port}-{self.port + PORT_FALLBACK_RANGE}. "
                "Close any other program that might be using these ports (including a previous "
                "copy of this app still running) and try again."
            )
            with self._state_lock:
                self._state = ServerLifecycleState.FAILED
                self._last_error = message
            self._trace("SERVER_START_04_FAILED", message)
            return
        self._actual_port = port
        self._trace("SERVER_START_04", f"port selected: {port}")

        self._trace("SERVER_START_05", "creating server thread")
        thread = threading.Thread(target=self._run_server, args=(port,), daemon=True, name="captain-bidding-server")
        self._thread = thread
        thread.start()
        self._trace("SERVER_START_06", "thread started")

        watcher = threading.Thread(
            target=self._watch_startup, args=(thread,), daemon=True, name="captain-bidding-startup-watch"
        )
        watcher.start()

    def _reserve_port(self, preferred_port: int) -> int | None:
        """Best-effort probe for a free port starting at `preferred_port`,
        trying up to `PORT_FALLBACK_RANGE` ports above it before giving
        up. A bind-then-immediately-close probe can't perfectly guarantee
        the same port is still free by the time uvicorn binds it a moment
        later (TOCTOU), but this is more than sufficient for a single
        organizer's laptop — it's not a hostile multi-tenant environment
        — and turns "silently stuck OFF forever" into "just works," which
        is the actual reported problem this exists to fix."""
        for candidate in range(preferred_port, preferred_port + PORT_FALLBACK_RANGE + 1):
            probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                probe.bind((self.host, candidate))
            except OSError:
                continue
            finally:
                probe.close()
            return candidate
        return None

    def _run_server(self, port: int) -> None:
        """The real thread target — RC2 targeted debug pass: rewritten
        from a bare `server.run()` call to explicit, traced,
        manually-managed event-loop control (per the ticket's own
        "controlled thread runner" request), so the trace log shows
        exactly which stage a stuck startup never got past on a real
        machine this session can't reproduce the hang on directly.

        Catches `BaseException`, not just `Exception`: uvicorn signals a
        bind failure by calling `sys.exit(STARTUP_FAILURE)` inside its
        own startup coroutine, which raises `SystemExit` — a
        `BaseException` a plain `except Exception` never catches, so
        that failure mode previously died silently with nothing recorded
        anywhere. Widening further to `BaseException` also catches
        anything else unusual (a `GeneratorExit`, or any exotic error
        surfaced by uvicorn/anyio internals) rather than risking the same
        "thread just vanishes, nothing logged" symptom the real
        organizer report showed for a *second* undiagnosed reason.
        """
        self._trace("SERVER_THREAD_01", "entered thread target")
        loop: asyncio.AbstractEventLoop | None = None
        try:
            self._trace("SERVER_THREAD_02", "creating event loop")
            loop = _threaded_server_event_loop()
            self._trace(
                "SERVER_THREAD_03",
                f"event loop created: {type(loop).__module__}.{type(loop).__qualname__}",
            )
            asyncio.set_event_loop(loop)
            self._trace("SERVER_THREAD_04", "set_event_loop complete")

            self._trace("SERVER_THREAD_05", "constructing uvicorn Config")
            config = uvicorn.Config(self.app, host=self.host, port=port, log_level="warning")
            self._trace("SERVER_THREAD_06", "Config constructed")

            self._trace("SERVER_THREAD_07", "constructing uvicorn Server")
            server = uvicorn.Server(config)
            server.install_signal_handlers = lambda: None  # only the main thread may install signal handlers
            self._server = server
            self._trace("SERVER_THREAD_08", "Server constructed")

            self._trace("SERVER_THREAD_09", "before server.serve()")
            loop.run_until_complete(self._traced_serve(server))
            self._trace("SERVER_THREAD_12", "serve() coroutine returned normally (server stopped)")
        except SystemExit as exc:
            message = f"Server failed to bind to port {port} (it may already be in use)."
            with self._state_lock:
                self._last_error = message
                self._state = ServerLifecycleState.FAILED
            self._trace("SERVER_THREAD_FAILED_SYSTEMEXIT", f"{message} (code={exc.code})")
        except BaseException as exc:  # noqa: BLE001 - intentional: see docstring, must never crash the app
            message = f"Server thread crashed: {type(exc).__name__}: {exc}"
            with self._state_lock:
                self._last_error = message
                self._state = ServerLifecycleState.FAILED
            self._trace("SERVER_THREAD_FAILED_EXCEPTION", message)
            log_event("Captain bidding server: traceback:\n" + traceback.format_exc())
        finally:
            if loop is not None:
                try:
                    loop.close()
                except Exception:  # noqa: BLE001 - best-effort cleanup only
                    pass
                asyncio.set_event_loop(None)

    async def _traced_serve(self, server: uvicorn.Server) -> None:
        """Manually replicates `uvicorn.Server.serve()`'s own internal
        `_serve()` (config load, lifespan construction, `startup()`,
        `main_loop()`, `shutdown()`) instead of calling `serve()` as one
        opaque call — RC2 targeted debug pass: this is what let a real
        reproduction (found in this project's own test suite — a
        captain-bidding server started for the first time in a process
        that had already built and torn down many CustomTkinter dialogs)
        pinpoint that a stuck startup was hanging specifically inside
        `startup()` (lifespan + the actual socket bind) rather than
        `main_loop()` or anywhere in this module's own code. Skips only
        `serve()`'s `capture_signals()` wrapper, which is unconditionally
        a no-op off the main thread (uvicorn's own source: "Signals can
        only be listened to from the main thread") — never relevant here.
        """
        config = server.config
        if not config.loaded:
            config.load()
        server.lifespan = config.lifespan_class(config)
        self._trace("SERVER_THREAD_10", "async serve() entered, calling startup()")
        await server.startup()
        self._trace("SERVER_THREAD_10B", f"startup() returned (should_exit={server.should_exit})")
        if not server.should_exit:
            self._trace("SERVER_THREAD_10C", "entering main_loop()")
            await server.main_loop()
            self._trace("SERVER_THREAD_10D", "main_loop() returned")
        self._trace("SERVER_THREAD_10E", "calling shutdown()")
        await server.shutdown()
        self._trace("SERVER_THREAD_11", "shutdown() complete")

    def _watch_startup(self, thread: threading.Thread) -> None:
        """Runs on its own short-lived background thread (never the Tk
        main thread) so `start()` itself can return immediately — the
        *fast path* for the common case where the server thread and its
        async startup are both healthy. This is intentionally NOT the
        only thing enforcing the startup timeout: see `check_startup_
        timeout`, called from Settings' own Tk-`after()`-driven poll,
        which is the bulletproof backstop that still works even if this
        watcher thread itself never gets scheduled again — exactly what
        a real organizer laptop's log showed (STARTING for ~2 minutes,
        no watcher timeout message ever appeared)."""
        self._trace("WATCHER_01", "started")
        deadline = time.time() + _STARTUP_TIMEOUT_SECONDS
        while time.time() < deadline:
            server = self._server
            if server is not None and server.started:
                self._trace("WATCHER_03", "observed server.started")
                with self._state_lock:
                    if self._state == ServerLifecycleState.STARTING:
                        self._state = ServerLifecycleState.RUNNING
                        self._last_error = None
                log_event(f"Captain bidding server: RUNNING on port {self._actual_port}")
                return
            if not thread.is_alive():
                # _run_server already records FAILED/last_error in the
                # common case; this covers the rare case where the
                # thread died before that handler even ran.
                self._trace("WATCHER_02", "observed thread not alive")
                with self._state_lock:
                    if self._state == ServerLifecycleState.STARTING:
                        self._state = ServerLifecycleState.FAILED
                        if not self._last_error:
                            self._last_error = "Server thread exited unexpectedly during startup."
                return
            time.sleep(0.05)

        self._trace("WATCHER_04", f"timeout (last stage: {self._last_stage})")
        self._force_startup_timeout()

    def check_startup_timeout(self) -> None:
        """Fail a server launch that remains in STARTING beyond the timeout."""
        with self._state_lock:
            if self._state != ServerLifecycleState.STARTING or self._starting_since is None:
                return
            elapsed = time.monotonic() - self._starting_since
            if elapsed < _STARTUP_TIMEOUT_SECONDS:
                return
        self._force_startup_timeout()

    def _force_startup_timeout(self) -> None:
        with self._state_lock:
            if self._state != ServerLifecycleState.STARTING:
                return  # already resolved (RUNNING/FAILED) by the time we got the lock
            self._state = ServerLifecycleState.FAILED
            self._last_error = f"Server startup timed out at {self._last_stage}."
        log_event(f"Captain bidding server: FAILED - startup timed out at {self._last_stage}")

    def stop(self) -> None:
        """Idempotent: safe to call repeatedly, including when the server
        was never started — used both from Settings' STOP button and on
        app exit. Manual desktop bidding, SOLD/UNSOLD, and the rest of
        the app are entirely unaffected by this. Safe to call while
        STARTING (including a startup that is hung/stuck): `should_exit`
        is set if a `Server` object exists yet, and `join(timeout=5)`
        gives up rather than blocking forever if the thread itself is
        unresponsive — the daemon thread is then simply abandoned (it
        can never block process exit) and the state machine still
        cleanly returns to OFF so the organizer can try again."""
        with self._state_lock:
            if self._state == ServerLifecycleState.OFF:
                return
            self._state = ServerLifecycleState.STOPPING
        log_event("Captain bidding server: STOP requested")
        if self._server is not None:
            self._server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._server = None
        self._thread = None
        self._actual_port = None
        with self._state_lock:
            self._state = ServerLifecycleState.OFF
            self._last_error = None
        log_event("Captain bidding server: OFF")

    # ------------------------------------------------------------------
    # State serialization — only what an authenticated captain's phone
    # needs, never save-file internals, other teams' data, or any PIN.
    # ------------------------------------------------------------------

    @staticmethod
    def _team_payload(team: Team, current_player: Player | None, players: list[Player]) -> dict:
        # First Auction Rules V2: the max legal bid is player-aware (GK vs.
        # outfield, whether this team already owns a goalkeeper) — with no
        # current player (auction not started/complete/blocked) there is
        # nothing to bid on, so this falls back to the team's own
        # remaining budget rather than calling the player-aware formula
        # with no player.
        max_legal = (
            max(maximum_legal_bid(team, current_player, players), 0)
            if current_player is not None
            else team.remaining_budget
        )
        return {
            "id": team.id,
            "name": team.name,
            "remaining_budget": team.remaining_budget,
            "roster_size": team.roster_size,
            "max_squad_size": team.max_squad_size,
            "maximum_legal_bid": max_legal,
        }

    def build_state(self, token: str | None) -> dict:
        """Dispatches to the second auction's own state builder once it
        has been started (IN_PROGRESS/COMPLETE/BLOCKED) — see Second
        Auction — Milestone 3's "do not accidentally return first-auction
        remaining budgets during second auction" requirement — and to the
        unchanged first-auction builder otherwise. Both return the same
        core key *names* (`current_player`/`current_bid`/
        `leading_team_name`/`own_team`/`authenticated`/`bidding_enabled`/
        `round_number`/`session_invalid`) so `assets/captain_bidding/
        phone.html` only needs to branch on the additive `auction_phase`
        field for the handful of fields that differ, never reimplement
        its whole rendering path per phase.
        """
        session = self.session
        if not session.started:
            return {"no_active_session": True}

        if session.second_auction_state.is_initialized:
            return self._build_second_auction_state(token, session, session.second_auction_state)
        return self._build_first_auction_state(token, session)

    def _build_first_auction_state(self, token: str | None, session: AuctionSession) -> dict:
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
                "base_price": player_base_price(current_player),
            }

        team_id = self.auth.authenticate(token)
        # A token was supplied but no longer resolves (organizer reset it,
        # all PINs were regenerated, or the app/server restarted since it
        # was issued) — distinct from "never logged in," so the phone can
        # show a clear "please log in again" message instead of silently
        # reverting to a blank login screen with no explanation.
        session_invalid = token is not None and team_id is None

        own_team_payload = None
        team_name = None
        if team_id is not None:
            own_team = next((team for team in session.teams if team.id == team_id), None)
            if own_team is not None:
                own_team_payload = self._team_payload(own_team, current_player, session.players)
                team_name = own_team.name

        return {
            "no_active_session": False,
            "auction_phase": "FIRST_AUCTION",
            "session_mode": session.mode.value if session.mode else None,
            "auction_status": auction.status.value,
            "round_number": session.round_number,
            "bidding_enabled": auction.status == AuctionStatus.IN_PROGRESS and current_player is not None,
            "current_player": current_player_payload,
            "current_bid": auction.current_bid,
            "leading_team_name": leading_team.name if leading_team is not None else None,
            "authenticated": team_id is not None,
            "team_id": team_id,
            "team_name": team_name,
            "own_team": own_team_payload,
            "session_invalid": session_invalid,
        }

    @staticmethod
    def _second_auction_own_team_payload(
        state: SecondAuctionState, team: Team, current_player: Player | None
    ) -> dict:
        roster_size = sabs.team_roster_size(state, team.id)
        remaining_budget = sabs.team_remaining_budget(state, team.id)
        max_legal = max(sabs.team_max_legal_bid(state, team), 0)
        eligible = current_player is not None and sabs.team_can_bid_for_player(state, team, current_player)
        return {
            "id": team.id,
            "name": team.name,
            "remaining_budget": remaining_budget,
            "roster_size": roster_size,
            "max_squad_size": team.max_squad_size,
            "maximum_legal_bid": max_legal,
            "eligible": eligible,
            "ineligibility_reason": None if eligible else sabs.team_ineligibility_reason(state, team),
        }

    def _build_second_auction_state(
        self, token: str | None, session: AuctionSession, state: SecondAuctionState
    ) -> dict:
        players_by_id = {player.id: player for player in session.players}
        current_player_id = state.current_player_id
        current_player = players_by_id.get(current_player_id) if current_player_id is not None else None

        current_player_payload = None
        if current_player is not None:
            pool_entry = sabs.release_pool_lookup(
                session.teams, session.second_auction_setup, players_by_id
            ).get(current_player.id)
            photo_url = f"/photos/{Path(current_player.photo_path).name}" if current_player.photo_path else None
            current_player_payload = {
                "full_name": current_player.full_name,
                "position": current_player.position.value,
                "overall_rating": current_player.overall_rating,
                "fpl_display": _fpl_display(current_player.last_season_fpl_points),
                "photo_url": photo_url,
                "base_price": player_base_price(current_player),
                "previous_team_name": pool_entry.previous_team.name if pool_entry is not None else None,
                "original_first_auction_price": pool_entry.original_price if pool_entry is not None else None,
            }

        leading_team = (
            next((team for team in session.teams if team.id == state.leading_team_id), None)
            if state.leading_team_id is not None
            else None
        )

        team_id = self.auth.authenticate(token)
        session_invalid = token is not None and team_id is None

        own_team_payload = None
        team_name = None
        if team_id is not None:
            own_team = next((team for team in session.teams if team.id == team_id), None)
            if own_team is not None:
                own_team_payload = self._second_auction_own_team_payload(state, own_team, current_player)
                team_name = own_team.name

        resolved = len(sabs.sold_player_ids(state))
        total = len(state.released_player_ids)

        return {
            "no_active_session": False,
            "auction_phase": "SECOND_AUCTION",
            "session_mode": session.mode.value if session.mode else None,
            "auction_status": state.status.value,
            "round_number": state.round_number,
            "bidding_enabled": state.status == SecondAuctionStatus.IN_PROGRESS and current_player is not None,
            "current_player": current_player_payload,
            "current_bid": state.current_bid,
            "leading_team_name": leading_team.name if leading_team is not None else None,
            "authenticated": team_id is not None,
            "team_id": team_id,
            "team_name": team_name,
            "own_team": own_team_payload,
            "session_invalid": session_invalid,
            "progress": {"resolved": resolved, "total": total},
        }

    # ------------------------------------------------------------------
    # FastAPI app / routes
    # ------------------------------------------------------------------

    def _build_app(self) -> FastAPI:
        app = FastAPI(title="Calgary Vipers Captain Bidding", docs_url=None, redoc_url=None)

        def resolve_token(
            x_captain_token: str | None = Header(default=None),
            token: str | None = None,
        ) -> str | None:
            """A phone's session token, read from the `X-Captain-Token`
            header (what `assets/captain_bidding/phone.html` sends) or,
            as a convenience for manual testing, a `?token=` query
            param. Used as a FastAPI dependency so every authenticated
            route resolves it identically."""
            return x_captain_token or token

        @app.get("/health")
        def health() -> dict:
            return {"ok": True}

        @app.post("/api/login")
        def api_login(payload: LoginRequest, request: Request) -> JSONResponse:
            remote_key = request.client.host if request.client is not None else None
            result = self.auth.login(payload.pin, remote_key=remote_key)
            body = {
                "accepted": result.accepted,
                "token": result.token,
                "team_id": result.team_id,
                "team_name": self.auth.team_name(result.team_id) if result.team_id is not None else None,
                "error_code": result.error_code,
                "message": result.message,
            }
            status_code = 200 if result.accepted else _LOGIN_ERROR_STATUS.get(result.error_code, 401)
            return JSONResponse(body, status_code=status_code)

        @app.post("/api/logout")
        def api_logout(resolved_token: str | None = Depends(resolve_token)) -> dict:
            self.auth.logout(resolved_token)
            return {"ok": True}

        @app.post("/api/heartbeat")
        def api_heartbeat(resolved_token: str | None = Depends(resolve_token)) -> dict:
            return {"ok": self.auth.heartbeat(resolved_token)}

        @app.get("/api/state")
        def api_state(resolved_token: str | None = Depends(resolve_token)) -> JSONResponse:
            return JSONResponse(self.build_state(resolved_token))

        @app.post("/api/bid")
        def api_bid(payload: BidRequest, resolved_token: str | None = Depends(resolve_token)) -> dict:
            """Submit a bid to the currently active auction phase.

            Team identity always comes from the authenticated captain token,
            never from the request body.
            """
            if not self.session.started:
                raise HTTPException(status_code=409, detail="No active auction session.")
            team_id = self.auth.authenticate(resolved_token)
            if team_id is None:
                raise HTTPException(status_code=401, detail="Not authenticated. Please log in again.")
            if self.session.second_auction_state.is_initialized:
                try:
                    result = self.session.place_second_auction_live_bid(team_id, payload.amount)
                except SecondAuctionBiddingError as exc:
                    raise HTTPException(status_code=409, detail=str(exc)) from exc
            else:
                try:
                    result = self.session.place_live_bid(team_id, payload.amount)
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
