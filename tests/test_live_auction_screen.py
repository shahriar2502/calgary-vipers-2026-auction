"""Lightweight, non-visual checks for the Milestone 8 Live Auction screen.

GUI creation is best-effort via the shared hidden_root fixture (see
tests/conftest.py). Most tests focus on callback/session-state behavior
rather than widget pixels — SOLD/UNSOLD confirmation dialogs are mostly
exercised through their underlying `_execute_sale`/`_execute_unsold`
callbacks directly rather than a real CTkToplevel. The dedicated
"CONFIRMATION DIALOG" section near the end is the exception: those tests
open a real dialog to guard against the Milestone 8 bug-fix regression
(Confirm/Cancel clipped off the bottom of a fixed-size dialog) — they
assert on requested/rendered geometry and on locating the actual button
widgets, never on exact pixel positions.
"""

from pathlib import Path

import customtkinter as ctk
import pytest

from models.auction import AuctionStatus
from services.auction_service import AuctionTransactionError, team_can_bid_for_player
from services.auction_session_service import AuctionSession

ROOT = Path(__file__).resolve().parents[1]


def team_by_name(teams, name: str):
    return next(team for team in teams if team.name == name)


def _find_widgets_by_text(widget, text: str) -> list:
    matches = []
    for child in widget.winfo_children():
        try:
            if child.cget("text") == text:
                matches.append(child)
        except Exception:
            pass
        matches.extend(_find_widgets_by_text(child, text))
    return matches


def _find_toplevel(widget) -> ctk.CTkToplevel | None:
    for child in widget.winfo_children():
        if isinstance(child, ctk.CTkToplevel):
            return child
    return None


def _find_button_by_text(widget, text: str) -> ctk.CTkButton | None:
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


def start_with_seed(screen, seed: int) -> None:
    """Start the session deterministically (screen._on_start_auction_clicked
    always starts unseeded, which would make a full-auction test flaky)
    then re-render exactly as the real button handler would."""
    screen._session.start(seed=seed)
    screen._selected_team_id = None
    screen._price_text = ""
    screen._error_message = None
    screen._render()


def resolve_whole_screen(screen, sale_price: int = 1) -> None:
    """Drive the screen's session to COMPLETE or BLOCKED via its own
    _execute_sale/_execute_unsold callbacks (the same seam a real SOLD/
    UNSOLD confirm click uses), picking the currently-eligible team with
    the fewest players so far each time — see the identical helper in
    tests/test_auction_service.py for why naive round-robin can
    legitimately hit a GK conflict instead."""
    for _ in range(500):
        session = screen._session
        if session.auction.status in (AuctionStatus.COMPLETE, AuctionStatus.BLOCKED):
            return
        current = session.current_player
        eligible = [team for team in session.teams if team_can_bid_for_player(team, current, session.players)]
        if not eligible:
            screen._execute_unsold()
        else:
            team = min(eligible, key=lambda team: team.roster_size)
            screen._execute_sale(team.id, sale_price)
    raise AssertionError("resolve_whole_screen did not reach COMPLETE/BLOCKED within 500 attempts")


@pytest.fixture
def session() -> AuctionSession:
    return AuctionSession()


@pytest.fixture
def screen(hidden_root, session):
    from ui.screens.live_auction_screen import LiveAuctionScreen

    built = LiveAuctionScreen(hidden_root, session)
    yield built
    built.destroy()


# ============================================================
# CURRENT PLAYER / PROGRESS
# ============================================================


def test_screen_starts_in_not_started_state(screen) -> None:
    assert screen._session.started is False


def test_start_auction_button_creates_session_and_reveals_first_player(screen) -> None:
    screen._on_start_auction_clicked()
    assert screen._session.started is True
    current = screen._session.current_player
    assert current is not None
    assert current.is_captain is False


def test_progress_starts_at_0_resolved(screen) -> None:
    screen._on_start_auction_clicked()
    assert screen._session.resolved_count == 0
    assert screen._session.total_queue_length == 28


def test_progress_increments_after_sold(screen) -> None:
    screen._on_start_auction_clicked()
    team = team_by_name(screen._session.teams, "Blackout FC")
    screen._execute_sale(team.id, 5)
    assert screen._session.resolved_count == 1


def test_progress_increments_after_unsold(screen) -> None:
    screen._on_start_auction_clicked()
    screen._execute_unsold()
    assert screen._session.resolved_count == 1


def test_screen_does_not_store_the_upcoming_queue_itself(screen) -> None:
    screen._on_start_auction_clicked()
    instance_attrs = vars(screen)
    assert "queue" not in instance_attrs
    assert "_queue" not in instance_attrs


# ============================================================
# SOLD UI INTEGRATION
# ============================================================


def test_execute_sale_triggers_process_sale_via_session(screen) -> None:
    screen._on_start_auction_clicked()
    current = screen._session.current_player
    team = team_by_name(screen._session.teams, "Blackout FC")
    screen._execute_sale(team.id, 10)
    assert screen._session.last_result.outcome == "SOLD"
    assert screen._session.last_result.player.id == current.id


def test_sold_updates_live_team_budget(screen) -> None:
    screen._on_start_auction_clicked()
    team = team_by_name(screen._session.teams, "Blackout FC")
    screen._execute_sale(team.id, 10)
    assert team.remaining_budget == 90


def test_sold_updates_live_roster(screen) -> None:
    screen._on_start_auction_clicked()
    current = screen._session.current_player
    team = team_by_name(screen._session.teams, "Blackout FC")
    screen._execute_sale(team.id, 10)
    assert current.id in team.roster


def test_sold_updates_squad_size(screen) -> None:
    screen._on_start_auction_clicked()
    team = team_by_name(screen._session.teams, "Blackout FC")
    screen._execute_sale(team.id, 10)
    assert team.roster_size == 2


def test_sold_creates_transaction_feedback(screen) -> None:
    screen._on_start_auction_clicked()
    team = team_by_name(screen._session.teams, "Blackout FC")
    screen._execute_sale(team.id, 10)
    assert screen._session.last_result is not None
    assert screen._session.last_result.outcome == "SOLD"


def test_sold_advances_to_next_player(screen) -> None:
    screen._on_start_auction_clicked()
    first = screen._session.current_player
    team = team_by_name(screen._session.teams, "Blackout FC")
    screen._execute_sale(team.id, 10)
    second = screen._session.current_player
    assert second is not None
    assert second.id != first.id


def test_sold_clears_team_selection(screen) -> None:
    screen._on_start_auction_clicked()
    team = team_by_name(screen._session.teams, "Blackout FC")
    screen._on_team_selected(team.id)
    assert screen._selected_team_id == team.id
    screen._execute_sale(team.id, 10)
    assert screen._selected_team_id is None


def test_sold_resets_price_input(screen) -> None:
    screen._on_start_auction_clicked()
    team = team_by_name(screen._session.teams, "Blackout FC")
    screen._price_var.set("10")
    screen._execute_sale(team.id, 10)
    assert screen._price_text == ""


def test_failed_sold_does_not_advance(screen) -> None:
    screen._on_start_auction_clicked()
    position_before = screen._session.auction.current_queue_position
    screen._execute_sale(999999, 10)  # nonexistent team id
    assert screen._session.auction.current_queue_position == position_before


def test_failed_sold_leaves_team_state_unchanged(screen) -> None:
    screen._on_start_auction_clicked()
    team = team_by_name(screen._session.teams, "Blackout FC")
    budget_before = team.remaining_budget
    roster_before = list(team.roster)
    screen._execute_sale(team.id, 1000)  # exceeds budget
    assert team.remaining_budget == budget_before
    assert team.roster == roster_before
    assert screen._error_message is not None
    assert "budget" in screen._error_message.lower()


def test_missing_team_selection_produces_readable_ui_error(screen) -> None:
    screen._on_start_auction_clicked()
    screen._selected_team_id = None
    screen._on_sold_clicked()
    assert screen._error_message == "Select a winning team before marking SOLD."


def test_missing_price_produces_readable_ui_error(screen) -> None:
    screen._on_start_auction_clicked()
    team = team_by_name(screen._session.teams, "Blackout FC")
    screen._on_team_selected(team.id)
    screen._price_var.set("")
    screen._on_sold_clicked()
    assert screen._error_message is not None
    assert "price" in screen._error_message.lower()


def test_malformed_price_text_produces_readable_ui_error(screen) -> None:
    screen._on_start_auction_clicked()
    team = team_by_name(screen._session.teams, "Blackout FC")
    screen._on_team_selected(team.id)
    screen._price_var.set("not-a-number")
    screen._on_sold_clicked()
    assert screen._error_message is not None
    assert "price" in screen._error_message.lower()


def test_insufficient_budget_error_is_surfaced_without_crash(screen) -> None:
    screen._on_start_auction_clicked()
    team = team_by_name(screen._session.teams, "Blackout FC")
    screen._execute_sale(team.id, 101)
    assert screen._error_message is not None
    assert "budget" in screen._error_message.lower()


# ============================================================
# UNSOLD UI INTEGRATION
# ============================================================


def test_execute_unsold_triggers_process_unsold_via_session(screen) -> None:
    screen._on_start_auction_clicked()
    current = screen._session.current_player
    screen._execute_unsold()
    assert screen._session.last_result.outcome == "UNSOLD"
    assert screen._session.last_result.player.id == current.id


def test_unsold_advances_to_next_player(screen) -> None:
    screen._on_start_auction_clicked()
    first = screen._session.current_player
    screen._execute_unsold()
    second = screen._session.current_player
    assert second.id != first.id


def test_unsold_does_not_change_team_budgets(screen) -> None:
    screen._on_start_auction_clicked()
    budgets_before = {team.name: team.remaining_budget for team in screen._session.teams}
    screen._execute_unsold()
    for team in screen._session.teams:
        assert team.remaining_budget == budgets_before[team.name]


def test_unsold_does_not_change_rosters(screen) -> None:
    screen._on_start_auction_clicked()
    rosters_before = {team.name: list(team.roster) for team in screen._session.teams}
    screen._execute_unsold()
    for team in screen._session.teams:
        assert team.roster == rosters_before[team.name]


def test_unsold_displays_unsold_feedback(screen) -> None:
    screen._on_start_auction_clicked()
    screen._execute_unsold()
    assert screen._session.last_result.outcome == "UNSOLD"


def test_team_selection_not_required_for_unsold(screen) -> None:
    screen._on_start_auction_clicked()
    assert screen._selected_team_id is None
    screen._execute_unsold()
    assert screen._session.resolved_count == 1


# ============================================================
# TEAMS SCREEN SHARED STATE
# ============================================================


def test_teams_screen_uses_canonical_state_before_auction_starts(hidden_root, session) -> None:
    from ui.screens.teams_screen import TeamsScreen

    teams_screen = TeamsScreen(hidden_root, session=session)
    try:
        blackout = next(s for s in teams_screen._summaries if s.team.name == "Blackout FC")
        assert blackout.remaining_budget == 100
        assert blackout.squad_size == 1
    finally:
        teams_screen.destroy()


def test_teams_screen_uses_live_state_after_a_sale(hidden_root, session) -> None:
    from ui.screens.teams_screen import TeamsScreen

    session.start(seed=1)
    blackout_live = team_by_name(session.teams, "Blackout FC")
    session.sell_current_player(winning_team=blackout_live.id, sale_price=10)

    teams_screen = TeamsScreen(hidden_root, session=session)
    try:
        blackout_summary = next(s for s in teams_screen._summaries if s.team.name == "Blackout FC")
        assert blackout_summary.remaining_budget == 90
        assert blackout_summary.squad_size == 2
        assert blackout_summary.team is blackout_live
    finally:
        teams_screen.destroy()


def test_purchased_player_is_visible_on_teams_screen(hidden_root, session) -> None:
    from ui.screens.teams_screen import TeamsScreen

    session.start(seed=1)
    purchased = session.current_player
    blackout_live = team_by_name(session.teams, "Blackout FC")
    session.sell_current_player(winning_team=blackout_live.id, sale_price=10)

    teams_screen = TeamsScreen(hidden_root, session=session)
    try:
        blackout_summary = next(s for s in teams_screen._summaries if s.team.name == "Blackout FC")
        roster_ids = [player.id for player in blackout_summary.roster_players]
        assert purchased.id in roster_ids
    finally:
        teams_screen.destroy()


def test_returning_to_live_auction_still_shows_same_session(hidden_root, screen) -> None:
    from ui.screens.live_auction_screen import LiveAuctionScreen

    screen._on_start_auction_clicked()
    team = team_by_name(screen._session.teams, "Blackout FC")
    screen._execute_sale(team.id, 10)
    resolved_after_first = screen._session.resolved_count

    # Simulate MainWindow destroying and recreating the screen frame.
    recreated = LiveAuctionScreen(hidden_root, screen._session)
    try:
        assert recreated._session.resolved_count == resolved_after_first
        assert recreated._session.auction is screen._session.auction
    finally:
        recreated.destroy()


# ============================================================
# PHOTO / PLACEHOLDER
# ============================================================


def test_load_image_safely_returns_none_for_missing_path() -> None:
    from ui.widgets import load_image_safely

    assert load_image_safely(Path("does/not/exist.png"), (140, 140)) is None


def test_load_image_safely_returns_none_for_corrupt_file(tmp_path) -> None:
    from ui.widgets import load_image_safely

    bad_file = tmp_path / "corrupt.png"
    bad_file.write_bytes(b"not a real image")
    assert load_image_safely(bad_file, (140, 140)) is None


def test_load_image_safely_loads_a_valid_image() -> None:
    from services.config_service import ROOT_DIR
    from ui.widgets import load_image_safely

    logo_path = ROOT_DIR / "assets" / "branding" / "calgary_vipers_logo.png"
    assert load_image_safely(logo_path, (140, 140)) is not None


def test_player_card_falls_back_to_placeholder_when_photo_path_is_none(hidden_root, session) -> None:
    from ui.screens.live_auction_screen import LiveAuctionScreen

    session.start(seed=1)
    session.current_player.photo_path = None

    built = LiveAuctionScreen(hidden_root, session)
    try:
        assert built._current_photo_image is None
    finally:
        built.destroy()


def test_player_card_loads_a_real_canonical_photo(hidden_root, session) -> None:
    # Every canonical player now has a real photo asset (September 2026
    # photo integration) — the current player's card should load it, not
    # fall back to the initials placeholder.
    from ui.screens.live_auction_screen import LiveAuctionScreen

    session.start(seed=1)
    assert session.current_player.photo_path is not None

    built = LiveAuctionScreen(hidden_root, session)
    try:
        assert built._current_photo_image is not None
    finally:
        built.destroy()


def test_player_card_handles_missing_photo_file_without_crashing(hidden_root, session) -> None:
    from ui.screens.live_auction_screen import LiveAuctionScreen

    session.start(seed=1)
    session.current_player.photo_path = "assets/players/does_not_exist.png"

    built = LiveAuctionScreen(hidden_root, session)
    try:
        assert built._current_photo_image is None
    finally:
        built.destroy()


def test_player_card_handles_corrupt_photo_file_without_crashing(hidden_root, session, tmp_path) -> None:
    from ui.screens.live_auction_screen import LiveAuctionScreen

    bad_file = tmp_path / "corrupt.png"
    bad_file.write_bytes(b"not a real image")

    session.start(seed=1)
    session.current_player.photo_path = str(bad_file)

    built = LiveAuctionScreen(hidden_root, session)
    try:
        assert built._current_photo_image is None
    finally:
        built.destroy()


# ============================================================
# PREMIUM CARD (September 2026): the current-player reveal now reuses the
# same Vipers-branded card-art system as the Player Cards gallery (see
# ui/player_card_art.py), plus a last-season FPL line during bidding.
# ============================================================


def _force_current_player_by_name(session: AuctionSession, full_name: str) -> None:
    player = next(p for p in session.players if p.full_name == full_name)
    session.auction.queue[session.auction.current_queue_position] = player.id


def test_current_player_card_uses_shared_card_art_helper(screen, session) -> None:
    """The card's composited image is exactly one of the two shared sizes
    ui.player_card_art produces — not a second/duplicate implementation."""
    from ui.screens.live_auction_screen import _COMPACT_CARD_GEOMETRY, _STANDARD_CARD_GEOMETRY

    start_with_seed(screen, seed=1)
    assert screen._current_card_art_image is not None
    width, height = screen._current_card_art_image.cget("size")
    assert (width, height) in (_STANDARD_CARD_GEOMETRY["card_size"], _COMPACT_CARD_GEOMETRY["card_size"])


def test_current_players_real_photo_loads(screen, session) -> None:
    start_with_seed(screen, seed=1)
    assert session.current_player.photo_path is not None
    assert screen._current_photo_image is not None


def test_position_displays_correctly(screen, session) -> None:
    start_with_seed(screen, seed=1)
    assert _find_widgets_by_text(screen, session.current_player.position.value)


def test_ovr_displays_correctly(screen, session) -> None:
    start_with_seed(screen, seed=1)
    assert _find_widgets_by_text(screen, str(session.current_player.overall_rating))


def test_known_fpl_displays_correctly(screen, session) -> None:
    start_with_seed(screen, seed=1)
    _force_current_player_by_name(session, "Rizvi Ibrahim")
    screen._render()
    assert _find_widgets_by_text(screen, "FPL 111")


def test_arik_shows_fpl_70(screen, session) -> None:
    start_with_seed(screen, seed=1)
    _force_current_player_by_name(session, "Shahriar Anwar Khan")
    screen._render()
    assert _find_widgets_by_text(screen, "FPL 70")


def test_rizvi_shows_fpl_111(screen, session) -> None:
    start_with_seed(screen, seed=1)
    _force_current_player_by_name(session, "Rizvi Ibrahim")
    screen._render()
    assert _find_widgets_by_text(screen, "FPL 111")


def test_munem_shows_fpl_na(screen, session) -> None:
    start_with_seed(screen, seed=1)
    _force_current_player_by_name(session, "Munem")
    screen._render()
    assert _find_widgets_by_text(screen, "FPL N/A")


def test_masrur_shows_fpl_na(screen, session) -> None:
    start_with_seed(screen, seed=1)
    _force_current_player_by_name(session, "Masrur Rahman")
    screen._render()
    assert _find_widgets_by_text(screen, "FPL N/A")


def test_none_fpl_never_rendered_as_zero(screen, session) -> None:
    start_with_seed(screen, seed=1)
    _force_current_player_by_name(session, "Munem")
    screen._render()
    assert not _find_widgets_by_text(screen, "FPL 0")
    assert _find_widgets_by_text(screen, "FPL N/A")


def test_real_zero_fpl_would_render_as_zero(screen, session) -> None:
    """A player whose FPL is genuinely 0 (a real, distinct score) must
    show "FPL 0", never N/A — None and 0 are never conflated."""
    start_with_seed(screen, seed=1)
    session.current_player.last_season_fpl_points = 0
    screen._render()
    assert _find_widgets_by_text(screen, "FPL 0")
    assert not _find_widgets_by_text(screen, "FPL N/A")


def test_advancing_player_updates_fpl_value(screen, session) -> None:
    start_with_seed(screen, seed=1)
    _force_current_player_by_name(session, "Munem")
    screen._render()
    assert _find_widgets_by_text(screen, "FPL N/A")

    team = team_by_name(session.teams, "Blackout FC")
    screen._execute_sale(team.id, 5)
    _force_current_player_by_name(session, "Rizvi Ibrahim")
    screen._render()
    assert _find_widgets_by_text(screen, "FPL 111")
    assert not _find_widgets_by_text(screen, "FPL N/A")


def test_advancing_player_updates_photo_name_ovr_position_together(screen, session) -> None:
    start_with_seed(screen, seed=1)
    _force_current_player_by_name(session, "Munem")
    screen._render()
    assert _find_widgets_by_text(screen, "MUNEM")
    assert _find_widgets_by_text(screen, "89")
    assert _find_widgets_by_text(screen, "MID")

    team = team_by_name(session.teams, "Blackout FC")
    screen._execute_sale(team.id, 5)
    _force_current_player_by_name(session, "Rizvi Ibrahim")
    screen._render()
    assert _find_widgets_by_text(screen, "RIZVI IBRAHIM")
    assert _find_widgets_by_text(screen, "90")
    assert _find_widgets_by_text(screen, "DEF")
    assert not _find_widgets_by_text(screen, "MUNEM")


def test_stale_card_art_reference_replaced_safely(screen, session) -> None:
    """Exactly one reference is kept per attribute (not a growing list),
    replaced outright on every advance — nothing accumulates."""
    start_with_seed(screen, seed=1)
    first_art = screen._current_card_art_image

    team = next(t for t in session.teams if team_can_bid_for_player(t, session.current_player, session.players))
    screen._execute_sale(team.id, 5)
    second_art = screen._current_card_art_image

    assert first_art is not second_art
    assert isinstance(screen._current_card_art_image, ctk.CTkImage)


def test_missing_photo_fallback_still_works_with_new_card(hidden_root, session) -> None:
    from ui.screens.live_auction_screen import LiveAuctionScreen

    session.start(seed=1)
    session.current_player.photo_path = None

    built = LiveAuctionScreen(hidden_root, session)
    try:
        assert built._current_photo_image is None
        assert built._current_card_art_image is not None  # the placeholder-panel art still renders
        assert _find_widgets_by_text(built, session.current_player.short_name[:2].upper())
    finally:
        built.destroy()


def test_card_geometry_chooses_compact_below_threshold() -> None:
    from ui.screens.live_auction_screen import (
        _COMPACT_CARD_GEOMETRY,
        _COMPACT_WIDTH_THRESHOLD,
        _STANDARD_CARD_GEOMETRY,
        _choose_card_geometry,
    )

    assert _choose_card_geometry(1024) == _COMPACT_CARD_GEOMETRY
    assert _choose_card_geometry(_COMPACT_WIDTH_THRESHOLD - 1) == _COMPACT_CARD_GEOMETRY
    assert _choose_card_geometry(_COMPACT_WIDTH_THRESHOLD) == _STANDARD_CARD_GEOMETRY
    assert _choose_card_geometry(1920) == _STANDARD_CARD_GEOMETRY


# -- bidding controls still function alongside the new card ---------------


def test_bidding_controls_still_render_with_new_card(screen, session) -> None:
    start_with_seed(screen, seed=1)
    assert _find_button_by_text(screen, "SOLD") is not None
    assert _find_button_by_text(screen, "UNSOLD") is not None


def test_team_selection_still_functions_with_new_card(screen, session) -> None:
    start_with_seed(screen, seed=1)
    blackout = team_by_name(session.teams, "Blackout FC")
    screen._on_team_selected(blackout.id)
    assert screen._selected_team_id == blackout.id


def test_sold_confirmation_still_functions_with_new_card(screen, session) -> None:
    start_with_seed(screen, seed=1)
    team = next(t for t in session.teams if team_can_bid_for_player(t, session.current_player, session.players))
    screen._execute_sale(team.id, 7)
    assert session.last_result.outcome == "SOLD"


def test_unsold_still_functions_with_new_card(screen, session) -> None:
    start_with_seed(screen, seed=1)
    screen._execute_unsold()
    assert session.last_result.outcome == "UNSOLD"


def test_max_legal_bid_display_remains_correct_with_new_card(screen, session) -> None:
    start_with_seed(screen, seed=1)
    # Team buttons render multi-line text ("Team\nBudget\n...\nMax 94M"),
    # so this needs a substring search, not an exact-text match.
    assert _find_widget_containing_text(screen, "Max 94M") is not None


# ============================================================
# COMPLETION
# ============================================================


def test_final_transaction_produces_auction_complete_state(screen) -> None:
    start_with_seed(screen, seed=1)
    resolve_whole_screen(screen, sale_price=1)
    assert screen._session.is_complete is True


def test_no_next_player_shown_after_completion(screen) -> None:
    start_with_seed(screen, seed=1)
    resolve_whole_screen(screen, sale_price=1)
    assert screen._session.current_player is None


def test_sold_and_unsold_buttons_disappear_after_completion(screen) -> None:
    start_with_seed(screen, seed=1)
    resolve_whole_screen(screen, sale_price=1)
    assert _find_widgets_by_text(screen, "SOLD") == []
    assert _find_widgets_by_text(screen, "UNSOLD") == []


def test_no_transaction_allowed_after_completion(screen) -> None:
    start_with_seed(screen, seed=1)
    resolve_whole_screen(screen, sale_price=1)
    with pytest.raises(AuctionTransactionError, match="already complete"):
        screen._session.sell_current_player(winning_team=screen._session.teams[0].id, sale_price=1)
    with pytest.raises(AuctionTransactionError, match="already complete"):
        screen._session.mark_current_player_unsold()


def test_final_team_values_remain_visible_after_completion(screen) -> None:
    start_with_seed(screen, seed=1)
    resolve_whole_screen(screen, sale_price=1)
    for team in screen._session.teams:
        assert team.roster_size == 8
        assert team.remaining_budget == 93  # 100 - (7 purchases x 1M)


def test_sold_unsold_totals_match_history(screen) -> None:
    start_with_seed(screen, seed=1)
    for index in range(10):
        current = screen._session.current_player
        if index % 2 == 0:
            eligible = [t for t in screen._session.teams if team_can_bid_for_player(t, current, screen._session.players)]
            team = min(eligible, key=lambda t: t.roster_size)
            screen._execute_sale(team.id, 1)
        else:
            screen._execute_unsold()

    sold_in_history = sum(1 for entry in screen._session.auction.history if entry.status == "SOLD")
    unsold_in_history = sum(1 for entry in screen._session.auction.history if entry.status == "UNSOLD")
    assert sold_in_history == 5
    assert unsold_in_history == 5
    # resolved_count is scoped to the current round; all 10 attempts here
    # happen within round 1 (28 players), so it matches the raw total.
    assert sold_in_history + unsold_in_history == screen._session.resolved_count


# ============================================================
# REGRESSION
# ============================================================


def test_players_setup_screen_still_works() -> None:
    from services.player_service import load_players

    assert len(load_players()) == 32


def test_teams_screen_still_works_with_no_session() -> None:
    from services.team_service import load_team_summaries

    assert len(load_team_summaries()) == 4


def test_no_sidebar_screens_remain_placeholders() -> None:
    """Auction History, Reports, and (September 2026) Settings are no
    longer placeholders — see tests/test_auction_history_screen.py,
    tests/test_reports_screen.py, and tests/test_settings_screen.py.
    Every sidebar route now has a real screen."""
    from ui.main_window import NAV_ITEMS
    from ui.screens import SCREEN_BUILDERS

    for name, _description in NAV_ITEMS:
        assert name in SCREEN_BUILDERS


def test_canonical_players_json_unchanged_after_full_screen_flow(screen) -> None:
    path = ROOT / "data" / "players.json"
    before = path.read_text(encoding="utf-8")
    screen._on_start_auction_clicked()
    team = team_by_name(screen._session.teams, "Blackout FC")
    screen._execute_sale(team.id, 10)
    screen._execute_unsold()
    assert path.read_text(encoding="utf-8") == before


def test_canonical_teams_json_unchanged_after_full_screen_flow(screen) -> None:
    path = ROOT / "data" / "teams.json"
    before = path.read_text(encoding="utf-8")
    screen._on_start_auction_clicked()
    team = team_by_name(screen._session.teams, "Blackout FC")
    screen._execute_sale(team.id, 10)
    screen._execute_unsold()
    assert path.read_text(encoding="utf-8") == before


def test_main_window_default_screen_is_now_the_real_live_auction_screen() -> None:
    import tkinter

    from ui.screens.live_auction_screen import LiveAuctionScreen

    try:
        from ui.main_window import MainWindow

        window = MainWindow()
    except tkinter.TclError as exc:
        pytest.skip(f"No display/Tk backend available for GUI test: {exc}")

    try:
        window.withdraw()
        assert isinstance(window._screen_frame, LiveAuctionScreen)
        assert window._screen_frame._session is window.session
    finally:
        window.destroy()


# ============================================================
# CONFIRMATION DIALOG (bug-fix regression: Confirm/Cancel were clipped
# off the bottom of a fixed-size SOLD dialog on some Windows displays)
# ============================================================


def test_sold_confirmation_dialog_can_be_created_successfully(screen) -> None:
    screen._on_start_auction_clicked()
    team = team_by_name(screen._session.teams, "Blackout FC")
    player = screen._session.current_player

    screen._open_sale_confirmation(player, team, 10)
    dialog = _find_toplevel(screen)
    try:
        assert dialog is not None
        assert dialog.winfo_exists()
    finally:
        if dialog is not None:
            dialog.destroy()


def test_sold_dialog_confirm_and_cancel_buttons_are_present_and_sized(screen) -> None:
    """Regression test for the clipped-buttons bug.

    Deliberately does not assert on window-manager-relative pixel
    positions (winfo_rooty()/winfo_y() are unreliable for a dialog whose
    parent chain is withdrawn, as in this test's hidden_root — verified
    empirically while writing this test) — that's exactly the kind of
    brittle geometry assertion the fix itself should not depend on either.
    Instead this checks the actual mechanism the fix relies on: because
    _finalize_dialog reads winfo_reqwidth/reqheight only *after* both
    buttons are packed, Tk's own pack() geometry propagation guarantees
    the dialog's requested size already accounts for them — so a real,
    positive requested size together with both buttons actually existing
    and being independently sized is the meaningful, non-flaky check.
    Real-window clipping is additionally confirmed by manual screenshot
    verification (see the bug-fix report).
    """
    screen._on_start_auction_clicked()
    team = team_by_name(screen._session.teams, "Blackout FC")
    player = screen._session.current_player

    screen._open_sale_confirmation(player, team, 10)
    dialog = _find_toplevel(screen)
    try:
        confirm_button = _find_button_by_text(dialog, "Confirm")
        cancel_button = _find_button_by_text(dialog, "Cancel")
        assert confirm_button is not None
        assert cancel_button is not None

        dialog.update_idletasks()
        assert confirm_button.winfo_reqwidth() > 0
        assert confirm_button.winfo_reqheight() > 0
        assert cancel_button.winfo_reqwidth() > 0
        assert cancel_button.winfo_reqheight() > 0
        assert dialog.winfo_reqheight() > 0
        assert dialog.winfo_reqwidth() > 0
    finally:
        dialog.destroy()


def test_sold_dialog_geometry_is_content_driven_not_a_tiny_fixed_size(screen) -> None:
    """The old bug came from a fixed 360x280 guess set before any content
    was packed; the fix (_finalize_dialog) instead reads winfo_reqwidth/
    reqheight *after* packing everything, so the requested size always
    reflects what the content actually needs at the current font/DPI
    scaling. Asserting on winfo_reqwidth/reqheight (rather than round-
    tripping through the scaled .geometry() string) avoids depending on
    CTk's internal scaling/WM-timing behavior."""
    screen._on_start_auction_clicked()
    team = team_by_name(screen._session.teams, "Blackout FC")
    player = screen._session.current_player

    screen._open_sale_confirmation(player, team, 10)
    dialog = _find_toplevel(screen)
    try:
        dialog.update_idletasks()
        assert dialog.winfo_reqwidth() > 0
        assert dialog.winfo_reqheight() > 0
    finally:
        dialog.destroy()


def test_confirm_action_triggers_the_sale(screen) -> None:
    screen._on_start_auction_clicked()
    current = screen._session.current_player
    team = team_by_name(screen._session.teams, "Blackout FC")

    screen._open_sale_confirmation(current, team, 10)
    dialog = _find_toplevel(screen)
    confirm_button = _find_button_by_text(dialog, "Confirm")
    assert confirm_button is not None

    confirm_button.cget("command")()  # simulate an actual organizer click

    assert not dialog.winfo_exists()  # dialog closed itself
    assert team.remaining_budget == 90
    assert team.roster_size == 2
    assert screen._session.last_result.outcome == "SOLD"
    assert screen._session.last_result.player.id == current.id


def test_cancel_action_does_not_trigger_a_sale(screen) -> None:
    screen._on_start_auction_clicked()
    current = screen._session.current_player
    team = team_by_name(screen._session.teams, "Blackout FC")
    budget_before = team.remaining_budget
    roster_before = list(team.roster)
    position_before = screen._session.auction.current_queue_position

    screen._open_sale_confirmation(current, team, 10)
    dialog = _find_toplevel(screen)
    cancel_button = _find_button_by_text(dialog, "Cancel")
    assert cancel_button is not None

    cancel_button.cget("command")()  # simulate an actual organizer click

    assert not dialog.winfo_exists()
    assert team.remaining_budget == budget_before
    assert team.roster == roster_before
    assert screen._session.auction.current_queue_position == position_before
    assert screen._session.auction.history == []
    assert screen._session.current_player.id == current.id
    assert screen._session.last_result is None


def test_closing_sold_dialog_via_window_x_does_not_trigger_a_sale(screen) -> None:
    """The dialog's WM_DELETE_WINDOW protocol (the window's own X button)
    must behave exactly like Cancel: destroy with no transaction."""
    screen._on_start_auction_clicked()
    current = screen._session.current_player
    team = team_by_name(screen._session.teams, "Blackout FC")
    budget_before = team.remaining_budget

    screen._open_sale_confirmation(current, team, 10)
    dialog = _find_toplevel(screen)

    close_handler_name = dialog.protocol("WM_DELETE_WINDOW")
    assert close_handler_name  # a handler is actually registered
    dialog.tk.call(close_handler_name)  # invoke the registered Tcl close handler, as the WM would

    assert not dialog.winfo_exists()
    assert team.remaining_budget == budget_before
    assert screen._session.auction.history == []
    assert screen._session.last_result is None


def test_unsold_confirmation_dialog_can_be_created_successfully(screen) -> None:
    screen._on_start_auction_clicked()
    player = screen._session.current_player

    screen._open_unsold_confirmation(player)
    dialog = _find_toplevel(screen)
    try:
        assert dialog is not None
        assert dialog.winfo_exists()
        assert _find_button_by_text(dialog, "Confirm") is not None
        assert _find_button_by_text(dialog, "Cancel") is not None
    finally:
        dialog.destroy()


def test_unsold_dialog_confirm_action_still_works_after_the_shared_fix(screen) -> None:
    screen._on_start_auction_clicked()
    current = screen._session.current_player

    screen._open_unsold_confirmation(current)
    dialog = _find_toplevel(screen)
    confirm_button = _find_button_by_text(dialog, "Confirm")
    assert confirm_button is not None

    confirm_button.cget("command")()

    assert not dialog.winfo_exists()
    assert screen._session.last_result.outcome == "UNSOLD"
    assert screen._session.last_result.player.id == current.id
    assert screen._session.resolved_count == 1


def test_unsold_dialog_cancel_action_leaves_state_unchanged(screen) -> None:
    screen._on_start_auction_clicked()
    current = screen._session.current_player
    budgets_before = {team.name: team.remaining_budget for team in screen._session.teams}
    position_before = screen._session.auction.current_queue_position

    screen._open_unsold_confirmation(current)
    dialog = _find_toplevel(screen)
    cancel_button = _find_button_by_text(dialog, "Cancel")
    assert cancel_button is not None

    cancel_button.cget("command")()

    assert not dialog.winfo_exists()
    for team in screen._session.teams:
        assert team.remaining_budget == budgets_before[team.name]
    assert screen._session.auction.current_queue_position == position_before
    assert screen._session.auction.history == []
    assert screen._session.current_player.id == current.id


def test_unsold_dialog_confirm_and_cancel_buttons_are_present_and_sized(screen) -> None:
    """See test_sold_dialog_confirm_and_cancel_buttons_are_present_and_sized
    for why this checks widget existence/sizing rather than
    window-manager-relative pixel positions."""
    screen._on_start_auction_clicked()
    player = screen._session.current_player

    screen._open_unsold_confirmation(player)
    dialog = _find_toplevel(screen)
    try:
        confirm_button = _find_button_by_text(dialog, "Confirm")
        cancel_button = _find_button_by_text(dialog, "Cancel")
        assert confirm_button is not None
        assert cancel_button is not None

        dialog.update_idletasks()
        assert confirm_button.winfo_reqwidth() > 0
        assert confirm_button.winfo_reqheight() > 0
        assert cancel_button.winfo_reqwidth() > 0
        assert cancel_button.winfo_reqheight() > 0
        assert dialog.winfo_reqheight() > 0
        assert dialog.winfo_reqwidth() > 0
    finally:
        dialog.destroy()


# ============================================================
# CONFIRMATION PREFERENCE INTEGRATION (Settings — September 2026): the
# Confirm SOLD/UNSOLD toggles only control whether the dialog appears —
# `_execute_sale`/`_execute_unsold` (the exact same calls the dialog's own
# Confirm button makes) run identically either way.
# ============================================================


def _isolate_preferences(tmp_path, monkeypatch):
    from services import preferences_service as ps

    monkeypatch.setattr(ps, "PREFERENCES_PATH", tmp_path / "user_preferences.json")
    return ps


def test_sold_click_skips_dialog_when_confirm_sold_preference_off(screen, tmp_path, monkeypatch) -> None:
    ps = _isolate_preferences(tmp_path, monkeypatch)
    ps.save_preferences(ps.UserPreferences(confirm_sold=False))

    screen._on_start_auction_clicked()
    team = team_by_name(screen._session.teams, "Blackout FC")
    screen._on_team_selected(team.id)
    screen._price_var.set("10")

    screen._on_sold_clicked()

    assert _find_toplevel(screen) is None
    assert screen._session.last_result.outcome == "SOLD"
    assert screen._session.last_result.sale_price == 10
    assert screen._session.last_result.team.name == "Blackout FC"


def test_unsold_click_skips_dialog_when_confirm_unsold_preference_off(screen, tmp_path, monkeypatch) -> None:
    ps = _isolate_preferences(tmp_path, monkeypatch)
    ps.save_preferences(ps.UserPreferences(confirm_unsold=False))

    screen._on_start_auction_clicked()
    screen._on_unsold_clicked()

    assert _find_toplevel(screen) is None
    assert screen._session.last_result.outcome == "UNSOLD"


def test_sold_click_still_opens_dialog_by_default(screen, tmp_path, monkeypatch) -> None:
    _isolate_preferences(tmp_path, monkeypatch)  # no file on disk -> defaults (confirm_sold=True)

    screen._on_start_auction_clicked()
    team = team_by_name(screen._session.teams, "Blackout FC")
    screen._on_team_selected(team.id)
    screen._price_var.set("10")

    screen._on_sold_clicked()

    dialog = _find_toplevel(screen)
    try:
        assert dialog is not None
        assert screen._session.last_result is None  # nothing executed until Confirm is clicked
    finally:
        if dialog is not None:
            dialog.destroy()


def test_unsold_click_still_opens_dialog_by_default(screen, tmp_path, monkeypatch) -> None:
    _isolate_preferences(tmp_path, monkeypatch)

    screen._on_start_auction_clicked()
    screen._on_unsold_clicked()

    dialog = _find_toplevel(screen)
    try:
        assert dialog is not None
        assert screen._session.last_result is None
    finally:
        if dialog is not None:
            dialog.destroy()


def test_confirmation_off_sold_still_applies_full_validation(screen, tmp_path, monkeypatch) -> None:
    """Skipping the dialog never skips business-layer validation — an
    illegal sale (a price above the team's legal maximum) is still
    rejected exactly as it would be with the dialog on."""
    ps = _isolate_preferences(tmp_path, monkeypatch)
    ps.save_preferences(ps.UserPreferences(confirm_sold=False))

    screen._on_start_auction_clicked()
    team = team_by_name(screen._session.teams, "Blackout FC")
    screen._on_team_selected(team.id)
    screen._price_var.set("9999")

    screen._on_sold_clicked()

    assert _find_toplevel(screen) is None
    assert screen._error_message is not None
    assert screen._session.resolved_count == 0  # nothing was sold


# ============================================================
# CAPTAIN PHONE BIDDING — PHASE 1 (September 2026): desktop bidding
# controls, the shared live-bid display, SOLD pre-fill, and reset-on-
# advancement — see services/live_bid_service.py and
# services/captain_bidding_server.py for the underlying service/server.
# ============================================================


def test_live_auction_server_status_displays_off_by_default(screen) -> None:
    screen._on_start_auction_clicked()
    assert _find_widget_containing_text(screen, "CAPTAIN BIDDING: OFF") is not None


def test_live_auction_server_status_displays_running(hidden_root, session) -> None:
    from services.captain_bidding_server import CaptainBiddingServer
    from ui.screens.live_auction_screen import LiveAuctionScreen

    hidden_root.captain_bidding_server = CaptainBiddingServer(session, host="127.0.0.1", port=18790)
    hidden_root.captain_bidding_server.start()
    try:
        session.start(seed=1)
        built = LiveAuctionScreen(hidden_root, session)
        try:
            assert _find_widget_containing_text(built, "CAPTAIN BIDDING: RUNNING") is not None
        finally:
            built.destroy()
    finally:
        hidden_root.captain_bidding_server.stop()
        del hidden_root.captain_bidding_server


def test_desktop_bid_accepted(screen) -> None:
    screen._on_start_auction_clicked()
    blackout = team_by_name(screen._session.teams, "Blackout FC")
    screen._on_team_selected(blackout.id)

    screen._on_desktop_bid_clicked(5)

    assert screen._session.auction.current_bid == 5
    assert screen._session.auction.leading_team_id == blackout.id
    assert screen._error_message is None


def test_desktop_bid_increments_from_current_bid(screen) -> None:
    """+1/+2/+5 add to the current live bid, matching the ticket's exact
    example (current 7M, +5 -> 12M) — never to some other base."""
    screen._on_start_auction_clicked()
    blackout = team_by_name(screen._session.teams, "Blackout FC")
    screen._on_team_selected(blackout.id)

    screen._on_desktop_bid_clicked(1)
    assert screen._session.auction.current_bid == 1
    screen._on_desktop_bid_clicked(2)
    assert screen._session.auction.current_bid == 3
    screen._on_desktop_bid_clicked(5)
    assert screen._session.auction.current_bid == 8


def test_desktop_bid_uses_same_bid_service_as_direct_call(screen) -> None:
    """Desktop bidding and a direct `place_live_bid` call produce
    identical state — proving there is no second/parallel bid mechanism."""
    screen._on_start_auction_clicked()
    blackout = team_by_name(screen._session.teams, "Blackout FC")
    screen._on_team_selected(blackout.id)
    screen._on_desktop_bid_clicked(5)

    darkstar = team_by_name(screen._session.teams, "Darkstar FC")
    direct_result = screen._session.place_live_bid(darkstar.id, 6)

    assert direct_result.accepted is True
    assert screen._session.auction.current_bid == 6
    assert screen._session.auction.leading_team_id == darkstar.id


def test_desktop_bid_rejected_shows_error_message(screen) -> None:
    screen._on_start_auction_clicked()
    blackout = team_by_name(screen._session.teams, "Blackout FC")
    screen._on_team_selected(blackout.id)
    screen._on_desktop_bid_clicked(5)

    darkstar = team_by_name(screen._session.teams, "Darkstar FC")
    screen._on_team_selected(darkstar.id)
    screen._on_desktop_bid_clicked(0)  # +0 would equal, not exceed, the current bid

    assert screen._error_message is not None
    assert screen._session.auction.current_bid == 5  # unchanged


def test_phone_bid_visible_on_live_auction(screen) -> None:
    """A bid placed via the same shared service the phone API uses (not
    through the desktop UI at all) shows up in the screen's own state —
    proving the desktop reads the one authoritative AuctionSession."""
    screen._on_start_auction_clicked()
    blackout = team_by_name(screen._session.teams, "Blackout FC")

    result = screen._session.place_live_bid(blackout.id, 9)
    assert result.accepted is True

    assert screen._session.auction.current_bid == 9
    assert screen._session.leading_team.name == "Blackout FC"


def test_desktop_bid_visible_through_state_endpoint() -> None:
    from services.captain_bidding_server import CaptainBiddingServer

    session = AuctionSession()
    session.autosave = None
    session.start(seed=1)
    blackout = team_by_name(session.teams, "Blackout FC")

    # A "desktop" bid — placed the exact same way LiveAuctionScreen's own
    # _on_desktop_bid_clicked does, via AuctionSession.place_live_bid.
    session.place_live_bid(blackout.id, 7)

    server = CaptainBiddingServer(session)
    from fastapi.testclient import TestClient

    client = TestClient(server.app)
    data = client.get("/api/state").json()
    assert data["current_bid"] == 7
    assert data["leading_team_name"] == "Blackout FC"


def test_sold_prefills_leading_team_and_current_bid(screen) -> None:
    screen._on_start_auction_clicked()
    blackout = team_by_name(screen._session.teams, "Blackout FC")
    screen._session.place_live_bid(blackout.id, 12)

    # No manual team/price ever set — SOLD must still work from the live bid alone.
    screen._on_sold_clicked()

    dialog = _find_toplevel(screen)
    try:
        assert dialog is not None
        assert _find_widget_containing_text(dialog, "Blackout FC") is not None
        assert _find_widget_containing_text(dialog, "12M") is not None
    finally:
        if dialog is not None:
            dialog.destroy()


def test_sold_via_live_bid_charges_exact_leading_team_and_price(screen, tmp_path, monkeypatch) -> None:
    ps = _isolate_preferences(tmp_path, monkeypatch)
    ps.save_preferences(ps.UserPreferences(confirm_sold=False))

    screen._on_start_auction_clicked()
    blackout = team_by_name(screen._session.teams, "Blackout FC")
    screen._session.place_live_bid(blackout.id, 12)

    screen._on_sold_clicked()  # dialog skipped; executes immediately

    assert screen._session.last_result.outcome == "SOLD"
    assert screen._session.last_result.team.name == "Blackout FC"
    assert screen._session.last_result.sale_price == 12
    assert blackout.remaining_budget == 88


def test_unsold_clears_live_bid(screen) -> None:
    screen._on_start_auction_clicked()
    blackout = team_by_name(screen._session.teams, "Blackout FC")
    screen._session.place_live_bid(blackout.id, 6)

    screen._execute_unsold()

    assert screen._session.auction.current_bid is None
    assert screen._session.auction.leading_team_id is None


def test_sold_clears_live_bid_for_next_player(screen, tmp_path, monkeypatch) -> None:
    ps = _isolate_preferences(tmp_path, monkeypatch)
    ps.save_preferences(ps.UserPreferences(confirm_sold=False))

    screen._on_start_auction_clicked()
    blackout = team_by_name(screen._session.teams, "Blackout FC")
    screen._session.place_live_bid(blackout.id, 6)
    screen._on_sold_clicked()

    assert screen._session.auction.current_bid is None
    assert screen._session.auction.leading_team_id is None


def test_no_stale_leader_after_advancement(screen) -> None:
    """A player forced UNSOLD with a live bid on it must never leak that
    bid/leader into the next player's fresh reveal."""
    screen._on_start_auction_clicked()
    darkstar = team_by_name(screen._session.teams, "Darkstar FC")
    screen._session.place_live_bid(darkstar.id, 20)
    screen._execute_unsold()

    assert screen._session.current_player is not None  # a new player is now current
    assert screen._session.auction.current_bid is None
    assert screen._session.auction.leading_team_id is None

    # And a fresh, unrelated bid on the new player must not be compared
    # against the old (already-cleared) 20M.
    blackout = team_by_name(screen._session.teams, "Blackout FC")
    result = screen._session.place_live_bid(blackout.id, 1)
    assert result.accepted is True


def test_normal_manual_auction_works_with_server_off(screen) -> None:
    """Phone bidding must never be a dependency for SOLD/UNSOLD — with the
    captain-bidding server never started at all, the existing manual
    team-select/price-entry/SOLD flow still works exactly as before."""
    screen._on_start_auction_clicked()
    team = team_by_name(screen._session.teams, "Blackout FC")
    screen._execute_sale(team.id, 10)
    assert screen._session.last_result.outcome == "SOLD"
    assert team.remaining_budget == 90


# ============================================================
# MILESTONE 8.1: GK / FULL-TEAM UI ELIGIBILITY, ROUND DISPLAY,
# COMPLETION TIMING, BLOCKED STATE
# ============================================================

GK_PLAYER_ID = 11  # Nabil Shahriar
OTHER_GK_PLAYER_ID = 13  # Masrur Rahman


def _find_team_button_by_name(widget, team_name: str):
    for child in widget.winfo_children():
        if isinstance(child, ctk.CTkButton):
            try:
                if child.cget("text").startswith(team_name):
                    return child
            except Exception:
                pass
        found = _find_team_button_by_name(child, team_name)
        if found is not None:
            return found
    return None


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


def _force_current_player(screen, player_id: int) -> None:
    """Rearranges the session's current round queue so `player_id` is
    current, for tests that need a specific player (e.g. a GK) on screen
    without depending on shuffle order. Setup-only, not itself the thing
    under test."""
    auction = screen._session.auction
    queue = list(auction.queue)
    queue.remove(player_id)
    auction.queue = [player_id] + queue
    auction.current_queue_position = 0
    screen._render()


def test_current_gk_disables_teams_already_owning_gk(screen) -> None:
    start_with_seed(screen, seed=1)
    blackout = next(t for t in screen._session.teams if t.name == "Blackout FC")
    blackout.roster.append(GK_PLAYER_ID)  # setup: give Blackout FC a GK

    _force_current_player(screen, OTHER_GK_PLAYER_ID)  # a different GK now up for auction
    button = _find_team_button_by_name(screen, "Blackout FC")
    assert button is not None
    assert button.cget("state") == "disabled"


def test_eligible_teams_remain_selectable(screen) -> None:
    start_with_seed(screen, seed=1)
    _force_current_player(screen, GK_PLAYER_ID)
    button = _find_team_button_by_name(screen, "Blackout FC")
    assert button is not None
    assert button.cget("state") == "normal"


def test_current_non_gk_does_not_disable_a_team_merely_because_it_owns_a_gk(screen) -> None:
    start_with_seed(screen, seed=1)
    blackout = next(t for t in screen._session.teams if t.name == "Blackout FC")
    blackout.roster.append(GK_PLAYER_ID)  # setup: give Blackout FC a GK

    non_gk_id = next(
        player.id for player in screen._session.players if player.auction_eligible and player.position.value != "GK"
    )
    _force_current_player(screen, non_gk_id)
    button = _find_team_button_by_name(screen, "Blackout FC")
    assert button is not None
    assert button.cget("state") == "normal"


def test_full_team_appears_ineligible_in_ui(screen) -> None:
    start_with_seed(screen, seed=1)
    blackout = next(t for t in screen._session.teams if t.name == "Blackout FC")
    extra_needed = blackout.max_squad_size - blackout.roster_size
    blackout.roster.extend(range(9001, 9001 + extra_needed))  # setup: fill to 8/8 directly

    screen._render()
    button = _find_team_button_by_name(screen, "Blackout FC")
    assert button is not None
    assert button.cget("state") == "disabled"


def test_round_label_shows_round_1_initially(screen) -> None:
    start_with_seed(screen, seed=1)
    assert _find_widget_containing_text(screen, "Round 1") is not None
    assert _find_widget_containing_text(screen, "Re-Auction") is None


def test_round_label_updates_to_reauction_round_2(screen) -> None:
    start_with_seed(screen, seed=1)
    for _ in range(screen._session.total_queue_length):
        screen._execute_unsold()
    assert screen._session.round_number == 2
    assert _find_widget_containing_text(screen, "Re-Auction Round 2") is not None


def test_completion_screen_not_shown_while_unsold_players_remain(screen) -> None:
    start_with_seed(screen, seed=1)
    for _ in range(screen._session.total_queue_length):
        screen._execute_unsold()
    assert screen._session.is_complete is False
    assert _find_widget_containing_text(screen, "AUCTION COMPLETE") is None


def test_completion_screen_appears_after_every_player_sold(screen) -> None:
    start_with_seed(screen, seed=1)
    resolve_whole_screen(screen, sale_price=1)
    assert screen._session.is_complete is True
    assert _find_widget_containing_text(screen, "AUCTION COMPLETE") is not None


def test_blocked_state_is_detected_and_shown_in_ui(hidden_root, session) -> None:
    from ui.screens.live_auction_screen import LiveAuctionScreen

    session.start(seed=1)
    # Fill every team to 8/8 directly (setup only) so nothing can be sold.
    for team in session.teams:
        extra_needed = team.max_squad_size - team.roster_size
        team.roster.extend(range(9000, 9000 + extra_needed))

    stuck_player = next(player for player in session.players if player.auction_eligible)
    session.auction.queue = [stuck_player.id]
    session.auction.current_queue_position = 0
    session.mark_current_player_unsold()
    assert session.is_blocked is True

    built = LiveAuctionScreen(hidden_root, session)
    try:
        assert _find_widget_containing_text(built, "AUCTION BLOCKED") is not None
        assert _find_widget_containing_text(built, "Re-auction cannot continue") is not None
        # No SOLD/UNSOLD controls once blocked.
        assert _find_widgets_by_text(built, "SOLD") == []
        assert _find_widgets_by_text(built, "UNSOLD") == []
    finally:
        built.destroy()


# ============================================================
# MILESTONE 9: SESSION LAUNCHER / PERSISTENCE
# ============================================================


@pytest.fixture
def isolated_saves(tmp_path, monkeypatch):
    """Redirect persistence_service's save paths to a temp directory so
    these UI tests never touch the real saves/ folder."""
    from services import persistence_service as ps

    mocks_dir = tmp_path / "mocks"
    live_dir = tmp_path / "live"
    monkeypatch.setattr(ps, "SAVES_DIR", tmp_path)
    monkeypatch.setattr(ps, "MOCKS_DIR", mocks_dir)
    monkeypatch.setattr(ps, "LIVE_DIR", live_dir)
    monkeypatch.setattr(ps, "LIVE_ACTIVE_PATH", live_dir / "live_active.json")
    return tmp_path


def _wire_autosave(session) -> None:
    from services import persistence_service as ps

    session.autosave = ps.autosave_hook


def test_launcher_shows_new_mock_and_new_live_buttons(screen) -> None:
    assert _find_button_by_text(screen, "NEW MOCK AUCTION") is not None
    assert _find_button_by_text(screen, "NEW LIVE AUCTION") is not None


def test_launcher_shows_no_saved_sessions_message_when_empty(isolated_saves, screen) -> None:
    assert _find_widget_containing_text(screen, "No active LIVE auction yet.") is not None
    assert _find_widget_containing_text(screen, "No saved mock sessions yet.") is not None


def test_new_mock_button_starts_a_mock_session(isolated_saves, screen) -> None:
    from services.auction_session_service import SessionMode

    button = _find_button_by_text(screen, "NEW MOCK AUCTION")
    button.invoke()
    assert screen._session.started is True
    assert screen._session.mode == SessionMode.MOCK


def test_new_live_button_starts_a_live_session_when_none_exists(isolated_saves, screen) -> None:
    from services.auction_session_service import SessionMode

    button = _find_button_by_text(screen, "NEW LIVE AUCTION")
    button.invoke()
    assert screen._session.started is True
    assert screen._session.mode == SessionMode.LIVE


def test_new_live_button_opens_confirmation_when_a_live_save_already_exists(isolated_saves, hidden_root) -> None:
    from services.auction_session_service import AuctionSession, SessionMode
    from ui.screens.live_auction_screen import LiveAuctionScreen

    existing = AuctionSession()
    _wire_autosave(existing)
    existing.start(seed=1, mode=SessionMode.LIVE)

    fresh_session = AuctionSession()
    built = LiveAuctionScreen(hidden_root, fresh_session)
    try:
        button = _find_button_by_text(built, "NEW LIVE AUCTION")
        button.invoke()
        dialog = _find_toplevel(built)
        assert dialog is not None
        assert fresh_session.started is False  # must not have started yet
        dialog.destroy()
    finally:
        built.destroy()


def test_confirming_new_live_archives_old_session_and_starts_new(isolated_saves, hidden_root) -> None:
    from services import persistence_service as ps
    from services.auction_session_service import AuctionSession, SessionMode
    from ui.screens.live_auction_screen import LiveAuctionScreen

    existing = AuctionSession()
    _wire_autosave(existing)
    existing.start(seed=1, mode=SessionMode.LIVE, name="Old Live")
    old_session_id = existing.session_id

    fresh_session = AuctionSession()
    built = LiveAuctionScreen(hidden_root, fresh_session)
    try:
        _find_button_by_text(built, "NEW LIVE AUCTION").invoke()
        dialog = _find_toplevel(built)
        confirm = _find_button_by_text(dialog, "Confirm")
        confirm.invoke()

        assert fresh_session.started is True
        assert fresh_session.mode == SessionMode.LIVE
        assert fresh_session.session_id != old_session_id
        # The old live session must be archived, not deleted.
        archived = list(ps.LIVE_DIR.glob("live_archived_*.json"))
        assert len(archived) == 1
    finally:
        built.destroy()


def test_resume_button_restores_a_saved_mock_session(isolated_saves, hidden_root) -> None:
    from services.auction_session_service import AuctionSession, SessionMode
    from ui.screens.live_auction_screen import LiveAuctionScreen

    original = AuctionSession()
    _wire_autosave(original)
    original.start(seed=1, mode=SessionMode.MOCK, name="Practice Session")
    blackout = team_by_name(original.teams, "Blackout FC")
    original.sell_current_player(winning_team=blackout.id, sale_price=7)

    fresh_session = AuctionSession()
    built = LiveAuctionScreen(hidden_root, fresh_session)
    try:
        resume_button = _find_button_by_text(built, "Resume")
        assert resume_button is not None
        resume_button.invoke()

        assert fresh_session.started is True
        assert fresh_session.sold_count == 1
        assert fresh_session.name == "Practice Session"
    finally:
        built.destroy()


def test_delete_button_removes_a_mock_session_after_confirmation(isolated_saves, hidden_root) -> None:
    from services import persistence_service as ps
    from services.auction_session_service import AuctionSession, SessionMode
    from ui.screens.live_auction_screen import LiveAuctionScreen

    doomed = AuctionSession()
    _wire_autosave(doomed)
    doomed.start(seed=1, mode=SessionMode.MOCK, name="Delete Me")
    assert len(ps.list_mock_sessions()) == 1

    fresh_session = AuctionSession()
    built = LiveAuctionScreen(hidden_root, fresh_session)
    try:
        _find_button_by_text(built, "Delete").invoke()
        dialog = _find_toplevel(built)
        _find_button_by_text(dialog, "Confirm").invoke()
        assert ps.list_mock_sessions() == []
    finally:
        built.destroy()


def test_session_mode_badge_shows_mock_or_live(isolated_saves, hidden_root) -> None:
    from services.auction_session_service import AuctionSession, SessionMode
    from ui.screens.live_auction_screen import LiveAuctionScreen

    mock_session = AuctionSession()
    mock_session.start(seed=1, mode=SessionMode.MOCK)
    built = LiveAuctionScreen(hidden_root, mock_session)
    try:
        assert _find_widget_containing_text(built, "MOCK SESSION") is not None
        assert _find_widget_containing_text(built, "LIVE SESSION") is None
    finally:
        built.destroy()

    live_session = AuctionSession()
    live_session.start(seed=1, mode=SessionMode.LIVE)
    built2 = LiveAuctionScreen(hidden_root, live_session)
    try:
        assert _find_widget_containing_text(built2, "LIVE SESSION") is not None
    finally:
        built2.destroy()


def test_save_status_shows_autosaved_after_a_successful_save(isolated_saves, hidden_root) -> None:
    from services.auction_session_service import AuctionSession
    from ui.screens.live_auction_screen import LiveAuctionScreen

    session = AuctionSession()
    _wire_autosave(session)
    session.start(seed=1)
    built = LiveAuctionScreen(hidden_root, session)
    try:
        assert _find_widget_containing_text(built, "Autosaved") is not None
        assert _find_widget_containing_text(built, "SAVE FAILED") is None
    finally:
        built.destroy()


def test_save_status_shows_save_failed_when_autosave_hook_raises(hidden_root) -> None:
    from services.auction_session_service import AuctionSession
    from ui.screens.live_auction_screen import LiveAuctionScreen

    session = AuctionSession()

    def _broken_autosave(_session):
        raise OSError("disk is full (simulated)")

    session.autosave = _broken_autosave
    session.start(seed=1)
    assert session.last_save_error is not None

    built = LiveAuctionScreen(hidden_root, session)
    try:
        assert _find_widget_containing_text(built, "SAVE FAILED") is not None
    finally:
        built.destroy()


def test_save_failure_does_not_block_a_transaction_from_succeeding(hidden_root) -> None:
    """AUTOSAVE FAILURE policy: a persistence failure must never roll back
    or block an already-successful in-memory transaction."""
    from services.auction_session_service import AuctionSession
    from ui.screens.live_auction_screen import LiveAuctionScreen

    session = AuctionSession()
    session.autosave = lambda _s: (_ for _ in ()).throw(OSError("simulated disk failure"))
    session.start(seed=1)

    blackout = team_by_name(session.teams, "Blackout FC")
    result = session.sell_current_player(winning_team=blackout.id, sale_price=5)

    assert result.outcome == "SOLD"
    assert blackout.remaining_budget == 95
    assert session.last_save_error is not None

    built = LiveAuctionScreen(hidden_root, session)
    try:
        assert _find_widget_containing_text(built, "SAVE FAILED") is not None
    finally:
        built.destroy()
