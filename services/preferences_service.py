"""Organizer-facing app preferences: fullscreen/display and SOLD/UNSOLD
confirmation-dialog toggles.

Deliberately separate from auction session persistence
(`services/persistence_service.py`): these are app-local UI preferences,
never tournament/auction data, so they live in their own file
(`config/user_preferences.json`) and are never written into a MOCK/LIVE
save, `data/players.json`, or `data/teams.json`. Nothing here can affect
an auction transaction's business-rule validation — a confirmation
preference only controls whether a dialog is shown before calling the
exact same `AuctionSession.sell_current_player`/`mark_current_player_unsold`
path (see `ui/screens/live_auction_screen.py`).

A missing, corrupt, or partially-invalid preferences file is never a
crash: every field falls back to its documented default independently, so
one bad/missing key can't take down the rest.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path

from services.config_service import ROOT_DIR

PREFERENCES_PATH = ROOT_DIR / "config" / "user_preferences.json"


@dataclass(frozen=True, slots=True)
class UserPreferences:
    """Every field defaults to the safe, documented default so a missing
    file (a fresh install) behaves identically to an explicit reset."""

    remember_fullscreen: bool = False
    fullscreen_enabled: bool = False
    confirm_sold: bool = True
    confirm_unsold: bool = True

    def to_dict(self) -> dict[str, bool]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "UserPreferences":
        """Reads each field independently with its own default fallback
        (rather than `cls(**data)`) so a partially-invalid file — an
        unexpected extra key, a wrong-typed value, one missing field —
        degrades to that one field's default instead of discarding the
        whole file."""
        return cls(
            remember_fullscreen=bool(data.get("remember_fullscreen", False)),
            fullscreen_enabled=bool(data.get("fullscreen_enabled", False)),
            confirm_sold=bool(data.get("confirm_sold", True)),
            confirm_unsold=bool(data.get("confirm_unsold", True)),
        )


DEFAULT_PREFERENCES = UserPreferences()


def _atomic_write_json(path: Path, data: dict) -> None:
    """Same atomic-write pattern as services/persistence_service.py's
    `atomic_write_json`, duplicated at this small scale rather than
    imported — preferences are a deliberately independent concern from
    auction-session persistence, not a consumer of it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(path.name + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp_path, path)


def load_preferences(path: Path | None = None) -> UserPreferences:
    """Never raises: a missing file, unreadable file, invalid JSON, or a
    JSON value that isn't an object all fall back to `UserPreferences()`
    (every field at its default) rather than crashing the Settings screen
    or app startup."""
    preferences_path = path or PREFERENCES_PATH
    try:
        raw = json.loads(preferences_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return UserPreferences()
    if not isinstance(raw, dict):
        return UserPreferences()
    return UserPreferences.from_dict(raw)


def save_preferences(preferences: UserPreferences, path: Path | None = None) -> None:
    """Persist `preferences` atomically. Raises `OSError` only for a
    genuine filesystem failure (e.g. a read-only volume) — callers that
    want a non-fatal save can catch that, but there is no separate
    exception type here since, unlike auction saves, a failed preferences
    write has no in-memory state that could drift out of sync with disk."""
    preferences_path = path or PREFERENCES_PATH
    _atomic_write_json(preferences_path, preferences.to_dict())


def reset_preferences(path: Path | None = None) -> UserPreferences:
    """Restore and persist the default preferences. Touches only the
    preferences file — never an auction save, `data/players.json`, or
    `data/teams.json`."""
    defaults = UserPreferences()
    save_preferences(defaults, path)
    return defaults
