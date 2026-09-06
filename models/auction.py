"""Auction state model, including hidden-queue advancement.

Bidding and SOLD/UNSOLD workflows will be added in a later milestone.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class AuctionStatus(str, Enum):
    NOT_STARTED = "NOT_STARTED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETE = "COMPLETE"
    BLOCKED = "BLOCKED"  # no remaining unsold player can legally go to any team


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
    # Which auction round this attempt happened in (Auction.round_number at
    # the time). Optional/additive so an older Milestone 9 save file whose
    # entries predate this field still loads: a dict missing the key simply
    # falls back to this default via `AuctionHistoryEntry(**entry)` — no
    # migration needed. `None` means "unknown round" (a legacy entry), never
    # a guessed value.
    round_number: int | None = None

    def __post_init__(self) -> None:
        if self.auction_sequence <= 0 or self.player_id <= 0:
            raise ValueError("Sequence and player id must be positive")
        if self.status not in {"SOLD", "UNSOLD"}:
            raise ValueError("History status must be SOLD or UNSOLD")
        if self.status == "SOLD" and (not self.team or self.sold_price is None):
            raise ValueError("A SOLD history entry requires team and sold_price")
        if self.status == "UNSOLD" and (self.team is not None or self.sold_price is not None):
            raise ValueError("An UNSOLD history entry cannot contain sale data")
        if self.round_number is not None and self.round_number <= 0:
            raise ValueError("Round number must be positive")


@dataclass(slots=True)
class Auction:
    queue: list[int] = field(default_factory=list)
    current_queue_position: int = 0
    history: list[AuctionHistoryEntry] = field(default_factory=list)
    status: AuctionStatus = AuctionStatus.NOT_STARTED
    random_seed: int | None = None
    current_bid: int | None = None
    leading_team_id: int | None = None
    round_number: int = 1  # 1 = the original queue; 2+ = a re-auction round

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

    @property
    def is_initialized(self) -> bool:
        """Whether a randomized queue has been generated for this auction."""
        return bool(self.queue)

    @property
    def has_current_player(self) -> bool:
        """Whether there is a player waiting to be revealed right now."""
        return self.current_player_id is not None

    def advance(self) -> None:
        """Move to the next queue position.

        Raises instead of silently continuing so callers cannot advance an
        empty or already-completed queue by mistake.
        """
        if not self.queue:
            raise ValueError("Cannot advance an empty auction queue")
        if self.is_complete:
            raise ValueError("Cannot advance past a completed auction queue")
        self.current_queue_position += 1

    def start_new_round(self, new_queue: list[int]) -> None:
        """Replace the current (exhausted) round's queue with a fresh
        re-auction pool and begin the next round.

        Only call this once the current round's queue is exhausted
        (`is_complete`); the auction-service layer is responsible for
        deciding *whether* a new round is needed and for shuffling
        `new_queue` — randomization does not belong on this model, same as
        the original queue's creation.
        """
        if not new_queue:
            raise ValueError("Cannot start a new round with an empty queue")
        if len(new_queue) != len(set(new_queue)):
            raise ValueError("New round queue cannot contain duplicate players")
        self.queue = list(new_queue)
        self.current_queue_position = 0
        self.round_number += 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "queue": self.queue.copy(),
            "current_queue_position": self.current_queue_position,
            "history": [asdict(entry) for entry in self.history],
            "status": self.status.value,
            "random_seed": self.random_seed,
            "current_bid": self.current_bid,
            "leading_team_id": self.leading_team_id,
            "round_number": self.round_number,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Auction":
        values = dict(data)
        values["history"] = [AuctionHistoryEntry(**entry) for entry in values.get("history", [])]
        return cls(**values)
