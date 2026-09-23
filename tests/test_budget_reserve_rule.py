"""First Auction Rules V2 (September 2026): GK/non-GK base prices and the
dynamic completion reserve that replaced the earlier flat 1M-per-slot
reserve (see PROJECT_CONTEXT.md's "FIRST AUCTION RULES V2" and the
now-historical "PRE-M9 BUDGET RESERVE RULE" it supersedes).

Section headers below mirror the ticket's own numbered test list exactly
(BASE PRICE 1-6, COMPLETION RESERVE 7-16, EDGE CASES 17-25) so each test
name can be matched back to its numbered requirement.
"""
import copy

import pytest

from models.auction import Auction, AuctionStatus
from models.player import GK_BASE_PRICE, OUTFIELD_BASE_PRICE, Player, Position, player_base_price
from models.team import Team
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
# TESTING — BASE PRICE (1-6)
# ============================================================


def test_1_gk_base_price_is_4m() -> None:
    gk = _players([(901, "GK1", Position.GK)])[0]
    assert player_base_price(gk) == GK_BASE_PRICE == 4


def test_2_non_gk_base_price_is_2m() -> None:
    non_gk = _players([(901, "P1", Position.DEF)])[0]
    assert player_base_price(non_gk) == OUTFIELD_BASE_PRICE == 2


def test_3_gk_bid_3m_rejected() -> None:
    team = _team_with(roster_size=1, remaining_budget=100)
    gk = _players([(901, "GK1", Position.GK)])[0]
    auction, players, teams = _single_player_setup(team, gk)
    with pytest.raises(AuctionTransactionError, match="minimum price is 4M"):
        process_sale(auction, players, teams, winning_team=team.id, sale_price=3)


def test_4_gk_bid_4m_accepted_when_otherwise_legal() -> None:
    team = _team_with(roster_size=1, remaining_budget=100)
    gk = _players([(901, "GK1", Position.GK)])[0]
    auction, players, teams = _single_player_setup(team, gk)
    result = process_sale(auction, players, teams, winning_team=team.id, sale_price=4)
    assert result.outcome == "SOLD"
    assert team.remaining_budget == 96


def test_5_non_gk_bid_1m_rejected() -> None:
    team = _team_with(roster_size=1, remaining_budget=100)
    non_gk = _players([(901, "P1", Position.DEF)])[0]
    auction, players, teams = _single_player_setup(team, non_gk)
    with pytest.raises(AuctionTransactionError, match="minimum price is 2M"):
        process_sale(auction, players, teams, winning_team=team.id, sale_price=1)


def test_6_non_gk_bid_2m_accepted_when_otherwise_legal() -> None:
    team = _team_with(roster_size=1, remaining_budget=100)
    non_gk = _players([(901, "P1", Position.DEF)])[0]
    auction, players, teams = _single_player_setup(team, non_gk)
    result = process_sale(auction, players, teams, winning_team=team.id, sale_price=2)
    assert result.outcome == "SOLD"
    assert team.remaining_budget == 98


# ============================================================
# TESTING — COMPLETION RESERVE (7-16)
# Worked examples A-F from PROJECT_CONTEXT.md's "FIRST AUCTION RULES V2".
# ============================================================


def test_7_example_a_1_of_8_100m_no_gk_non_gk_max_86m() -> None:
    team = _team_with(roster_size=1, remaining_budget=100)
    assert team.maximum_legal_bid(purchasing_gk=False, team_has_gk=False) == 86


def test_8_example_b_1_of_8_100m_no_gk_gk_max_88m() -> None:
    team = _team_with(roster_size=1, remaining_budget=100)
    assert team.maximum_legal_bid(purchasing_gk=True, team_has_gk=False) == 88


def test_9_example_c_5_of_8_30m_has_gk_non_gk_max_26m() -> None:
    team = _team_with(roster_size=5, remaining_budget=30)
    assert team.maximum_legal_bid(purchasing_gk=False, team_has_gk=True) == 26


def test_10_example_d_5_of_8_30m_no_gk_non_gk_max_24m() -> None:
    team = _team_with(roster_size=5, remaining_budget=30)
    assert team.maximum_legal_bid(purchasing_gk=False, team_has_gk=False) == 24


def test_11_example_e_5_of_8_30m_no_gk_gk_max_26m() -> None:
    team = _team_with(roster_size=5, remaining_budget=30)
    assert team.maximum_legal_bid(purchasing_gk=True, team_has_gk=False) == 26


def test_12_example_f_7_of_8_final_player_can_spend_entire_remaining_budget() -> None:
    team = _team_with(roster_size=7, remaining_budget=10)
    # Whether the final slot is filled by a GK (team has none yet) or a
    # non-GK (team already has one), the whole 10M remaining is legal —
    # zero slots remain to reserve anything for afterward.
    assert team.maximum_legal_bid(purchasing_gk=True, team_has_gk=False) == 10
    assert team.maximum_legal_bid(purchasing_gk=False, team_has_gk=True) == 10


def test_13_team_may_finish_auction_with_0m() -> None:
    # Team already owns its mandatory GK (a real GK Player on the roster,
    # not just a filler id), so its final non-GK purchase may legally
    # spend every remaining M.
    gk_on_roster = Player(id=800, full_name="Existing GK", short_name="GK1", position=Position.GK, overall_rating=80)
    team = _team_with(roster_size=6, remaining_budget=10)
    team.roster.append(gk_on_roster.id)
    non_gk = _players([(901, "P1", Position.DEF)])[0]
    auction = Auction(queue=[901], status=AuctionStatus.IN_PROGRESS)
    players = [gk_on_roster, non_gk]
    teams = [team]
    result = process_sale(auction, players, teams, winning_team=team.id, sale_price=10)
    assert result.outcome == "SOLD"
    assert team.remaining_budget == 0
    assert team.roster_size == 8


def test_14_purchase_rejected_if_it_would_make_squad_completion_impossible() -> None:
    # 7/8, no GK, buying a non-GK as the literal final slot: the team
    # would finish 8/8 without ever acquiring its mandatory goalkeeper.
    team = _team_with(roster_size=7, remaining_budget=50)
    non_gk = _players([(901, "P1", Position.DEF)])[0]
    auction, players, teams = _single_player_setup(team, non_gk)
    with pytest.raises(AuctionTransactionError, match="mandatory goalkeeper"):
        process_sale(auction, players, teams, winning_team=team.id, sale_price=2)


def test_15_gk_requirement_reserve_handled_correctly() -> None:
    team = _team_with(roster_size=1, remaining_budget=100)
    # Needing a GK reserves GK_BASE_PRICE for it plus OUTFIELD_BASE_PRICE
    # for every other still-required slot -- never just a flat per-slot
    # amount regardless of position.
    reserve = team.minimum_completion_cost_after_purchase(purchasing_gk=False, team_has_gk=False)
    assert reserve == GK_BASE_PRICE + 5 * OUTFIELD_BASE_PRICE == 14
    reserve_after_gk_purchase = team.minimum_completion_cost_after_purchase(purchasing_gk=True, team_has_gk=False)
    assert reserve_after_gk_purchase == 6 * OUTFIELD_BASE_PRICE == 12


def test_16_full_team_rejected() -> None:
    team = _team_with(roster_size=8, remaining_budget=50)
    non_gk = _players([(901, "P1", Position.DEF)])[0]
    assert team_can_bid_for_player(team, non_gk, [non_gk]) is False
    auction, players, teams = _single_player_setup(team, non_gk)
    with pytest.raises(AuctionTransactionError, match="roster is full"):
        process_sale(auction, players, teams, winning_team=team.id, sale_price=2)


# ============================================================
# TESTING — EDGE CASES (17-25)
# ============================================================


def test_17_team_budget_exactly_equals_minimum_completion_requirement() -> None:
    team = _team_with(roster_size=1, remaining_budget=14)  # exactly this ticket's own reserve, example A's shape
    assert team.maximum_legal_bid(purchasing_gk=False, team_has_gk=False) == 0


def test_18_budget_one_million_below_minimum_completion_requirement() -> None:
    team = _team_with(roster_size=1, remaining_budget=13)
    assert team.maximum_legal_bid(purchasing_gk=False, team_has_gk=False) == -1


def test_19_current_player_gk_when_gk_already_owned_is_illegal() -> None:
    gk_on_roster = Player(id=800, full_name="Existing GK", short_name="GK1", position=Position.GK, overall_rating=80)
    team = _team_with(roster_size=1, remaining_budget=100)
    team.roster.append(gk_on_roster.id)
    new_gk = Player(id=901, full_name="New GK", short_name="GK2", position=Position.GK, overall_rating=80)
    assert team_can_bid_for_player(team, new_gk, [gk_on_roster, new_gk]) is False
    auction, _players_list, teams = _single_player_setup(team, new_gk)
    all_players = [gk_on_roster, new_gk]
    with pytest.raises(AuctionTransactionError, match="already has a goalkeeper"):
        process_sale(auction, all_players, teams, winning_team=team.id, sale_price=4)


def test_20_current_player_non_gk_when_final_required_slot_must_be_gk() -> None:
    team = _team_with(roster_size=7, remaining_budget=50)
    non_gk = _players([(901, "P1", Position.DEF)])[0]
    assert team_can_bid_for_player(team, non_gk, [non_gk]) is False


def test_21_current_player_gk_when_final_slot_and_no_gk_owned_is_legal() -> None:
    team = _team_with(roster_size=7, remaining_budget=10)
    gk = _players([(901, "GK1", Position.GK)])[0]
    assert team_can_bid_for_player(team, gk, [gk]) is True
    auction, players, teams = _single_player_setup(team, gk)
    result = process_sale(auction, players, teams, winning_team=team.id, sale_price=10)
    assert result.outcome == "SOLD"
    assert team.remaining_budget == 0


def test_22_final_non_gk_when_gk_already_owned_is_legal() -> None:
    gk_on_roster = Player(id=800, full_name="Existing GK", short_name="GK1", position=Position.GK, overall_rating=80)
    team = _team_with(roster_size=6, remaining_budget=10)
    team.roster.append(gk_on_roster.id)  # roster_size now 7, one of them a real GK
    non_gk = _players([(901, "P1", Position.DEF)])[0]
    all_players = [gk_on_roster, non_gk]
    assert team_can_bid_for_player(team, non_gk, all_players) is True
    auction = Auction(queue=[901], status=AuctionStatus.IN_PROGRESS)
    teams = [team]
    result = process_sale(auction, all_players, teams, winning_team=team.id, sale_price=10)
    assert result.outcome == "SOLD"
    assert team.remaining_budget == 0


def test_23_max_legal_bid_never_displays_negative() -> None:
    team = _team_with(roster_size=1, remaining_budget=13)
    raw = team.maximum_legal_bid(purchasing_gk=False, team_has_gk=False)
    assert raw < 0
    assert max(raw, 0) == 0  # the exact clamp every UI/report/phone display applies


def test_24_max_legal_bid_below_base_price_means_team_cannot_bid() -> None:
    team = _team_with(roster_size=1, remaining_budget=13)  # max legal = -1 (see test 18)
    non_gk = _players([(901, "P1", Position.DEF)])[0]
    assert team_can_bid_for_player(team, non_gk, [non_gk]) is False
    assert team.can_afford_while_preserving_roster_budget(2, purchasing_gk=False, team_has_gk=False) is False


def test_25_blocked_detection_uses_new_rules() -> None:
    """One team remains with roster space, but spending even the base
    price on the last remaining (non-GK) player would leave it unable to
    ever acquire its mandatory goalkeeper -> no legal destination anywhere
    -> BLOCKED, under the new dynamic-reserve rules, not the old flat one."""
    starving_team = _team_with(roster_size=7, remaining_budget=50, team_id=1)
    other_teams = [
        _team_with(roster_size=8, remaining_budget=0, team_id=2),
        _team_with(roster_size=8, remaining_budget=0, team_id=3),
        _team_with(roster_size=8, remaining_budget=0, team_id=4),
    ]
    for team in other_teams:
        team.name = f"Team {team.id}"

    non_gk = _players([(901, "P1", Position.DEF)])[0]
    auction, players, teams = _single_player_setup(starving_team, non_gk, other_teams)

    result = process_unsold(auction, players, teams)
    assert result.auction_status == AuctionStatus.BLOCKED
    assert auction.status == AuctionStatus.BLOCKED


# ============================================================
# A successful purchase recalculates the reserve correctly
# (kept from the pre-V2 test file, updated to the new formula/prices)
# ============================================================


def test_successful_purchase_recalculates_reserve() -> None:
    gk_on_roster = Player(id=800, full_name="Existing GK", short_name="GK1", position=Position.GK, overall_rating=80)
    team = _team_with(roster_size=4, remaining_budget=30)
    team.roster.append(gk_on_roster.id)  # roster_size now 5, has a real GK
    assert team.maximum_legal_bid(purchasing_gk=False, team_has_gk=True) == 26

    non_gk = _players([(901, "P1", Position.DEF)])[0]
    auction = Auction(queue=[901], status=AuctionStatus.IN_PROGRESS)
    players = [gk_on_roster, non_gk]
    teams = [team]
    process_sale(auction, players, teams, winning_team=team.id, sale_price=26)

    assert team.roster_size == 6
    assert team.remaining_budget == 4
    # 2 roster slots remaining, team already has its GK -> 1 slot after
    # this next purchase * 2M reserve.
    assert team.maximum_legal_bid(purchasing_gk=False, team_has_gk=True) == 2


def test_rejected_purchase_is_atomic() -> None:
    team = _team_with(roster_size=5, remaining_budget=30)
    non_gk = _players([(901, "P1", Position.DEF)])[0]
    auction, players, teams = _single_player_setup(team, non_gk)

    snapshot_team = copy.deepcopy(team)
    snapshot_players = copy.deepcopy(players)
    snapshot_position = auction.current_queue_position
    snapshot_current_player_id = auction.current_player_id

    with pytest.raises(AuctionTransactionError):
        process_sale(auction, players, teams, winning_team=team.id, sale_price=27)

    assert team == snapshot_team  # budget, roster, and spending unchanged
    assert players == snapshot_players
    assert auction.history == []
    assert auction.current_queue_position == snapshot_position
    assert auction.current_player_id == snapshot_current_player_id


# ============================================================
# Real canonical data: Blackout FC's real first-purchase ceiling
# ============================================================


def test_real_canonical_first_purchase_ceiling() -> None:
    """Blackout FC's captain (Samin Haque, ATT) means the team owns no GK
    yet, so its first purchase's ceiling reserves a future GK_BASE_PRICE
    slot (unless the very first queued player already is the GK)."""
    players = load_players()
    teams = load_teams()
    auction = create_auction(players, seed=1)
    blackout = next(t for t in teams if t.name == "Blackout FC")
    first_player = next(p for p in players if p.id == auction.queue[0])
    purchasing_gk = first_player.position == Position.GK

    expected_max = blackout.maximum_legal_bid(purchasing_gk=purchasing_gk, team_has_gk=False)
    if purchasing_gk:
        assert expected_max == 100 - 6 * OUTFIELD_BASE_PRICE
    else:
        assert expected_max == 100 - (GK_BASE_PRICE + 5 * OUTFIELD_BASE_PRICE)

    result = process_sale(auction, players, teams, winning_team="Blackout FC", sale_price=expected_max)
    assert result.outcome == "SOLD"
    assert blackout.remaining_budget == 100 - expected_max


# ============================================================
# Error message wording
# ============================================================


def test_budget_reserve_violation_message_names_team_and_reserve() -> None:
    team = _team_with(roster_size=5, remaining_budget=5)
    non_gk = _players([(901, "P1", Position.DEF)])[0]
    message = budget_reserve_violation_message(team, non_gk, [non_gk])
    assert "Blackout FC" in message
    assert "must reserve" in message


def test_budget_reserve_violation_message_raised_on_rejection() -> None:
    team = _team_with(roster_size=5, remaining_budget=5)
    non_gk = _players([(901, "P1", Position.DEF)])[0]
    auction, players, teams = _single_player_setup(team, non_gk)

    with pytest.raises(AuctionTransactionError, match="must reserve"):
        process_sale(auction, players, teams, winning_team=team.id, sale_price=5)
