"""Players & Setup screen: a read-only, searchable/filterable view of the
complete player database and a tournament setup-readiness check.

GUI only — all loading, filtering, and validation logic lives in
services/player_service.py and services/config_service.py.
"""

from __future__ import annotations

import json

import customtkinter as ctk

from models.player import Player
from services.config_service import ROOT_DIR, load_position_colors
from services.player_service import (
    SetupValidationResult,
    filter_players,
    load_players,
    load_teams,
    validate_setup,
)
from ui import theme
from ui.widgets import auction_status_badge, captain_badge, load_cover_fit_image, position_badge

POSITION_FILTER_OPTIONS = ["All", "GK", "DEF", "MID", "ATT"]
STATUS_FILTER_OPTIONS = ["All Players", "Auction Players", "Captains"]
_STATUS_FILTER_TO_SERVICE_VALUE = {
    "All Players": None,
    "Auction Players": "AUCTION",
    "Captains": "CAPTAIN",
}

# Small enough to keep every row compact in the scrollable 32-row table.
THUMBNAIL_SIZE = (32, 40)

# (column title, pixel width) shared by the header row and every data row.
COLUMNS = [
    ("ID", 48),
    ("", 44),
    ("Player", 240),
    ("Short Name", 110),
    ("Position", 90),
    ("OVR", 60),
    ("Status", 100),
    ("Assigned Team", 160),
]


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


def _build_setup_status_banner(parent: ctk.CTkBaseClass, result: SetupValidationResult) -> ctk.CTkFrame:
    if result.is_ready:
        banner = ctk.CTkFrame(parent, fg_color=theme.ACCENT_GREEN, corner_radius=8)
        ctk.CTkLabel(
            banner,
            text="Tournament Setup Ready",
            font=theme.body_font(size=14, weight="bold"),
            text_color=theme.BACKGROUND,
            anchor="w",
        ).pack(padx=16, pady=10, anchor="w")
        return banner

    banner = ctk.CTkFrame(parent, fg_color=theme.UNSOLD_RED, corner_radius=8)
    lines = ["Tournament Setup Not Ready:"]
    lines += [f"- {check.label}: {check.detail}" for check in result.failures]
    ctk.CTkLabel(
        banner,
        text="\n".join(lines),
        font=theme.body_font(size=13),
        text_color=theme.TEXT_PRIMARY,
        anchor="w",
        justify="left",
    ).pack(padx=16, pady=10, anchor="w")
    return banner


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


def _build_player_row(
    parent: ctk.CTkBaseClass,
    player: Player,
    position_colors: dict[str, str],
    zebra: bool,
    photo_refs: list,
) -> ctk.CTkFrame:
    """`photo_refs` collects every CTkImage created for this row so the
    caller can keep them alive for as long as the row widgets exist —
    CTkImage/PhotoImage objects are garbage-collected (and the label goes
    blank) the moment nothing else references them."""
    row = ctk.CTkFrame(parent, fg_color=theme.SURFACE if zebra else "transparent", corner_radius=4)
    for index, (_title, width) in enumerate(COLUMNS):
        row.grid_columnconfigure(index, minsize=width)

    ctk.CTkLabel(
        row, text=str(player.id), font=theme.body_font(size=13), text_color=theme.TEXT_SECONDARY, anchor="w"
    ).grid(row=0, column=0, sticky="w", padx=(12, 4), pady=6)

    photo_image = load_cover_fit_image(ROOT_DIR / player.photo_path, THUMBNAIL_SIZE) if player.photo_path else None
    if photo_image is not None:
        photo_refs.append(photo_image)
        thumbnail = ctk.CTkLabel(row, image=photo_image, text="", corner_radius=4)
    else:
        thumbnail = ctk.CTkLabel(
            row,
            text=player.short_name[:1].upper(),
            font=theme.body_font(size=12, weight="bold"),
            fg_color=theme.SURFACE_ALT,
            text_color=theme.TEXT_SECONDARY,
            corner_radius=4,
            width=THUMBNAIL_SIZE[0],
            height=THUMBNAIL_SIZE[1],
        )
    thumbnail.grid(row=0, column=1, sticky="w", padx=4, pady=4)

    ctk.CTkLabel(
        row, text=player.full_name, font=theme.body_font(size=13), text_color=theme.TEXT_PRIMARY, anchor="w"
    ).grid(row=0, column=2, sticky="w", padx=4, pady=6)
    ctk.CTkLabel(
        row, text=player.short_name, font=theme.body_font(size=13), text_color=theme.TEXT_SECONDARY, anchor="w"
    ).grid(row=0, column=3, sticky="w", padx=4, pady=6)
    position_badge(row, player.position, position_colors).grid(row=0, column=4, sticky="w", padx=4, pady=6)
    ctk.CTkLabel(
        row,
        text=str(player.overall_rating),
        font=theme.body_font(size=13, weight="bold"),
        text_color=theme.TEXT_PRIMARY,
        anchor="w",
    ).grid(row=0, column=5, sticky="w", padx=4, pady=6)
    status_badge = captain_badge(row) if player.is_captain else auction_status_badge(row)
    status_badge.grid(row=0, column=6, sticky="w", padx=4, pady=6)
    ctk.CTkLabel(
        row,
        text=player.assigned_team if player.is_captain else "—",
        font=theme.body_font(size=13),
        text_color=theme.TEXT_SECONDARY,
        anchor="w",
    ).grid(row=0, column=7, sticky="w", padx=4, pady=6)
    return row


class PlayersSetupScreen(ctk.CTkFrame):
    """Read-only player database / tournament setup inspection screen."""

    def __init__(self, parent: ctk.CTkBaseClass) -> None:
        super().__init__(parent, fg_color=theme.BACKGROUND, corner_radius=0)

        self._search_var = ctk.StringVar(value="")
        self._position_var = ctk.StringVar(value=POSITION_FILTER_OPTIONS[0])
        self._status_var = ctk.StringVar(value=STATUS_FILTER_OPTIONS[0])
        self._row_container: ctk.CTkScrollableFrame | None = None
        self._count_label: ctk.CTkLabel | None = None
        self._row_photo_images: list = []  # kept alive to avoid GC; see _build_player_row

        try:
            self._players: list[Player] = load_players()
            self._teams = load_teams()
            self._position_colors = load_position_colors()
        except (OSError, json.JSONDecodeError, ValueError, KeyError, TypeError) as exc:
            self._build_error_state(str(exc))
            return

        self._validation = validate_setup(self._players, self._teams)

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(5, weight=1)

        self._build_title()
        self._build_summary_row()
        self._build_status_banner()
        self._build_controls()
        self._build_table()

        self._search_var.trace_add("write", lambda *_args: self._refresh_table())
        self._refresh_table()

    def _build_error_state(self, message: str) -> None:
        self.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            self,
            text="PLAYERS & SETUP",
            font=theme.heading_font(size=26),
            text_color=theme.TEXT_PRIMARY,
            anchor="w",
        ).grid(row=0, column=0, sticky="w", padx=32, pady=(32, 8))
        ctk.CTkLabel(
            self,
            text="Player or team data could not be loaded.",
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
            text="PLAYERS & SETUP",
            font=theme.heading_font(size=26),
            text_color=theme.TEXT_PRIMARY,
            anchor="w",
        ).grid(row=0, column=0, sticky="w", padx=32, pady=(28, 4))
        ctk.CTkLabel(
            self,
            text="Review the complete player database and tournament setup before the auction starts.",
            font=theme.body_font(size=14),
            text_color=theme.TEXT_SECONDARY,
            anchor="w",
        ).grid(row=1, column=0, sticky="w", padx=32, pady=(0, 16))

    def _build_summary_row(self) -> None:
        summary = ctk.CTkFrame(self, fg_color="transparent")
        summary.grid(row=2, column=0, sticky="ew", padx=32, pady=(0, 12))

        # All four teams share one starting budget and squad limit (enforced
        # by Team validation and Milestone 2 tests); any team's value works.
        starting_budget = self._teams[0].starting_budget if self._teams else "—"
        max_squad_size = self._teams[0].max_squad_size if self._teams else "—"

        stats = [
            ("Total Players", len(self._players)),
            ("Auction Players", sum(player.auction_eligible for player in self._players)),
            ("Captains", sum(player.is_captain for player in self._players)),
            ("Teams", len(self._teams)),
            ("Budget / Team", f"{starting_budget}M"),
            ("Squad Limit", max_squad_size),
        ]
        for index, (label, value) in enumerate(stats):
            summary.grid_columnconfigure(index, weight=1)
            _build_stat_card(summary, label, value).grid(
                row=0, column=index, sticky="ew", padx=(0 if index == 0 else 8, 0)
            )

    def _build_status_banner(self) -> None:
        banner = _build_setup_status_banner(self, self._validation)
        banner.grid(row=3, column=0, sticky="ew", padx=32, pady=(0, 16))

    def _build_controls(self) -> None:
        controls = ctk.CTkFrame(self, fg_color="transparent")
        controls.grid(row=4, column=0, sticky="ew", padx=32, pady=(0, 12))
        controls.grid_columnconfigure(3, weight=1)

        ctk.CTkEntry(
            controls,
            textvariable=self._search_var,
            placeholder_text="Search by name...",
            width=220,
            fg_color=theme.SURFACE,
            border_color=theme.BORDER,
            text_color=theme.TEXT_PRIMARY,
        ).grid(row=0, column=0, sticky="w")

        ctk.CTkSegmentedButton(
            controls,
            values=POSITION_FILTER_OPTIONS,
            variable=self._position_var,
            command=lambda _value: self._refresh_table(),
            selected_color=theme.ACCENT_GREEN,
            selected_hover_color=theme.ACCENT_GREEN_HOVER,
            fg_color=theme.SURFACE,
            unselected_color=theme.SURFACE,
            text_color=theme.TEXT_SECONDARY,
        ).grid(row=0, column=1, sticky="w", padx=(16, 0))

        ctk.CTkSegmentedButton(
            controls,
            values=STATUS_FILTER_OPTIONS,
            variable=self._status_var,
            command=lambda _value: self._refresh_table(),
            selected_color=theme.ACCENT_GREEN,
            selected_hover_color=theme.ACCENT_GREEN_HOVER,
            fg_color=theme.SURFACE,
            unselected_color=theme.SURFACE,
            text_color=theme.TEXT_SECONDARY,
        ).grid(row=0, column=2, sticky="w", padx=(16, 0))

        self._count_label = ctk.CTkLabel(
            controls, text="", font=theme.body_font(size=12), text_color=theme.TEXT_SECONDARY, anchor="e"
        )
        self._count_label.grid(row=0, column=3, sticky="e")

    def _build_table(self) -> None:
        table_frame = ctk.CTkFrame(self, fg_color="transparent")
        table_frame.grid(row=5, column=0, sticky="nsew", padx=32, pady=(0, 24))
        table_frame.grid_columnconfigure(0, weight=1)
        table_frame.grid_rowconfigure(1, weight=1)

        _build_table_header(table_frame).grid(row=0, column=0, sticky="ew")

        self._row_container = ctk.CTkScrollableFrame(table_frame, fg_color=theme.BACKGROUND, corner_radius=0)
        self._row_container.grid(row=1, column=0, sticky="nsew", pady=(4, 0))
        self._row_container.grid_columnconfigure(0, weight=1)

    def _refresh_table(self) -> None:
        if self._row_container is None:
            return

        for child in self._row_container.winfo_children():
            child.destroy()
        self._row_photo_images = []

        filtered = filter_players(
            self._players,
            search=self._search_var.get(),
            position=self._position_var.get(),
            status=_STATUS_FILTER_TO_SERVICE_VALUE[self._status_var.get()],
        )

        if not filtered:
            ctk.CTkLabel(
                self._row_container,
                text="No players match the current search and filters.",
                font=theme.body_font(size=13),
                text_color=theme.TEXT_SECONDARY,
            ).grid(row=0, column=0, sticky="w", padx=12, pady=12)
        else:
            for index, player in enumerate(filtered):
                row = _build_player_row(
                    self._row_container,
                    player,
                    self._position_colors,
                    zebra=index % 2 == 1,
                    photo_refs=self._row_photo_images,
                )
                row.grid(row=index, column=0, sticky="ew", pady=1)

        if self._count_label is not None:
            self._count_label.configure(text=f"Showing {len(filtered)} of {len(self._players)} players")


def build_players_setup_screen(parent: ctk.CTkBaseClass, session: object = None) -> ctk.CTkFrame:
    """`session` is accepted (and ignored) only for SCREEN_BUILDERS' shared
    calling convention — Players & Setup stays a canonical, session-free
    inspection screen; see PROJECT_CONTEXT.md."""
    return PlayersSetupScreen(parent)
