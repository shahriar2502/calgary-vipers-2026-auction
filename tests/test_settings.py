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
