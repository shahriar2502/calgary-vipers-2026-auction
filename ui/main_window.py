"""Main application window: header, sidebar navigation, and placeholder screens.

GUI only. No auction, roster, or persistence logic lives here — that stays
in models/ and services/.
"""

from __future__ import annotations

import tkinter
from pathlib import Path

import customtkinter as ctk

from services import persistence_service, preferences_service
from services.auction_session_service import AuctionSession
from services.captain_bidding_server import CaptainBiddingServer
from services.config_service import BrandingConfig, load_branding_config
from ui import theme
from ui.screens import SCREEN_BUILDERS
from ui.widgets import load_image_safely

# (nav label, placeholder description) in sidebar display order.
NAV_ITEMS: list[tuple[str, str]] = [
    ("Live Auction", "Random player reveal and bidding controls will appear here."),
    ("Player Cards", "A visual showcase of all 32 players will appear here."),
    ("Players & Setup", "Player roster management and auction setup will appear here."),
    ("Teams", "Team rosters, captains, remaining budgets, and squad sizes will appear here."),
    ("Auction History", "A log of completed SOLD and UNSOLD results will appear here."),
    ("Reports", "Auction summaries and exports will appear here."),
    ("Settings", "Tournament and branding configuration will appear here."),
]
DEFAULT_SCREEN = NAV_ITEMS[0][0]

LOGO_MAX_SIZE = (56, 56)


def _load_logo_image(path: Path) -> ctk.CTkImage | None:
    """Load the branding logo, preserving aspect ratio.

    Never raises: a missing or unreadable file returns None so callers can
    fall back to a text badge instead of crashing the application.
    """
    return load_image_safely(path, LOGO_MAX_SIZE)


def _build_placeholder_screen(parent: ctk.CTkBaseClass, title: str, description: str) -> ctk.CTkFrame:
    frame = ctk.CTkFrame(parent, fg_color=theme.BACKGROUND, corner_radius=0)
    frame.grid_columnconfigure(0, weight=1)

    ctk.CTkLabel(
        frame,
        text=title.upper(),
        text_color=theme.TEXT_PRIMARY,
        font=theme.heading_font(size=26),
        anchor="w",
    ).grid(row=0, column=0, sticky="w", padx=32, pady=(32, 8))

    ctk.CTkLabel(
        frame,
        text=description,
        text_color=theme.TEXT_SECONDARY,
        font=theme.body_font(size=15),
        anchor="w",
        justify="left",
        wraplength=760,
    ).grid(row=1, column=0, sticky="w", padx=32)

    ctk.CTkLabel(
        frame,
        text="Coming in a later milestone",
        text_color=theme.ACCENT_GREEN,
        font=theme.body_font(size=13, weight="bold"),
        anchor="w",
    ).grid(row=2, column=0, sticky="w", padx=32, pady=(16, 0))

    return frame


class MainWindow(ctk.CTk):
    """The single top-level application window."""

    def __init__(self, branding: BrandingConfig | None = None, session: AuctionSession | None = None) -> None:
        super().__init__()
        theme.apply_appearance()

        self.branding = branding or load_branding_config()
        self.session = session or AuctionSession()
        # Wire real disk persistence by default. A caller (or test) that
        # injects its own `session` with an `autosave` already set (e.g. a
        # no-op, to avoid touching disk) is respected — this only fills in
        # the default when nothing else has claimed the hook.
        if self.session.autosave is None:
            self.session.autosave = persistence_service.autosave_hook
        self._nav_buttons: dict[str, ctk.CTkButton] = {}
        self._active_screen_name: str | None = None
        self._screen_frame: ctk.CTkFrame | None = None
        self._logo_image: ctk.CTkImage | None = None  # kept alive to avoid GC
        self._is_fullscreen = False
        # Organizer app preferences (Settings screen) — fullscreen/display
        # and SOLD/UNSOLD confirmation toggles. Deliberately separate from
        # `self.session`: these are UI preferences, never auction data.
        self.preferences = preferences_service.load_preferences()
        # Captain Phone Bidding (Phase 1): one server instance for the
        # life of the app, bound to this exact `self.session` object (not
        # a copy) so every phone request and the desktop UI always see the
        # same live auction state. Off by default — Settings' START/STOP
        # buttons are the only thing that ever calls .start()/.stop().
        self.captain_bidding_server = CaptainBiddingServer(self.session)

        self.title(self.branding.header_title)
        self.geometry(theme.WINDOW_DEFAULT_SIZE)
        self.minsize(*theme.WINDOW_MIN_SIZE)
        self.configure(fg_color=theme.BACKGROUND)

        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(1, weight=1)

        self.bind("<F11>", lambda _event: self.toggle_fullscreen())
        self.bind("<Escape>", lambda _event: self.set_fullscreen(False))
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self._build_header()
        self._build_sidebar()
        self._build_content_area()
        self.show_screen(DEFAULT_SCREEN)

        # Applied after the window is otherwise fully built, per
        # PROJECT_CONTEXT.md's Settings note: starting fullscreen
        # immediately (before layout settles) risks a worse first-paint on
        # some platforms than starting windowed and switching a moment
        # later.
        if self.preferences.remember_fullscreen and self.preferences.fullscreen_enabled:
            self.after(50, lambda: self.set_fullscreen(True))

    def set_fullscreen(self, enabled: bool) -> None:
        """Enter/exit fullscreen. Never raises: the `-fullscreen` window
        attribute is unsupported on some platforms/backends, and this is a
        cosmetic feature that must never crash the app."""
        try:
            self.attributes("-fullscreen", enabled)
        except tkinter.TclError:
            pass
        self._is_fullscreen = enabled

    def toggle_fullscreen(self) -> None:
        self.set_fullscreen(not self._is_fullscreen)

    def _on_close(self) -> None:
        """Attempt a clean captain-bidding-server shutdown before the
        window actually closes, so a running server doesn't linger as a
        zombie thread after the app exits. `stop()` is already safe to
        call even if the server was never started."""
        self.captain_bidding_server.stop()
        self.destroy()

    def _build_header(self) -> None:
        header = ctk.CTkFrame(self, fg_color=theme.SURFACE, corner_radius=0, height=72)
        header.grid(row=0, column=0, columnspan=2, sticky="ew")
        header.grid_propagate(False)
        header.grid_columnconfigure(1, weight=1)

        self._logo_image = _load_logo_image(self.branding.logo_full_path)
        if self._logo_image is not None:
            logo_widget = ctk.CTkLabel(header, image=self._logo_image, text="")
        else:
            logo_widget = ctk.CTkLabel(
                header,
                text="CV",
                text_color=theme.ACCENT_GREEN,
                font=theme.heading_font(size=18),
                fg_color=theme.SURFACE_ALT,
                corner_radius=8,
                width=48,
                height=48,
            )
        logo_widget.grid(row=0, column=0, rowspan=2, padx=(20, 12), pady=12)

        ctk.CTkLabel(
            header,
            text=self.branding.header_title,
            text_color=theme.TEXT_PRIMARY,
            font=theme.heading_font(size=20),
            anchor="w",
        ).grid(row=0, column=1, sticky="sw", pady=(14, 0))

        ctk.CTkLabel(
            header,
            text=self.branding.app_name,
            text_color=theme.TEXT_SECONDARY,
            font=theme.body_font(size=12),
            anchor="w",
        ).grid(row=1, column=1, sticky="nw", pady=(0, 14))

    def _build_sidebar(self) -> None:
        sidebar = ctk.CTkFrame(self, fg_color=theme.SURFACE, corner_radius=0, width=200)
        sidebar.grid(row=1, column=0, sticky="nsw")
        sidebar.grid_propagate(False)

        for index, (name, _description) in enumerate(NAV_ITEMS):
            button = ctk.CTkButton(
                sidebar,
                text=name,
                anchor="w",
                corner_radius=6,
                fg_color="transparent",
                hover_color=theme.SURFACE_ALT,
                text_color=theme.TEXT_SECONDARY,
                font=theme.body_font(size=14),
                command=lambda selected=name: self.show_screen(selected),
            )
            button.grid(row=index, column=0, sticky="ew", padx=12, pady=(16 if index == 0 else 4, 4))
            self._nav_buttons[name] = button

        sidebar.grid_rowconfigure(len(NAV_ITEMS), weight=1)

    def _build_content_area(self) -> None:
        self._content_area = ctk.CTkFrame(self, fg_color=theme.BACKGROUND, corner_radius=0)
        self._content_area.grid(row=1, column=1, sticky="nsew")
        self._content_area.grid_rowconfigure(0, weight=1)
        self._content_area.grid_columnconfigure(0, weight=1)

    def show_screen(self, name: str) -> None:
        """Switch the central content area to the named screen.

        Screens listed in ui.screens.SCREEN_BUILDERS get their real,
        data-driven implementation; every other nav item still falls back
        to the Milestone 4 placeholder.
        """
        descriptions = dict(NAV_ITEMS)
        if name not in descriptions:
            raise ValueError(f"Unknown navigation screen: {name}")

        if self._screen_frame is not None:
            self._screen_frame.destroy()

        builder = SCREEN_BUILDERS.get(name)
        if builder is not None:
            self._screen_frame = builder(self._content_area, self.session)
        else:
            self._screen_frame = _build_placeholder_screen(self._content_area, name, descriptions[name])
        self._screen_frame.grid(row=0, column=0, sticky="nsew")

        self._set_active_button(name)
        self._active_screen_name = name

    def _set_active_button(self, active_name: str) -> None:
        for name, button in self._nav_buttons.items():
            if name == active_name:
                button.configure(
                    fg_color=theme.ACCENT_GREEN,
                    text_color=theme.BACKGROUND,
                    hover_color=theme.ACCENT_GREEN_HOVER,
                )
            else:
                button.configure(
                    fg_color="transparent",
                    text_color=theme.TEXT_SECONDARY,
                    hover_color=theme.SURFACE_ALT,
                )


def run() -> None:
    """Create and run the main application window."""
    app = MainWindow()
    app.mainloop()
