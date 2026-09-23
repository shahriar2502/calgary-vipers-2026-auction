"""Team domain model and its budget/squad invariants."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from models.player import GK_BASE_PRICE, OUTFIELD_BASE_PRICE


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

    def minimum_completion_cost_after_purchase(self, *, purchasing_gk: bool, team_has_gk: bool) -> int | None:
        """The minimum total cost to legally complete this team's squad,
        covering every roster slot that will still be required AFTER the
        purchase under consideration — each such slot filled at its own
        minimum legal price (`GK_BASE_PRICE` for the mandatory goalkeeper
        slot if the team does not already have one and isn't acquiring one
        right now, `OUTFIELD_BASE_PRICE` for every other remaining slot).

        `purchasing_gk` is whether the player under consideration right
        now is a goalkeeper; `team_has_gk` is whether this team already
        owns one BEFORE this purchase.

        Returns `None` when completion would be structurally impossible:
        this purchase would fill the team's very last roster slot without
        the team ever having acquired its mandatory goalkeeper. See
        PROJECT_CONTEXT.md's "FIRST AUCTION RULES V2" for the worked
        examples this formula must match exactly.
        """
        slots_after = max(self.remaining_required_purchases - 1, 0)
        still_needs_gk = not (team_has_gk or purchasing_gk)
        if still_needs_gk:
            if slots_after == 0:
                return None
            return GK_BASE_PRICE + (slots_after - 1) * OUTFIELD_BASE_PRICE
        return slots_after * OUTFIELD_BASE_PRICE

    def maximum_legal_bid(self, *, purchasing_gk: bool, team_has_gk: bool) -> int:
        """The most this team could legally pay for its very next purchase
        (a goalkeeper if `purchasing_gk`, an outfield player otherwise)
        while still able to complete its mandatory 8-player squad
        afterward — see `minimum_completion_cost_after_purchase`. A team
        may legitimately finish the first auction with 0M remaining; this
        is never artificially floored above 0 (a caller displaying it
        should clamp with `max(value, 0)` for presentation, exactly as
        before this rule's dynamic-reserve rewrite).

        Tournament rule (First Auction Rules V2 — supersedes the earlier
        flat 1M-per-slot reserve): see PROJECT_CONTEXT.md's "FIRST AUCTION
        RULES V2" for the formula and worked examples this method's own
        tests mirror directly.
        """
        reserve = self.minimum_completion_cost_after_purchase(purchasing_gk=purchasing_gk, team_has_gk=team_has_gk)
        if reserve is None:
            # No price is ever legal here (see the docstring above) --
            # -1 reads unambiguously as "cannot buy" to every caller,
            # including the base-price floor check below (no legal price
            # is ever <= -1) and any UI that clamps for display.
            return -1
        return self.remaining_budget - reserve

    def can_afford(self, price: int) -> bool:
        return price >= 0 and price <= self.remaining_budget

    def can_afford_while_preserving_roster_budget(self, price: int, *, purchasing_gk: bool, team_has_gk: bool) -> bool:
        """Whether `price` is both >= the purchase's own base price
        (`GK_BASE_PRICE`/`OUTFIELD_BASE_PRICE`) and leaves enough
        remaining budget to still complete the squad afterward."""
        base_price = GK_BASE_PRICE if purchasing_gk else OUTFIELD_BASE_PRICE
        return base_price <= price <= self.maximum_legal_bid(purchasing_gk=purchasing_gk, team_has_gk=team_has_gk)

    def can_buy_player(self, player_id: int, price: int, *, purchasing_gk: bool, team_has_gk: bool) -> bool:
        if purchasing_gk and team_has_gk:
            return False  # exactly one GK per team is mandatory, never negotiable
        return (
            player_id not in self.roster
            and self.roster_size < self.max_squad_size
            and self.can_afford(price)
            and self.can_afford_while_preserving_roster_budget(
                price, purchasing_gk=purchasing_gk, team_has_gk=team_has_gk
            )
        )

    def add_purchased_player(self, player_id: int, price: int, *, purchasing_gk: bool, team_has_gk: bool) -> None:
        if not self.can_buy_player(player_id, price, purchasing_gk=purchasing_gk, team_has_gk=team_has_gk):
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
