"""Second Auction — Milestone 2: the bidding engine (October 2026).

Initializes a `SecondAuctionState` from a CONFIRMED `SecondAuctionSetup`,
then runs SOLD/UNSOLD/re-auction over the 16-player release pool.

Deliberately NOT a reuse of `services/auction_service.py`'s `Team`/
`Player` mutation: the ticket's own "do not assume first-auction
architecture can simply be reused unchanged" warning applies most
directly here. This module:

- never calls `process_sale`/`process_unsold`/`place_bid`
- never mutates a `Team`'s `remaining_budget`/`roster`/`auction_spending`
- never mutates a `Player`'s `sold_price`/`sold_to`/`auction_status`
- never touches the first auction's `Auction.history`

Every second-auction budget/roster/transaction fact lives only on the
`SecondAuctionState` passed in — see models/second_auction.py's own
docstring. Each team's starting budget comes directly from
`services/second_auction_service.build_all_release_previews` (never a
second formula), and "previous team"/"original first-auction price" for
a released player comes directly from that same module's
`build_release_pool` (never duplicated here either).

The reserve/max-legal-bid rule below is also deliberately NOT the first
auction's dynamic goalkeeper-aware formula (`Team.maximum_legal_bid`):
every second-auction team already has its mandatory goalkeeper (GKs are
never released), and every player in the pool is outfield (GKs are never
released either), so the reserve only ever needs to cover the remaining
roster slots at `OUTFIELD_BASE_PRICE` each — see
`minimum_completion_reserve_after_purchase`.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone

from models.auction import AuctionStatus
from models.match_result import MatchResult
from models.player import OUTFIELD_BASE_PRICE, Player, player_base_price
from models.second_auction import (
    SecondAuctionHistoryEntry,
    SecondAuctionSetup,
    SecondAuctionState,
    SecondAuctionStatus,
)
from models.team import Team
from services import second_auction_service
from services.randomization_service import shuffle_player_ids
from services.second_auction_service import ReleasePoolEntry

SOLD = "SOLD"
UNSOLD = "UNSOLD"


class SecondAuctionBiddingError(Exception):
    """Raised when a second-auction initialize/SOLD/UNSOLD action is not
    currently valid. Deliberately distinct from `AuctionTransactionError`/
    `SecondAuctionError` (the Milestone 1 release-setup exception) — the
    bidding engine is its own independent subsystem."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ----------------------------------------------------------------------
# Reserve / maximum-legal-bid rule (second-auction-specific — see module
# docstring for why this is not the first auction's formula)
# ----------------------------------------------------------------------


def remaining_slots_to_fill(roster_size: int, max_squad_size: int) -> int:
    return max(max_squad_size - roster_size, 0)


def minimum_completion_reserve_after_purchase(roster_size: int, max_squad_size: int) -> int:
    """How much budget must stay unspent after the purchase under
    consideration to still fill every remaining roster slot at
    `OUTFIELD_BASE_PRICE` — no goalkeeper case to consider (see module
    docstring), so this is always simply `slots_after * OUTFIELD_BASE_PRICE`."""
    slots_after = max(remaining_slots_to_fill(roster_size, max_squad_size) - 1, 0)
    return slots_after * OUTFIELD_BASE_PRICE


def maximum_legal_bid(remaining_budget: int, roster_size: int, max_squad_size: int) -> int:
    """The most a team with `roster_size` current players and
    `remaining_budget` could legally pay for its next second-auction
    purchase. May be negative (never floored) — a caller displaying it
    should clamp with `max(value, 0)`, exactly as the first auction's
    own `Team.maximum_legal_bid` callers already do."""
    reserve = minimum_completion_reserve_after_purchase(roster_size, max_squad_size)
    return remaining_budget - reserve


def team_can_bid(remaining_budget: int, roster_size: int, max_squad_size: int) -> bool:
    if roster_size >= max_squad_size:
        return False
    return maximum_legal_bid(remaining_budget, roster_size, max_squad_size) >= OUTFIELD_BASE_PRICE


def team_roster_size(state: SecondAuctionState, team_id: int) -> int:
    return len(state.active_rosters_by_team.get(team_id, []))


def team_remaining_budget(state: SecondAuctionState, team_id: int) -> int:
    return state.remaining_budgets_by_team.get(team_id, 0)


def team_max_legal_bid(state: SecondAuctionState, team: Team) -> int:
    return maximum_legal_bid(
        team_remaining_budget(state, team.id), team_roster_size(state, team.id), team.max_squad_size
    )


def team_can_bid_for_player(state: SecondAuctionState, team: Team, player: Player) -> bool:
    """Whether `team` could currently legally buy `player` at its own
    minimum base price — the second-auction equivalent of
    `services/auction_service.py`'s `team_can_bid_for_player`."""
    roster_size = team_roster_size(state, team.id)
    if roster_size >= team.max_squad_size:
        return False
    base_price = player_base_price(player)
    return base_price <= maximum_legal_bid(team_remaining_budget(state, team.id), roster_size, team.max_squad_size)


def team_ineligibility_reason(state: SecondAuctionState, team: Team) -> str:
    """Human-readable reason `team` cannot currently bid at all right
    now, regardless of price — mirrors `services/auction_service.py`'s
    `team_ineligibility_reason`/`budget_reserve_violation_message`
    wording so the UI stays consistent between the two auctions."""
    roster_size = team_roster_size(state, team.id)
    if roster_size >= team.max_squad_size:
        return f"{team.name}'s roster is full."
    remaining_budget = team_remaining_budget(state, team.id)
    reserve = minimum_completion_reserve_after_purchase(roster_size, team.max_squad_size)
    slots_after = max(remaining_slots_to_fill(roster_size, team.max_squad_size) - 1, 0)
    if reserve and slots_after > 0:
        spot_word = "spot" if slots_after == 1 else "spots"
        displayed_max = max(maximum_legal_bid(remaining_budget, roster_size, team.max_squad_size), 0)
        return (
            f"{team.name} must reserve {reserve}M of its remaining second-auction budget to fill its "
            f"{slots_after} remaining roster {spot_word} at minimum prices. Maximum legal price is {displayed_max}M."
        )
    return f"{team.name} does not have enough second-auction budget."


# ----------------------------------------------------------------------
# Initialization
# ----------------------------------------------------------------------


def release_pool_lookup(
    teams: Iterable[Team], setup: SecondAuctionSetup, players_by_id: dict[int, Player]
) -> dict[int, ReleasePoolEntry]:
    """Every confirmed release, keyed by player id — reused by both
    initialization (to freeze `released_player_ids`) and SOLD/UNSOLD
    processing (to look up a player's previous team/original price for
    the second-auction history entry), never duplicated as separate
    tracking on `SecondAuctionState` itself."""
    return {
        entry.player.id: entry
        for entry in second_auction_service.build_release_pool(teams, setup, players_by_id)
    }


def _validate_initialization_preconditions(
    first_auction_status: AuctionStatus,
    setup: SecondAuctionSetup,
    state: SecondAuctionState,
    teams: list[Team],
    players_by_id: dict[int, Player],
) -> None:
    if first_auction_status != AuctionStatus.COMPLETE:
        raise SecondAuctionBiddingError(
            "The second auction requires a completed first auction "
            f"(current status: {first_auction_status.value})."
        )
    if state.is_initialized:
        raise SecondAuctionBiddingError("The second auction has already been started.")
    if not setup.is_confirmed:
        raise SecondAuctionBiddingError("The second auction requires a confirmed release plan.")
    if not second_auction_service.all_teams_have_exactly_four(setup, teams):
        raise SecondAuctionBiddingError(
            "Every team must have exactly "
            f"{second_auction_service.MAX_RELEASES_PER_TEAM} confirmed releases before starting."
        )

    pool_ids = [player_id for team in teams for player_id in setup.selections_for(team.id)]
    expected_pool_size = len(teams) * second_auction_service.MAX_RELEASES_PER_TEAM
    if len(pool_ids) != expected_pool_size:
        raise SecondAuctionBiddingError(
            f"Expected exactly {expected_pool_size} released players, found {len(pool_ids)}."
        )
    if len(pool_ids) != len(set(pool_ids)):
        raise SecondAuctionBiddingError("The release pool contains a duplicate player.")

    for team in teams:
        for player_id in setup.selections_for(team.id):
            if player_id not in team.roster:
                raise SecondAuctionBiddingError(
                    f"{team.name}'s release selection does not match its first-auction roster."
                )
            player = players_by_id.get(player_id)
            if player is None:
                raise SecondAuctionBiddingError("A released player could not be resolved.")
            if second_auction_service.is_locked_player(player):
                reason = "the captain" if player.is_captain else "the goalkeeper"
                raise SecondAuctionBiddingError(f"{player.full_name} is {reason} and cannot be released.")


def initialize_second_auction(
    *,
    first_auction_status: AuctionStatus,
    setup: SecondAuctionSetup,
    current_state: SecondAuctionState,
    teams: list[Team],
    players_by_id: dict[int, Player],
    match_results: Iterable[MatchResult],
    seed: int | None = None,
) -> SecondAuctionState:
    """Validate every precondition, then return a brand-new, freshly
    initialized `SecondAuctionState` (mirrors `services/randomization_
    service.create_auction`'s "build a new immutable state, let the
    caller assign it" shape rather than mutating `current_state` in
    place). Raises `SecondAuctionBiddingError` (leaving `current_state`
    and every `Team`/`Player` object completely untouched) on any
    validation failure.
    """
    teams = list(teams)
    _validate_initialization_preconditions(first_auction_status, setup, current_state, teams, players_by_id)

    previews_by_team_id = {
        preview.team.id: preview
        for preview in second_auction_service.build_all_release_previews(teams, setup, players_by_id, match_results)
    }

    released_ids: list[int] = []
    starting_budgets: dict[int, int] = {}
    active_rosters: dict[int, list[int]] = {}
    for team in teams:
        selected = setup.selections_for(team.id)
        released_ids.extend(selected)
        starting_budgets[team.id] = previews_by_team_id[team.id].second_auction_starting_budget
        active_rosters[team.id] = [player_id for player_id in team.roster if player_id not in selected]

    return SecondAuctionState(
        status=SecondAuctionStatus.IN_PROGRESS,
        queue=shuffle_player_ids(released_ids, seed=seed),
        current_queue_position=0,
        round_number=1,
        released_player_ids=released_ids,
        starting_budgets_by_team=starting_budgets,
        remaining_budgets_by_team=dict(starting_budgets),
        active_rosters_by_team=active_rosters,
        history=[],
        random_seed=seed,
        initialized_at=_now_iso(),
        completed_at=None,
    )


# ----------------------------------------------------------------------
# SOLD / UNSOLD
# ----------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SecondAuctionTransactionResult:
    player: Player
    outcome: str  # SOLD or UNSOLD
    team: Team | None
    sale_price: int | None
    history_entry: SecondAuctionHistoryEntry
    status: SecondAuctionStatus
    is_complete: bool
    next_player_id: int | None


def sold_player_ids(state: SecondAuctionState) -> set[int]:
    """Ids of every player permanently SOLD in the second auction, across
    all re-auction rounds — the second-auction equivalent of `services/
    auction_service.py`'s `sold_player_ids`."""
    return {entry.player_id for entry in state.history if entry.status == SOLD}


def _validate_active_state(state: SecondAuctionState) -> None:
    if not state.is_initialized:
        raise SecondAuctionBiddingError("The second auction has not been started yet.")
    if state.status == SecondAuctionStatus.BLOCKED:
        raise SecondAuctionBiddingError(
            "The second auction is blocked: no eligible team can purchase the remaining player(s)."
        )
    if state.status == SecondAuctionStatus.COMPLETE:
        raise SecondAuctionBiddingError("The second auction is already complete.")


def _validate_sale_price(sale_price: object) -> int:
    if isinstance(sale_price, bool) or not isinstance(sale_price, int) or sale_price < 1:
        raise SecondAuctionBiddingError("Sale price must be a positive integer.")
    return sale_price


def _resolve_team(teams: list[Team], team_id_or_name: int | str) -> Team | None:
    for team in teams:
        if team.id == team_id_or_name or team.name == team_id_or_name:
            return team
    return None


def _next_sequence(state: SecondAuctionState) -> int:
    return len(state.history) + 1


def _finish_transaction(
    state: SecondAuctionState, teams: list[Team], players_by_id: dict[int, Player]
) -> SecondAuctionStatus:
    """Advance to the next player, transitioning rounds, completing, or
    blocking as needed — the second-auction equivalent of `services/
    auction_service.py`'s `_finish_transaction`. Called only after this
    transaction's own mutation has already succeeded.
    """
    # Captain Phone Bidding for the second auction (Milestone 3): the
    # live current_bid/leading_team_id are attempt-scoped, never
    # player-scoped — cleared unconditionally before revealing whatever
    # comes next, exactly mirroring services/auction_service.py's own
    # identical reset (see services/second_auction_live_bid_service.py,
    # the only other code that writes these two fields).
    state.current_bid = None
    state.leading_team_id = None

    state.advance()
    if not state.queue_exhausted:
        return state.status  # more players left in this round

    sold_ids = sold_player_ids(state)
    pending_ids = [player_id for player_id in state.released_player_ids if player_id not in sold_ids]
    if not pending_ids:
        state.status = SecondAuctionStatus.COMPLETE
        state.completed_at = _now_iso()
        return state.status

    pending_players = [players_by_id[player_id] for player_id in pending_ids if player_id in players_by_id]
    can_continue = any(
        team_can_bid_for_player(state, team, player) for player in pending_players for team in teams
    )
    if not can_continue:
        state.status = SecondAuctionStatus.BLOCKED
        return state.status

    seed = None if state.random_seed is None else state.random_seed + state.round_number
    state.start_new_round(shuffle_player_ids(pending_ids, seed=seed))
    return state.status


def process_second_auction_sale(
    state: SecondAuctionState,
    setup: SecondAuctionSetup,
    teams: Iterable[Team],
    players_by_id: dict[int, Player],
    winning_team: int | str,
    sale_price: int,
) -> SecondAuctionTransactionResult:
    """Sell the second auction's current player to `winning_team` for
    `sale_price`. Validates everything first and only then mutates
    `state`'s own remaining budget/active roster/history — never a
    `Team`/`Player` object. Any validation failure raises
    `SecondAuctionBiddingError` and leaves `state` completely unchanged.
    """
    teams = list(teams)
    _validate_active_state(state)

    player_id = state.current_player_id
    player = players_by_id.get(player_id) if player_id is not None else None
    if player is None:
        raise SecondAuctionBiddingError("No current second-auction player is available.")
    if player.id in sold_player_ids(state):
        raise SecondAuctionBiddingError("Player has already been resolved.")

    team = _resolve_team(teams, winning_team)
    if team is None:
        raise SecondAuctionBiddingError("Winning team does not exist.")

    roster = state.active_rosters_by_team.get(team.id, [])
    if len(roster) >= team.max_squad_size:
        raise SecondAuctionBiddingError(f"{team.name}'s roster is full.")

    price = _validate_sale_price(sale_price)
    base_price = player_base_price(player)
    if price < base_price:
        raise SecondAuctionBiddingError(f"{player.full_name}'s minimum price is {base_price}M.")
    if price > team_max_legal_bid(state, team):
        raise SecondAuctionBiddingError(team_ineligibility_reason(state, team))

    pool_entry = release_pool_lookup(teams, setup, players_by_id).get(player.id)
    if pool_entry is None:
        raise SecondAuctionBiddingError("This player is not part of the confirmed release pool.")

    sequence = _next_sequence(state)
    state.remaining_budgets_by_team[team.id] = state.remaining_budgets_by_team.get(team.id, 0) - price
    state.active_rosters_by_team.setdefault(team.id, []).append(player.id)

    entry = SecondAuctionHistoryEntry(
        sequence=sequence,
        player_id=player.id,
        player_name=player.full_name,
        position=player.position.value,
        previous_team_id=pool_entry.previous_team.id,
        previous_team_name=pool_entry.previous_team.name,
        original_first_auction_price=pool_entry.original_price,
        status=SOLD,
        team_id=team.id,
        team_name=team.name,
        sold_price=price,
        round_number=state.round_number,
    )
    state.history.append(entry)

    resulting_status = _finish_transaction(state, teams, players_by_id)

    return SecondAuctionTransactionResult(
        player=player,
        outcome=SOLD,
        team=team,
        sale_price=price,
        history_entry=entry,
        status=resulting_status,
        is_complete=resulting_status == SecondAuctionStatus.COMPLETE,
        next_player_id=state.current_player_id,
    )


def process_second_auction_unsold(
    state: SecondAuctionState,
    setup: SecondAuctionSetup,
    teams: Iterable[Team],
    players_by_id: dict[int, Player],
) -> SecondAuctionTransactionResult:
    """Mark the second auction's current player UNSOLD. Makes no change
    to any team's second-auction budget or roster. UNSOLD is never a
    permanent outcome here either — the player returns in a future
    re-auction round until sold or until the second auction is BLOCKED.
    """
    teams = list(teams)
    _validate_active_state(state)

    player_id = state.current_player_id
    player = players_by_id.get(player_id) if player_id is not None else None
    if player is None:
        raise SecondAuctionBiddingError("No current second-auction player is available.")
    if player.id in sold_player_ids(state):
        raise SecondAuctionBiddingError("Player has already been resolved.")

    pool_entry = release_pool_lookup(teams, setup, players_by_id).get(player.id)
    if pool_entry is None:
        raise SecondAuctionBiddingError("This player is not part of the confirmed release pool.")

    sequence = _next_sequence(state)
    entry = SecondAuctionHistoryEntry(
        sequence=sequence,
        player_id=player.id,
        player_name=player.full_name,
        position=player.position.value,
        previous_team_id=pool_entry.previous_team.id,
        previous_team_name=pool_entry.previous_team.name,
        original_first_auction_price=pool_entry.original_price,
        status=UNSOLD,
        round_number=state.round_number,
    )
    state.history.append(entry)

    resulting_status = _finish_transaction(state, teams, players_by_id)

    return SecondAuctionTransactionResult(
        player=player,
        outcome=UNSOLD,
        team=None,
        sale_price=None,
        history_entry=entry,
        status=resulting_status,
        is_complete=resulting_status == SecondAuctionStatus.COMPLETE,
        next_player_id=state.current_player_id,
    )
