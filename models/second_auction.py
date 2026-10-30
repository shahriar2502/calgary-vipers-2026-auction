"""Second Auction models (September/October 2026).

`SecondAuctionSetup` (Milestone 1): the transfer-window release-plan
setup model. Deliberately minimal — stores only the organizer's per-team
release *selections* and the plan's DRAFT/CONFIRMED status. Refund
amounts and budget totals are never stored here — they are always
derived, on demand, from each released `Player`'s own (immutable,
first-auction-final) `sold_price` plus the existing `services/match_
result_service.py` ledger (see `services/second_auction_service.py`), so
there is never a second number that could disagree with the authoritative
data it was computed from.

`SecondAuctionState` (Milestone 2): the second-auction bidding engine's
own state — see its own docstring below and `services/second_auction_
bidding_service.py`.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class SecondAuctionSetupStatus(str, Enum):
    DRAFT = "DRAFT"
    CONFIRMED = "CONFIRMED"


class SecondAuctionStatus(str, Enum):
    """Second Auction — Milestone 2 (October 2026): the bidding engine's
    own lifecycle, entirely independent of the first auction's
    `AuctionStatus` and of `SecondAuctionSetupStatus` above (which only
    ever reaches CONFIRMED, never any of these four values)."""

    NOT_STARTED = "NOT_STARTED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETE = "COMPLETE"
    BLOCKED = "BLOCKED"


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


@dataclass(slots=True)
class SecondAuctionHistoryEntry:
    """One second-auction SOLD/UNSOLD transaction — the second auction's
    own transaction log, entirely separate from `Auction.history`
    (first-auction SOLD/UNSOLD) so neither can ever overwrite or be
    confused with the other. `original_first_auction_price` is carried
    here (copied once, at release-pool build time) specifically so this
    record stays self-contained and auditable even though
    `Player.sold_price` itself is never touched by the second auction."""

    sequence: int
    player_id: int
    player_name: str
    position: str
    previous_team_id: int
    previous_team_name: str
    original_first_auction_price: int
    status: str  # SOLD or UNSOLD
    team_id: int | None = None  # the second-auction buying team
    team_name: str | None = None
    sold_price: int | None = None
    round_number: int = 1

    def __post_init__(self) -> None:
        if self.sequence <= 0 or self.player_id <= 0:
            raise ValueError("Sequence and player id must be positive")
        if self.status not in {"SOLD", "UNSOLD"}:
            raise ValueError("History status must be SOLD or UNSOLD")
        if self.status == "SOLD" and (self.team_id is None or not self.team_name or self.sold_price is None):
            raise ValueError("A SOLD history entry requires team_id, team_name, and sold_price")
        if self.status == "UNSOLD" and (
            self.team_id is not None or self.team_name is not None or self.sold_price is not None
        ):
            raise ValueError("An UNSOLD history entry cannot contain sale data")
        if self.round_number <= 0:
            raise ValueError("Round number must be positive")


@dataclass(slots=True)
class SecondAuctionState:
    """Second Auction — Milestone 2: the bidding engine's own state, a
    sibling of `models/auction.py`'s `Auction` but deliberately not a
    reuse of it — the first-auction `Team`/`Player` objects are never
    mutated by anything below (see `services/second_auction_bidding_
    service.py`'s module docstring). Every team's second-auction budget
    and "current tournament roster" lives only here, keyed by `Team.id`.

    `active_rosters_by_team` is each team's *current tournament* squad —
    starting from its 4 retained (non-released) first-auction players and
    growing to 8 as second-auction purchases are made — never to be
    confused with the frozen, original `Team.roster` (always exactly the
    8 first-auction players, forever).

    `current_bid`/`leading_team_id` (Second Auction — Milestone 3,
    October 2026: captain phone bidding) are the live, attempt-scoped
    equivalent of `models/auction.py`'s `Auction.current_bid`/
    `leading_team_id` — a second, independent pair of fields so a
    second-auction phone bid can never be confused with a first-auction
    one. Reset to `None` the instant a SOLD/UNSOLD reveals the next
    second-auction player (see `services/second_auction_bidding_
    service.py`'s `_finish_transaction`), exactly mirroring the first
    auction's own reset rule."""

    status: SecondAuctionStatus = SecondAuctionStatus.NOT_STARTED
    queue: list[int] = field(default_factory=list)
    current_queue_position: int = 0
    round_number: int = 1
    released_player_ids: list[int] = field(default_factory=list)
    starting_budgets_by_team: dict[int, int] = field(default_factory=dict)
    remaining_budgets_by_team: dict[int, int] = field(default_factory=dict)
    active_rosters_by_team: dict[int, list[int]] = field(default_factory=dict)
    history: list[SecondAuctionHistoryEntry] = field(default_factory=list)
    random_seed: int | None = None
    initialized_at: str | None = None
    completed_at: str | None = None
    current_bid: int | None = None
    leading_team_id: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.status, SecondAuctionStatus):
            self.status = SecondAuctionStatus(self.status)
        if len(self.queue) != len(set(self.queue)):
            raise ValueError("Second-auction queue cannot contain duplicate players")
        if not 0 <= self.current_queue_position <= len(self.queue):
            raise ValueError("Current queue position is outside the queue")
        # JSON round-trips dict keys as strings; normalize back to int so
        # callers can always index these dicts with a real Team.id —
        # exactly the same rationale as SecondAuctionSetup above.
        self.starting_budgets_by_team = {int(k): v for k, v in self.starting_budgets_by_team.items()}
        self.remaining_budgets_by_team = {int(k): v for k, v in self.remaining_budgets_by_team.items()}
        self.active_rosters_by_team = {int(k): list(v) for k, v in self.active_rosters_by_team.items()}

    @property
    def is_initialized(self) -> bool:
        return self.status != SecondAuctionStatus.NOT_STARTED

    @property
    def current_player_id(self) -> int | None:
        if self.current_queue_position >= len(self.queue):
            return None
        return self.queue[self.current_queue_position]

    @property
    def queue_exhausted(self) -> bool:
        """Whether the *current round's* queue has been fully worked
        through — mirrors `Auction.is_complete`'s naming, but under a
        different name here since `SecondAuctionStatus.COMPLETE` means
        something broader (the whole second auction, not just one
        round)."""
        return bool(self.queue) and self.current_queue_position >= len(self.queue)

    def advance(self) -> None:
        if not self.queue:
            raise ValueError("Cannot advance an empty second-auction queue")
        if self.queue_exhausted:
            raise ValueError("Cannot advance past a completed second-auction round")
        self.current_queue_position += 1

    def start_new_round(self, new_queue: list[int]) -> None:
        if not new_queue:
            raise ValueError("Cannot start a new round with an empty queue")
        if len(new_queue) != len(set(new_queue)):
            raise ValueError("New round queue cannot contain duplicate players")
        self.queue = list(new_queue)
        self.current_queue_position = 0
        self.round_number += 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "queue": self.queue.copy(),
            "current_queue_position": self.current_queue_position,
            "round_number": self.round_number,
            "released_player_ids": self.released_player_ids.copy(),
            "starting_budgets_by_team": dict(self.starting_budgets_by_team),
            "remaining_budgets_by_team": dict(self.remaining_budgets_by_team),
            "active_rosters_by_team": {k: list(v) for k, v in self.active_rosters_by_team.items()},
            "history": [asdict(entry) for entry in self.history],
            "random_seed": self.random_seed,
            "initialized_at": self.initialized_at,
            "completed_at": self.completed_at,
            "current_bid": self.current_bid,
            "leading_team_id": self.leading_team_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SecondAuctionState":
        values = dict(data)
        values["history"] = [SecondAuctionHistoryEntry(**entry) for entry in values.get("history", [])]
        return cls(**values)
