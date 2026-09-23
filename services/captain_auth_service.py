"""Captain Phone Bidding — Phase 2: per-team PIN authentication, browser
session tokens, one-active-device-per-team enforcement, and connection
(heartbeat) tracking.

This module is deliberately independent of `AuctionSession`/`Auction`:
which four teams exist and their PINs are tournament-day identity
concerns, not live-bid state, and must be available (and stable) even
before an auction session has been started for the day. It never reads
or writes `Auction.current_bid`/`leading_team_id` (that remains
`services/live_bid_service.py`'s job) and never touches
`data/players.json`/`data/teams.json` — only the team `id`/`name` pairs
those files define are used, purely to label PINs and connection status.

Two closely related but distinct concepts, kept separate throughout:

- AUTHENTICATED: a team's PIN has been used to obtain a still-valid
  session token. This persists until the organizer resets that team's
  connection, all PINs are regenerated, or the app restarts (tokens are
  in-memory only — see PIN STORAGE below). A brief Wi-Fi drop never
  clears this.
- CONNECTED: an authenticated token's owner has been seen (via
  `/api/state` or `/api/heartbeat`) within the last few seconds. This is
  the heartbeat-based "currently active" signal shown as CONNECTED /
  DISCONNECTED in Settings; it can flip to DISCONNECTED and back to
  CONNECTED repeatedly without ever touching authentication.

PIN storage: PINs persist to `config/captain_bidding.json` (gitignored,
alongside `config/user_preferences.json`) so restarting the app during
tournament preparation never silently changes a captain's PIN. A
missing or corrupt file is recovered by generating a fresh, unique set
and persisting it immediately — this never crashes the app, and never
happens again on a later restart once a valid file exists. PINs are
never stored in `data/players.json`, team canonical data, or auction
history/saves.

Session tokens themselves are intentionally NOT persisted (see
`AuctionSession`'s docstring pattern of separating transient runtime
state from what's actually saved) — a full application restart loses
every captain's login and they simply re-enter their still-valid PIN.
A `CaptainBiddingServer` STOP/START (the Settings button, not an app
restart) does *not* lose tokens, since the same `CaptainAuthService`
instance keeps running underneath the HTTP layer the whole time.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import threading
import time
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from models.team import Team
from services.player_service import load_teams
from services.runtime_paths import WRITABLE_ROOT

# Writable runtime state — beside the .exe when packaged, never inside
# PyInstaller's read-only bundle directory. See services/runtime_paths.py.
PIN_CONFIG_PATH = WRITABLE_ROOT / "config" / "captain_bidding.json"
PIN_LENGTH = 4
_PIN_PATTERN = re.compile(r"^\d{4}$")

# A captain is "connected" if their token has been seen (state poll or
# heartbeat call) within this window. Widened from 5s (RC1 stabilization
# ticket) after real packaged-app use showed captains flapping to
# DISCONNECTED far too easily: a mobile browser throttling its timers
# when the screen dims or the tab isn't foreground, a moment of Wi-Fi
# latency, or the packaged app itself briefly stalling can all silently
# push one 750ms poll past 5 seconds. This never affects authentication
# (see `is_authenticated` vs. `is_connected`) — a captain never has to
# re-enter their PIN just because their phone screen dimmed for a bit.
_ACTIVE_WINDOW_SECONDS = 15.0

# Local-LAN-appropriate, not internet-banking-grade: after this many
# consecutive failed PIN attempts from the same client, a short lockout
# applies before another attempt is processed. Keeps a fumbling captain
# from being cut off for long, while ruling out a trivial 10,000-guess
# brute force against a 4-digit PIN over a LAN.
_MAX_FAILED_ATTEMPTS = 5
_LOCKOUT_SECONDS = 10.0


# ---------------------------------------------------------------------
# PIN generation / persistence — testable independently of any running
# server or auth session.
# ---------------------------------------------------------------------


def generate_pin() -> str:
    """One random 4-digit PIN, using `secrets` (not `random`) since this
    is a credential, however low-stakes."""
    return "".join(secrets.choice("0123456789") for _ in range(PIN_LENGTH))


def _generate_unique_pins(team_ids: Iterable[int]) -> dict[int, str]:
    team_ids = list(team_ids)
    while True:
        pins = {team_id: generate_pin() for team_id in team_ids}
        if len(set(pins.values())) == len(team_ids):
            return pins


def _atomic_write_json(path: Path, data: dict) -> None:
    """Same atomic-write pattern as `services/preferences_service.py`
    (temp file + `os.replace`), duplicated at this small scale rather
    than imported, matching this project's established precedent for
    small, independent config files."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(path.name + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp_path, path)


def save_pin_config(path: Path, pins: dict[int, str]) -> None:
    payload = {"pins": {str(team_id): pin for team_id, pin in pins.items()}}
    _atomic_write_json(path, payload)


def load_pin_config(path: Path, team_ids: Iterable[int]) -> tuple[dict[int, str], bool]:
    """Returns `(pins, recovered)`. `recovered` is True whenever the file
    was missing, unreadable, malformed, missing a team, badly formatted,
    or contained a duplicate PIN — in every such case a fresh, unique
    set is generated and persisted immediately, so this only ever
    "recovers" once per actual problem rather than on every launch."""
    team_ids = list(team_ids)

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        pins = _generate_unique_pins(team_ids)
        save_pin_config(path, pins)
        return pins, True

    pins_raw = raw.get("pins") if isinstance(raw, dict) else None
    if not isinstance(pins_raw, dict):
        pins = _generate_unique_pins(team_ids)
        save_pin_config(path, pins)
        return pins, True

    pins: dict[int, str] = {}
    valid = True
    for team_id in team_ids:
        value = pins_raw.get(str(team_id))
        if not isinstance(value, str) or not _PIN_PATTERN.match(value):
            valid = False
            break
        pins[team_id] = value

    if valid and len(set(pins.values())) == len(team_ids):
        return pins, False

    pins = _generate_unique_pins(team_ids)
    save_pin_config(path, pins)
    return pins, True


# ---------------------------------------------------------------------
# Login result
# ---------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LoginResult:
    """`error_code` is one of "invalid_pin", "already_connected",
    "rate_limited", or `None` on success — a caller (the HTTP layer)
    maps this to a status code without needing to inspect `message`."""

    accepted: bool
    token: str | None
    team_id: int | None
    error_code: str | None
    message: str


# ---------------------------------------------------------------------
# The service itself
# ---------------------------------------------------------------------


class CaptainAuthService:
    """Owns PIN storage, session tokens, and connection tracking for the
    four fixed tournament teams. One instance is shared by every route
    on `CaptainBiddingServer` and by the organizer's own Settings screen
    (which talks to it directly, in-process — never over HTTP, per the
    "PIN privacy" rule that PINs are never exposed via any API route).
    """

    def __init__(self, teams: Iterable[Team] | None = None, config_path: Path | None = None) -> None:
        resolved_teams = list(teams) if teams is not None else load_teams()
        self._team_ids: tuple[int, ...] = tuple(team.id for team in resolved_teams)
        self._team_names: dict[int, str] = {team.id: team.name for team in resolved_teams}
        self._config_path = config_path or PIN_CONFIG_PATH

        self._lock = threading.Lock()
        self._pins, self.pin_config_recovered = load_pin_config(self._config_path, self._team_ids)
        self._sessions: dict[str, int] = {}  # token -> team_id
        self._team_tokens: dict[int, str] = {}  # team_id -> token (at most one live token per team)
        self._last_seen: dict[str, float] = {}  # token -> last-seen timestamp
        self._failed_attempts: dict[str, dict[str, float]] = {}  # remote_key -> {"count", "locked_until"}

    # ------------------------------------------------------------------
    # Team identity (read-only, canonical)
    # ------------------------------------------------------------------

    @property
    def team_ids(self) -> tuple[int, ...]:
        return self._team_ids

    def team_name(self, team_id: int) -> str | None:
        return self._team_names.get(team_id)

    # ------------------------------------------------------------------
    # PIN management — organizer-only; never exposed through any HTTP route.
    # ------------------------------------------------------------------

    def get_pins(self) -> dict[int, str]:
        """A copy of every team's current PIN, for the organizer's own
        Settings screen only. No route on `CaptainBiddingServer` ever
        calls this — a phone can never learn another team's PIN, or even
        confirm one exists, through the API."""
        with self._lock:
            return dict(self._pins)

    def get_pin(self, team_id: int) -> str | None:
        with self._lock:
            return self._pins.get(team_id)

    def regenerate_pins(self) -> dict[int, str]:
        """Generates and persists a brand-new, unique PIN per team, and
        invalidates every current session (every captain must re-enter
        their new PIN) — required by the ticket's "regeneration must
        invalidate existing captain logins" rule. Never touches an
        auction session, save file, or canonical player/team data."""
        with self._lock:
            new_pins = _generate_unique_pins(self._team_ids)
            save_pin_config(self._config_path, new_pins)
            self._pins = new_pins
            self._sessions.clear()
            self._team_tokens.clear()
            self._last_seen.clear()
            self.pin_config_recovered = False
            return dict(self._pins)

    # ------------------------------------------------------------------
    # Login / logout
    # ------------------------------------------------------------------

    def login(self, pin: object, remote_key: str | None = None) -> LoginResult:
        """Validate a submitted PIN and, if it's currently unclaimed,
        issue a new session token for that team.

        A wrong PIN never reveals which team (if any) it "almost"
        matched — the rejection message is always the same generic
        "Invalid PIN." Rate limiting is keyed by `remote_key` (the
        caller's IP, when known) and only ever engages after repeated
        failures; a valid PIN always succeeds immediately, lockout or
        not, since a captain who knows their own PIN should never be
        blocked by someone else's failed guesses on a *different* key.
        """
        key = remote_key or "_unknown"
        with self._lock:
            if self._is_locked_out(key):
                return LoginResult(False, None, None, "rate_limited", "Too many attempts. Please wait a moment and try again.")

            if not isinstance(pin, str) or not _PIN_PATTERN.match(pin):
                self._record_failure(key)
                return LoginResult(False, None, None, "invalid_pin", "Invalid PIN.")

            team_id = next((tid for tid, tpin in self._pins.items() if tpin == pin), None)
            if team_id is None:
                self._record_failure(key)
                return LoginResult(False, None, None, "invalid_pin", "Invalid PIN.")

            if team_id in self._team_tokens:
                team_name = self._team_names.get(team_id, "This team")
                return LoginResult(
                    False, None, None, "already_connected",
                    f"{team_name} is already connected. Ask the organizer to reset the connection.",
                )

            self._clear_failures(key)
            token = secrets.token_urlsafe(24)
            self._sessions[token] = team_id
            self._team_tokens[team_id] = token
            self._last_seen[token] = time.time()
            return LoginResult(True, token, team_id, None, "Connected.")

    def logout(self, token: str | None) -> None:
        """Self-service logout for the browser holding `token` — frees
        that team's slot for a different device without organizer
        involvement. A missing/unknown token is a safe no-op."""
        if not token:
            return
        with self._lock:
            team_id = self._sessions.pop(token, None)
            self._last_seen.pop(token, None)
            if team_id is not None and self._team_tokens.get(team_id) == token:
                self._team_tokens.pop(team_id, None)

    # ------------------------------------------------------------------
    # Per-request authentication + heartbeat (used by every authenticated route)
    # ------------------------------------------------------------------

    def authenticate(self, token: str | None) -> int | None:
        """Resolves `token` to the team it belongs to, and — since the
        caller is by definition active right now — records this as a
        heartbeat. Returns `None` for a missing, unknown, reset, or
        regenerated-away token; the caller (the HTTP layer) treats that
        as "not authenticated," never as an error."""
        if not token:
            return None
        with self._lock:
            team_id = self._sessions.get(token)
            if team_id is not None:
                self._last_seen[token] = time.time()
            return team_id

    def heartbeat(self, token: str | None) -> bool:
        """Same effect as `authenticate`, for a caller that only wants to
        know whether the token is still valid, not the team it maps to."""
        return self.authenticate(token) is not None

    # ------------------------------------------------------------------
    # Connection status
    # ------------------------------------------------------------------

    def is_authenticated(self, team_id: int) -> bool:
        with self._lock:
            return team_id in self._team_tokens

    def is_connected(self, team_id: int) -> bool:
        with self._lock:
            token = self._team_tokens.get(team_id)
            if token is None:
                return False
            last_seen = self._last_seen.get(token)
            return last_seen is not None and (time.time() - last_seen) <= _ACTIVE_WINDOW_SECONDS

    def connected_count(self) -> int:
        return sum(1 for team_id in self._team_ids if self.is_connected(team_id))

    def connection_overview(self) -> list[dict]:
        """One entry per team, in canonical order — everything Settings'
        "CONNECTED TEAMS" list needs, and nothing PIN-related (PINs come
        from `get_pins()` separately, so a caller can render this list
        without organizer-only data if it ever needed to)."""
        return [
            {
                "team_id": team_id,
                "team_name": self._team_names.get(team_id, "Unknown"),
                "authenticated": self.is_authenticated(team_id),
                "connected": self.is_connected(team_id),
            }
            for team_id in self._team_ids
        ]

    def reset_team_session(self, team_id: int) -> None:
        """Invalidates whatever session currently owns `team_id`, freeing
        it for a new device to log in with that team's PIN. Never
        touches PINs, roster, budget, auction history, or the live bid —
        purely an authentication-layer action."""
        with self._lock:
            token = self._team_tokens.pop(team_id, None)
            if token is not None:
                self._sessions.pop(token, None)
                self._last_seen.pop(token, None)

    # ------------------------------------------------------------------
    # Basic local-LAN brute-force throttling
    # ------------------------------------------------------------------

    def _is_locked_out(self, key: str) -> bool:
        entry = self._failed_attempts.get(key)
        return entry is not None and time.time() < entry["locked_until"]

    def _record_failure(self, key: str) -> None:
        entry = self._failed_attempts.setdefault(key, {"count": 0.0, "locked_until": 0.0})
        entry["count"] += 1
        if entry["count"] >= _MAX_FAILED_ATTEMPTS:
            entry["locked_until"] = time.time() + _LOCKOUT_SECONDS
            entry["count"] = 0.0

    def _clear_failures(self, key: str) -> None:
        self._failed_attempts.pop(key, None)
