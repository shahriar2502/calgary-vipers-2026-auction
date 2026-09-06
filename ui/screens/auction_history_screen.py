"""Auction History screen: a read-only, chronological transaction timeline
for the currently loaded MOCK/LIVE auction session.

This is the event log, not the analytics screen — Reports (still a
placeholder) is where summary/statistics dashboards belong. History is
attempt-based, not player-based: services/auction_service.py's
`process_sale`/`process_unsold` already append one AuctionHistoryEntry per
attempt to `Auction.history` (a player UNSOLD twice and then SOLD keeps
all three entries), and this screen only reads that existing log via
services/auction_history_service.py — it never builds a second history
store and never mutates a Player/Team/Auction object.

Session-aware like ui/screens/teams_screen.py, but with one difference:
there is no canonical/pre-session fallback here, since history only makes
sense for a session that has actually run some transactions. With no
active session, the screen shows a plain empty state instead.
"""

from __future__ import annotations

import json

import customtkinter as ctk

from models.auction import AuctionHistoryEntry
from models.player import Position
from services.auction_history_service import (
    ALL_RESULTS,
    ALL_ROUNDS,
    ALL_TEAMS,
    NEWEST_FIRST,
    OLDEST_FIRST,
    SOLD,
    HistorySummary,
    build_short_name_lookup,
    filter_history,
    round_numbers,
    sort_history,
    summarize_history,
)
from services.auction_session_service import AuctionSession
from services.config_service import ROOT_DIR, load_position_colors
from ui import theme
from ui.widgets import load_cover_fit_image, pill_badge, position_badge

RESULT_OPTIONS = [ALL_RESULTS, SOLD, "UNSOLD"]
SORT_OPTIONS = [OLDEST_FIRST, NEWEST_FIRST]

# Small enough to keep every row compact in a long scrollable history list.
THUMBNAIL_SIZE = (32, 32)

# (column title, pixel width) shared by the header row and every data row.
COLUMNS = [
    ("#", 44),
    ("", 40),
    ("Player", 220),
    ("Pos", 64),
    ("Result", 90),
    ("Round", 90),
    ("Team", 170),
    ("Price", 90),
]


def _build_stat_card(parent: ctk.CTkBaseClass, label: str, value: object) -> ctk.CTkFrame:
    card = ctk.CTkFrame(parent, fg_color=theme.SURFACE, corner_radius=8)
    ctk.CTkLabel(
        card, text=str(value), font=theme.heading_font(size=22), text_color=theme.TEXT_PRIMARY
    ).pack(padx=16, pady=(12, 0))
    ctk.CTkLabel(
        card, text=label, font=theme.body_font(size=12), text_color=theme.TEXT_SECONDARY
    ).pack(padx=16, pady=(0, 12))
    return card


def _round_label(round_number: int | None) -> str:
    return f"Round {round_number}" if round_number is not None else "—"


def _build_table_header(parent: ctk.CTkBaseClass) -> ctk.CTkFrame:
    header = ctk.CTkFrame(parent, fg_color=theme.SURFACE_ALT, corner_radius=6)
    for index, (title, width) in enumerate(COLUMNS):
        header.grid_columnconfigure(index, minsize=width)
        ctk.CTkLabel(
            header,
            text=title.upper(),
            font=theme.body_font(size=11, weight="bold"),
            text_color=theme.TEXT_SECONDARY,
            anchor="w",
        ).grid(row=0, column=index, sticky="w", padx=(12 if index == 0 else 4, 4), pady=8)
    return header


def _build_history_row(
    parent: ctk.CTkBaseClass,
    entry: AuctionHistoryEntry,
    photo_path_by_id: dict[int, str],
    position_colors: dict[str, str],
    zebra: bool,
    photo_refs: list,
) -> ctk.CTkFrame:
    """`photo_refs` collects every CTkImage created for this row so the
    caller can keep them alive for as long as the row widgets exist — see
    ui/screens/players_setup_screen.py's `_build_player_row` for the same
    GC-safety pattern."""
    row = ctk.CTkFrame(parent, fg_color=theme.SURFACE if zebra else "transparent", corner_radius=4)
    for index, (_title, width) in enumerate(COLUMNS):
        row.grid_columnconfigure(index, minsize=width)

    ctk.CTkLabel(
        row, text=f"#{entry.auction_sequence}", font=theme.body_font(size=13), text_color=theme.TEXT_SECONDARY, anchor="w"
    ).grid(row=0, column=0, sticky="w", padx=(12, 4), pady=6)

    photo_path = photo_path_by_id.get(entry.player_id)
    photo_image = load_cover_fit_image(ROOT_DIR / photo_path, THUMBNAIL_SIZE) if photo_path else None
    if photo_image is not None:
        photo_refs.append(photo_image)
        thumbnail = ctk.CTkLabel(row, image=photo_image, text="", corner_radius=4)
    else:
        thumbnail = ctk.CTkLabel(
            row,
            text=entry.player_name[:1].upper(),
            font=theme.body_font(size=12, weight="bold"),
            fg_color=theme.SURFACE_ALT,
            text_color=theme.TEXT_SECONDARY,
            corner_radius=4,
            width=THUMBNAIL_SIZE[0],
            height=THUMBNAIL_SIZE[1],
        )
    thumbnail.grid(row=0, column=1, sticky="w", padx=4, pady=4)

    ctk.CTkLabel(
        row, text=entry.player_name, font=theme.body_font(size=13), text_color=theme.TEXT_PRIMARY, anchor="w"
    ).grid(row=0, column=2, sticky="w", padx=4, pady=6)

    position_badge(row, Position(entry.position), position_colors).grid(row=0, column=3, sticky="w", padx=4, pady=6)

    if entry.status == SOLD:
        pill_badge(row, "SOLD", fg_color=theme.SOLD_GREEN, text_color=theme.BACKGROUND, width=72).grid(
            row=0, column=4, sticky="w", padx=4, pady=6
        )
    else:
        pill_badge(row, "UNSOLD", fg_color=theme.UNSOLD_RED, text_color=theme.BACKGROUND, width=72).grid(
            row=0, column=4, sticky="w", padx=4, pady=6
        )

    pill_badge(
        row, _round_label(entry.round_number), fg_color=theme.SURFACE_ALT, text_color=theme.TEXT_SECONDARY, width=80
    ).grid(row=0, column=5, sticky="w", padx=4, pady=6)

    ctk.CTkLabel(
        row,
        text=entry.team if entry.team else "—",
        font=theme.body_font(size=13),
        text_color=theme.TEXT_SECONDARY,
        anchor="w",
    ).grid(row=0, column=6, sticky="w", padx=4, pady=6)

    ctk.CTkLabel(
        row,
        text=f"{entry.sold_price}M" if entry.sold_price is not None else "—",
        font=theme.body_font(size=13, weight="bold"),
        text_color=theme.GOLD_ACCENT if entry.sold_price is not None else theme.TEXT_SECONDARY,
        anchor="w",
    ).grid(row=0, column=7, sticky="w", padx=4, pady=6)

    return row


class AuctionHistoryScreen(ctk.CTkFrame):
    """Read-only transaction timeline for the current auction session.

    With no active session, shows a plain empty state and nothing else.
    Once a session is active, everything renders from that session's own
    `Auction.history` — switching to a different (resumed) session and
    rebuilding this screen always reflects exactly that session's log,
    never a mix of sessions.
    """

    def __init__(self, parent: ctk.CTkBaseClass, session: AuctionSession | None = None) -> None:
        super().__init__(parent, fg_color=theme.BACKGROUND, corner_radius=0)
        self._session = session
        self._row_container: ctk.CTkScrollableFrame | None = None
        self._count_label: ctk.CTkLabel | None = None
        self._row_photo_images: list = []  # kept alive to avoid GC

        self.grid_columnconfigure(0, weight=1)

        if session is None or not session.started:
            self._build_no_session_state()
            return

        try:
            self._position_colors = load_position_colors()
        except (OSError, json.JSONDecodeError, ValueError, KeyError, TypeError) as exc:
            self._build_error_state(str(exc))
            return

        self._search_var = ctk.StringVar(value="")
        self._result_var = ctk.StringVar(value=ALL_RESULTS)
        self._round_var = ctk.StringVar(value=ALL_ROUNDS)
        self._team_var = ctk.StringVar(value=ALL_TEAMS)
        self._sort_var = ctk.StringVar(value=OLDEST_FIRST)

        self.grid_rowconfigure(5, weight=1)
        self._build_title()
        self._build_context_row()
        self._build_summary_row()
        self._build_controls()
        self._build_table()

        self._search_var.trace_add("write", lambda *_args: self._refresh_table())
        self._refresh_table()

    def _build_error_state(self, message: str) -> None:
        ctk.CTkLabel(
            self, text="AUCTION HISTORY", font=theme.heading_font(size=26), text_color=theme.TEXT_PRIMARY, anchor="w"
        ).grid(row=0, column=0, sticky="w", padx=32, pady=(32, 8))
        ctk.CTkLabel(
            self,
            text="Tournament data could not be loaded.",
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
    # Empty state (no session)
    # ------------------------------------------------------------------

    def _build_no_session_state(self) -> None:
        ctk.CTkLabel(
            self, text="AUCTION HISTORY", font=theme.heading_font(size=26), text_color=theme.TEXT_PRIMARY, anchor="w"
        ).grid(row=0, column=0, sticky="w", padx=32, pady=(28, 4))
        ctk.CTkLabel(
            self,
            text="Complete transaction timeline for the current auction session.",
            font=theme.body_font(size=14),
            text_color=theme.TEXT_SECONDARY,
            anchor="w",
        ).grid(row=1, column=0, sticky="w", padx=32, pady=(0, 24))
        ctk.CTkLabel(
            self,
            text="NO ACTIVE AUCTION SESSION",
            font=theme.heading_font(size=18),
            text_color=theme.TEXT_SECONDARY,
            anchor="w",
        ).grid(row=2, column=0, sticky="w", padx=32)
        ctk.CTkLabel(
            self,
            text="Start or resume a MOCK/LIVE auction to view transaction history.",
            font=theme.body_font(size=13),
            text_color=theme.TEXT_SECONDARY,
            anchor="w",
        ).grid(row=3, column=0, sticky="w", padx=32, pady=(4, 0))

    # ------------------------------------------------------------------
    # Active-session layout
    # ------------------------------------------------------------------

    def _build_title(self) -> None:
        ctk.CTkLabel(
            self, text="AUCTION HISTORY", font=theme.heading_font(size=26), text_color=theme.TEXT_PRIMARY, anchor="w"
        ).grid(row=0, column=0, sticky="w", padx=32, pady=(28, 4))
        ctk.CTkLabel(
            self,
            text="Complete transaction timeline for the current auction session.",
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

        total_attempts = len(session.auction.history)
        ctk.CTkLabel(
            row,
            text=f"{total_attempts} transaction attempt{'s' if total_attempts != 1 else ''}",
            font=theme.body_font(size=13),
            text_color=theme.TEXT_SECONDARY,
        ).pack(side="left", padx=(0, 16))

        if session.name:
            ctk.CTkLabel(
                row, text=session.name, font=theme.body_font(size=13), text_color=theme.TEXT_SECONDARY
            ).pack(side="left")

    def _build_summary_row(self) -> None:
        summary_row = ctk.CTkFrame(self, fg_color="transparent")
        summary_row.grid(row=3, column=0, sticky="ew", padx=32, pady=(0, 16))

        summary: HistorySummary = summarize_history(self._session.auction.history)
        stats = [
            ("Total Attempts", summary.total_attempts),
            ("Sold", summary.sold_count),
            ("Unsold Attempts", summary.unsold_count),
            ("Total Spent", f"{summary.total_spent}M"),
        ]
        for index, (label, value) in enumerate(stats):
            summary_row.grid_columnconfigure(index, weight=1)
            _build_stat_card(summary_row, label, value).grid(
                row=0, column=index, sticky="ew", padx=(0 if index == 0 else 8, 0)
            )

    def _build_controls(self) -> None:
        """Two rows rather than one long one: search/result/count fit
        comfortably on their own, and round/team/sort filters on a second
        line below — five controls plus a count label in a single row
        would overflow the app's 1024px minimum window width (verified via
        screenshot at 1024x640), and CTk has no auto-wrapping layout to
        fall back on."""
        controls = ctk.CTkFrame(self, fg_color="transparent")
        controls.grid(row=4, column=0, sticky="ew", padx=32, pady=(0, 12))
        controls.grid_columnconfigure(2, weight=1)

        ctk.CTkEntry(
            controls,
            textvariable=self._search_var,
            placeholder_text="Search by name...",
            width=200,
            fg_color=theme.SURFACE,
            border_color=theme.BORDER,
            text_color=theme.TEXT_PRIMARY,
        ).grid(row=0, column=0, sticky="w")

        ctk.CTkSegmentedButton(
            controls,
            values=RESULT_OPTIONS,
            variable=self._result_var,
            command=lambda _value: self._refresh_table(),
            selected_color=theme.ACCENT_GREEN,
            selected_hover_color=theme.ACCENT_GREEN_HOVER,
            fg_color=theme.SURFACE,
            unselected_color=theme.SURFACE,
            text_color=theme.TEXT_SECONDARY,
        ).grid(row=0, column=1, sticky="w", padx=(16, 0))

        self._count_label = ctk.CTkLabel(
            controls, text="", font=theme.body_font(size=12), text_color=theme.TEXT_SECONDARY, anchor="e"
        )
        self._count_label.grid(row=0, column=2, sticky="e")

        filters_row = ctk.CTkFrame(controls, fg_color="transparent")
        filters_row.grid(row=1, column=0, columnspan=3, sticky="w", pady=(12, 0))

        round_options = [ALL_ROUNDS] + [f"Round {number}" for number in round_numbers(self._session.auction.history)]
        ctk.CTkOptionMenu(
            filters_row,
            values=round_options,
            variable=self._round_var,
            command=lambda _value: self._refresh_table(),
            fg_color=theme.SURFACE,
            button_color=theme.SURFACE_ALT,
            button_hover_color=theme.BORDER,
            text_color=theme.TEXT_PRIMARY,
            dropdown_fg_color=theme.SURFACE,
            width=140,
        ).grid(row=0, column=0, sticky="w")

        team_options = [ALL_TEAMS] + [team.name for team in self._session.teams]
        ctk.CTkOptionMenu(
            filters_row,
            values=team_options,
            variable=self._team_var,
            command=lambda _value: self._refresh_table(),
            fg_color=theme.SURFACE,
            button_color=theme.SURFACE_ALT,
            button_hover_color=theme.BORDER,
            text_color=theme.TEXT_PRIMARY,
            dropdown_fg_color=theme.SURFACE,
            width=160,
        ).grid(row=0, column=1, sticky="w", padx=(16, 0))

        ctk.CTkOptionMenu(
            filters_row,
            values=SORT_OPTIONS,
            variable=self._sort_var,
            command=lambda _value: self._refresh_table(),
            fg_color=theme.SURFACE,
            button_color=theme.SURFACE_ALT,
            button_hover_color=theme.BORDER,
            text_color=theme.TEXT_PRIMARY,
            dropdown_fg_color=theme.SURFACE,
            width=170,
        ).grid(row=0, column=2, sticky="w", padx=(16, 0))

    def _build_table(self) -> None:
        table_frame = ctk.CTkFrame(self, fg_color="transparent")
        table_frame.grid(row=5, column=0, sticky="nsew", padx=32, pady=(0, 24))
        table_frame.grid_columnconfigure(0, weight=1)
        table_frame.grid_rowconfigure(1, weight=1)

        _build_table_header(table_frame).grid(row=0, column=0, sticky="ew")

        self._row_container = ctk.CTkScrollableFrame(table_frame, fg_color=theme.BACKGROUND, corner_radius=0)
        self._row_container.grid(row=1, column=0, sticky="nsew", pady=(4, 0))
        self._row_container.grid_columnconfigure(0, weight=1)

    # ------------------------------------------------------------------
    # Filtering / rendering
    # ------------------------------------------------------------------

    def _round_filter_value(self) -> int | str:
        selected = self._round_var.get()
        if selected == ALL_ROUNDS:
            return ALL_ROUNDS
        return int(selected.removeprefix("Round ").strip())

    def _visible_rows(self) -> list[AuctionHistoryEntry]:
        """The current filtered/searched/sorted rows — exposed as its own
        method (mirroring ui/screens/player_cards_screen.py's
        `_visible_players`) so tests can assert on it directly."""
        history = self._session.auction.history
        short_names = build_short_name_lookup(self._session.players)
        filtered = filter_history(
            history,
            search=self._search_var.get(),
            result=self._result_var.get(),
            round_number=self._round_filter_value(),
            team=self._team_var.get(),
            short_names=short_names,
        )
        return sort_history(filtered, order=self._sort_var.get())

    def _refresh_table(self) -> None:
        if self._row_container is None:
            return

        for child in self._row_container.winfo_children():
            child.destroy()
        self._row_photo_images = []

        history = self._session.auction.history
        if not history:
            ctk.CTkLabel(
                self._row_container,
                text="NO TRANSACTIONS YET\nThe first SOLD or UNSOLD action will appear here.",
                font=theme.body_font(size=13),
                text_color=theme.TEXT_SECONDARY,
                justify="left",
            ).grid(row=0, column=0, sticky="w", padx=12, pady=12)
            if self._count_label is not None:
                self._count_label.configure(text="Showing 0 of 0 transactions")
            return

        rows = self._visible_rows()
        photo_path_by_id = {
            player.id: player.photo_path for player in (self._session.players or []) if player.photo_path
        }
        position_colors = self._position_colors

        if not rows:
            ctk.CTkLabel(
                self._row_container,
                text="No transactions match the current search and filters.",
                font=theme.body_font(size=13),
                text_color=theme.TEXT_SECONDARY,
            ).grid(row=0, column=0, sticky="w", padx=12, pady=12)
        else:
            for index, entry in enumerate(rows):
                row = _build_history_row(
                    self._row_container,
                    entry,
                    photo_path_by_id,
                    position_colors,
                    zebra=index % 2 == 1,
                    photo_refs=self._row_photo_images,
                )
                row.grid(row=index, column=0, sticky="ew", pady=1)

        if self._count_label is not None:
            self._count_label.configure(text=f"Showing {len(rows)} of {len(history)} transactions")


def build_auction_history_screen(parent: ctk.CTkBaseClass, session: AuctionSession | None = None) -> ctk.CTkFrame:
    return AuctionHistoryScreen(parent, session=session)
