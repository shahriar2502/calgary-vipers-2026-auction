"""Auction state model. Auction workflows will be added in a later milestone."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class AuctionStatus(str, Enum):
    NOT_STARTED = "NOT_STARTED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETE = "COMPLETE"


@dataclass(slots=True)
class AuctionHistoryEntry:
    auction_sequence: int
    player_id: int
    player_name: str
    position: str
    overall_rating: int
    base_price: int | None
    status: str
    team: str | None = None
    sold_price: int | None = None

    def __post_init__(self) -> None:
        if self.auction_sequence <= 0 or self.player_id <= 0:
            raise ValueError("Sequence and player id must be positive")
        if self.status not in {"SOLD", "UNSOLD"}:
            raise ValueError("History status must be SOLD or UNSOLD")
        if self.status == "SOLD" and (not self.team or self.sold_price is None):
            raise ValueError("A SOLD history entry requires team and sold_price")
        if self.status == "UNSOLD" and (self.team is not None or self.sold_price is not None):
            raise ValueError("An UNSOLD history entry cannot contain sale data")


@dataclass(slots=True)
class Auction:
    queue: list[int] = field(default_factory=list)
    current_queue_position: int = 0
    history: list[AuctionHistoryEntry] = field(default_factory=list)
    status: AuctionStatus = AuctionStatus.NOT_STARTED
    random_seed: int | None = None
    current_bid: int | None = None
    leading_team_id: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.status, AuctionStatus):
            self.status = AuctionStatus(self.status)
        if len(self.queue) != len(set(self.queue)):
            raise ValueError("Auction queue cannot contain duplicate players")
        if not 0 <= self.current_queue_position <= len(self.queue):
            raise ValueError("Current queue position is outside the queue")
        if self.current_bid is not None and self.current_bid < 0:
            raise ValueError("Current bid cannot be negative")

    @property
    def current_player_id(self) -> int | None:
        if self.current_queue_position >= len(self.queue):
            return None
        return self.queue[self.current_queue_position]

    @property
    def is_complete(self) -> bool:
        return bool(self.queue) and self.current_queue_position >= len(self.queue)

    def to_dict(self) -> dict[str, Any]:
        return {
            "queue": self.queue.copy(),
            "current_queue_position": self.current_queue_position,
            "history": [asdict(entry) for entry in self.history],
            "status": self.status.value,
            "random_seed": self.random_seed,
            "current_bid": self.current_bid,
            "leading_team_id": self.leading_team_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Auction":
        values = dict(data)
        values["history"] = [AuctionHistoryEntry(**entry) for entry in values.get("history", [])]
        return cls(**values)
