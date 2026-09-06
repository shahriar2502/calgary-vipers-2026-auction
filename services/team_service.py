"""Resolves team rosters to Player objects and derives team summary data.

Pure business logic: no GUI, no write/update methods. Team and player data
always come from data/teams.json and data/players.json through
services/player_service.py's loaders — this module never edits budgets,
rosters, or squad sizes; it only reads and presents the Team model's
current state.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from models.player import Player
from models.team import Team
from services.player_service import load_players, load_teams


@dataclass(frozen=True, slots=True)
class TeamSummary:
    """A team's current state with its roster resolved to Player objects."""

    team: Team
    captain: Player | None
    roster_players: list[Player] = field(default_factory=list)
    unresolved_roster_ids: list[int] = field(default_factory=list)

    @property
    def squad_size(self) -> int:
        return len(self.team.roster)

    @property
    def remaining_slots(self) -> int:
        return max(self.team.max_squad_size - self.squad_size, 0)

    @property
    def total_spent(self) -> int:
        return self.team.auction_spending

    @property
    def remaining_budget(self) -> int:
        return self.team.remaining_budget


def build_team_summary(team: Team, players_by_id: dict[int, Player]) -> TeamSummary:
    """Resolve one team's roster player ids to Player objects.

    A roster id with no matching player is reported in
    `unresolved_roster_ids` instead of raising, so a corrupted roster (an
    unknown player id, a captain id that does not resolve) can still be
    displayed as a readable, partial state.
    """
    roster_players: list[Player] = []
    unresolved_ids: list[int] = []
    for player_id in team.roster:
        player = players_by_id.get(player_id)
        if player is None:
            unresolved_ids.append(player_id)
        else:
            roster_players.append(player)

    captain = players_by_id.get(team.captain_player_id)
    return TeamSummary(
        team=team,
        captain=captain,
        roster_players=roster_players,
        unresolved_roster_ids=unresolved_ids,
    )


def build_team_summaries(teams: Iterable[Team], players: Iterable[Player]) -> list[TeamSummary]:
    """Build a TeamSummary for every team, in the given teams order."""
    players_by_id = {player.id: player for player in players}
    return [build_team_summary(team, players_by_id) for team in teams]


def load_team_summaries(players_path: Path | None = None, teams_path: Path | None = None) -> list[TeamSummary]:
    """Load players and teams and resolve every team's roster in one call."""
    players = load_players(players_path)
    teams = load_teams(teams_path)
    return build_team_summaries(teams, players)
