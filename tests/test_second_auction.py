"""tests/test_second_auction.py — models/second_auction.py:
SecondAuctionSetup's own shape/round-trip invariants."""
from models.second_auction import SecondAuctionSetup, SecondAuctionSetupStatus


def test_default_setup_is_draft_with_no_releases() -> None:
    setup = SecondAuctionSetup()
    assert setup.status == SecondAuctionSetupStatus.DRAFT
    assert setup.is_confirmed is False
    assert setup.released_player_ids_by_team == {}
    assert setup.confirmed_at is None


def test_selections_for_unknown_team_returns_empty_list() -> None:
    setup = SecondAuctionSetup()
    assert setup.selections_for(999) == []


def test_selections_for_returns_a_copy_not_the_live_list() -> None:
    setup = SecondAuctionSetup(released_player_ids_by_team={1: [10, 11]})
    selections = setup.selections_for(1)
    selections.append(999)
    assert setup.selections_for(1) == [10, 11]


def test_to_dict_from_dict_round_trip() -> None:
    setup = SecondAuctionSetup(
        status=SecondAuctionSetupStatus.CONFIRMED,
        released_player_ids_by_team={1: [10, 11, 12, 13], 2: [20, 21, 22, 23]},
        confirmed_at="2026-01-01T00:00:00+00:00",
    )
    restored = SecondAuctionSetup.from_dict(setup.to_dict())
    assert restored == setup


def test_from_dict_normalizes_string_team_id_keys() -> None:
    """JSON always round-trips dict keys as strings; the model normalizes
    them back to int so callers can index with a real Team.id."""
    restored = SecondAuctionSetup.from_dict(
        {"status": "DRAFT", "released_player_ids_by_team": {"1": [10, 11]}, "confirmed_at": None}
    )
    assert restored.selections_for(1) == [10, 11]


def test_status_accepts_plain_string_value() -> None:
    setup = SecondAuctionSetup(status="CONFIRMED")
    assert setup.status == SecondAuctionSetupStatus.CONFIRMED
    assert setup.is_confirmed is True
