"""Post-auction match result domain model (September 2026).

Deliberately independent of the first-auction transaction models
(`models/auction.py`, `models/team.py`) — a `MatchResult` records a
football match's final score between two of the tournament's existing
teams and derives its own win/draw/loss outcome and match-money award
from that score. Nothing here mutates a `Team`'s budget directly (see
`services/match_result_service.py` for how awards are combined into a
read-only budget ledger instead).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

# Match-money awards (First Auction Rules V2's sibling ruleset for the
# post-auction phase) — the single source of truth. Never hard-code these
# amounts anywhere else (UI, services, tests) — import them from here.
WIN_AWARD_M = 4
DRAW_AWARD_M = 2
LOSS_AWARD_M = 1

# A goal count above this is almost certainly a data-entry mistake, not a
# real result — validated here so a typo can't silently corrupt the
# budget ledger with an absurd award-eligible "result."
MAX_REASONABLE_GOALS = 50


@dataclass(slots=True)
class MatchResult:
    """One organizer-entered match result. `id` is a stable, generated
    identity (see `services/match_result_service.generate_match_id`) —
    never derived from the two team names, so the same fixture can recur
    across the tournament as distinct records. `match_number` is a plain,
    organizer-facing label (not enforced unique — the organizer's own
    schedule numbering is authoritative, not this app's)."""

    id: str
    match_number: int
    team1_id: int
    team2_id: int
    team1_goals: int
    team2_goals: int
    created_at: str
    updated_at: str | None = None

    def __post_init__(self) -> None:
        if not self.id or not isinstance(self.id, str):
            raise ValueError("Match result id must be a non-empty string")
        if self.match_number <= 0:
            raise ValueError("Match number must be positive")
        if self.team1_id == self.team2_id:
            raise ValueError("A match must be between two different teams")
        for label, value in (("team1_goals", self.team1_goals), ("team2_goals", self.team2_goals)):
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"{label} must be a whole number")
            if value < 0:
                raise ValueError(f"{label} cannot be negative")
            if value > MAX_REASONABLE_GOALS:
                raise ValueError(f"{label} exceeds the maximum reasonable value ({MAX_REASONABLE_GOALS})")

    @property
    def is_draw(self) -> bool:
        return self.team1_goals == self.team2_goals

    @property
    def winner_id(self) -> int | None:
        """`None` for a draw — never guessed."""
        if self.is_draw:
            return None
        return self.team1_id if self.team1_goals > self.team2_goals else self.team2_id

    @property
    def loser_id(self) -> int | None:
        if self.is_draw:
            return None
        return self.team2_id if self.winner_id == self.team1_id else self.team1_id

    def awards(self) -> dict[int, int]:
        """This match's money award for each of its two participating
        teams, keyed by team id — the single place the WIN/DRAW/LOSS
        amounts are ever applied to a specific match's score."""
        if self.is_draw:
            return {self.team1_id: DRAW_AWARD_M, self.team2_id: DRAW_AWARD_M}
        return {self.winner_id: WIN_AWARD_M, self.loser_id: LOSS_AWARD_M}

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MatchResult":
        return cls(**data)
