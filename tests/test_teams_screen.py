"""Lightweight, non-visual checks for the Milestone 6 Teams screen.

GUI creation is best-effort: skipped (not failed) when no display/Tk
backend is available. The `hidden_root` fixture is shared (see
tests/conftest.py) across all frame-level screen tests to avoid flaky
TclError teardown timing from repeatedly creating/destroying CTk() roots
in the same process.
"""

import pytest


@pytest.fixture
def screen(hidden_root):
    from ui.screens.teams_screen import TeamsScreen

    built = TeamsScreen(hidden_root)
    yield built
    built.destroy()


def test_teams_screen_loads_all_4_teams(screen) -> None:
    assert len(screen._summaries) == 4
    assert [summary.team.name for summary in screen._summaries] == [
        "Blackout FC",
        "Darkstar FC",
        "Goli Underdogs",
        "Showstoppers",
    ]


def test_teams_screen_each_team_shows_correct_captain(screen) -> None:
    captains = {summary.team.name: summary.captain.full_name for summary in screen._summaries}
    assert captains == {
        "Blackout FC": "Samin Haque",
        "Darkstar FC": "Sabit Khan",
        "Goli Underdogs": "Arafatul Mamur",
        "Showstoppers": "Riyad Zaman",
    }


def test_teams_screen_each_team_shows_initial_budget_and_squad_state(screen) -> None:
    for summary in screen._summaries:
        assert summary.remaining_budget == 100
        assert summary.total_spent == 0
        assert summary.squad_size == 1
        assert summary.remaining_slots == 7
        assert summary.team.max_squad_size == 8


def test_teams_screen_roster_supports_more_than_one_player(hidden_root, monkeypatch) -> None:
    from models.player import Player, PlayerAuctionStatus, Position
    from models.team import Team
    from services.team_service import TeamSummary

    extra_player = Player(
        id=201, full_name="Extra Purchase", short_name="Extra", position=Position.MID, overall_rating=80
    )
    captain = Player(
        id=1,
        full_name="Samin Haque",
        short_name="Samin",
        position=Position.MID,
        overall_rating=88,
        is_captain=True,
        assigned_team="Blackout FC",
        auction_eligible=False,
        auction_status=PlayerAuctionStatus.PRE_ASSIGNED,
    )
    team = Team(
        id=1,
        name="Blackout FC",
        short_name="Samin",
        captain_player_id=1,
        captain_name="Samin Haque",
        roster=[1, 201],
        auction_spending=14,
        remaining_budget=86,
        players_purchased=1,
    )
    fake_summary = TeamSummary(team=team, captain=captain, roster_players=[captain, extra_player])

    monkeypatch.setattr("ui.screens.teams_screen.load_team_summaries", lambda: [fake_summary])

    from ui.screens.teams_screen import TeamsScreen

    two_player_screen = TeamsScreen(hidden_root)
    try:
        assert two_player_screen._summaries[0].squad_size == 2
        assert two_player_screen._summaries[0].remaining_slots == 6
    finally:
        two_player_screen.destroy()


def test_teams_screen_handles_missing_data_file_without_crashing(hidden_root, monkeypatch) -> None:
    def _raise_missing_file(*_args, **_kwargs):
        raise FileNotFoundError("data/teams.json not found")

    monkeypatch.setattr("ui.screens.teams_screen.load_team_summaries", _raise_missing_file)

    from ui.screens.teams_screen import TeamsScreen

    broken_screen = TeamsScreen(hidden_root)
    try:
        assert not hasattr(broken_screen, "_summaries")
    finally:
        broken_screen.destroy()


def test_teams_screen_handles_unresolved_roster_player_id_without_crashing(hidden_root, monkeypatch) -> None:
    from models.team import Team
    from services.team_service import TeamSummary

    ghost_team = Team(id=1, name="Blackout FC", short_name="Samin", captain_player_id=1, captain_name="Samin Haque")
    broken_summary = TeamSummary(team=ghost_team, captain=None, roster_players=[], unresolved_roster_ids=[1])

    monkeypatch.setattr("ui.screens.teams_screen.load_team_summaries", lambda: [broken_summary])

    from ui.screens.teams_screen import TeamsScreen

    partial_screen = TeamsScreen(hidden_root)
    try:
        assert partial_screen._summaries[0].captain is None
        assert partial_screen._summaries[0].unresolved_roster_ids == [1]
    finally:
        partial_screen.destroy()
