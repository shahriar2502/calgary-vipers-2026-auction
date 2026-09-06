"""Team domain model and its budget/squad invariants."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

# The minimum legal price for any auction purchase (pre-Milestone-9 budget
# reserve fix — see PROJECT_CONTEXT.md's "PRE-M9 BUDGET RESERVE RULE"). A
# single source of truth: services/auction_service.py imports this same
# constant rather than hard-coding "1" a second time.
MINIMUM_LEGAL_PRICE = 1


@dataclass(slots=True)
class Team:
    id: int
    name: str
    short_name: str
    captain_player_id: int
    captain_name: str
    starting_budget: int = 100
    remaining_budget: int = 100
    max_squad_size: int = 8
    roster: list[int] = field(default_factory=list)
    auction_spending: int = 0
    players_purchased: int = 0

    def __post_init__(self) -> None:
        if self.id <= 0 or self.captain_player_id <= 0:
            raise ValueError("Team and captain ids must be positive")
        if not self.name.strip() or not self.short_name.strip() or not self.captain_name.strip():
            raise ValueError("Team and captain names cannot be blank")
        if self.starting_budget < 0 or self.remaining_budget < 0:
            raise ValueError("Budgets cannot be negative")
        if self.remaining_budget > self.starting_budget:
            raise ValueError("Remaining budget cannot exceed starting budget")
        if self.max_squad_size < 1:
            raise ValueError("Maximum squad size must be at least one")
        if not self.roster:
            self.roster.append(self.captain_player_id)
        if self.captain_player_id not in self.roster:
            raise ValueError("Team roster must contain its captain")
        if len(self.roster) != len(set(self.roster)):
            raise ValueError("Team roster cannot contain duplicate players")
        if len(self.roster) > self.max_squad_size:
            raise ValueError("Team roster exceeds maximum squad size")
        if self.auction_spending < 0 or self.auction_spending > self.starting_budget:
            raise ValueError("Auction spending must be within the starting budget")
        if self.remaining_budget != self.starting_budget - self.auction_spending:
            raise ValueError("Remaining budget and auction spending are inconsistent")
        if self.players_purchased != len(self.roster) - 1:
            raise ValueError("players_purchased must exclude the captain")

    @property
    def roster_size(self) -> int:
        return len(self.roster)

    @property
    def remaining_required_purchases(self) -> int:
        """How many more players this team must still buy to reach a full
        (`max_squad_size`) roster, including whatever purchase is being
        considered right now. Zero once the roster is full."""
        return max(self.max_squad_size - self.roster_size, 0)

    @property
    def maximum_legal_bid(self) -> int:
        """The most this team could legally pay for its very next purchase
        while still able to afford every purchase after that at the
        minimum legal price (`MINIMUM_LEGAL_PRICE` per slot still needed).

        Tournament rule (pre-Milestone-9 budget reserve fix): a team must
        never spend so much on one player that filling its remaining
        roster slots afterward becomes mathematically impossible. See
        PROJECT_CONTEXT.md's "PRE-M9 BUDGET RESERVE RULE" for the formula
        and worked examples.
        """
        slots_after_this_purchase = max(self.remaining_required_purchases - 1, 0)
        reserve_after_this_purchase = slots_after_this_purchase * MINIMUM_LEGAL_PRICE
        return self.remaining_budget - reserve_after_this_purchase

    def can_afford(self, price: int) -> bool:
        return price >= 0 and price <= self.remaining_budget

    def can_afford_while_preserving_roster_budget(self, price: int) -> bool:
        """Whether `price` is both >= the minimum legal price and leaves
        enough remaining budget to still fill every roster slot needed
        after this purchase, each at the minimum legal price."""
        return MINIMUM_LEGAL_PRICE <= price <= self.maximum_legal_bid

    def can_buy_player(self, player_id: int, price: int) -> bool:
        return (
            player_id not in self.roster
            and self.roster_size < self.max_squad_size
            and self.can_afford(price)
            and self.can_afford_while_preserving_roster_budget(price)
        )

    def add_purchased_player(self, player_id: int, price: int) -> None:
        if not self.can_buy_player(player_id, price):
            raise ValueError("Team cannot buy this player at the given price")
        self.roster.append(player_id)
        self.remaining_budget -= price
        self.auction_spending += price
        self.players_purchased += 1

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Team":
        return cls(**data)
