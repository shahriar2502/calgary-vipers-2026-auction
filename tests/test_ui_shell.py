"""Lightweight, non-visual checks for the Milestone 4 UI shell.

Deliberately avoids screenshot/pixel-level assertions. GUI window creation
is best-effort: it is skipped rather than failed when no display/Tk backend
is available in the current environment.
"""

import pytest

from services.config_service import load_branding_config, load_position_colors


def test_branding_config_matches_settings_json() -> None:
    branding = load_branding_config()
    assert branding.app_name == "Football Auction Manager"
    assert branding.branding_name == "Calgary Vipers"
    assert branding.header_title == "Calgary Vipers Auction 2026"
    assert branding.logo_path == "assets/branding/calgary_vipers_logo.png"
    assert branding.theme_name == "vipers_dark"
    assert branding.logo_full_path.is_file()


def test_position_colors_are_loaded_from_settings_not_hardcoded() -> None:
    colors = load_position_colors()
    assert colors == {"GK": "purple", "DEF": "blue", "MID": "green", "ATT": "orange"}


def test_ui_modules_import_successfully() -> None:
    import ui  # noqa: F401
    import ui.main_window  # noqa: F401
    import ui.screens  # noqa: F401
    import ui.screens.players_setup_screen  # noqa: F401
    import ui.theme  # noqa: F401


def test_logo_loader_returns_none_for_a_missing_file(tmp_path) -> None:
    from ui.main_window import _load_logo_image

    assert _load_logo_image(tmp_path / "does_not_exist.png") is None


def test_logo_loader_returns_none_for_a_corrupt_file(tmp_path) -> None:
    from ui.main_window import _load_logo_image

    bad_file = tmp_path / "not_really_a_png.png"
    bad_file.write_bytes(b"not a real image")
    assert _load_logo_image(bad_file) is None


def test_main_window_launches_with_default_screen_if_display_available() -> None:
    tkinter = pytest.importorskip("tkinter")
    try:
        from ui.main_window import DEFAULT_SCREEN, NAV_ITEMS, MainWindow

        window = MainWindow()
    except tkinter.TclError as exc:
        pytest.skip(f"No display/Tk backend available for GUI test: {exc}")

    try:
        window.withdraw()
        assert window.title() == "Calgary Vipers Auction 2026"
        assert window._active_screen_name == DEFAULT_SCREEN == "Live Auction"
        assert list(window._nav_buttons) == [name for name, _ in NAV_ITEMS]

        window.show_screen("Teams")
        assert window._active_screen_name == "Teams"

        with pytest.raises(ValueError, match="Unknown navigation screen"):
            window.show_screen("Nonexistent Screen")
    finally:
        window.destroy()
