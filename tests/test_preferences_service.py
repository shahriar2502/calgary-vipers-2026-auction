"""Tests for services/preferences_service.py: organizer app preferences,
kept deliberately separate from auction session persistence.
"""

import json

from services import preferences_service as ps


def test_defaults_are_safe() -> None:
    defaults = ps.UserPreferences()
    assert defaults.remember_fullscreen is False
    assert defaults.fullscreen_enabled is False
    assert defaults.confirm_sold is True
    assert defaults.confirm_unsold is True


def test_load_preferences_missing_file_falls_back_to_defaults(tmp_path) -> None:
    missing_path = tmp_path / "does_not_exist.json"
    assert ps.load_preferences(missing_path) == ps.UserPreferences()


def test_load_preferences_corrupt_json_falls_back_to_defaults(tmp_path) -> None:
    bad_file = tmp_path / "prefs.json"
    bad_file.write_text("{not valid json", encoding="utf-8")
    assert ps.load_preferences(bad_file) == ps.UserPreferences()


def test_load_preferences_non_object_json_falls_back_to_defaults(tmp_path) -> None:
    bad_file = tmp_path / "prefs.json"
    bad_file.write_text("[1, 2, 3]", encoding="utf-8")
    assert ps.load_preferences(bad_file) == ps.UserPreferences()


def test_load_preferences_partially_invalid_file_falls_back_field_by_field(tmp_path) -> None:
    partial_file = tmp_path / "prefs.json"
    partial_file.write_text(json.dumps({"confirm_sold": False, "unexpected_key": "junk"}), encoding="utf-8")
    loaded = ps.load_preferences(partial_file)
    assert loaded.confirm_sold is False
    assert loaded.confirm_unsold is True  # missing key falls back to its own default
    assert loaded.remember_fullscreen is False
    assert loaded.fullscreen_enabled is False


def test_save_then_load_round_trips(tmp_path) -> None:
    path = tmp_path / "prefs.json"
    original = ps.UserPreferences(
        remember_fullscreen=True, fullscreen_enabled=True, confirm_sold=False, confirm_unsold=False
    )
    ps.save_preferences(original, path)
    assert ps.load_preferences(path) == original


def test_save_preferences_writes_readable_json(tmp_path) -> None:
    path = tmp_path / "prefs.json"
    ps.save_preferences(ps.UserPreferences(confirm_sold=False), path)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["confirm_sold"] is False
    assert data["confirm_unsold"] is True


def test_reset_preferences_restores_and_persists_defaults(tmp_path) -> None:
    path = tmp_path / "prefs.json"
    ps.save_preferences(
        ps.UserPreferences(remember_fullscreen=True, fullscreen_enabled=True, confirm_sold=False, confirm_unsold=False),
        path,
    )
    result = ps.reset_preferences(path)
    assert result == ps.UserPreferences()
    assert ps.load_preferences(path) == ps.UserPreferences()


def test_reset_preferences_does_not_touch_auction_saves(tmp_path, monkeypatch) -> None:
    """Reset only ever writes the preferences file — never an auction save,
    canonical player/team data, or anything else on disk."""
    from services import persistence_service as auction_ps
    from services.auction_session_service import AuctionSession

    saves_dir = tmp_path / "saves"
    mocks_dir = saves_dir / "mocks"
    monkeypatch.setattr(auction_ps, "SAVES_DIR", saves_dir)
    monkeypatch.setattr(auction_ps, "MOCKS_DIR", mocks_dir)
    monkeypatch.setattr(auction_ps, "LIVE_DIR", saves_dir / "live")
    monkeypatch.setattr(auction_ps, "LIVE_ACTIVE_PATH", saves_dir / "live" / "live_active.json")

    session = AuctionSession()
    session.autosave = auction_ps.autosave_hook
    session.start(seed=1)
    save_path = auction_ps.session_save_path(session.mode, session.session_id)
    before = save_path.read_text(encoding="utf-8")

    prefs_path = tmp_path / "prefs.json"
    ps.reset_preferences(prefs_path)

    assert save_path.read_text(encoding="utf-8") == before


def test_preferences_default_path_is_under_config_directory() -> None:
    assert ps.PREFERENCES_PATH.parent.name == "config"
    assert ps.PREFERENCES_PATH.name == "user_preferences.json"
