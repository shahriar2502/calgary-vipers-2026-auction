"""Second Auction — Milestone 3: a single, phase-aware "current state of
the tournament" read model (October 2026).

Chooses whether a team's current roster/budget should be read from the
first auction (`Team.roster`/`remaining_budget`/`starting_budget`) or
from the second auction (`SecondAuctionState.active_rosters_by_team`/
`remaining_budgets_by_team`/`starting_budgets_by_team`), based entirely
on `session.second_auction_state.status`. This is the one place that
phase logic lives — screens (Teams, the Second Auction LIVE view) call
these functions instead of each re-deriving "which phase are we in, and
which roster/budget does that mean" independently.

Never mutates `Team.roster`/`remaining_budget` or `SecondAuctionState`
itself — purely a read model. `Team.roster`/`starting_budget`/
`remaining_budget` remain the permanent first-auction historical record
forever, exactly as every earlier milestone already established.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from models.player import Player
from models.second_auction import SecondAuctionStatus
from models.team import Team
from services.auction_session_service import AuctionSession


def second_auction_is_active_or_complete(session: AuctionSession) -> bool:
    """True once the second auction has been started — IN_PROGRESS,
    COMPLETE, or BLOCKED. False while it is still NOT_STARTED (including
    the entire Milestone 1 transfer-window-setup phase), in which case
    every "current" read below falls back to the first auction's own
    (already-final) state."""
    return session.second_auction_state.status != SecondAuctionStatus.NOT_STARTED


def _resolve_team(session: AuctionSession, team_id: int) -> Team | None:
    return next((team for team in session.teams if team.id == team_id), None)


def get_current_roster_for_team(session: AuctionSession, team_id: int) -> list[int]:
    """The current tournament roster (player ids) for `team_id` — the
    second-auction active roster once the second auction has started,
    otherwise the original first-auction `Team.roster`. Never mutates
    either source."""
    if second_auction_is_active_or_complete(session):
        return list(session.second_auction_state.active_rosters_by_team.get(team_id, []))
    team = _resolve_team(session, team_id)
    return list(team.roster) if team is not None else []


def get_current_team_for_player(session: AuctionSession, player_id: int) -> Team | None:
    """Whichever team currently has `player_id` on its *current*
    tournament roster — reflects a second-auction transfer once one has
    happened, without ever touching the first auction's own historical
    ownership record (`Player.sold_to`, untouched by the second auction —
    see services/second_auction_bidding_service.py)."""
    for team in session.teams:
        if player_id in get_current_roster_for_team(session, team.id):
            return team
    return None


def get_current_team_budget(session: AuctionSession, team_id: int) -> int:
    """Current spendable budget for `team_id` — the second-auction
    remaining budget once the second auction has started, otherwise the
    first-auction remaining budget."""
    if second_auction_is_active_or_complete(session):
        return session.second_auction_state.remaining_budgets_by_team.get(team_id, 0)
    team = _resolve_team(session, team_id)
    return team.remaining_budget if team is not None else 0


def get_current_starting_budget(session: AuctionSession, team_id: int) -> int:
    """The starting budget for whichever phase is currently active — the
    frozen second-auction starting budget once started, otherwise the
    first-auction `starting_budget`."""
    if second_auction_is_active_or_complete(session):
        return session.second_auction_state.starting_budgets_by_team.get(team_id, 0)
    team = _resolve_team(session, team_id)
    return team.starting_budget if team is not None else 0


def get_second_auction_spent(session: AuctionSession, team_id: int) -> int:
    """How much `team_id` has spent in the second auction so far — 0
    before the second auction has started. Derived, never a stored
    running total, matching this project's established "never duplicate
    a number that can disagree with its source" rule."""
    if not second_auction_is_active_or_complete(session):
        return 0
    return get_current_starting_budget(session, team_id) - get_current_team_budget(session, team_id)


def is_player_new_transfer(session: AuctionSession, player_id: int) -> bool:
    """True if `player_id` was bought via the second auction (has a SOLD
    entry in `SecondAuctionState.history`) — the source for an optional
    "NEW / TRANSFER" badge, derived purely from history. Never stored on
    `Player`, and never requires mutating any historical field."""
    return any(
        entry.player_id == player_id and entry.status == "SOLD" for entry in session.second_auction_state.history
    )


@dataclass(frozen=True, slots=True)
class CurrentTeamSummary:
    """A team's *current* tournament state — phase-aware, unlike
    `services/team_service.py`'s `TeamSummary` (which only ever reflects
    the first auction). Used by the Teams screen once a session is
    active; before/independent of any session, Teams still falls back to
    `team_service.load_team_summaries()` exactly as before this
    milestone."""

    team: Team
    captain: Player | None
    roster_players: list[Player] = field(default_factory=list)
    unresolved_roster_ids: list[int] = field(default_factory=list)
    remaining_budget: int = 0
    starting_budget: int = 0
    first_auction_remaining_budget: int = 0
    second_auction_spent: int = 0
    is_second_auction_phase: bool = False
    second_auction_status: SecondAuctionStatus = SecondAuctionStatus.NOT_STARTED

    @property
    def squad_size(self) -> int:
        return len(self.roster_players) + len(self.unresolved_roster_ids)

    @property
    def remaining_slots(self) -> int:
        return max(self.team.max_squad_size - self.squad_size, 0)


def build_current_team_summary(
    session: AuctionSession, team: Team, players_by_id: dict[int, Player]
) -> CurrentTeamSummary:
    roster_ids = get_current_roster_for_team(session, team.id)
    roster_players: list[Player] = []
    unresolved_ids: list[int] = []
    for player_id in roster_ids:
        player = players_by_id.get(player_id)
        if player is None:
            unresolved_ids.append(player_id)
        else:
            roster_players.append(player)

    captain = players_by_id.get(team.captain_player_id)
    is_second_phase = second_auction_is_active_or_complete(session)
    return CurrentTeamSummary(
        team=team,
        captain=captain,
        roster_players=roster_players,
        unresolved_roster_ids=unresolved_ids,
        remaining_budget=get_current_team_budget(session, team.id),
        starting_budget=get_current_starting_budget(session, team.id),
        first_auction_remaining_budget=team.remaining_budget,
        second_auction_spent=get_second_auction_spent(session, team.id),
        is_second_auction_phase=is_second_phase,
        second_auction_status=session.second_auction_state.status,
    )


def build_current_team_summaries(session: AuctionSession) -> list[CurrentTeamSummary]:
    players_by_id = {player.id: player for player in session.players}
    return [build_current_team_summary(session, team, players_by_id) for team in session.teams]
