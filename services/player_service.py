"""Loads the canonical player/team roster and validates tournament setup.

Pure business logic: no GUI. Player and team data always come from
data/players.json and data/teams.json in their canonical on-disk order —
nothing here randomizes, edits, or writes back to those files.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from models.player import Player, Position
from models.team import Team

ROOT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_PLAYERS_PATH = ROOT_DIR / "data" / "players.json"
DEFAULT_TEAMS_PATH = ROOT_DIR / "data" / "teams.json"

# Mirror the tournament invariants documented in PROJECT_CONTEXT.md and
# data/settings.json. Kept as plain defaults (not read from JSON) so
# validate_setup has no file I/O of its own and tests can override them.
EXPECTED_TOTAL_PLAYERS = 32
EXPECTED_CAPTAIN_COUNT = 4
EXPECTED_AUCTION_ELIGIBLE_COUNT = 28
EXPECTED_TEAM_COUNT = 4

CAPTAIN_STATUS = "CAPTAIN"
AUCTION_STATUS = "AUCTION"


def load_players(path: Path | None = None) -> list[Player]:
    """Load all players in the canonical order data/players.json defines."""
    players_path = path or DEFAULT_PLAYERS_PATH
    data = json.loads(players_path.read_text(encoding="utf-8"))
    return [Player.from_dict(item) for item in data]


def load_teams(path: Path | None = None) -> list[Team]:
    """Load all teams in the canonical order data/teams.json defines.

    Used alongside `load_players` to cross-check captain/team links during
    setup validation; full team-roster management belongs to a later
    Teams-screen milestone.
    """
    teams_path = path or DEFAULT_TEAMS_PATH
    data = json.loads(teams_path.read_text(encoding="utf-8"))
    return [Team.from_dict(item) for item in data]


def player_status_label(player: Player) -> str:
    """The Status column value for a player: CAPTAIN or AUCTION."""
    return CAPTAIN_STATUS if player.is_captain else AUCTION_STATUS


def filter_players(
    players: Iterable[Player],
    search: str = "",
    position: Position | str | None = None,
    status: str | None = None,
) -> list[Player]:
    """Filter players by name/short-name search, position, and status.

    Preserves the input order. `search` is a case-insensitive, partial
    match against full_name or short_name. `position` accepts a Position,
    a matching string ("GK"/"DEF"/"MID"/"ATT"), or None/"All" for no
    position filtering. `status` accepts "CAPTAIN"/"AUCTION" (any case) or
    None/"All Players" for no status filtering.
    """
    query = search.strip().lower()

    wanted_position: Position | None = None
    if position is not None and str(position).strip().lower() != "all":
        wanted_position = position if isinstance(position, Position) else Position(position)

    wanted_status: str | None = None
    if status is not None and status.strip().lower() not in ("all", "all players"):
        wanted_status = status.strip().upper()
        if wanted_status not in (CAPTAIN_STATUS, AUCTION_STATUS):
            raise ValueError(f"Unknown status filter: {status}")

    matches = []
    for player in players:
        if query and query not in player.full_name.lower() and query not in player.short_name.lower():
            continue
        if wanted_position is not None and player.position != wanted_position:
            continue
        if wanted_status is not None and player_status_label(player) != wanted_status:
            continue
        matches.append(player)
    return matches


@dataclass(frozen=True, slots=True)
class SetupCheck:
    label: str
    passed: bool
    detail: str = ""


@dataclass(frozen=True, slots=True)
class SetupValidationResult:
    checks: list[SetupCheck] = field(default_factory=list)

    @property
    def is_ready(self) -> bool:
        return all(check.passed for check in self.checks)

    @property
    def failures(self) -> list[SetupCheck]:
        return [check for check in self.checks if not check.passed]


def validate_setup(
    players: list[Player],
    teams: list[Team],
    expected_total: int = EXPECTED_TOTAL_PLAYERS,
    expected_captains: int = EXPECTED_CAPTAIN_COUNT,
    expected_eligible: int = EXPECTED_AUCTION_ELIGIBLE_COUNT,
    expected_teams: int = EXPECTED_TEAM_COUNT,
) -> SetupValidationResult:
    """Check the loaded player/team data against the tournament invariants.

    Never raises for a normal data problem (wrong counts, a bad captain
    link) — that is reported as a failed SetupCheck so the UI can render a
    readable status instead of crashing.
    """
    checks: list[SetupCheck] = []

    checks.append(
        SetupCheck(
            "Total players",
            len(players) == expected_total,
            f"found {len(players)}, expected {expected_total}",
        )
    )

    captain_count = sum(player.is_captain for player in players)
    checks.append(
        SetupCheck(
            "Captains",
            captain_count == expected_captains,
            f"found {captain_count}, expected {expected_captains}",
        )
    )

    eligible_count = sum(player.auction_eligible for player in players)
    checks.append(
        SetupCheck(
            "Auction-eligible players",
            eligible_count == expected_eligible,
            f"found {eligible_count}, expected {expected_eligible}",
        )
    )

    ids = [player.id for player in players]
    duplicate_ids = len(ids) != len(set(ids))
    checks.append(
        SetupCheck(
            "Unique player IDs",
            not duplicate_ids,
            "duplicate player ids found" if duplicate_ids else "",
        )
    )

    ineligible_captains = [player for player in players if player.is_captain and player.auction_eligible]
    checks.append(
        SetupCheck(
            "No captain is auction-eligible",
            not ineligible_captains,
            ", ".join(player.full_name for player in ineligible_captains),
        )
    )

    checks.append(
        SetupCheck(
            "Four teams exist",
            len(teams) == expected_teams,
            f"found {len(teams)}, expected {expected_teams}",
        )
    )

    by_id = {player.id: player for player in players}
    link_issues: list[str] = []
    for team in teams:
        captain = by_id.get(team.captain_player_id)
        if captain is None:
            link_issues.append(f"{team.name}: captain id {team.captain_player_id} not found among players")
        elif not captain.is_captain:
            link_issues.append(f"{team.name}: {captain.full_name} is not marked as a captain")
        elif captain.full_name != team.captain_name:
            link_issues.append(f"{team.name}: captain name mismatch ({captain.full_name} vs {team.captain_name})")
        elif captain.assigned_team != team.name:
            link_issues.append(f"{team.name}: captain is assigned to {captain.assigned_team} instead")
    checks.append(
        SetupCheck(
            "Captain assignments are valid",
            not link_issues,
            "; ".join(link_issues),
        )
    )

    # Every team must end with exactly one GK. A GK captain already fills
    # that slot; every other team needs to acquire one via auction. This
    # only checks there are *enough* eligible GKs for that to be possible
    # (>=, not an exact match) — a genuine surplus/shortage that only
    # emerges from how the auction actually plays out is caught at runtime
    # by services.auction_service's blocked-state detection instead.
    teams_needing_gk = sum(
        1
        for team in teams
        if (captain := by_id.get(team.captain_player_id)) is None or captain.position != Position.GK
    )
    available_gk_count = sum(1 for player in players if player.auction_eligible and player.position == Position.GK)
    checks.append(
        SetupCheck(
            "Sufficient goalkeeper coverage",
            available_gk_count >= teams_needing_gk,
            f"{available_gk_count} auction-eligible GK(s) available for {teams_needing_gk} team(s) without a GK captain",
        )
    )

    return SetupValidationResult(checks=checks)
