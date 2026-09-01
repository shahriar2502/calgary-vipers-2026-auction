"""Player domain model."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any


class Position(str, Enum):
    GK = "GK"
    DEF = "DEF"
    MID = "MID"
    ATT = "ATT"


class PlayerAuctionStatus(str, Enum):
    PRE_ASSIGNED = "PRE_ASSIGNED"
    AVAILABLE = "AVAILABLE"
    SOLD = "SOLD"
    UNSOLD = "UNSOLD"


@dataclass(slots=True)
class Player:
    id: int
    full_name: str
    short_name: str
    position: Position
    overall_rating: int
    photo_path: str | None = None
    is_captain: bool = False
    assigned_team: str | None = None
    auction_eligible: bool = True
    auction_status: PlayerAuctionStatus = PlayerAuctionStatus.AVAILABLE
    base_price: int | None = None
    sold_price: int | None = None
    sold_to: str | None = None
    auction_sequence: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.position, Position):
            self.position = Position(self.position)
        if not isinstance(self.auction_status, PlayerAuctionStatus):
            self.auction_status = PlayerAuctionStatus(self.auction_status)
        if self.id <= 0:
            raise ValueError("Player id must be positive")
        if not self.full_name.strip() or not self.short_name.strip():
            raise ValueError("Player names cannot be blank")
        if not 0 <= self.overall_rating <= 100:
            raise ValueError("Overall rating must be between 0 and 100")
        for field_name, value in (("base_price", self.base_price), ("sold_price", self.sold_price)):
            if value is not None and value < 0:
                raise ValueError(f"{field_name} cannot be negative")

        if self.is_captain:
            if self.auction_eligible:
                raise ValueError("Captains cannot be auction eligible")
            if not self.assigned_team:
                raise ValueError("Captains must be pre-assigned to a team")
            if self.auction_status != PlayerAuctionStatus.PRE_ASSIGNED:
                raise ValueError("Captains must have PRE_ASSIGNED status")
            if any(value is not None for value in (self.base_price, self.sold_price, self.sold_to, self.auction_sequence)):
                raise ValueError("Captains cannot contain auction result data")
        elif not self.auction_eligible and self.auction_status == PlayerAuctionStatus.AVAILABLE:
            raise ValueError("An ineligible player cannot have AVAILABLE status")

        if self.auction_status == PlayerAuctionStatus.SOLD:
            if not self.sold_to or self.sold_price is None:
                raise ValueError("A sold player requires sold_to and sold_price")
        elif self.sold_to is not None or self.sold_price is not None:
            raise ValueError("Only SOLD players may contain sold result data")

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["position"] = self.position.value
        data["auction_status"] = self.auction_status.value
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Player":
        return cls(**data)
