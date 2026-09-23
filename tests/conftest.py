"""Shared pytest fixtures for GUI-frame tests.

Repeatedly creating/destroying separate CTk() roots across test modules in
one process is a known source of flaky TclError teardown timing on this
platform. Frame-level screen tests (screens built as a child of a root,
not MainWindow itself) share one session-scoped hidden root instead.
"""

import pytest

# Eager imports, deliberately at collection time (before any fixture ever
# runs): every one of these modules does `from services.runtime_paths
# import WRITABLE_ROOT`, capturing a *snapshot* of that value into its own
# namespace at first import — not a live reference back to
# `runtime_paths.WRITABLE_ROOT`. `_isolate_app_log` below monkeypatches
# `runtime_paths.WRITABLE_ROOT` itself (the only way to isolate
# `services/app_logging.py`, which has no precomputed constant of its
# own to patch directly). If any of these modules' *first* import in the
# whole pytest process happened to occur later, inside some test's
# `_isolate_app_log`-patched context, its captured `WRITABLE_ROOT`
# snapshot (and therefore `PIN_CONFIG_PATH`/`PREFERENCES_PATH`/
# `SAVES_DIR`) would be silently and permanently wrong (cached in
# `sys.modules`) for every other test in the same run — found via this
# exact failure while adding `_isolate_app_log`, not guessed. Importing
# them here first guarantees their snapshots are captured from the real,
# unpatched value.
import services.captain_auth_service  # noqa: F401,E402
import services.persistence_service  # noqa: F401,E402
import services.preferences_service  # noqa: F401,E402


@pytest.fixture(autouse=True)
def _isolate_captain_pin_storage(tmp_path, monkeypatch):
    """Every test gets its own captain-bidding PIN storage file.

    A real `MainWindow` (built directly here, in test_ui_shell.py, or in
    test_live_auction_screen.py) constructs a default-configured
    `CaptainAuthService` (services/captain_auth_service.py) unless a test
    injects its own — which otherwise reads and can even regenerate the
    real `config/captain_bidding.json`. Applying this globally, rather
    than per test file, means no future test that happens to build a
    real MainWindow can accidentally disturb an organizer's actual
    saved tournament PINs.
    """
    from services import captain_auth_service

    monkeypatch.setattr(captain_auth_service, "PIN_CONFIG_PATH", tmp_path / "captain_bidding.json")


@pytest.fixture(autouse=True)
def _isolate_app_log(tmp_path, monkeypatch):
    """Every test gets its own diagnostic-log location.

    A real `MainWindow` logs an app-startup line (`ui/main_window.py`)
    via `services/app_logging.py`, which otherwise creates and writes to
    the real `logs/calgary_vipers_auction.log` beside the repository
    root. Unlike `PIN_CONFIG_PATH`/`PREFERENCES_PATH`/`SAVES_DIR` (each
    a module-level constant computed once at import time, so those are
    isolated by patching the constant directly), `app_logging.get_logger`
    reads `services.runtime_paths.WRITABLE_ROOT` lazily on first actual
    use — so patching that source attribute here works. The cached
    logger/handler are also reset so a test that ran before this
    patch took effect can't leave a stale handle to the real log file.
    """
    import logging
    import logging.handlers

    from services import app_logging
    from services import runtime_paths

    monkeypatch.setattr(runtime_paths, "WRITABLE_ROOT", tmp_path)
    logger = logging.getLogger(app_logging._LOGGER_NAME)
    for handler in list(logger.handlers):
        if isinstance(handler, logging.handlers.RotatingFileHandler):
            logger.removeHandler(handler)
            handler.close()
    monkeypatch.setattr(app_logging, "_logger", None)


@pytest.fixture(scope="session")
def hidden_root():
    tkinter = pytest.importorskip("tkinter")
    import customtkinter as ctk

    try:
        root = ctk.CTk()
        root.withdraw()
    except tkinter.TclError as exc:
        pytest.skip(f"No display/Tk backend available for GUI test: {exc}")

    yield root
    root.destroy()
