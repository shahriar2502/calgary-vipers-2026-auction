"""Pre-Milestone-9 bug fix: minimum budget reserve for remaining roster slots.

A team must never spend so much on one purchase that filling its
remaining roster slots afterward becomes mathematically impossible at the
minimum legal price (1M per slot). See PROJECT_CONTEXT.md's "PRE-M9 BUDGET
RESERVE RULE" for the formula and worked examples this file's test names
mirror directly.
"""
import copy

import pytest

from models.auction import Auction, AuctionStatus
from models.player import Player, Position
from models.team import MINIMUM_LEGAL_PRICE, Team
from services.auction_service import (
    AuctionTransactionError,
    budget_reserve_violation_message,
    process_sale,
    process_unsold,
    team_can_bid_for_player,
)
from services.player_service import load_players, load_teams
from services.randomization_service import create_auction


def _team_with(roster_size: int, remaining_budget: int, starting_budget: int = 100, team_id: int = 1) -> Team:
    """A synthetic Blackout-FC-shaped team at a specific roster size and
    remaining budget, independent of how it supposedly got there (the
    exact prior spending history doesn't matter for these tests, only the
    current roster_size/remaining_budget snapshot)."""
    captain_id = 1
    # Filler roster ids for "already purchased" slots — offset well away
    # from the 900s used for this file's actual auction-eligible test
    # players, so a filler id can never collide with a real player id.
    extra_ids = [950 + i for i in range(roster_size - 1)]
    spending = starting_budget - remaining_budget
    return Team(
        id=team_id,
        name="Blackout FC",
        short_name="Samin",
        captain_player_id=captain_id,
        captain_name="Samin Haque",
        starting_budget=starting_budget,
        remaining_budget=remaining_budget,
        max_squad_size=8,
        roster=[captain_id] + extra_ids,
        auction_spending=spending,
        players_purchased=roster_size - 1,
    )


def _players(specs: list[tuple[int, str, Position]]) -> list[Player]:
    return [
        Player(id=player_id, full_name=name, short_name=name, position=position, overall_rating=80)
        for player_id, name, position in specs
    ]


def _single_player_setup(team: Team, player: Player, other_teams: list[Team] | None = None):
    teams = [team] + (other_teams or [])
    auction = Auction(queue=[player.id], status=AuctionStatus.IN_PROGRESS)
    return auction, [player], teams


# ============================================================
# FORMULA: maximum_legal_bid / can_afford_while_preserving_roster_budget
# ============================================================


def test_max_legal_bid_roster_5_of_8_budget_3m() -> None:
    team = _team_with(roster_size=5, remaining_budget=3)
    assert team.maximum_legal_bid == 1


def test_max_legal_bid_roster_6_of_8_budget_5m() -> None:
    team = _team_with(roster_size=6, remaining_budget=5)
    assert team.maximum_legal_bid == 4


def test_max_legal_bid_roster_7_of_8_budget_10m() -> None:
    team = _team_with(roster_size=7, remaining_budget=10)
    assert team.maximum_legal_bid == 10


def test_max_legal_bid_fresh_team_roster_1_of_8_budget_100m() -> None:
    team = _team_with(roster_size=1, remaining_budget=100)
    assert team.maximum_legal_bid == 94


# ============================================================
# CASE 1: roster 5/8, budget 3M
# ============================================================


def test_case1_1m_purchase_succeeds_at_roster_5_of_8_budget_3m() -> None:
    team = _team_with(roster_size=5, remaining_budget=3)
    player = _players([(901, "P1", Position.DEF)])[0]
    auction, players, teams = _single_player_setup(team, player)

    result = process_sale(auction, players, teams, winning_team=team.id, sale_price=1)
    assert result.outcome == "SOLD"
    assert team.remaining_budget == 2
    assert team.roster_size == 6


def test_case1_2m_purchase_rejected_at_roster_5_of_8_budget_3m() -> None:
    team = _team_with(roster_size=5, remaining_budget=3)
    player = _players([(901, "P1", Position.DEF)])[0]
    auction, players, teams = _single_player_setup(team, player)

    with pytest.raises(AuctionTransactionError):
        process_sale(auction, players, teams, winning_team=team.id, sale_price=2)


# ============================================================
# CASE 2: roster 6/8, budget 5M
# ============================================================


def test_case2_4m_purchase_succeeds_at_roster_6_of_8_budget_5m() -> None:
    team = _team_with(roster_size=6, remaining_budget=5)
    player = _players([(901, "P1", Position.DEF)])[0]
    auction, players, teams = _single_player_setup(team, player)

    result = process_sale(auction, players, teams, winning_team=team.id, sale_price=4)
    assert result.outcome == "SOLD"
    assert team.remaining_budget == 1


def test_case2_5m_purchase_rejected_at_roster_6_of_8_budget_5m() -> None:
    team = _team_with(roster_size=6, remaining_budget=5)
    player = _players([(901, "P1", Position.DEF)])[0]
    auction, players, teams = _single_player_setup(team, player)

    with pytest.raises(AuctionTransactionError):
        process_sale(auction, players, teams, winning_team=team.id, sale_price=5)


# ============================================================
# CASE 3: roster 7/8, budget 10M -> final player may take it all
# ============================================================


def test_case3_10m_purchase_succeeds_at_roster_7_of_8_budget_10m() -> None:
    team = _team_with(roster_size=7, remaining_budget=10)
    player = _players([(901, "P1", Position.DEF)])[0]
    auction, players, teams = _single_player_setup(team, player)

    result = process_sale(auction, players, teams, winning_team=team.id, sale_price=10)
    assert result.outcome == "SOLD"
    assert team.remaining_budget == 0
    assert team.roster_size == 8


# ============================================================
# CASE 4: fresh team, roster 1/8, budget 100M -> max first-purchase = 94M
# ============================================================


def test_case4_94m_first_purchase_succeeds() -> None:
    players = load_players()
    teams = load_teams()
    auction = create_auction(players, seed=1)
    blackout = next(t for t in teams if t.name == "Blackout FC")
    assert blackout.maximum_legal_bid == 94

    result = process_sale(auction, players, teams, winning_team="Blackout FC", sale_price=94)
    assert result.outcome == "SOLD"
    assert blackout.remaining_budget == 6


def test_case4_95m_first_purchase_rejected() -> None:
    players = load_players()
    teams = load_teams()
    auction = create_auction(players, seed=1)

    with pytest.raises(AuctionTransactionError):
        process_sale(auction, players, teams, winning_team="Blackout FC", sale_price=95)


# ============================================================
# CASE 5: a rejected purchase is fully atomic
# ============================================================


def test_case5_rejected_purchase_is_atomic() -> None:
    team = _team_with(roster_size=5, remaining_budget=3)
    player = _players([(901, "P1", Position.DEF)])[0]
    auction, players, teams = _single_player_setup(team, player)

    snapshot_team = copy.deepcopy(team)
    snapshot_players = copy.deepcopy(players)
    snapshot_position = auction.current_queue_position
    snapshot_current_player_id = auction.current_player_id

    with pytest.raises(AuctionTransactionError):
        process_sale(auction, players, teams, winning_team=team.id, sale_price=2)

    assert team == snapshot_team  # budget, roster, and spending unchanged
    assert players == snapshot_players
    assert auction.history == []
    assert auction.current_queue_position == snapshot_position
    assert auction.current_player_id == snapshot_current_player_id


# ============================================================
# CASE 6: exactly 1M works when 1M is the maximum legal price
# ============================================================


def test_case6_exact_1m_minimum_succeeds_when_it_is_also_the_maximum() -> None:
    # roster 7/8, budget 1M: slots_after_purchase = 0, so max legal = 1M.
    team = _team_with(roster_size=7, remaining_budget=1)
    assert team.maximum_legal_bid == 1
    player = _players([(901, "P1", Position.DEF)])[0]
    auction, players, teams = _single_player_setup(team, player)

    result = process_sale(auction, players, teams, winning_team=team.id, sale_price=1)
    assert result.outcome == "SOLD"
    assert team.remaining_budget == 0


# ============================================================
# CASE 7: price below 1M rejected
# ============================================================


def test_case7_zero_price_rejected() -> None:
    team = _team_with(roster_size=1, remaining_budget=100)
    player = _players([(901, "P1", Position.DEF)])[0]
    auction, players, teams = _single_player_setup(team, player)

    with pytest.raises(AuctionTransactionError):
        process_sale(auction, players, teams, winning_team=team.id, sale_price=0)


def test_case7_negative_price_rejected() -> None:
    team = _team_with(roster_size=1, remaining_budget=100)
    player = _players([(901, "P1", Position.DEF)])[0]
    auction, players, teams = _single_player_setup(team, player)

    with pytest.raises(AuctionTransactionError):
        process_sale(auction, players, teams, winning_team=team.id, sale_price=-1)


# ============================================================
# CASE 8: a full team remains unable to purchase
# ============================================================


def test_case8_full_team_cannot_purchase_regardless_of_budget() -> None:
    team = _team_with(roster_size=8, remaining_budget=50)
    player = _players([(901, "P1", Position.DEF)])[0]
    auction, players, teams = _single_player_setup(team, player)

    with pytest.raises(AuctionTransactionError, match="roster is full"):
        process_sale(auction, players, teams, winning_team=team.id, sale_price=1)


def test_case8_full_team_is_ineligible_per_team_can_bid_for_player() -> None:
    team = _team_with(roster_size=8, remaining_budget=50)
    player = _players([(901, "P1", Position.DEF)])[0]
    assert team_can_bid_for_player(team, player, [player]) is False


# ============================================================
# CASE 9: second-GK restriction still works alongside the budget rule
# ============================================================


def test_case9_second_gk_still_rejected_even_with_plenty_of_budget() -> None:
    gk_on_roster = Player(id=800, full_name="Existing GK", short_name="GK1", position=Position.GK, overall_rating=80)
    team = _team_with(roster_size=1, remaining_budget=100)
    team.roster.append(gk_on_roster.id)
    new_gk = Player(id=901, full_name="New GK", short_name="GK2", position=Position.GK, overall_rating=80)
    auction, _players_list, teams = _single_player_setup(team, new_gk)
    all_players = [gk_on_roster, new_gk]

    with pytest.raises(AuctionTransactionError, match="already has a goalkeeper"):
        process_sale(auction, all_players, teams, winning_team=team.id, sale_price=1)


def test_case9_gk_rule_checked_even_when_price_is_within_budget_reserve() -> None:
    # A price that easily satisfies the budget-reserve rule must still be
    # rejected on GK grounds — enough total budget never overrides the
    # one-GK-per-team rule.
    gk_on_roster = Player(id=800, full_name="Existing GK", short_name="GK1", position=Position.GK, overall_rating=80)
    team = _team_with(roster_size=5, remaining_budget=10)
    team.roster.append(gk_on_roster.id)
    new_gk = Player(id=901, full_name="New GK", short_name="GK2", position=Position.GK, overall_rating=80)
    assert team.maximum_legal_bid >= MINIMUM_LEGAL_PRICE  # budget rule alone would allow it
    assert team_can_bid_for_player(team, new_gk, [gk_on_roster, new_gk]) is False


# ============================================================
# CASE 10: blocked-state logic considers the budget reserve
# ============================================================


def test_case10_blocked_state_when_only_team_left_cannot_afford_minimum() -> None:
    """One team remains with roster space, but spending even 1M on the
    last remaining player would leave it unable to fill its other
    required slots -> no legal destination anywhere -> BLOCKED."""
    # roster 5/8, budget 2M: needs 3 more players; even a 1M purchase
    # would leave 1M for the other 2 required slots -> illegal.
    starving_team = _team_with(roster_size=5, remaining_budget=2, team_id=1)
    other_teams = [
        _team_with(roster_size=8, remaining_budget=0, team_id=2),
        _team_with(roster_size=8, remaining_budget=0, team_id=3),
        _team_with(roster_size=8, remaining_budget=0, team_id=4),
    ]
    for team in other_teams:
        team.name = f"Team {team.id}"

    player = _players([(901, "P1", Position.DEF)])[0]
    auction, players, teams = _single_player_setup(starving_team, player, other_teams)

    result = process_unsold(auction, players, teams)
    assert result.auction_status == AuctionStatus.BLOCKED
    assert auction.status == AuctionStatus.BLOCKED


def test_case10_starving_team_is_ineligible_before_reaching_blocked_state() -> None:
    team = _team_with(roster_size=5, remaining_budget=2)
    player = _players([(901, "P1", Position.DEF)])[0]
    assert team_can_bid_for_player(team, player, [player]) is False


# ============================================================
# CASE 11: a successful purchase recalculates the new reserve correctly
# ============================================================


def test_case11_successful_purchase_recalculates_reserve() -> None:
    team = _team_with(roster_size=5, remaining_budget=3)
    assert team.maximum_legal_bid == 1

    player = _players([(901, "P1", Position.DEF)])[0]
    auction, players, teams = _single_player_setup(team, player)
    process_sale(auction, players, teams, winning_team=team.id, sale_price=1)

    assert team.roster_size == 6
    assert team.remaining_budget == 2
    # 2 roster slots remaining -> reserve 1M for the slot after this one.
    assert team.maximum_legal_bid == 1


# ============================================================
# CASE 12: reserve becomes 0M after filling the final roster slot
# ============================================================


def test_case12_reserve_is_0m_after_filling_the_final_roster_slot() -> None:
    team = _team_with(roster_size=7, remaining_budget=10)
    player = _players([(901, "P1", Position.DEF)])[0]
    auction, players, teams = _single_player_setup(team, player)

    process_sale(auction, players, teams, winning_team=team.id, sale_price=10)

    assert team.roster_size == 8
    assert team.remaining_budget == 0
    assert team.remaining_required_purchases == 0
    assert team.maximum_legal_bid == 0


# ============================================================
# Manual-verification scenario, exactly as specified in the ticket
# ============================================================


def test_manual_scenario_three_slots_three_million_walkthrough() -> None:
    team = _team_with(roster_size=5, remaining_budget=3)
    players = _players([(901, "P1", Position.DEF), (902, "P2", Position.DEF), (903, "P3", Position.DEF)])
    auction = Auction(queue=[901, 902, 903], status=AuctionStatus.IN_PROGRESS)
    teams = [team]

    with pytest.raises(AuctionTransactionError):
        process_sale(auction, players, teams, winning_team=team.id, sale_price=2)
    process_sale(auction, players, teams, winning_team=team.id, sale_price=1)
    assert team.roster_size == 6
    assert team.remaining_budget == 2
    assert team.maximum_legal_bid == 1

    process_sale(auction, players, teams, winning_team=team.id, sale_price=1)
    assert team.roster_size == 7
    assert team.remaining_budget == 1
    assert team.maximum_legal_bid == 1

    process_sale(auction, players, teams, winning_team=team.id, sale_price=1)
    assert team.roster_size == 8
    assert team.remaining_budget == 0


# ============================================================
# Error message wording
# ============================================================


def test_budget_reserve_violation_message_names_team_and_reserve() -> None:
    team = _team_with(roster_size=5, remaining_budget=3)
    message = budget_reserve_violation_message(team)
    assert "Blackout FC" in message
    assert "2M" in message  # reserve for the 2 slots remaining after this purchase
    assert "1M" in message  # maximum legal price


def test_budget_reserve_violation_message_raised_on_rejection() -> None:
    team = _team_with(roster_size=5, remaining_budget=3)
    player = _players([(901, "P1", Position.DEF)])[0]
    auction, players, teams = _single_player_setup(team, player)

    with pytest.raises(AuctionTransactionError, match="must reserve"):
        process_sale(auction, players, teams, winning_team=team.id, sale_price=2)
