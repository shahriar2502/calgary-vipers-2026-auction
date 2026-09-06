"""Reports screen: a read-only auction summary/analytics dashboard for the
currently loaded MOCK/LIVE session.

Deliberately distinct from Auction History (the chronological event-log
screen): Reports shows aggregated statistics, per-team summaries, and
comparisons, never a row-per-transaction timeline. All calculations live
in services/report_service.py — this module only renders the resulting
plain dataclasses. Nothing here mutates a Player/Team/Auction object.
"""

from __future__ import annotations

import json

import customtkinter as ctk

from models.player import Position
from services import report_service as rs
from services.auction_session_service import AuctionSession
from services.config_service import load_position_colors
from ui import theme
from ui.widgets import captain_badge, pill_badge, position_badge

TEAMS_PER_ROW = 2

_STATUS_COLORS = {
    "IN_PROGRESS": theme.ACCENT_GREEN,
    "COMPLETE": theme.GOLD_ACCENT,
    "BLOCKED": theme.UNSOLD_RED,
    "NOT_STARTED": theme.SURFACE_ALT,
}


def _money(value: int) -> str:
    return f"{value}M"


def _optional_money(value: int | float | None) -> str:
    if value is None:
        return "N/A"
    if isinstance(value, float):
        return f"{value:.1f}M"
    return f"{value}M"


def _optional_number(value: float | None, digits: int = 1) -> str:
    return "N/A" if value is None else f"{value:.{digits}f}"


# ----------------------------------------------------------------------
# Small reusable building blocks
# ----------------------------------------------------------------------


def _build_kpi_card(parent: ctk.CTkBaseClass, label: str, value: object) -> ctk.CTkFrame:
    card = ctk.CTkFrame(parent, fg_color=theme.SURFACE, corner_radius=8)
    ctk.CTkLabel(
        card, text=str(value), font=theme.heading_font(size=22), text_color=theme.TEXT_PRIMARY
    ).pack(padx=16, pady=(12, 0))
    ctk.CTkLabel(
        card, text=label, font=theme.body_font(size=12), text_color=theme.TEXT_SECONDARY
    ).pack(padx=16, pady=(0, 12))
    return card


def _build_mini_stat(parent: ctk.CTkBaseClass, label: str, value: str) -> ctk.CTkFrame:
    block = ctk.CTkFrame(parent, fg_color="transparent")
    ctk.CTkLabel(
        block, text=value, font=theme.heading_font(size=16), text_color=theme.TEXT_PRIMARY, anchor="w"
    ).pack(anchor="w")
    ctk.CTkLabel(
        block, text=label, font=theme.body_font(size=11), text_color=theme.TEXT_SECONDARY, anchor="w"
    ).pack(anchor="w")
    return block


def _build_section_label(parent: ctk.CTkBaseClass, text: str) -> ctk.CTkLabel:
    return ctk.CTkLabel(
        parent, text=text.upper(), font=theme.body_font(size=13, weight="bold"), text_color=theme.TEXT_PRIMARY, anchor="w"
    )


def _build_comparison_bar(
    parent: ctk.CTkBaseClass, name: str, value_text: str, fraction: float, color: str
) -> ctk.CTkFrame:
    row = ctk.CTkFrame(parent, fg_color="transparent")
    row.grid_columnconfigure(1, weight=1)

    ctk.CTkLabel(
        row, text=name, font=theme.body_font(size=12), text_color=theme.TEXT_SECONDARY, anchor="w", width=130
    ).grid(row=0, column=0, sticky="w")

    track = ctk.CTkFrame(row, fg_color=theme.SURFACE_ALT, height=14, corner_radius=4)
    track.grid(row=0, column=1, sticky="ew", padx=(8, 8))
    track.grid_propagate(False)
    clamped_fraction = max(0.0, min(fraction, 1.0))
    if clamped_fraction > 0:
        ctk.CTkFrame(track, fg_color=color, corner_radius=4).place(relx=0, rely=0, relwidth=clamped_fraction, relheight=1)

    ctk.CTkLabel(
        row, text=value_text, font=theme.body_font(size=12, weight="bold"), text_color=theme.TEXT_PRIMARY, anchor="e", width=70
    ).grid(row=0, column=2, sticky="e")
    return row


def _build_highlight_card(
    parent: ctk.CTkBaseClass,
    title: str,
    entries,
    value_label: str,
    position_colors: dict[str, str],
) -> ctk.CTkFrame:
    """`entries` is the list of tied AuctionHistoryEntry (SOLD only) sharing
    the extreme value being highlighted — empty means nothing SOLD yet."""
    card = ctk.CTkFrame(parent, fg_color=theme.SURFACE, corner_radius=8)
    card.grid_columnconfigure(0, weight=1)

    ctk.CTkLabel(
        card, text=title.upper(), font=theme.body_font(size=11, weight="bold"), text_color=theme.TEXT_SECONDARY, anchor="w"
    ).grid(row=0, column=0, sticky="w", padx=16, pady=(14, 4))

    if not entries:
        ctk.CTkLabel(
            card, text="N/A", font=theme.heading_font(size=18), text_color=theme.TEXT_SECONDARY, anchor="w"
        ).grid(row=1, column=0, sticky="w", padx=16, pady=(0, 14))
        return card

    first = entries[0]
    name_text = first.player_name if len(entries) == 1 else f"{len(entries)} players tied"
    ctk.CTkLabel(
        card, text=name_text, font=theme.heading_font(size=17), text_color=theme.TEXT_PRIMARY, anchor="w"
    ).grid(row=1, column=0, sticky="w", padx=16)

    detail_row = ctk.CTkFrame(card, fg_color="transparent")
    detail_row.grid(row=2, column=0, sticky="w", padx=16, pady=(6, 4))
    position_badge(detail_row, Position(first.position), position_colors).pack(side="left")
    ctk.CTkLabel(
        detail_row, text=f"OVR {first.overall_rating}", font=theme.body_font(size=12), text_color=theme.TEXT_SECONDARY
    ).pack(side="left", padx=(8, 0))

    team_text = first.team if len(entries) == 1 else "Tied — see below"
    ctk.CTkLabel(
        card, text=team_text, font=theme.body_font(size=12), text_color=theme.TEXT_SECONDARY, anchor="w"
    ).grid(row=3, column=0, sticky="w", padx=16)

    ctk.CTkLabel(
        card, text=value_label, font=theme.body_font(size=15, weight="bold"), text_color=theme.GOLD_ACCENT, anchor="w"
    ).grid(row=4, column=0, sticky="w", padx=16, pady=(2, 8))

    if len(entries) > 1:
        ctk.CTkLabel(
            card,
            text="Tied: " + ", ".join(f"{entry.player_name} ({entry.team})" for entry in entries),
            font=theme.body_font(size=11),
            text_color=theme.TEXT_SECONDARY,
            anchor="w",
            justify="left",
            wraplength=260,
        ).grid(row=5, column=0, sticky="w", padx=16, pady=(0, 12))
    else:
        ctk.CTkFrame(card, fg_color="transparent", height=4).grid(row=5, column=0)

    return card


def _build_roster_row(
    parent: ctk.CTkBaseClass, entry: rs.RosterEntry, position_colors: dict[str, str]
) -> ctk.CTkFrame:
    row = ctk.CTkFrame(parent, fg_color="transparent")
    row.grid_columnconfigure(0, weight=1)

    ctk.CTkLabel(
        row, text=entry.player.full_name, font=theme.body_font(size=13), text_color=theme.TEXT_PRIMARY, anchor="w"
    ).grid(row=0, column=0, sticky="w")
    position_badge(row, entry.player.position, position_colors).grid(row=0, column=1, sticky="e", padx=(8, 4))
    ctk.CTkLabel(
        row,
        text=str(entry.player.overall_rating),
        font=theme.body_font(size=12, weight="bold"),
        text_color=theme.TEXT_SECONDARY,
        width=28,
        anchor="e",
    ).grid(row=0, column=2, sticky="e", padx=(0, 8))

    if entry.role == rs.CAPTAIN_ROLE:
        price_text = "CAPTAIN"
        price_color = theme.ACCENT_GREEN
    else:
        price_text = _money(entry.price) if entry.price is not None else "—"
        price_color = theme.GOLD_ACCENT

    ctk.CTkLabel(
        row, text=price_text, font=theme.body_font(size=12, weight="bold"), text_color=price_color, width=64, anchor="e"
    ).grid(row=0, column=3, sticky="e")
    return row


def _build_team_panel(
    parent: ctk.CTkBaseClass, team_report: rs.TeamReport, position_colors: dict[str, str]
) -> ctk.CTkFrame:
    card = ctk.CTkFrame(parent, fg_color=theme.SURFACE, corner_radius=10)
    card.grid_columnconfigure(0, weight=1)

    ctk.CTkLabel(
        card, text=team_report.team.name, font=theme.heading_font(size=19), text_color=theme.TEXT_PRIMARY, anchor="w"
    ).grid(row=0, column=0, sticky="w", padx=20, pady=(18, 4))

    captain_row = ctk.CTkFrame(card, fg_color="transparent")
    captain_row.grid(row=1, column=0, sticky="w", padx=20, pady=(0, 12))
    if team_report.captain is not None:
        ctk.CTkLabel(
            captain_row,
            text=team_report.captain.full_name,
            font=theme.body_font(size=14),
            text_color=theme.TEXT_SECONDARY,
        ).pack(side="left")
        captain_badge(captain_row).pack(side="left", padx=(10, 0))
    else:
        ctk.CTkLabel(
            captain_row,
            text="Captain could not be resolved",
            font=theme.body_font(size=13),
            text_color=theme.UNSOLD_RED,
        ).pack(side="left")

    # A 2-column grid (rather than one or two 4-column rows) — verified via
    # screenshot that 4 columns per row clips longer labels/values ("Max
    # Next Bid", a longer player name) at the app's 1024px minimum width,
    # since each team panel is itself already only ~half the window wide.
    stats_grid = ctk.CTkFrame(card, fg_color="transparent")
    stats_grid.grid(row=2, column=0, sticky="ew", padx=20, pady=(0, 14))
    stats_grid.grid_columnconfigure(0, weight=1)
    stats_grid.grid_columnconfigure(1, weight=1)
    stats = [
        ("Squad", f"{team_report.squad_size} / {team_report.max_squad_size}"),
        ("Total Spent", _money(team_report.total_spent)),
        ("Remaining Budget", _money(team_report.remaining_budget)),
        ("GK", "YES" if team_report.has_goalkeeper else "NO"),
        ("Purchases", str(team_report.purchases_count)),
        ("Avg Purchase", _optional_money(team_report.average_purchase_price)),
        ("Highest Purchase", team_report.highest_purchase_player.full_name if team_report.highest_purchase_player else "N/A"),
        ("Max Next Bid", _money(team_report.maximum_legal_bid)),
    ]
    for index, (label, value) in enumerate(stats):
        row, col = divmod(index, 2)
        _build_mini_stat(stats_grid, label, value).grid(
            row=row, column=col, sticky="w", padx=(0 if col == 0 else 10, 0), pady=(0 if row == 0 else 10, 0)
        )

    ctk.CTkFrame(card, fg_color=theme.BORDER, height=1, corner_radius=0).grid(
        row=3, column=0, sticky="ew", padx=20, pady=(0, 10)
    )

    ctk.CTkLabel(
        card, text="ROSTER", font=theme.body_font(size=11, weight="bold"), text_color=theme.TEXT_SECONDARY, anchor="w"
    ).grid(row=4, column=0, sticky="w", padx=20, pady=(0, 6))

    roster_container = ctk.CTkFrame(card, fg_color="transparent")
    roster_container.grid(row=5, column=0, sticky="ew", padx=20, pady=(0, 18))
    roster_container.grid_columnconfigure(0, weight=1)
    for index, entry in enumerate(team_report.roster):
        _build_roster_row(roster_container, entry, position_colors).grid(row=index, column=0, sticky="ew", pady=3)

    return card


class ReportsScreen(ctk.CTkFrame):
    """Read-only auction summary/analytics dashboard for the current
    session. Shows a plain empty state with no active session, and never
    mutates a Player/Team/Auction object."""

    def __init__(self, parent: ctk.CTkBaseClass, session: AuctionSession | None = None) -> None:
        super().__init__(parent, fg_color=theme.BACKGROUND, corner_radius=0)
        self._session = session

        self.grid_columnconfigure(0, weight=1)

        if session is None or not session.started:
            self._build_no_session_state()
            return

        try:
            self._position_colors = load_position_colors()
            self._report: rs.AuctionReport = rs.build_full_report(session)
        except (OSError, json.JSONDecodeError, ValueError, KeyError, TypeError) as exc:
            self._build_error_state(str(exc))
            return

        self.grid_rowconfigure(3, weight=1)
        self._build_title()
        self._build_context_row()
        self._build_body()

    # ------------------------------------------------------------------
    # Empty / error states
    # ------------------------------------------------------------------

    def _build_no_session_state(self) -> None:
        ctk.CTkLabel(
            self, text="REPORTS", font=theme.heading_font(size=26), text_color=theme.TEXT_PRIMARY, anchor="w"
        ).grid(row=0, column=0, sticky="w", padx=32, pady=(28, 4))
        ctk.CTkLabel(
            self,
            text="Auction summary and team analytics for the current session.",
            font=theme.body_font(size=14),
            text_color=theme.TEXT_SECONDARY,
            anchor="w",
        ).grid(row=1, column=0, sticky="w", padx=32, pady=(0, 24))
        ctk.CTkLabel(
            self, text="NO ACTIVE AUCTION SESSION", font=theme.heading_font(size=18), text_color=theme.TEXT_SECONDARY, anchor="w"
        ).grid(row=2, column=0, sticky="w", padx=32)
        ctk.CTkLabel(
            self,
            text="Start or resume a MOCK/LIVE auction to view reports.",
            font=theme.body_font(size=13),
            text_color=theme.TEXT_SECONDARY,
            anchor="w",
        ).grid(row=3, column=0, sticky="w", padx=32, pady=(4, 0))

    def _build_error_state(self, message: str) -> None:
        ctk.CTkLabel(
            self, text="REPORTS", font=theme.heading_font(size=26), text_color=theme.TEXT_PRIMARY, anchor="w"
        ).grid(row=0, column=0, sticky="w", padx=32, pady=(32, 8))
        ctk.CTkLabel(
            self, text="Tournament data could not be loaded.", font=theme.body_font(size=15, weight="bold"),
            text_color=theme.UNSOLD_RED, anchor="w",
        ).grid(row=1, column=0, sticky="w", padx=32)
        ctk.CTkLabel(
            self, text=message, font=theme.body_font(size=13), text_color=theme.TEXT_SECONDARY, anchor="w",
            justify="left", wraplength=760,
        ).grid(row=2, column=0, sticky="w", padx=32, pady=(8, 0))

    # ------------------------------------------------------------------
    # Header
    # ------------------------------------------------------------------

    def _build_title(self) -> None:
        ctk.CTkLabel(
            self, text="REPORTS", font=theme.heading_font(size=26), text_color=theme.TEXT_PRIMARY, anchor="w"
        ).grid(row=0, column=0, sticky="w", padx=32, pady=(28, 4))
        ctk.CTkLabel(
            self,
            text="Auction summary and team analytics for the current session.",
            font=theme.body_font(size=14),
            text_color=theme.TEXT_SECONDARY,
            anchor="w",
        ).grid(row=1, column=0, sticky="w", padx=32, pady=(0, 16))

    def _build_context_row(self) -> None:
        session = self._session
        row = ctk.CTkFrame(self, fg_color="transparent")
        row.grid(row=2, column=0, sticky="w", padx=32, pady=(0, 16))

        if session.mode is not None and session.mode.value == "LIVE":
            pill_badge(row, "LIVE SESSION", fg_color=theme.UNSOLD_RED, text_color=theme.TEXT_PRIMARY, width=118).pack(
                side="left", padx=(0, 10)
            )
        else:
            pill_badge(
                row, "MOCK SESSION", fg_color=theme.SURFACE_ALT, text_color=theme.TEXT_SECONDARY, width=118
            ).pack(side="left", padx=(0, 10))

        status = session.auction.status.value
        status_color = _STATUS_COLORS.get(status, theme.SURFACE_ALT)
        pill_badge(row, status, fg_color=status_color, text_color=theme.BACKGROUND, width=110).pack(
            side="left", padx=(0, 10)
        )

        round_label = "Round 1" if session.round_number == 1 else f"Re-Auction Round {session.round_number}"
        ctk.CTkLabel(
            row, text=round_label, font=theme.body_font(size=13, weight="bold"), text_color=theme.TEXT_PRIMARY
        ).pack(side="left", padx=(0, 16))

        ctk.CTkLabel(
            row,
            text=f"SOLD {session.sold_count} / {session.total_eligible_count}",
            font=theme.body_font(size=13),
            text_color=theme.TEXT_SECONDARY,
        ).pack(side="left", padx=(0, 16))

        if session.name:
            ctk.CTkLabel(
                row, text=session.name, font=theme.body_font(size=13), text_color=theme.TEXT_SECONDARY
            ).pack(side="left")

    # ------------------------------------------------------------------
    # Scrollable body
    # ------------------------------------------------------------------

    def _build_body(self) -> None:
        body = ctk.CTkScrollableFrame(self, fg_color=theme.BACKGROUND, corner_radius=0)
        body.grid(row=3, column=0, sticky="nsew", padx=32, pady=(0, 24))
        body.grid_columnconfigure(0, weight=1)
        self._body = body  # kept for tests/manual verification (scroll position, content lookup)

        self._build_kpi_row(body).grid(row=0, column=0, sticky="ew", pady=(0, 16))
        self._build_secondary_metrics(body).grid(row=1, column=0, sticky="ew", pady=(0, 20))
        self._build_highlights(body).grid(row=2, column=0, sticky="ew", pady=(0, 20))
        self._build_fpl_and_reauction(body).grid(row=3, column=0, sticky="ew", pady=(0, 20))
        self._build_comparison_section(body).grid(row=4, column=0, sticky="ew", pady=(0, 24))
        self._build_team_grid(body).grid(row=5, column=0, sticky="ew")

    def _build_kpi_row(self, parent: ctk.CTkBaseClass) -> ctk.CTkFrame:
        summary = self._report.summary
        row = ctk.CTkFrame(parent, fg_color="transparent")
        stats = [
            ("Total Sold", f"{summary.sold_count} / {summary.total_eligible}"),
            ("Total Spent", _money(summary.price_stats.total_spent)),
            ("Avg Sale", _optional_money(summary.price_stats.average_price)),
            ("Highest Sale", _optional_money(summary.price_stats.highest_price)),
            ("Rounds", str(summary.round_number)),
        ]
        for index, (label, value) in enumerate(stats):
            row.grid_columnconfigure(index, weight=1)
            _build_kpi_card(row, label, value).grid(row=0, column=index, sticky="ew", padx=(0 if index == 0 else 8, 0))
        return row

    def _build_secondary_metrics(self, parent: ctk.CTkBaseClass) -> ctk.CTkFrame:
        summary = self._report.summary
        section = ctk.CTkFrame(parent, fg_color=theme.SURFACE, corner_radius=8)
        _build_section_label(section, "Additional Metrics").grid(row=0, column=0, columnspan=4, sticky="w", padx=16, pady=(12, 8))

        stats = [
            ("Sold %", f"{summary.sold_percentage:.0f}%"),
            ("Remaining Unsold", str(summary.remaining_unsold_count)),
            ("Total Attempts", str(summary.total_attempts)),
            ("Median Sale", _optional_money(summary.price_stats.median_price)),
            ("Lowest Sale", _optional_money(summary.price_stats.lowest_price)),
            ("Teams Full", f"{summary.completed_team_count} / {summary.total_team_count}"),
            ("Combined Remaining Budget", _money(summary.combined_remaining_budget)),
        ]
        for index, (label, value) in enumerate(stats):
            row, col = divmod(index, 4)
            section.grid_columnconfigure(col, weight=1)
            _build_mini_stat(section, label, value).grid(
                row=row + 1, column=col, sticky="w", padx=16, pady=(0, 14)
            )
        return section

    def _build_highlights(self, parent: ctk.CTkBaseClass) -> ctk.CTkFrame:
        section = ctk.CTkFrame(parent, fg_color="transparent")
        _build_section_label(section, "Highlights").grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 8))

        price_stats = self._report.summary.price_stats
        ovr_stats = self._report.ovr_stats

        cards = [
            ("Most Expensive", price_stats.highest_price_entries, _optional_money(price_stats.highest_price)),
            ("Cheapest Sold", price_stats.lowest_price_entries, _optional_money(price_stats.lowest_price)),
            ("Highest-Rated Purchase", ovr_stats.highest_ovr_entries, f"OVR {ovr_stats.highest_ovr_value}" if ovr_stats.highest_ovr_value else "N/A"),
        ]
        for index, (title, entries, value_label) in enumerate(cards):
            section.grid_columnconfigure(index, weight=1)
            _build_highlight_card(section, title, entries, value_label, self._position_colors).grid(
                row=1, column=index, sticky="nsew", padx=(0 if index == 0 else 8, 0)
            )
        return section

    def _build_fpl_and_reauction(self, parent: ctk.CTkBaseClass) -> ctk.CTkFrame:
        section = ctk.CTkFrame(parent, fg_color="transparent")
        section.grid_columnconfigure(0, weight=1)
        section.grid_columnconfigure(1, weight=1)

        fpl = self._report.fpl_stats
        fpl_panel = ctk.CTkFrame(section, fg_color=theme.SURFACE, corner_radius=8)
        _build_section_label(fpl_panel, "FPL Analytics").grid(row=0, column=0, columnspan=2, sticky="w", padx=16, pady=(12, 8))
        fpl_stats_list = [
            ("Highest FPL Sold", fpl.highest_fpl_entries[0].player_name if fpl.highest_fpl_entries else "N/A"),
            ("Highest FPL Value", str(fpl.highest_fpl_value) if fpl.highest_fpl_value is not None else "N/A"),
            ("Avg FPL (known only)", _optional_number(fpl.average_fpl_of_known)),
            ("FPL N/A Count", str(fpl.na_count)),
        ]
        for index, (label, value) in enumerate(fpl_stats_list):
            row, col = divmod(index, 2)
            fpl_panel.grid_columnconfigure(col, weight=1)
            _build_mini_stat(fpl_panel, label, value).grid(row=row + 1, column=col, sticky="w", padx=16, pady=(0, 14))
        fpl_panel.grid(row=0, column=0, sticky="nsew", padx=(0, 8))

        reauction = self._report.summary.reauction_stats
        reauction_panel = ctk.CTkFrame(section, fg_color=theme.SURFACE, corner_radius=8)
        _build_section_label(reauction_panel, "Re-Auction Summary").grid(
            row=0, column=0, columnspan=2, sticky="w", padx=16, pady=(12, 8)
        )
        if not reauction.has_activity:
            ctk.CTkLabel(
                reauction_panel,
                text="No re-auction activity yet.",
                font=theme.body_font(size=13),
                text_color=theme.TEXT_SECONDARY,
                anchor="w",
            ).grid(row=1, column=0, columnspan=2, sticky="w", padx=16, pady=(0, 14))
        else:
            reauction_stats_list = [
                ("Unsold Attempts", str(reauction.unsold_attempts)),
                ("Unique Unsold Players", str(reauction.unique_unsold_players)),
                ("Eventually Sold", str(reauction.eventually_sold_after_unsold)),
                ("Rounds Reached", str(reauction.rounds_reached)),
            ]
            for index, (label, value) in enumerate(reauction_stats_list):
                row, col = divmod(index, 2)
                reauction_panel.grid_columnconfigure(col, weight=1)
                _build_mini_stat(reauction_panel, label, value).grid(row=row + 1, column=col, sticky="w", padx=16, pady=(0, 14))
        reauction_panel.grid(row=0, column=1, sticky="nsew", padx=(8, 0))

        return section

    def _build_comparison_section(self, parent: ctk.CTkBaseClass) -> ctk.CTkFrame:
        section = ctk.CTkFrame(parent, fg_color=theme.SURFACE, corner_radius=8)
        section.grid_columnconfigure(0, weight=1)
        section.grid_columnconfigure(1, weight=1)
        _build_section_label(section, "Team Comparison").grid(row=0, column=0, columnspan=2, sticky="w", padx=16, pady=(12, 10))

        team_reports = self._report.team_reports

        def _bars_group(title: str, values: list[tuple[str, float, str]]) -> ctk.CTkFrame:
            group = ctk.CTkFrame(section, fg_color="transparent")
            ctk.CTkLabel(
                group, text=title, font=theme.body_font(size=12, weight="bold"), text_color=theme.TEXT_SECONDARY, anchor="w"
            ).pack(anchor="w", pady=(0, 6))
            max_value = max((value for _name, value, _label in values), default=0) or 1
            for name, value, label in values:
                _build_comparison_bar(group, name, label, value / max_value, theme.ACCENT_GREEN).pack(
                    fill="x", pady=3
                )
            return group

        spending_values = [(tr.team.name, tr.total_spent, _money(tr.total_spent)) for tr in team_reports]
        budget_values = [(tr.team.name, tr.remaining_budget, _money(tr.remaining_budget)) for tr in team_reports]
        squad_values = [(tr.team.name, tr.squad_size, f"{tr.squad_size}/{tr.max_squad_size}") for tr in team_reports]
        avg_price_values = [
            (tr.team.name, tr.average_purchase_price or 0, _optional_money(tr.average_purchase_price))
            for tr in team_reports
        ]

        _bars_group("Team Spending", spending_values).grid(row=1, column=0, sticky="ew", padx=16, pady=(0, 16))
        _bars_group("Remaining Budget", budget_values).grid(row=1, column=1, sticky="ew", padx=16, pady=(0, 16))
        _bars_group("Squad Size", squad_values).grid(row=2, column=0, sticky="ew", padx=16, pady=(0, 16))
        _bars_group("Average Purchase Price", avg_price_values).grid(row=2, column=1, sticky="ew", padx=16, pady=(0, 16))

        return section

    def _build_team_grid(self, parent: ctk.CTkBaseClass) -> ctk.CTkFrame:
        grid = ctk.CTkFrame(parent, fg_color="transparent")
        for column in range(TEAMS_PER_ROW):
            grid.grid_columnconfigure(column, weight=1)

        for index, team_report in enumerate(self._report.team_reports):
            row, column = divmod(index, TEAMS_PER_ROW)
            panel = _build_team_panel(grid, team_report, self._position_colors)
            panel.grid(row=row, column=column, sticky="nsew", padx=8, pady=8)

        return grid


def build_reports_screen(parent: ctk.CTkBaseClass, session: AuctionSession | None = None) -> ctk.CTkFrame:
    return ReportsScreen(parent, session=session)
