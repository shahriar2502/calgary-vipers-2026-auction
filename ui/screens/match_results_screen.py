"""Match Results screen (September 2026): post-auction match-score entry
and the transfer-budget tracker derived from it.

GUI only, and deliberately independent of the first-auction transaction
screens (Live Auction, Teams, Auction History) — every rule enforced here
lives in `services/match_result_service.py` / `AuctionSession`'s own
`add_match_result`/`update_match_result`/`delete_match_result` methods,
never inline in this file. Nothing here ever calls `process_sale`/
`process_unsold`/`place_bid`, and nothing here mutates `Team.remaining_
budget` directly — see PROJECT_CONTEXT.md's "POST-AUCTION MATCH RESULTS +
TRANSFER BUDGET TRACKER".
"""

from __future__ import annotations

import customtkinter as ctk

from models.auction import AuctionStatus
from models.match_result import MatchResult
from services import match_result_service as mrs
from services.auction_session_service import AuctionSession
from services.match_result_service import MatchResultError, TeamBudgetLedger
from ui import theme

_PLACEHOLDER = "—"


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


class MatchResultsScreen(ctk.CTkFrame):
    """Organizer match-score entry + the transfer-budget tracker it feeds.

    Visible before the first auction ends, but official match-money
    accounting is gated on `session.auction.status == COMPLETE` — see
    `_render`'s dispatch below, which mirrors the same "no session /
    in-progress / blocked / active" pattern Reports and Auction History
    already use."""

    def __init__(self, parent: ctk.CTkBaseClass, session: AuctionSession | None = None) -> None:
        super().__init__(parent, fg_color=theme.BACKGROUND, corner_radius=0)
        self._session = session

        self._match_number_var = ctk.StringVar(value="1")
        self._team1_var = ctk.StringVar(value="")
        self._team2_var = ctk.StringVar(value="")
        self._team1_goals_var = ctk.StringVar(value="")
        self._team2_goals_var = ctk.StringVar(value="")
        self._form_error: str | None = None
        self._editing_match_id: str | None = None
        # Debounce: guards against a genuine rapid double-click firing the
        # SAVE handler twice before this screen has a chance to re-render
        # (see PROJECT_CONTEXT.md's "do not create duplicate records
        # merely because the organizer double-clicked SAVE RESULT").
        self._save_in_flight = False
        self._pending_delete: MatchResult | None = None

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
    # Gating states
    # ------------------------------------------------------------------

    def _build_header(self, subtitle: str) -> None:
        ctk.CTkLabel(
            self, text="MATCH RESULTS", font=theme.heading_font(size=26), text_color=theme.TEXT_PRIMARY, anchor="w"
        ).grid(row=0, column=0, sticky="w", padx=32, pady=(28, 4))
        ctk.CTkLabel(
            self, text=subtitle, font=theme.body_font(size=14), text_color=theme.TEXT_SECONDARY, anchor="w",
        ).grid(row=1, column=0, sticky="w", padx=32, pady=(0, 24))

    def _build_no_session_state(self) -> None:
        self.grid_rowconfigure(0, weight=0)
        self._build_header("Post-auction match scores and the transfer budget they earn each team.")
        ctk.CTkLabel(
            self, text="NO AUCTION SESSION", font=theme.heading_font(size=18), text_color=theme.TEXT_SECONDARY, anchor="w"
        ).grid(row=2, column=0, sticky="w", padx=32)
        ctk.CTkLabel(
            self,
            text="Start or resume an auction before recording tournament matches.",
            font=theme.body_font(size=13),
            text_color=theme.TEXT_SECONDARY,
            anchor="w",
        ).grid(row=3, column=0, sticky="w", padx=32, pady=(4, 0))

    def _build_in_progress_state(self) -> None:
        self.grid_rowconfigure(0, weight=0)
        self._build_header("Post-auction match scores and the transfer budget they earn each team.")
        ctk.CTkLabel(
            self, text="FIRST AUCTION IN PROGRESS", font=theme.heading_font(size=18), text_color=theme.GOLD_ACCENT, anchor="w"
        ).grid(row=2, column=0, sticky="w", padx=32)
        ctk.CTkLabel(
            self,
            text="Match-result entry is unavailable until the initial auction is completed.",
            font=theme.body_font(size=13),
            text_color=theme.TEXT_SECONDARY,
            anchor="w",
        ).grid(row=3, column=0, sticky="w", padx=32, pady=(4, 0))

    def _build_blocked_state(self) -> None:
        self.grid_rowconfigure(0, weight=0)
        self._build_header("Post-auction match scores and the transfer budget they earn each team.")
        ctk.CTkLabel(
            self, text="FIRST AUCTION BLOCKED", font=theme.heading_font(size=18), text_color=theme.UNSOLD_RED, anchor="w"
        ).grid(row=2, column=0, sticky="w", padx=32)
        ctk.CTkLabel(
            self,
            text=(
                "A blocked auction is not a completed first auction. Match-money accounting "
                "requires a completed auction, or an explicitly supported recovery workflow."
            ),
            font=theme.body_font(size=13),
            text_color=theme.TEXT_SECONDARY,
            anchor="w",
            justify="left",
            wraplength=760,
        ).grid(row=3, column=0, sticky="w", padx=32, pady=(4, 0))

    # ------------------------------------------------------------------
    # Active view (auction COMPLETE)
    # ------------------------------------------------------------------

    def _build_active_view(self) -> None:
        self._build_header("Enter match scores; each team's transfer budget updates automatically below.")

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

        self._build_match_entry_section(body).grid(row=next_row, column=0, sticky="ew", pady=(0, 16))
        next_row += 1
        self._build_match_history_section(body).grid(row=next_row, column=0, sticky="ew", pady=(0, 16))
        next_row += 1
        self._build_budget_tracker_section(body).grid(row=next_row, column=0, sticky="ew", pady=(0, 16))
        next_row += 1
        self._build_money_summary_section(body).grid(row=next_row, column=0, sticky="ew")

    # ------------------------------------------------------------------
    # A. MATCH ENTRY
    # ------------------------------------------------------------------

    def _build_match_entry_section(self, parent: ctk.CTkBaseClass) -> ctk.CTkFrame:
        session = self._session
        section = _build_section(parent, "Match Entry" if self._editing_match_id is None else "Edit Match Result")
        team_names = [team.name for team in session.teams]

        if not self._team1_var.get() or self._team1_var.get() not in team_names:
            self._team1_var.set(team_names[0])
        if not self._team2_var.get() or self._team2_var.get() not in team_names:
            self._team2_var.set(team_names[1] if len(team_names) > 1 else team_names[0])

        if self._editing_match_id is not None:
            ctk.CTkLabel(
                section,
                text=f"Editing Match #{self._match_number_var.get()} — corrections replace the original award, never add to it.",
                font=theme.body_font(size=12, weight="bold"),
                text_color=theme.GOLD_ACCENT,
                anchor="w",
                wraplength=900,
            ).grid(row=1, column=0, sticky="w", padx=18, pady=(0, 10))

        fields = ctk.CTkFrame(section, fg_color="transparent")
        fields.grid(row=2, column=0, sticky="ew", padx=18, pady=(0, 6))

        def _labeled(col: int, label: str, widget_builder) -> None:
            block = ctk.CTkFrame(fields, fg_color="transparent")
            block.grid(row=0, column=col, sticky="w", padx=(0, 18))
            ctk.CTkLabel(
                block, text=label, font=theme.body_font(size=11, weight="bold"), text_color=theme.TEXT_SECONDARY, anchor="w"
            ).pack(anchor="w")
            widget_builder(block).pack(anchor="w", pady=(4, 0))

        _labeled(0, "MATCH NUMBER", lambda p: ctk.CTkEntry(
            p, textvariable=self._match_number_var, width=90, fg_color=theme.SURFACE_ALT,
            border_color=theme.BORDER, text_color=theme.TEXT_PRIMARY,
        ))
        _labeled(1, "TEAM 1", lambda p: ctk.CTkOptionMenu(
            p, values=team_names, variable=self._team1_var, width=170, fg_color=theme.SURFACE_ALT,
            button_color=theme.BORDER, button_hover_color=theme.SURFACE, text_color=theme.TEXT_PRIMARY,
            dropdown_fg_color=theme.SURFACE_ALT,
        ))
        _labeled(2, "TEAM 1 GOALS", lambda p: ctk.CTkEntry(
            p, textvariable=self._team1_goals_var, width=80, fg_color=theme.SURFACE_ALT,
            border_color=theme.BORDER, text_color=theme.TEXT_PRIMARY, placeholder_text="e.g. 3",
        ))
        _labeled(3, "TEAM 2", lambda p: ctk.CTkOptionMenu(
            p, values=team_names, variable=self._team2_var, width=170, fg_color=theme.SURFACE_ALT,
            button_color=theme.BORDER, button_hover_color=theme.SURFACE, text_color=theme.TEXT_PRIMARY,
            dropdown_fg_color=theme.SURFACE_ALT,
        ))
        _labeled(4, "TEAM 2 GOALS", lambda p: ctk.CTkEntry(
            p, textvariable=self._team2_goals_var, width=80, fg_color=theme.SURFACE_ALT,
            border_color=theme.BORDER, text_color=theme.TEXT_PRIMARY, placeholder_text="e.g. 1",
        ))

        button_row = ctk.CTkFrame(section, fg_color="transparent")
        button_row.grid(row=3, column=0, sticky="w", padx=18, pady=(10, 16))
        ctk.CTkButton(
            button_row,
            text="UPDATE RESULT" if self._editing_match_id is not None else "SAVE RESULT",
            font=theme.body_font(size=13, weight="bold"),
            fg_color=theme.ACCENT_GREEN,
            hover_color=theme.ACCENT_GREEN_HOVER,
            text_color=theme.BACKGROUND,
            width=150,
            height=36,
            corner_radius=8,
            state="disabled" if self._save_in_flight else "normal",
            command=self._on_save_result_clicked,
        ).pack(side="left")
        if self._editing_match_id is not None:
            ctk.CTkButton(
                button_row,
                text="CANCEL EDIT",
                font=theme.body_font(size=13, weight="bold"),
                fg_color=theme.SURFACE_ALT,
                hover_color=theme.BORDER,
                text_color=theme.TEXT_PRIMARY,
                width=120,
                height=36,
                corner_radius=8,
                command=self._on_cancel_edit_clicked,
            ).pack(side="left", padx=(10, 0))

        return section

    def _read_goals(self, text: str) -> object:
        """Returns an int, or the original stripped text (so the shared
        validator can produce a specific "not a whole number" message)
        when it isn't a clean integer. Blank stays blank."""
        stripped = text.strip()
        if not stripped:
            return None
        try:
            return int(stripped)
        except ValueError:
            return stripped

    def _on_save_result_clicked(self) -> None:
        if self._save_in_flight:
            return
        self._save_in_flight = True
        try:
            session = self._session
            team_by_name = {team.name: team for team in session.teams}
            team1 = team_by_name.get(self._team1_var.get())
            team2 = team_by_name.get(self._team2_var.get())
            if team1 is None or team2 is None:
                self._form_error = "Select two tournament teams."
                self._render()
                return

            try:
                match_number = int(self._match_number_var.get().strip())
            except ValueError:
                self._form_error = "Match number must be a whole number."
                self._render()
                return

            goals1 = self._read_goals(self._team1_goals_var.get())
            goals2 = self._read_goals(self._team2_goals_var.get())

            try:
                if self._editing_match_id is not None:
                    session.update_match_result(
                        self._editing_match_id, match_number, team1.id, team2.id, goals1, goals2
                    )
                else:
                    session.add_match_result(match_number, team1.id, team2.id, goals1, goals2)
            except MatchResultError as exc:
                self._form_error = str(exc)
                self._render()
                return

            self._form_error = None
            self._editing_match_id = None
            self._team1_goals_var.set("")
            self._team2_goals_var.set("")
            next_number = max((m.match_number for m in session.match_results), default=0) + 1
            self._match_number_var.set(str(next_number))
            self._render()
        finally:
            self._save_in_flight = False

    def _on_edit_clicked(self, match: MatchResult) -> None:
        session = self._session
        team_by_id = {team.id: team for team in session.teams}
        self._editing_match_id = match.id
        self._match_number_var.set(str(match.match_number))
        self._team1_var.set(team_by_id[match.team1_id].name if match.team1_id in team_by_id else "")
        self._team2_var.set(team_by_id[match.team2_id].name if match.team2_id in team_by_id else "")
        self._team1_goals_var.set(str(match.team1_goals))
        self._team2_goals_var.set(str(match.team2_goals))
        self._form_error = None
        self._render()

    def _on_cancel_edit_clicked(self) -> None:
        self._editing_match_id = None
        self._team1_goals_var.set("")
        self._team2_goals_var.set("")
        self._form_error = None
        self._render()

    # ------------------------------------------------------------------
    # B. SAVED MATCH HISTORY
    # ------------------------------------------------------------------

    def _build_match_history_section(self, parent: ctk.CTkBaseClass) -> ctk.CTkFrame:
        session = self._session
        section = _build_section(parent, "Saved Match History")
        team_by_id = {team.id: team for team in session.teams}

        results = sorted(session.match_results, key=lambda m: m.match_number)
        if not results:
            ctk.CTkLabel(
                section, text="No match results recorded yet.", font=theme.body_font(size=13),
                text_color=theme.TEXT_SECONDARY, anchor="w",
            ).grid(row=1, column=0, sticky="w", padx=18, pady=(0, 16))
            return section

        columns = [
            ("Match #", 70), ("Team 1", 150), ("Score", 70), ("Team 2", 150),
            ("Result", 220), ("T1 Earnings", 90), ("T2 Earnings", 90), ("", 130),
        ]
        header = ctk.CTkFrame(section, fg_color=theme.SURFACE_ALT, corner_radius=6)
        header.grid(row=1, column=0, sticky="ew", padx=18)
        for index, (title, width) in enumerate(columns):
            header.grid_columnconfigure(index, minsize=width)
            ctk.CTkLabel(
                header, text=title.upper(), font=theme.body_font(size=11, weight="bold"),
                text_color=theme.TEXT_SECONDARY, anchor="w",
            ).grid(row=0, column=index, sticky="w", padx=(12 if index == 0 else 4, 4), pady=8)

        rows_container = ctk.CTkFrame(section, fg_color="transparent")
        rows_container.grid(row=2, column=0, sticky="ew", padx=18, pady=(0, 16))

        for row_index, match in enumerate(results):
            self._build_match_history_row(rows_container, match, team_by_id, columns, row_index).grid(
                row=row_index, column=0, sticky="ew", pady=2
            )
        return section

    def _build_match_history_row(
        self, parent: ctk.CTkBaseClass, match: MatchResult, team_by_id: dict, columns: list, row_index: int
    ) -> ctk.CTkFrame:
        team1 = team_by_id.get(match.team1_id)
        team2 = team_by_id.get(match.team2_id)
        team1_name = team1.name if team1 is not None else "Unknown team"
        team2_name = team2.name if team2 is not None else "Unknown team"
        awards = match.awards()

        if match.is_draw:
            result_text = "Draw"
            result_color = theme.GOLD_ACCENT
        else:
            winner_name = team1_name if match.winner_id == match.team1_id else team2_name
            result_text = f"{winner_name} win"
            result_color = theme.ACCENT_GREEN

        row = ctk.CTkFrame(parent, fg_color=theme.SURFACE if row_index % 2 else "transparent", corner_radius=4)
        for index, (_title, width) in enumerate(columns):
            row.grid_columnconfigure(index, minsize=width)

        values = [
            (f"#{match.match_number}", theme.TEXT_SECONDARY),
            (team1_name, theme.TEXT_PRIMARY),
            (f"{match.team1_goals}–{match.team2_goals}", theme.TEXT_PRIMARY),
            (team2_name, theme.TEXT_PRIMARY),
            (result_text, result_color),
            (_money(awards.get(match.team1_id, 0)), theme.GOLD_ACCENT),
            (_money(awards.get(match.team2_id, 0)), theme.GOLD_ACCENT),
        ]
        for index, (text, color) in enumerate(values):
            ctk.CTkLabel(
                row, text=text, font=theme.body_font(size=13, weight="bold" if index == 4 else "normal"),
                text_color=color, anchor="w",
            ).grid(row=0, column=index, sticky="w", padx=(12 if index == 0 else 4, 4), pady=6)

        actions = ctk.CTkFrame(row, fg_color="transparent")
        actions.grid(row=0, column=len(values), sticky="w", padx=4, pady=4)
        ctk.CTkButton(
            actions, text="EDIT", width=56, height=26, corner_radius=6, font=theme.body_font(size=11, weight="bold"),
            fg_color=theme.SURFACE_ALT, hover_color=theme.BORDER, text_color=theme.TEXT_PRIMARY,
            command=lambda: self._on_edit_clicked(match),
        ).pack(side="left", padx=(0, 6))
        ctk.CTkButton(
            actions, text="DELETE", width=64, height=26, corner_radius=6, font=theme.body_font(size=11, weight="bold"),
            fg_color=theme.UNSOLD_RED, hover_color="#c93f43", text_color=theme.TEXT_PRIMARY,
            command=lambda: self._on_delete_clicked(match),
        ).pack(side="left")
        return row

    def _on_delete_clicked(self, match: MatchResult) -> None:
        dialog = ctk.CTkToplevel(self)
        dialog.title("Delete Match Result")
        dialog.configure(fg_color=theme.BACKGROUND)
        dialog.transient(self.winfo_toplevel())
        dialog.protocol("WM_DELETE_WINDOW", dialog.destroy)

        content = ctk.CTkFrame(dialog, fg_color="transparent")
        content.pack(fill="both", expand=True, padx=28, pady=24)

        ctk.CTkLabel(
            content, text="Delete this match result?", font=theme.heading_font(size=16), text_color=theme.TEXT_PRIMARY
        ).pack(pady=(0, 10))
        ctk.CTkLabel(
            content,
            text=f"Match #{match.match_number} — {match.team1_goals}–{match.team2_goals}. "
            "Its match-money award will be removed from both teams' budgets.",
            font=theme.body_font(size=12),
            text_color=theme.TEXT_SECONDARY,
            wraplength=360,
            justify="center",
        ).pack(pady=(0, 16))

        button_row = ctk.CTkFrame(content, fg_color="transparent")
        button_row.pack()
        ctk.CTkButton(
            button_row, text="Delete", width=112, height=36, corner_radius=8, fg_color=theme.UNSOLD_RED,
            text_color=theme.TEXT_PRIMARY, command=lambda: self._confirm_delete(match, dialog),
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

    def _confirm_delete(self, match: MatchResult, dialog: ctk.CTkToplevel) -> None:
        dialog.destroy()
        try:
            self._session.delete_match_result(match.id)
        except MatchResultError as exc:
            self._form_error = str(exc)
        else:
            self._form_error = None
            if self._editing_match_id == match.id:
                self._editing_match_id = None
        self._render()

    # ------------------------------------------------------------------
    # C. TRANSFER BUDGET TRACKER
    # ------------------------------------------------------------------

    def _build_budget_tracker_section(self, parent: ctk.CTkBaseClass) -> ctk.CTkFrame:
        session = self._session
        section = _build_section(parent, "Transfer Budget Tracker")

        ledgers = mrs.build_all_ledgers(session.teams, session.match_results)
        cards_row = ctk.CTkFrame(section, fg_color="transparent")
        cards_row.grid(row=1, column=0, sticky="ew", padx=18, pady=(0, 16))
        for index, ledger in enumerate(ledgers):
            cards_row.grid_columnconfigure(index, weight=1)
            self._build_team_ledger_card(cards_row, ledger).grid(row=0, column=index, sticky="new", padx=6)
        return section

    def _build_team_ledger_card(self, parent: ctk.CTkBaseClass, ledger: TeamBudgetLedger) -> ctk.CTkFrame:
        card = ctk.CTkFrame(parent, fg_color=theme.SURFACE_ALT, corner_radius=8)
        ctk.CTkLabel(
            card, text=ledger.team.name.upper(), font=theme.heading_font(size=14), text_color=theme.TEXT_PRIMARY, anchor="w"
        ).pack(anchor="w", padx=14, pady=(12, 6))

        for label, value, color in [
            ("First Auction Remaining", f"{ledger.first_auction_remaining}M", theme.TEXT_PRIMARY),
            ("Match Earnings", _money(ledger.total_match_earnings), theme.GOLD_ACCENT),
        ]:
            row = ctk.CTkFrame(card, fg_color="transparent")
            row.pack(fill="x", padx=14, pady=1)
            ctk.CTkLabel(row, text=label, font=theme.body_font(size=11), text_color=theme.TEXT_SECONDARY, anchor="w").pack(side="left")
            ctk.CTkLabel(row, text=value, font=theme.body_font(size=12, weight="bold"), text_color=color, anchor="e").pack(side="right")

        ctk.CTkFrame(card, fg_color=theme.BORDER, height=1, corner_radius=0).pack(fill="x", padx=14, pady=8)

        ctk.CTkLabel(
            card, text="AVAILABLE TRANSFER BUDGET", font=theme.body_font(size=10, weight="bold"),
            text_color=theme.TEXT_SECONDARY, anchor="w",
        ).pack(anchor="w", padx=14)
        ctk.CTkLabel(
            card, text=f"{ledger.current_transfer_budget}M", font=theme.heading_font(size=24), text_color=theme.ACCENT_GREEN, anchor="w",
        ).pack(anchor="w", padx=14, pady=(0, 14))
        return card

    # ------------------------------------------------------------------
    # D. MATCH MONEY SUMMARY
    # ------------------------------------------------------------------

    def _build_money_summary_section(self, parent: ctk.CTkBaseClass) -> ctk.CTkFrame:
        session = self._session
        section = _build_section(parent, "Match Money Summary")

        ledgers = mrs.build_all_ledgers(session.teams, session.match_results)
        columns = [("Team", 170), ("Played", 70), ("Wins", 70), ("Draws", 70), ("Losses", 70), ("Total Earnings", 110)]
        header = ctk.CTkFrame(section, fg_color=theme.SURFACE_ALT, corner_radius=6)
        header.grid(row=1, column=0, sticky="ew", padx=18)
        for index, (title, width) in enumerate(columns):
            header.grid_columnconfigure(index, minsize=width)
            ctk.CTkLabel(
                header, text=title.upper(), font=theme.body_font(size=11, weight="bold"),
                text_color=theme.TEXT_SECONDARY, anchor="w",
            ).grid(row=0, column=index, sticky="w", padx=(12 if index == 0 else 4, 4), pady=8)

        rows_container = ctk.CTkFrame(section, fg_color="transparent")
        rows_container.grid(row=2, column=0, sticky="ew", padx=18, pady=(0, 16))
        for row_index, ledger in enumerate(ledgers):
            row = ctk.CTkFrame(rows_container, fg_color=theme.SURFACE if row_index % 2 else "transparent", corner_radius=4)
            row.grid(row=row_index, column=0, sticky="ew", pady=2)
            for index, (_title, width) in enumerate(columns):
                row.grid_columnconfigure(index, minsize=width)
            values = [
                ledger.team.name, str(ledger.matches_played), str(ledger.wins), str(ledger.draws), str(ledger.losses),
                _money(ledger.total_match_earnings),
            ]
            for index, text in enumerate(values):
                ctk.CTkLabel(
                    row, text=text, font=theme.body_font(size=13), text_color=theme.TEXT_PRIMARY, anchor="w",
                ).grid(row=0, column=index, sticky="w", padx=(12 if index == 0 else 4, 4), pady=6)
        return section


def build_match_results_screen(parent: ctk.CTkBaseClass, session: AuctionSession | None = None) -> MatchResultsScreen:
    return MatchResultsScreen(parent, session)
