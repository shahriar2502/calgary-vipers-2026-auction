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


def test_history_entry_round_number_defaults_to_none() -> None:
    entry = AuctionHistoryEntry(
        auction_sequence=1,
        player_id=2,
        player_name="Rahmat Ullah",
        position="DEF",
        overall_rating=89,
        base_price=5,
        status="UNSOLD",
    )
    assert entry.round_number is None


def test_history_entry_accepts_explicit_round_number() -> None:
    entry = AuctionHistoryEntry(
        auction_sequence=1,
        player_id=2,
        player_name="Rahmat Ullah",
        position="DEF",
        overall_rating=89,
        base_price=5,
        status="UNSOLD",
        round_number=2,
    )
    assert entry.round_number == 2


def test_history_entry_rejects_non_positive_round_number() -> None:
    with pytest.raises(ValueError, match="Round number"):
        AuctionHistoryEntry(
            auction_sequence=1,
            player_id=2,
            player_name="Rahmat Ullah",
            position="DEF",
            overall_rating=89,
            base_price=5,
            status="UNSOLD",
            round_number=0,
        )


def test_old_history_entry_dict_missing_round_number_loads_safely() -> None:
    """A Milestone 9 save file predating this field lacks the key entirely —
    AuctionHistoryEntry(**entry) must still succeed via the dataclass
    default rather than raising a missing-argument TypeError."""
    legacy_entry_dict = {
        "auction_sequence": 1,
        "player_id": 2,
        "player_name": "Rahmat Ullah",
        "position": "DEF",
        "overall_rating": 89,
        "base_price": 5,
        "status": "SOLD",
        "team": "Blackout FC",
        "sold_price": 12,
    }
    entry = AuctionHistoryEntry(**legacy_entry_dict)
    assert entry.round_number is None


def test_is_initialized_reflects_queue_state() -> None:
    assert Auction().is_initialized is False
    assert Auction(queue=[1, 2]).is_initialized is True


def test_advance_moves_to_next_player() -> None:
    auction = Auction(queue=[2, 4, 5], status=AuctionStatus.IN_PROGRESS)
    assert auction.current_player_id == 2
    assert auction.has_current_player is True

    auction.advance()
    assert auction.current_player_id == 4
    assert auction.has_current_player is True
    assert auction.is_complete is False


def test_advance_through_full_queue_marks_complete() -> None:
    auction = Auction(queue=[2, 4], status=AuctionStatus.IN_PROGRESS)
    auction.advance()
    auction.advance()
    assert auction.current_player_id is None
    assert auction.has_current_player is False
    assert auction.is_complete is True


def test_advance_past_completion_raises() -> None:
    auction = Auction(queue=[2], current_queue_position=1)
    with pytest.raises(ValueError, match="completed"):
        auction.advance()


def test_advance_on_empty_queue_raises() -> None:
    auction = Auction()
    with pytest.raises(ValueError, match="empty"):
        auction.advance()
