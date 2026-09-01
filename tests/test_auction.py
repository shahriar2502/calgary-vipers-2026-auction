import pytest

from models.auction import Auction, AuctionHistoryEntry, AuctionStatus


def test_auction_tracks_current_player_without_exposing_extra_behavior() -> None:
    auction = Auction(queue=[2, 4, 5], status=AuctionStatus.IN_PROGRESS, random_seed=42)
    assert auction.current_player_id == 2
    assert auction.is_complete is False


def test_auction_rejects_duplicate_queue_entries() -> None:
    with pytest.raises(ValueError, match="duplicate"):
        Auction(queue=[2, 2])


def test_auction_round_trip_serialization() -> None:
    entry = AuctionHistoryEntry(
        auction_sequence=1,
        player_id=2,
        player_name="Rahmat Ullah",
        position="DEF",
        overall_rating=89,
        base_price=5,
        status="SOLD",
        team="Blackout FC",
        sold_price=12,
    )
    auction = Auction(
        queue=[2, 4],
        current_queue_position=1,
        history=[entry],
        status=AuctionStatus.IN_PROGRESS,
        random_seed=42,
    )
    assert Auction.from_dict(auction.to_dict()) == auction


def test_invalid_unsold_history_entry_is_rejected() -> None:
    with pytest.raises(ValueError):
        AuctionHistoryEntry(
            auction_sequence=1,
            player_id=2,
            player_name="Rahmat Ullah",
            position="DEF",
            overall_rating=89,
            base_price=5,
            status="UNSOLD",
            team="Blackout FC",
            sold_price=12,
        )
