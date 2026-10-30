"""tests/test_second_auction_final_team_view.py — services/current_
roster_service.py and the Teams screen's phase-aware "current/final
squads" behavior (Second Auction — Milestone 3, Part B). Numbered to
match the ticket's own "TESTING — FINAL TEAM VIEW" list (62-77).
"""
from __future__ import annotations

import pytest

from models.auction import AuctionStatus
from models.player import OUTFIELD_BASE_PRICE, Position, player_base_price
from services import current_roster_service as crs
from services import persistence_service as ps
from services.auction_service import team_can_bid_for_player
from services.auction_session_service import AuctionSession, SessionMode
from services.second_auction_bidding_service import team_can_bid_for_player as sa_team_can_bid_for_player
from services.second_auction_service import is_locked_player


@pytest.fixture
def isolated_saves(tmp_path, monkeypatch):
    mocks_dir = tmp_path / "mocks"
    live_dir = tmp_path / "live"
    live_active = live_dir / "live_active.json"
    monkeypatch.setattr(ps, "SAVES_DIR", tmp_path)
    monkeypatch.setattr(ps, "MOCKS_DIR", mocks_dir)
    monkeypatch.setattr(ps, "LIVE_DIR", live_dir)
    monkeypatch.setattr(ps, "LIVE_ACTIVE_PATH", live_active)
    return tmp_path


def team_by_name(teams, name: str):
    return next(team for team in teams if team.name == name)


def pick_eligible_team(player, teams, players):
    eligible = [team for team in teams if team_can_bid_for_player(team, player, players)]
    if not eligible:
        return None
    return min(eligible, key=lambda team: team.roster_size)


def non_locked_roster_ids(team, players_by_id) -> list[int]:
    return [pid for pid in team.roster if pid in players_by_id and not is_locked_player(players_by_id[pid])]


def completed_session(seed: int = 1, mode: SessionMode = SessionMode.MOCK) -> AuctionSession:
    session = AuctionSession()
    session.autosave = ps.autosave_hook
    session.start(seed=seed, mode=mode)
    guard = 0
    while session.auction.status not in (AuctionStatus.COMPLETE, AuctionStatus.BLOCKED) and guard < 300:
        guard += 1
        current = session.current_player
        team = pick_eligible_team(current, session.teams, session.players)
        if team is None:
            session.mark_current_player_unsold()
        else:
            session.sell_current_player(winning_team=team.id, sale_price=player_base_price(current))
    assert session.auction.status == AuctionStatus.COMPLETE
    return session


def confirm_full_release_plan(session: AuctionSession) -> None:
    players_by_id = {player.id: player for player in session.players}
    for team in session.teams:
        for player_id in non_locked_roster_ids(team, players_by_id)[:4]:
            session.toggle_second_auction_release(team.id, player_id)
    session.confirm_second_auction_release_plan()


def run_to_completion(session: AuctionSession) -> None:
    state = session.second_auction_state
    players_by_id = {player.id: player for player in session.players}
    guard = 0
    while state.status.value == "IN_PROGRESS" and guard < 200:
        guard += 1
        current_player = players_by_id[state.current_player_id]
        eligible = [team for team in session.teams if sa_team_can_bid_for_player(state, team, current_player)]
        if not eligible:
            session.mark_second_auction_player_unsold()
            continue
        team = min(eligible, key=lambda team: len(state.active_rosters_by_team[team.id]))
        session.sell_second_auction_player(winning_team=team.id, sale_price=player_base_price(current_player))


# ============================================================
# 62-64. Teams uses the correct source per phase
# ============================================================


def test_62_before_second_auction_teams_uses_first_auction_roster(isolated_saves) -> None:
    session = completed_session()
    team = session.teams[0]
    assert crs.get_current_roster_for_team(session, team.id) == team.roster


def test_63_during_second_auction_teams_uses_active_current_roster(isolated_saves) -> None:
    session = completed_session()
    confirm_full_release_plan(session)
    session.start_second_auction()
    state = session.second_auction_state
    team = session.teams[0]
    assert crs.get_current_roster_for_team(session, team.id) == state.active_rosters_by_team[team.id]
    assert crs.get_current_roster_for_team(session, team.id) != team.roster


def test_64_after_second_auction_teams_uses_final_current_roster(isolated_saves) -> None:
    session = completed_session()
    confirm_full_release_plan(session)
    session.start_second_auction()
    run_to_completion(session)
    state = session.second_auction_state
    team = session.teams[0]
    assert crs.get_current_roster_for_team(session, team.id) == state.active_rosters_by_team[team.id]
    assert len(crs.get_current_roster_for_team(session, team.id)) == 8


# ============================================================
# 65-66. released/purchased players move correctly
# ============================================================


def test_65_released_player_removed_from_previous_teams_current_roster(isolated_saves) -> None:
    session = completed_session()
    confirm_full_release_plan(session)
    team = session.teams[0]
    released_id = session.second_auction_setup.selections_for(team.id)[0]
    session.start_second_auction()
    assert released_id not in crs.get_current_roster_for_team(session, team.id)
    assert released_id in team.roster  # historical roster untouched


def test_66_purchased_player_added_to_new_teams_current_roster(isolated_saves) -> None:
    session = completed_session()
    confirm_full_release_plan(session)
    session.start_second_auction()
    state = session.second_auction_state
    players_by_id = {player.id: player for player in session.players}
    current_player = players_by_id[state.current_player_id]
    team = next(t for t in session.teams if sa_team_can_bid_for_player(state, t, current_player))
    session.sell_second_auction_player(winning_team=team.id, sale_price=player_base_price(current_player))
    assert current_player.id in crs.get_current_roster_for_team(session, team.id)
    assert crs.get_current_team_for_player(session, current_player.id).id == team.id


# ============================================================
# 67. moved player's historical first-auction owner remains unchanged
# ============================================================


def test_67_moved_players_historical_first_auction_owner_unchanged(isolated_saves) -> None:
    session = completed_session()
    confirm_full_release_plan(session)
    team = session.teams[0]
    released_id = session.second_auction_setup.selections_for(team.id)[0]
    players_by_id = {player.id: player for player in session.players}
    original_sold_to = players_by_id[released_id].sold_to

    session.start_second_auction()
    run_to_completion(session)

    assert players_by_id[released_id].sold_to == original_sold_to
    assert released_id in team.roster  # Team.roster (first-auction truth) never changes


# ============================================================
# 68-70. final teams show correct 8/8, captain, GK
# ============================================================


def test_68_all_final_teams_show_8_of_8(isolated_saves) -> None:
    session = completed_session()
    confirm_full_release_plan(session)
    session.start_second_auction()
    run_to_completion(session)
    for team in session.teams:
        assert len(crs.get_current_roster_for_team(session, team.id)) == 8


def test_69_all_final_teams_show_correct_captain(isolated_saves) -> None:
    session = completed_session()
    confirm_full_release_plan(session)
    session.start_second_auction()
    run_to_completion(session)
    for team in session.teams:
        assert team.captain_player_id in crs.get_current_roster_for_team(session, team.id)


def test_70_all_final_teams_show_exactly_one_gk(isolated_saves) -> None:
    session = completed_session()
    confirm_full_release_plan(session)
    session.start_second_auction()
    run_to_completion(session)
    players_by_id = {player.id: player for player in session.players}
    for team in session.teams:
        roster = crs.get_current_roster_for_team(session, team.id)
        gks = [pid for pid in roster if players_by_id[pid].position == Position.GK]
        assert len(gks) == 1


# ============================================================
# 71-73. budget displays correct
# ============================================================


def test_71_second_auction_starting_budget_displayed_correctly(isolated_saves) -> None:
    session = completed_session()
    confirm_full_release_plan(session)
    session.start_second_auction()
    state = session.second_auction_state
    team = session.teams[0]
    assert crs.get_current_starting_budget(session, team.id) == state.starting_budgets_by_team[team.id]


def test_72_second_auction_spent_calculated_correctly(isolated_saves) -> None:
    session = completed_session()
    confirm_full_release_plan(session)
    session.start_second_auction()
    team = session.teams[0]
    before = crs.get_second_auction_spent(session, team.id)
    assert before == 0
    session.sell_second_auction_player(winning_team=team.id, sale_price=OUTFIELD_BASE_PRICE + 1)
    assert crs.get_second_auction_spent(session, team.id) == OUTFIELD_BASE_PRICE + 1


def test_73_final_remaining_budget_displayed_correctly(isolated_saves) -> None:
    session = completed_session()
    confirm_full_release_plan(session)
    session.start_second_auction()
    run_to_completion(session)
    state = session.second_auction_state
    for team in session.teams:
        assert crs.get_current_team_budget(session, team.id) == state.remaining_budgets_by_team[team.id]


# ============================================================
# 74. first-auction budget/history remains unchanged
# ============================================================


def test_74_first_auction_budget_history_remains_unchanged(isolated_saves) -> None:
    session = completed_session()
    budgets_before = {team.id: team.remaining_budget for team in session.teams}
    history_before = list(session.auction.history)
    confirm_full_release_plan(session)
    session.start_second_auction()
    run_to_completion(session)
    for team in session.teams:
        assert team.remaining_budget == budgets_before[team.id]
    assert session.auction.history == history_before


# ============================================================
# 75-76. current/final Teams view survives app restart
# ============================================================


def test_75_current_roster_survives_app_restart(isolated_saves) -> None:
    session = completed_session()
    confirm_full_release_plan(session)
    session.start_second_auction()
    state = session.second_auction_state
    team = session.teams[0]
    rosters_before = {t.id: list(state.active_rosters_by_team[t.id]) for t in session.teams}

    restored = ps.load_session(ps.session_save_path(session.mode, session.session_id))
    for t in restored.teams:
        assert crs.get_current_roster_for_team(restored, t.id) == rosters_before[t.id]


def test_76_final_teams_view_survives_app_restart(isolated_saves) -> None:
    session = completed_session()
    confirm_full_release_plan(session)
    session.start_second_auction()
    run_to_completion(session)
    state = session.second_auction_state
    rosters_before = {t.id: list(state.active_rosters_by_team[t.id]) for t in session.teams}
    budgets_before = {t.id: state.remaining_budgets_by_team[t.id] for t in session.teams}

    restored = ps.load_session(ps.session_save_path(session.mode, session.session_id))
    assert restored.second_auction_state.status.value == "COMPLETE"
    for t in restored.teams:
        assert crs.get_current_roster_for_team(restored, t.id) == rosters_before[t.id]
        assert crs.get_current_team_budget(restored, t.id) == budgets_before[t.id]


# ============================================================
# 77. optional transfer badge derived correctly
# ============================================================


def test_77_transfer_badge_derived_correctly(isolated_saves) -> None:
    session = completed_session()
    confirm_full_release_plan(session)
    session.start_second_auction()
    state = session.second_auction_state
    players_by_id = {player.id: player for player in session.players}
    current_player = players_by_id[state.current_player_id]
    team = next(t for t in session.teams if sa_team_can_bid_for_player(state, t, current_player))

    assert crs.is_player_new_transfer(session, current_player.id) is False
    session.sell_second_auction_player(winning_team=team.id, sale_price=player_base_price(current_player))
    assert crs.is_player_new_transfer(session, current_player.id) is True

    # Never derived for a retained player (never released/purchased).
    retained_id = next(pid for pid in state.active_rosters_by_team[team.id] if pid != current_player.id)
    assert crs.is_player_new_transfer(session, retained_id) is False
