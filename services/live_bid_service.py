"""Captain Phone Bidding — Phase 1: live bidding state, separate from the
SOLD/UNSOLD transaction layer.

This module owns exactly one responsibility: validating and applying a
change to the auction's *live* current bid / leading team while a player
is up for auction. It never sells, marks unsold, advances the queue, or
touches a Team's budget/roster — that remains exclusively
`services/auction_service.py`'s job (`process_sale`/`process_unsold`),
called only by the organizer. A phone (or the desktop's own bid buttons)
can move the live bid; only the organizer's SOLD/UNSOLD press ever
finalizes a transaction.

No new state is invented: `Auction.current_bid`/`Auction.leading_team_id`
already existed as dataclass fields (already serialized by
`Auction.to_dict`/`from_dict`) before this feature, unused by any caller.
This module is simply the first thing to actually read and write them —
one authoritative bid/leader per auction, shared by every caller
(desktop UI and every phone) via `AuctionSession.place_live_bid`, which
wraps `place_bid` below. There is no separate `phone_current_bid` /
`desktop_current_bid` — both paths call this exact function.

Thread safety: `place_bid` is the only place that reads-then-writes
`auction.current_bid`/`leading_team_id`, guarded by one module-level lock
so two nearly-simultaneous bids (e.g. two captains' phones) can never both
"win" — the second to acquire the lock re-validates against whatever the
first just committed and is rejected if it no longer qualifies.
"""

from __future__ import annotations

import threading
from collections.abc import Iterable
from dataclasses import dataclass

from models.auction import Auction, AuctionStatus
from models.player import Player
from models.team import MINIMUM_LEGAL_PRICE, Team
from services.auction_service import (
    budget_reserve_violation_message,
    get_current_player,
    team_can_bid_for_player,
    team_has_goalkeeper,
)

_BID_LOCK = threading.Lock()


@dataclass(frozen=True, slots=True)
class BidResult:
    """The outcome of one `place_bid` call. `current_bid`/`leading_team_id`
    always reflect the auction's actual resulting state — whether or not
    *this* call was the one that produced it — so a caller never needs a
    second read to display the current truth after a rejection."""

    accepted: bool
    reason: str | None
    current_bid: int | None
    leading_team_id: int | None


def _resolve_team(teams: Iterable[Team], team_id_or_name: int | str) -> Team | None:
    for team in teams:
        if team.id == team_id_or_name or team.name == team_id_or_name:
            return team
    return None


def _ineligibility_reason(team: Team, player: Player, players: Iterable[Player]) -> str:
    """Mirrors the reason labels `ui/screens/live_auction_screen.py`'s
    team-status row already shows the organizer, so a rejected phone bid
    reads consistently with what the projector already displays."""
    if team.roster_size >= team.max_squad_size:
        return f"{team.name} is full."
    if player.position.value == "GK" and team_has_goalkeeper(team, players):
        return f"{team.name} already has a goalkeeper."
    if team.maximum_legal_bid < MINIMUM_LEGAL_PRICE:
        return budget_reserve_violation_message(team)
    return f"{team.name} cannot bid on this player."


def place_bid(
    auction: Auction | None,
    players: Iterable[Player],
    teams: Iterable[Team],
    team_id_or_name: int | str,
    bid_amount: object,
) -> BidResult:
    """Validate and, if legal, apply one live bid.

    Every check below is enforced here regardless of caller (desktop
    button or phone API) — nothing upstream is trusted. Reuses the exact
    same eligibility/budget helpers `process_sale` itself relies on
    (`team_can_bid_for_player`, `Team.maximum_legal_bid`,
    `budget_reserve_violation_message`) rather than re-deriving any of
    those formulas here.
    """
    with _BID_LOCK:
        if auction is None or not auction.is_initialized:
            return BidResult(False, "Auction is not initialized.", None, None)

        current_bid = auction.current_bid
        leading_team_id = auction.leading_team_id

        if auction.status != AuctionStatus.IN_PROGRESS:
            reason = "Auction is complete. Bidding closed." if auction.status == AuctionStatus.COMPLETE else (
                "Auction is blocked. Bidding unavailable."
                if auction.status == AuctionStatus.BLOCKED
                else "Auction has not started."
            )
            return BidResult(False, reason, current_bid, leading_team_id)

        players = list(players)
        teams = list(teams)

        current_player = get_current_player(auction, players)
        if current_player is None:
            return BidResult(False, "No current player is up for auction.", current_bid, leading_team_id)

        team = _resolve_team(teams, team_id_or_name)
        if team is None:
            return BidResult(False, "Team not found.", current_bid, leading_team_id)

        if not team_can_bid_for_player(team, current_player, players):
            return BidResult(False, _ineligibility_reason(team, current_player, players), current_bid, leading_team_id)

        if isinstance(bid_amount, bool) or not isinstance(bid_amount, int):
            return BidResult(False, "Bid must be a whole number.", current_bid, leading_team_id)
        if bid_amount < MINIMUM_LEGAL_PRICE:
            return BidResult(
                False, f"Bid must be at least {MINIMUM_LEGAL_PRICE}M.", current_bid, leading_team_id
            )
        if current_bid is not None and bid_amount <= current_bid:
            return BidResult(
                False, f"Bid must exceed the current highest bid of {current_bid}M.", current_bid, leading_team_id
            )
        if bid_amount > team.maximum_legal_bid:
            return BidResult(False, budget_reserve_violation_message(team), current_bid, leading_team_id)

        auction.current_bid = bid_amount
        auction.leading_team_id = team.id
        return BidResult(True, None, bid_amount, team.id)
