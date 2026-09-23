"""A small, bounded diagnostic log for packaged builds.

The final, user-facing RC1 .exe is windowed (no visible console — see
`calgary_vipers_auction.spec`'s `CVA_DEBUG_CONSOLE` toggle), so this is
the only way an organizer (or a follow-up debugging session) can see
what actually happened — captain-bidding server start/stop, LAN
detection results, and any uncaught server-thread exception — without
rebuilding a console-attached debug variant.

Writes to `logs/calgary_vipers_auction.log` under
`services.runtime_paths.WRITABLE_ROOT` (beside the .exe when packaged,
the repository root in source mode — the same split already used for
`config/` and `saves/`). Uses `RotatingFileHandler` so the file never
grows unbounded across a long tournament day.

Never logs a PIN or an auth token: every call site here passes a
pre-formatted, already-safe message string — this module has no
knowledge of PIN/token values, so there is nothing sensitive for it to
accidentally include.
"""

from __future__ import annotations

import logging
import logging.handlers

from services.runtime_paths import ensure_writable_dir

_MAX_BYTES = 2 * 1024 * 1024  # ~2MB per file
_BACKUP_COUNT = 3
_LOGGER_NAME = "calgary_vipers_auction"

_logger: logging.Logger | None = None


def get_logger() -> logging.Logger:
    """Lazily configured — creating `logs/` and attaching the file handler
    only happens on first actual use, never merely on import, matching
    this project's "no disk I/O just from importing a module" convention
    (see `services/preferences_service.py`/`captain_auth_service.py`).

    Checks specifically for *our own* `RotatingFileHandler` already being
    attached — not just "does this logger have any handlers at all" —
    since another party (a test framework's log-capture plugin, or any
    other code that happens to call `logging.getLogger` on this same
    name) can legitimately attach its own handler first. Checking only
    "handlers is non-empty" would then skip attaching ours entirely,
    silently losing every log line to disk while still trusting
    `propagate = False` was set (it wouldn't have been) — found via this
    module's own test suite, not guessed.
    """
    global _logger
    if _logger is not None:
        return _logger

    logger = logging.getLogger(_LOGGER_NAME)
    logger.setLevel(logging.INFO)
    has_own_handler = any(isinstance(handler, logging.handlers.RotatingFileHandler) for handler in logger.handlers)
    if not has_own_handler:
        log_dir = ensure_writable_dir("logs")
        handler = logging.handlers.RotatingFileHandler(
            log_dir / "calgary_vipers_auction.log",
            maxBytes=_MAX_BYTES,
            backupCount=_BACKUP_COUNT,
            encoding="utf-8",
        )
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(handler)
        logger.propagate = False
    _logger = logger
    return logger


def log_event(message: str) -> None:
    """Best-effort info-level log line. Never raises: a logging failure
    (e.g. a read-only filesystem, a locked file) must never crash or
    interrupt the feature it's describing — logging is purely
    observational."""
    try:
        get_logger().info(message)
    except OSError:
        pass
