"""Settings screen: organizer configuration/diagnostics dashboard.

GUI creation is best-effort via the shared `hidden_root` fixture (see
tests/conftest.py) — skipped, not failed, without a real display/Tk
backend. Every test isolates `services.preferences_service.PREFERENCES_PATH`
to a temp file via `monkeypatch` so nothing here ever touches the real
config/user_preferences.json.
"""

from __future__ import annotations

import customtkinter as ctk
import pytest

from services import preferences_service as ps
from services.auction_session_service import AuctionSession, SessionMode
from services.persistence_service import SCHEMA_VERSION


def _find_widget_containing_text(widget, substring: str):
    for child in widget.winfo_children():
        try:
            text = child.cget("text")
            if isinstance(text, str) and substring in text:
                return child
        except Exception:
            pass
        found = _find_widget_containing_text(child, substring)
        if found is not None:
            return found
    return None


def _collect_widget_types(widget, types: set) -> None:
    for child in widget.winfo_children():
        types.add(type(child).__name__)
        _collect_widget_types(child, types)


@pytest.fixture(autouse=True)
def isolated_preferences(tmp_path, monkeypatch):
    monkeypatch.setattr(ps, "PREFERENCES_PATH", tmp_path / "user_preferences.json")
    return tmp_path


def build_screen(hidden_root, session_obj):
    from ui.screens.settings_screen import SettingsScreen

    return SettingsScreen(hidden_root, session_obj)


@pytest.fixture
def started_mock_session() -> AuctionSession:
    session = AuctionSession()
    session.start(seed=1, mode=SessionMode.MOCK, name="Practice Session")
    return session


# ============================================================
# 1-2. REGISTRATION / LOADING
# ============================================================


def test_settings_screen_is_registered() -> None:
    from ui.screens import SCREEN_BUILDERS

    assert "Settings" in SCREEN_BUILDERS


def test_settings_screen_loads_without_a_session(hidden_root) -> None:
    screen = build_screen(hidden_root, None)
    try:
        assert _find_widget_containing_text(screen, "SETTINGS") is not None
    finally:
        screen.destroy()


def test_settings_screen_loads_with_a_session(hidden_root, started_mock_session) -> None:
    screen = build_screen(hidden_root, started_mock_session)
    try:
        assert screen._players
        assert screen._teams
    finally:
        screen.destroy()


# ============================================================
# 3-5. TOURNAMENT INFO / TOURNAMENT RULES (READ-ONLY)
# ============================================================


def test_tournament_info_correct(hidden_root) -> None:
    screen = build_screen(hidden_root, None)
    try:
        assert _find_widget_containing_text(screen, "Calgary Vipers Auction 2026") is not None
        assert _find_widget_containing_text(screen, "Calgary Vipers") is not None
        assert _find_widget_containing_text(screen, "32") is not None
        assert _find_widget_containing_text(screen, "28") is not None
    finally:
        screen.destroy()


def test_core_rules_displayed_correctly(hidden_root) -> None:
    screen = build_screen(hidden_root, None)
    try:
        assert _find_widget_containing_text(screen, "LOCKED TOURNAMENT RULES") is not None
        assert _find_widget_containing_text(screen, "Exactly one GK per team") is not None
        assert _find_widget_containing_text(screen, "1M") is not None
        assert _find_widget_containing_text(screen, "100M") is not None
        assert _find_widget_containing_text(screen, "Re-auctioned") is not None
    finally:
        screen.destroy()


def test_core_rules_are_not_editable(hidden_root) -> None:
    """The Tournament Rules section must contain only labels/frames — no
    entry, switch, or enabled button that could change a locked rule."""
    from ui.screens.settings_screen import SettingsScreen

    screen = SettingsScreen(hidden_root, None)
    try:
        rules_section = None
        for child in screen._body.winfo_children():
            if _find_widget_containing_text(child, "LOCKED TOURNAMENT RULES") is not None:
                rules_section = child
                break
        assert rules_section is not None

        types_found: set = set()
        _collect_widget_types(rules_section, types_found)
        assert "CTkEntry" not in types_found
        assert "CTkSwitch" not in types_found
        assert "CTkButton" not in types_found
    finally:
        screen.destroy()


# ============================================================
# 6-9. FULLSCREEN
# ============================================================


def test_fullscreen_preference_defaults_safely(hidden_root) -> None:
    screen = build_screen(hidden_root, None)
    try:
        assert screen._preferences.fullscreen_enabled is False
        assert screen._preferences.remember_fullscreen is False
    finally:
        screen.destroy()


def test_fullscreen_toggle_bindings_and_escape_exit_on_a_real_main_window(isolated_preferences) -> None:
    """Combines what were three separate MainWindow-creating tests into
    one: PROJECT_CONTEXT.md documents that repeatedly creating/destroying
    separate CTk() roots in one process is a known source of flaky
    cross-interpreter image-cache errors ("image ... doesn't exist") when
    several such tests run back-to-back — fewer windows created means less
    surface for that pre-existing flakiness, not a coverage reduction.

    The <Escape> binding is `lambda _e: self.set_fullscreen(False)` —
    verified by calling `set_fullscreen(False)` directly (the redesign
    ticket found synthetic Tk key events unreliable against a withdrawn
    root) alongside confirming the binding itself is actually registered.
    """
    tkinter = pytest.importorskip("tkinter")
    try:
        from ui.main_window import MainWindow

        window = MainWindow()
    except tkinter.TclError as exc:
        pytest.skip(f"No display/Tk backend available for GUI test: {exc}")

    try:
        window.withdraw()

        assert window.bind("<F11>") != ""
        assert window.bind("<Escape>") != ""

        assert window._is_fullscreen is False
        window.toggle_fullscreen()
        assert window._is_fullscreen is True
        window.set_fullscreen(False)  # exactly what the Escape binding calls
        assert window._is_fullscreen is False
    finally:
        window.destroy()


def test_settings_fullscreen_switch_is_a_noop_under_a_plain_root(hidden_root) -> None:
    """Under a bare Tk/CTk root with no `set_fullscreen` method (e.g. this
    shared test fixture), toggling must never raise."""
    screen = build_screen(hidden_root, None)
    try:
        screen._set_fullscreen_preference(True)  # must not raise
    finally:
        screen.destroy()


def test_remember_fullscreen_persists_current_state(hidden_root, isolated_preferences) -> None:
    screen = build_screen(hidden_root, None)
    try:
        screen._set_remember_fullscreen_preference(True)
        assert screen._preferences.remember_fullscreen is True
        reloaded = ps.load_preferences()
        assert reloaded.remember_fullscreen is True
    finally:
        screen.destroy()


# ============================================================
# 10-17. AUCTION PREFERENCES / STORAGE / RESET
# ============================================================


def test_sold_confirmation_defaults_on(hidden_root) -> None:
    screen = build_screen(hidden_root, None)
    try:
        assert screen._preferences.confirm_sold is True
        assert screen._confirm_sold_switch.get() == 1
    finally:
        screen.destroy()


def test_unsold_confirmation_defaults_on(hidden_root) -> None:
    screen = build_screen(hidden_root, None)
    try:
        assert screen._preferences.confirm_unsold is True
        assert screen._confirm_unsold_switch.get() == 1
    finally:
        screen.destroy()


def test_preference_file_saves_on_toggle(hidden_root, isolated_preferences) -> None:
    screen = build_screen(hidden_root, None)
    try:
        screen._set_confirm_sold_preference(False)
        assert (isolated_preferences / "user_preferences.json").is_file()
        assert ps.load_preferences().confirm_sold is False
    finally:
        screen.destroy()


def test_preference_file_loads_on_next_screen_build(hidden_root, isolated_preferences) -> None:
    ps.save_preferences(ps.UserPreferences(confirm_unsold=False))
    screen = build_screen(hidden_root, None)
    try:
        assert screen._preferences.confirm_unsold is False
        assert screen._confirm_unsold_switch.get() == 0
    finally:
        screen.destroy()


def test_corrupt_preference_file_falls_back_safely(hidden_root, isolated_preferences) -> None:
    (isolated_preferences / "user_preferences.json").write_text("{not valid json", encoding="utf-8")
    screen = build_screen(hidden_root, None)
    try:
        assert screen._preferences == ps.UserPreferences()
    finally:
        screen.destroy()


def test_missing_preference_file_falls_back_safely(hidden_root, isolated_preferences) -> None:
    assert not (isolated_preferences / "user_preferences.json").exists()
    screen = build_screen(hidden_root, None)
    try:
        assert screen._preferences == ps.UserPreferences()
    finally:
        screen.destroy()


def test_reset_preferences_restores_defaults(hidden_root, isolated_preferences) -> None:
    ps.save_preferences(ps.UserPreferences(confirm_sold=False, confirm_unsold=False, remember_fullscreen=True))
    screen = build_screen(hidden_root, None)
    try:
        screen._execute_reset()
        assert screen._preferences == ps.UserPreferences()
        assert ps.load_preferences() == ps.UserPreferences()
    finally:
        screen.destroy()


def test_reset_preferences_does_not_touch_auction_saves(hidden_root, isolated_preferences, tmp_path, monkeypatch) -> None:
    from services import persistence_service as auction_ps

    saves_dir = tmp_path / "saves"
    monkeypatch.setattr(auction_ps, "SAVES_DIR", saves_dir)
    monkeypatch.setattr(auction_ps, "MOCKS_DIR", saves_dir / "mocks")
    monkeypatch.setattr(auction_ps, "LIVE_DIR", saves_dir / "live")
    monkeypatch.setattr(auction_ps, "LIVE_ACTIVE_PATH", saves_dir / "live" / "live_active.json")

    session = AuctionSession()
    session.autosave = auction_ps.autosave_hook
    session.start(seed=1)
    save_path = auction_ps.session_save_path(session.mode, session.session_id)
    before = save_path.read_text(encoding="utf-8")

    screen = build_screen(hidden_root, session)
    try:
        screen._execute_reset()
    finally:
        screen.destroy()

    assert save_path.read_text(encoding="utf-8") == before


# ============================================================
# 18-23. SESSION / SAVE STATUS
# ============================================================


def test_no_active_session_state_displays(hidden_root) -> None:
    screen = build_screen(hidden_root, None)
    try:
        assert _find_widget_containing_text(screen, "NO ACTIVE SESSION") is not None
    finally:
        screen.destroy()


def test_current_mock_session_info_displays(hidden_root, started_mock_session) -> None:
    screen = build_screen(hidden_root, started_mock_session)
    try:
        assert _find_widget_containing_text(screen, "MOCK") is not None
        assert _find_widget_containing_text(screen, started_mock_session.session_id) is not None
    finally:
        screen.destroy()


def test_current_live_session_info_displays(hidden_root) -> None:
    session = AuctionSession()
    session.start(seed=1, mode=SessionMode.LIVE)
    screen = build_screen(hidden_root, session)
    try:
        assert _find_widget_containing_text(screen, "LIVE") is not None
    finally:
        screen.destroy()


def test_current_auction_status_displays(hidden_root, started_mock_session) -> None:
    screen = build_screen(hidden_root, started_mock_session)
    try:
        assert _find_widget_containing_text(screen, "IN_PROGRESS") is not None
    finally:
        screen.destroy()


def test_current_round_displays(hidden_root, started_mock_session) -> None:
    screen = build_screen(hidden_root, started_mock_session)
    try:
        assert _find_widget_containing_text(screen, str(started_mock_session.round_number)) is not None
    finally:
        screen.destroy()


def test_sold_count_displays(hidden_root, started_mock_session) -> None:
    screen = build_screen(hidden_root, started_mock_session)
    try:
        assert _find_widget_containing_text(screen, "0 / 28") is not None
    finally:
        screen.destroy()


# ============================================================
# 24-28. DIAGNOSTICS
# ============================================================


def test_save_schema_version_displays(hidden_root) -> None:
    screen = build_screen(hidden_root, None)
    try:
        assert _find_widget_containing_text(screen, str(SCHEMA_VERSION)) is not None
    finally:
        screen.destroy()


def test_player_count_is_32(hidden_root) -> None:
    screen = build_screen(hidden_root, None)
    try:
        assert len(screen._players) == 32
        assert _find_widget_containing_text(screen, "32 / 32") is not None
    finally:
        screen.destroy()


def test_team_count_is_4(hidden_root) -> None:
    screen = build_screen(hidden_root, None)
    try:
        assert len(screen._teams) == 4
        assert _find_widget_containing_text(screen, "4 / 4") is not None
    finally:
        screen.destroy()


def test_photo_count_correct(hidden_root) -> None:
    """Every canonical player has a real photo asset (September 2026
    photo integration), so diagnostics should show all 32 found."""
    screen = build_screen(hidden_root, None)
    try:
        assert _find_widget_containing_text(screen, "32 / 32") is not None
        assert _find_widget_containing_text(screen, "Missing photo") is None
    finally:
        screen.destroy()


def test_setup_validation_pass_with_canonical_data(hidden_root) -> None:
    screen = build_screen(hidden_root, None)
    try:
        assert screen._validation.is_ready is True
        assert _find_widget_containing_text(screen, "TOURNAMENT SETUP: READY") is not None
    finally:
        screen.destroy()


# ============================================================
# 29-30. CAPTAIN BIDDING PLACEHOLDER
# ============================================================


def test_captain_bidding_section_displays_off_state_under_bare_root(hidden_root) -> None:
    """Under this shared test fixture (a bare CTk root, not a real
    MainWindow), there is no real `captain_bidding_server` to control —
    the section must degrade to a clean OFF state rather than raising."""
    screen = build_screen(hidden_root, None)
    try:
        assert _find_widget_containing_text(screen, "CAPTAIN PHONE BIDDING") is not None
        assert _find_widget_containing_text(screen, "OFF") is not None
        assert _find_widget_containing_text(screen, "TEST MODE") is not None
        assert _find_widget_containing_text(screen, "LAN Address") is not None
    finally:
        screen.destroy()


def _find_button_by_text(widget, text: str):
    for child in widget.winfo_children():
        if isinstance(child, ctk.CTkButton):
            try:
                if child.cget("text") == text:
                    return child
            except Exception:
                pass
        found = _find_button_by_text(child, text)
        if found is not None:
            return found
    return None


def test_phone_bidding_start_button_disabled_without_a_real_server(hidden_root) -> None:
    screen = build_screen(hidden_root, None)
    try:
        found_button = _find_button_by_text(screen, "START CAPTAIN BIDDING")
        assert found_button is not None
        assert found_button.cget("state") == "disabled"
    finally:
        screen.destroy()


# ============================================================
# 31. FULLSCREEN DOES NOT AFFECT BUSINESS LOGIC
# ============================================================


def test_fullscreen_and_confirmation_toggles_do_not_mutate_session(hidden_root, started_mock_session) -> None:
    import copy

    history_snapshot = copy.deepcopy(started_mock_session.auction.history)
    teams_snapshot = copy.deepcopy(started_mock_session.teams)
    players_snapshot = copy.deepcopy(started_mock_session.players)

    screen = build_screen(hidden_root, started_mock_session)
    try:
        screen._set_fullscreen_preference(True)
        screen._set_remember_fullscreen_preference(True)
        screen._set_confirm_sold_preference(False)
        screen._set_confirm_unsold_preference(False)
        screen._execute_reset()
    finally:
        screen.destroy()

    assert started_mock_session.auction.history == history_snapshot
    assert started_mock_session.teams == teams_snapshot
    assert started_mock_session.players == players_snapshot


# ============================================================
# CAPTAIN PHONE BIDDING — SETTINGS START/STOP (real MainWindow)
# ============================================================


def test_settings_start_and_stop_captain_bidding(isolated_preferences) -> None:
    """START/STOP through the actual Settings button handlers, against a
    real MainWindow.captain_bidding_server — one more full-MainWindow
    test, so kept as a single test (not split further) per this file's
    existing note about the pre-existing multi-root Tk flakiness."""
    tkinter = pytest.importorskip("tkinter")
    try:
        from ui.main_window import MainWindow

        window = MainWindow()
    except tkinter.TclError as exc:
        pytest.skip(f"No display/Tk backend available for GUI test: {exc}")

    try:
        window.withdraw()
        window.captain_bidding_server.port = 18780  # avoid clashing with a real dev-port server
        window.session.autosave = None
        window.session.start(seed=1)
        window.show_screen("Settings")
        screen = window._screen_frame

        assert window.captain_bidding_server.is_running is False
        screen._on_start_captain_bidding_clicked()
        assert window.captain_bidding_server.is_running is True
        assert _find_widget_containing_text(screen, "RUNNING") is not None
        assert _find_widget_containing_text(screen, "http://") is not None

        screen._on_stop_captain_bidding_clicked()
        assert window.captain_bidding_server.is_running is False
        assert _find_widget_containing_text(screen, "OFF") is not None
    finally:
        window.captain_bidding_server.stop()
        window.destroy()
