"""Teams screen: a read-only view of the four auction teams, their
captains, budgets, squad capacity, and current rosters.

GUI only — all loading and roster-resolution logic lives in
services/team_service.py and services/config_service.py. This screen is
the visual foundation Live Auction will later refresh after each SOLD
player is added to a team; nothing here mutates Team/Player state.
"""

from __future__ import annotations

import json

import customtkinter as ctk

from models.player import Player
from models.second_auction import SecondAuctionStatus
from services import current_roster_service as crs
from services.auction_session_service import AuctionSession
from services.config_service import load_position_colors
from services.team_service import load_team_summaries
from ui import theme
from ui.widgets import captain_badge, position_badge, transfer_badge

TEAMS_PER_ROW = 2


def _build_stat_card(parent: ctk.CTkBaseClass, label: str, value: object) -> ctk.CTkFrame:
    card = ctk.CTkFrame(parent, fg_color=theme.SURFACE, corner_radius=8)
    ctk.CTkLabel(
        card,
        text=str(value),
        font=theme.heading_font(size=22),
        text_color=theme.TEXT_PRIMARY,
    ).pack(padx=16, pady=(12, 0))
    ctk.CTkLabel(
        card,
        text=label,
        font=theme.body_font(size=12),
        text_color=theme.TEXT_SECONDARY,
    ).pack(padx=16, pady=(0, 12))
    return card


def _build_mini_stat(parent: ctk.CTkBaseClass, label: str, value: str) -> ctk.CTkFrame:
    block = ctk.CTkFrame(parent, fg_color="transparent")
    ctk.CTkLabel(
        block, text=value, font=theme.heading_font(size=17), text_color=theme.TEXT_PRIMARY, anchor="w"
    ).pack(anchor="w")
    ctk.CTkLabel(
        block, text=label, font=theme.body_font(size=11), text_color=theme.TEXT_SECONDARY, anchor="w"
    ).pack(anchor="w")
    return block


def _build_roster_row(
    parent: ctk.CTkBaseClass, player: Player, position_colors: dict[str, str], is_new_transfer: bool = False
) -> ctk.CTkFrame:
    row = ctk.CTkFrame(parent, fg_color="transparent")
    row.grid_columnconfigure(0, weight=1)

    ctk.CTkLabel(
        row, text=player.full_name, font=theme.body_font(size=13), text_color=theme.TEXT_PRIMARY, anchor="w"
    ).grid(row=0, column=0, sticky="w")
    position_badge(row, player.position, position_colors).grid(row=0, column=1, sticky="e", padx=(8, 4))
    next_column = 2
    if player.is_captain:
        captain_badge(row).grid(row=0, column=next_column, sticky="e")
        next_column += 1
    # Second Auction — Milestone 3: a player bought via the second
    # auction, derived purely from SecondAuctionState.history (see
    # services/current_roster_service.is_player_new_transfer) — never a
    # mutated historical field, and never shown for a captain (captains
    # are never released/purchased in either auction).
    if is_new_transfer:
        transfer_badge(row).grid(row=0, column=next_column, sticky="e", padx=(4, 0))
    return row


def _build_unresolved_roster_row(parent: ctk.CTkBaseClass, player_id: int) -> ctk.CTkFrame:
    row = ctk.CTkFrame(parent, fg_color="transparent")
    ctk.CTkLabel(
        row,
        text=f"Unknown player id {player_id}",
        font=theme.body_font(size=13),
        text_color=theme.UNSOLD_RED,
        anchor="w",
    ).pack(anchor="w")
    return row


def _build_team_card(
    parent: ctk.CTkBaseClass,
    summary,
    position_colors: dict[str, str],
    stats: list[tuple[str, str]],
    is_new_transfer=lambda player_id: False,
) -> ctk.CTkFrame:
    """`summary` may be either `services/team_service.py`'s `TeamSummary`
    (canonical/first-auction-only, no active session) or `services/
    current_roster_service.py`'s `CurrentTeamSummary` (phase-aware, once
    a session is active) — this function only ever touches the handful
    of fields both share (`team`/`captain`/`roster_players`/
    `unresolved_roster_ids`), with every phase-specific stat already
    precomputed into `stats` by the caller (see `TeamsScreen._build_team_
    grid`), so it never needs to know which summary type it was given."""
    card = ctk.CTkFrame(parent, fg_color=theme.SURFACE, corner_radius=10)
    card.grid_columnconfigure(0, weight=1)

    ctk.CTkLabel(
        card,
        text=summary.team.name,
        font=theme.heading_font(size=19),
        text_color=theme.TEXT_PRIMARY,
        anchor="w",
    ).grid(row=0, column=0, sticky="w", padx=20, pady=(18, 4))

    captain_row = ctk.CTkFrame(card, fg_color="transparent")
    captain_row.grid(row=1, column=0, sticky="w", padx=20, pady=(0, 14))
    if summary.captain is not None:
        ctk.CTkLabel(
            captain_row,
            text=summary.captain.full_name,
            font=theme.body_font(size=14),
            text_color=theme.TEXT_SECONDARY,
            anchor="w",
        ).pack(side="left")
        captain_badge(captain_row).pack(side="left", padx=(10, 0))
    else:
        ctk.CTkLabel(
            captain_row,
            text=f"Captain id {summary.team.captain_player_id} could not be resolved",
            font=theme.body_font(size=13),
            text_color=theme.UNSOLD_RED,
            anchor="w",
        ).pack(side="left")

    stats_row = ctk.CTkFrame(card, fg_color="transparent")
    stats_row.grid(row=2, column=0, sticky="ew", padx=20, pady=(0, 14))
    for index, (label, value) in enumerate(stats):
        stats_row.grid_columnconfigure(index, weight=1)
        _build_mini_stat(stats_row, label, value).grid(row=0, column=index, sticky="w", padx=(0 if index == 0 else 12, 0))

    ctk.CTkFrame(card, fg_color=theme.BORDER, height=1, corner_radius=0).grid(
        row=3, column=0, sticky="ew", padx=20, pady=(0, 10)
    )

    ctk.CTkLabel(
        card,
        text="ROSTER",
        font=theme.body_font(size=11, weight="bold"),
        text_color=theme.TEXT_SECONDARY,
        anchor="w",
    ).grid(row=4, column=0, sticky="w", padx=20, pady=(0, 6))

    roster_container = ctk.CTkFrame(card, fg_color="transparent")
    roster_container.grid(row=5, column=0, sticky="ew", padx=20, pady=(0, 18))
    roster_container.grid_columnconfigure(0, weight=1)

    for index, player in enumerate(summary.roster_players):
        _build_roster_row(
            roster_container, player, position_colors, is_new_transfer=is_new_transfer(player.id)
        ).grid(row=index, column=0, sticky="ew", pady=3)

    offset = len(summary.roster_players)
    for index, unresolved_id in enumerate(summary.unresolved_roster_ids):
        _build_unresolved_roster_row(roster_container, unresolved_id).grid(
            row=offset + index, column=0, sticky="ew", pady=3
        )

    return card


class TeamsScreen(ctk.CTkFrame):
    """View of the four auction teams and their current rosters.

    Before an auction session has started (or with no session passed at
    all), shows canonical data/teams.json + data/players.json. Once a
    session is active, shows that session's live in-memory Team/Player
    objects instead — so purchases made on the Live Auction screen are
    reflected here without re-reading canonical JSON (which would discard
    them). Still entirely read-only: nothing here mutates state.
    """

    def __init__(self, parent: ctk.CTkBaseClass, session: AuctionSession | None = None) -> None:
        super().__init__(parent, fg_color=theme.BACKGROUND, corner_radius=0)
        self._session = session
        # Second Auction — Milestone 3: once a session is active, always
        # use the phase-aware CurrentTeamSummary (see services/current_
        # roster_service.py) instead of the first-auction-only
        # TeamSummary — it already falls back to identical first-auction
        # values while second_auction_state.status == NOT_STARTED, so
        # this is a behavior-preserving swap for every phase before the
        # second auction starts.
        self._is_second_auction_phase = (
            session is not None and session.started and crs.second_auction_is_active_or_complete(session)
        )

        try:
            if session is not None and session.started:
                self._summaries = crs.build_current_team_summaries(session)
            else:
                self._summaries = load_team_summaries()
            self._position_colors = load_position_colors()
        except (OSError, json.JSONDecodeError, ValueError, KeyError, TypeError) as exc:
            self._build_error_state(str(exc))
            return

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(3, weight=1)

        self._build_title()
        self._build_summary_row()
        self._build_team_grid()

    def _build_error_state(self, message: str) -> None:
        self.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            self,
            text="TEAMS",
            font=theme.heading_font(size=26),
            text_color=theme.TEXT_PRIMARY,
            anchor="w",
        ).grid(row=0, column=0, sticky="w", padx=32, pady=(32, 8))
        ctk.CTkLabel(
            self,
            text="Team or player data could not be loaded.",
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

    def _build_title(self) -> None:
        ctk.CTkLabel(
            self,
            text="TEAMS",
            font=theme.heading_font(size=26),
            text_color=theme.TEXT_PRIMARY,
            anchor="w",
        ).grid(row=0, column=0, sticky="w", padx=32, pady=(28, 4))

        subtitle, subtitle_color = self._title_subtitle()
        ctk.CTkLabel(
            self,
            text=subtitle,
            font=theme.body_font(size=14, weight="bold" if self._is_second_auction_phase else "normal"),
            text_color=subtitle_color,
            anchor="w",
        ).grid(row=1, column=0, sticky="w", padx=32, pady=(0, 16))

    def _title_subtitle(self) -> tuple[str, str]:
        """Second Auction — Milestone 3: makes the current tournament
        phase unmistakable at a glance, per the ticket's "do NOT show
        stale first-auction rosters as if they were current" requirement."""
        if not self._is_second_auction_phase:
            return "Captains, budgets, squad capacity, and current rosters for all four auction teams.", theme.TEXT_SECONDARY
        status = self._session.second_auction_state.status
        if status == SecondAuctionStatus.COMPLETE:
            return "SECOND AUCTION COMPLETE — FINAL SQUADS", theme.ACCENT_GREEN
        if status == SecondAuctionStatus.BLOCKED:
            return "SECOND AUCTION BLOCKED — showing current active rosters", theme.UNSOLD_RED
        return "SECOND AUCTION IN PROGRESS — showing current active rosters", theme.GOLD_ACCENT

    def _build_summary_row(self) -> None:
        summary_row = ctk.CTkFrame(self, fg_color="transparent")
        summary_row.grid(row=2, column=0, sticky="ew", padx=32, pady=(0, 16))

        teams = [summary.team for summary in self._summaries]
        # All four teams share one starting budget and squad limit (enforced
        # by Team validation and Milestone 2 tests); any team's value works.
        starting_budget = teams[0].starting_budget if teams else "—"
        max_squad_size = teams[0].max_squad_size if teams else "—"
        total_budget = sum(team.starting_budget for team in teams)

        stats = [
            ("Teams", len(teams)),
            ("Starting Budget / Team", f"{starting_budget}M"),
            ("Squad Limit", max_squad_size),
            ("Total Auction Budget", f"{total_budget}M"),
        ]
        for index, (label, value) in enumerate(stats):
            summary_row.grid_columnconfigure(index, weight=1)
            _build_stat_card(summary_row, label, value).grid(
                row=0, column=index, sticky="ew", padx=(0 if index == 0 else 8, 0)
            )

    def _stats_for_summary(self, summary) -> list[tuple[str, str]]:
        if not self._is_second_auction_phase:
            return [
                ("Remaining Budget", f"{summary.remaining_budget}M"),
                ("Starting Budget", f"{summary.team.starting_budget}M"),
                ("Total Spent", f"{summary.team.starting_budget - summary.remaining_budget}M"),
                ("Squad", f"{summary.squad_size} / {summary.team.max_squad_size}"),
                ("Remaining Slots", str(summary.remaining_slots)),
            ]
        # Second Auction — Milestone 3: never label the old first-auction
        # 100M budget as the team's current spendable budget during/after
        # the second auction — see the ticket's "BUDGET DISPLAY AFTER
        # SECOND AUCTION" requirement. A small first-auction reference
        # figure is preserved, clearly labeled, never confused with funds.
        return [
            ("First Auction Remaining", f"{summary.first_auction_remaining_budget}M"),
            ("Second Auction Starting Budget", f"{summary.starting_budget}M"),
            ("Second Auction Spent", f"{summary.second_auction_spent}M"),
            ("Final Remaining Budget", f"{summary.remaining_budget}M"),
            ("Current Squad", f"{summary.squad_size} / {summary.team.max_squad_size}"),
        ]

    def _build_team_grid(self) -> None:
        scroll_area = ctk.CTkScrollableFrame(self, fg_color=theme.BACKGROUND, corner_radius=0)
        scroll_area.grid(row=3, column=0, sticky="nsew", padx=32, pady=(0, 24))
        for column in range(TEAMS_PER_ROW):
            scroll_area.grid_columnconfigure(column, weight=1)

        is_new_transfer = (
            (lambda player_id: crs.is_player_new_transfer(self._session, player_id))
            if self._is_second_auction_phase
            else (lambda player_id: False)
        )

        for index, summary in enumerate(self._summaries):
            row, column = divmod(index, TEAMS_PER_ROW)
            stats = self._stats_for_summary(summary)
            card = _build_team_card(scroll_area, summary, self._position_colors, stats, is_new_transfer)
            card.grid(row=row, column=column, sticky="nsew", padx=8, pady=8)


def build_teams_screen(parent: ctk.CTkBaseClass, session: AuctionSession | None = None) -> ctk.CTkFrame:
    return TeamsScreen(parent, session=session)
