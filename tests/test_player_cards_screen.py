"""Player Cards screen (September 2026): visual gallery, filters, search,
sort, and the read-only click-to-expand detail view.

GUI creation is best-effort via the shared `hidden_root` fixture (see
tests/conftest.py) — skipped, not failed, without a real display/Tk
backend.
"""
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


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


@pytest.fixture
def screen(hidden_root):
    from ui.screens.player_cards_screen import PlayerCardsScreen

    built = PlayerCardsScreen(hidden_root)
    yield built
    built.destroy()


def _card_count(screen) -> int:
    return len(screen._grid_container.winfo_children())


# ============================================================
# 1. LOADING
# ============================================================


def test_all_32_players_load_by_default(screen) -> None:
    assert len(screen._players) == 32
    assert _card_count(screen) == 32


def test_count_label_reports_32_of_32(screen) -> None:
    assert screen._count_label.cget("text") == "Showing 32 of 32 players"


# ============================================================
# PREMIUM CARD ART / HOVER (Calgary Vipers redesign)
# ============================================================


def test_every_card_art_image_is_kept_alive(screen) -> None:
    # One composited background art image per rendered card, regardless
    # of whether that player has a real photo — losing this reference
    # would blank the whole card, not just the photo.
    assert len(screen._card_art_images) == 32


def test_card_variant_is_deterministic_per_player(screen) -> None:
    from ui.screens.player_cards_screen import _card_variant

    player = next(p for p in screen._players if p.full_name == "Rizvi Ibrahim")
    variant_a = _card_variant(player)
    variant_b = _card_variant(player)
    assert variant_a == variant_b
    assert variant_a in (0, 1)


def test_card_hover_brightens_border_and_leave_restores_it(screen) -> None:
    """Exercises the exact same styling function the real `<Enter>`/<Leave>`
    Tk bindings call. Synthetic Tk event delivery through a withdrawn
    test-fixture root is unreliable (confirmed working against a real,
    mapped MainWindow but not here), so this calls the underlying pure
    function directly instead of simulating mouse events."""
    from ui.screens.player_cards_screen import _VARIANT_COLORS, _card_variant, _set_card_hover
    from ui.theme import GOLD_ACCENT

    card = screen._grid_container.winfo_children()[0]
    player = screen._visible_players()[0]
    default_border = _VARIANT_COLORS[_card_variant(player)][0]
    default_width = card.cget("border_width")

    _set_card_hover(card, True, default_border)
    assert card.cget("border_color") == GOLD_ACCENT
    assert card.cget("border_width") > default_width

    _set_card_hover(card, False, default_border)
    assert card.cget("border_color") == default_border
    assert card.cget("border_width") == default_width


def test_card_click_still_opens_detail_view(screen) -> None:
    """The premium hover styling must not interfere with the existing
    click-to-open behavior. The real `<Button-1>` Tk binding just calls
    the card's exposed `_on_click` handler — invoked directly here since
    synthetic Tk click events are unreliable against a withdrawn
    test-fixture root."""
    screen._sort_var.set("Name — A to Z")
    screen._refresh_grid()
    first_player = screen._visible_players()[0]
    card = screen._grid_container.winfo_children()[0]

    card._on_click()

    assert screen._detail_player is first_player


# ============================================================
# 11-14. POSITION FILTERS
# ============================================================


def test_filter_gk_shows_exactly_the_4_goalkeepers(screen) -> None:
    screen._filter_var.set("GK")
    screen._refresh_grid()
    assert _card_count(screen) == 4


def test_filter_def_shows_only_defenders(screen) -> None:
    from models.player import Position

    expected = sum(1 for p in screen._players if p.position == Position.DEF)
    screen._filter_var.set("DEF")
    screen._refresh_grid()
    assert _card_count(screen) == expected


def test_filter_mid_shows_only_midfielders(screen) -> None:
    from models.player import Position

    expected = sum(1 for p in screen._players if p.position == Position.MID)
    screen._filter_var.set("MID")
    screen._refresh_grid()
    assert _card_count(screen) == expected


def test_filter_att_shows_only_attackers(screen) -> None:
    from models.player import Position

    expected = sum(1 for p in screen._players if p.position == Position.ATT)
    screen._filter_var.set("ATT")
    screen._refresh_grid()
    assert _card_count(screen) == expected


# ============================================================
# 15. CAPTAIN FILTER
# ============================================================


def test_captain_filter_returns_exactly_4(screen) -> None:
    screen._filter_var.set("CAPTAINS")
    screen._refresh_grid()
    assert _card_count(screen) == 4


def test_captain_filter_returns_only_captains(screen) -> None:
    screen._filter_var.set("CAPTAINS")
    visible = screen._visible_players()
    assert all(p.is_captain for p in visible)
    assert len(visible) == 4


# ============================================================
# 16-17. SEARCH
# ============================================================


def test_search_by_full_name(screen) -> None:
    screen._search_var.set("Rizvi Ibrahim")
    visible = screen._visible_players()
    assert [p.full_name for p in visible] == ["Rizvi Ibrahim"]


def test_search_by_short_name(screen) -> None:
    screen._search_var.set("arik")
    visible = screen._visible_players()
    assert [p.short_name for p in visible] == ["Arik"]


def test_search_is_case_insensitive(screen) -> None:
    screen._search_var.set("RIZVI")
    visible = screen._visible_players()
    assert [p.full_name for p in visible] == ["Rizvi Ibrahim"]


# ============================================================
# 18-21. SORT
# ============================================================


def test_ovr_high_to_low_sorting(screen) -> None:
    screen._sort_var.set("OVR — High to Low")
    visible = screen._visible_players()
    ratings = [p.overall_rating for p in visible]
    assert ratings == sorted(ratings, reverse=True)
    assert visible[0].full_name == "Rizvi Ibrahim"


def test_ovr_low_to_high_sorting(screen) -> None:
    screen._sort_var.set("OVR — Low to High")
    visible = screen._visible_players()
    ratings = [p.overall_rating for p in visible]
    assert ratings == sorted(ratings)


def test_fpl_high_to_low_sorting(screen) -> None:
    screen._sort_var.set("FPL — High to Low")
    visible = screen._visible_players()
    assert visible[0].full_name == "Rizvi Ibrahim"  # 111, the highest
    real_values = [p.last_season_fpl_points for p in visible if p.last_season_fpl_points is not None]
    assert real_values == sorted(real_values, reverse=True)


def test_fpl_low_to_high_sorting(screen) -> None:
    screen._sort_var.set("FPL — Low to High")
    visible = screen._visible_players()
    real_values = [p.last_season_fpl_points for p in visible if p.last_season_fpl_points is not None]
    assert real_values == sorted(real_values)


def test_none_fpl_values_sort_after_real_values_both_directions(screen) -> None:
    for sort_value in ("FPL — High to Low", "FPL — Low to High"):
        screen._sort_var.set(sort_value)
        visible = screen._visible_players()
        none_flags = [p.last_season_fpl_points is None for p in visible]
        # Once a True (missing) appears, every subsequent entry must also
        # be missing — i.e. all real values come first, as one contiguous
        # block, never interleaved with or before a None.
        first_none_index = none_flags.index(True) if True in none_flags else len(none_flags)
        assert all(flag for flag in none_flags[first_none_index:])
        assert not any(none_flags[:first_none_index])


def test_name_a_to_z_sorting(screen) -> None:
    screen._sort_var.set("Name — A to Z")
    visible = screen._visible_players()
    names = [p.full_name.lower() for p in visible]
    assert names == sorted(names)


# ============================================================
# 22-23. PHOTOS
# ============================================================


def test_card_uses_real_photo_for_every_canonical_player(screen) -> None:
    # All 32 canonical players have a real photo asset (Milestone: Player
    # Photo Integration), so every rendered card should have loaded one.
    assert len(screen._card_photo_images) == 32


def test_missing_photo_falls_back_safely_without_crashing(hidden_root) -> None:
    from ui.screens.player_cards_screen import PlayerCardsScreen

    built = PlayerCardsScreen(hidden_root)
    try:
        built._players[0].photo_path = "assets/players/does_not_exist.jpg"
        built._refresh_grid()
        # No crash, and every OTHER player's photo still loaded correctly.
        assert len(built._card_photo_images) == 31
        assert _card_count(built) == 32
    finally:
        built.destroy()


def test_corrupt_photo_file_falls_back_safely(hidden_root, tmp_path) -> None:
    import os

    from ui.screens.player_cards_screen import PlayerCardsScreen

    built = PlayerCardsScreen(hidden_root)
    try:
        bad_file = tmp_path / "corrupt.jpg"
        bad_file.write_bytes(b"not a real image")
        # photo_path is joined with ROOT_DIR by the screen, so an absolute
        # path expressed relative to ROOT works regardless of tmp_path's
        # actual location.
        built._players[0].photo_path = os.path.relpath(bad_file, ROOT)
        built._refresh_grid()
        assert _card_count(built) == 32  # no crash
    finally:
        built.destroy()


# ============================================================
# 24. DETAIL (EXPANDED) VIEW — an in-page panel, not a dialog
# ============================================================


def test_expanded_player_detail_opens(screen) -> None:
    player = next(p for p in screen._players if p.full_name == "Rizvi Ibrahim")
    screen._open_detail(player)
    assert screen._detail_player is player
    assert screen._grid_container is None  # gallery is swapped out
    assert _find_widget_containing_text(screen, "RIZVI IBRAHIM") is not None
    assert _find_widget_containing_text(screen, "90") is not None
    assert _find_widget_containing_text(screen, "LAST SEASON FPL: 111") is not None


def test_expanded_view_shows_na_for_missing_fpl(screen) -> None:
    player = next(p for p in screen._players if p.full_name == "Munem")
    screen._open_detail(player)
    assert _find_widget_containing_text(screen, "LAST SEASON FPL: N/A") is not None


def test_expanded_view_shows_captain_badge_for_captains(screen) -> None:
    player = next(p for p in screen._players if p.full_name == "Samin Haque")
    screen._open_detail(player)
    assert _find_widget_containing_text(screen, "CAPTAIN") is not None
    assert _find_widget_containing_text(screen, "Blackout FC") is not None


def test_expanded_view_shows_pre_auction_status_when_no_session_active(screen) -> None:
    player = next(p for p in screen._players if p.full_name == "Rizvi Ibrahim")
    screen._open_detail(player)
    assert _find_widget_containing_text(screen, "PRE-AUCTION") is not None


def test_expanded_view_reflects_sold_status_when_session_active(hidden_root) -> None:
    from services.auction_session_service import AuctionSession
    from ui.screens.player_cards_screen import PlayerCardsScreen

    session = AuctionSession()
    session.start(seed=1)
    blackout = next(t for t in session.teams if t.name == "Blackout FC")
    result = session.sell_current_player(winning_team=blackout.id, sale_price=9)

    built = PlayerCardsScreen(hidden_root, session=session)
    try:
        built._open_detail(result.player)
        assert _find_widget_containing_text(built, "SOLD — Blackout FC — 9M") is not None
    finally:
        built.destroy()


def test_back_button_returns_to_the_gallery(screen) -> None:
    player = next(p for p in screen._players if p.full_name == "Rizvi Ibrahim")
    screen._open_detail(player)
    assert screen._grid_container is None

    screen._close_detail()
    assert screen._detail_player is None
    assert screen._grid_container is not None
    assert _card_count(screen) == 32


# ============================================================
# 25 & 29. READ-ONLY
# ============================================================


def test_screen_never_mutates_canonical_players_json(screen) -> None:
    path = ROOT / "data" / "players.json"
    before = path.read_text(encoding="utf-8")

    screen._filter_var.set("CAPTAINS")
    screen._refresh_grid()
    screen._search_var.set("riz")
    screen._filter_var.set("ALL")
    screen._sort_var.set("FPL — High to Low")
    screen._refresh_grid()
    player = next(p for p in screen._players if p.full_name == "Rizvi Ibrahim")
    screen._open_detail(player)
    screen._close_detail()

    assert path.read_text(encoding="utf-8") == before


def test_filters_and_search_do_not_mutate_the_loaded_player_objects(screen) -> None:
    import copy

    snapshot = [copy.deepcopy(p) for p in screen._players]

    screen._filter_var.set("GK")
    screen._refresh_grid()
    screen._search_var.set("a")
    screen._sort_var.set("Name — A to Z")
    screen._refresh_grid()

    assert screen._players == snapshot


def test_opening_expanded_view_does_not_change_player_state(screen) -> None:
    player = next(p for p in screen._players if p.full_name == "Samin Haque")
    import copy

    before = copy.deepcopy(player)
    screen._open_detail(player)
    screen._close_detail()
    assert player == before
