"""Post-auction match results + transfer budget ledger (September 2026).

Deliberately independent of `services/auction_service.py` (SOLD/UNSOLD/
re-auction/bidding) — this module never touches a `Team`'s
`remaining_budget`, `roster`, or `auction_spending`, and never calls
`process_sale`/`process_unsold`/`place_bid`. It only reads a team's
already-final `remaining_budget` (the first auction's own outcome, frozen
the instant `Auction.status` becomes COMPLETE — nothing in this module or
elsewhere mutates it again) and combines it with a list of `MatchResult`
records to derive a read-only budget ledger.

Money is never double-counted because it is never stored as a running
total: `current_transfer_budget` is always recomputed from
`first_auction_remaining_budget + sum(valid match awards)` rather than
incremented/decremented in place, so editing or deleting a match result
can never leave two independent numbers disagreeing (see
PROJECT_CONTEXT.md's "POST-AUCTION MATCH RESULTS + TRANSFER BUDGET
TRACKER" for the full rationale).
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timezone

from models.match_result import MatchResult
from models.team import Team


class MatchResultError(Exception):
    """Raised when a match-result entry/edit/delete is not currently
    valid. Deliberately a distinct type from `AuctionTransactionError` —
    match accounting is an independent subsystem from bidding."""


def generate_match_id() -> str:
    """A stable, generated identity for a new `MatchResult` — never
    derived from the two team names or the score, so the exact same
    fixture can recur as a distinct record (see the ticket's explicit
    "do not use team names alone as match identity" requirement)."""
    return f"match_{uuid.uuid4().hex[:12]}"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def validate_match_input(team1_id: int, team2_id: int, team1_goals: object, team2_goals: object, teams: Iterable[Team]) -> None:
    """Every check a match entry must pass before a `MatchResult` is even
    constructed — raises `MatchResultError` with an organizer-readable
    message on the first failure. `MatchResult.__post_init__` re-validates
    the same score/team-identity invariants independently (defense in
    depth, same pattern as the auction's own base-price/reserve checks),
    so this function is the friendly, specific-field-aware layer in front
    of it.
    """
    team_ids = {team.id for team in teams}
    if team1_id not in team_ids:
        raise MatchResultError("Team 1 is not one of this tournament's teams.")
    if team2_id not in team_ids:
        raise MatchResultError("Team 2 is not one of this tournament's teams.")
    if team1_id == team2_id:
        raise MatchResultError("Team 1 and Team 2 must be different teams.")
    for label, value in (("Team 1 goals", team1_goals), ("Team 2 goals", team2_goals)):
        if value is None or value == "":
            raise MatchResultError(f"{label} cannot be blank.")
        if isinstance(value, bool) or not isinstance(value, int):
            raise MatchResultError(f"{label} must be a whole number.")
        if value < 0:
            raise MatchResultError(f"{label} cannot be negative.")
        if value > 50:
            raise MatchResultError(f"{label} is not a reasonable score.")


def add_match_result(
    results: list[MatchResult],
    teams: Iterable[Team],
    match_number: int,
    team1_id: int,
    team2_id: int,
    team1_goals: int,
    team2_goals: int,
) -> MatchResult:
    """Validate and append a brand-new `MatchResult` to `results` (in
    place) and return it. Never mutates any `Team`."""
    validate_match_input(team1_id, team2_id, team1_goals, team2_goals, teams)
    now = _now_iso()
    result = MatchResult(
        id=generate_match_id(),
        match_number=match_number,
        team1_id=team1_id,
        team2_id=team2_id,
        team1_goals=team1_goals,
        team2_goals=team2_goals,
        created_at=now,
        updated_at=now,
    )
    results.append(result)
    return result


def update_match_result(
    results: list[MatchResult],
    teams: Iterable[Team],
    match_id: str,
    match_number: int,
    team1_id: int,
    team2_id: int,
    team1_goals: int,
    team2_goals: int,
) -> MatchResult:
    """Replace the existing record identified by `match_id` with a
    corrected one, preserving its `id`/`created_at` — the resulting
    ledger reflects ONLY the corrected score (see `awards()`), never the
    original award plus the new one, because a team's total is always
    recomputed fresh from the current `results` list rather than
    accumulated in place."""
    existing = next((entry for entry in results if entry.id == match_id), None)
    if existing is None:
        raise MatchResultError("This match result no longer exists.")
    validate_match_input(team1_id, team2_id, team1_goals, team2_goals, teams)
    updated = MatchResult(
        id=existing.id,
        match_number=match_number,
        team1_id=team1_id,
        team2_id=team2_id,
        team1_goals=team1_goals,
        team2_goals=team2_goals,
        created_at=existing.created_at,
        updated_at=_now_iso(),
    )
    index = results.index(existing)
    results[index] = updated
    return updated


def delete_match_result(results: list[MatchResult], match_id: str) -> None:
    """Remove the record identified by `match_id` from `results` (in
    place). Its contribution to every team's match earnings disappears
    automatically the next time the ledger is (re)computed — nothing
    else needs updating."""
    existing = next((entry for entry in results if entry.id == match_id), None)
    if existing is None:
        raise MatchResultError("This match result no longer exists.")
    results.remove(existing)


# ----------------------------------------------------------------------
# Budget ledger (read-only, always derived — never a stored running total)
# ----------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MatchAwardEntry:
    """One traceable line in a team's ledger: what this specific match
    contributed to this specific team's earnings."""

    match: MatchResult
    outcome: str  # "WIN", "DRAW", or "LOSS" from this team's perspective
    award: int


@dataclass(frozen=True, slots=True)
class TeamBudgetLedger:
    team: Team
    first_auction_remaining: int
    entries: list[MatchAwardEntry] = field(default_factory=list)
    matches_played: int = 0
    wins: int = 0
    draws: int = 0
    losses: int = 0

    @property
    def total_match_earnings(self) -> int:
        return sum(entry.award for entry in self.entries)

    @property
    def current_transfer_budget(self) -> int:
        return self.first_auction_remaining + self.total_match_earnings


def _outcome_for_team(match: MatchResult, team_id: int) -> str:
    if match.is_draw:
        return "DRAW"
    return "WIN" if match.winner_id == team_id else "LOSS"


def build_team_ledger(team: Team, results: Iterable[MatchResult]) -> TeamBudgetLedger:
    """Derive `team`'s complete post-auction ledger from its own
    (immutable, first-auction-final) `remaining_budget` plus every match
    result it participated in. A match result naming a team id that isn't
    `team.id` (and isn't its opponent either) simply never matches here —
    it contributes nothing, rather than raising, so one orphaned/corrupt
    record can never break every other team's ledger."""
    entries = [
        MatchAwardEntry(match=match, outcome=_outcome_for_team(match, team.id), award=match.awards()[team.id])
        for match in results
        if team.id in (match.team1_id, match.team2_id)
    ]
    wins = sum(1 for entry in entries if entry.outcome == "WIN")
    draws = sum(1 for entry in entries if entry.outcome == "DRAW")
    losses = sum(1 for entry in entries if entry.outcome == "LOSS")
    return TeamBudgetLedger(
        team=team,
        first_auction_remaining=team.remaining_budget,
        entries=entries,
        matches_played=len(entries),
        wins=wins,
        draws=draws,
        losses=losses,
    )


def build_all_ledgers(teams: Iterable[Team], results: Iterable[MatchResult]) -> list[TeamBudgetLedger]:
    results = list(results)
    return [build_team_ledger(team, results) for team in teams]
