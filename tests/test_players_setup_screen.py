"""Lightweight, non-visual checks for the Milestone 5 Players & Setup screen.

GUI creation is best-effort: skipped (not failed) when no display/Tk
backend is available, matching tests/test_ui_shell.py's approach. The
`hidden_root` fixture is shared (see tests/conftest.py) across all
frame-level screen tests to avoid flaky TclError teardown timing from
repeatedly creating/destroying CTk() roots in the same process.
"""

import pytest


@pytest.fixture
def screen(hidden_root):
    from ui.screens.players_setup_screen import PlayersSetupScreen

    built = PlayersSetupScreen(hidden_root)
    yield built
    built.destroy()


def test_players_setup_screen_loads_all_32_players_by_default(screen) -> None:
    assert len(screen._players) == 32
    assert len(screen._row_container.winfo_children()) == 32


def test_players_setup_screen_reports_setup_ready(screen) -> None:
    assert screen._validation.is_ready is True


def test_players_setup_screen_search_filters_visible_rows(screen) -> None:
    screen._search_var.set("arik")
    assert len(screen._row_container.winfo_children()) == 1


def test_players_setup_screen_position_filter_updates_visible_rows(screen) -> None:
    screen._position_var.set("GK")
    screen._refresh_table()
    assert len(screen._row_container.winfo_children()) == 4


def test_players_setup_screen_clearing_filters_restores_all_rows(screen) -> None:
    screen._search_var.set("arik")
    assert len(screen._row_container.winfo_children()) == 1

    screen._search_var.set("")
    screen._position_var.set("All")
    screen._status_var.set("All Players")
    screen._refresh_table()
    assert len(screen._row_container.winfo_children()) == 32


def test_players_setup_screen_status_filter_shows_only_captains(screen) -> None:
    screen._status_var.set("Captains")
    screen._refresh_table()
    assert len(screen._row_container.winfo_children()) == 4


def test_players_setup_screen_shows_a_thumbnail_for_every_row(screen) -> None:
    # Every canonical player now has a real photo asset (September 2026
    # photo integration); the table should load a thumbnail image per row
    # rather than falling back to the initials placeholder.
    assert len(screen._row_photo_images) == 32


def test_players_setup_screen_refresh_replaces_stale_photo_references(screen) -> None:
    screen._search_var.set("arik")
    assert len(screen._row_photo_images) == 1

    screen._search_var.set("")
    screen._refresh_table()
    assert len(screen._row_photo_images) == 32


def test_players_setup_screen_falls_back_to_initials_when_photo_file_is_missing(hidden_root) -> None:
    from ui.screens.players_setup_screen import PlayersSetupScreen

    built = PlayersSetupScreen(hidden_root)
    try:
        arik = next(p for p in built._players if p.full_name == "Shahriar Anwar Khan")
        arik.photo_path = "assets/players/does_not_exist.jpg"
        built._refresh_table()
        # No crash, and the row still renders (missing photo -> placeholder).
        assert len(built._row_container.winfo_children()) == 32
    finally:
        built.destroy()


def test_players_setup_screen_handles_missing_data_file_without_crashing(hidden_root, monkeypatch) -> None:
    def _raise_missing_file(*_args, **_kwargs):
        raise FileNotFoundError("data/players.json not found")

    monkeypatch.setattr("ui.screens.players_setup_screen.load_players", _raise_missing_file)

    from ui.screens.players_setup_screen import PlayersSetupScreen

    broken_screen = PlayersSetupScreen(hidden_root)
    try:
        assert broken_screen._row_container is None
    finally:
        broken_screen.destroy()
