from models.player import Player, Position
from models.team import Team
from services.player_service import load_players, load_teams
from services.team_service import build_team_summaries, build_team_summary, load_team_summaries

EXPECTED_CAPTAINS = {
    "Blackout FC": "Samin Haque",
    "Darkstar FC": "Sabit Khan",
    "Goli Underdogs": "Arafatul Mamur",
    "Showstoppers": "Riyad Zaman",
}


def test_exactly_4_teams_load() -> None:
    assert len(load_teams()) == 4


def test_official_team_names_are_correct() -> None:
    names = [team.name for team in load_teams()]
    assert names == ["Blackout FC", "Darkstar FC", "Goli Underdogs", "Showstoppers"]


def test_calgary_vipers_is_not_returned_as_an_auction_team() -> None:
    names = {team.name for team in load_teams()}
    assert "Calgary Vipers" not in names


def test_each_team_has_the_correct_captain() -> None:
    summaries = load_team_summaries()
    captains = {summary.team.name: summary.captain.full_name for summary in summaries}
    assert captains == EXPECTED_CAPTAINS


def test_each_initial_roster_contains_exactly_one_player() -> None:
    for summary in load_team_summaries():
        assert summary.squad_size == 1
        assert len(summary.roster_players) == 1


def test_initial_roster_player_is_the_captain() -> None:
    for summary in load_team_summaries():
        assert summary.roster_players[0].id == summary.team.captain_player_id
        assert summary.roster_players[0].is_captain is True


def test_each_team_starts_with_100m_budget() -> None:
    for summary in load_team_summaries():
        assert summary.team.starting_budget == 100
        assert summary.remaining_budget == 100


def test_each_team_starts_with_0m_spent() -> None:
    for summary in load_team_summaries():
        assert summary.total_spent == 0


def test_each_team_reports_7_remaining_slots() -> None:
    for summary in load_team_summaries():
        assert summary.remaining_slots == 7


def test_maximum_squad_size_is_8() -> None:
    for team in load_teams():
        assert team.max_squad_size == 8


def test_captain_player_ids_resolve_correctly() -> None:
    for summary in load_team_summaries():
        assert summary.captain is not None
        assert summary.captain.id == summary.team.captain_player_id


def test_roster_player_ids_resolve_to_player_objects() -> None:
    for summary in load_team_summaries():
        assert all(isinstance(player, Player) for player in summary.roster_players)
        assert [player.id for player in summary.roster_players] == summary.team.roster


def test_team_summary_helper_returns_correct_current_values_for_blackout_fc() -> None:
    summary = next(s for s in load_team_summaries() if s.team.name == "Blackout FC")

    assert summary.team.short_name == "Samin"
    assert summary.captain.full_name == "Samin Haque"
    assert summary.squad_size == 1
    assert summary.remaining_slots == 7
    assert summary.total_spent == 0
    assert summary.remaining_budget == 100
    assert summary.unresolved_roster_ids == []


def test_build_team_summaries_preserves_team_order() -> None:
    teams = load_teams()
    players = load_players()
    summaries = build_team_summaries(teams, players)
    assert [summary.team.id for summary in summaries] == [team.id for team in teams]


def test_build_team_summary_reports_unresolved_roster_and_captain_ids() -> None:
    ghost_team = Team(
        id=99,
        name="Ghost FC",
        short_name="Ghost",
        captain_player_id=9999,
        captain_name="Nobody",
        roster=[9999],
    )
    summary = build_team_summary(ghost_team, players_by_id={})

    assert summary.captain is None
    assert summary.roster_players == []
    assert summary.unresolved_roster_ids == [9999]
    # Derived stats must still work even with an unresolved roster.
    assert summary.squad_size == 1
    assert summary.remaining_slots == 7


def test_build_team_summary_supports_a_roster_larger_than_one() -> None:
    extra_player = Player(
        id=201,
        full_name="Extra Purchase",
        short_name="Extra",
        position=Position.MID,
        overall_rating=80,
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
    captain = next(player for player in load_players() if player.id == 1)
    players_by_id = {1: captain, 201: extra_player}

    summary = build_team_summary(team, players_by_id)

    assert summary.squad_size == 2
    assert summary.remaining_slots == 6
    assert summary.total_spent == 14
    assert summary.remaining_budget == 86
    assert [player.id for player in summary.roster_players] == [1, 201]
