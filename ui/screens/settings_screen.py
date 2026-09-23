"""Settings screen: organizer-facing app configuration and diagnostics.

GUI only, and deliberately conservative about what it lets the organizer
change. Core tournament rules (player/team counts, starting budget, squad
size, GK rule, base prices, dynamic completion reserve, re-auction
behavior, captain assignments) are authoritative business logic implemented in
`models/`/`services/` — this screen only ever *displays* them (the
"TOURNAMENT RULES — READ-ONLY" section), never edits them. The only
things this screen actually changes are organizer app preferences
(fullscreen/display, SOLD/UNSOLD confirmation-dialog toggles), persisted
through `services/preferences_service.py` — a file entirely separate from
auction session saves, `data/players.json`, and `data/teams.json`.
"""

from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
from dataclasses import replace

import customtkinter as ctk

from models.player import GK_BASE_PRICE, OUTFIELD_BASE_PRICE
from services import persistence_service, preferences_service
from services.auction_session_service import AuctionSession
from services.captain_bidding_server import ServerLifecycleState
from services.config_service import APP_VERSION, ROOT_DIR, load_branding_config, load_settings
from services.runtime_paths import WRITABLE_ROOT
from services.player_service import (
    EXPECTED_AUCTION_ELIGIBLE_COUNT,
    EXPECTED_CAPTAIN_COUNT,
    EXPECTED_TEAM_COUNT,
    EXPECTED_TOTAL_PLAYERS,
    load_players,
    load_teams,
    validate_setup,
)
from ui import theme
from ui.widgets import format_timestamp

_PLACEHOLDER = "—"


# ----------------------------------------------------------------------
# Small reusable building blocks
# ----------------------------------------------------------------------


def _build_section(parent: ctk.CTkBaseClass, title: str) -> ctk.CTkFrame:
    section = ctk.CTkFrame(parent, fg_color=theme.SURFACE, corner_radius=10)
    section.grid_columnconfigure(0, weight=1)
    ctk.CTkLabel(
        section, text=title.upper(), font=theme.heading_font(size=15), text_color=theme.TEXT_PRIMARY, anchor="w"
    ).grid(row=0, column=0, sticky="w", padx=18, pady=(16, 10))
    return section


def _info_row(parent: ctk.CTkBaseClass, row: int, label: str, value: object, value_color: str | None = None) -> None:
    line = ctk.CTkFrame(parent, fg_color="transparent")
    line.grid(row=row, column=0, sticky="ew", padx=18, pady=3)
    line.grid_columnconfigure(1, weight=1)
    ctk.CTkLabel(
        line, text=label, font=theme.body_font(size=12), text_color=theme.TEXT_SECONDARY, anchor="w"
    ).grid(row=0, column=0, sticky="w")
    ctk.CTkLabel(
        line,
        text=str(value),
        font=theme.body_font(size=12, weight="bold"),
        text_color=value_color or theme.TEXT_PRIMARY,
        anchor="e",
    ).grid(row=0, column=1, sticky="e")


class SettingsScreen(ctk.CTkFrame):
    """Organizer configuration/diagnostics dashboard. Session-aware (like
    Auction History/Reports) for the Session/Save Status section only —
    everything else is independent of whether a session is active."""

    def __init__(self, parent: ctk.CTkBaseClass, session: AuctionSession | None = None) -> None:
        super().__init__(parent, fg_color=theme.BACKGROUND, corner_radius=0)
        self._session = session
        # Set before the error-path `return` below so `destroy()` can
        # always safely reference this, even if construction failed.
        self._startup_poll_job: str | None = None

        try:
            self._branding = load_branding_config()
            self._settings = load_settings()
            self._players = load_players()
            self._teams = load_teams()
            self._validation = validate_setup(self._players, self._teams)
        except (OSError, json.JSONDecodeError, ValueError, KeyError, TypeError) as exc:
            self._build_error_state(str(exc))
            return

        # Static diagnostics (RC1 stabilization ticket): computed once here,
        # never recomputed by `_render()` — every preference toggle, PIN
        # reset, or server start/stop previously re-ran a 32-file disk scan
        # plus a full canonical-data reload for no reason, since none of
        # this ever changes while the screen is open. Only genuinely
        # dynamic state (captain-bidding server/connection status,
        # preferences) is allowed to be recomputed on every `_render()`.
        photo_paths = [player.photo_path for player in self._players if player.photo_path]
        self._photos_found = sum(1 for path in photo_paths if (ROOT_DIR / path).is_file())
        self._photo_count = len(photo_paths)
        self._missing_photos = [
            player.full_name
            for player in self._players
            if player.photo_path and not (ROOT_DIR / player.photo_path).is_file()
        ]
        self._python_version = platform.python_version()
        self._platform_name = platform.system() or _PLACEHOLDER

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(3, weight=1)

        self._render()

    def destroy(self) -> None:
        if self._startup_poll_job is not None:
            try:
                self.after_cancel(self._startup_poll_job)
            except Exception:  # noqa: BLE001 - best-effort cleanup, must never block/prevent teardown
                pass
            self._startup_poll_job = None
        super().destroy()

    def _render(self) -> None:
        """Rebuilds the whole widget tree from current state — the same
        clear-and-rebuild pattern Live Auction/Player Cards already use,
        rather than re-invoking `__init__` on a live widget (unsafe: the
        underlying Tk widget already exists). Re-reads preferences fresh
        each time so a reset or a switch flip is reflected immediately."""
        for child in self.winfo_children():
            child.destroy()
        self._preferences = preferences_service.load_preferences()
        self._build_title()
        self._build_body()

    # ------------------------------------------------------------------

    def _build_error_state(self, message: str) -> None:
        self.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            self, text="SETTINGS", font=theme.heading_font(size=26), text_color=theme.TEXT_PRIMARY, anchor="w"
        ).grid(row=0, column=0, sticky="w", padx=32, pady=(32, 8))
        ctk.CTkLabel(
            self,
            text="Tournament data could not be loaded.",
            font=theme.body_font(size=15, weight="bold"),
            text_color=theme.UNSOLD_RED,
            anchor="w",
        ).grid(row=1, column=0, sticky="w", padx=32)
        ctk.CTkLabel(
            self, text=message, font=theme.body_font(size=13), text_color=theme.TEXT_SECONDARY,
            anchor="w", justify="left", wraplength=760,
        ).grid(row=2, column=0, sticky="w", padx=32, pady=(8, 0))

    def _build_title(self) -> None:
        ctk.CTkLabel(
            self, text="SETTINGS", font=theme.heading_font(size=26), text_color=theme.TEXT_PRIMARY, anchor="w"
        ).grid(row=0, column=0, sticky="w", padx=32, pady=(28, 4))
        ctk.CTkLabel(
            self,
            text="Organizer configuration, display preferences, and tournament diagnostics.",
            font=theme.body_font(size=14),
            text_color=theme.TEXT_SECONDARY,
            anchor="w",
        ).grid(row=1, column=0, sticky="w", padx=32, pady=(0, 16))

    def _build_body(self) -> None:
        body = ctk.CTkScrollableFrame(self, fg_color=theme.BACKGROUND, corner_radius=0)
        body.grid(row=3, column=0, sticky="nsew", padx=32, pady=(0, 24))
        body.grid_columnconfigure(0, weight=1)
        body.grid_columnconfigure(1, weight=1)
        self._body = body

        self._build_tournament_info(body).grid(row=0, column=0, sticky="new", padx=(0, 8), pady=(0, 16))
        self._build_display_section(body).grid(row=0, column=1, sticky="new", padx=(8, 0), pady=(0, 16))

        self._build_auction_preferences_section(body).grid(row=1, column=0, sticky="new", padx=(0, 8), pady=(0, 16))
        self._build_session_status_section(body).grid(row=1, column=1, sticky="new", padx=(8, 0), pady=(0, 16))

        self._build_reset_bar(body).grid(row=2, column=0, columnspan=2, sticky="ew", pady=(0, 16))

        self._build_captain_bidding_section(body).grid(row=3, column=0, columnspan=2, sticky="ew", pady=(0, 16))

        self._build_diagnostics_section(body).grid(row=4, column=0, columnspan=2, sticky="ew", pady=(0, 16))

        self._build_tournament_rules_section(body).grid(row=5, column=0, columnspan=2, sticky="ew")

    # ------------------------------------------------------------------
    # 1. TOURNAMENT INFO
    # ------------------------------------------------------------------

    def _build_tournament_info(self, parent: ctk.CTkBaseClass) -> ctk.CTkFrame:
        section = _build_section(parent, "Tournament Info")
        rows = [
            ("App Name", self._branding.header_title),
            ("Tournament Year", "2026"),
            ("Branding", self._branding.branding_name),
            ("Canonical Player Count", EXPECTED_TOTAL_PLAYERS),
            ("Auction Players", EXPECTED_AUCTION_ELIGIBLE_COUNT),
            ("Captains", EXPECTED_CAPTAIN_COUNT),
            ("Teams", EXPECTED_TEAM_COUNT),
        ]
        for index, (label, value) in enumerate(rows, start=1):
            _info_row(section, index, label, value)
        ctk.CTkFrame(section, fg_color="transparent", height=8).grid(row=len(rows) + 1, column=0)
        return section

    # ------------------------------------------------------------------
    # 2. DISPLAY / PROJECTOR
    # ------------------------------------------------------------------

    def _build_display_section(self, parent: ctk.CTkBaseClass) -> ctk.CTkFrame:
        section = _build_section(parent, "Display / Projector")
        window = self.winfo_toplevel()

        fullscreen_row = ctk.CTkFrame(section, fg_color="transparent")
        fullscreen_row.grid(row=1, column=0, sticky="ew", padx=18, pady=4)
        fullscreen_row.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            fullscreen_row, text="Fullscreen", font=theme.body_font(size=12), text_color=theme.TEXT_SECONDARY, anchor="w"
        ).grid(row=0, column=0, sticky="w")
        self._fullscreen_switch = ctk.CTkSwitch(
            fullscreen_row,
            text="",
            width=40,
            progress_color=theme.ACCENT_GREEN,
            command=self._on_fullscreen_switch_changed,
        )
        self._fullscreen_switch.grid(row=0, column=1, sticky="e")
        if getattr(window, "_is_fullscreen", False):
            self._fullscreen_switch.select()
        else:
            self._fullscreen_switch.deselect()

        remember_row = ctk.CTkFrame(section, fg_color="transparent")
        remember_row.grid(row=2, column=0, sticky="ew", padx=18, pady=4)
        remember_row.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            remember_row,
            text="Remember Fullscreen",
            font=theme.body_font(size=12),
            text_color=theme.TEXT_SECONDARY,
            anchor="w",
        ).grid(row=0, column=0, sticky="w")
        self._remember_fullscreen_switch = ctk.CTkSwitch(
            remember_row,
            text="",
            width=40,
            progress_color=theme.ACCENT_GREEN,
            command=self._on_remember_fullscreen_switch_changed,
        )
        self._remember_fullscreen_switch.grid(row=0, column=1, sticky="e")
        if self._preferences.remember_fullscreen:
            self._remember_fullscreen_switch.select()
        else:
            self._remember_fullscreen_switch.deselect()

        ctk.CTkLabel(
            section,
            text="F11 toggles fullscreen. Escape always exits fullscreen cleanly.",
            font=theme.body_font(size=11),
            text_color=theme.TEXT_SECONDARY,
            anchor="w",
            justify="left",
            wraplength=340,
        ).grid(row=3, column=0, sticky="w", padx=18, pady=(4, 10))

        ctk.CTkFrame(section, fg_color=theme.BORDER, height=1, corner_radius=0).grid(
            row=4, column=0, sticky="ew", padx=18, pady=(0, 10)
        )

        _info_row(section, 5, "Current Resolution", f"{window.winfo_width()} x {window.winfo_height()}")

        ctk.CTkLabel(
            section,
            text=(
                "Connect the laptop to a TV/projector via HDMI and use Windows "
                "Duplicate or Extend display — this app does not configure Windows "
                "display settings."
            ),
            font=theme.body_font(size=11),
            text_color=theme.TEXT_SECONDARY,
            anchor="w",
            justify="left",
            wraplength=340,
        ).grid(row=6, column=0, sticky="w", padx=18, pady=(8, 16))

        return section

    def _on_fullscreen_switch_changed(self) -> None:
        self._set_fullscreen_preference(bool(self._fullscreen_switch.get()))

    def _apply_window_fullscreen(self, enabled: bool) -> None:
        """Calls the real MainWindow.set_fullscreen when parented under one
        — a plain Tk/CTk root (e.g. a bare test fixture) simply has no
        such method, so this degrades to a no-op rather than an
        AttributeError."""
        set_fullscreen = getattr(self.winfo_toplevel(), "set_fullscreen", None)
        if set_fullscreen is not None:
            set_fullscreen(enabled)

    def _set_fullscreen_preference(self, enabled: bool) -> None:
        """Applies the live window state immediately; only persists it to
        disk if "Remember Fullscreen" is on — otherwise this is a one-time
        toggle for the current session, exactly like a plain F11 press."""
        self._apply_window_fullscreen(enabled)
        if self._preferences.remember_fullscreen:
            self._update_preference(fullscreen_enabled=enabled)

    def _on_remember_fullscreen_switch_changed(self) -> None:
        self._set_remember_fullscreen_preference(bool(self._remember_fullscreen_switch.get()))

    def _set_remember_fullscreen_preference(self, remember: bool) -> None:
        # Capture the *current* live fullscreen state as what gets
        # remembered from now on — matches the intuitive "remember my
        # current choice" meaning of flipping this switch on.
        current_fullscreen = getattr(self.winfo_toplevel(), "_is_fullscreen", False)
        self._update_preference(remember_fullscreen=remember, fullscreen_enabled=current_fullscreen)

    # ------------------------------------------------------------------
    # 3. AUCTION PREFERENCES
    # ------------------------------------------------------------------

    def _build_auction_preferences_section(self, parent: ctk.CTkBaseClass) -> ctk.CTkFrame:
        section = _build_section(parent, "Auction Preferences")

        confirm_sold_row = ctk.CTkFrame(section, fg_color="transparent")
        confirm_sold_row.grid(row=1, column=0, sticky="ew", padx=18, pady=4)
        confirm_sold_row.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            confirm_sold_row,
            text="Confirm SOLD Transactions",
            font=theme.body_font(size=12),
            text_color=theme.TEXT_SECONDARY,
            anchor="w",
        ).grid(row=0, column=0, sticky="w")
        self._confirm_sold_switch = ctk.CTkSwitch(
            confirm_sold_row, text="", width=40, progress_color=theme.ACCENT_GREEN, command=self._on_confirm_sold_switch_changed
        )
        self._confirm_sold_switch.grid(row=0, column=1, sticky="e")
        if self._preferences.confirm_sold:
            self._confirm_sold_switch.select()
        else:
            self._confirm_sold_switch.deselect()

        confirm_unsold_row = ctk.CTkFrame(section, fg_color="transparent")
        confirm_unsold_row.grid(row=2, column=0, sticky="ew", padx=18, pady=4)
        confirm_unsold_row.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            confirm_unsold_row,
            text="Confirm UNSOLD Transactions",
            font=theme.body_font(size=12),
            text_color=theme.TEXT_SECONDARY,
            anchor="w",
        ).grid(row=0, column=0, sticky="w")
        self._confirm_unsold_switch = ctk.CTkSwitch(
            confirm_unsold_row,
            text="",
            width=40,
            progress_color=theme.ACCENT_GREEN,
            command=self._on_confirm_unsold_switch_changed,
        )
        self._confirm_unsold_switch.grid(row=0, column=1, sticky="e")
        if self._preferences.confirm_unsold:
            self._confirm_unsold_switch.select()
        else:
            self._confirm_unsold_switch.deselect()

        ctk.CTkLabel(
            section,
            text=(
                "These only control the confirmation dialog — every budget, roster, "
                "and GK rule is still enforced exactly the same either way."
            ),
            font=theme.body_font(size=11),
            text_color=theme.TEXT_SECONDARY,
            anchor="w",
            justify="left",
            wraplength=340,
        ).grid(row=3, column=0, sticky="w", padx=18, pady=(4, 16))

        return section

    def _on_confirm_sold_switch_changed(self) -> None:
        self._set_confirm_sold_preference(bool(self._confirm_sold_switch.get()))

    def _set_confirm_sold_preference(self, enabled: bool) -> None:
        self._update_preference(confirm_sold=enabled)

    def _on_confirm_unsold_switch_changed(self) -> None:
        self._set_confirm_unsold_preference(bool(self._confirm_unsold_switch.get()))

    def _set_confirm_unsold_preference(self, enabled: bool) -> None:
        self._update_preference(confirm_unsold=enabled)

    def _update_preference(self, **changes: bool) -> None:
        self._preferences = replace(self._preferences, **changes)
        preferences_service.save_preferences(self._preferences)

    # ------------------------------------------------------------------
    # Reset bar
    # ------------------------------------------------------------------

    def _build_reset_bar(self, parent: ctk.CTkBaseClass) -> ctk.CTkFrame:
        bar = ctk.CTkFrame(parent, fg_color=theme.SURFACE, corner_radius=10)
        bar.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            bar,
            text=(
                "Resets fullscreen, display, and confirmation preferences only. "
                "Auction sessions, saves, history, teams, and photos are never affected."
            ),
            font=theme.body_font(size=11),
            text_color=theme.TEXT_SECONDARY,
            anchor="w",
            justify="left",
            wraplength=700,
        ).grid(row=0, column=0, sticky="w", padx=18, pady=14)
        ctk.CTkButton(
            bar,
            text="RESET APP PREFERENCES",
            font=theme.body_font(size=12, weight="bold"),
            fg_color=theme.SURFACE_ALT,
            hover_color=theme.BORDER,
            text_color=theme.TEXT_PRIMARY,
            width=200,
            height=34,
            corner_radius=8,
            command=self._on_reset_preferences_clicked,
        ).grid(row=0, column=1, padx=18, pady=14)
        return bar

    def _on_reset_preferences_clicked(self) -> None:
        dialog = ctk.CTkToplevel(self)
        dialog.title("Reset App Preferences")
        dialog.configure(fg_color=theme.BACKGROUND)
        dialog.transient(self.winfo_toplevel())
        dialog.protocol("WM_DELETE_WINDOW", dialog.destroy)

        content = ctk.CTkFrame(dialog, fg_color="transparent")
        content.pack(fill="both", expand=True, padx=28, pady=24)

        ctk.CTkLabel(
            content, text="Reset App Preferences?", font=theme.heading_font(size=16), text_color=theme.TEXT_PRIMARY
        ).pack(pady=(0, 10))
        ctk.CTkLabel(
            content,
            text=(
                "Fullscreen, display, and SOLD/UNSOLD confirmation preferences will "
                "return to their defaults. Auction sessions, saves, and player data "
                "are never affected."
            ),
            font=theme.body_font(size=12),
            text_color=theme.TEXT_SECONDARY,
            wraplength=360,
            justify="center",
        ).pack(pady=(0, 16))

        button_row = ctk.CTkFrame(content, fg_color="transparent")
        button_row.pack()
        ctk.CTkButton(
            button_row,
            text="Reset",
            width=112,
            height=36,
            corner_radius=8,
            fg_color=theme.UNSOLD_RED,
            text_color=theme.TEXT_PRIMARY,
            command=lambda: self._confirm_reset(dialog),
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
            command=dialog.destroy,
        ).pack(side="left", padx=8)

        dialog.update_idletasks()
        width = max(dialog.winfo_reqwidth(), 320)
        height = dialog.winfo_reqheight()
        root = self.winfo_toplevel()
        x = root.winfo_rootx() + (root.winfo_width() - width) // 2
        y = root.winfo_rooty() + (root.winfo_height() - height) // 2
        dialog.geometry(f"{width}x{height}+{max(x, 0)}+{max(y, 0)}")
        dialog.grab_set()

    def _confirm_reset(self, dialog: ctk.CTkToplevel) -> None:
        dialog.destroy()
        self._execute_reset()

    def _execute_reset(self) -> None:
        """The reset action itself — a public-ish seam so tests can
        exercise it without opening a real confirmation dialog, matching
        the pattern already used by Live Auction's SOLD/UNSOLD flows."""
        preferences_service.reset_preferences()
        self._apply_window_fullscreen(False)
        self._render()

    # ------------------------------------------------------------------
    # 4. SESSION / SAVE STATUS
    # ------------------------------------------------------------------

    def _build_session_status_section(self, parent: ctk.CTkBaseClass) -> ctk.CTkFrame:
        section = _build_section(parent, "Session / Save Status")
        session = self._session

        if session is None or not session.started:
            ctk.CTkLabel(
                section,
                text="NO ACTIVE SESSION",
                font=theme.body_font(size=13, weight="bold"),
                text_color=theme.TEXT_SECONDARY,
                anchor="w",
            ).grid(row=1, column=0, sticky="w", padx=18, pady=(4, 16))
            return section

        status = session.auction.status.value
        if session.last_save_error:
            save_status, save_color = "SAVE FAILED", theme.UNSOLD_RED
        elif session.updated_at:
            save_status, save_color = "SAVED", theme.ACCENT_GREEN
        else:
            save_status, save_color = "NOT YET SAVED", theme.TEXT_SECONDARY

        try:
            save_path = persistence_service.session_save_path(session.mode, session.session_id)
            save_location = str(save_path.relative_to(WRITABLE_ROOT))
        except (ValueError, TypeError):
            save_location = _PLACEHOLDER

        rows = [
            ("Current Session", session.mode.value if session.mode else _PLACEHOLDER),
            ("Session ID", session.session_id or _PLACEHOLDER),
            ("Status", status),
            ("Current Round", session.round_number),
            ("SOLD Count", f"{session.sold_count} / {session.total_eligible_count}"),
            ("Last Autosave", format_timestamp(session.updated_at)),
        ]
        for index, (label, value) in enumerate(rows, start=1):
            _info_row(section, index, label, value)
        _info_row(section, len(rows) + 1, "Save Status", save_status, value_color=save_color)
        _info_row(section, len(rows) + 2, "Save Location", save_location)

        ctk.CTkButton(
            section,
            text="OPEN SAVE FOLDER",
            font=theme.body_font(size=12, weight="bold"),
            fg_color=theme.SURFACE_ALT,
            hover_color=theme.BORDER,
            text_color=theme.TEXT_PRIMARY,
            width=180,
            height=32,
            corner_radius=8,
            command=self._on_open_save_folder_clicked,
        ).grid(row=len(rows) + 3, column=0, sticky="w", padx=18, pady=(10, 16))

        return section

    def _on_open_save_folder_clicked(self) -> None:
        """OS-safe folder opening — never crashes if the folder doesn't
        exist yet (a session that hasn't autosaved even once) or if the
        current platform has no associated "open folder" mechanism."""
        session = self._session
        if session is None or not session.started or session.mode is None or session.session_id is None:
            return
        folder = persistence_service.session_save_path(session.mode, session.session_id).parent
        if not folder.is_dir():
            return
        try:
            if sys.platform == "win32" and hasattr(os, "startfile"):
                os.startfile(str(folder))  # noqa: S606 - organizer-triggered, opening a local app-owned folder
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(folder)])
            else:
                subprocess.Popen(["xdg-open", str(folder)])
        except OSError:
            pass

    # ------------------------------------------------------------------
    # 5. CAPTAIN BIDDING — Phase 2: PIN auth + mixed-mode connections
    # ------------------------------------------------------------------

    def _captain_bidding_server(self) -> object | None:
        """The real MainWindow.captain_bidding_server when parented under
        one — `getattr`-guarded so a bare test root (no such attribute)
        degrades to "server unavailable" instead of raising."""
        return getattr(self.winfo_toplevel(), "captain_bidding_server", None)

    def _build_captain_bidding_section(self, parent: ctk.CTkBaseClass) -> ctk.CTkFrame:
        section = _build_section(parent, "Captain Phone Bidding")
        section.grid_columnconfigure(0, weight=1)
        server = self._captain_bidding_server()
        state = server.state if server is not None else ServerLifecycleState.OFF
        is_running = state == ServerLifecycleState.RUNNING

        _STATE_BADGE_COLORS = {
            ServerLifecycleState.OFF: (theme.SURFACE_ALT, theme.TEXT_SECONDARY),
            ServerLifecycleState.STARTING: (theme.GOLD_ACCENT, theme.BACKGROUND),
            ServerLifecycleState.RUNNING: (theme.ACCENT_GREEN, theme.BACKGROUND),
            ServerLifecycleState.FAILED: (theme.UNSOLD_RED, theme.TEXT_PRIMARY),
            ServerLifecycleState.STOPPING: (theme.SURFACE_ALT, theme.TEXT_SECONDARY),
        }
        badge_fg, badge_text = _STATE_BADGE_COLORS[state]

        status_row = ctk.CTkFrame(section, fg_color="transparent")
        status_row.grid(row=1, column=0, sticky="ew", padx=18, pady=(0, 8))
        ctk.CTkLabel(
            status_row,
            text=state.value,
            font=theme.body_font(size=11, weight="bold"),
            fg_color=badge_fg,
            text_color=badge_text,
            corner_radius=6,
            width=120,
            height=22,
        ).grid(row=0, column=0, sticky="w")

        ctk.CTkLabel(
            section,
            text=(
                "Mixed mode: 0 to 4 captain phones may connect at once, each locked "
                "to its own team by PIN. Manual desktop bidding for any team — "
                "connected, disconnected, or never logged in — always remains "
                "available. The organizer laptop stays in sole control of SOLD/UNSOLD."
            ),
            font=theme.body_font(size=12),
            text_color=theme.TEXT_SECONDARY,
            anchor="w",
            justify="left",
            wraplength=760,
        ).grid(row=2, column=0, sticky="w", padx=18, pady=(0, 10))

        if is_running:
            lan_url = server.lan_url
            if lan_url is not None:
                lan_display = lan_url
            else:
                lan_display = f"Unavailable — try http://localhost:{server.actual_port} on this computer"
        else:
            lan_display = _PLACEHOLDER

        info_rows = [
            ("Server", state.value),
            ("LAN Address", lan_display),
            ("Authentication", "PIN ENABLED"),
        ]
        for index, (label, value) in enumerate(info_rows, start=3):
            _info_row(section, index, label, value)

        next_row = 3 + len(info_rows)

        if state == ServerLifecycleState.FAILED and server is not None and server.last_error:
            error_row = ctk.CTkFrame(section, fg_color="transparent")
            error_row.grid(row=next_row, column=0, sticky="ew", padx=18, pady=3)
            error_row.grid_columnconfigure(0, weight=1)
            ctk.CTkLabel(
                error_row, text="SERVER START FAILED", font=theme.body_font(size=12, weight="bold"),
                text_color=theme.UNSOLD_RED, anchor="w",
            ).grid(row=0, column=0, sticky="w")
            ctk.CTkLabel(
                error_row,
                text=f"Last Error: {server.last_error}",
                font=theme.body_font(size=11),
                text_color=theme.TEXT_SECONDARY,
                anchor="w",
                justify="left",
                wraplength=760,
            ).grid(row=1, column=0, sticky="w", pady=(2, 0))
            next_row += 1

        button_row = ctk.CTkFrame(section, fg_color="transparent")
        button_row.grid(row=next_row, column=0, sticky="w", padx=18, pady=(10, 2))
        can_start = server is not None and state in (ServerLifecycleState.OFF, ServerLifecycleState.FAILED)
        can_stop = server is not None and state in (
            ServerLifecycleState.STARTING,
            ServerLifecycleState.RUNNING,
            ServerLifecycleState.FAILED,
        )
        ctk.CTkButton(
            button_row,
            text="START CAPTAIN BIDDING",
            font=theme.body_font(size=12, weight="bold"),
            fg_color=theme.ACCENT_GREEN if can_start else theme.SURFACE_ALT,
            hover_color=theme.ACCENT_GREEN_HOVER if can_start else theme.SURFACE_ALT,
            text_color=theme.BACKGROUND if can_start else theme.TEXT_SECONDARY,
            state="normal" if can_start else "disabled",
            width=190,
            height=34,
            corner_radius=8,
            command=self._on_start_captain_bidding_clicked,
        ).pack(side="left", padx=(0, 8))
        ctk.CTkButton(
            button_row,
            text="STOP CAPTAIN BIDDING",
            font=theme.body_font(size=12, weight="bold"),
            fg_color=theme.UNSOLD_RED if can_stop else theme.SURFACE_ALT,
            hover_color="#c93f43" if can_stop else theme.SURFACE_ALT,
            text_color=theme.TEXT_PRIMARY if can_stop else theme.TEXT_SECONDARY,
            state="normal" if can_stop else "disabled",
            width=190,
            height=34,
            corner_radius=8,
            command=self._on_stop_captain_bidding_clicked,
        ).pack(side="left")
        next_row += 1

        if is_running and server.lan_url is not None:
            ctk.CTkButton(
                section,
                text="COPY ADDRESS",
                font=theme.body_font(size=11, weight="bold"),
                fg_color=theme.SURFACE_ALT,
                hover_color=theme.BORDER,
                text_color=theme.TEXT_PRIMARY,
                width=140,
                height=28,
                corner_radius=8,
                command=self._on_copy_address_clicked,
            ).grid(row=next_row, column=0, sticky="w", padx=18, pady=(6, 2))
            next_row += 1

        if is_running:
            ctk.CTkLabel(
                section,
                text=(
                    "If phones cannot connect, allow \"Calgary Vipers Auction 2026\" "
                    "through Windows Firewall on Private networks."
                ),
                font=theme.body_font(size=11),
                text_color=theme.TEXT_SECONDARY,
                anchor="w",
                justify="left",
                wraplength=760,
            ).grid(row=next_row, column=0, sticky="w", padx=18, pady=(0, 2))
            next_row += 1

        ctk.CTkFrame(section, fg_color=theme.BORDER, height=1, corner_radius=0).grid(
            row=next_row, column=0, sticky="ew", padx=18, pady=(6, 10)
        )
        next_row += 1

        ctk.CTkLabel(
            section,
            text="CAPTAIN PINS / CONNECTIONS",
            font=theme.body_font(size=12, weight="bold"),
            text_color=theme.TEXT_PRIMARY,
            anchor="w",
        ).grid(row=next_row, column=0, sticky="w", padx=18, pady=(0, 8))
        next_row += 1

        if server is None:
            ctk.CTkLabel(
                section,
                text="Captain bidding is unavailable in this context (no server object attached).",
                font=theme.body_font(size=12),
                text_color=theme.TEXT_SECONDARY,
                anchor="w",
            ).grid(row=next_row, column=0, sticky="w", padx=18, pady=(0, 12))
            next_row += 1
        else:
            if server.auth.pin_config_recovered:
                ctk.CTkLabel(
                    section,
                    text=(
                        "PIN configuration was missing or unreadable — a fresh set of "
                        "PINs was generated and saved automatically below."
                    ),
                    font=theme.body_font(size=11, weight="bold"),
                    text_color=theme.GOLD_ACCENT,
                    anchor="w",
                    justify="left",
                    wraplength=760,
                ).grid(row=next_row, column=0, sticky="w", padx=18, pady=(0, 8))
                next_row += 1

            pins = server.auth.get_pins()
            for entry in server.auth.connection_overview():
                next_row = self._build_team_connection_row(section, next_row, entry, pins)

            ctk.CTkButton(
                section,
                text="REGENERATE ALL PINS",
                font=theme.body_font(size=12, weight="bold"),
                fg_color=theme.UNSOLD_RED,
                hover_color="#c93f43",
                text_color=theme.TEXT_PRIMARY,
                width=210,
                height=34,
                corner_radius=8,
                command=self._on_regenerate_pins_clicked,
            ).grid(row=next_row, column=0, sticky="w", padx=18, pady=(10, 2))
            next_row += 1

            ctk.CTkLabel(
                section,
                text="Generates a new PIN for every team and immediately signs out every currently connected captain.",
                font=theme.body_font(size=11),
                text_color=theme.TEXT_SECONDARY,
                anchor="w",
                justify="left",
                wraplength=760,
            ).grid(row=next_row, column=0, sticky="w", padx=18, pady=(4, 4))
            next_row += 1

        ctk.CTkFrame(section, fg_color="transparent", height=12).grid(row=next_row, column=0)
        return section

    def _build_team_connection_row(
        self, section: ctk.CTkBaseClass, row: int, entry: dict, pins: dict[int, str]
    ) -> int:
        team_id = entry["team_id"]
        line = ctk.CTkFrame(section, fg_color=theme.SURFACE_ALT, corner_radius=8)
        line.grid(row=row, column=0, sticky="ew", padx=18, pady=4)
        line.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            line,
            text=entry["team_name"].upper(),
            font=theme.body_font(size=13, weight="bold"),
            text_color=theme.TEXT_PRIMARY,
            anchor="w",
            width=150,
        ).grid(row=0, column=0, sticky="w", padx=(14, 6), pady=10)

        pin_value = pins.get(team_id, "----")
        pin_row = ctk.CTkFrame(line, fg_color="transparent")
        pin_row.grid(row=0, column=1, sticky="w")
        ctk.CTkLabel(
            pin_row,
            text=f"PIN: {pin_value}",
            font=theme.body_font(size=13, weight="bold"),
            text_color=theme.GOLD_ACCENT,
        ).pack(side="left")
        ctk.CTkButton(
            pin_row,
            text="COPY",
            font=theme.body_font(size=10, weight="bold"),
            fg_color=theme.SURFACE,
            hover_color=theme.BORDER,
            text_color=theme.TEXT_PRIMARY,
            width=52,
            height=22,
            corner_radius=6,
            command=lambda pin=pin_value: self._on_copy_pin_clicked(pin),
        ).pack(side="left", padx=(10, 0))

        if entry["connected"]:
            status_text, status_color = "CONNECTED", theme.ACCENT_GREEN
        elif entry["authenticated"]:
            status_text, status_color = "DISCONNECTED", theme.GOLD_ACCENT
        else:
            status_text, status_color = "NOT CONNECTED", theme.TEXT_SECONDARY
        ctk.CTkLabel(
            line,
            text=status_text,
            font=theme.body_font(size=11, weight="bold"),
            text_color=status_color,
            anchor="e",
            width=110,
        ).grid(row=0, column=2, sticky="e", padx=(6, 6))

        ctk.CTkButton(
            line,
            text="RESET CONNECTION",
            font=theme.body_font(size=10, weight="bold"),
            fg_color=theme.SURFACE,
            hover_color=theme.BORDER,
            text_color=theme.TEXT_PRIMARY,
            width=140,
            height=26,
            corner_radius=6,
            command=lambda tid=team_id: self._on_reset_team_connection_clicked(tid),
        ).grid(row=0, column=3, sticky="e", padx=(0, 14))

        return row + 1

    def _on_start_captain_bidding_clicked(self) -> None:
        """`CaptainBiddingServer.start()` returns almost immediately,
        leaving the server in STARTING — this schedules a short poll
        (cancelled in `destroy()` if the organizer navigates away first)
        so the screen updates to RUNNING/FAILED on its own once the
        server actually finishes starting, without blocking this click
        handler (and therefore the whole GUI) on a wait loop."""
        server = self._captain_bidding_server()
        if server is not None:
            server.start()
        self._render()
        self._poll_captain_bidding_startup()

    def _poll_captain_bidding_startup(self) -> None:
        """RC2 targeted debug pass: this Tk `after()`-driven poll is also
        the bulletproof timeout backstop (`check_startup_timeout`) — it
        runs on the main thread, which stayed responsive even in the real
        organizer report where both the server thread and its own
        background watcher went silent for ~2 minutes with no further
        trace line. Calling it here guarantees STARTING can never persist
        indefinitely regardless of what those background threads do."""
        server = self._captain_bidding_server()
        if server is None:
            self._startup_poll_job = None
            self._render()
            return
        server.check_startup_timeout()
        if server.state != ServerLifecycleState.STARTING:
            self._startup_poll_job = None
            self._render()
            return
        self._startup_poll_job = self.after(200, self._poll_captain_bidding_startup)

    def _on_stop_captain_bidding_clicked(self) -> None:
        server = self._captain_bidding_server()
        if server is not None:
            server.stop()
        self._render()

    def _on_copy_address_clicked(self) -> None:
        server = self._captain_bidding_server()
        if server is None or not server.is_running or server.lan_url is None:
            return
        self._copy_to_clipboard(server.lan_url)

    def _on_copy_pin_clicked(self, pin: str) -> None:
        self._copy_to_clipboard(pin)

    def _copy_to_clipboard(self, value: str) -> None:
        try:
            self.clipboard_clear()
            self.clipboard_append(value)
        except Exception:  # noqa: BLE001 - clipboard access is best-effort and platform-dependent
            pass

    def _on_reset_team_connection_clicked(self, team_id: int) -> None:
        """Invalidates only that team's captain session — never the
        roster, budget, history, or live bid (see
        `CaptainAuthService.reset_team_session`). Safe to call even if
        the team was never connected."""
        server = self._captain_bidding_server()
        if server is not None:
            server.auth.reset_team_session(team_id)
        self._render()

    def _on_regenerate_pins_clicked(self) -> None:
        dialog = ctk.CTkToplevel(self)
        dialog.title("Regenerate All PINs")
        dialog.configure(fg_color=theme.BACKGROUND)
        dialog.transient(self.winfo_toplevel())
        dialog.protocol("WM_DELETE_WINDOW", dialog.destroy)

        content = ctk.CTkFrame(dialog, fg_color="transparent")
        content.pack(fill="both", expand=True, padx=28, pady=24)

        ctk.CTkLabel(
            content, text="Regenerate All PINs?", font=theme.heading_font(size=16), text_color=theme.TEXT_PRIMARY
        ).pack(pady=(0, 10))
        ctk.CTkLabel(
            content,
            text="This will invalidate existing captain logins. Continue?",
            font=theme.body_font(size=12),
            text_color=theme.TEXT_SECONDARY,
            wraplength=360,
            justify="center",
        ).pack(pady=(0, 16))

        button_row = ctk.CTkFrame(content, fg_color="transparent")
        button_row.pack()
        ctk.CTkButton(
            button_row,
            text="Regenerate",
            width=130,
            height=36,
            corner_radius=8,
            fg_color=theme.UNSOLD_RED,
            text_color=theme.TEXT_PRIMARY,
            command=lambda: self._confirm_regenerate_pins(dialog),
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
            command=dialog.destroy,
        ).pack(side="left", padx=8)

        dialog.update_idletasks()
        width = max(dialog.winfo_reqwidth(), 320)
        height = dialog.winfo_reqheight()
        root = self.winfo_toplevel()
        x = root.winfo_rootx() + (root.winfo_width() - width) // 2
        y = root.winfo_rooty() + (root.winfo_height() - height) // 2
        dialog.geometry(f"{width}x{height}+{max(x, 0)}+{max(y, 0)}")
        dialog.grab_set()

    def _confirm_regenerate_pins(self, dialog: ctk.CTkToplevel) -> None:
        dialog.destroy()
        self._execute_regenerate_pins()

    def _execute_regenerate_pins(self) -> None:
        """The regenerate action itself — a public-ish seam so tests can
        exercise it without opening a real confirmation dialog, matching
        `_execute_reset`'s existing pattern."""
        server = self._captain_bidding_server()
        if server is not None:
            server.auth.regenerate_pins()
        self._render()

    # ------------------------------------------------------------------
    # 6. DIAGNOSTICS / BUILD INFO
    # ------------------------------------------------------------------

    def _build_diagnostics_section(self, parent: ctk.CTkBaseClass) -> ctk.CTkFrame:
        section = _build_section(parent, "Diagnostics / Build Info")

        rows = [
            ("App Version", APP_VERSION),
            ("Python Version", self._python_version),
            ("Platform", self._platform_name),
            ("Players Loaded", f"{len(self._players)} / {EXPECTED_TOTAL_PLAYERS}"),
            ("Teams Loaded", f"{len(self._teams)} / {EXPECTED_TEAM_COUNT}"),
            ("Photos Found", f"{self._photos_found} / {self._photo_count}"),
            ("Save Schema Version", persistence_service.SCHEMA_VERSION),
        ]
        for index, (label, value) in enumerate(rows, start=1):
            _info_row(section, index, label, value)

        next_row = len(rows) + 1
        if self._missing_photos:
            ctk.CTkLabel(
                section,
                text=f"Missing photo(s): {', '.join(self._missing_photos)}",
                font=theme.body_font(size=11),
                text_color=theme.UNSOLD_RED,
                anchor="w",
                justify="left",
                wraplength=340,
            ).grid(row=next_row, column=0, sticky="w", padx=18, pady=(4, 0))
            next_row += 1

        validation_text = "TOURNAMENT SETUP: READY" if self._validation.is_ready else "TOURNAMENT SETUP: ISSUES FOUND"
        validation_color = theme.ACCENT_GREEN if self._validation.is_ready else theme.UNSOLD_RED
        ctk.CTkLabel(
            section, text=validation_text, font=theme.body_font(size=12, weight="bold"), text_color=validation_color, anchor="w"
        ).grid(row=next_row, column=0, sticky="w", padx=18, pady=(10, 4))
        next_row += 1

        if not self._validation.is_ready:
            for check in self._validation.failures:
                ctk.CTkLabel(
                    section,
                    text=f"- {check.label}: {check.detail}",
                    font=theme.body_font(size=11),
                    text_color=theme.TEXT_SECONDARY,
                    anchor="w",
                    justify="left",
                    wraplength=340,
                ).grid(row=next_row, column=0, sticky="w", padx=18, pady=(0, 2))
                next_row += 1

        ctk.CTkFrame(section, fg_color="transparent", height=8).grid(row=next_row, column=0)
        return section

    # ------------------------------------------------------------------
    # 7. TOURNAMENT RULES (READ-ONLY)
    # ------------------------------------------------------------------

    def _build_tournament_rules_section(self, parent: ctk.CTkBaseClass) -> ctk.CTkFrame:
        section = _build_section(parent, "Tournament Rules")

        ctk.CTkLabel(
            section,
            text="LOCKED TOURNAMENT RULES",
            font=theme.body_font(size=11, weight="bold"),
            fg_color=theme.UNSOLD_RED,
            text_color=theme.TEXT_PRIMARY,
            corner_radius=6,
            width=180,
            height=22,
        ).grid(row=1, column=0, sticky="w", padx=18, pady=(0, 10))

        rules = [
            ("Teams", str(EXPECTED_TEAM_COUNT)),
            ("Starting Budget", f"{self._settings.get('starting_budget_millions', '—')}M"),
            ("Squad Size", str(self._settings.get("max_squad_size", "—"))),
            ("Captains", "1 pre-assigned per team"),
            ("Auction Players", str(EXPECTED_AUCTION_ELIGIBLE_COUNT)),
            ("Outfield Base Price", f"{OUTFIELD_BASE_PRICE}M"),
            ("GK Base Price", f"{GK_BASE_PRICE}M"),
            ("GK Rule", "Exactly one GK per team"),
            (
                "Completion Reserve",
                "A team must always retain enough budget to complete its remaining mandatory "
                "roster slots at minimum prices.",
            ),
            ("UNSOLD", "Re-auctioned in later rounds until sold or the auction becomes blocked"),
            ("First Auction Remaining Budget", "Carries forward for future transfer activity."),
        ]
        for index, (label, value) in enumerate(rules, start=2):
            line = ctk.CTkFrame(section, fg_color="transparent")
            line.grid(row=index, column=0, sticky="ew", padx=18, pady=3)
            line.grid_columnconfigure(1, weight=1)
            ctk.CTkLabel(
                line, text=label, font=theme.body_font(size=12), text_color=theme.TEXT_SECONDARY, anchor="w", width=160
            ).grid(row=0, column=0, sticky="w")
            ctk.CTkLabel(
                line,
                text=value,
                font=theme.body_font(size=12, weight="bold"),
                text_color=theme.TEXT_PRIMARY,
                anchor="w",
                justify="left",
                wraplength=520,
            ).grid(row=0, column=1, sticky="w")

        ctk.CTkFrame(section, fg_color="transparent", height=16).grid(row=len(rules) + 2, column=0)
        return section


def build_settings_screen(parent: ctk.CTkBaseClass, session: AuctionSession | None = None) -> ctk.CTkFrame:
    return SettingsScreen(parent, session=session)
