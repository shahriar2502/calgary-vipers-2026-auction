"""Randomized hidden auction queue creation.

Pure business logic: no GUI, no file persistence. The queue is generated
once from an in-memory player collection and stored as an ordered list of
player ids, ready to be attached to an `Auction`.
"""

from __future__ import annotations

import random
from collections.abc import Iterable

from models.auction import Auction, AuctionStatus
from models.player import Player

# Mirrors data/settings.json's "auction_player_count". Kept as a plain
# default (not read from JSON) so this module has no file I/O of its own
# and callers/tests can override it explicitly for other tournament sizes.
EXPECTED_ELIGIBLE_PLAYER_COUNT = 28


def create_auction_queue(
    players: Iterable[Player],
    seed: int | None = None,
    expected_count: int | None = EXPECTED_ELIGIBLE_PLAYER_COUNT,
) -> list[int]:
    """Build one randomized queue of auction-eligible player ids.

    The input collection (and therefore data/players.json's on-disk order,
    if that is what was loaded) is never modified. Invalid tournament data
    raises ValueError immediately rather than silently producing a
    wrong-sized or captain-containing queue:

    - duplicate player ids in the input
    - a captain marked auction-eligible
    - an auction-eligible count that does not match `expected_count`
      (pass expected_count=None to skip this check)

    With a seed, the same players and seed always produce the same order;
    without one, ordering uses normal randomness.
    """
    players = list(players)

    ids = [player.id for player in players]
    if len(ids) != len(set(ids)):
        raise ValueError("Player collection contains duplicate player ids")

    eligible = [player for player in players if player.auction_eligible]

    captains = [player for player in eligible if player.is_captain]
    if captains:
        names = ", ".join(player.full_name for player in captains)
        raise ValueError(f"Captains must never be auction eligible: {names}")

    if expected_count is not None and len(eligible) != expected_count:
        raise ValueError(
            f"Expected exactly {expected_count} auction-eligible players, found {len(eligible)}"
        )

    eligible_ids = [player.id for player in eligible]
    random.Random(seed).shuffle(eligible_ids)
    return eligible_ids


def create_auction(
    players: Iterable[Player],
    seed: int | None = None,
    expected_count: int | None = EXPECTED_ELIGIBLE_PLAYER_COUNT,
) -> Auction:
    """Create a fresh, in-progress Auction with a newly randomized queue."""
    queue = create_auction_queue(players, seed=seed, expected_count=expected_count)
    return Auction(
        queue=queue,
        current_queue_position=0,
        status=AuctionStatus.IN_PROGRESS,
        random_seed=seed,
    )
