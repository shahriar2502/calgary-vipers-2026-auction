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
    assert all(team.auction_spending == 0 for team in teams)
    assert all(team.players_purchased == 0 for team in teams)


def test_team_names_are_the_four_official_tournament_teams() -> None:
    teams = load_teams()
    assert [team.name for team in teams] == [
        "Blackout FC",
        "Darkstar FC",
        "Goli Underdogs",
        "Showstoppers",
    ]


def test_purchase_updates_budget_roster_and_totals() -> None:
    team = load_teams()[0]
    team.add_purchased_player(player_id=2, price=14, purchasing_gk=False, team_has_gk=True)
    assert team.remaining_budget == 86
    assert team.auction_spending == 14
    assert team.players_purchased == 1
    assert team.roster == [1, 2]


def test_team_cannot_overspend() -> None:
    team = load_teams()[0]
    with pytest.raises(ValueError):
        team.add_purchased_player(player_id=2, price=101, purchasing_gk=False, team_has_gk=True)
    assert team.remaining_budget == 100


def test_team_cannot_exceed_eight_players() -> None:
    team = load_teams()[0]
    # First Auction Rules V2: OUTFIELD_BASE_PRICE (2M) is the minimum legal
    # price for a non-GK purchase — team_has_gk=True throughout keeps the
    # dynamic completion reserve at a simple 2M-per-remaining-slot formula,
    # which this exact price satisfies at every step (see models/team.py's
    # maximum_legal_bid formula).
    for player_id in range(2, 9):
        team.add_purchased_player(player_id, 2, purchasing_gk=False, team_has_gk=True)
    assert team.roster_size == 8
    with pytest.raises(ValueError):
        team.add_purchased_player(9, 2, purchasing_gk=False, team_has_gk=True)


def test_team_cannot_add_same_player_twice() -> None:
    team = load_teams()[0]
    team.add_purchased_player(2, 10, purchasing_gk=False, team_has_gk=True)
    with pytest.raises(ValueError):
        team.add_purchased_player(2, 10, purchasing_gk=False, team_has_gk=True)


def test_negative_purchase_price_is_rejected() -> None:
    team = load_teams()[0]
    with pytest.raises(ValueError):
        team.add_purchased_player(2, -1, purchasing_gk=False, team_has_gk=True)


def test_second_gk_purchase_is_always_rejected() -> None:
    """Exactly one GK per team is mandatory and never negotiable, even
    when the budget/reserve math alone would otherwise allow it."""
    team = load_teams()[0]
    with pytest.raises(ValueError):
        team.add_purchased_player(2, 4, purchasing_gk=True, team_has_gk=True)
    assert team.remaining_budget == 100
