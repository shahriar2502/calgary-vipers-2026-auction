"""Player Cards screen: a visual gallery of all 32 players.

GUI only, entirely read-only — never mutates a Player/Team/Auction object.
Separate from Players & Setup (which stays the administrative, table-style
roster view); this is the sports-broadcast-style showcase, one reusable
card component per player rather than 32 hand-built cards.

Session-aware exactly like ui/screens/teams_screen.py: before a session
has started, shows canonical data/players.json; once a session is active,
shows that session's live in-memory Player objects instead, so the detail
view can reflect current SOLD/UNSOLD/captain status without a second
query mechanism — Player already carries `auction_status`/`sold_to`/
`sold_price` from Milestone 7, updated in place by the auction service, so
this screen only reads fields that already exist.

The expanded player view is an in-page detail panel (this screen's own
gallery grid swapped out for a detail layout, with a "Back" button) rather
than a separate CTkToplevel dialog — deliberately, to avoid a CTkToplevel/
DPI-scaling geometry-measurement instability observed while prototyping a
dialog for a panel this tall with a large image (winfo_reqheight() and the
window's actual rendered size disagreed and kept drifting across redraw
cycles on a scaled display); an in-page panel sidesteps toplevel geometry
entirely and is one of the layouts the spec explicitly allows.
"""

from __future__ import annotations

import json

import customtkinter as ctk
from PIL import Image

from models.player import Player, PlayerAuctionStatus, Position
from services.auction_session_service import AuctionSession
from services.config_service import ROOT_DIR, load_position_colors
from services.player_service import filter_players, load_players
from ui import player_card_art
from ui import theme
from ui.widgets import captain_badge, cover_fit_pil_image, position_badge

FILTER_OPTIONS = ["ALL", "GK", "DEF", "MID", "ATT", "CAPTAINS"]
SORT_OPTIONS = [
    "OVR — High to Low",
    "OVR — Low to High",
    "FPL — High to Low",
    "FPL — Low to High",
    "Name — A to Z",
]

# ----------------------------------------------------------------------
# Card geometry. Photo keeps the same portrait language as the Live
# Auction player card (see ui/screens/live_auction_screen.py) so the app
# has one consistent "player photo" visual style, just smaller here to
# fit a grid. Everything below is one place to retune the premium card's
# proportions without touching the drawing code itself.
# ----------------------------------------------------------------------
CARD_WIDTH = 240
CARD_HEIGHT = 420
CARD_GAP = 16
MIN_COLUMNS = 2
MAX_COLUMNS = 6
CARD_CORNER_RADIUS = 20

PHOTO_LEFT = 14
PHOTO_TOP = 14
PHOTO_SIZE = (CARD_WIDTH - 2 * PHOTO_LEFT, 236)
PHOTO_CORNER_RADIUS = 14
PHOTO_BORDER_WIDTH = 3

NAME_Y = PHOTO_TOP + PHOTO_SIZE[1] + 14
SHORT_NAME_Y = NAME_Y + 25
FPL_Y = SHORT_NAME_Y + 23
CAPTAIN_BADGE_Y = FPL_Y + 27

DETAIL_PHOTO_SIZE = (280, 350)

# Re-exported from the shared card-art system (ui/player_card_art.py) so
# existing call sites/tests in this module keep working unchanged — see
# that module for the Live Auction variant that now shares this same
# visual language via different size/offset parameters.
_VARIANT_COLORS = player_card_art.VARIANT_COLORS
_hex_to_rgb = player_card_art.hex_to_rgb
_card_variant = player_card_art.card_variant


def _set_card_hover(card: ctk.CTkFrame, hovering: bool, default_border: str) -> None:
    """Apply/revert the hover glow on a compact card's border.

    A plain module-level function (rather than a closure inline in the
    binding setup) so tests can exercise the exact same styling logic
    directly, without depending on synthetic Tk event delivery through a
    widget's internal canvas — which is unreliable against a withdrawn
    test-fixture root.
    """
    if hovering:
        card.configure(border_color=theme.GOLD_ACCENT, border_width=3)
    else:
        card.configure(border_color=default_border, border_width=2)


def _fpl_display(points: int | None) -> str:
    return player_card_art.fpl_display(points)


# ----------------------------------------------------------------------
# Premium card art: thin wrappers over ui/player_card_art.py's shared
# compositing system, fixed to this screen's own gallery-tile geometry
# (CARD_WIDTH/HEIGHT, PHOTO_*). See that module for the Live Auction
# variant, which reuses the exact same background/framing functions at a
# different size — one visual design system, two size/layout variants.
# ----------------------------------------------------------------------


def _frame_photo(photo: Image.Image, accent_rgb: tuple[int, int, int]) -> Image.Image:
    return player_card_art.frame_photo(photo, accent_rgb, PHOTO_CORNER_RADIUS, PHOTO_BORDER_WIDTH)


def _build_card_image(player: Player) -> tuple[Image.Image, bool]:
    """The full composited card art for one player at this screen's
    gallery-tile size. Returns (image, has_real_photo) — the caller uses
    the flag to decide whether to also draw an initials label and whether
    to count this card toward the "real photo loaded" bookkeeping."""
    return player_card_art.build_player_card_image(
        player,
        background_size=(CARD_WIDTH, CARD_HEIGHT),
        photo_size=PHOTO_SIZE,
        photo_offset=(PHOTO_LEFT, PHOTO_TOP),
        corner_radius=CARD_CORNER_RADIUS,
        photo_corner_radius=PHOTO_CORNER_RADIUS,
        photo_border_width=PHOTO_BORDER_WIDTH,
    )


def _sort_players(players: list[Player], sort_value: str) -> list[Player]:
    """Pure sort helper. FPL sorts always place `None` (no last-season
    data) after every real value, in both directions — `None` is never
    treated as 0, per the explicit requirement."""
    if sort_value == "OVR — High to Low":
        return sorted(players, key=lambda p: -p.overall_rating)
    if sort_value == "OVR — Low to High":
        return sorted(players, key=lambda p: p.overall_rating)
    if sort_value == "Name — A to Z":
        return sorted(players, key=lambda p: p.full_name.lower())
    if sort_value == "FPL — High to Low":
        return sorted(
            players,
            key=lambda p: (p.last_season_fpl_points is None, -(p.last_season_fpl_points or 0)),
        )
    if sort_value == "FPL — Low to High":
        return sorted(
            players,
            key=lambda p: (p.last_season_fpl_points is None, p.last_season_fpl_points or 0),
        )
    return players


def _current_status_text(player: Player, session_active: bool) -> str:
    """Read-only description of a player's current auction/team status,
    derived entirely from fields already on `Player` (updated in place by
    services/auction_service.py) — no separate cross-referencing needed."""
    if player.is_captain:
        return f"CAPTAIN — {player.assigned_team}"
    if not session_active:
        return "PRE-AUCTION"
    if player.auction_status == PlayerAuctionStatus.SOLD:
        return f"SOLD — {player.sold_to} — {player.sold_price}M"
    if player.auction_status == PlayerAuctionStatus.UNSOLD:
        return "UNSOLD — awaiting re-auction"
    return "UNASSIGNED"


class PlayerCardsScreen(ctk.CTkFrame):
    """Read-only visual gallery of all 32 players with search/filter/sort
    and a click-to-expand in-page detail view."""

    def __init__(self, parent: ctk.CTkBaseClass, session: AuctionSession | None = None) -> None:
        super().__init__(parent, fg_color=theme.BACKGROUND, corner_radius=0)
        self._session = session
        self._search_var = ctk.StringVar(value="")
        self._filter_var = ctk.StringVar(value=FILTER_OPTIONS[0])
        self._sort_var = ctk.StringVar(value=SORT_OPTIONS[0])
        self._grid_container: ctk.CTkScrollableFrame | None = None
        self._count_label: ctk.CTkLabel | None = None
        self._card_photo_images: list = []  # count of cards with a *real* loaded photo (see _build_compact_card)
        self._card_art_images: list = []  # every card's composited art image, kept alive to avoid GC
        self._detail_photo_image: ctk.CTkImage | None = None  # kept alive to avoid GC
        self._last_columns: int | None = None
        self._detail_player: Player | None = None

        try:
            if session is not None and session.started:
                self._players: list[Player] = session.players
            else:
                self._players = load_players()
            self._position_colors = load_position_colors()
        except (OSError, json.JSONDecodeError, ValueError, KeyError, TypeError) as exc:
            self._build_error_state(str(exc))
            return

        # Recompute the gallery's column count whenever this screen's own
        # real size becomes known/changes: at construction time (before
        # MainWindow has gridded this frame into its content area, or in a
        # withdrawn test root) `self.winfo_width()` is not yet meaningful,
        # so the first render uses a sensible fallback — this binding is
        # what corrects it once the geometry manager actually places/
        # resizes the frame, without rebuilding the whole 32-card grid on
        # every pixel of a live drag (only when the column count itself
        # would change).
        self.bind("<Configure>", self._on_configure)
        self._search_var.trace_add("write", lambda *_args: self._refresh_grid())
        self._render()

    # ------------------------------------------------------------------
    # Top-level render dispatch: gallery grid, or one player's detail view
    # ------------------------------------------------------------------

    def _render(self) -> None:
        for child in self.winfo_children():
            child.destroy()
        self._grid_container = None
        self._count_label = None

        if self._detail_player is not None:
            self._build_detail_view(self._detail_player)
        else:
            self._build_gallery_view()

    def _build_error_state(self, message: str) -> None:
        self.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            self, text="PLAYER CARDS", font=theme.heading_font(size=26), text_color=theme.TEXT_PRIMARY, anchor="w"
        ).grid(row=0, column=0, sticky="w", padx=32, pady=(32, 8))
        ctk.CTkLabel(
            self,
            text="Player data could not be loaded.",
            font=theme.body_font(size=15, weight="bold"),
            text_color=theme.UNSOLD_RED,
            anchor="w",
        ).grid(row=1, column=0, sticky="w", padx=32)
        ctk.CTkLabel(
            self, text=message, font=theme.body_font(size=13), text_color=theme.TEXT_SECONDARY,
            anchor="w", justify="left", wraplength=760,
        ).grid(row=2, column=0, sticky="w", padx=32, pady=(8, 0))

    # ------------------------------------------------------------------
    # Gallery view
    # ------------------------------------------------------------------

    def _build_gallery_view(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(3, weight=1)

        self._build_title()
        self._build_controls()
        self._build_grid_area()
        self._refresh_grid()

    def _build_title(self) -> None:
        ctk.CTkLabel(
            self, text="PLAYER CARDS", font=theme.heading_font(size=26), text_color=theme.TEXT_PRIMARY, anchor="w"
        ).grid(row=0, column=0, sticky="w", padx=32, pady=(28, 4))
        ctk.CTkLabel(
            self,
            text="A visual showcase of all 32 players — click a card for full details.",
            font=theme.body_font(size=14),
            text_color=theme.TEXT_SECONDARY,
            anchor="w",
        ).grid(row=1, column=0, sticky="w", padx=32, pady=(0, 16))

    def _build_controls(self) -> None:
        controls = ctk.CTkFrame(self, fg_color="transparent")
        controls.grid(row=2, column=0, sticky="ew", padx=32, pady=(0, 12))
        controls.grid_columnconfigure(3, weight=1)

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
            values=FILTER_OPTIONS,
            variable=self._filter_var,
            command=lambda _value: self._refresh_grid(),
            selected_color=theme.ACCENT_GREEN,
            selected_hover_color=theme.ACCENT_GREEN_HOVER,
            fg_color=theme.SURFACE,
            unselected_color=theme.SURFACE,
            text_color=theme.TEXT_SECONDARY,
        ).grid(row=0, column=1, sticky="w", padx=(16, 0))

        ctk.CTkOptionMenu(
            controls,
            values=SORT_OPTIONS,
            variable=self._sort_var,
            command=lambda _value: self._refresh_grid(),
            fg_color=theme.SURFACE,
            button_color=theme.SURFACE_ALT,
            button_hover_color=theme.BORDER,
            text_color=theme.TEXT_PRIMARY,
            dropdown_fg_color=theme.SURFACE,
            width=180,
        ).grid(row=0, column=2, sticky="w", padx=(16, 0))

        self._count_label = ctk.CTkLabel(
            controls, text="", font=theme.body_font(size=12), text_color=theme.TEXT_SECONDARY, anchor="e"
        )
        self._count_label.grid(row=0, column=3, sticky="e")

    def _build_grid_area(self) -> None:
        self._grid_container = ctk.CTkScrollableFrame(self, fg_color=theme.BACKGROUND, corner_radius=0)
        self._grid_container.grid(row=3, column=0, sticky="nsew", padx=32, pady=(0, 24))

    # ------------------------------------------------------------------

    def _compute_columns(self, width: int | None = None) -> int:
        """Column count from this screen frame's own current width (it is
        already the content-area child, sidebar excluded, so no separate
        sidebar-width subtraction is needed). Falls back to a reasonable
        default when the width isn't meaningful yet (e.g. before the
        geometry manager has placed this frame) — corrected automatically
        by `_on_configure` once it is."""
        if width is None:
            width = self.winfo_width()
        if width <= 1:
            return 4
        available = max(width - 64, CARD_WIDTH)
        columns = available // (CARD_WIDTH + CARD_GAP)
        return max(MIN_COLUMNS, min(MAX_COLUMNS, columns))

    def _on_configure(self, event: object) -> None:
        if getattr(event, "widget", None) is not self:
            return
        if self._grid_container is None:
            return  # detail view is showing; nothing to reflow
        columns = self._compute_columns(event.width)
        if columns != self._last_columns:
            self._refresh_grid()

    def _visible_players(self) -> list[Player]:
        filter_value = self._filter_var.get()
        position = None if filter_value in ("ALL", "CAPTAINS") else filter_value
        status = "CAPTAIN" if filter_value == "CAPTAINS" else None
        filtered = filter_players(self._players, search=self._search_var.get(), position=position, status=status)
        return _sort_players(filtered, self._sort_var.get())

    def _refresh_grid(self) -> None:
        if self._grid_container is None:
            return

        for child in self._grid_container.winfo_children():
            child.destroy()
        self._card_photo_images = []
        self._card_art_images = []

        visible = self._visible_players()
        columns = self._compute_columns()
        self._last_columns = columns
        # Reset every possible column slot, not just 0..columns-1 — a
        # previous render at a larger column count could otherwise leave a
        # stale weight=1 (and therefore an oversized empty) column behind
        # when the count shrinks (e.g. after a window resize).
        for column in range(MAX_COLUMNS):
            self._grid_container.grid_columnconfigure(column, weight=1 if column < columns else 0)

        if not visible:
            ctk.CTkLabel(
                self._grid_container,
                text="No players match the current search and filters.",
                font=theme.body_font(size=13),
                text_color=theme.TEXT_SECONDARY,
            ).grid(row=0, column=0, sticky="w", padx=12, pady=12)
        else:
            for index, player in enumerate(visible):
                row, column = divmod(index, columns)
                card = self._build_compact_card(self._grid_container, player)
                card.grid(row=row, column=column, sticky="n", padx=8, pady=8)

        if self._count_label is not None:
            self._count_label.configure(text=f"Showing {len(visible)} of {len(self._players)} players")

    # ------------------------------------------------------------------
    # Compact card
    # ------------------------------------------------------------------

    def _build_compact_card(self, parent: ctk.CTkBaseClass, player: Player) -> ctk.CTkFrame:
        """A premium, sports-card-style tile: one composited PIL image
        (decorative Vipers-green/red diagonal art + this player's own
        framed real photo — see `_build_card_image`) filling the card,
        with real CTkLabel/badge widgets placed on top for the name/OVR/
        position/FPL/captain text — so everything stays exactly as
        findable and testable as a flat-color card, just with a much
        richer backdrop behind it."""
        variant = _card_variant(player)
        border_color = _VARIANT_COLORS[variant][0]

        card = ctk.CTkFrame(
            parent, fg_color=theme.SURFACE, corner_radius=CARD_CORNER_RADIUS,
            width=CARD_WIDTH, height=CARD_HEIGHT, border_width=2, border_color=border_color,
        )
        card.grid_propagate(False)

        art_image, has_photo = _build_card_image(player)
        art_ctk_image = ctk.CTkImage(light_image=art_image, dark_image=art_image, size=(CARD_WIDTH, CARD_HEIGHT))
        # Every card's composited art (real photo or placeholder) must be
        # kept alive regardless, or the whole background — not just the
        # photo — goes blank once Tk garbage-collects the CTkImage.
        # `_card_photo_images` stays a count of *real photo* successes
        # specifically (existing tests rely on this), so it only gets the
        # image appended when a real photo was actually composited in.
        self._card_art_images.append(art_ctk_image)
        if has_photo:
            self._card_photo_images.append(art_ctk_image)

        ctk.CTkLabel(card, image=art_ctk_image, text="").place(x=0, y=0)

        if not has_photo:
            # width/height must be set on the widget itself, not .place() —
            # the placeholder panel's rounded frame is already baked into
            # the art image; this just centers the initials text over it.
            ctk.CTkLabel(
                card, text=player.short_name[:2].upper(), font=theme.heading_font(size=32),
                text_color=theme.TEXT_SECONDARY, fg_color="transparent",
                width=PHOTO_SIZE[0], height=PHOTO_SIZE[1],
            ).place(x=PHOTO_LEFT, y=PHOTO_TOP)

        position_badge(card, player.position, self._position_colors).place(x=10, y=10)
        ctk.CTkLabel(
            card, text=f"{player.overall_rating}", font=theme.heading_font(size=20), text_color=theme.GOLD_ACCENT,
        ).place(relx=1.0, x=-12, y=4, anchor="ne")
        ctk.CTkLabel(
            card, text="OVR", font=theme.body_font(size=10, weight="bold"), text_color=theme.GOLD_ACCENT,
        ).place(relx=1.0, x=-12, y=28, anchor="ne")

        ctk.CTkLabel(
            card, text=player.full_name.upper(), font=theme.body_font(size=14, weight="bold"),
            text_color=theme.TEXT_PRIMARY, wraplength=CARD_WIDTH - 24, justify="center", fg_color="transparent",
        ).place(relx=0.5, y=NAME_Y, anchor="n")
        ctk.CTkLabel(
            card, text=player.short_name, font=theme.body_font(size=12), text_color=theme.TEXT_SECONDARY,
            fg_color="transparent",
        ).place(relx=0.5, y=SHORT_NAME_Y, anchor="n")
        ctk.CTkLabel(
            card,
            text=f"FPL LAST SEASON: {_fpl_display(player.last_season_fpl_points)}",
            font=theme.body_font(size=11, weight="bold"),
            text_color=theme.GOLD_ACCENT,
            fg_color="transparent",
        ).place(relx=0.5, y=FPL_Y, anchor="n")

        if player.is_captain:
            captain_badge(card).place(relx=0.5, y=CAPTAIN_BADGE_Y, anchor="n")

        self._bind_card_interactions(card, player)
        return card

    def _bind_card_interactions(self, card: ctk.CTkFrame, player: Player) -> None:
        """Click-anywhere-to-open plus a lightweight hover glow (border
        brightens to gold) — bound recursively so it fires from any child
        widget (photo, badges, text), not just the bare card background."""
        variant = _card_variant(player)
        default_border = _VARIANT_COLORS[variant][0]

        def handle_click(_event: object = None) -> None:
            self._open_detail(player)

        # Exposed as a plain attribute (not just a Tk binding) so tests can
        # invoke the exact same click handler directly, without depending
        # on synthetic Tk event delivery through a withdrawn test root.
        card._on_click = handle_click

        def bind_all(widget: ctk.CTkBaseClass) -> None:
            widget.bind("<Button-1>", lambda _event: handle_click())
            widget.bind("<Enter>", lambda _event: _set_card_hover(card, True, default_border))
            widget.bind("<Leave>", lambda _event: _set_card_hover(card, False, default_border))
            for child in widget.winfo_children():
                bind_all(child)

        bind_all(card)

    # ------------------------------------------------------------------
    # Detail (expanded) view — an in-page panel, not a separate window
    # ------------------------------------------------------------------

    def _open_detail(self, player: Player) -> None:
        self._detail_player = player
        self._render()

    def _close_detail(self) -> None:
        self._detail_player = None
        self._render()

    def _build_detail_view(self, player: Player) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=32, pady=(24, 8))
        ctk.CTkButton(
            header, text="← Back to Player Cards", font=theme.body_font(size=13, weight="bold"),
            fg_color=theme.SURFACE_ALT, hover_color=theme.BORDER, text_color=theme.TEXT_PRIMARY,
            width=180, height=36, corner_radius=8, command=self._close_detail,
        ).pack(anchor="w")

        scroll_area = ctk.CTkScrollableFrame(self, fg_color=theme.BACKGROUND, corner_radius=0)
        scroll_area.grid(row=1, column=0, sticky="nsew", padx=32, pady=(0, 24))
        scroll_area.grid_columnconfigure(0, weight=1)

        variant = _card_variant(player)
        accent_color = _VARIANT_COLORS[variant][0]

        card = ctk.CTkFrame(
            scroll_area, fg_color=theme.SURFACE, corner_radius=18, border_width=2, border_color=accent_color,
        )
        card.grid(row=0, column=0, sticky="n", pady=(4, 4))
        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(padx=48, pady=36)

        raw_photo = cover_fit_pil_image(ROOT_DIR / player.photo_path, DETAIL_PHOTO_SIZE) if player.photo_path else None
        if raw_photo is not None:
            framed = _frame_photo(raw_photo, _hex_to_rgb(accent_color))
            self._detail_photo_image = ctk.CTkImage(light_image=framed, dark_image=framed, size=DETAIL_PHOTO_SIZE)
            photo_widget = ctk.CTkLabel(inner, image=self._detail_photo_image, text="")
        else:
            self._detail_photo_image = None
            photo_widget = ctk.CTkLabel(
                inner, text=player.short_name[:2].upper(), font=theme.heading_font(size=54),
                fg_color=theme.SURFACE_ALT, text_color=theme.TEXT_SECONDARY, corner_radius=16,
                width=DETAIL_PHOTO_SIZE[0], height=DETAIL_PHOTO_SIZE[1],
            )
        photo_widget.pack(pady=(0, 18))

        ctk.CTkLabel(
            inner, text=player.full_name.upper(), font=theme.heading_font(size=26), text_color=theme.TEXT_PRIMARY,
        ).pack()
        ctk.CTkLabel(
            inner, text=player.short_name, font=theme.body_font(size=15), text_color=theme.TEXT_SECONDARY,
        ).pack(pady=(2, 14))

        badge_row = ctk.CTkFrame(inner, fg_color="transparent")
        badge_row.pack(pady=(0, 16))
        position_badge(badge_row, player.position, self._position_colors).pack(side="left", padx=(0, 14))
        ovr_block = ctk.CTkFrame(badge_row, fg_color="transparent")
        ovr_block.pack(side="left", padx=(0, 14))
        ctk.CTkLabel(
            ovr_block, text=str(player.overall_rating), font=theme.heading_font(size=24), text_color=theme.ACCENT_GREEN
        ).pack(side="left")
        ctk.CTkLabel(
            ovr_block, text=" OVR", font=theme.body_font(size=13, weight="bold"), text_color=theme.TEXT_SECONDARY
        ).pack(side="left", pady=(7, 0))
        if player.is_captain:
            captain_badge(badge_row).pack(side="left")

        ctk.CTkLabel(
            inner,
            text=f"LAST SEASON FPL: {_fpl_display(player.last_season_fpl_points)}",
            font=theme.body_font(size=14, weight="bold"),
            text_color=theme.TEXT_SECONDARY,
        ).pack(pady=(0, 12))

        session_active = self._session is not None and self._session.started
        ctk.CTkLabel(
            inner,
            text=_current_status_text(player, session_active),
            font=theme.body_font(size=14, weight="bold"),
            text_color=theme.ACCENT_GREEN,
        ).pack(pady=(0, 4))


def build_player_cards_screen(parent: ctk.CTkBaseClass, session: AuctionSession | None = None) -> ctk.CTkFrame:
    return PlayerCardsScreen(parent, session=session)
