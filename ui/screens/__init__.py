"""Real (non-placeholder) screen builders, keyed by sidebar navigation name.

MainWindow looks a nav name up here first; anything not listed still gets
Milestone 4's generic placeholder screen.
"""

from __future__ import annotations

from collections.abc import Callable

import customtkinter as ctk

from services.auction_session_service import AuctionSession

from .auction_history_screen import build_auction_history_screen
from .live_auction_screen import build_live_auction_screen
from .player_cards_screen import build_player_cards_screen
from .players_setup_screen import build_players_setup_screen
from .reports_screen import build_reports_screen
from .settings_screen import build_settings_screen
from .teams_screen import build_teams_screen

SCREEN_BUILDERS: dict[str, Callable[[ctk.CTkBaseClass, AuctionSession], ctk.CTkFrame]] = {
    "Live Auction": build_live_auction_screen,
    "Player Cards": build_player_cards_screen,
    "Players & Setup": build_players_setup_screen,
    "Teams": build_teams_screen,
    "Auction History": build_auction_history_screen,
    "Reports": build_reports_screen,
    "Settings": build_settings_screen,
}

__all__ = [
    "SCREEN_BUILDERS",
    "build_auction_history_screen",
    "build_live_auction_screen",
    "build_player_cards_screen",
    "build_players_setup_screen",
    "build_reports_screen",
    "build_settings_screen",
    "build_teams_screen",
]
