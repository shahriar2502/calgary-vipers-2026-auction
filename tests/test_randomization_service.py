import json
from pathlib import Path

import pytest

from models.auction import Auction, AuctionStatus
from models.player import Player
from services.randomization_service import create_auction, create_auction_queue

ROOT = Path(__file__).resolve().parents[1]


def load_players() -> list[Player]:
    data = json.loads((ROOT / "data" / "players.json").read_text(encoding="utf-8"))
    return [Player.from_dict(item) for item in data]


def test_queue_contains_exactly_28_eligible_non_captain_players() -> None:
    players = load_players()
    queue = create_auction_queue(players, seed=1)

    assert len(queue) == 28
    assert len(set(queue)) == 28

    by_id = {player.id: player for player in players}
    queued_players = [by_id[player_id] for player_id in queue]
    assert all(player.auction_eligible for player in queued_players)
    assert all(not player.is_captain for player in queued_players)


@pytest.mark.parametrize(
    "captain_name",
    ["Samin Haque", "Sabit Khan", "Arafatul Mamur", "Riyad Zaman"],
)
def test_named_captain_never_appears_in_queue(captain_name: str) -> None:
    players = load_players()
    by_name = {player.full_name: player for player in players}
    queue = create_auction_queue(players, seed=1)
    assert by_name[captain_name].id not in queue


def test_every_eligible_player_appears_exactly_once() -> None:
    players = load_players()
    queue = create_auction_queue(players, seed=7)
    expected_ids = {player.id for player in players if player.auction_eligible}
    assert set(queue) == expected_ids
    assert len(queue) == len(expected_ids)


def test_same_seed_produces_identical_queue() -> None:
    players = load_players()
    first = create_auction_queue(players, seed=42)
    second = create_auction_queue(players, seed=42)
    assert first == second


def test_different_seeds_can_produce_different_orders() -> None:
    players = load_players()
    orders = {tuple(create_auction_queue(players, seed=seed)) for seed in range(10)}
    assert len(orders) > 1


def test_no_seed_still_returns_a_valid_shuffled_queue() -> None:
    players = load_players()
    queue = create_auction_queue(players)
    assert len(queue) == 28
    assert len(set(queue)) == 28


def test_queue_generation_does_not_reorder_players_json() -> None:
    path = ROOT / "data" / "players.json"
    before = path.read_text(encoding="utf-8")
    players = load_players()
    original_order = [player.id for player in players]

    create_auction_queue(players, seed=3)

    assert [player.id for player in players] == original_order
    assert path.read_text(encoding="utf-8") == before


def test_duplicate_player_ids_in_input_are_rejected() -> None:
    players = load_players()
    players.append(players[0])
    with pytest.raises(ValueError, match="duplicate"):
        create_auction_queue(players, seed=1)


def test_captain_accidentally_marked_eligible_is_rejected() -> None:
    players = load_players()
    captain = next(p for p in players if p.full_name == "Samin Haque")
    captain.auction_eligible = True  # simulate corrupted in-memory state
    with pytest.raises(ValueError, match="[Cc]aptain"):
        create_auction_queue(players, seed=1)


def test_fewer_than_expected_eligible_players_is_rejected() -> None:
    players = load_players()
    dropped_id = next(p.id for p in players if p.auction_eligible)
    players = [p for p in players if p.id != dropped_id]
    with pytest.raises(ValueError, match="28"):
        create_auction_queue(players, seed=1)


def test_more_than_expected_eligible_players_is_rejected() -> None:
    players = load_players()
    extra = Player(
        id=999,
        full_name="Extra Player",
        short_name="Extra",
        position=players[1].position,
        overall_rating=80,
    )
    players.append(extra)
    with pytest.raises(ValueError, match="28"):
        create_auction_queue(players, seed=1)


def test_expected_count_check_can_be_disabled_explicitly() -> None:
    players = load_players()
    dropped_id = next(p.id for p in players if p.auction_eligible)
    players = [p for p in players if p.id != dropped_id]
    queue = create_auction_queue(players, seed=1, expected_count=None)
    assert len(queue) == 27


def test_create_auction_returns_in_progress_auction_with_seeded_queue() -> None:
    players = load_players()
    auction = create_auction(players, seed=99)

    assert isinstance(auction, Auction)
    assert auction.status == AuctionStatus.IN_PROGRESS
    assert auction.random_seed == 99
    assert auction.current_queue_position == 0
    assert len(auction.queue) == 28
    assert auction.current_player_id == auction.queue[0]

    again = create_auction(players, seed=99)
    assert again.queue == auction.queue
