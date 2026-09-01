import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_json(name: str):
    return json.loads((ROOT / "data" / name).read_text(encoding="utf-8"))


def test_official_branding_is_configurable_and_logo_exists() -> None:
    settings = load_json("settings.json")
    branding = settings["branding"]

    assert branding == {
        "app_name": "Football Auction Manager",
        "branding_name": "Calgary Vipers",
        "header_title": "Calgary Vipers Auction 2026",
        "logo_path": "assets/branding/calgary_vipers_logo.png",
        "theme_name": "vipers_dark",
    }
    assert (ROOT / branding["logo_path"]).is_file()


def test_calgary_vipers_is_not_an_auction_team() -> None:
    teams = load_json("teams.json")
    assert [team["name"] for team in teams] == [
        "Blackout FC",
        "Darkstar FC",
        "Goli Underdogs",
        "Showstoppers",
    ]
    assert all(team["name"] != "Calgary Vipers" for team in teams)


def test_settings_counts_match_canonical_player_and_team_data() -> None:
    settings = load_json("settings.json")
    players = load_json("players.json")
    teams = load_json("teams.json")

    assert len(players) == settings["total_players"]
    assert len(teams) == settings["team_count"]
    assert sum(player["is_captain"] for player in players) == settings["captain_count"]
    assert sum(player["auction_eligible"] for player in players) == settings["auction_player_count"]
    assert all(team["starting_budget"] == settings["starting_budget_millions"] for team in teams)
    assert all(team["max_squad_size"] == settings["max_squad_size"] for team in teams)


def test_team_captain_links_match_player_records() -> None:
    players = {player["id"]: player for player in load_json("players.json")}
    teams = load_json("teams.json")

    for team in teams:
        captain = players[team["captain_player_id"]]
        assert captain["is_captain"] is True
        assert captain["full_name"] == team["captain_name"]
        assert captain["assigned_team"] == team["name"]
