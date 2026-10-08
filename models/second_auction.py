"""Second Auction — Milestone 1: transfer-window release-plan setup model
(September/October 2026).

Deliberately minimal: stores only the organizer's per-team release
*selections* and the plan's DRAFT/CONFIRMED status. Refund amounts and
budget totals are never stored here — they are always derived, on demand,
from each released `Player`'s own (immutable, first-auction-final)
`sold_price` plus the existing `services/match_result_service.py` ledger
(see `services/second_auction_service.py`), so there is never a second
number that could disagree with the authoritative data it was computed
from.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class SecondAuctionSetupStatus(str, Enum):
    DRAFT = "DRAFT"
    CONFIRMED = "CONFIRMED"


@dataclass(slots=True)
class SecondAuctionSetup:
    """One tournament session's transfer-window setup state.

    `released_player_ids_by_team` maps a team id to the list of player ids
    the organizer has selected for release from that team's first-auction
    roster — zero to four entries per team while DRAFT, and (once
    `status` is CONFIRMED) exactly four for every one of the session's
    teams. Never mutated by anything in `models/`/`services/auction_
    service.py` — see `services/second_auction_service.py` for every
    read/write rule (captain/GK protection, the four-per-team cap,
    confirm/unlock)."""

    status: SecondAuctionSetupStatus = SecondAuctionSetupStatus.DRAFT
    released_player_ids_by_team: dict[int, list[int]] = field(default_factory=dict)
    confirmed_at: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.status, SecondAuctionSetupStatus):
            self.status = SecondAuctionSetupStatus(self.status)
        # JSON round-trips dict keys as strings; normalize back to int so
        # callers can always index this dict with a real Team.id.
        self.released_player_ids_by_team = {
            int(team_id): list(player_ids) for team_id, player_ids in self.released_player_ids_by_team.items()
        }

    @property
    def is_confirmed(self) -> bool:
        return self.status == SecondAuctionSetupStatus.CONFIRMED

    def selections_for(self, team_id: int) -> list[int]:
        return list(self.released_player_ids_by_team.get(team_id, []))

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SecondAuctionSetup":
        return cls(**data)
