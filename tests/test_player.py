import json
from pathlib import Path

import pytest

from models.player import Player, PlayerAuctionStatus, Position


ROOT = Path(__file__).resolve().parents[1]


def load_players() -> list[Player]:
    data = json.loads((ROOT / "data" / "players.json").read_text(encoding="utf-8"))
    return [Player.from_dict(item) for item in data]


def test_player_database_counts_and_unique_ids() -> None:
    players = load_players()
    assert len(players) == 32
    assert len({player.id for player in players}) == 32
    assert sum(player.is_captain for player in players) == 4
    assert sum(player.auction_eligible for player in players) == 28


def test_captains_are_preassigned_and_never_auctioned() -> None:
    expected = {
        "Samin Haque": "Blackout FC",
        "Sabit Khan": "Darkstar FC",
        "Arafatul Mamur": "Goli Underdogs",
        "Riyad Zaman": "Showstoppers",
    }
    captains = {player.full_name: player for player in load_players() if player.is_captain}
    assert set(captains) == set(expected)
    for name, team in expected.items():
        captain = captains[name]
        assert captain.assigned_team == team
        assert captain.auction_eligible is False
        assert captain.auction_status == PlayerAuctionStatus.PRE_ASSIGNED
        assert captain.auction_sequence is None
        assert captain.base_price is None
        assert captain.sold_price is None
        assert captain.sold_to is None


@pytest.mark.parametrize(
    ("name", "position"),
    [
        ("Nabil Shahriar", Position.GK),
        ("Masrur Rahman", Position.GK),
        ("Rayhan", Position.GK),
        ("Jawad", Position.GK),
        ("Mirza", Position.MID),
        ("Munem", Position.ATT),
        ("Sarim", Position.ATT),
    ],
)
def test_required_positions(name: str, position: Position) -> None:
    players = {player.full_name: player for player in load_players()}
    assert players[name].position == position


def test_rating_rules() -> None:
    players = load_players()
    by_name = {player.full_name: player for player in players}
    top_names = {
        "Rizvi Ibrahim", "Navid Rahman", "Rahmat Ullah", "Samin Haque",
        "Fairooz Abir", "Faiad Rehman", "Hasan Mahtab", "Minhaz Hamim",
        "Adeeb Ahmed", "Shahriar Anwar Khan",
    }
    assert by_name["Rizvi Ibrahim"].overall_rating == 90
    assert max(player.overall_rating for player in players) == 90
    assert all(85 <= by_name[name].overall_rating <= 90 for name in top_names)
    assert all(75 <= player.overall_rating <= 84 for player in players if player.full_name not in top_names)


def test_invalid_captain_is_rejected() -> None:
    with pytest.raises(ValueError, match="Captains cannot be auction eligible"):
        Player(
            id=99,
            full_name="Invalid Captain",
            short_name="Invalid",
            position=Position.MID,
            overall_rating=80,
            is_captain=True,
            assigned_team="Test FC",
            auction_eligible=True,
            auction_status=PlayerAuctionStatus.PRE_ASSIGNED,
        )


def test_player_round_trip_serialization() -> None:
    player = load_players()[10]
    assert Player.from_dict(player.to_dict()) == player


def test_non_captain_cannot_have_pre_assigned_status() -> None:
    with pytest.raises(ValueError, match="Only captains may have PRE_ASSIGNED status"):
        Player(
            id=99,
            full_name="Invalid Non-Captain",
            short_name="Invalid",
            position=Position.MID,
            overall_rating=80,
            is_captain=False,
            auction_eligible=True,
            auction_status=PlayerAuctionStatus.PRE_ASSIGNED,
        )
