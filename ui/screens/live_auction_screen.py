"""Live Auction screen: start a session, reveal players, and record
SOLD/UNSOLD outcomes.

GUI only. Every transaction rule (budget, roster space, captain/eligible/
duplicate protection, history, queue advancement, completion) lives in
services/auction_service.py via the shared services/auction_session_service
AuctionSession — this module only collects input, calls the session, and
re-renders. It never mutates a Team/Player/Auction object directly.
"""

from __future__ import annotations

import json
import customtkinter as ctk

from models.player import Player, Position
from models.team import Team
from services import persistence_service, preferences_service
from services.auction_service import (
    AuctionTransactionError,
    TransactionResult,
    budget_reserve_violation_message,
    team_can_bid_for_player,
    team_has_goalkeeper,
)
from services.auction_session_service import AuctionSession, SessionMode
from services.config_service import load_position_colors
from services.persistence_service import PersistenceError, SavedSessionSummary
from ui import player_card_art, theme
from ui.widgets import format_timestamp, pill_badge, position_badge

QUICK_PRICE_INCREMENTS = (1, 2, 5)

# Two size variants of the shared premium player-card art (see
# ui/player_card_art.py) — the same visual language as the Player Cards
# gallery, just larger and simpler (photo/name/position/OVR/FPL only).
# STANDARD targets the app's default 1366x768/1920x1080 sizes; COMPACT
# keeps the card from crowding out the team-status/controls/action rows
# below it at the app's 1024x640 minimum window size.
_COMPACT_WIDTH_THRESHOLD = 1200

#  Both variants reserve enough vertical room between the name and the
# badge row for a full-name two-line wrap ("Shahriar Anwar Khan", "Aiman
# Nawar Chowdhury") — a real overlap bug found via screenshot when that
# gap only budgeted for a single line.
_STANDARD_CARD_GEOMETRY = {
    "card_size": (300, 490),
    "photo_offset": (18, 18),
    "photo_size": (264, 300),
    "name_y": 332,
    "badge_y": 400,
    "fpl_y": 450,
    "name_font_size": 22,
    "ovr_font_size": 30,
    "fpl_font_size": 15,
}
_COMPACT_CARD_GEOMETRY = {
    "card_size": (172, 266),
    "photo_offset": (11, 10),
    "photo_size": (150, 148),
    "name_y": 164,
    "badge_y": 204,
    "fpl_y": 234,
    "name_font_size": 13,
    "ovr_font_size": 16,
    "fpl_font_size": 10,
}


def _choose_card_geometry(window_width: int) -> dict:
    """Pure selection function (no widget access) so tests can exercise
    both branches directly without needing a real window at a specific
    size."""
    return _COMPACT_CARD_GEOMETRY if window_width < _COMPACT_WIDTH_THRESHOLD else _STANDARD_CARD_GEOMETRY


# Re-exported from ui/widgets.py (September 2026: also used by the
# Settings screen's session/save diagnostics) so this module's existing
# call sites keep working unchanged.
_format_timestamp = format_timestamp


def _describe_summary_details(summary: SavedSessionSummary) -> str:
    """Started/round/sold/status only — no name/id, for use under a card
    heading that already shows the name (the MOCKS list)."""
    round_text = f"Round {summary.round_number}" if summary.round_number else "Round —"
    sold_text = f"{summary.sold_count} SOLD" if summary.sold_count is not None else "— SOLD"
    status = summary.status or "—"
    return f"Started {_format_timestamp(summary.created_at)}  ·  {round_text}  ·  {sold_text}  ·  {status}"


def _describe_summary(summary: SavedSessionSummary) -> str:
    """Full description including the name/id — for contexts with no
    separate name heading already shown (the LIVE card, confirmation
    dialogs)."""
    label = summary.name or summary.session_id or summary.path.name
    return f"{label}  ·  {_describe_summary_details(summary)}"


def _build_stat_card(parent: ctk.CTkBaseClass, label: str, value: object) -> ctk.CTkFrame:
    card = ctk.CTkFrame(parent, fg_color=theme.SURFACE, corner_radius=8)
    ctk.CTkLabel(
        card, text=str(value), font=theme.heading_font(size=22), text_color=theme.TEXT_PRIMARY
    ).pack(padx=20, pady=(14, 0))
    ctk.CTkLabel(
        card, text=label, font=theme.body_font(size=12), text_color=theme.TEXT_SECONDARY
    ).pack(padx=20, pady=(0, 14))
    return card


class LiveAuctionScreen(ctk.CTkFrame):
    """The interactive auction-day screen, backed by a shared AuctionSession."""

    def __init__(self, parent: ctk.CTkBaseClass, session: AuctionSession) -> None:
        super().__init__(parent, fg_color=theme.BACKGROUND, corner_radius=0)
        self._session = session
        self._selected_team_id: int | None = None
        # The price entry's StringVar is recreated fresh on every _render()
        # (see _build_controls_row) rather than reused across widget
        # generations: CTkEntry leaves an internal trace registered on its
        # textvariable that still fires after the entry is destroyed, so a
        # StringVar surviving past its entry's destroy() raises TclError on
        # the next .set(). _price_text is the value that actually survives
        # across renders; _price_var only exists while its entry is alive.
        self._price_text: str = ""
        self._price_var: ctk.StringVar | None = None
        self._error_message: str | None = None
        self._launcher_error: str | None = None  # session-launcher-only errors (start/resume/delete)
        self._current_photo_image: ctk.CTkImage | None = None  # kept alive to avoid GC (non-None only for a real photo)
        self._current_card_art_image: ctk.CTkImage | None = None  # the whole composited card; kept alive to avoid GC
        self._position_colors: dict[str, str] = {}
        # Captain Phone Bidding (Phase 1): a phone bid mutates the shared
        # AuctionSession from a different thread (the web server's — see
        # services/captain_bidding_server.py), never a Tk widget directly.
        # This poll, not the phone request itself, is what refreshes the
        # desktop display — see _poll_captain_bidding_state.
        self._last_bid_snapshot: tuple | None = None
        self._poll_job: str | None = None

        try:
            self._position_colors = load_position_colors()
        except (OSError, json.JSONDecodeError, ValueError, KeyError, TypeError) as exc:
            self._build_error_state(str(exc))
            return

        self._render()
        self._poll_job = self.after(500, self._poll_captain_bidding_state)

    def destroy(self) -> None:
        if self._poll_job is not None:
            try:
                self.after_cancel(self._poll_job)
            except Exception:  # noqa: BLE001 - best-effort cleanup, must never block/prevent teardown
                pass
            self._poll_job = None
        super().destroy()

    def _has_open_dialog(self) -> bool:
        """True while a SOLD/UNSOLD/session-launcher confirmation dialog
        is open — the poll-triggered refresh must never rebuild (and
        thereby destroy) an open CTkToplevel out from under the
        organizer."""
        return any(isinstance(child, ctk.CTkToplevel) for child in self.winfo_children())

    def _bid_snapshot(self) -> tuple:
        """A cheap fingerprint of everything the desktop display needs to
        notice changed — including a phone bid, another desktop bid, a
        SOLD/UNSOLD advancing to the next player, or a round transition.
        Comparing this (rather than unconditionally re-rendering every
        poll tick) means a quiet period with no bidding activity costs
        nothing beyond one tuple comparison every 500ms."""
        session = self._session
        if not session.started or session.auction is None:
            return (None, None, None, None, None)
        current_player = session.current_player
        return (
            current_player.id if current_player is not None else None,
            session.auction.current_bid,
            session.auction.leading_team_id,
            session.auction.status,
            session.round_number,
        )

    def _poll_captain_bidding_state(self) -> None:
        """Runs on Tk's own main-thread timer (`after`) — never called
        from the web server's thread, and never touches a widget except
        through the normal `_render()` path. See services/
        captain_bidding_server.py's module docstring for the "never
        manipulate Tk widgets from the server thread" rule this keeps."""
        if not self._has_open_dialog():
            snapshot = self._bid_snapshot()
            if snapshot != self._last_bid_snapshot:
                self._last_bid_snapshot = snapshot
                self._render()
        self._poll_job = self.after(500, self._poll_captain_bidding_state)

    # ------------------------------------------------------------------
    # Top-level render dispatch
    # ------------------------------------------------------------------

    def _render(self) -> None:
        for child in self.winfo_children():
            child.destroy()

        if not self._session.started:
            self._build_not_started_view()
        elif self._session.is_blocked:
            self._build_blocked_view()
        elif self._session.is_complete:
            self._build_complete_view()
        else:
            self._build_in_progress_view()

    def _build_error_state(self, message: str) -> None:
        for child in self.winfo_children():
            child.destroy()
        self.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            self, text="LIVE AUCTION", font=theme.heading_font(size=26), text_color=theme.TEXT_PRIMARY, anchor="w"
        ).grid(row=0, column=0, sticky="w", padx=32, pady=(32, 8))
        ctk.CTkLabel(
            self,
            text="Auction data could not be loaded.",
            font=theme.body_font(size=15, weight="bold"),
            text_color=theme.UNSOLD_RED,
            anchor="w",
        ).grid(row=1, column=0, sticky="w", padx=32)
        ctk.CTkLabel(
            self,
            text=message,
            font=theme.body_font(size=13),
            text_color=theme.TEXT_SECONDARY,
            anchor="w",
            justify="left",
            wraplength=760,
        ).grid(row=2, column=0, sticky="w", padx=32, pady=(8, 0))

    # ------------------------------------------------------------------
    # Not-started state: the session launcher (Milestone 9)
    # ------------------------------------------------------------------

    def _build_not_started_view(self) -> None:
        """No active in-memory session: let the organizer start a brand
        new MOCK/LIVE auction, or resume a saved one. Kept deliberately
        focused (per PROJECT_CONTEXT.md's persistence UI scope) — once a
        session is active, this entire view disappears in favor of the
        unchanged in-progress/complete/blocked views below."""
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=40, pady=(24, 8))

        ctk.CTkLabel(
            header, text="CALGARY VIPERS AUCTION 2026", font=theme.heading_font(size=28), text_color=theme.TEXT_PRIMARY
        ).pack()
        ctk.CTkLabel(
            header,
            text="Start a new auction, or resume a saved session below.",
            font=theme.body_font(size=14),
            text_color=theme.TEXT_SECONDARY,
        ).pack(pady=(4, 16))

        if self._launcher_error:
            self._build_error_banner(header, self._launcher_error).pack(fill="x", pady=(0, 16))

        button_row = ctk.CTkFrame(header, fg_color="transparent")
        button_row.pack()
        ctk.CTkButton(
            button_row,
            text="NEW MOCK AUCTION",
            font=theme.heading_font(size=15),
            fg_color=theme.SURFACE_ALT,
            hover_color=theme.BORDER,
            text_color=theme.TEXT_PRIMARY,
            width=220,
            height=48,
            corner_radius=10,
            command=self._on_new_mock_clicked,
        ).pack(side="left", padx=8)
        ctk.CTkButton(
            button_row,
            text="NEW LIVE AUCTION",
            font=theme.heading_font(size=15),
            fg_color=theme.UNSOLD_RED,
            hover_color="#c93f43",
            text_color=theme.TEXT_PRIMARY,
            width=220,
            height=48,
            corner_radius=10,
            command=self._on_new_live_clicked,
        ).pack(side="left", padx=8)

        body = ctk.CTkScrollableFrame(self, fg_color="transparent")
        body.grid(row=1, column=0, sticky="nsew", padx=40, pady=(8, 24))
        body.grid_columnconfigure(0, weight=1)

        self._build_live_section(body).grid(row=0, column=0, sticky="ew", pady=(0, 20))
        self._build_mocks_section(body).grid(row=1, column=0, sticky="ew")

    def _build_live_section(self, parent: ctk.CTkBaseClass) -> ctk.CTkFrame:
        section = ctk.CTkFrame(parent, fg_color="transparent")
        ctk.CTkLabel(
            section, text="LIVE", font=theme.heading_font(size=14), text_color=theme.UNSOLD_RED, anchor="w"
        ).pack(anchor="w", pady=(0, 6))

        active_live = persistence_service.detect_active_live_session()
        if active_live is None:
            ctk.CTkLabel(
                section,
                text="No active LIVE auction yet.",
                font=theme.body_font(size=13),
                text_color=theme.TEXT_SECONDARY,
                anchor="w",
            ).pack(anchor="w")
            return section

        card = ctk.CTkFrame(section, fg_color=theme.SURFACE, corner_radius=10)
        card.pack(fill="x")
        if active_live.is_corrupt:
            ctk.CTkLabel(
                card,
                text="LIVE SAVE CORRUPT / UNREADABLE",
                font=theme.body_font(size=13, weight="bold"),
                text_color=theme.UNSOLD_RED,
                anchor="w",
            ).pack(anchor="w", padx=16, pady=(12, 2))
            ctk.CTkLabel(
                card,
                text=active_live.error or "",
                font=theme.body_font(size=11),
                text_color=theme.TEXT_SECONDARY,
                anchor="w",
                wraplength=700,
                justify="left",
            ).pack(anchor="w", padx=16, pady=(0, 8))
            ctk.CTkButton(
                card,
                text="Delete",
                width=90,
                height=30,
                corner_radius=6,
                fg_color=theme.SURFACE_ALT,
                hover_color=theme.BORDER,
                text_color=theme.TEXT_PRIMARY,
                command=lambda: self._on_delete_clicked(active_live),
            ).pack(anchor="e", padx=16, pady=(0, 12))
        else:
            ctk.CTkLabel(
                card,
                text="RESUME LIVE AUCTION",
                font=theme.body_font(size=14, weight="bold"),
                text_color=theme.TEXT_PRIMARY,
                anchor="w",
            ).pack(anchor="w", padx=16, pady=(12, 2))
            ctk.CTkLabel(
                card,
                text=_describe_summary(active_live),
                font=theme.body_font(size=12),
                text_color=theme.TEXT_SECONDARY,
                anchor="w",
            ).pack(anchor="w", padx=16, pady=(0, 12))
            ctk.CTkButton(
                card,
                text="Resume",
                width=100,
                height=32,
                corner_radius=6,
                fg_color=theme.ACCENT_GREEN,
                hover_color=theme.ACCENT_GREEN_HOVER,
                text_color=theme.BACKGROUND,
                command=lambda: self._on_resume_clicked(active_live),
            ).pack(anchor="e", padx=16, pady=(0, 12))
        return section

    def _build_mocks_section(self, parent: ctk.CTkBaseClass) -> ctk.CTkFrame:
        section = ctk.CTkFrame(parent, fg_color="transparent")
        ctk.CTkLabel(
            section, text="MOCKS", font=theme.heading_font(size=14), text_color=theme.TEXT_PRIMARY, anchor="w"
        ).pack(anchor="w", pady=(0, 6))

        mocks = persistence_service.list_mock_sessions()
        if not mocks:
            ctk.CTkLabel(
                section,
                text="No saved mock sessions yet.",
                font=theme.body_font(size=13),
                text_color=theme.TEXT_SECONDARY,
                anchor="w",
            ).pack(anchor="w")
            return section

        for summary in mocks:
            card = ctk.CTkFrame(section, fg_color=theme.SURFACE, corner_radius=10)
            card.pack(fill="x", pady=4)
            card.grid_columnconfigure(0, weight=1)
            if summary.is_corrupt:
                ctk.CTkLabel(
                    card,
                    text=f"{summary.path.name} — SAVE CORRUPT / UNREADABLE",
                    font=theme.body_font(size=13, weight="bold"),
                    text_color=theme.UNSOLD_RED,
                    anchor="w",
                ).grid(row=0, column=0, sticky="w", padx=16, pady=12)
                ctk.CTkButton(
                    card,
                    text="Delete",
                    width=90,
                    height=30,
                    corner_radius=6,
                    fg_color=theme.SURFACE_ALT,
                    hover_color=theme.BORDER,
                    text_color=theme.TEXT_PRIMARY,
                    command=lambda s=summary: self._on_delete_clicked(s),
                ).grid(row=0, column=1, padx=16, pady=12)
            else:
                ctk.CTkLabel(
                    card,
                    text=summary.name or summary.session_id,
                    font=theme.body_font(size=14, weight="bold"),
                    text_color=theme.TEXT_PRIMARY,
                    anchor="w",
                ).grid(row=0, column=0, sticky="w", padx=16, pady=(12, 2))
                ctk.CTkLabel(
                    card,
                    text=_describe_summary_details(summary),
                    font=theme.body_font(size=12),
                    text_color=theme.TEXT_SECONDARY,
                    anchor="w",
                ).grid(row=1, column=0, sticky="w", padx=16, pady=(0, 12))
                btns = ctk.CTkFrame(card, fg_color="transparent")
                btns.grid(row=0, column=1, rowspan=2, padx=16, pady=12)
                ctk.CTkButton(
                    btns,
                    text="Resume",
                    width=90,
                    height=30,
                    corner_radius=6,
                    fg_color=theme.ACCENT_GREEN,
                    hover_color=theme.ACCENT_GREEN_HOVER,
                    text_color=theme.BACKGROUND,
                    command=lambda s=summary: self._on_resume_clicked(s),
                ).pack(side="left", padx=4)
                ctk.CTkButton(
                    btns,
                    text="Delete",
                    width=90,
                    height=30,
                    corner_radius=6,
                    fg_color=theme.SURFACE_ALT,
                    hover_color=theme.BORDER,
                    text_color=theme.TEXT_PRIMARY,
                    command=lambda s=summary: self._on_delete_clicked(s),
                ).pack(side="left", padx=4)
        return section

    # -- session-launcher actions ---------------------------------------

    def _on_start_auction_clicked(self) -> None:
        """Legacy-named entry point for starting a new MOCK session
        unseeded — kept as a stable seam (exercised directly by many
        existing tests) equivalent to a real click on "NEW MOCK AUCTION"."""
        self._start_new_session(SessionMode.MOCK)

    def _on_new_mock_clicked(self) -> None:
        self._start_new_session(SessionMode.MOCK)

    def _on_new_live_clicked(self) -> None:
        active_live = persistence_service.detect_active_live_session()
        if active_live is not None:
            self._open_new_live_confirmation(active_live)
        else:
            self._start_new_session(SessionMode.LIVE)

    def _start_new_session(self, mode: SessionMode) -> None:
        try:
            self._session.start(mode=mode)
        except (OSError, json.JSONDecodeError, ValueError, KeyError, TypeError, AuctionTransactionError) as exc:
            self._launcher_error = str(exc)
            self._render()
            return
        self._reset_ui_state()
        self._render()

    def _open_new_live_confirmation(self, active_live: SavedSessionSummary) -> None:
        dialog, content = self._build_confirmation_dialog("Start New Live Auction")

        ctk.CTkLabel(
            content,
            text="An active LIVE auction already exists",
            font=theme.heading_font(size=16),
            text_color=theme.UNSOLD_RED,
        ).pack(pady=(0, 10))
        detail = (
            "(existing save is corrupt/unreadable)"
            if active_live.is_corrupt
            else _describe_summary(active_live)
        )
        ctk.CTkLabel(
            content, text=detail, font=theme.body_font(size=12), text_color=theme.TEXT_SECONDARY,
            wraplength=380, justify="center",
        ).pack(pady=(0, 10))
        ctk.CTkLabel(
            content,
            text="Starting a new LIVE auction will archive this one — it will not be deleted.",
            font=theme.body_font(size=12),
            text_color=theme.TEXT_PRIMARY,
            wraplength=380,
            justify="center",
        ).pack(pady=(0, 14))

        self._build_dialog_button_row(
            content,
            confirm_color=theme.UNSOLD_RED,
            confirm_command=lambda: self._confirm_new_live(dialog),
            cancel_command=dialog.destroy,
        ).pack()
        self._finalize_dialog(dialog)

    def _confirm_new_live(self, dialog: ctk.CTkToplevel) -> None:
        dialog.destroy()
        try:
            persistence_service.archive_live_session()
        except PersistenceError as exc:
            self._launcher_error = f"Could not archive the existing live session: {exc}"
            self._render()
            return
        self._start_new_session(SessionMode.LIVE)

    def _on_resume_clicked(self, summary: SavedSessionSummary) -> None:
        try:
            restored = persistence_service.load_session(summary.path)
        except PersistenceError as exc:
            self._launcher_error = f"Could not resume this session: {exc}"
            self._render()
            return
        self._session.adopt(restored)
        self._reset_ui_state()
        self._render()

    def _on_delete_clicked(self, summary: SavedSessionSummary) -> None:
        dialog, content = self._build_confirmation_dialog("Delete Saved Session")

        ctk.CTkLabel(
            content, text="Delete this saved session?", font=theme.heading_font(size=16), text_color=theme.TEXT_PRIMARY
        ).pack(pady=(0, 10))
        ctk.CTkLabel(
            content,
            text=summary.name or summary.session_id or summary.path.name,
            font=theme.body_font(size=13, weight="bold"),
            text_color=theme.TEXT_SECONDARY,
        ).pack(pady=(0, 14))

        self._build_dialog_button_row(
            content,
            confirm_color=theme.UNSOLD_RED,
            confirm_command=lambda: self._confirm_delete(summary, dialog),
            cancel_command=dialog.destroy,
        ).pack()
        self._finalize_dialog(dialog)

    def _confirm_delete(self, summary: SavedSessionSummary, dialog: ctk.CTkToplevel) -> None:
        dialog.destroy()
        try:
            persistence_service.delete_save_file(summary.path)
        except PersistenceError as exc:
            self._launcher_error = f"Could not delete this session: {exc}"
        else:
            self._launcher_error = None
        self._render()

    def _reset_ui_state(self) -> None:
        self._selected_team_id = None
        self._price_text = ""
        self._error_message = None
        self._launcher_error = None

    # ------------------------------------------------------------------
    # In-progress state
    # ------------------------------------------------------------------

    def _build_in_progress_view(self) -> None:
        session = self._session
        current_player = session.current_player
        if current_player is None:
            # Defensive: should not happen once started and not complete,
            # but never let an inconsistent session crash the screen.
            self._build_error_state("No current player is available.")
            return

        # If the previously selected team is no longer eligible for the
        # current player (e.g. it filled up or already has a GK), clear
        # the selection rather than silently keeping an invalid one.
        if self._selected_team_id is not None:
            selected_team = next((t for t in session.teams if t.id == self._selected_team_id), None)
            if selected_team is None or not team_can_bid_for_player(selected_team, current_player, session.players):
                self._selected_team_id = None

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)

        # A CTkScrollableFrame, not a plain CTkFrame: Captain Phone Bidding
        # (September 2026) added the CURRENT BID/LEADING TEAM row and the
        # desktop bid-buttons row on top of the already-tall premium
        # player card, and a real screenshot check at the app's own
        # default 1366x768 size showed the team-status/controls/SOLD/
        # UNSOLD rows clipped completely off the bottom with no way to
        # reach them — scrolling guarantees every control stays reachable
        # regardless of window size/DPI scaling/future additions, which
        # matters more here than a strictly fixed layout (SOLD/UNSOLD are
        # the two controls this app can least afford to hide).
        body = ctk.CTkScrollableFrame(self, fg_color="transparent")
        body.grid(row=0, column=0, sticky="nsew", padx=28, pady=(16, 16))
        body.grid_columnconfigure(0, weight=1)
        self._in_progress_body = body  # for tests/manual verification (scroll position)

        next_row = 0
        self._build_progress_row(body, session).grid(row=next_row, column=0, sticky="ew", pady=(0, 10))
        next_row += 1

        if session.last_result is not None:
            self._build_feedback_banner(body, session.last_result).grid(
                row=next_row, column=0, sticky="ew", pady=(0, 10)
            )
            next_row += 1

        if self._error_message:
            self._build_error_banner(body, self._error_message).grid(
                row=next_row, column=0, sticky="ew", pady=(0, 10)
            )
            next_row += 1

        # No sticky="ew": the card is now a fixed-size premium tile (see
        # _build_player_card), not a full-width stretched band — leaving
        # sticky unset centers it within the row, matching body's own
        # weighted column.
        self._build_player_card(body, current_player).grid(row=next_row, column=0, pady=(0, 14))
        next_row += 1

        # Captain Phone Bidding (Phase 1): the single authoritative live
        # bid/leader — updated by either a phone or the desktop bid
        # buttons below, both via AuctionSession.place_live_bid. Placed
        # prominently right under the player card, per the ticket's
        # "highly visible on the projector" requirement.
        self._build_current_bid_row(body).grid(row=next_row, column=0, sticky="ew", pady=(0, 14))
        next_row += 1

        self._build_team_status_row(body, session.teams, current_player, session.players).grid(
            row=next_row, column=0, sticky="ew", pady=(0, 14)
        )
        next_row += 1

        self._build_desktop_bid_row(body).grid(row=next_row, column=0, sticky="ew", pady=(0, 14))
        next_row += 1

        self._build_controls_row(body).grid(row=next_row, column=0, sticky="ew", pady=(0, 14))
        next_row += 1

        self._build_action_row(body).grid(row=next_row, column=0, sticky="ew")

    def _build_current_bid_row(self, parent: ctk.CTkBaseClass) -> ctk.CTkFrame:
        """CURRENT BID / LEADING TEAM — the one authoritative live-bid
        display, fed by `AuctionSession.current_bid`/`leading_team` (see
        services/live_bid_service.py). `—` before any bid, per the
        ticket's explicit "do not pretend a 0M bid exists" rule."""
        row = ctk.CTkFrame(parent, fg_color=theme.SURFACE, corner_radius=12)
        row.grid_columnconfigure((0, 1), weight=1)

        current_bid = self._session.auction.current_bid if self._session.auction is not None else None
        leading_team = self._session.leading_team

        bid_block = ctk.CTkFrame(row, fg_color="transparent")
        bid_block.grid(row=0, column=0, pady=16)
        ctk.CTkLabel(
            bid_block, text="CURRENT BID", font=theme.body_font(size=12, weight="bold"), text_color=theme.TEXT_SECONDARY
        ).pack()
        ctk.CTkLabel(
            bid_block,
            text=f"{current_bid}M" if current_bid is not None else "—",
            font=theme.heading_font(size=34),
            text_color=theme.GOLD_ACCENT,
        ).pack()

        team_block = ctk.CTkFrame(row, fg_color="transparent")
        team_block.grid(row=0, column=1, pady=16)
        ctk.CTkLabel(
            team_block, text="LEADING TEAM", font=theme.body_font(size=12, weight="bold"), text_color=theme.TEXT_SECONDARY
        ).pack()
        ctk.CTkLabel(
            team_block,
            text=leading_team.name if leading_team is not None else "—",
            font=theme.heading_font(size=24),
            text_color=theme.ACCENT_GREEN,
        ).pack()

        return row

    def _build_desktop_bid_row(self, parent: ctk.CTkBaseClass) -> ctk.CTkFrame:
        """The organizer's own bidding controls — for today's one-phone
        trial, connectivity failure, or plain verbal-bid fallback (see
        PROJECT_CONTEXT.md's "Captain Phone Bidding — Phase 1"). Bids for
        whichever team is currently selected in the team-status row above
        (the same selection already used for the manual SOLD flow) and
        calls the exact same `AuctionSession.place_live_bid` a phone
        would — never a separate mechanism."""
        row = ctk.CTkFrame(parent, fg_color="transparent")
        selected_team = next((t for t in self._session.teams if t.id == self._selected_team_id), None)

        label_text = f"PLACE BID — {selected_team.name.upper()}" if selected_team else "SELECT A TEAM ABOVE TO BID"
        ctk.CTkLabel(
            row, text=label_text, font=theme.body_font(size=12, weight="bold"), text_color=theme.TEXT_SECONDARY
        ).pack(side="left", padx=(0, 14))

        for amount in QUICK_PRICE_INCREMENTS:
            ctk.CTkButton(
                row,
                text=f"+{amount}M",
                width=64,
                height=32,
                corner_radius=6,
                fg_color=theme.ACCENT_GREEN,
                hover_color=theme.ACCENT_GREEN_HOVER,
                text_color=theme.BACKGROUND,
                font=theme.body_font(size=13, weight="bold"),
                state="normal" if selected_team is not None else "disabled",
                command=lambda amount=amount: self._on_desktop_bid_clicked(amount),
            ).pack(side="left", padx=4)

        return row

    def _on_desktop_bid_clicked(self, increment: int) -> None:
        if self._selected_team_id is None:
            return
        base = self._session.auction.current_bid or 0
        result = self._session.place_live_bid(self._selected_team_id, base + increment)
        self._error_message = None if result.accepted else result.reason
        self._render()

    def _build_progress_row(self, parent: ctk.CTkBaseClass, session: AuctionSession) -> ctk.CTkFrame:
        row = ctk.CTkFrame(parent, fg_color="transparent")
        round_label = "Round 1" if session.round_number == 1 else f"Re-Auction Round {session.round_number}"
        position_number = session.resolved_count + 1
        ctk.CTkLabel(
            row,
            text=f"{round_label}  ·  Player {position_number} of {session.total_queue_length}",
            font=theme.heading_font(size=16),
            text_color=theme.TEXT_PRIMARY,
            anchor="w",
        ).pack(side="left")
        ctk.CTkLabel(
            row,
            text=f"    {session.resolved_count} resolved this round    ·    {session.remaining_count} remaining this round",
            font=theme.body_font(size=13),
            text_color=theme.TEXT_SECONDARY,
            anchor="w",
        ).pack(side="left")
        self._build_session_status_badges(row).pack(side="right")
        return row

    def _build_session_status_badges(self, parent: ctk.CTkBaseClass) -> ctk.CTkFrame:
        """Compact MOCK/LIVE + save-status indicator, shared by the
        in-progress/complete/blocked views. LIVE is styled unmistakably
        (red pill) per Milestone 9's UI requirement; MOCK is deliberately
        muted so it never competes visually with LIVE."""
        row = ctk.CTkFrame(parent, fg_color="transparent")
        session = self._session

        if session.mode == SessionMode.LIVE:
            pill_badge(row, "LIVE SESSION", fg_color=theme.UNSOLD_RED, text_color=theme.TEXT_PRIMARY, width=118).pack(
                side="left", padx=(0, 10)
            )
        else:
            pill_badge(
                row, "MOCK SESSION", fg_color=theme.SURFACE_ALT, text_color=theme.TEXT_SECONDARY, width=118
            ).pack(side="left", padx=(0, 10))

        if session.last_save_error:
            status_text, status_color = "SAVE FAILED", theme.UNSOLD_RED
        elif session.updated_at:
            status_text, status_color = f"Autosaved {_format_timestamp(session.updated_at)}", theme.TEXT_SECONDARY
        else:
            status_text, status_color = "Not yet saved", theme.TEXT_SECONDARY
        ctk.CTkLabel(
            row, text=status_text, font=theme.body_font(size=12, weight="bold"), text_color=status_color
        ).pack(side="left")

        server = getattr(self.winfo_toplevel(), "captain_bidding_server", None)
        if server is not None and server.is_running:
            bidding_text = f"CAPTAIN BIDDING: RUNNING  ({server.lan_url.removeprefix('http://')})"
            bidding_color = theme.ACCENT_GREEN
        else:
            bidding_text = "CAPTAIN BIDDING: OFF"
            bidding_color = theme.TEXT_SECONDARY
        ctk.CTkLabel(
            row, text=bidding_text, font=theme.body_font(size=11, weight="bold"), text_color=bidding_color
        ).pack(side="left", padx=(14, 0))
        return row

    def _build_feedback_banner(self, parent: ctk.CTkBaseClass, result: TransactionResult) -> ctk.CTkFrame:
        if result.outcome == "SOLD":
            color = theme.SOLD_GREEN
            text = f"SOLD  ·  {result.player.full_name} → {result.team.name} for {result.sale_price}M"
        else:
            color = theme.UNSOLD_RED
            text = f"UNSOLD  ·  {result.player.full_name}"
        banner = ctk.CTkFrame(parent, fg_color=color, corner_radius=8)
        ctk.CTkLabel(
            banner, text=text, font=theme.body_font(size=14, weight="bold"), text_color=theme.BACKGROUND, anchor="w"
        ).pack(padx=16, pady=10, anchor="w")
        return banner

    def _build_error_banner(self, parent: ctk.CTkBaseClass, message: str) -> ctk.CTkFrame:
        banner = ctk.CTkFrame(parent, fg_color=theme.UNSOLD_RED, corner_radius=8)
        ctk.CTkLabel(
            banner,
            text=message,
            font=theme.body_font(size=13, weight="bold"),
            text_color=theme.BACKGROUND,
            anchor="w",
            justify="left",
            wraplength=900,
        ).pack(padx=16, pady=10, anchor="w")
        return banner

    def _build_player_card(self, parent: ctk.CTkBaseClass, player: Player) -> ctk.CTkFrame:
        """The current-player reveal, built from the same premium
        Calgary-Vipers card-art system as the Player Cards gallery (see
        ui/player_card_art.py) — a larger, simpler "Live Auction" variant
        of one shared visual design: real photo, full name, position
        badge, OVR, and last-season FPL only. Deliberately nothing else
        (no short name, no captain badge, no auction history) per the
        project's "essential auction information only" rule — bidding
        information (team buttons, price entry, SOLD/UNSOLD) stays in
        separate rows below this card, untouched by this method.

        Sized via `_choose_card_geometry` (STANDARD at the app's default
        1366x768/1920x1080, a smaller COMPACT variant at the 1024x640
        minimum) so the card never crowds out the controls beneath it.
        """
        geometry = _choose_card_geometry(self.winfo_toplevel().winfo_width())
        card_width, card_height = geometry["card_size"]
        photo_size = geometry["photo_size"]
        photo_offset = geometry["photo_offset"]

        variant = player_card_art.card_variant(player)
        border_color = player_card_art.VARIANT_COLORS[variant][0]

        card = ctk.CTkFrame(
            parent,
            fg_color=theme.SURFACE,
            corner_radius=20,
            width=card_width,
            height=card_height,
            border_width=2,
            border_color=border_color,
        )
        card.grid_propagate(False)
        card.grid_columnconfigure(0, weight=1)

        art_image, has_photo = player_card_art.build_player_card_image(
            player, background_size=(card_width, card_height), photo_size=photo_size, photo_offset=photo_offset
        )
        art_ctk_image = ctk.CTkImage(light_image=art_image, dark_image=art_image, size=(card_width, card_height))
        # A single reference per attribute (not a growing list), replaced
        # outright on every call — the previous player's images become
        # eligible for GC the instant these are reassigned, so repeatedly
        # advancing through 28 reveals never accumulates stale CTkImages.
        self._current_card_art_image = art_ctk_image
        self._current_photo_image = art_ctk_image if has_photo else None

        ctk.CTkLabel(card, image=art_ctk_image, text="").place(x=0, y=0)

        if not has_photo:
            # width/height on the widget itself, not .place() — the
            # placeholder panel's rounded frame is already baked into the
            # art image; this just centers the initials text over it.
            ctk.CTkLabel(
                card,
                text=player.short_name[:2].upper(),
                font=theme.heading_font(size=geometry["ovr_font_size"]),
                text_color=theme.TEXT_SECONDARY,
                fg_color="transparent",
                width=photo_size[0],
                height=photo_size[1],
            ).place(x=photo_offset[0], y=photo_offset[1])

        ctk.CTkLabel(
            card,
            text=player.full_name.upper(),
            font=theme.heading_font(size=geometry["name_font_size"]),
            text_color=theme.TEXT_PRIMARY,
            wraplength=card_width - 24,
            justify="center",
            fg_color="transparent",
        ).place(relx=0.5, y=geometry["name_y"], anchor="n")

        badge_row = ctk.CTkFrame(card, fg_color="transparent")
        position_badge(badge_row, player.position, self._position_colors).pack(side="left", padx=(0, 14))
        ovr_block = ctk.CTkFrame(badge_row, fg_color="transparent")
        ovr_block.pack(side="left")
        ctk.CTkLabel(
            ovr_block,
            text=str(player.overall_rating),
            font=theme.heading_font(size=geometry["ovr_font_size"]),
            text_color=theme.GOLD_ACCENT,
        ).pack(side="left")
        ctk.CTkLabel(
            ovr_block,
            text=" OVR",
            font=theme.body_font(size=12, weight="bold"),
            text_color=theme.TEXT_SECONDARY,
        ).pack(side="left", pady=(int(geometry["ovr_font_size"] * 0.34), 0))
        badge_row.place(relx=0.5, y=geometry["badge_y"], anchor="n")

        ctk.CTkLabel(
            card,
            text=f"FPL {player_card_art.fpl_display(player.last_season_fpl_points)}",
            font=theme.body_font(size=geometry["fpl_font_size"], weight="bold"),
            text_color=theme.GOLD_ACCENT,
            fg_color="transparent",
        ).place(relx=0.5, y=geometry["fpl_y"], anchor="n")

        return card

    def _build_team_status_row(
        self,
        parent: ctk.CTkBaseClass,
        teams: list[Team],
        current_player: Player,
        players: list[Player],
    ) -> ctk.CTkFrame:
        row = ctk.CTkFrame(parent, fg_color="transparent")
        showing_gk = current_player.position == Position.GK
        for index, team in enumerate(teams):
            row.grid_columnconfigure(index, weight=1)
            selected = self._selected_team_id == team.id
            eligible = team_can_bid_for_player(team, current_player, players)
            remaining_slots = max(team.max_squad_size - team.roster_size, 0)

            lines = [
                team.name,
                f"{team.remaining_budget}M",
                f"{team.roster_size} / {team.max_squad_size}",
                f"{remaining_slots} slots",
                f"Max {max(team.maximum_legal_bid, 0)}M",
            ]
            if showing_gk:
                lines.append("GK 1/1" if team_has_goalkeeper(team, players) else "GK 0/1")
            if not eligible:
                if remaining_slots == 0:
                    reason = "FULL"
                elif current_player.position == Position.GK and team_has_goalkeeper(team, players):
                    reason = "HAS GK"
                else:
                    # Pre-Milestone-9 budget reserve rule: not enough
                    # budget left to buy anything without making the
                    # remaining roster slots impossible to fill later.
                    reason = "LOW BUDGET"
                lines.append(reason)

            if eligible:
                fg_color = theme.ACCENT_GREEN if selected else theme.SURFACE
                text_color = theme.BACKGROUND if selected else theme.TEXT_PRIMARY
                hover_color = theme.ACCENT_GREEN_HOVER if selected else theme.SURFACE_ALT
                border_color = theme.ACCENT_GREEN if selected else theme.BORDER
                state = "normal"
                command = lambda team_id=team.id: self._on_team_selected(team_id)
            else:
                fg_color = theme.SURFACE_ALT
                text_color = theme.TEXT_SECONDARY
                hover_color = theme.SURFACE_ALT
                border_color = theme.BORDER
                state = "disabled"
                command = None

            button = ctk.CTkButton(
                row,
                text="\n".join(lines),
                font=theme.body_font(size=13, weight="bold"),
                fg_color=fg_color,
                text_color=text_color,
                hover_color=hover_color,
                border_width=2,
                border_color=border_color,
                corner_radius=10,
                height=44 + 12 * len(lines),
                state=state,
                command=command,
            )
            button.grid(row=0, column=index, sticky="nsew", padx=6)
        return row

    def _on_team_selected(self, team_id: int) -> None:
        self._selected_team_id = team_id
        self._error_message = None
        self._render()

    def _build_controls_row(self, parent: ctk.CTkBaseClass) -> ctk.CTkFrame:
        row = ctk.CTkFrame(parent, fg_color="transparent")

        # A fresh StringVar per render, seeded with the last known text —
        # see the note in __init__ on why this isn't reused across renders.
        self._price_var = ctk.StringVar(value=self._price_text)
        self._price_var.trace_add("write", lambda *_args: self._on_price_var_changed())

        ctk.CTkLabel(
            row,
            text="FINAL PRICE (M)",
            font=theme.body_font(size=12, weight="bold"),
            text_color=theme.TEXT_SECONDARY,
        ).pack(side="left", padx=(0, 10))
        ctk.CTkEntry(
            row,
            textvariable=self._price_var,
            width=100,
            fg_color=theme.SURFACE,
            border_color=theme.BORDER,
            text_color=theme.TEXT_PRIMARY,
            placeholder_text="e.g. 10",
        ).pack(side="left", padx=(0, 10))

        for amount in QUICK_PRICE_INCREMENTS:
            ctk.CTkButton(
                row,
                text=f"+{amount}M",
                width=54,
                height=28,
                corner_radius=6,
                fg_color=theme.SURFACE_ALT,
                hover_color=theme.BORDER,
                text_color=theme.TEXT_PRIMARY,
                font=theme.body_font(size=12, weight="bold"),
                command=lambda amount=amount: self._bump_price(amount),
            ).pack(side="left", padx=4)

        return row

    def _on_price_var_changed(self) -> None:
        self._price_text = self._price_var.get()

    def _bump_price(self, amount: int) -> None:
        text = self._price_var.get().strip()
        try:
            current_value = int(text) if text else 0
        except ValueError:
            current_value = 0
        self._price_var.set(str(current_value + amount))

    def _build_action_row(self, parent: ctk.CTkBaseClass) -> ctk.CTkFrame:
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.grid_columnconfigure((0, 1), weight=1)

        ctk.CTkButton(
            row,
            text="SOLD",
            font=theme.heading_font(size=18),
            fg_color=theme.SOLD_GREEN,
            hover_color=theme.ACCENT_GREEN_HOVER,
            text_color=theme.BACKGROUND,
            height=52,
            corner_radius=10,
            command=self._on_sold_clicked,
        ).grid(row=0, column=0, sticky="ew", padx=(0, 8))

        ctk.CTkButton(
            row,
            text="UNSOLD",
            font=theme.heading_font(size=18),
            fg_color=theme.UNSOLD_RED,
            hover_color="#c93f43",
            text_color=theme.TEXT_PRIMARY,
            height=52,
            corner_radius=10,
            command=self._on_unsold_clicked,
        ).grid(row=0, column=1, sticky="ew", padx=(8, 0))

        return row

    # ------------------------------------------------------------------
    # SOLD flow: input collection -> confirmation dialog -> execution
    # ------------------------------------------------------------------

    def _read_price(self) -> int | None:
        text = self._price_var.get().strip()
        if not text:
            return None
        try:
            value = int(text)
        except ValueError:
            return None
        if value <= 0:
            return None
        return value

    def _on_sold_clicked(self) -> None:
        # Captain Phone Bidding (Phase 1): a valid live bid (from a phone
        # or the desktop's own bid buttons — both go through the same
        # AuctionSession.place_live_bid) takes priority over the manual
        # team-select/price-entry controls, per the ticket's "SOLD should
        # automatically use the leading team and current bid" requirement.
        # The manual flow below remains the fallback whenever no live bid
        # exists yet (verbal bidding, phone/Wi-Fi unavailable, or the
        # organizer simply prefers typing a price).
        leading_team = self._session.leading_team
        live_bid = self._session.auction.current_bid if self._session.auction is not None else None

        if leading_team is not None and live_bid is not None:
            team = leading_team
            price = live_bid
        else:
            if self._selected_team_id is None:
                self._error_message = "Select a winning team before marking SOLD."
                self._render()
                return

            price = self._read_price()
            if price is None:
                self._error_message = "Enter a valid whole-number sale price before marking SOLD."
                self._render()
                return

            team = next((t for t in self._session.teams if t.id == self._selected_team_id), None)
            if team is None:
                self._error_message = "Selected team could not be found."
                self._render()
                return

        # Pre-Milestone-9 budget reserve rule: reject before even opening
        # the confirmation dialog rather than silently clamping the price
        # or letting the organizer confirm a transaction the service layer
        # will reject anyway — see PROJECT_CONTEXT.md's "PRE-M9 BUDGET
        # RESERVE RULE". process_sale enforces this independently too, so
        # this is purely an earlier, friendlier surfacing of the same rule.
        if price > team.maximum_legal_bid:
            self._error_message = budget_reserve_violation_message(team)
            self._render()
            return

        # Settings' "Confirm SOLD transactions" preference (default ON)
        # controls only whether this dialog appears — every validation
        # above and `_execute_sale` itself (the exact same call the
        # dialog's Confirm button makes) run identically either way.
        if preferences_service.load_preferences().confirm_sold:
            self._open_sale_confirmation(self._session.current_player, team, price)
        else:
            self._execute_sale(team.id, price)

    def _build_confirmation_dialog(self, title: str) -> tuple[ctk.CTkToplevel, ctk.CTkFrame]:
        """A Toplevel confirmation dialog shell, centered over the main
        window and sized to fit whatever gets packed into the returned
        content frame — never a fixed guessed size, so nothing can be
        clipped regardless of font size or Windows display scaling. Call
        `_finalize_dialog(dialog)` once all content has been added.

        Cancel is the caller's job to wire up; the window's own close (X)
        button is bound here to `dialog.destroy` so it is always a safe,
        no-transaction close.
        """
        dialog = ctk.CTkToplevel(self)
        dialog.title(title)
        dialog.configure(fg_color=theme.BACKGROUND)
        dialog.transient(self.winfo_toplevel())
        dialog.protocol("WM_DELETE_WINDOW", dialog.destroy)

        content = ctk.CTkFrame(dialog, fg_color="transparent")
        content.pack(fill="both", expand=True, padx=28, pady=24)
        return dialog, content

    def _finalize_dialog(self, dialog: ctk.CTkToplevel, min_width: int = 320) -> None:
        """Size the dialog to fit its packed content, then center it over
        the main window. Must be called only after all content has been
        added — sizing is derived from the actual rendered content, not a
        fixed pixel guess, so it stays correct at any DPI/font scaling."""
        dialog.update_idletasks()
        width = max(dialog.winfo_reqwidth(), min_width)
        height = dialog.winfo_reqheight()

        root = self.winfo_toplevel()
        x = root.winfo_rootx() + (root.winfo_width() - width) // 2
        y = root.winfo_rooty() + (root.winfo_height() - height) // 2
        dialog.geometry(f"{width}x{height}+{max(x, 0)}+{max(y, 0)}")
        dialog.grab_set()

    def _build_dialog_button_row(self, parent: ctk.CTkBaseClass, confirm_color: str, confirm_command, cancel_command) -> ctk.CTkFrame:
        button_row = ctk.CTkFrame(parent, fg_color="transparent")
        ctk.CTkButton(
            button_row,
            text="Confirm",
            width=112,
            height=36,
            corner_radius=8,
            fg_color=confirm_color,
            text_color=theme.BACKGROUND if confirm_color == theme.SOLD_GREEN else theme.TEXT_PRIMARY,
            command=confirm_command,
        ).pack(side="left", padx=8)
        ctk.CTkButton(
            button_row,
            text="Cancel",
            width=112,
            height=36,
            corner_radius=8,
            fg_color=theme.SURFACE_ALT,
            hover_color=theme.BORDER,
            text_color=theme.TEXT_PRIMARY,
            command=cancel_command,
        ).pack(side="left", padx=8)
        return button_row

    def _open_sale_confirmation(self, player: Player, team: Team, price: int) -> None:
        dialog, content = self._build_confirmation_dialog("Confirm Sale")

        ctk.CTkLabel(
            content, text="Confirm Sale", font=theme.heading_font(size=18), text_color=theme.TEXT_PRIMARY
        ).pack(pady=(0, 14))
        for label, value in [("Player", player.full_name), ("Team", team.name), ("Price", f"{price}M")]:
            ctk.CTkLabel(
                content, text=label, font=theme.body_font(size=12), text_color=theme.TEXT_SECONDARY
            ).pack()
            ctk.CTkLabel(
                content, text=value, font=theme.body_font(size=15, weight="bold"), text_color=theme.TEXT_PRIMARY
            ).pack(pady=(0, 10))

        self._build_dialog_button_row(
            content,
            confirm_color=theme.SOLD_GREEN,
            confirm_command=lambda: self._confirm_sale(team.id, price, dialog),
            cancel_command=dialog.destroy,
        ).pack(pady=(10, 0))

        self._finalize_dialog(dialog)

    def _confirm_sale(self, team_id: int, price: int, dialog: ctk.CTkToplevel) -> None:
        dialog.destroy()
        self._execute_sale(team_id, price)

    def _execute_sale(self, team_id: int, price: int) -> None:
        """Call the session's process_sale and refresh. Public-ish seam so
        tests can exercise the transaction outcome without opening a real
        confirmation dialog."""
        try:
            self._session.sell_current_player(winning_team=team_id, sale_price=price)
        except AuctionTransactionError as exc:
            self._error_message = str(exc)
            self._render()
            return
        self._selected_team_id = None
        self._price_text = ""
        self._error_message = None
        self._render()

    # ------------------------------------------------------------------
    # UNSOLD flow: confirmation dialog -> execution
    # ------------------------------------------------------------------

    def _on_unsold_clicked(self) -> None:
        # Settings' "Confirm UNSOLD transactions" preference (default ON)
        # controls only whether this dialog appears — `_execute_unsold` is
        # the exact same call the dialog's Confirm button makes either way.
        if preferences_service.load_preferences().confirm_unsold:
            self._open_unsold_confirmation(self._session.current_player)
        else:
            self._execute_unsold()

    def _open_unsold_confirmation(self, player: Player) -> None:
        dialog, content = self._build_confirmation_dialog("Confirm UNSOLD")

        ctk.CTkLabel(
            content, text="Confirm UNSOLD", font=theme.heading_font(size=18), text_color=theme.TEXT_PRIMARY
        ).pack(pady=(0, 14))
        ctk.CTkLabel(
            content, text=player.full_name, font=theme.body_font(size=15, weight="bold"), text_color=theme.TEXT_PRIMARY
        ).pack(pady=(0, 16))

        self._build_dialog_button_row(
            content,
            confirm_color=theme.UNSOLD_RED,
            confirm_command=lambda: self._confirm_unsold(dialog),
            cancel_command=dialog.destroy,
        ).pack(pady=(10, 0))

        self._finalize_dialog(dialog)

    def _confirm_unsold(self, dialog: ctk.CTkToplevel) -> None:
        dialog.destroy()
        self._execute_unsold()

    def _execute_unsold(self) -> None:
        """Call the session's process_unsold and refresh. Public-ish seam
        so tests can exercise the transaction outcome without opening a
        real confirmation dialog."""
        try:
            self._session.mark_current_player_unsold()
        except AuctionTransactionError as exc:
            self._error_message = str(exc)
            self._render()
            return
        self._selected_team_id = None
        self._price_text = ""
        self._error_message = None
        self._render()

    # ------------------------------------------------------------------
    # Complete state
    # ------------------------------------------------------------------

    def _build_complete_view(self) -> None:
        session = self._session
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)

        container = ctk.CTkFrame(self, fg_color="transparent")
        container.grid(row=0, column=0)

        self._build_session_status_badges(container).pack(pady=(0, 10))

        ctk.CTkLabel(
            container, text="AUCTION COMPLETE", font=theme.heading_font(size=30), text_color=theme.ACCENT_GREEN
        ).pack(pady=(0, 8))
        ctk.CTkLabel(
            container,
            text=f"{session.sold_count} / {session.total_eligible_count} players sold",
            font=theme.body_font(size=16),
            text_color=theme.TEXT_PRIMARY,
        ).pack(pady=(0, 20))

        # "Sold" is always the full eligible count here (that's what
        # completion means); "Rounds" shows how many re-auction passes it
        # took — more informative than an always-zero "Unsold" count.
        counts_row = ctk.CTkFrame(container, fg_color="transparent")
        counts_row.pack(pady=(0, 24))
        for label, value in [("Sold", session.sold_count), ("Rounds", session.round_number)]:
            _build_stat_card(counts_row, label, value).pack(side="left", padx=10)

        ctk.CTkLabel(
            container,
            text="Final Team Budgets",
            font=theme.body_font(size=13, weight="bold"),
            text_color=theme.TEXT_SECONDARY,
        ).pack(pady=(0, 8))
        for team in session.teams:
            ctk.CTkLabel(
                container,
                text=f"{team.name} — {team.remaining_budget}M",
                font=theme.body_font(size=14),
                text_color=theme.TEXT_PRIMARY,
            ).pack(pady=2)

    # ------------------------------------------------------------------
    # Blocked state
    # ------------------------------------------------------------------

    def _build_blocked_view(self) -> None:
        session = self._session
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)

        container = ctk.CTkFrame(self, fg_color="transparent")
        container.grid(row=0, column=0)

        self._build_session_status_badges(container).pack(pady=(0, 10))

        ctk.CTkLabel(
            container, text="AUCTION BLOCKED", font=theme.heading_font(size=28), text_color=theme.UNSOLD_RED
        ).pack(pady=(0, 10))
        ctk.CTkLabel(
            container,
            text="Re-auction cannot continue: no eligible team can purchase the remaining player(s).",
            font=theme.body_font(size=14),
            text_color=theme.TEXT_PRIMARY,
            wraplength=760,
            justify="center",
        ).pack(pady=(0, 20))
        ctk.CTkLabel(
            container,
            text=f"{session.sold_count} / {session.total_eligible_count} players sold before the auction stalled",
            font=theme.body_font(size=13),
            text_color=theme.TEXT_SECONDARY,
        ).pack(pady=(0, 16))
        for team in session.teams:
            ctk.CTkLabel(
                container,
                text=f"{team.name} — {team.remaining_budget}M, {team.roster_size} / {team.max_squad_size}",
                font=theme.body_font(size=14),
                text_color=theme.TEXT_PRIMARY,
            ).pack(pady=2)


def build_live_auction_screen(parent: ctk.CTkBaseClass, session: AuctionSession) -> ctk.CTkFrame:
    return LiveAuctionScreen(parent, session)
