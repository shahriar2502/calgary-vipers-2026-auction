"""Second Auction — Milestone 1: transfer-window release selection,
original-purchase-price refund preview, and the 16-player release pool
(September/October 2026).

Deliberately independent of `services/auction_service.py` (first-auction
SOLD/UNSOLD/bidding) and `services/match_result_service.py`'s own
add/update/delete methods — this module never calls `process_sale`/
`process_unsold`/`place_bid`, never mutates a `Team`'s `remaining_budget`/
`roster`/`auction_spending`, never mutates a `Player`'s `sold_price`, and
never touches `auction.history`. It only *reads* a team's already-final
roster/`remaining_budget` and each roster player's already-final
`sold_price` (both frozen the instant the first auction's `Auction.status`
becomes COMPLETE) plus the existing match-result ledger, and combines them
into a read-only preview. Actual release/roster/budget mutation and
second-auction bidding are explicitly future milestones — see
PROJECT_CONTEXT.md's "SECOND AUCTION — MILESTONE 1".
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timezone

from models.player import Player, Position
from models.second_auction import SecondAuctionSetup, SecondAuctionSetupStatus
from models.team import Team
from services import match_result_service

MAX_RELEASES_PER_TEAM = 4


class SecondAuctionError(Exception):
    """Raised when a release-selection/confirm/unlock action is not
    currently valid. Deliberately distinct from `MatchResultError`/
    `AuctionTransactionError` — the transfer-window setup is its own
    independent subsystem."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def is_locked_player(player: Player) -> bool:
    """Captain or goalkeeper — the only two protections this app can
    verify on its own (the ticket explicitly forbids any FPL-based
    eligibility logic). A captain is always `is_captain=True` on their
    own team's roster; a roster's goalkeeper is whichever roster player
    has `position == Position.GK` (at most one, enforced since the first
    auction itself — see models/team.py's one-GK-per-team rule)."""
    return player.is_captain or player.position == Position.GK


def releasable_roster(team: Team, players_by_id: dict[int, Player]) -> list[Player]:
    """Every roster player who is *not* locked, in roster order."""
    return [
        players_by_id[player_id]
        for player_id in team.roster
        if player_id in players_by_id and not is_locked_player(players_by_id[player_id])
    ]


def _validate_team_and_player(team: Team, player_id: int, players_by_id: dict[int, Player]) -> Player:
    if player_id not in team.roster:
        raise SecondAuctionError(f"That player does not belong to {team.name}'s roster.")
    player = players_by_id.get(player_id)
    if player is None:
        raise SecondAuctionError("That player could not be found.")
    return player


def toggle_release(
    setup: SecondAuctionSetup, team: Team, player_id: int, players_by_id: dict[int, Player]
) -> None:
    """Select `player_id` for release from `team` if not already
    selected, or deselect it if it is — the one entry point the UI's
    per-row checkbox uses. Raises `SecondAuctionError` (and changes
    nothing) if the plan is already CONFIRMED, the player isn't on this
    team's roster, the player is locked (captain/GK), or this team
    already has 4 selected and is trying to add a 5th."""
    if setup.is_confirmed:
        raise SecondAuctionError("The release plan is confirmed and locked. Edit it first to make changes.")
    player = _validate_team_and_player(team, player_id, players_by_id)
    if is_locked_player(player):
        reason = "the captain" if player.is_captain else "the goalkeeper"
        raise SecondAuctionError(f"{player.full_name} is {reason} and cannot be released.")

    current = setup.released_player_ids_by_team.get(team.id, [])
    if player_id in current:
        setup.released_player_ids_by_team[team.id] = [pid for pid in current if pid != player_id]
        return
    if len(current) >= MAX_RELEASES_PER_TEAM:
        raise SecondAuctionError(f"{team.name} already has {MAX_RELEASES_PER_TEAM} players selected for release.")
    setup.released_player_ids_by_team[team.id] = current + [player_id]


def all_teams_have_exactly_four(setup: SecondAuctionSetup, teams: Iterable[Team]) -> bool:
    teams = list(teams)
    return bool(teams) and all(len(setup.selections_for(team.id)) == MAX_RELEASES_PER_TEAM for team in teams)


def confirm_release_plan(setup: SecondAuctionSetup, teams: Iterable[Team]) -> None:
    """Lock the current selections in as the tournament's official
    16-player release pool. Requires every team to have exactly 4
    selected right now — raises `SecondAuctionError` (changing nothing)
    otherwise. Does not touch any `Team`/`Player`/`Auction` object: this
    milestone is preview/setup only (see the module docstring)."""
    teams = list(teams)
    if setup.is_confirmed:
        raise SecondAuctionError("The release plan is already confirmed.")
    if not all_teams_have_exactly_four(setup, teams):
        incomplete = [
            team.name for team in teams if len(setup.selections_for(team.id)) != MAX_RELEASES_PER_TEAM
        ]
        raise SecondAuctionError(
            f"Every team must have exactly {MAX_RELEASES_PER_TEAM} selected releases before confirming "
            f"(still incomplete: {', '.join(incomplete)})."
        )
    setup.status = SecondAuctionSetupStatus.CONFIRMED
    setup.confirmed_at = _now_iso()


def unlock_release_plan(setup: SecondAuctionSetup) -> None:
    """Return a CONFIRMED plan to DRAFT so the organizer can correct a
    mistake — safe only because second-auction bidding does not exist yet
    (nothing downstream has acted on the confirmed plan)."""
    if not setup.is_confirmed:
        raise SecondAuctionError("The release plan is not confirmed, so there is nothing to unlock.")
    setup.status = SecondAuctionSetupStatus.DRAFT
    setup.confirmed_at = None


# ----------------------------------------------------------------------
# Refund / budget preview (read-only, always derived)
# ----------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ReleaseRefund:
    """One released player's refund — exactly what their current team
    paid for them in the first auction (`Player.sold_price`), never OVR,
    base price, or any other value."""

    player: Player
    original_price: int


@dataclass(frozen=True, slots=True)
class TeamReleasePreview:
    team: Team
    current_transfer_budget: int
    refunds: list[ReleaseRefund] = field(default_factory=list)

    @property
    def total_refunds(self) -> int:
        return sum(refund.original_price for refund in self.refunds)

    @property
    def second_auction_starting_budget(self) -> int:
        return self.current_transfer_budget + self.total_refunds

    @property
    def selected_count(self) -> int:
        return len(self.refunds)


def build_team_release_preview(
    team: Team,
    setup: SecondAuctionSetup,
    players_by_id: dict[int, Player],
    match_results,
) -> TeamReleasePreview:
    """`current_transfer_budget` is read from the exact same
    `match_result_service.build_team_ledger` the Match Results screen
    itself uses — never a second formula."""
    ledger = match_result_service.build_team_ledger(team, match_results)
    selected_ids = setup.selections_for(team.id)
    refunds = [
        ReleaseRefund(player=players_by_id[player_id], original_price=players_by_id[player_id].sold_price or 0)
        for player_id in selected_ids
        if player_id in players_by_id
    ]
    return TeamReleasePreview(
        team=team, current_transfer_budget=ledger.current_transfer_budget, refunds=refunds
    )


def build_all_release_previews(
    teams: Iterable[Team], setup: SecondAuctionSetup, players_by_id: dict[int, Player], match_results
) -> list[TeamReleasePreview]:
    return [build_team_release_preview(team, setup, players_by_id, match_results) for team in teams]


@dataclass(frozen=True, slots=True)
class ReleasePoolEntry:
    player: Player
    previous_team: Team
    original_price: int


def build_release_pool(
    teams: Iterable[Team], setup: SecondAuctionSetup, players_by_id: dict[int, Player]
) -> list[ReleasePoolEntry]:
    """Every currently-selected release across every team, regardless of
    whether the plan is confirmed yet — the Release Pool Summary section
    only chooses to *show* this once every team has 4 (see
    `all_teams_have_exactly_four`); this function itself has no opinion
    on completeness."""
    teams = list(teams)
    entries: list[ReleasePoolEntry] = []
    for team in teams:
        for player_id in setup.selections_for(team.id):
            player = players_by_id.get(player_id)
            if player is None:
                continue
            entries.append(ReleasePoolEntry(player=player, previous_team=team, original_price=player.sold_price or 0))
    return entries
