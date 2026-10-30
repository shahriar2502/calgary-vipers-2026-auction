"""Second Auction screen — Milestone 1 (September/October 2026): transfer-
window release-plan setup (release selection, original-purchase-price
refund preview, and the 16-player release pool); Milestone 2 (October
2026): the second-auction bidding engine itself (initialize, live
SOLD/UNSOLD, completion, blocked detection); and Milestone 3 (October
2026): captain phone bidding for the second auction, a permanent
CAPTAIN CONNECTIONS panel, and premium visual polish for the LIVE view.

GUI only. Every Milestone 1 rule still lives in
`services/second_auction_service.py` / `AuctionSession`'s own
`toggle_second_auction_release`/`confirm_second_auction_release_plan`/
`unlock_second_auction_release_plan` methods; every Milestone 2 rule
lives in `services/second_auction_bidding_service.py` /
`AuctionSession`'s own `start_second_auction`/`sell_second_auction_player`/
`mark_second_auction_player_unsold` methods; every Milestone 3 live-bid
rule lives in `services/second_auction_live_bid_service.py` /
`AuctionSession.place_second_auction_live_bid` (the exact same entry
point a captain's phone uses — see `services/captain_bidding_server.py`)
— never inline in this file. Nothing here calls `process_sale`/
`process_unsold`/`place_bid`, and nothing here ever mutates a
first-auction `Team`'s roster or budget, or a `Player`'s `sold_price` —
see PROJECT_CONTEXT.md's "SECOND AUCTION — MILESTONE 1/2/3".
"""

from __future__ import annotations

from pathlib import Path

import customtkinter as ctk

from models.auction import AuctionStatus
from models.player import OUTFIELD_BASE_PRICE, Player, Position, player_base_price
from models.second_auction import SecondAuctionStatus
from services import second_auction_bidding_service as sabs
from services import second_auction_service as sas
from services.auction_session_service import AuctionSession
from services.second_auction_bidding_service import SecondAuctionBiddingError, SecondAuctionTransactionResult
from services.second_auction_service import ReleaseRefund, SecondAuctionError, TeamReleasePreview
from ui import theme
from ui.widgets import load_cover_fit_image

QUICK_PRICE_INCREMENTS = (1, 2, 5)
_CARD_PHOTO_SIZE = (160, 160)

# Milestone 3: honest, existing-architecture-only connection labels (see
# services/captain_auth_service.py's own CONNECTED-vs-AUTHENTICATED
# distinction) — never a fabricated live-presence signal. "RECENTLY
# ACTIVE" means the token is still valid but hasn't polled/heartbeated
# within the connected window (e.g. the phone's screen dimmed); "MANUAL /
# OFFLINE" means no captain has ever authenticated for that team this
# session, so the organizer is bidding for them manually.
_CONNECTION_LABEL_COLORS = {
    "CONNECTED": (theme.ACCENT_GREEN, theme.BACKGROUND),
    "RECENTLY ACTIVE": (theme.GOLD_ACCENT, theme.BACKGROUND),
    "MANUAL / OFFLINE": (theme.SURFACE_ALT, theme.TEXT_SECONDARY),
}


def _money(value: int) -> str:
    sign = "+" if value > 0 else ""
    return f"{sign}{value}M"


def _build_section(parent: ctk.CTkBaseClass, title: str) -> ctk.CTkFrame:
    section = ctk.CTkFrame(parent, fg_color=theme.SURFACE, corner_radius=10)
    section.grid_columnconfigure(0, weight=1)
    ctk.CTkLabel(
        section, text=title.upper(), font=theme.heading_font(size=15), text_color=theme.TEXT_PRIMARY, anchor="w"
    ).grid(row=0, column=0, sticky="w", padx=18, pady=(16, 10))
    return section


class SecondAuctionScreen(ctk.CTkFrame):
    """Organizer transfer-window release setup: pick exactly 4 releases
    per team, preview each team's original-purchase-price refunds and
    projected second-auction starting budget, then confirm the 16-player
    release pool. Gated on `session.auction.status == COMPLETE` — the
    same "no session / in-progress / blocked / active" pattern Match
    Results and Reports already use."""

    def __init__(self, parent: ctk.CTkBaseClass, session: AuctionSession | None = None) -> None:
        super().__init__(parent, fg_color=theme.BACKGROUND, corner_radius=0)
        self._session = session
        self._form_error: str | None = None

        # Milestone 2 (live bidding view) — screen-local only, never
        # persisted, exactly like Live Auction's own `_selected_team_id`/
        # `_price_text`/`_error_message`. `_last_result` mirrors Live
        # Auction's use of `AuctionSession.last_result` for the SOLD/
        # UNSOLD feedback banner, but kept on the screen instance instead
        # since `SecondAuctionTransactionResult` is not a persisted field
        # of `AuctionSession` (see services/second_auction_bidding_
        # service.py) — it only needs to survive across this screen's own
        # `_render()` calls, not across a session reload.
        self._selected_team_id: int | None = None
        self._price_text: str = ""
        self._price_var: ctk.StringVar | None = None
        self._last_result: SecondAuctionTransactionResult | None = None
        self._current_photo_image: ctk.CTkImage | None = None

        # Milestone 3: a captain's phone bid (or a connection/
        # disconnection) mutates the shared AuctionSession/CaptainAuthService
        # from the server's own thread, never a Tk widget directly — this
        # poll is what refreshes the desktop display, exactly mirroring
        # ui/screens/live_auction_screen.py's identical mechanism.
        self._last_bid_snapshot: tuple | None = None
        self._poll_job: str | None = None

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)

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
        return any(isinstance(child, ctk.CTkToplevel) for child in self.winfo_children())

    def _captain_bidding_server(self):
        return getattr(self.winfo_toplevel(), "captain_bidding_server", None)

    def _bid_snapshot(self) -> tuple:
        """A cheap fingerprint of everything the LIVE view needs to
        notice changed — a phone bid, another desktop bid, a SOLD/UNSOLD
        advancing to the next player, a round transition, or a captain
        connecting/disconnecting — mirrors `LiveAuctionScreen._bid_
        snapshot` exactly, scoped to the second auction's own state."""
        session = self._session
        server = self._captain_bidding_server()
        server_state = server.state if server is not None else None
        connected_count = server.auth.connected_count() if server is not None and server.is_running else None
        if session is None or not session.started:
            return (None, None, None, None, None, server_state, connected_count)
        state = session.second_auction_state
        return (
            state.current_player_id,
            state.current_bid,
            state.leading_team_id,
            state.status,
            state.round_number,
            server_state,
            connected_count,
        )

    def _poll_captain_bidding_state(self) -> None:
        server = self._captain_bidding_server()
        if server is not None:
            server.check_startup_timeout()
        if not self._has_open_dialog():
            snapshot = self._bid_snapshot()
            if snapshot != self._last_bid_snapshot:
                self._last_bid_snapshot = snapshot
                self._render()
        self._poll_job = self.after(500, self._poll_captain_bidding_state)

    # ------------------------------------------------------------------
    # Top-level dispatch
    # ------------------------------------------------------------------

    def _render(self) -> None:
        for child in self.winfo_children():
            child.destroy()

        if self._session is None or not self._session.started:
            self._build_no_session_state()
            return

        status = self._session.auction.status
        if status == AuctionStatus.BLOCKED:
            self._build_blocked_state()
            return
        if status != AuctionStatus.COMPLETE:
            self._build_in_progress_state()
            return

        # Milestone 2: once the second auction has actually been started,
        # it takes over this screen entirely — the Milestone 1 setup/
        # confirm view below is only ever shown while
        # `second_auction_state.status == NOT_STARTED` (whether DRAFT or
        # CONFIRMED-but-not-yet-started).
        second_status = self._session.second_auction_state.status
        if second_status == SecondAuctionStatus.NOT_STARTED:
            self._build_active_view()
        elif second_status == SecondAuctionStatus.IN_PROGRESS:
            self._build_second_auction_live_view()
        elif second_status == SecondAuctionStatus.COMPLETE:
            self._build_second_auction_complete_view()
        else:
            self._build_second_auction_blocked_view()

    # ------------------------------------------------------------------
    # A. TRANSFER WINDOW STATUS (gating states)
    # ------------------------------------------------------------------

    def _build_header(self, title: str, subtitle: str) -> None:
        ctk.CTkLabel(
            self, text=title, font=theme.heading_font(size=26), text_color=theme.TEXT_PRIMARY, anchor="w"
        ).grid(row=0, column=0, sticky="w", padx=32, pady=(28, 4))
        ctk.CTkLabel(
            self, text=subtitle, font=theme.body_font(size=14), text_color=theme.TEXT_SECONDARY, anchor="w",
        ).grid(row=1, column=0, sticky="w", padx=32, pady=(0, 24))

    def _build_no_session_state(self) -> None:
        self._build_header("SECOND AUCTION", "Transfer-window release setup for the second auction.")
        ctk.CTkLabel(
            self, text="NO AUCTION SESSION", font=theme.heading_font(size=18), text_color=theme.TEXT_SECONDARY, anchor="w"
        ).grid(row=2, column=0, sticky="w", padx=32)
        ctk.CTkLabel(
            self, text="Start or resume an auction before setting up the second auction.",
            font=theme.body_font(size=13), text_color=theme.TEXT_SECONDARY, anchor="w",
        ).grid(row=3, column=0, sticky="w", padx=32, pady=(4, 0))

    def _build_in_progress_state(self) -> None:
        self._build_header("SECOND AUCTION", "Transfer-window release setup for the second auction.")
        ctk.CTkLabel(
            self, text="FIRST AUCTION IN PROGRESS", font=theme.heading_font(size=18), text_color=theme.GOLD_ACCENT, anchor="w"
        ).grid(row=2, column=0, sticky="w", padx=32)
        ctk.CTkLabel(
            self, text="Second-auction setup requires a completed first auction.",
            font=theme.body_font(size=13), text_color=theme.TEXT_SECONDARY, anchor="w",
        ).grid(row=3, column=0, sticky="w", padx=32, pady=(4, 0))

    def _build_blocked_state(self) -> None:
        self._build_header("SECOND AUCTION", "Transfer-window release setup for the second auction.")
        ctk.CTkLabel(
            self, text="FIRST AUCTION BLOCKED", font=theme.heading_font(size=18), text_color=theme.UNSOLD_RED, anchor="w"
        ).grid(row=2, column=0, sticky="w", padx=32)
        ctk.CTkLabel(
            self,
            text=(
                "A blocked auction is not a completed first auction. Second-auction setup "
                "requires a completed auction, or an explicitly supported recovery workflow."
            ),
            font=theme.body_font(size=13), text_color=theme.TEXT_SECONDARY, anchor="w",
            justify="left", wraplength=760,
        ).grid(row=3, column=0, sticky="w", padx=32, pady=(4, 0))

    # ------------------------------------------------------------------
    # Active view (auction COMPLETE)
    # ------------------------------------------------------------------

    def _build_active_view(self) -> None:
        setup = self._session.second_auction_setup
        self._build_header(
            "TRANSFER WINDOW SETUP",
            "Select exactly four released players per team before the second auction begins.",
        )

        body = ctk.CTkScrollableFrame(self, fg_color=theme.BACKGROUND, corner_radius=0)
        body.grid(row=2, column=0, sticky="nsew", padx=32, pady=(0, 24))
        body.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        next_row = 0
        if self._form_error:
            error_banner = ctk.CTkFrame(body, fg_color=theme.UNSOLD_RED, corner_radius=8)
            ctk.CTkLabel(
                error_banner, text=self._form_error, font=theme.body_font(size=13, weight="bold"),
                text_color=theme.BACKGROUND, anchor="w", justify="left", wraplength=900,
            ).pack(padx=16, pady=10, anchor="w")
            error_banner.grid(row=next_row, column=0, sticky="ew", pady=(0, 14))
            next_row += 1

        status_banner = ctk.CTkFrame(body, fg_color=theme.SURFACE, corner_radius=8)
        if setup.is_confirmed:
            ctk.CTkLabel(
                status_banner, text="RELEASE PLAN CONFIRMED", font=theme.body_font(size=14, weight="bold"),
                text_color=theme.ACCENT_GREEN, anchor="w",
            ).pack(side="left", padx=16, pady=10)
            ctk.CTkLabel(
                status_banner, text="SECOND AUCTION NOT STARTED", font=theme.body_font(size=12, weight="bold"),
                text_color=theme.TEXT_SECONDARY, anchor="w",
            ).pack(side="left", padx=(0, 16), pady=10)
            ctk.CTkButton(
                status_banner, text="START SECOND AUCTION", width=190, height=32, corner_radius=8,
                font=theme.body_font(size=12, weight="bold"), fg_color=theme.ACCENT_GREEN,
                hover_color=theme.ACCENT_GREEN_HOVER, text_color=theme.BACKGROUND,
                command=self._on_start_second_auction_clicked,
            ).pack(side="right", padx=(8, 16), pady=8)
            ctk.CTkButton(
                status_banner, text="EDIT RELEASE PLAN", width=150, height=32, corner_radius=8,
                font=theme.body_font(size=12, weight="bold"), fg_color=theme.SURFACE_ALT, hover_color=theme.BORDER,
                text_color=theme.TEXT_PRIMARY, command=self._on_edit_plan_clicked,
            ).pack(side="right", padx=(0, 0), pady=8)
        else:
            ctk.CTkLabel(
                status_banner, text="DRAFT — not yet confirmed", font=theme.body_font(size=13, weight="bold"),
                text_color=theme.GOLD_ACCENT, anchor="w",
            ).pack(side="left", padx=16, pady=10)
        status_banner.grid(row=next_row, column=0, sticky="ew", pady=(0, 16))
        next_row += 1

        self._build_budgets_section(body).grid(row=next_row, column=0, sticky="ew", pady=(0, 16))
        next_row += 1
        self._build_release_selection_section(body).grid(row=next_row, column=0, sticky="ew", pady=(0, 16))
        next_row += 1
        self._build_refund_preview_section(body).grid(row=next_row, column=0, sticky="ew", pady=(0, 16))
        next_row += 1

        pool_section = self._build_release_pool_section(body)
        if pool_section is not None:
            pool_section.grid(row=next_row, column=0, sticky="ew", pady=(0, 16))
            next_row += 1

        self._build_confirm_row(body).grid(row=next_row, column=0, sticky="ew")

    # ------------------------------------------------------------------
    # B. CURRENT TRANSFER BUDGETS
    # ------------------------------------------------------------------

    def _build_budgets_section(self, parent: ctk.CTkBaseClass) -> ctk.CTkFrame:
        session = self._session
        section = _build_section(parent, "Current Transfer Budgets")

        from services import match_result_service as mrs

        ledgers = mrs.build_all_ledgers(session.teams, session.match_results)
        cards_row = ctk.CTkFrame(section, fg_color="transparent")
        cards_row.grid(row=1, column=0, sticky="ew", padx=18, pady=(0, 16))
        for index, ledger in enumerate(ledgers):
            cards_row.grid_columnconfigure(index, weight=1)
            card = ctk.CTkFrame(cards_row, fg_color=theme.SURFACE_ALT, corner_radius=8)
            card.grid(row=0, column=index, sticky="new", padx=6)
            ctk.CTkLabel(
                card, text=ledger.team.name.upper(), font=theme.heading_font(size=13), text_color=theme.TEXT_PRIMARY, anchor="w"
            ).pack(anchor="w", padx=14, pady=(12, 6))
            for label, value in [
                ("First Auction Remaining", f"{ledger.first_auction_remaining}M"),
                ("Match Earnings", _money(ledger.total_match_earnings)),
                ("Current Transfer Budget", f"{ledger.current_transfer_budget}M"),
            ]:
                row = ctk.CTkFrame(card, fg_color="transparent")
                row.pack(fill="x", padx=14, pady=1)
                ctk.CTkLabel(row, text=label, font=theme.body_font(size=11), text_color=theme.TEXT_SECONDARY, anchor="w").pack(side="left")
                ctk.CTkLabel(row, text=value, font=theme.body_font(size=12, weight="bold"), text_color=theme.GOLD_ACCENT, anchor="e").pack(side="right")
            ctk.CTkLabel(card, text="", height=1).pack(pady=(0, 6))
        return section

    # ------------------------------------------------------------------
    # C. PLAYER RELEASE SELECTION
    # ------------------------------------------------------------------

    def _build_release_selection_section(self, parent: ctk.CTkBaseClass) -> ctk.CTkFrame:
        session = self._session
        setup = session.second_auction_setup
        players_by_id = {player.id: player for player in session.players}
        section = _build_section(parent, "Player Release Selection")

        teams_row = ctk.CTkFrame(section, fg_color="transparent")
        teams_row.grid(row=1, column=0, sticky="ew", padx=18, pady=(0, 16))
        for index, team in enumerate(session.teams):
            teams_row.grid_columnconfigure(index, weight=1)
            self._build_team_release_card(teams_row, team, setup, players_by_id).grid(
                row=0, column=index, sticky="new", padx=6
            )
        return section

    def _build_team_release_card(self, parent: ctk.CTkBaseClass, team, setup, players_by_id: dict) -> ctk.CTkFrame:
        selected_ids = setup.selections_for(team.id)
        card = ctk.CTkFrame(parent, fg_color=theme.SURFACE_ALT, corner_radius=8)
        header = ctk.CTkFrame(card, fg_color="transparent")
        header.pack(fill="x", padx=14, pady=(12, 6))
        ctk.CTkLabel(
            header, text=team.name.upper(), font=theme.heading_font(size=13), text_color=theme.TEXT_PRIMARY, anchor="w"
        ).pack(side="left")
        ctk.CTkLabel(
            header, text=f"Selected: {len(selected_ids)} / {sas.MAX_RELEASES_PER_TEAM}",
            font=theme.body_font(size=11, weight="bold"),
            text_color=theme.ACCENT_GREEN if len(selected_ids) == sas.MAX_RELEASES_PER_TEAM else theme.TEXT_SECONDARY,
            anchor="e",
        ).pack(side="right")

        for player_id in team.roster:
            player = players_by_id.get(player_id)
            if player is None:
                continue
            self._build_release_row(card, team, player, selected_ids, setup).pack(fill="x", padx=14, pady=2)

        ctk.CTkLabel(card, text="", height=1).pack(pady=(0, 8))
        return card

    def _build_release_row(self, parent: ctk.CTkBaseClass, team, player, selected_ids: list[int], setup) -> ctk.CTkFrame:
        row = ctk.CTkFrame(parent, fg_color="transparent")
        locked = sas.is_locked_player(player)
        price_text = f"{player.sold_price}M" if player.sold_price is not None else "—"

        name_block = ctk.CTkFrame(row, fg_color="transparent")
        name_block.pack(side="left", fill="x", expand=True)
        ctk.CTkLabel(
            name_block, text=player.full_name, font=theme.body_font(size=12, weight="bold"), text_color=theme.TEXT_PRIMARY, anchor="w"
        ).pack(anchor="w")
        badge_text = player.position.value
        if player.is_captain:
            badge_text += "  ·  CAPTAIN"
        elif player.position == Position.GK:
            badge_text += "  ·  GK"
        ctk.CTkLabel(
            name_block, text=f"{badge_text}  ·  {price_text}", font=theme.body_font(size=10), text_color=theme.TEXT_SECONDARY, anchor="w"
        ).pack(anchor="w")

        if locked:
            ctk.CTkLabel(
                row, text="LOCKED", font=theme.body_font(size=10, weight="bold"), text_color=theme.BACKGROUND,
                fg_color=theme.UNSOLD_RED, corner_radius=6, width=64, height=22,
            ).pack(side="right")
        else:
            is_selected = player.id in selected_ids
            at_limit = len(selected_ids) >= sas.MAX_RELEASES_PER_TEAM
            disabled = setup.is_confirmed or (at_limit and not is_selected)
            var = ctk.BooleanVar(value=is_selected)
            ctk.CTkCheckBox(
                row, text="RELEASE", variable=var, onvalue=True, offvalue=False,
                font=theme.body_font(size=10, weight="bold"), text_color=theme.TEXT_SECONDARY,
                fg_color=theme.ACCENT_GREEN, hover_color=theme.ACCENT_GREEN_HOVER,
                state="disabled" if disabled else "normal",
                command=lambda team_id=team.id, player_id=player.id: self._on_toggle_release(team_id, player_id),
            ).pack(side="right")
        return row

    def _on_toggle_release(self, team_id: int, player_id: int) -> None:
        try:
            self._session.toggle_second_auction_release(team_id, player_id)
        except SecondAuctionError as exc:
            self._form_error = str(exc)
        else:
            self._form_error = None
        self._render()

    # ------------------------------------------------------------------
    # D/E. REFUND PREVIEW + SECOND AUCTION BUDGET PREVIEW
    # ------------------------------------------------------------------

    def _build_refund_preview_section(self, parent: ctk.CTkBaseClass) -> ctk.CTkFrame:
        session = self._session
        section = _build_section(parent, "Refund Preview")
        previews = sas.build_all_release_previews(
            session.teams, session.second_auction_setup, {p.id: p for p in session.players}, session.match_results
        )
        cards_row = ctk.CTkFrame(section, fg_color="transparent")
        cards_row.grid(row=1, column=0, sticky="ew", padx=18, pady=(0, 16))
        for index, preview in enumerate(previews):
            cards_row.grid_columnconfigure(index, weight=1)
            self._build_refund_card(cards_row, preview).grid(row=0, column=index, sticky="new", padx=6)
        return section

    def _build_refund_card(self, parent: ctk.CTkBaseClass, preview: TeamReleasePreview) -> ctk.CTkFrame:
        card = ctk.CTkFrame(parent, fg_color=theme.SURFACE_ALT, corner_radius=8)
        ctk.CTkLabel(
            card, text=preview.team.name.upper(), font=theme.heading_font(size=13), text_color=theme.TEXT_PRIMARY, anchor="w"
        ).pack(anchor="w", padx=14, pady=(12, 6))
        ctk.CTkLabel(
            card, text=f"Current Transfer Budget: {preview.current_transfer_budget}M", font=theme.body_font(size=11),
            text_color=theme.TEXT_SECONDARY, anchor="w",
        ).pack(anchor="w", padx=14)

        if preview.refunds:
            ctk.CTkLabel(
                card, text="Selected releases:", font=theme.body_font(size=10, weight="bold"),
                text_color=theme.TEXT_SECONDARY, anchor="w",
            ).pack(anchor="w", padx=14, pady=(8, 2))
            for refund in preview.refunds:
                self._build_refund_line(card, refund).pack(fill="x", padx=14, pady=1)
        else:
            ctk.CTkLabel(
                card, text="No releases selected yet.", font=theme.body_font(size=11), text_color=theme.TEXT_SECONDARY, anchor="w",
            ).pack(anchor="w", padx=14, pady=(8, 2))

        ctk.CTkFrame(card, fg_color=theme.BORDER, height=1, corner_radius=0).pack(fill="x", padx=14, pady=8)
        row = ctk.CTkFrame(card, fg_color="transparent")
        row.pack(fill="x", padx=14, pady=1)
        ctk.CTkLabel(row, text="Release Refunds", font=theme.body_font(size=11), text_color=theme.TEXT_SECONDARY, anchor="w").pack(side="left")
        ctk.CTkLabel(row, text=_money(preview.total_refunds), font=theme.body_font(size=12, weight="bold"), text_color=theme.GOLD_ACCENT, anchor="e").pack(side="right")

        ctk.CTkLabel(
            card, text="SECOND AUCTION STARTING BUDGET", font=theme.body_font(size=10, weight="bold"),
            text_color=theme.TEXT_SECONDARY, anchor="w",
        ).pack(anchor="w", padx=14, pady=(8, 0))
        ctk.CTkLabel(
            card, text=f"{preview.second_auction_starting_budget}M", font=theme.heading_font(size=22), text_color=theme.ACCENT_GREEN, anchor="w",
        ).pack(anchor="w", padx=14, pady=(0, 14))
        return card

    def _build_refund_line(self, parent: ctk.CTkBaseClass, refund: ReleaseRefund) -> ctk.CTkFrame:
        row = ctk.CTkFrame(parent, fg_color="transparent")
        ctk.CTkLabel(row, text=refund.player.full_name, font=theme.body_font(size=11), text_color=theme.TEXT_PRIMARY, anchor="w").pack(side="left")
        ctk.CTkLabel(row, text=f"{refund.original_price}M", font=theme.body_font(size=11, weight="bold"), text_color=theme.TEXT_PRIMARY, anchor="e").pack(side="right")
        return row

    # ------------------------------------------------------------------
    # F. RELEASE POOL SUMMARY
    # ------------------------------------------------------------------

    def _build_release_pool_section(self, parent: ctk.CTkBaseClass) -> ctk.CTkFrame | None:
        session = self._session
        setup = session.second_auction_setup
        if not sas.all_teams_have_exactly_four(setup, session.teams):
            return None

        players_by_id = {p.id: p for p in session.players}
        pool = sas.build_release_pool(session.teams, setup, players_by_id)
        section = _build_section(parent, f"Second Auction Player Pool — {len(pool)} Players")

        columns = [("Player", 180), ("Previous Team", 170), ("Position", 90), ("Original Price", 120)]
        header = ctk.CTkFrame(section, fg_color=theme.SURFACE_ALT, corner_radius=6)
        header.grid(row=1, column=0, sticky="ew", padx=18)
        for index, (title, width) in enumerate(columns):
            header.grid_columnconfigure(index, minsize=width)
            ctk.CTkLabel(
                header, text=title.upper(), font=theme.body_font(size=11, weight="bold"), text_color=theme.TEXT_SECONDARY, anchor="w",
            ).grid(row=0, column=index, sticky="w", padx=(12 if index == 0 else 4, 4), pady=8)

        rows_container = ctk.CTkFrame(section, fg_color="transparent")
        rows_container.grid(row=2, column=0, sticky="ew", padx=18, pady=(0, 10))
        for row_index, entry in enumerate(pool):
            row = ctk.CTkFrame(rows_container, fg_color=theme.SURFACE if row_index % 2 else "transparent", corner_radius=4)
            row.grid(row=row_index, column=0, sticky="ew", pady=2)
            for index, (_title, width) in enumerate(columns):
                row.grid_columnconfigure(index, minsize=width)
            values = [entry.player.full_name, entry.previous_team.name, entry.player.position.value, f"{entry.original_price}M"]
            for index, text in enumerate(values):
                ctk.CTkLabel(
                    row, text=text, font=theme.body_font(size=13), text_color=theme.TEXT_PRIMARY, anchor="w",
                ).grid(row=0, column=index, sticky="w", padx=(12 if index == 0 else 4, 4), pady=6)

        summary_row = ctk.CTkFrame(section, fg_color="transparent")
        summary_row.grid(row=3, column=0, sticky="w", padx=18, pady=(0, 16))
        for team in session.teams:
            count = len(setup.selections_for(team.id))
            ctk.CTkLabel(
                summary_row, text=f"{team.name}: {count} released", font=theme.body_font(size=12),
                text_color=theme.TEXT_SECONDARY,
            ).pack(side="left", padx=(0, 16))
        return section

    # ------------------------------------------------------------------
    # Confirm / Edit release plan
    # ------------------------------------------------------------------

    def _build_confirm_row(self, parent: ctk.CTkBaseClass) -> ctk.CTkFrame:
        row = ctk.CTkFrame(parent, fg_color="transparent")
        setup = self._session.second_auction_setup
        ready = sas.all_teams_have_exactly_four(setup, self._session.teams)
        if not setup.is_confirmed:
            ctk.CTkButton(
                row, text="CONFIRM RELEASE LISTS", font=theme.heading_font(size=15),
                fg_color=theme.ACCENT_GREEN, hover_color=theme.ACCENT_GREEN_HOVER, text_color=theme.BACKGROUND,
                height=44, corner_radius=10, width=220,
                state="normal" if ready else "disabled",
                command=self._on_confirm_clicked,
            ).pack(side="left")
        return row

    def _on_confirm_clicked(self) -> None:
        dialog = ctk.CTkToplevel(self)
        dialog.title("Confirm Release Plan")
        dialog.configure(fg_color=theme.BACKGROUND)
        dialog.transient(self.winfo_toplevel())
        dialog.protocol("WM_DELETE_WINDOW", dialog.destroy)

        content = ctk.CTkFrame(dialog, fg_color="transparent")
        content.pack(fill="both", expand=True, padx=28, pady=24)

        ctk.CTkLabel(
            content, text="Confirm these 16 released players?", font=theme.heading_font(size=16), text_color=theme.TEXT_PRIMARY
        ).pack(pady=(0, 10))
        ctk.CTkLabel(
            content,
            text=(
                "This will lock the transfer-window release plan for the second auction. "
                "The first-auction history and budgets will remain unchanged."
            ),
            font=theme.body_font(size=12), text_color=theme.TEXT_SECONDARY, wraplength=380, justify="center",
        ).pack(pady=(0, 16))

        button_row = ctk.CTkFrame(content, fg_color="transparent")
        button_row.pack()
        ctk.CTkButton(
            button_row, text="Confirm", width=112, height=36, corner_radius=8, fg_color=theme.ACCENT_GREEN,
            hover_color=theme.ACCENT_GREEN_HOVER, text_color=theme.BACKGROUND,
            command=lambda: self._confirm_release_plan(dialog),
        ).pack(side="left", padx=8)
        ctk.CTkButton(
            button_row, text="Cancel", width=112, height=36, corner_radius=8, fg_color=theme.SURFACE_ALT,
            hover_color=theme.BORDER, text_color=theme.TEXT_PRIMARY, command=dialog.destroy,
        ).pack(side="left", padx=8)

        dialog.update_idletasks()
        width = max(dialog.winfo_reqwidth(), 320)
        height = dialog.winfo_reqheight()
        root = self.winfo_toplevel()
        x = root.winfo_rootx() + (root.winfo_width() - width) // 2
        y = root.winfo_rooty() + (root.winfo_height() - height) // 2
        dialog.geometry(f"{width}x{height}+{max(x, 0)}+{max(y, 0)}")
        dialog.grab_set()

    def _confirm_release_plan(self, dialog: ctk.CTkToplevel) -> None:
        dialog.destroy()
        try:
            self._session.confirm_second_auction_release_plan()
        except SecondAuctionError as exc:
            self._form_error = str(exc)
        else:
            self._form_error = None
        self._render()

    def _on_edit_plan_clicked(self) -> None:
        dialog = ctk.CTkToplevel(self)
        dialog.title("Edit Release Plan")
        dialog.configure(fg_color=theme.BACKGROUND)
        dialog.transient(self.winfo_toplevel())
        dialog.protocol("WM_DELETE_WINDOW", dialog.destroy)

        content = ctk.CTkFrame(dialog, fg_color="transparent")
        content.pack(fill="both", expand=True, padx=28, pady=24)

        ctk.CTkLabel(
            content, text="Unlock the release plan for editing?", font=theme.heading_font(size=16), text_color=theme.TEXT_PRIMARY
        ).pack(pady=(0, 10))
        ctk.CTkLabel(
            content,
            text="This is safe only because the second auction has not started yet. The confirmed "
            "plan will return to DRAFT until you confirm it again.",
            font=theme.body_font(size=12), text_color=theme.TEXT_SECONDARY, wraplength=380, justify="center",
        ).pack(pady=(0, 16))

        button_row = ctk.CTkFrame(content, fg_color="transparent")
        button_row.pack()
        ctk.CTkButton(
            button_row, text="Unlock", width=112, height=36, corner_radius=8, fg_color=theme.UNSOLD_RED,
            text_color=theme.TEXT_PRIMARY, command=lambda: self._confirm_edit_plan(dialog),
        ).pack(side="left", padx=8)
        ctk.CTkButton(
            button_row, text="Cancel", width=112, height=36, corner_radius=8, fg_color=theme.SURFACE_ALT,
            hover_color=theme.BORDER, text_color=theme.TEXT_PRIMARY, command=dialog.destroy,
        ).pack(side="left", padx=8)

        dialog.update_idletasks()
        width = max(dialog.winfo_reqwidth(), 320)
        height = dialog.winfo_reqheight()
        root = self.winfo_toplevel()
        x = root.winfo_rootx() + (root.winfo_width() - width) // 2
        y = root.winfo_rooty() + (root.winfo_height() - height) // 2
        dialog.geometry(f"{width}x{height}+{max(x, 0)}+{max(y, 0)}")
        dialog.grab_set()

    def _confirm_edit_plan(self, dialog: ctk.CTkToplevel) -> None:
        dialog.destroy()
        try:
            self._session.unlock_second_auction_release_plan()
        except SecondAuctionError as exc:
            self._form_error = str(exc)
        else:
            self._form_error = None
        self._render()

    # ------------------------------------------------------------------
    # Milestone 2: start the second auction (confirmed plan -> IN_PROGRESS)
    # ------------------------------------------------------------------

    def _on_start_second_auction_clicked(self) -> None:
        dialog = ctk.CTkToplevel(self)
        dialog.title("Start Second Auction")
        dialog.configure(fg_color=theme.BACKGROUND)
        dialog.transient(self.winfo_toplevel())
        dialog.protocol("WM_DELETE_WINDOW", dialog.destroy)

        content = ctk.CTkFrame(dialog, fg_color="transparent")
        content.pack(fill="both", expand=True, padx=28, pady=24)

        ctk.CTkLabel(
            content, text="Start the second auction?", font=theme.heading_font(size=16), text_color=theme.TEXT_PRIMARY
        ).pack(pady=(0, 10))
        ctk.CTkLabel(
            content,
            text=(
                "This will apply the confirmed releases, create the 16-player auction pool, and "
                "initialize second-auction budgets. The first auction and match-result records "
                "will remain unchanged."
            ),
            font=theme.body_font(size=12), text_color=theme.TEXT_SECONDARY, wraplength=380, justify="center",
        ).pack(pady=(0, 16))

        button_row = ctk.CTkFrame(content, fg_color="transparent")
        button_row.pack()
        ctk.CTkButton(
            button_row, text="Start", width=112, height=36, corner_radius=8, fg_color=theme.ACCENT_GREEN,
            hover_color=theme.ACCENT_GREEN_HOVER, text_color=theme.BACKGROUND,
            command=lambda: self._confirm_start_second_auction(dialog),
        ).pack(side="left", padx=8)
        ctk.CTkButton(
            button_row, text="Cancel", width=112, height=36, corner_radius=8, fg_color=theme.SURFACE_ALT,
            hover_color=theme.BORDER, text_color=theme.TEXT_PRIMARY, command=dialog.destroy,
        ).pack(side="left", padx=8)

        dialog.update_idletasks()
        width = max(dialog.winfo_reqwidth(), 320)
        height = dialog.winfo_reqheight()
        root = self.winfo_toplevel()
        x = root.winfo_rootx() + (root.winfo_width() - width) // 2
        y = root.winfo_rooty() + (root.winfo_height() - height) // 2
        dialog.geometry(f"{width}x{height}+{max(x, 0)}+{max(y, 0)}")
        dialog.grab_set()

    def _confirm_start_second_auction(self, dialog: ctk.CTkToplevel) -> None:
        dialog.destroy()
        try:
            self._session.start_second_auction()
        except SecondAuctionBiddingError as exc:
            self._form_error = str(exc)
        else:
            self._form_error = None
            self._selected_team_id = None
            self._price_text = ""
            self._last_result = None
        self._render()

    # ------------------------------------------------------------------
    # Milestone 2: SECOND AUCTION LIVE — manual organizer bidding over the
    # confirmed 16-player release pool. Visually consistent with the
    # existing Live Auction screen, but its own simpler implementation —
    # no captain phone bidding, no premium card-art compositing, and a
    # second-auction-specific reserve/eligibility rule (see services/
    # second_auction_bidding_service.py's module docstring for why this
    # is not a reuse of services/auction_service.py).
    # ------------------------------------------------------------------

    def _build_second_auction_live_view(self) -> None:
        session = self._session
        state = session.second_auction_state
        players_by_id = {player.id: player for player in session.players}
        current_player_id = state.current_player_id
        current_player = players_by_id.get(current_player_id) if current_player_id is not None else None

        if current_player is None:
            # Defensive: should not happen while IN_PROGRESS.
            self._build_header("SECOND AUCTION — TRANSFER WINDOW", "No current second-auction player is available.")
            return

        if self._selected_team_id is not None:
            selected_team = next((t for t in session.teams if t.id == self._selected_team_id), None)
            if selected_team is None or not sabs.team_can_bid_for_player(state, selected_team, current_player):
                self._selected_team_id = None

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)

        body = ctk.CTkScrollableFrame(self, fg_color=theme.BACKGROUND, corner_radius=0)
        body.grid(row=0, column=0, sticky="nsew", padx=28, pady=(16, 16))
        body.grid_columnconfigure(0, weight=1)

        next_row = 0
        ctk.CTkLabel(
            body, text="SECOND AUCTION — TRANSFER WINDOW", font=theme.heading_font(size=22),
            text_color=theme.ACCENT_GREEN, anchor="w",
        ).grid(row=next_row, column=0, sticky="w", pady=(4, 10))
        next_row += 1

        self._build_second_auction_progress_row(body, state).grid(row=next_row, column=0, sticky="ew", pady=(0, 10))
        next_row += 1

        if self._last_result is not None:
            self._build_second_auction_feedback_banner(body, self._last_result).grid(
                row=next_row, column=0, sticky="ew", pady=(0, 10)
            )
            next_row += 1

        if self._form_error:
            error_banner = ctk.CTkFrame(body, fg_color=theme.UNSOLD_RED, corner_radius=8)
            ctk.CTkLabel(
                error_banner, text=self._form_error, font=theme.body_font(size=13, weight="bold"),
                text_color=theme.BACKGROUND, anchor="w", justify="left", wraplength=900,
            ).pack(padx=16, pady=10, anchor="w")
            error_banner.grid(row=next_row, column=0, sticky="ew", pady=(0, 10))
            next_row += 1

        pool_entry = sabs.release_pool_lookup(
            session.teams, session.second_auction_setup, players_by_id
        ).get(current_player.id)

        # Visual hierarchy (Milestone 3 — "feel like a real transfer-
        # window event"): CURRENT PLAYER is the strongest element, CURRENT
        # BID/LEADING TEAM immediately below it is the next most
        # prominent, and historical context (previous team/original
        # price) stays visually secondary inside the player card itself.
        self._build_second_auction_player_card(body, current_player, pool_entry).grid(
            row=next_row, column=0, sticky="ew", pady=(0, 14)
        )
        next_row += 1

        self._build_second_auction_bid_row(body, state).grid(row=next_row, column=0, sticky="ew", pady=(0, 14))
        next_row += 1

        self._build_second_auction_team_status_row(body, state, current_player).grid(
            row=next_row, column=0, sticky="ew", pady=(0, 14)
        )
        next_row += 1

        self._build_captain_connections_section(body).grid(row=next_row, column=0, sticky="ew", pady=(0, 14))
        next_row += 1

        self._build_second_auction_controls_row(body).grid(row=next_row, column=0, sticky="ew", pady=(0, 14))
        next_row += 1

        self._build_second_auction_action_row(body).grid(row=next_row, column=0, sticky="ew", pady=(0, 16))
        next_row += 1

        self._build_second_auction_history_section(body, state).grid(row=next_row, column=0, sticky="ew")

    def _build_second_auction_progress_row(self, parent: ctk.CTkBaseClass, state) -> ctk.CTkFrame:
        row = ctk.CTkFrame(parent, fg_color=theme.SURFACE, corner_radius=12)
        round_label = "Round 1" if state.round_number == 1 else f"Re-Auction Round {state.round_number}"
        position_number = state.current_queue_position + 1
        resolved = state.current_queue_position
        remaining = len(state.queue) - resolved
        sold_total = len(sabs.sold_player_ids(state))
        ctk.CTkLabel(
            row, text=f"  {round_label}  ·  Player {position_number} of {len(state.queue)}",
            font=theme.heading_font(size=16), text_color=theme.TEXT_PRIMARY, anchor="w",
        ).pack(side="left", pady=12)
        ctk.CTkLabel(
            row, text=f"    {resolved} resolved this round    ·    {remaining} remaining this round",
            font=theme.body_font(size=13), text_color=theme.TEXT_SECONDARY, anchor="w",
        ).pack(side="left", pady=12)
        ctk.CTkLabel(
            row, text=f"{sold_total} / {len(state.released_player_ids)} SOLD  ",
            font=theme.body_font(size=13, weight="bold"), text_color=theme.GOLD_ACCENT, anchor="e",
        ).pack(side="right", pady=12)
        return row

    def _build_second_auction_feedback_banner(
        self, parent: ctk.CTkBaseClass, result: SecondAuctionTransactionResult
    ) -> ctk.CTkFrame:
        if result.outcome == "SOLD":
            color = theme.SOLD_GREEN
            text = f"SOLD  ·  {result.player.full_name} → {result.team.name} for {result.sale_price}M"
        else:
            color = theme.UNSOLD_RED
            text = f"UNSOLD  ·  {result.player.full_name}"
        banner = ctk.CTkFrame(parent, fg_color=color, corner_radius=8)
        ctk.CTkLabel(
            banner, text=text, font=theme.body_font(size=14, weight="bold"), text_color=theme.BACKGROUND, anchor="w",
        ).pack(padx=16, pady=10, anchor="w")
        return banner

    def _build_second_auction_player_card(
        self, parent: ctk.CTkBaseClass, player: Player, pool_entry
    ) -> ctk.CTkFrame:
        card = ctk.CTkFrame(parent, fg_color=theme.SURFACE, corner_radius=16, border_width=2, border_color=theme.BORDER)
        card.grid_columnconfigure(1, weight=1)

        photo_frame = ctk.CTkFrame(
            card, fg_color=theme.SURFACE_ALT, corner_radius=12,
            width=_CARD_PHOTO_SIZE[0], height=_CARD_PHOTO_SIZE[1],
        )
        photo_frame.grid(row=0, column=0, rowspan=5, padx=16, pady=16)
        photo_frame.grid_propagate(False)

        image = load_cover_fit_image(Path(player.photo_path), _CARD_PHOTO_SIZE) if player.photo_path else None
        self._current_photo_image = image  # kept alive to avoid GC; None is fine (no photo)
        if image is not None:
            ctk.CTkLabel(photo_frame, image=image, text="").place(x=0, y=0)
        else:
            ctk.CTkLabel(
                photo_frame, text=player.short_name[:2].upper(), font=theme.heading_font(size=28),
                text_color=theme.TEXT_SECONDARY,
            ).place(relx=0.5, rely=0.5, anchor="center")

        ctk.CTkLabel(
            card, text=player.full_name.upper(), font=theme.heading_font(size=26), text_color=theme.TEXT_PRIMARY,
            anchor="w",
        ).grid(row=0, column=1, sticky="w", padx=(0, 16), pady=(18, 4))

        badge_row = ctk.CTkFrame(card, fg_color="transparent")
        badge_row.grid(row=1, column=1, sticky="w", padx=(0, 16), pady=(0, 6))
        ctk.CTkLabel(
            badge_row, text=player.position.value, font=theme.body_font(size=12, weight="bold"),
            fg_color=theme.SURFACE_ALT, text_color=theme.TEXT_PRIMARY, corner_radius=6, width=48, height=22,
        ).pack(side="left")

        previous_team_name = pool_entry.previous_team.name if pool_entry is not None else "—"
        original_price = f"{pool_entry.original_price}M" if pool_entry is not None else "—"
        base_price = player_base_price(player)

        # Historical context (previous team/original first-auction price)
        # stays visually secondary — smaller, muted text — distinct from
        # the current second-auction bidding numbers shown in the bid
        # row built right below this card (see the ticket's "historical
        # context should be clearly separated from current bidding").
        info_lines = [
            ("PREVIOUS TEAM", previous_team_name),
            ("ORIGINAL PRICE", original_price),
            ("BASE PRICE (SECOND AUCTION)", f"{base_price}M"),
        ]
        for index, (label, value) in enumerate(info_lines, start=2):
            line = ctk.CTkFrame(card, fg_color="transparent")
            line.grid(row=index, column=1, sticky="w", padx=(0, 16), pady=1)
            ctk.CTkLabel(
                line, text=f"{label}: ", font=theme.body_font(size=12), text_color=theme.TEXT_SECONDARY,
            ).pack(side="left")
            ctk.CTkLabel(
                line, text=value, font=theme.body_font(size=12, weight="bold"), text_color=theme.TEXT_SECONDARY,
            ).pack(side="left")
        ctk.CTkLabel(card, text="", height=1).grid(row=5, column=1, pady=(0, 10))
        return card

    def _build_second_auction_bid_row(self, parent: ctk.CTkBaseClass, state) -> ctk.CTkFrame:
        """CURRENT BID / LEADING TEAM — the one authoritative live-bid
        display for the second auction, fed by `SecondAuctionState.
        current_bid`/`leading_team_id` (Milestone 3 — see services/
        second_auction_live_bid_service.py). `—` before any bid, same
        rule as Live Auction's own equivalent row."""
        row = ctk.CTkFrame(parent, fg_color=theme.SURFACE, corner_radius=12)
        row.grid_columnconfigure((0, 1), weight=1)

        bid_block = ctk.CTkFrame(row, fg_color="transparent")
        bid_block.grid(row=0, column=0, pady=18)
        ctk.CTkLabel(
            bid_block, text="CURRENT BID", font=theme.body_font(size=12, weight="bold"), text_color=theme.TEXT_SECONDARY,
        ).pack()
        ctk.CTkLabel(
            bid_block, text=f"{state.current_bid}M" if state.current_bid is not None else "—",
            font=theme.heading_font(size=36), text_color=theme.GOLD_ACCENT,
        ).pack()

        team_block = ctk.CTkFrame(row, fg_color="transparent")
        team_block.grid(row=0, column=1, pady=18)
        leading_team = next((t for t in self._session.teams if t.id == state.leading_team_id), None)
        ctk.CTkLabel(
            team_block, text="LEADING", font=theme.body_font(size=12, weight="bold"), text_color=theme.TEXT_SECONDARY,
        ).pack()
        ctk.CTkLabel(
            team_block, text=leading_team.name.upper() if leading_team is not None else "—",
            font=theme.heading_font(size=26), text_color=theme.ACCENT_GREEN,
        ).pack()
        return row

    def _build_captain_connections_section(self, parent: ctk.CTkBaseClass) -> ctk.CTkFrame:
        """A permanent, always-visible panel on the LIVE view (never only
        inside Settings — the ticket's explicit requirement) showing all
        four teams' captain-phone connection status at once. Purely
        informational: connection status never disables or is required
        for organizer manual bidding (see `_build_second_auction_team_
        status_row`, which never consults this)."""
        section = _build_section(parent, "Captain Connections")
        teams = self._session.teams
        server = self._captain_bidding_server()
        overview_by_id = {entry["team_id"]: entry for entry in server.auth.connection_overview()} if server is not None else {}

        labels: dict[int, str] = {}
        connected_count = 0
        for team in teams:
            entry = overview_by_id.get(team.id)
            if entry is not None and entry["connected"]:
                labels[team.id] = "CONNECTED"
                connected_count += 1
            elif entry is not None and entry["authenticated"]:
                labels[team.id] = "RECENTLY ACTIVE"
            else:
                labels[team.id] = "MANUAL / OFFLINE"

        summary_color = theme.ACCENT_GREEN if teams and connected_count == len(teams) else theme.TEXT_SECONDARY
        ctk.CTkLabel(
            section, text=f"{connected_count} / {len(teams)} CONNECTED", font=theme.heading_font(size=16),
            text_color=summary_color, anchor="w",
        ).grid(row=1, column=0, sticky="w", padx=18, pady=(0, 10))

        rows_container = ctk.CTkFrame(section, fg_color="transparent")
        rows_container.grid(row=2, column=0, sticky="ew", padx=18, pady=(0, 16))
        rows_container.grid_columnconfigure(0, weight=1)
        for index, team in enumerate(teams):
            line = ctk.CTkFrame(rows_container, fg_color=theme.SURFACE_ALT, corner_radius=8)
            line.grid(row=index, column=0, sticky="ew", pady=3)
            line.grid_columnconfigure(0, weight=1)
            ctk.CTkLabel(
                line, text=team.name, font=theme.body_font(size=13, weight="bold"), text_color=theme.TEXT_PRIMARY,
                anchor="w",
            ).grid(row=0, column=0, sticky="w", padx=14, pady=10)
            label = labels[team.id]
            fg_color, text_color = _CONNECTION_LABEL_COLORS[label]
            ctk.CTkLabel(
                line, text=label, font=theme.body_font(size=11, weight="bold"), fg_color=fg_color,
                text_color=text_color, corner_radius=6, width=140, height=24,
            ).grid(row=0, column=1, sticky="e", padx=14, pady=10)
        return section

    def _build_second_auction_team_status_row(
        self, parent: ctk.CTkBaseClass, state, current_player: Player
    ) -> ctk.CTkFrame:
        row = ctk.CTkFrame(parent, fg_color="transparent")
        for index, team in enumerate(self._session.teams):
            row.grid_columnconfigure(index, weight=1)
            selected = self._selected_team_id == team.id
            eligible = sabs.team_can_bid_for_player(state, team, current_player)
            roster_size = sabs.team_roster_size(state, team.id)
            remaining_budget = sabs.team_remaining_budget(state, team.id)
            max_legal = max(sabs.team_max_legal_bid(state, team), 0)

            lines = [
                team.name,
                f"{remaining_budget}M",
                f"{roster_size} / {team.max_squad_size}",
                f"Max {max_legal}M",
            ]
            if not eligible:
                lines.append("FULL" if roster_size >= team.max_squad_size else "LOW BUDGET")

            if eligible:
                fg_color = theme.ACCENT_GREEN if selected else theme.SURFACE
                text_color = theme.BACKGROUND if selected else theme.TEXT_PRIMARY
                hover_color = theme.ACCENT_GREEN_HOVER if selected else theme.SURFACE_ALT
                border_color = theme.ACCENT_GREEN if selected else theme.BORDER
                button_state = "normal"
                command = lambda team_id=team.id: self._on_second_auction_team_selected(team_id)
            else:
                fg_color = theme.SURFACE_ALT
                text_color = theme.TEXT_SECONDARY
                hover_color = theme.SURFACE_ALT
                border_color = theme.BORDER
                button_state = "disabled"
                command = None

            button = ctk.CTkButton(
                row, text="\n".join(lines), font=theme.body_font(size=13, weight="bold"),
                fg_color=fg_color, text_color=text_color, hover_color=hover_color,
                border_width=2, border_color=border_color, corner_radius=10,
                height=44 + 12 * len(lines), state=button_state, command=command,
            )
            button.grid(row=0, column=index, sticky="nsew", padx=6)
        return row

    def _on_second_auction_team_selected(self, team_id: int) -> None:
        self._selected_team_id = team_id
        self._form_error = None
        self._render()

    def _build_second_auction_controls_row(self, parent: ctk.CTkBaseClass) -> ctk.CTkFrame:
        row = ctk.CTkFrame(parent, fg_color="transparent")

        self._price_var = ctk.StringVar(value=self._price_text)
        self._price_var.trace_add("write", lambda *_args: self._on_second_auction_price_var_changed())

        selected_team = next((t for t in self._session.teams if t.id == self._selected_team_id), None)
        label_text = f"PRICE FOR — {selected_team.name.upper()}" if selected_team else "SELECT A TEAM ABOVE"
        ctk.CTkLabel(
            row, text=label_text, font=theme.body_font(size=12, weight="bold"), text_color=theme.TEXT_SECONDARY,
        ).pack(side="left", padx=(0, 10))
        ctk.CTkEntry(
            row, textvariable=self._price_var, width=100, fg_color=theme.SURFACE, border_color=theme.BORDER,
            text_color=theme.TEXT_PRIMARY, placeholder_text="e.g. 4",
        ).pack(side="left", padx=(0, 10))

        for amount in QUICK_PRICE_INCREMENTS:
            ctk.CTkButton(
                row, text=f"+{amount}M", width=54, height=28, corner_radius=6, fg_color=theme.SURFACE_ALT,
                hover_color=theme.BORDER, text_color=theme.TEXT_PRIMARY, font=theme.body_font(size=12, weight="bold"),
                command=lambda amount=amount: self._bump_second_auction_price(amount),
            ).pack(side="left", padx=4)
        return row

    def _on_second_auction_price_var_changed(self) -> None:
        self._price_text = self._price_var.get()

    def _bump_second_auction_price(self, amount: int) -> None:
        text = self._price_var.get().strip()
        try:
            current_value = int(text) if text else 0
        except ValueError:
            current_value = 0
        if current_value > 0:
            self._price_var.set(str(current_value + amount))
        else:
            self._price_var.set(str(max(OUTFIELD_BASE_PRICE, amount)))

    def _build_second_auction_action_row(self, parent: ctk.CTkBaseClass) -> ctk.CTkFrame:
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.grid_columnconfigure((0, 1), weight=1)
        ctk.CTkButton(
            row, text="SOLD", font=theme.heading_font(size=18), fg_color=theme.SOLD_GREEN,
            hover_color=theme.ACCENT_GREEN_HOVER, text_color=theme.BACKGROUND, height=52, corner_radius=10,
            command=self._on_second_auction_sold_clicked,
        ).grid(row=0, column=0, sticky="ew", padx=(0, 8))
        ctk.CTkButton(
            row, text="UNSOLD", font=theme.heading_font(size=18), fg_color=theme.UNSOLD_RED,
            hover_color="#c93f43", text_color=theme.TEXT_PRIMARY, height=52, corner_radius=10,
            command=self._on_second_auction_unsold_clicked,
        ).grid(row=0, column=1, sticky="ew", padx=(8, 0))
        return row

    def _read_second_auction_price(self) -> int | None:
        text = self._price_var.get().strip() if self._price_var is not None else ""
        if not text:
            return None
        try:
            value = int(text)
        except ValueError:
            return None
        if value <= 0:
            return None
        return value

    def _on_second_auction_sold_clicked(self) -> None:
        # Mixed mode (Milestone 3): a live bid — placed by a captain's
        # phone, or the organizer's own desktop bid — takes priority over
        # the manual team-select/price-entry controls, exactly mirroring
        # ui/screens/live_auction_screen.py's `_on_sold_clicked`. The
        # manual flow below remains the fallback whenever no live bid
        # exists yet (no phones connected, or the organizer prefers
        # typing a price directly).
        state = self._session.second_auction_state
        leading_team_id = state.leading_team_id
        live_bid = state.current_bid

        if leading_team_id is not None and live_bid is not None:
            team_id = leading_team_id
            price = live_bid
        else:
            if self._selected_team_id is None:
                self._form_error = "Select a buying team before marking SOLD."
                self._render()
                return
            price = self._read_second_auction_price()
            if price is None:
                self._form_error = "Enter a valid whole-number sale price before marking SOLD."
                self._render()
                return
            team_id = self._selected_team_id

        try:
            result = self._session.sell_second_auction_player(winning_team=team_id, sale_price=price)
        except SecondAuctionBiddingError as exc:
            self._form_error = str(exc)
            self._render()
            return
        self._last_result = result
        self._selected_team_id = None
        self._price_text = ""
        self._form_error = None
        self._render()

    def _on_second_auction_unsold_clicked(self) -> None:
        try:
            result = self._session.mark_second_auction_player_unsold()
        except SecondAuctionBiddingError as exc:
            self._form_error = str(exc)
            self._render()
            return
        self._last_result = result
        self._selected_team_id = None
        self._price_text = ""
        self._form_error = None
        self._render()

    # ------------------------------------------------------------------
    # Second-auction transaction history — shared by the LIVE, COMPLETE,
    # and BLOCKED views. Reads only `SecondAuctionState.history`, never
    # the first auction's own `Auction.history`.
    # ------------------------------------------------------------------

    def _build_second_auction_history_section(self, parent: ctk.CTkBaseClass, state) -> ctk.CTkFrame:
        section = _build_section(parent, f"Second Auction Transaction History — {len(state.history)} entries")
        if not state.history:
            ctk.CTkLabel(
                section, text="No transactions yet.", font=theme.body_font(size=12), text_color=theme.TEXT_SECONDARY,
                anchor="w",
            ).grid(row=1, column=0, sticky="w", padx=18, pady=(0, 16))
            return section

        columns = [
            ("Seq", 46), ("Player", 150), ("Previous Team", 130), ("Orig. Price", 90),
            ("Status", 70), ("New Team", 130), ("Price", 60), ("Round", 56),
        ]
        header = ctk.CTkFrame(section, fg_color=theme.SURFACE_ALT, corner_radius=6)
        header.grid(row=1, column=0, sticky="ew", padx=18)
        for index, (title, width) in enumerate(columns):
            header.grid_columnconfigure(index, minsize=width)
            ctk.CTkLabel(
                header, text=title.upper(), font=theme.body_font(size=10, weight="bold"),
                text_color=theme.TEXT_SECONDARY, anchor="w",
            ).grid(row=0, column=index, sticky="w", padx=(12 if index == 0 else 4, 4), pady=6)

        rows_container = ctk.CTkFrame(section, fg_color="transparent")
        rows_container.grid(row=2, column=0, sticky="ew", padx=18, pady=(0, 16))
        for row_index, entry in enumerate(reversed(state.history)):
            row = ctk.CTkFrame(rows_container, fg_color=theme.SURFACE if row_index % 2 else "transparent", corner_radius=4)
            row.grid(row=row_index, column=0, sticky="ew", pady=1)
            for index, (_title, width) in enumerate(columns):
                row.grid_columnconfigure(index, minsize=width)
            status_color = theme.SOLD_GREEN if entry.status == "SOLD" else theme.UNSOLD_RED
            values = [
                str(entry.sequence),
                entry.player_name,
                entry.previous_team_name,
                f"{entry.original_first_auction_price}M",
                entry.status,
                entry.team_name or "—",
                f"{entry.sold_price}M" if entry.sold_price is not None else "—",
                str(entry.round_number),
            ]
            for index, text in enumerate(values):
                color = status_color if index == 4 else theme.TEXT_PRIMARY
                ctk.CTkLabel(
                    row, text=text, font=theme.body_font(size=11, weight="bold" if index == 4 else "normal"),
                    text_color=color, anchor="w",
                ).grid(row=0, column=index, sticky="w", padx=(12 if index == 0 else 4, 4), pady=4)
        return section

    # ------------------------------------------------------------------
    # Milestone 2: SECOND AUCTION COMPLETE
    # ------------------------------------------------------------------

    def _build_second_auction_complete_view(self) -> None:
        session = self._session
        state = session.second_auction_state
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)

        body = ctk.CTkScrollableFrame(self, fg_color=theme.BACKGROUND, corner_radius=0)
        body.grid(row=0, column=0, sticky="nsew", padx=28, pady=(16, 16))
        body.grid_columnconfigure(0, weight=1)

        total_sold = len(sabs.sold_player_ids(state))
        total_pool = len(state.released_player_ids)

        next_row = 0
        ctk.CTkLabel(
            body, text="SECOND AUCTION — TRANSFER WINDOW", font=theme.heading_font(size=22),
            text_color=theme.ACCENT_GREEN, anchor="w",
        ).grid(row=next_row, column=0, sticky="w", pady=(4, 4))
        next_row += 1
        ctk.CTkLabel(
            body, text="SECOND AUCTION COMPLETE", font=theme.heading_font(size=30), text_color=theme.ACCENT_GREEN,
            anchor="w",
        ).grid(row=next_row, column=0, sticky="w", pady=(0, 4))
        next_row += 1
        ctk.CTkLabel(
            body, text=f"{total_sold} / {total_pool} PLAYERS SOLD", font=theme.heading_font(size=16),
            text_color=theme.TEXT_PRIMARY, anchor="w",
        ).grid(row=next_row, column=0, sticky="w", pady=(0, 2))
        next_row += 1
        ctk.CTkLabel(
            body, text="FINAL SQUADS LOCKED", font=theme.body_font(size=13, weight="bold"),
            text_color=theme.GOLD_ACCENT, anchor="w",
        ).grid(row=next_row, column=0, sticky="w", pady=(0, 16))
        next_row += 1

        cards_row = ctk.CTkFrame(body, fg_color="transparent")
        cards_row.grid(row=next_row, column=0, sticky="ew", pady=(0, 16))
        for index, team in enumerate(session.teams):
            cards_row.grid_columnconfigure(index, weight=1)
            card = ctk.CTkFrame(cards_row, fg_color=theme.SURFACE, corner_radius=10)
            card.grid(row=0, column=index, sticky="new", padx=6)
            ctk.CTkLabel(
                card, text=team.name.upper(), font=theme.heading_font(size=14), text_color=theme.TEXT_PRIMARY,
                anchor="w",
            ).pack(anchor="w", padx=14, pady=(12, 6))
            roster_size = len(state.active_rosters_by_team.get(team.id, []))
            remaining = state.remaining_budgets_by_team.get(team.id, 0)
            starting = state.starting_budgets_by_team.get(team.id, 0)
            purchases = sum(
                1 for entry in state.history if entry.status == "SOLD" and entry.team_id == team.id
            )
            for label, value in [
                ("Starting Budget", f"{starting}M"),
                ("Final Remaining Budget", f"{remaining}M"),
                ("Final Squad", f"{roster_size} / {team.max_squad_size}"),
                ("Second-Window Purchases", str(purchases)),
            ]:
                line = ctk.CTkFrame(card, fg_color="transparent")
                line.pack(fill="x", padx=14, pady=1)
                ctk.CTkLabel(
                    line, text=label, font=theme.body_font(size=11), text_color=theme.TEXT_SECONDARY, anchor="w",
                ).pack(side="left")
                ctk.CTkLabel(
                    line, text=value, font=theme.body_font(size=12, weight="bold"), text_color=theme.GOLD_ACCENT,
                    anchor="e",
                ).pack(side="right")
            ctk.CTkLabel(card, text="", height=1).pack(pady=(0, 6))
        next_row += 1

        toplevel = self.winfo_toplevel()
        if hasattr(toplevel, "show_screen"):
            ctk.CTkButton(
                body, text="VIEW FINAL TEAMS", font=theme.heading_font(size=14), fg_color=theme.ACCENT_GREEN,
                hover_color=theme.ACCENT_GREEN_HOVER, text_color=theme.BACKGROUND, height=40, corner_radius=10,
                width=220, command=lambda: toplevel.show_screen("Teams"),
            ).grid(row=next_row, column=0, sticky="w", pady=(0, 16))
            next_row += 1

        self._build_second_auction_history_section(body, state).grid(row=next_row, column=0, sticky="ew")

    # ------------------------------------------------------------------
    # Milestone 2: SECOND AUCTION BLOCKED
    # ------------------------------------------------------------------

    def _build_second_auction_blocked_view(self) -> None:
        session = self._session
        state = session.second_auction_state
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)

        body = ctk.CTkScrollableFrame(self, fg_color=theme.BACKGROUND, corner_radius=0)
        body.grid(row=0, column=0, sticky="nsew", padx=28, pady=(16, 16))
        body.grid_columnconfigure(0, weight=1)

        next_row = 0
        ctk.CTkLabel(
            body, text="SECOND AUCTION — TRANSFER WINDOW", font=theme.heading_font(size=22),
            text_color=theme.UNSOLD_RED, anchor="w",
        ).grid(row=next_row, column=0, sticky="w", pady=(4, 4))
        next_row += 1
        ctk.CTkLabel(
            body, text="SECOND AUCTION BLOCKED", font=theme.heading_font(size=26), text_color=theme.UNSOLD_RED,
            anchor="w",
        ).grid(row=next_row, column=0, sticky="w", pady=(0, 10))
        next_row += 1

        sold_total = len(sabs.sold_player_ids(state))
        ctk.CTkLabel(
            body,
            text=(
                "Re-auction cannot continue: no eligible team can purchase the remaining released "
                f"player(s). {sold_total} / {len(state.released_player_ids)} released players sold "
                "before the second auction stalled."
            ),
            font=theme.body_font(size=14), text_color=theme.TEXT_PRIMARY, wraplength=900, justify="left",
        ).grid(row=next_row, column=0, sticky="w", pady=(0, 16))
        next_row += 1

        teams_row = ctk.CTkFrame(body, fg_color="transparent")
        teams_row.grid(row=next_row, column=0, sticky="ew", pady=(0, 16))
        for index, team in enumerate(session.teams):
            teams_row.grid_columnconfigure(index, weight=1)
            roster_size = len(state.active_rosters_by_team.get(team.id, []))
            remaining = state.remaining_budgets_by_team.get(team.id, 0)
            ctk.CTkLabel(
                teams_row, text=f"{team.name} — {remaining}M, {roster_size} / {team.max_squad_size}",
                font=theme.body_font(size=13, weight="bold"), text_color=theme.TEXT_PRIMARY,
            ).grid(row=0, column=index, sticky="w", padx=6)
        next_row += 1

        self._build_second_auction_history_section(body, state).grid(row=next_row, column=0, sticky="ew")


def build_second_auction_screen(parent: ctk.CTkBaseClass, session: AuctionSession | None = None) -> SecondAuctionScreen:
    return SecondAuctionScreen(parent, session)
