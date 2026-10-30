"""Second Auction — Milestone 3: live bidding state for captain phone
bidding during the second auction, separate from the second-auction
SOLD/UNSOLD transaction layer (`services/second_auction_bidding_service
.py`) exactly the way `services/live_bid_service.py` is separate from
the first auction's own `services/auction_service.py`.

This module owns exactly one responsibility: validating and applying a
change to the second auction's *live* current bid / leading team while a
released player is up for bidding. It never sells, marks unsold,
advances the queue, or touches a team's second-auction budget/roster —
that remains exclusively `services/second_auction_bidding_service.py`'s
job (`process_second_auction_sale`/`process_second_auction_unsold`),
called only by the organizer. A captain's phone (or the desktop's own
bid controls) can move the live bid; only the organizer's SOLD/UNSOLD
press ever finalizes a second-auction transaction.

Reuses the exact same second-auction eligibility/reserve helpers
`process_second_auction_sale` itself relies on
(`team_can_bid_for_player`, `team_max_legal_bid`, `team_ineligibility_
reason`, `player_base_price`) — never a second formula, so a phone bid
and a desktop SOLD confirmation apply identically.

Thread safety: mirrors `live_bid_service.place_bid`'s own module-level
lock exactly — one lock per module, so a first-auction phone bid and a
second-auction phone bid can never block each other, but two
second-auction bids can never both "win" a race.
"""

from __future__ import annotations

import threading
from collections.abc import Iterable
from dataclasses import dataclass

from models.player import Player, player_base_price
from models.second_auction import SecondAuctionState, SecondAuctionStatus
from models.team import Team
from services.second_auction_bidding_service import (
    team_can_bid_for_player,
    team_ineligibility_reason,
    team_max_legal_bid,
)

_BID_LOCK = threading.Lock()

_INACTIVE_STATUS_REASON = {
    SecondAuctionStatus.NOT_STARTED: "Second auction has not started yet.",
    SecondAuctionStatus.COMPLETE: "Second auction is complete. Bidding closed.",
    SecondAuctionStatus.BLOCKED: "Second auction is blocked. Bidding unavailable.",
}


@dataclass(frozen=True, slots=True)
class SecondAuctionBidResult:
    """Same shape as `live_bid_service.BidResult` by design — the
    captain-bidding server's `/api/bid` route returns an identical
    response regardless of which auction phase handled it."""

    accepted: bool
    reason: str | None
    current_bid: int | None
    leading_team_id: int | None


def _resolve_team(teams: Iterable[Team], team_id_or_name: int | str) -> Team | None:
    for team in teams:
        if team.id == team_id_or_name or team.name == team_id_or_name:
            return team
    return None


def place_second_auction_bid(
    state: SecondAuctionState | None,
    teams: Iterable[Team],
    players_by_id: dict[int, Player],
    team_id_or_name: int | str,
    bid_amount: object,
) -> SecondAuctionBidResult:
    """Validate and, if legal, apply one live second-auction bid. Every
    check below is enforced here regardless of caller (desktop or
    phone) — nothing upstream is trusted."""
    with _BID_LOCK:
        if state is None or not state.is_initialized:
            return SecondAuctionBidResult(False, "Second auction has not started yet.", None, None)

        current_bid = state.current_bid
        leading_team_id = state.leading_team_id

        if state.status != SecondAuctionStatus.IN_PROGRESS:
            reason = _INACTIVE_STATUS_REASON[state.status]
            return SecondAuctionBidResult(False, reason, current_bid, leading_team_id)

        teams = list(teams)
        current_player_id = state.current_player_id
        current_player = players_by_id.get(current_player_id) if current_player_id is not None else None
        if current_player is None:
            return SecondAuctionBidResult(False, "No current second-auction player is up for bidding.", current_bid, leading_team_id)

        team = _resolve_team(teams, team_id_or_name)
        if team is None:
            return SecondAuctionBidResult(False, "Team not found.", current_bid, leading_team_id)

        if not team_can_bid_for_player(state, team, current_player):
            return SecondAuctionBidResult(False, team_ineligibility_reason(state, team), current_bid, leading_team_id)

        if isinstance(bid_amount, bool) or not isinstance(bid_amount, int):
            return SecondAuctionBidResult(False, "Bid must be a whole number.", current_bid, leading_team_id)
        base_price = player_base_price(current_player)
        if bid_amount < base_price:
            return SecondAuctionBidResult(False, f"Bid must be at least {base_price}M.", current_bid, leading_team_id)
        if current_bid is not None and bid_amount <= current_bid:
            return SecondAuctionBidResult(
                False, f"Bid must exceed the current highest bid of {current_bid}M.", current_bid, leading_team_id
            )
        if bid_amount > team_max_legal_bid(state, team):
            return SecondAuctionBidResult(False, team_ineligibility_reason(state, team), current_bid, leading_team_id)

        state.current_bid = bid_amount
        state.leading_team_id = team.id
        return SecondAuctionBidResult(True, None, bid_amount, team.id)
