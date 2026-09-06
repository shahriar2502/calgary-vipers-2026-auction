"""Milestone 9: saving, loading, and listing auction sessions to/from disk.

The only module allowed to perform file I/O for auction session state.
`AuctionSession` (services/auction_session_service.py) has no knowledge of
files or JSON — it only calls an injected `autosave` hook, which
`ui/main_window.py` wires to `autosave_hook` below. Screens never read or
write save files directly.

Save files live under `saves/` (a sibling of `data/` and `assets/`, using
the same `ROOT_DIR`-relative pattern as the rest of the app so paths work
whether run via `python main.py` or a later packaged build):

    saves/
      mocks/
        <session_id>.json        -- e.g. mock_20260905T142230Z_a1b2c3d4.json;
                                     any number of independent practice sessions
      live/
        live_active.json         -- the one current/active tournament session
        live_archived_<id>.json  -- a previous live session, kept (never deleted)
                                     when a new one is started over it

Every save reuses the existing `Player.to_dict()`/`Team.to_dict()`/
`Auction.to_dict()` (and their `from_dict()` counterparts) rather than a
second, competing serialization format — a save file is exactly session
metadata (schema version, session id/mode/name/timestamps) wrapped around
those three lists/objects. Because a saved session embeds its own
complete player/team snapshot, loading it never re-reads
`data/players.json`/`data/teams.json` — a later canonical data change can
never retroactively alter an already-saved session (only a *new* session
picks up new canonical data).

Every write goes through `atomic_write_json`: serialize to a temporary
file in the same directory, flush and close it, then `os.replace` it over
the target. `os.replace` is atomic on both POSIX and Windows for a
same-directory (same-volume) rename, so a crash or failure partway through
writing the temp file can never leave a corrupted file in place of a
previously valid save — the previous save simply survives untouched.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from models.auction import Auction
from models.player import Player
from models.team import Team
from services.auction_session_service import AuctionSession, SessionMode
from services.config_service import ROOT_DIR

SCHEMA_VERSION = 1

SAVES_DIR = ROOT_DIR / "saves"
MOCKS_DIR = SAVES_DIR / "mocks"
LIVE_DIR = SAVES_DIR / "live"
LIVE_ACTIVE_PATH = LIVE_DIR / "live_active.json"

_REQUIRED_TOP_LEVEL_KEYS = {"schema_version", "session_id", "session_mode", "players", "teams", "auction"}


class PersistenceError(Exception):
    """Raised for any save/load failure this module can't safely recover
    from: an I/O error, corrupt/malformed JSON, an unsupported or missing
    schema version, or a snapshot that fails cross-reference validation
    against its own embedded player/team/auction data. Callers only ever
    need to catch this one type — a raw OSError/JSONDecodeError/KeyError
    never escapes `save_session`/`load_session`.
    """


@dataclass(frozen=True, slots=True)
class SavedSessionSummary:
    """Lightweight metadata about one saved session, for listing (e.g. the
    Live Auction launcher's "Saved Sessions" panel) without fully
    reconstructing an AuctionSession for every file on disk. Corrupt/
    unreadable files still produce a summary (`is_corrupt=True`) rather
    than raising, so one bad save can't break the whole listing."""

    path: Path
    session_id: str | None
    mode: SessionMode | None
    name: str | None
    created_at: str | None
    updated_at: str | None
    round_number: int | None
    sold_count: int | None
    status: str | None
    is_corrupt: bool = False
    error: str | None = None


def _now_iso() -> str:
    # Mirrors services/auction_session_service.py's identical one-liner;
    # not shared as a common helper to avoid a circular import (this
    # module already imports from auction_session_service).
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def atomic_write_json(path: Path, data: dict) -> None:
    """Write `data` as JSON to `path` without ever leaving a partially
    written file in place of a previously valid one.

    Serializes to a `.tmp` file in the same directory, flushes and closes
    it (the file handle is fully closed before `os.replace` runs, which
    matters on Windows — an open handle can block a rename), then
    atomically replaces the target. If serialization itself fails (e.g. a
    caller-side bug producing a non-JSON-safe value), the exception
    propagates before `os.replace` is ever reached, so the previous target
    file — if any — is left completely untouched.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(path.name + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp_path, path)


# ----------------------------------------------------------------------
# Save
# ----------------------------------------------------------------------


def session_save_path(mode: SessionMode, session_id: str) -> Path:
    """Where a session of this mode/id is written. LIVE always resolves to
    the single `live_active.json` slot (mode never changes after a session
    is created, so a MOCK session can never be written to this path, and
    vice versa — this mapping is the sole thing that decides where a save
    goes). `session_id` already starts with "mock_"/"live_" (see
    `_generate_session_id`), so the filename is just `<session_id>.json` —
    not prefixed a second time."""
    if mode == SessionMode.LIVE:
        return LIVE_ACTIVE_PATH
    return MOCKS_DIR / f"{session_id}.json"


def build_snapshot(session: AuctionSession, updated_at: str) -> dict:
    """Assemble the full save-file dict for `session`. Pure — does not
    mutate `session` or touch disk; `save_session` commits `updated_at`
    onto the session only after a confirmed successful write."""
    if session.auction is None or session.players is None or session.teams is None:
        raise PersistenceError("Cannot save a session that has not been started.")
    if session.mode is None or session.session_id is None:
        raise PersistenceError("Cannot save a session without a mode and session_id.")
    return {
        "schema_version": SCHEMA_VERSION,
        "session_id": session.session_id,
        "session_mode": session.mode.value,
        "name": session.name,
        "created_at": session.created_at,
        "updated_at": updated_at,
        "players": [player.to_dict() for player in session.players],
        "teams": [team.to_dict() for team in session.teams],
        "auction": session.auction.to_dict(),
    }


def save_session(session: AuctionSession) -> Path:
    """Serialize and atomically persist `session`. Raises `PersistenceError`
    on any failure (never a raw OSError/TypeError); `session.updated_at`
    is advanced only once the write has actually succeeded, so a failed
    save can never be mistaken for a persisted one."""
    timestamp = _now_iso()
    try:
        snapshot = build_snapshot(session, updated_at=timestamp)
        path = session_save_path(session.mode, session.session_id)
        atomic_write_json(path, snapshot)
    except PersistenceError:
        raise
    except (OSError, TypeError, ValueError) as exc:
        raise PersistenceError(f"Failed to save session: {exc}") from exc
    session.updated_at = timestamp
    return path


def autosave_hook(session: AuctionSession) -> None:
    """The default `AuctionSession.autosave` callback. A thin alias for
    `save_session` kept as its own name so `ui/main_window.py` wires
    "the persistence layer's autosave behavior" rather than a specific
    function that happens to also be the manual-save entry point."""
    save_session(session)


# ----------------------------------------------------------------------
# Load / validate
# ----------------------------------------------------------------------


def _validate_snapshot_shape(data: object) -> None:
    """Cheap structural checks that run before any model reconstruction
    is attempted, so a wildly malformed file (not even a dict, or missing
    whole sections) fails fast with a clear message."""
    if not isinstance(data, dict):
        raise PersistenceError("Save file is corrupt: expected a JSON object at the top level.")
    missing = _REQUIRED_TOP_LEVEL_KEYS - data.keys()
    if missing:
        raise PersistenceError(f"Save file is corrupt: missing required field(s) {sorted(missing)}.")

    schema_version = data["schema_version"]
    if not isinstance(schema_version, int) or isinstance(schema_version, bool):
        raise PersistenceError("Save file is corrupt: schema_version must be an integer.")
    if schema_version > SCHEMA_VERSION:
        raise PersistenceError(
            f"Save file uses schema version {schema_version}, newer than this app supports "
            f"(version {SCHEMA_VERSION}). Update the app before opening this save."
        )
    if schema_version < SCHEMA_VERSION:
        raise PersistenceError(
            f"Save file uses schema version {schema_version}, older than this app's current "
            f"format (version {SCHEMA_VERSION}), and cannot be automatically migrated yet."
        )

    if data["session_mode"] not in (SessionMode.MOCK.value, SessionMode.LIVE.value):
        raise PersistenceError(f"Save file is corrupt: unknown session_mode {data['session_mode']!r}.")
    if not isinstance(data["players"], list) or not isinstance(data["teams"], list) or not isinstance(
        data["auction"], dict
    ):
        raise PersistenceError("Save file is corrupt: players/teams/auction have the wrong shape.")


def _reconstruct_models(data: dict) -> tuple[list[Player], list[Team], Auction]:
    """Reuse each model's own `from_dict()` — and therefore its own
    `__post_init__` invariant checks (valid position/status enums,
    non-negative budgets, no duplicate roster/queue ids, consistent
    SOLD/UNSOLD history shape, etc.) — instead of re-implementing any of
    that validation here."""
    try:
        players = [Player.from_dict(item) for item in data["players"]]
    except (TypeError, ValueError, KeyError) as exc:
        raise PersistenceError(f"Save file is corrupt: invalid player record ({exc}).") from exc
    try:
        teams = [Team.from_dict(item) for item in data["teams"]]
    except (TypeError, ValueError, KeyError) as exc:
        raise PersistenceError(f"Save file is corrupt: invalid team record ({exc}).") from exc
    try:
        auction = Auction.from_dict(data["auction"])
    except (TypeError, ValueError, KeyError) as exc:
        raise PersistenceError(f"Save file is corrupt: invalid auction state ({exc}).") from exc
    return players, teams, auction


def _validate_cross_references(players: list[Player], teams: list[Team], auction: Auction) -> None:
    """Checks the models can't perform on themselves in isolation: do the
    ids teams/auction reference actually resolve *within this snapshot*?
    Deliberately never touches today's canonical data/players.json —a
    historical save is validated only against its own embedded snapshot,
    per Milestone 9's "do not over-validate historical auction states
    against today's canonical JSON" requirement."""
    player_ids = [player.id for player in players]
    if len(player_ids) != len(set(player_ids)):
        raise PersistenceError("Save file is corrupt: duplicate player ids in snapshot.")
    player_ids_set = set(player_ids)
    players_by_id = {player.id: player for player in players}
    team_names = {team.name for team in teams}

    for team in teams:
        if team.captain_player_id not in player_ids_set:
            raise PersistenceError(
                f"Save file is corrupt: {team.name}'s captain id {team.captain_player_id} "
                "is not in the player snapshot."
            )
        if not players_by_id[team.captain_player_id].is_captain:
            raise PersistenceError(f"Save file is corrupt: {team.name}'s captain is not marked as a captain.")
        unresolved_roster = [pid for pid in team.roster if pid not in player_ids_set]
        if unresolved_roster:
            raise PersistenceError(
                f"Save file is corrupt: {team.name}'s roster references unknown player id(s) {unresolved_roster}."
            )

    unresolved_queue = [pid for pid in auction.queue if pid not in player_ids_set]
    if unresolved_queue:
        raise PersistenceError(
            f"Save file is corrupt: auction queue references unknown player id(s) {unresolved_queue}."
        )

    for entry in auction.history:
        if entry.player_id not in player_ids_set:
            raise PersistenceError(
                f"Save file is corrupt: history entry references unknown player id {entry.player_id}."
            )
        if entry.status == "SOLD" and entry.team not in team_names:
            raise PersistenceError(f"Save file is corrupt: history entry references unknown team {entry.team!r}.")


def restore_session(data: dict) -> AuctionSession:
    """Validate `data` (structurally, per-model, and cross-referentially)
    and reconstruct an `AuctionSession` equivalent to a freshly-created
    one — ready to hand straight to the Live Auction/Teams screens. Does
    not wire an `autosave` hook (see `AuctionSession.adopt`'s docstring);
    the caller re-attaches whatever hook it already had."""
    _validate_snapshot_shape(data)
    players, teams, auction = _reconstruct_models(data)
    _validate_cross_references(players, teams, auction)
    return AuctionSession(
        auction=auction,
        players=players,
        teams=teams,
        mode=SessionMode(data["session_mode"]),
        session_id=data["session_id"],
        name=data.get("name"),
        created_at=data.get("created_at"),
        updated_at=data.get("updated_at"),
    )


def load_session(path: Path) -> AuctionSession:
    """Read, parse, and restore the session saved at `path`. Raises
    `PersistenceError` for a missing file, unreadable file, invalid JSON,
    or any of `restore_session`'s validation failures — never a raw
    OSError/JSONDecodeError."""
    try:
        raw_text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise PersistenceError(f"Could not read save file {path.name}: {exc}") from exc
    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise PersistenceError(f"Save file {path.name} is corrupt (invalid JSON): {exc}") from exc
    return restore_session(data)


# ----------------------------------------------------------------------
# Listing / management
# ----------------------------------------------------------------------


def summarize_save_file(path: Path) -> SavedSessionSummary:
    """A best-effort, never-raising summary of the save file at `path` —
    used for listing saved sessions without the cost (or validation
    strictness) of fully reconstructing every one via `load_session`. A
    corrupt/unreadable file still produces a summary, flagged
    `is_corrupt=True`, so one bad file can't break an entire listing."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise PersistenceError("save file does not contain a JSON object")
        auction_data = data.get("auction") or {}
        if not isinstance(auction_data, dict):
            auction_data = {}
        history = auction_data.get("history") or []
        sold_count = sum(1 for entry in history if isinstance(entry, dict) and entry.get("status") == "SOLD")
        raw_mode = data.get("session_mode")
        mode = SessionMode(raw_mode) if raw_mode in (SessionMode.MOCK.value, SessionMode.LIVE.value) else None
        return SavedSessionSummary(
            path=path,
            session_id=data.get("session_id"),
            mode=mode,
            name=data.get("name"),
            created_at=data.get("created_at"),
            updated_at=data.get("updated_at"),
            round_number=auction_data.get("round_number"),
            sold_count=sold_count,
            status=auction_data.get("status"),
        )
    except (OSError, json.JSONDecodeError, PersistenceError, KeyError, TypeError, AttributeError) as exc:
        return SavedSessionSummary(
            path=path,
            session_id=None,
            mode=None,
            name=None,
            created_at=None,
            updated_at=None,
            round_number=None,
            sold_count=None,
            status=None,
            is_corrupt=True,
            error=str(exc),
        )


def list_mock_sessions() -> list[SavedSessionSummary]:
    """Every saved mock session, newest-created first. An empty list (not
    an error) if the mocks directory doesn't exist yet — a fresh install
    or a session that has never saved a mock."""
    if not MOCKS_DIR.is_dir():
        return []
    summaries = [summarize_save_file(path) for path in sorted(MOCKS_DIR.glob("mock_*.json"))]
    summaries.sort(key=lambda summary: summary.created_at or "", reverse=True)
    return summaries


def delete_save_file(path: Path) -> None:
    """Permanently remove the save file at `path`, whatever its contents —
    used both by `delete_mock_session` and directly by callers that only
    have a `SavedSessionSummary.path` (e.g. a corrupt file with no
    readable `session_id` to look up)."""
    try:
        path.unlink()
    except FileNotFoundError as exc:
        raise PersistenceError(f"No save file found at {path.name!r}.") from exc
    except OSError as exc:
        raise PersistenceError(f"Failed to delete {path.name!r}: {exc}") from exc


def delete_mock_session(session_id: str) -> None:
    """Permanently remove one mock save by id. Only ever touches a file
    under `mocks/` — structurally incapable of deleting a live save
    regardless of `session_id`'s value."""
    delete_save_file(MOCKS_DIR / f"{session_id}.json")


def detect_active_live_session() -> SavedSessionSummary | None:
    """A summary of the current live_active.json, or None if no LIVE
    session has ever been saved. Used to warn the organizer before they
    overwrite/replace an in-progress tournament session."""
    if not LIVE_ACTIVE_PATH.is_file():
        return None
    return summarize_save_file(LIVE_ACTIVE_PATH)


def archive_live_session() -> Path | None:
    """Move the current `live_active.json` aside — never delete it — so a
    new LIVE session can be created without silently overwriting
    tournament data. Returns the archived file's path, or None if there
    was nothing to archive. Uses `os.replace` (same directory, same
    volume), so this is itself an atomic rename, not a copy-then-delete
    that could lose data partway through."""
    if not LIVE_ACTIVE_PATH.is_file():
        return None
    summary = summarize_save_file(LIVE_ACTIVE_PATH)
    suffix = summary.session_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    archived_path = LIVE_DIR / f"live_archived_{suffix}.json"
    LIVE_DIR.mkdir(parents=True, exist_ok=True)
    os.replace(LIVE_ACTIVE_PATH, archived_path)
    return archived_path
