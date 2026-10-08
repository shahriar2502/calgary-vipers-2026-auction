"""Second Auction screen — Milestone 1 (September/October 2026): transfer-
window release-plan setup (release selection, original-purchase-price
refund preview, and the 16-player release pool).

GUI only, and deliberately independent of the first-auction transaction
screens and `services/match_result_service.py`'s own write methods —
every rule enforced here lives in `services/second_auction_service.py` /
`AuctionSession`'s own `toggle_second_auction_release`/
`confirm_second_auction_release_plan`/`unlock_second_auction_release_plan`
methods, never inline in this file. Nothing here calls `process_sale`/
`process_unsold`/`place_bid`, and nothing here mutates a `Team`'s roster
or budget, or a `Player`'s `sold_price` — see PROJECT_CONTEXT.md's
"SECOND AUCTION — MILESTONE 1". Actual second-auction bidding is an
explicit future milestone and is not implemented here.
"""

from __future__ import annotations

import customtkinter as ctk

from models.auction import AuctionStatus
from models.player import Position
from services import second_auction_service as sas
from services.auction_session_service import AuctionSession
from services.second_auction_service import ReleaseRefund, SecondAuctionError, TeamReleasePreview
from ui import theme


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

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self._render()

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
        elif status != AuctionStatus.COMPLETE:
            self._build_in_progress_state()
        else:
            self._build_active_view()

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
                status_banner, text="EDIT RELEASE PLAN", width=150, height=32, corner_radius=8,
                font=theme.body_font(size=12, weight="bold"), fg_color=theme.SURFACE_ALT, hover_color=theme.BORDER,
                text_color=theme.TEXT_PRIMARY, command=self._on_edit_plan_clicked,
            ).pack(side="right", padx=16, pady=8)
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


def build_second_auction_screen(parent: ctk.CTkBaseClass, session: AuctionSession | None = None) -> SecondAuctionScreen:
    return SecondAuctionScreen(parent, session)
