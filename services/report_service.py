"""Auction Reports: read-only analytics/summary dashboard for the current
AuctionSession.

Deliberately separate from services/auction_history_service.py: that
module supports the chronological event-log screen (Auction History);
this module supports aggregated statistics, team summaries, and
comparisons (Reports). Neither reads a second data source — everything
here is derived from the session's own `players`/`teams`/`auction.history`
snapshot, and nothing here mutates any of them.

Price/OVR/FPL statistics are always computed from SOLD entries only.
UNSOLD rows never contribute a price/rating/FPL value to an average,
median, min, or max — an UNSOLD attempt has no sale price at all, and
treating a missing value as 0 would silently corrupt every aggregate.

Per-team purchase data is deliberately read from each `Player`'s own
`sold_price`/`sold_to`/`auction_status` fields (set exactly once by
services/auction_service.process_sale) rather than re-scanning
`auction.history` per team — a player can only ever be SOLD once (SOLD is
the sole permanent outcome), so this can never double-count an earlier
UNSOLD attempt from a prior re-auction round. Global/session-wide
statistics use `auction.history` directly instead, since it is naturally
a flat list of every attempt; both approaches necessarily agree because
`process_sale` sets a player's `sold_price` and that transaction's history
entry's `sold_price` atomically, from the same value.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from statistics import median

from models.auction import AuctionHistoryEntry
from models.player import Player, PlayerAuctionStatus
from models.team import Team
from services.auction_service import team_has_goalkeeper
from services.auction_session_service import AuctionSession

SOLD = "SOLD"
UNSOLD = "UNSOLD"

CAPTAIN_ROLE = "CAPTAIN"
PURCHASE_ROLE = "PURCHASE"


# ----------------------------------------------------------------------
# Price statistics (SOLD entries only)
# ----------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PriceStatistics:
    sold_count: int
    total_spent: int
    average_price: float | None
    median_price: float | None
    highest_price: int | None
    highest_price_entries: list[AuctionHistoryEntry]
    lowest_price: int | None
    lowest_price_entries: list[AuctionHistoryEntry]


def calculate_price_statistics(history: Iterable[AuctionHistoryEntry]) -> PriceStatistics:
    """Price analytics over SOLD entries only. Gracefully returns all-None
    fields (never a crash or a fake 0) when there are no SOLD entries yet —
    e.g. a session that just started, or one where nothing has sold."""
    sold_entries = [entry for entry in history if entry.status == SOLD]
    if not sold_entries:
        return PriceStatistics(
            sold_count=0,
            total_spent=0,
            average_price=None,
            median_price=None,
            highest_price=None,
            highest_price_entries=[],
            lowest_price=None,
            lowest_price_entries=[],
        )

    prices = [entry.sold_price for entry in sold_entries]
    total = sum(prices)
    highest = max(prices)
    lowest = min(prices)
    return PriceStatistics(
        sold_count=len(sold_entries),
        total_spent=total,
        average_price=total / len(sold_entries),
        median_price=float(median(prices)),
        highest_price=highest,
        highest_price_entries=[entry for entry in sold_entries if entry.sold_price == highest],
        lowest_price=lowest,
        lowest_price_entries=[entry for entry in sold_entries if entry.sold_price == lowest],
    )


# ----------------------------------------------------------------------
# Re-auction statistics
# ----------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ReauctionStatistics:
    unsold_attempts: int
    unique_unsold_players: int
    eventually_sold_after_unsold: int
    max_attempts_for_one_player: int
    rounds_reached: int

    @property
    def has_activity(self) -> bool:
        return self.unsold_attempts > 0


def calculate_reauction_statistics(history: Iterable[AuctionHistoryEntry], rounds_reached: int) -> ReauctionStatistics:
    history = list(history)
    unsold_attempts = sum(1 for entry in history if entry.status == UNSOLD)

    statuses_by_player: dict[int, list[str]] = {}
    for entry in history:
        statuses_by_player.setdefault(entry.player_id, []).append(entry.status)

    unique_unsold_players = [player_id for player_id, statuses in statuses_by_player.items() if UNSOLD in statuses]
    eventually_sold_after_unsold = sum(1 for player_id in unique_unsold_players if SOLD in statuses_by_player[player_id])
    max_attempts = max((len(statuses) for statuses in statuses_by_player.values()), default=0)

    return ReauctionStatistics(
        unsold_attempts=unsold_attempts,
        unique_unsold_players=len(unique_unsold_players),
        eventually_sold_after_unsold=eventually_sold_after_unsold,
        max_attempts_for_one_player=max_attempts,
        rounds_reached=rounds_reached,
    )


# ----------------------------------------------------------------------
# FPL / OVR statistics
# ----------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FplStatistics:
    """FPL is purely descriptive analytics — it never affects auction
    rules. A player with no last-season result (`None`) is always
    excluded from averages, never coerced to 0."""

    highest_fpl_value: int | None
    highest_fpl_entries: list[AuctionHistoryEntry]
    average_fpl_of_known: float | None
    known_count: int
    na_count: int


def calculate_fpl_statistics(
    history: Iterable[AuctionHistoryEntry], players_by_id: dict[int, Player]
) -> FplStatistics:
    sold_entries = [entry for entry in history if entry.status == SOLD]
    known: list[tuple[AuctionHistoryEntry, int]] = []
    na_count = 0
    for entry in sold_entries:
        player = players_by_id.get(entry.player_id)
        fpl = player.last_season_fpl_points if player is not None else None
        if fpl is None:
            na_count += 1
        else:
            known.append((entry, fpl))

    if not known:
        return FplStatistics(None, [], None, 0, na_count)

    highest_value = max(value for _entry, value in known)
    highest_entries = [entry for entry, value in known if value == highest_value]
    average = sum(value for _entry, value in known) / len(known)
    return FplStatistics(highest_value, highest_entries, average, len(known), na_count)


@dataclass(frozen=True, slots=True)
class OvrStatistics:
    """OVR of SOLD auction purchases only — captains never appear in
    `auction.history`, so this naturally excludes them without any extra
    filtering (see PROJECT_CONTEXT.md's captain-handling rule)."""

    average_ovr_sold: float | None
    highest_ovr_value: int | None
    highest_ovr_entries: list[AuctionHistoryEntry]


def calculate_ovr_statistics(history: Iterable[AuctionHistoryEntry]) -> OvrStatistics:
    sold_entries = [entry for entry in history if entry.status == SOLD]
    if not sold_entries:
        return OvrStatistics(None, None, [])
    average = sum(entry.overall_rating for entry in sold_entries) / len(sold_entries)
    highest = max(entry.overall_rating for entry in sold_entries)
    return OvrStatistics(
        average_ovr_sold=average,
        highest_ovr_value=highest,
        highest_ovr_entries=[entry for entry in sold_entries if entry.overall_rating == highest],
    )


# ----------------------------------------------------------------------
# Per-team report
# ----------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RosterEntry:
    player: Player
    role: str  # CAPTAIN or PURCHASE
    price: int | None  # None for a captain — never displayed as "0M"


@dataclass(frozen=True, slots=True)
class TeamReport:
    team: Team
    captain: Player | None
    squad_size: int
    max_squad_size: int
    total_spent: int
    remaining_budget: int
    maximum_legal_bid: int
    has_goalkeeper: bool
    purchases_count: int
    average_purchase_price: float | None
    highest_purchase_player: Player | None
    lowest_purchase_player: Player | None
    average_ovr_squad: float | None
    average_ovr_purchases: float | None
    average_fpl_squad: float | None
    average_fpl_purchases: float | None
    roster: list[RosterEntry]

    @property
    def is_full(self) -> bool:
        return self.squad_size >= self.max_squad_size


def build_team_report(team: Team, players_by_id: dict[int, Player]) -> TeamReport:
    """Reads purchase data from each roster Player's own `sold_price`/
    `auction_status` (never from `auction.history` directly) — see this
    module's docstring for why that makes double-counting a re-auctioned
    player structurally impossible rather than something to guard against."""
    roster_players = [players_by_id[player_id] for player_id in team.roster if player_id in players_by_id]
    captain = next((player for player in roster_players if player.is_captain), None)
    purchase_players = [
        player
        for player in roster_players
        if not player.is_captain and player.auction_status == PlayerAuctionStatus.SOLD
    ]
    purchase_prices = [player.sold_price for player in purchase_players if player.sold_price is not None]

    roster_entries = [
        RosterEntry(player=player, role=CAPTAIN_ROLE, price=None)
        if player.is_captain
        else RosterEntry(player=player, role=PURCHASE_ROLE, price=player.sold_price)
        for player in roster_players
    ]

    highest_player = max(purchase_players, key=lambda player: player.sold_price, default=None)
    lowest_player = min(purchase_players, key=lambda player: player.sold_price, default=None)

    squad_ovrs = [player.overall_rating for player in roster_players]
    purchase_ovrs = [player.overall_rating for player in purchase_players]
    squad_fpls = [player.last_season_fpl_points for player in roster_players if player.last_season_fpl_points is not None]
    purchase_fpls = [
        player.last_season_fpl_points for player in purchase_players if player.last_season_fpl_points is not None
    ]

    def _average(values: list) -> float | None:
        return (sum(values) / len(values)) if values else None

    return TeamReport(
        team=team,
        captain=captain,
        squad_size=team.roster_size,
        max_squad_size=team.max_squad_size,
        total_spent=team.auction_spending,
        remaining_budget=team.remaining_budget,
        maximum_legal_bid=team.maximum_legal_bid,
        has_goalkeeper=team_has_goalkeeper(team, players_by_id.values()),
        purchases_count=len(purchase_players),
        average_purchase_price=_average(purchase_prices),
        highest_purchase_player=highest_player,
        lowest_purchase_player=lowest_player,
        average_ovr_squad=_average(squad_ovrs),
        average_ovr_purchases=_average(purchase_ovrs),
        average_fpl_squad=_average(squad_fpls),
        average_fpl_purchases=_average(purchase_fpls),
        roster=roster_entries,
    )


# ----------------------------------------------------------------------
# Top-level auction summary
# ----------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AuctionSummary:
    sold_count: int
    total_eligible: int
    remaining_unsold_count: int
    sold_percentage: float
    total_attempts: int
    round_number: int
    completed_team_count: int
    total_team_count: int
    combined_remaining_budget: int
    price_stats: PriceStatistics
    reauction_stats: ReauctionStatistics


def build_auction_summary(session: AuctionSession) -> AuctionSummary:
    if not session.started:
        raise ValueError("Cannot build a report for a session that has not started.")

    history = session.auction.history
    sold_count = session.sold_count
    total_eligible = session.total_eligible_count
    remaining = max(total_eligible - sold_count, 0)
    sold_percentage = (sold_count / total_eligible * 100) if total_eligible else 0.0
    completed_teams = sum(1 for team in session.teams if team.roster_size >= team.max_squad_size)
    combined_remaining_budget = sum(team.remaining_budget for team in session.teams)

    return AuctionSummary(
        sold_count=sold_count,
        total_eligible=total_eligible,
        remaining_unsold_count=remaining,
        sold_percentage=sold_percentage,
        total_attempts=len(history),
        round_number=session.round_number,
        completed_team_count=completed_teams,
        total_team_count=len(session.teams),
        combined_remaining_budget=combined_remaining_budget,
        price_stats=calculate_price_statistics(history),
        reauction_stats=calculate_reauction_statistics(history, session.round_number),
    )


@dataclass(frozen=True, slots=True)
class AuctionReport:
    """Everything the Reports screen needs, built in one call."""

    summary: AuctionSummary
    fpl_stats: FplStatistics
    ovr_stats: OvrStatistics
    team_reports: list[TeamReport]


def build_full_report(session: AuctionSession) -> AuctionReport:
    """The single entry point the UI calls. Read-only: builds plain
    dataclasses from `session`'s current state and never mutates it."""
    if not session.started:
        raise ValueError("Cannot build a report for a session that has not started.")

    players_by_id = {player.id: player for player in session.players}
    history = session.auction.history

    return AuctionReport(
        summary=build_auction_summary(session),
        fpl_stats=calculate_fpl_statistics(history, players_by_id),
        ovr_stats=calculate_ovr_statistics(history),
        team_reports=[build_team_report(team, players_by_id) for team in session.teams],
    )
