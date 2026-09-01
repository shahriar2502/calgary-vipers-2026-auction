import json
from pathlib import Path

import pytest

from models.team import Team


ROOT = Path(__file__).resolve().parents[1]


def load_teams() -> list[Team]:
    data = json.loads((ROOT / "data" / "teams.json").read_text(encoding="utf-8"))
    return [Team.from_dict(item) for item in data]


def test_initial_team_configuration() -> None:
    teams = load_teams()
    assert len(teams) == 4
    assert all(team.starting_budget == 100 for team in teams)
    assert all(team.remaining_budget == 100 for team in teams)
    assert all(team.roster_size == 1 for team in teams)
    assert all(team.roster == [team.captain_player_id] for team in teams)
    assert all(team.max_squad_size == 8 for team in teams)


def test_purchase_updates_budget_roster_and_totals() -> None:
    team = load_teams()[0]
    team.add_purchased_player(player_id=2, price=14)
    assert team.remaining_budget == 86
    assert team.auction_spending == 14
    assert team.players_purchased == 1
    assert team.roster == [1, 2]


def test_team_cannot_overspend() -> None:
    team = load_teams()[0]
    with pytest.raises(ValueError):
        team.add_purchased_player(player_id=2, price=101)
    assert team.remaining_budget == 100


def test_team_cannot_exceed_eight_players() -> None:
    team = load_teams()[0]
    for player_id in range(2, 9):
        team.add_purchased_player(player_id, 1)
    assert team.roster_size == 8
    with pytest.raises(ValueError):
        team.add_purchased_player(9, 1)


def test_team_cannot_add_same_player_twice() -> None:
    team = load_teams()[0]
    team.add_purchased_player(2, 10)
    with pytest.raises(ValueError):
        team.add_purchased_player(2, 10)


def test_negative_purchase_price_is_rejected() -> None:
    team = load_teams()[0]
    with pytest.raises(ValueError):
        team.add_purchased_player(2, -1)
