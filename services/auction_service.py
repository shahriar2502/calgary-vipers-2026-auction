"""Auction transaction orchestration: SOLD and UNSOLD business logic.

Coordinates the existing models rather than duplicating their invariants:

- `Team` remains the source of truth for budget/roster mutation
  (`add_purchased_player`) and its own invariants.
- `Auction` remains the source of truth for queue/round position and
  round completion (`advance`, `is_complete`, `start_new_round`).
- This module only adds the missing piece: validating a transaction is
  legal, applying it across `Player` + `Team` + `Auction` together,
  recording history, and — once a round's queue is exhausted — deciding
  whether the whole auction is done, needs another re-auction round, or
  has become impossible to finish. All in memory; no file persistence.

Every validation runs before any mutation, so a failed transaction leaves
the auction, every team, and every player completely unchanged.

SOLD is the only permanent outcome. UNSOLD players are not eliminated —
they return in the next re-auction round until sold or until no team can
legally take them (see `eligible_for_reauction` / `team_can_bid_for_player`
and the BLOCKED status).
"""

from __future__ import annotations

import random
from collections.abc import Iterable
from dataclasses import dataclass

from models.auction import Auction, AuctionHistoryEntry, AuctionStatus
from models.player import Player, PlayerAuctionStatus, Position
from models.team import MINIMUM_LEGAL_PRICE, Team

SOLD = "SOLD"
UNSOLD = "UNSOLD"


class AuctionTransactionError(Exception):
    """Raised when a SOLD/UNSOLD transaction is not currently valid."""


@dataclass(frozen=True, slots=True)
class TransactionResult:
    """What a successful SOLD/UNSOLD transaction did, for the caller's use."""

    player: Player
    outcome: str  # SOLD or UNSOLD
    team: Team | None
    sale_price: int | None
    history_entry: AuctionHistoryEntry
    auction_status: AuctionStatus
    auction_complete: bool
    next_player_id: int | None


def resolved_player_ids(auction: Auction) -> set[int]:
    """Ids of every player with at least one SOLD or UNSOLD history entry.

    Not a "cannot be auctioned again" list — UNSOLD is never permanent.
    Use `sold_player_ids` for the permanent-resolution check.
    """
    return {entry.player_id for entry in auction.history}


def sold_player_ids(auction: Auction) -> set[int]:
    """Ids of every player permanently SOLD, across all rounds."""
    return {entry.player_id for entry in auction.history if entry.status == SOLD}


def eligible_for_reauction(auction: Auction, players: Iterable[Player]) -> list[int]:
    """Auction-eligible players not yet permanently SOLD.

    This is exactly the pool for the next re-auction round once the
    current round's queue is exhausted: UNSOLD is never a permanent
    outcome, so anyone not yet sold must eventually come back up.
    """
    sold = sold_player_ids(auction)
    return [player.id for player in players if player.auction_eligible and player.id not in sold]


def get_current_player(auction: Auction, players: Iterable[Player]) -> Player | None:
    """Resolve the auction's current player id to a Player, or None."""
    player_id = auction.current_player_id
    if player_id is None:
        return None
    return next((player for player in players if player.id == player_id), None)


def team_has_goalkeeper(team: Team, players: Iterable[Player]) -> bool:
    """Whether `team`'s current roster already includes a goalkeeper."""
    players_by_id = {player.id: player for player in players}
    return any(
        (roster_player := players_by_id.get(roster_id)) is not None and roster_player.position == Position.GK
        for roster_id in team.roster
    )


def team_can_bid_for_player(team: Team, player: Player, players: Iterable[Player]) -> bool:
    """Whether `team` could currently legally buy `player`, ignoring the
    specific proposed price.

    Considers roster space, the at-most-one-goalkeeper-per-team rule, and
    (pre-Milestone-9 budget reserve fix) whether the team could legally
    pay even the minimum legal price for this player while preserving
    enough budget to fill every remaining roster slot afterward —
    `team.maximum_legal_bid` doesn't depend on the eventual price, only on
    the team's own roster/budget state, so this can be checked before a
    price is known. This is used by the UI to proactively disable
    ineligible teams and by this module's own blocked-auction detection —
    it does not replace `process_sale`'s own validation of a *specific*
    price, which remains authoritative for an actual transaction attempt.
    """
    if team.roster_size >= team.max_squad_size:
        return False
    if player.position == Position.GK and team_has_goalkeeper(team, players):
        return False
    if team.maximum_legal_bid < MINIMUM_LEGAL_PRICE:
        return False
    return True


def budget_reserve_violation_message(team: Team) -> str:
    """Human-readable explanation of why `team` cannot legally pay more
    than its current `maximum_legal_bid`, shared by `process_sale`'s
    rejection and the Live Auction UI's pre-confirmation check so the
    wording can never drift between the two (see PROJECT_CONTEXT.md's
    "PRE-M9 BUDGET RESERVE RULE").
    """
    slots_after_purchase = max(team.remaining_required_purchases - 1, 0)
    if slots_after_purchase > 0:
        reserve = slots_after_purchase * MINIMUM_LEGAL_PRICE
        spot_word = "spot" if slots_after_purchase == 1 else "spots"
        displayed_max = max(team.maximum_legal_bid, 0)
        return (
            f"{team.name} must reserve {reserve}M of its remaining budget for its "
            f"{slots_after_purchase} remaining roster {spot_word}. Maximum legal price is {displayed_max}M."
        )
    return "Team does not have enough budget."


def _validate_current_player(auction: Auction, players_by_id: dict[int, Player]) -> Player:
    """Checks shared by SOLD and UNSOLD before either one may proceed."""
    if not auction.is_initialized:
        raise AuctionTransactionError("Auction is not initialized.")
    if auction.status == AuctionStatus.BLOCKED:
        raise AuctionTransactionError(
            "Auction is blocked: no eligible team can purchase the remaining player(s)."
        )
    if auction.is_complete:
        raise AuctionTransactionError("Auction is already complete.")

    player_id = auction.current_player_id
    player = players_by_id.get(player_id) if player_id is not None else None
    if player is None:
        raise AuctionTransactionError("Current auction player could not be found.")

    if player.is_captain:
        raise AuctionTransactionError("Captains cannot be auctioned.")
    if not player.auction_eligible:
        raise AuctionTransactionError("Player is not auction-eligible.")
    if player.id in sold_player_ids(auction):
        raise AuctionTransactionError("Player has already been resolved.")

    return player


def _resolve_team(teams: list[Team], winning_team: int | str) -> Team | None:
    for team in teams:
        if team.id == winning_team or team.name == winning_team:
            return team
    return None


def _validate_sale_price(sale_price: object) -> int:
    # MINIMUM_LEGAL_PRICE is 1, so "< MINIMUM_LEGAL_PRICE" for an int is the
    # same threshold as the previous hard-coded "<= 0" — expressed via the
    # shared constant instead of a second magic number.
    if isinstance(sale_price, bool) or not isinstance(sale_price, int) or sale_price < MINIMUM_LEGAL_PRICE:
        raise AuctionTransactionError("Sale price must be a positive integer.")
    return sale_price


def _next_sequence(auction: Auction) -> int:
    return len(auction.history) + 1


def _finish_transaction(auction: Auction, players: list[Player], teams: list[Team]) -> AuctionStatus:
    """Advance to the next player, transitioning rounds or finishing the
    auction as needed. Returns the auction's resulting status.

    Called only after this transaction's own mutation has already
    succeeded, so a "cannot continue" situation is never raised here —
    the transaction that got us here was itself perfectly valid. Instead
    that situation is represented as AuctionStatus.BLOCKED so the caller
    can render a clear, persistent state rather than looping forever.
    """
    # Captain Phone Bidding (Phase 1): the live current_bid/leading_team_id
    # are attempt-scoped, never player-scoped — clear them unconditionally
    # before revealing whatever comes next (another player this round, a
    # new re-auction round, completion, or blocked), so no bid ever
    # carries from one player to another. See services/live_bid_service.py,
    # the only other code that writes these two fields.
    auction.current_bid = None
    auction.leading_team_id = None

    auction.advance()
    if not auction.is_complete:
        return auction.status  # more players left in this round

    pending_ids = eligible_for_reauction(auction, players)
    if not pending_ids:
        auction.status = AuctionStatus.COMPLETE
        return auction.status

    players_by_id = {player.id: player for player in players}
    pending_players = [players_by_id[player_id] for player_id in pending_ids if player_id in players_by_id]
    can_continue = any(
        team_can_bid_for_player(team, player, players) for player in pending_players for team in teams
    )
    if not can_continue:
        auction.status = AuctionStatus.BLOCKED
        return auction.status

    seed = None if auction.random_seed is None else auction.random_seed + auction.round_number
    shuffled_pending = list(pending_ids)
    random.Random(seed).shuffle(shuffled_pending)
    auction.start_new_round(shuffled_pending)
    return auction.status


def process_sale(
    auction: Auction,
    players: Iterable[Player],
    teams: Iterable[Team],
    winning_team: int | str,
    sale_price: int,
) -> TransactionResult:
    """Sell the auction's current player to `winning_team` for `sale_price`.

    Validates everything first (auction state, player eligibility, prior
    resolution, team existence, price, budget, roster space, the
    one-goalkeeper-per-team rule, and duplicate assignment) and only then
    mutates the winning `Team`, the `Player` record, and `auction.history`,
    advancing the queue exactly once. Any validation failure raises
    AuctionTransactionError and leaves every object passed in completely
    unchanged.
    """
    players = list(players)
    teams = list(teams)
    players_by_id = {player.id: player for player in players}

    player = _validate_current_player(auction, players_by_id)

    team = _resolve_team(teams, winning_team)
    if team is None:
        raise AuctionTransactionError("Winning team does not exist.")

    price = _validate_sale_price(sale_price)

    if any(player.id in other_team.roster for other_team in teams):
        raise AuctionTransactionError("Player is already assigned to a team.")
    if price > team.maximum_legal_bid:
        raise AuctionTransactionError(budget_reserve_violation_message(team))
    if team.roster_size >= team.max_squad_size:
        raise AuctionTransactionError("Team roster is full.")
    if player.position == Position.GK and team_has_goalkeeper(team, players):
        raise AuctionTransactionError("Team already has a goalkeeper.")

    sequence = _next_sequence(auction)

    team.add_purchased_player(player.id, price)

    player.auction_status = PlayerAuctionStatus.SOLD
    player.sold_price = price
    player.sold_to = team.name
    player.auction_sequence = sequence

    entry = AuctionHistoryEntry(
        auction_sequence=sequence,
        player_id=player.id,
        player_name=player.full_name,
        position=player.position.value,
        overall_rating=player.overall_rating,
        base_price=player.base_price,
        status=SOLD,
        team=team.name,
        sold_price=price,
        round_number=auction.round_number,
    )
    auction.history.append(entry)

    resulting_status = _finish_transaction(auction, players, teams)

    return TransactionResult(
        player=player,
        outcome=SOLD,
        team=team,
        sale_price=price,
        history_entry=entry,
        auction_status=resulting_status,
        auction_complete=resulting_status == AuctionStatus.COMPLETE,
        next_player_id=auction.current_player_id,
    )


def process_unsold(
    auction: Auction,
    players: Iterable[Player],
    teams: Iterable[Team],
) -> TransactionResult:
    """Mark the auction's current player UNSOLD.

    Makes no change to any team's budget, roster, or spending. Validates
    everything first and only then mutates the `Player` record and
    `auction.history`, advancing the queue exactly once. Any validation
    failure raises AuctionTransactionError and leaves every object passed
    in completely unchanged.

    UNSOLD is never a permanent outcome: the player remains eligible and
    will appear again in a future re-auction round if any remain unsold
    once the current round's queue is exhausted.
    """
    players = list(players)
    teams = list(teams)
    players_by_id = {player.id: player for player in players}

    player = _validate_current_player(auction, players_by_id)

    # Defense-in-depth: a player already on some team's roster should
    # already have been caught by sold_player_ids, but a caller could pass
    # a teams list that is out of sync with auction.history.
    if any(player.id in team.roster for team in teams):
        raise AuctionTransactionError("Player has already been resolved.")

    sequence = _next_sequence(auction)

    player.auction_status = PlayerAuctionStatus.UNSOLD
    player.auction_sequence = sequence

    entry = AuctionHistoryEntry(
        auction_sequence=sequence,
        player_id=player.id,
        player_name=player.full_name,
        position=player.position.value,
        overall_rating=player.overall_rating,
        base_price=player.base_price,
        status=UNSOLD,
        round_number=auction.round_number,
    )
    auction.history.append(entry)

    resulting_status = _finish_transaction(auction, players, teams)

    return TransactionResult(
        player=player,
        outcome=UNSOLD,
        team=None,
        sale_price=None,
        history_entry=entry,
        auction_status=resulting_status,
        auction_complete=resulting_status == AuctionStatus.COMPLETE,
        next_player_id=auction.current_player_id,
    )
