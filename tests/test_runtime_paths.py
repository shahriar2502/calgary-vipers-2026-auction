"""Tests for services/runtime_paths.py: resource vs. writable-data path
resolution in both source mode (`python main.py`) and PyInstaller
packaged-style mode (simulated via monkeypatching `sys.frozen`/
`sys._MEIPASS`/`sys.executable` — no actual packaged build is needed to
verify the resolution logic itself).

See PROJECT_CONTEXT.md's "Windows Packaging — RC0" for why these two
roots must be different in a packaged build (RESOURCE_ROOT is
PyInstaller's read-only bundle directory; WRITABLE_ROOT is the folder
beside the .exe where config/ and saves/ must live).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from services import runtime_paths


def test_is_frozen_false_in_source_mode() -> None:
    assert runtime_paths.is_frozen() is False


def test_source_mode_resource_root_is_repository_root() -> None:
    root = runtime_paths._resource_root()
    assert (root / "main.py").is_file()
    assert (root / "data").is_dir()
    assert (root / "assets").is_dir()


def test_source_mode_writable_root_equals_resource_root() -> None:
    assert runtime_paths._writable_root() == runtime_paths._resource_root()


def test_module_level_resource_root_matches_source_mode_when_not_frozen() -> None:
    """`RESOURCE_ROOT` (read-only canonical data) is never test-isolated —
    unlike `WRITABLE_ROOT`, which `tests/conftest.py`'s autouse
    `_isolate_app_log` fixture deliberately redirects to a per-test
    tmp_path for every test (so a real `MainWindow`'s startup log line
    can never write to the real repository's `logs/`) — so this only
    checks the resource side of the module-level/fresh-computation
    equivalence; `test_source_mode_writable_root_equals_resource_root`
    above already exercises the writable-side formula via fresh function
    calls, unaffected by that fixture."""
    assert runtime_paths.RESOURCE_ROOT == runtime_paths._resource_root()


# ---------------------------------------------------------------------
# Packaged-style resolution (simulated frozen state)
# ---------------------------------------------------------------------


@pytest.fixture
def simulated_frozen(monkeypatch, tmp_path):
    """Simulates a PyInstaller onedir layout:

        <dist_folder>/
            Calgary Vipers Auction 2026.exe   <- sys.executable
            _internal/                        <- sys._MEIPASS (bundle dir)
    """
    dist_folder = tmp_path / "Calgary Vipers Auction 2026 RC0"
    internal_dir = dist_folder / "_internal"
    internal_dir.mkdir(parents=True)
    exe_path = dist_folder / "Calgary Vipers Auction 2026.exe"
    exe_path.write_bytes(b"")  # placeholder; only the path matters here

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(internal_dir), raising=False)
    monkeypatch.setattr(sys, "executable", str(exe_path))
    return dist_folder, internal_dir, exe_path


def test_packaged_style_is_frozen_true(simulated_frozen) -> None:
    assert runtime_paths.is_frozen() is True


def test_packaged_style_resource_root_is_the_bundle_directory(simulated_frozen) -> None:
    _dist_folder, internal_dir, _exe_path = simulated_frozen
    assert runtime_paths._resource_root() == internal_dir


def test_packaged_style_writable_root_is_the_exe_folder_not_the_bundle(simulated_frozen) -> None:
    dist_folder, internal_dir, _exe_path = simulated_frozen
    writable = runtime_paths._writable_root()
    assert writable == dist_folder
    assert writable != internal_dir


def test_packaged_style_resource_and_writable_roots_differ(simulated_frozen) -> None:
    """The entire point of the split: in a packaged build these must NOT
    be the same directory, unlike source mode."""
    assert runtime_paths._resource_root() != runtime_paths._writable_root()


# ---------------------------------------------------------------------
# Concrete resource paths used throughout the app
# ---------------------------------------------------------------------


def test_branding_logo_path_resolves_under_resource_root() -> None:
    from services.config_service import load_branding_config

    branding = load_branding_config()
    assert branding.logo_full_path.is_file()
    assert branding.logo_full_path.is_relative_to(runtime_paths.RESOURCE_ROOT)


def test_player_photo_paths_resolve_under_resource_root() -> None:
    from services.config_service import ROOT_DIR
    from services.player_service import load_players

    players = load_players()
    photo_players = [p for p in players if p.photo_path]
    assert photo_players, "expected at least one canonical player with a photo_path"
    for player in photo_players:
        full_path = ROOT_DIR / player.photo_path
        assert full_path.is_file()
        assert full_path.is_relative_to(runtime_paths.RESOURCE_ROOT)


def test_phone_html_path_resolves_under_resource_root() -> None:
    from services.captain_bidding_server import _PHONE_PAGE_PATH

    assert _PHONE_PAGE_PATH.is_file()
    assert _PHONE_PAGE_PATH.is_relative_to(runtime_paths.RESOURCE_ROOT)


def test_players_json_path_resolves_under_resource_root() -> None:
    from services.player_service import DEFAULT_PLAYERS_PATH

    assert DEFAULT_PLAYERS_PATH.is_file()
    assert DEFAULT_PLAYERS_PATH.is_relative_to(runtime_paths.RESOURCE_ROOT)


def test_teams_json_and_settings_json_resolve_under_resource_root() -> None:
    from services.config_service import DEFAULT_SETTINGS_PATH
    from services.player_service import DEFAULT_TEAMS_PATH

    assert DEFAULT_TEAMS_PATH.is_file()
    assert DEFAULT_TEAMS_PATH.is_relative_to(runtime_paths.RESOURCE_ROOT)
    assert DEFAULT_SETTINGS_PATH.is_file()
    assert DEFAULT_SETTINGS_PATH.is_relative_to(runtime_paths.RESOURCE_ROOT)


# ---------------------------------------------------------------------
# Writable config/ and saves/ directories
# ---------------------------------------------------------------------


def test_preferences_path_resolves_under_writable_root() -> None:
    """Compared against a fresh `_writable_root()` computation, not the
    `_isolate_app_log`-test-isolated `runtime_paths.WRITABLE_ROOT`
    attribute — see the other writable-path tests' docstrings above."""
    from services.preferences_service import PREFERENCES_PATH

    assert PREFERENCES_PATH.is_relative_to(runtime_paths._writable_root())
    assert PREFERENCES_PATH.parent.name == "config"


def test_captain_pin_config_default_resolves_under_writable_root() -> None:
    """tests/conftest.py's autouse `_isolate_captain_pin_storage` (and,
    separately, `_isolate_app_log`, which redirects the *source*
    `runtime_paths.WRITABLE_ROOT` attribute itself) redirect real
    writable-state locations away from the repository root for every
    test — protecting any real saved PINs/logs — so this re-derives the
    module's actual default formula from its own imported `WRITABLE_ROOT`
    reference and compares it against a *fresh* computation
    (`_writable_root()`, unaffected by the attribute-level patch) rather
    than the deliberately-overridden `runtime_paths.WRITABLE_ROOT`
    attribute itself."""
    from services import captain_auth_service

    default_path = captain_auth_service.WRITABLE_ROOT / "config" / "captain_bidding.json"
    assert default_path.is_relative_to(runtime_paths._writable_root())
    assert default_path.parent.name == "config"


def test_saves_dir_resolves_under_writable_root() -> None:
    """See the docstring above — compared against a fresh
    `_writable_root()` computation, not the test-isolated attribute."""
    from services.persistence_service import SAVES_DIR

    assert SAVES_DIR.is_relative_to(runtime_paths._writable_root())
    assert SAVES_DIR.name == "saves"


def test_ensure_writable_dir_creates_missing_directories(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(runtime_paths, "WRITABLE_ROOT", tmp_path)
    created = runtime_paths.ensure_writable_dir("config", "nested")
    assert created.is_dir()
    assert created == tmp_path / "config" / "nested"


def test_writable_path_does_not_require_directory_to_exist(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(runtime_paths, "WRITABLE_ROOT", tmp_path)
    path = runtime_paths.writable_path("does", "not", "exist.json")
    assert path == tmp_path / "does" / "not" / "exist.json"
    assert not path.exists()


def test_resource_path_joins_under_resource_root() -> None:
    path = runtime_paths.resource_path("data", "players.json")
    assert path == runtime_paths.RESOURCE_ROOT / "data" / "players.json"
    assert path.is_file()
