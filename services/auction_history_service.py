"""Auction History business logic: filtering, searching, sorting, and
summarizing the existing append-only transaction log.

There is no second/competing history store here — every function in this
module reads `Auction.history` (a list of `AuctionHistoryEntry`, already
written by `services/auction_service.py`'s `process_sale`/`process_unsold`)
and never mutates it. History is attempt-based, not player-based: a player
UNSOLD in round 1, UNSOLD again in round 2, then SOLD in round 3 keeps all
three entries independently — nothing here deduplicates by player_id.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from models.auction import AuctionHistoryEntry
from models.player import Player

SOLD = "SOLD"
UNSOLD = "UNSOLD"

ALL_RESULTS = "ALL"
ALL_ROUNDS = "ALL ROUNDS"
ALL_TEAMS = "ALL TEAMS"

OLDEST_FIRST = "Oldest → Newest"
NEWEST_FIRST = "Newest → Oldest"


@dataclass(frozen=True, slots=True)
class HistorySummary:
    total_attempts: int
    sold_count: int
    unsold_count: int
    total_spent: int


def build_short_name_lookup(players: Iterable[Player] | None) -> dict[int, str]:
    """player_id -> short_name, used only for search.

    A history entry stores the player's full name at the time of the
    attempt but not their short name, so search needs the current player
    snapshot to resolve it. A missing id (a player somehow absent from the
    snapshot) simply doesn't match on short name — it still matches on
    full name, which every entry always carries.
    """
    if players is None:
        return {}
    return {player.id: player.short_name for player in players}


def round_numbers(history: Iterable[AuctionHistoryEntry]) -> list[int]:
    """Distinct round numbers present in history, ascending — the round
    filter's dynamic options. Legacy entries with no stored round_number
    (a save file from before this field existed) are excluded; there is
    nothing reliable to show for them."""
    return sorted({entry.round_number for entry in history if entry.round_number is not None})


def filter_history(
    history: Iterable[AuctionHistoryEntry],
    search: str = "",
    result: str = ALL_RESULTS,
    round_number: int | str | None = ALL_ROUNDS,
    team: str = ALL_TEAMS,
    short_names: dict[int, str] | None = None,
) -> list[AuctionHistoryEntry]:
    """Filter history entries by result/round/team plus a name search.

    Preserves input order (chronological, since history is append-only) —
    sorting is a separate step via `sort_history`. `team` filtering only
    ever matches SOLD entries (an UNSOLD entry's `team` is always `None`,
    so any specific team filter naturally excludes UNSOLD rows, matching
    the rule that UNSOLD attempts are never attributed to a team).
    """
    short_names = short_names or {}
    query = search.strip().lower()

    wanted_result = None if result in (None, ALL_RESULTS) else result
    wanted_round = None if round_number in (None, ALL_ROUNDS) else int(round_number)
    wanted_team = None if team in (None, ALL_TEAMS) else team

    matches = []
    for entry in history:
        if wanted_result is not None and entry.status != wanted_result:
            continue
        if wanted_round is not None and entry.round_number != wanted_round:
            continue
        if wanted_team is not None and entry.team != wanted_team:
            continue
        if query:
            short_name = short_names.get(entry.player_id, "")
            if query not in entry.player_name.lower() and query not in short_name.lower():
                continue
        matches.append(entry)
    return matches


def sort_history(history: Iterable[AuctionHistoryEntry], order: str = OLDEST_FIRST) -> list[AuctionHistoryEntry]:
    """Order history entries by their auction sequence — never by price or
    player name, since this is an event timeline, not a leaderboard."""
    return sorted(history, key=lambda entry: entry.auction_sequence, reverse=order == NEWEST_FIRST)


def summarize_history(history: Iterable[AuctionHistoryEntry]) -> HistorySummary:
    """Session-wide totals — always computed over the full history, not
    whatever the current filter/search happens to be showing.

    `unsold_count` counts UNSOLD *attempts*, not currently-unsold unique
    players: a player marked UNSOLD twice contributes 2 to this count.
    `total_spent` sums only SOLD prices.
    """
    history = list(history)
    sold_entries = [entry for entry in history if entry.status == SOLD]
    unsold_count = sum(1 for entry in history if entry.status == UNSOLD)
    total_spent = sum(entry.sold_price or 0 for entry in sold_entries)
    return HistorySummary(
        total_attempts=len(history),
        sold_count=len(sold_entries),
        unsold_count=unsold_count,
        total_spent=total_spent,
    )
