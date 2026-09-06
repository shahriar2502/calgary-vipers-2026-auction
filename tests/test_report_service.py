"""Tests for services/report_service.py: pure analytics over an
AuctionSession's players/teams/history snapshot. No GUI involved.
"""

import copy

import pytest

from models.auction import AuctionHistoryEntry
from models.player import Player, PlayerAuctionStatus, Position
from models.team import Team
from services import report_service as rs
from services.auction_service import process_sale, process_unsold, team_can_bid_for_player
from services.auction_session_service import AuctionSession
from services.player_service import load_players, load_teams
from services.randomization_service import create_auction


def team_by_name(teams, name: str):
    return next(team for team in teams if team.name == name)


def pick_eligible_team(player, teams, players):
    eligible = [team for team in teams if team_can_bid_for_player(team, player, players)]
    if not eligible:
        return None
    return min(eligible, key=lambda team: team.roster_size)


@pytest.fixture
def session() -> AuctionSession:
    s = AuctionSession()
    s.start(seed=1)
    return s


def sell_or_unsold(session: AuctionSession, price: int = 5):
    player = session.current_player
    team = pick_eligible_team(player, session.teams, session.players)
    if team is None:
        return session.mark_current_player_unsold()
    return session.sell_current_player(winning_team=team.id, sale_price=price)


def force_one_non_gk_unsold_then_resolve_round_one(session: AuctionSession) -> int:
    forced_id = None
    auction = session.auction
    while auction.round_number == 1 and not session.is_complete and not session.is_blocked:
        current = session.current_player
        if forced_id is None and current.position != Position.GK:
            forced_id = current.id
            session.mark_current_player_unsold()
            continue
        sell_or_unsold(session)
    assert forced_id is not None
    return forced_id


# ============================================================
# PRICE STATISTICS
# ============================================================


def _sold(sequence, player_id, name, team, price, position="MID", rating=80):
    return AuctionHistoryEntry(
        auction_sequence=sequence,
        player_id=player_id,
        player_name=name,
        position=position,
        overall_rating=rating,
        base_price=None,
        status="SOLD",
        team=team,
        sold_price=price,
        round_number=1,
    )


def _unsold(sequence, player_id, name, position="DEF", rating=80):
    return AuctionHistoryEntry(
        auction_sequence=sequence,
        player_id=player_id,
        player_name=name,
        position=position,
        overall_rating=rating,
        base_price=None,
        status="UNSOLD",
        round_number=1,
    )


def test_price_statistics_of_empty_history() -> None:
    stats = rs.calculate_price_statistics([])
    assert stats.sold_count == 0
    assert stats.total_spent == 0
    assert stats.average_price is None
    assert stats.median_price is None
    assert stats.highest_price is None
    assert stats.lowest_price is None


def test_price_statistics_ignores_unsold_entries() -> None:
    history = [_unsold(1, 1, "A"), _sold(2, 2, "B", "Blackout FC", 10), _unsold(3, 3, "C")]
    stats = rs.calculate_price_statistics(history)
    assert stats.sold_count == 1
    assert stats.total_spent == 10
    assert stats.average_price == 10.0


def test_average_sale_price_correct() -> None:
    history = [_sold(1, 1, "A", "Blackout FC", 10), _sold(2, 2, "B", "Darkstar FC", 20), _sold(3, 3, "C", "Showstoppers", 30)]
    stats = rs.calculate_price_statistics(history)
    assert stats.average_price == 20.0


def test_median_sale_price_correct_odd_count() -> None:
    history = [_sold(1, 1, "A", "T", 5), _sold(2, 2, "B", "T", 20), _sold(3, 3, "C", "T", 10)]
    stats = rs.calculate_price_statistics(history)
    assert stats.median_price == 10.0


def test_median_sale_price_correct_even_count() -> None:
    history = [_sold(1, 1, "A", "T", 5), _sold(2, 2, "B", "T", 20), _sold(3, 3, "C", "T", 10), _sold(4, 4, "D", "T", 15)]
    stats = rs.calculate_price_statistics(history)
    assert stats.median_price == 12.5


def test_highest_sale_correct() -> None:
    history = [_sold(1, 1, "A", "T1", 10), _sold(2, 2, "B", "T2", 25)]
    stats = rs.calculate_price_statistics(history)
    assert stats.highest_price == 25
    assert stats.highest_price_entries[0].player_name == "B"


def test_lowest_sale_correct() -> None:
    history = [_sold(1, 1, "A", "T1", 10), _sold(2, 2, "B", "T2", 25)]
    stats = rs.calculate_price_statistics(history)
    assert stats.lowest_price == 10
    assert stats.lowest_price_entries[0].player_name == "A"


def test_tied_highest_sale_returns_all_tied_entries() -> None:
    history = [_sold(1, 1, "A", "T1", 15), _sold(2, 2, "B", "T2", 15), _sold(3, 3, "C", "T3", 5)]
    stats = rs.calculate_price_statistics(history)
    assert stats.highest_price == 15
    names = {entry.player_name for entry in stats.highest_price_entries}
    assert names == {"A", "B"}


def test_lowest_sale_can_be_the_minimum_legal_price() -> None:
    history = [_sold(1, 1, "A", "T1", 1)]
    stats = rs.calculate_price_statistics(history)
    assert stats.lowest_price == 1


def test_total_attempts_and_unsold_attempts_correct() -> None:
    history = [_sold(1, 1, "A", "T", 5), _unsold(2, 2, "B"), _unsold(3, 3, "C")]
    assert len(history) == 3
    unsold_count = sum(1 for e in history if e.status == "UNSOLD")
    assert unsold_count == 2


# ============================================================
# RE-AUCTION STATISTICS
# ============================================================


def test_reauction_statistics_no_activity() -> None:
    history = [_sold(1, 1, "A", "T", 5)]
    stats = rs.calculate_reauction_statistics(history, rounds_reached=1)
    assert stats.has_activity is False
    assert stats.unsold_attempts == 0
    assert stats.unique_unsold_players == 0
    assert stats.eventually_sold_after_unsold == 0
    assert stats.max_attempts_for_one_player == 1


def test_unique_unsold_players_correct() -> None:
    history = [_unsold(1, 1, "A"), _unsold(2, 1, "A"), _unsold(3, 2, "B")]
    stats = rs.calculate_reauction_statistics(history, rounds_reached=2)
    assert stats.unique_unsold_players == 2
    assert stats.unsold_attempts == 3


def test_eventually_sold_after_unsold_correct() -> None:
    history = [_unsold(1, 1, "A"), _sold(2, 1, "A", "T", 5), _unsold(3, 2, "B")]
    stats = rs.calculate_reauction_statistics(history, rounds_reached=2)
    assert stats.eventually_sold_after_unsold == 1
    assert stats.unique_unsold_players == 2


def test_max_attempts_for_one_player_correct() -> None:
    history = [_unsold(1, 1, "A"), _unsold(2, 1, "A"), _sold(3, 1, "A", "T", 5)]
    stats = rs.calculate_reauction_statistics(history, rounds_reached=3)
    assert stats.max_attempts_for_one_player == 3


def test_rounds_reached_passed_through() -> None:
    stats = rs.calculate_reauction_statistics([], rounds_reached=4)
    assert stats.rounds_reached == 4


def test_repeated_unsold_attempts_do_not_count_as_purchases() -> None:
    history = [_unsold(1, 1, "A"), _unsold(2, 1, "A"), _unsold(3, 1, "A")]
    price_stats = rs.calculate_price_statistics(history)
    assert price_stats.sold_count == 0
    assert price_stats.total_spent == 0


def test_eventual_sold_counted_once_even_with_multiple_prior_unsold() -> None:
    history = [_unsold(1, 1, "A"), _unsold(2, 1, "A"), _sold(3, 1, "A", "T", 9)]
    price_stats = rs.calculate_price_statistics(history)
    assert price_stats.sold_count == 1
    assert price_stats.total_spent == 9


# ============================================================
# FPL STATISTICS
# ============================================================


def _player(id, name, position=Position.MID, rating=80, fpl=None, captain=False, team=None, eligible=True):
    return Player(
        id=id,
        full_name=name,
        short_name=name[:4],
        position=position,
        overall_rating=rating,
        last_season_fpl_points=fpl,
        is_captain=captain,
        assigned_team=team,
        auction_eligible=eligible and not captain,
        auction_status=PlayerAuctionStatus.PRE_ASSIGNED if captain else PlayerAuctionStatus.AVAILABLE,
    )


def test_fpl_none_excluded_from_averages() -> None:
    history = [_sold(1, 1, "A", "T", 5), _sold(2, 2, "B", "T", 8)]
    players_by_id = {1: _player(1, "A", fpl=50), 2: _player(2, "B", fpl=None)}
    stats = rs.calculate_fpl_statistics(history, players_by_id)
    assert stats.average_fpl_of_known == 50.0
    assert stats.na_count == 1
    assert stats.known_count == 1


def test_highest_fpl_sold_player_correct() -> None:
    history = [_sold(1, 1, "A", "T", 5), _sold(2, 2, "B", "T", 8)]
    players_by_id = {1: _player(1, "A", fpl=50), 2: _player(2, "B", fpl=90)}
    stats = rs.calculate_fpl_statistics(history, players_by_id)
    assert stats.highest_fpl_value == 90
    assert stats.highest_fpl_entries[0].player_name == "B"


def test_average_fpl_of_known_sold_players_correct() -> None:
    history = [_sold(1, 1, "A", "T", 5), _sold(2, 2, "B", "T", 8), _sold(3, 3, "C", "T", 3)]
    players_by_id = {1: _player(1, "A", fpl=10), 2: _player(2, "B", fpl=20), 3: _player(3, "C", fpl=None)}
    stats = rs.calculate_fpl_statistics(history, players_by_id)
    assert stats.average_fpl_of_known == 15.0
    assert stats.na_count == 1


def test_fpl_statistics_with_no_sold_entries() -> None:
    stats = rs.calculate_fpl_statistics([], {})
    assert stats.highest_fpl_value is None
    assert stats.average_fpl_of_known is None
    assert stats.na_count == 0


def test_fpl_none_never_treated_as_zero() -> None:
    """A single sold player with FPL=None must yield average None, not 0."""
    history = [_sold(1, 1, "A", "T", 5)]
    players_by_id = {1: _player(1, "A", fpl=None)}
    stats = rs.calculate_fpl_statistics(history, players_by_id)
    assert stats.average_fpl_of_known is None
    assert stats.na_count == 1


# ============================================================
# OVR STATISTICS
# ============================================================


def test_average_ovr_sold_players_correct() -> None:
    history = [_sold(1, 1, "A", "T", 5, rating=80), _sold(2, 2, "B", "T", 8, rating=90)]
    stats = rs.calculate_ovr_statistics(history)
    assert stats.average_ovr_sold == 85.0


def test_highest_ovr_sold_correct_and_ties_handled() -> None:
    history = [_sold(1, 1, "A", "T1", 5, rating=90), _sold(2, 2, "B", "T2", 8, rating=90), _sold(3, 3, "C", "T3", 3, rating=80)]
    stats = rs.calculate_ovr_statistics(history)
    assert stats.highest_ovr_value == 90
    assert {e.player_name for e in stats.highest_ovr_entries} == {"A", "B"}


def test_ovr_statistics_with_no_sold_entries() -> None:
    stats = rs.calculate_ovr_statistics([])
    assert stats.average_ovr_sold is None
    assert stats.highest_ovr_value is None


# ============================================================
# TEAM REPORT
# ============================================================


def _team(id, name, roster, spending=0, budget=100, purchased=0):
    return Team(
        id=id,
        name=name,
        short_name=name[:4],
        captain_player_id=roster[0],
        captain_name="Cap",
        starting_budget=100,
        remaining_budget=budget,
        roster=roster,
        auction_spending=spending,
        players_purchased=purchased,
    )


def test_team_report_no_purchases_shows_none_not_zero() -> None:
    captain = _player(1, "Cap", captain=True, team="Blackout FC")
    team = _team(1, "Blackout FC", [1])
    report = rs.build_team_report(team, {1: captain})
    assert report.average_purchase_price is None
    assert report.highest_purchase_player is None
    assert report.lowest_purchase_player is None
    assert report.purchases_count == 0


def test_team_report_average_purchase_price_correct() -> None:
    captain = _player(1, "Cap", captain=True, team="Blackout FC")
    p2 = _player(2, "P2", rating=80)
    p2.auction_status = PlayerAuctionStatus.SOLD
    p2.sold_price = 10
    p2.sold_to = "Blackout FC"
    p3 = _player(3, "P3", rating=80)
    p3.auction_status = PlayerAuctionStatus.SOLD
    p3.sold_price = 20
    p3.sold_to = "Blackout FC"
    team = _team(1, "Blackout FC", [1, 2, 3], spending=30, budget=70, purchased=2)
    report = rs.build_team_report(team, {1: captain, 2: p2, 3: p3})
    assert report.average_purchase_price == 15.0
    assert report.purchases_count == 2


def test_team_report_highest_and_lowest_purchase_correct() -> None:
    captain = _player(1, "Cap", captain=True, team="Blackout FC")
    p2 = _player(2, "Cheap")
    p2.auction_status = PlayerAuctionStatus.SOLD
    p2.sold_price = 3
    p3 = _player(3, "Expensive")
    p3.auction_status = PlayerAuctionStatus.SOLD
    p3.sold_price = 40
    team = _team(1, "Blackout FC", [1, 2, 3], spending=43, budget=57, purchased=2)
    report = rs.build_team_report(team, {1: captain, 2: p2, 3: p3})
    assert report.highest_purchase_player.full_name == "Expensive"
    assert report.lowest_purchase_player.full_name == "Cheap"


def test_captain_excluded_from_purchase_price_stats() -> None:
    captain = _player(1, "Cap", captain=True, team="Blackout FC")
    team = _team(1, "Blackout FC", [1])
    report = rs.build_team_report(team, {1: captain})
    assert report.purchases_count == 0
    assert report.average_purchase_price is None


def test_captain_included_in_squad_size() -> None:
    captain = _player(1, "Cap", captain=True, team="Blackout FC")
    team = _team(1, "Blackout FC", [1])
    report = rs.build_team_report(team, {1: captain})
    assert report.squad_size == 1


def test_captain_never_shown_as_a_purchase_role() -> None:
    captain = _player(1, "Cap", captain=True, team="Blackout FC")
    team = _team(1, "Blackout FC", [1])
    report = rs.build_team_report(team, {1: captain})
    entry = report.roster[0]
    assert entry.role == rs.CAPTAIN_ROLE
    assert entry.price is None


def test_captain_excluded_from_purchase_ovr_but_included_in_squad_ovr() -> None:
    captain = _player(1, "Cap", captain=True, team="Blackout FC", rating=70)
    p2 = _player(2, "P2", rating=90)
    p2.auction_status = PlayerAuctionStatus.SOLD
    p2.sold_price = 10
    team = _team(1, "Blackout FC", [1, 2], spending=10, budget=90, purchased=1)
    report = rs.build_team_report(team, {1: captain, 2: p2})
    assert report.average_ovr_purchases == 90.0
    assert report.average_ovr_squad == 80.0  # (70 + 90) / 2


def test_captain_excluded_from_fpl_purchase_average_but_included_in_squad() -> None:
    captain = _player(1, "Cap", captain=True, team="Blackout FC", fpl=60)
    p2 = _player(2, "P2", fpl=20)
    p2.auction_status = PlayerAuctionStatus.SOLD
    p2.sold_price = 10
    team = _team(1, "Blackout FC", [1, 2], spending=10, budget=90, purchased=1)
    report = rs.build_team_report(team, {1: captain, 2: p2})
    assert report.average_fpl_purchases == 20.0
    assert report.average_fpl_squad == 40.0  # (60 + 20) / 2


def test_gk_status_true_when_roster_has_a_goalkeeper() -> None:
    captain = _player(1, "Cap", captain=True, team="Blackout FC", position=Position.MID)
    gk = _player(2, "GK1", position=Position.GK)
    gk.auction_status = PlayerAuctionStatus.SOLD
    gk.sold_price = 5
    team = _team(1, "Blackout FC", [1, 2], spending=5, budget=95, purchased=1)
    report = rs.build_team_report(team, {1: captain, 2: gk})
    assert report.has_goalkeeper is True


def test_gk_status_false_when_roster_has_no_goalkeeper() -> None:
    captain = _player(1, "Cap", captain=True, team="Blackout FC", position=Position.MID)
    team = _team(1, "Blackout FC", [1])
    report = rs.build_team_report(team, {1: captain})
    assert report.has_goalkeeper is False


# ============================================================
# FULL-SESSION INTEGRATION (real AuctionSession)
# ============================================================


def test_no_active_session_raises_a_clear_error() -> None:
    with pytest.raises(ValueError):
        rs.build_full_report(AuctionSession())


def test_zero_transaction_session_handled_without_crash(session) -> None:
    report = rs.build_full_report(session)
    assert report.summary.sold_count == 0
    assert report.summary.price_stats.average_price is None
    for team_report in report.team_reports:
        assert team_report.average_purchase_price is None


def test_total_sold_correct(session) -> None:
    blackout = team_by_name(session.teams, "Blackout FC")
    session.sell_current_player(winning_team=blackout.id, sale_price=10)
    report = rs.build_full_report(session)
    assert report.summary.sold_count == 1


def test_total_spent_correct_across_session(session) -> None:
    blackout = team_by_name(session.teams, "Blackout FC")
    darkstar = team_by_name(session.teams, "Darkstar FC")
    session.sell_current_player(winning_team=blackout.id, sale_price=10)
    session.sell_current_player(winning_team=darkstar.id, sale_price=15)
    report = rs.build_full_report(session)
    assert report.summary.price_stats.total_spent == 25


def test_each_team_spend_correct(session) -> None:
    blackout = team_by_name(session.teams, "Blackout FC")
    session.sell_current_player(winning_team=blackout.id, sale_price=12)
    report = rs.build_full_report(session)
    blackout_report = next(t for t in report.team_reports if t.team.name == "Blackout FC")
    assert blackout_report.total_spent == 12


def test_each_team_remaining_budget_correct(session) -> None:
    blackout = team_by_name(session.teams, "Blackout FC")
    session.sell_current_player(winning_team=blackout.id, sale_price=12)
    report = rs.build_full_report(session)
    blackout_report = next(t for t in report.team_reports if t.team.name == "Blackout FC")
    assert blackout_report.remaining_budget == 88


def test_each_team_roster_size_correct(session) -> None:
    blackout = team_by_name(session.teams, "Blackout FC")
    session.sell_current_player(winning_team=blackout.id, sale_price=12)
    report = rs.build_full_report(session)
    blackout_report = next(t for t in report.team_reports if t.team.name == "Blackout FC")
    assert blackout_report.squad_size == 2


def test_in_progress_report_works(session) -> None:
    session.sell_current_player(winning_team=session.teams[0].id, sale_price=5)
    report = rs.build_full_report(session)
    assert session.auction.status.value == "IN_PROGRESS"
    assert report.summary.sold_count == 1


def test_complete_report_works() -> None:
    session = AuctionSession()
    session.start(seed=1)
    guard = 0
    while not session.is_complete and not session.is_blocked and guard < 200:
        sell_or_unsold(session)
        guard += 1
    assert session.is_complete
    report = rs.build_full_report(session)
    assert report.summary.sold_count == 28
    assert report.summary.remaining_unsold_count == 0
    for team_report in report.team_reports:
        assert team_report.squad_size == 8


def test_blocked_report_works() -> None:
    session = AuctionSession()
    session.start(seed=7)  # deterministically reaches BLOCKED, see test_auction_history_screen.py
    guard = 0
    while session.auction.status.value != "BLOCKED" and guard < 200:
        assert not session.is_complete
        sell_or_unsold(session)
        guard += 1
    assert session.auction.status.value == "BLOCKED"
    report = rs.build_full_report(session)
    # Must not crash while BLOCKED, and must report exactly the sales that
    # actually happened before the auction became unwinnable.
    assert report.summary.sold_count == len(session.auction.history) - report.summary.reauction_stats.unsold_attempts
    assert report.summary.sold_count > 0
    assert report.summary.sold_count < 28


def test_resumed_session_produces_identical_report_data(tmp_path, monkeypatch) -> None:
    from services import persistence_service as ps
    from services.auction_session_service import SessionMode

    monkeypatch.setattr(ps, "SAVES_DIR", tmp_path)
    monkeypatch.setattr(ps, "MOCKS_DIR", tmp_path / "mocks")
    monkeypatch.setattr(ps, "LIVE_DIR", tmp_path / "live")
    monkeypatch.setattr(ps, "LIVE_ACTIVE_PATH", tmp_path / "live" / "live_active.json")

    original = AuctionSession()
    original.start(seed=1, mode=SessionMode.MOCK)
    blackout = team_by_name(original.teams, "Blackout FC")
    original.sell_current_player(winning_team=blackout.id, sale_price=9)
    original.mark_current_player_unsold()

    save_path = ps.save_session(original)
    restored = ps.load_session(save_path)

    report_original = rs.build_full_report(original)
    report_restored = rs.build_full_report(restored)

    assert report_original.summary.sold_count == report_restored.summary.sold_count
    assert report_original.summary.price_stats.total_spent == report_restored.summary.price_stats.total_spent
    assert [t.total_spent for t in report_original.team_reports] == [t.total_spent for t in report_restored.team_reports]


def test_report_calculations_do_not_mutate_session(session) -> None:
    blackout = team_by_name(session.teams, "Blackout FC")
    session.sell_current_player(winning_team=blackout.id, sale_price=10)
    session.mark_current_player_unsold()

    history_snapshot = copy.deepcopy(session.auction.history)
    teams_snapshot = copy.deepcopy(session.teams)
    players_snapshot = copy.deepcopy(session.players)

    rs.build_full_report(session)
    rs.build_full_report(session)

    assert session.auction.history == history_snapshot
    assert session.teams == teams_snapshot
    assert session.players == players_snapshot


def test_reauction_round_2_reflected_in_report() -> None:
    session = AuctionSession()
    session.start(seed=1)
    forced_id = force_one_non_gk_unsold_then_resolve_round_one(session)
    assert session.round_number == 2

    report = rs.build_full_report(session)
    assert report.summary.reauction_stats.has_activity is True
    assert report.summary.reauction_stats.unique_unsold_players >= 1
    assert report.summary.round_number == 2
