"""Tests for services/live_bid_service.py: Captain Phone Bidding Phase 1's
live bid/leader state, kept separate from SOLD/UNSOLD transactions.
"""

from __future__ import annotations

import threading

import pytest

from models.auction import AuctionStatus
from models.player import Player, Position
from models.team import Team
from services import live_bid_service as lbs
from services.auction_service import (
    get_current_player,
    process_sale,
    process_unsold,
    team_can_bid_for_player,
)
from services.player_service import load_players, load_teams
from services.randomization_service import create_auction


def build_state(seed: int = 1):
    players = load_players()
    teams = load_teams()
    auction = create_auction(players, seed=seed)
    return auction, players, teams


def team_by_name(teams: list[Team], name: str) -> Team:
    return next(team for team in teams if team.name == name)


def pick_eligible_team(player: Player, teams: list[Team], players: list[Player]) -> Team | None:
    eligible = [team for team in teams if team_can_bid_for_player(team, player, players)]
    if not eligible:
        return None
    return min(eligible, key=lambda team: team.roster_size)


# ============================================================
# 1-2. AUCTION / TEAM EXISTENCE
# ============================================================


def test_no_active_auction_rejects_bid() -> None:
    _, players, teams = build_state()
    result = lbs.place_bid(None, players, teams, "Blackout FC", 5)
    assert result.accepted is False
    assert "not initialized" in result.reason.lower()


def test_invalid_team_rejects() -> None:
    auction, players, teams = build_state()
    result = lbs.place_bid(auction, players, teams, "Nonexistent FC", 5)
    assert result.accepted is False
    assert result.reason == "Team not found."


# ============================================================
# 3-4. INITIAL BID / MUST EXCEED CURRENT
# ============================================================


def test_valid_initial_1m_bid_accepted() -> None:
    auction, players, teams = build_state()
    result = lbs.place_bid(auction, players, teams, "Blackout FC", 1)
    assert result.accepted is True
    assert result.current_bid == 1
    blackout = team_by_name(teams, "Blackout FC")
    assert result.leading_team_id == blackout.id


def test_bid_must_exceed_current_bid() -> None:
    auction, players, teams = build_state()
    lbs.place_bid(auction, players, teams, "Blackout FC", 5)
    result = lbs.place_bid(auction, players, teams, "Darkstar FC", 5)
    assert result.accepted is False
    assert "exceed the current highest bid of 5M" in result.reason


def test_equal_bid_rejected() -> None:
    auction, players, teams = build_state()
    lbs.place_bid(auction, players, teams, "Blackout FC", 5)
    result = lbs.place_bid(auction, players, teams, "Blackout FC", 5)
    assert result.accepted is False


# ============================================================
# 5-7. INCREMENTS (caller computes the target; service validates it)
# ============================================================


def test_plus_1_increment_from_current_bid() -> None:
    auction, players, teams = build_state()
    lbs.place_bid(auction, players, teams, "Blackout FC", 7)
    result = lbs.place_bid(auction, players, teams, "Darkstar FC", auction.current_bid + 1)
    assert result.accepted is True
    assert result.current_bid == 8


def test_plus_2_increment_from_current_bid() -> None:
    auction, players, teams = build_state()
    lbs.place_bid(auction, players, teams, "Blackout FC", 7)
    result = lbs.place_bid(auction, players, teams, "Darkstar FC", auction.current_bid + 2)
    assert result.accepted is True
    assert result.current_bid == 9


def test_plus_5_increment_from_current_bid() -> None:
    auction, players, teams = build_state()
    lbs.place_bid(auction, players, teams, "Blackout FC", 7)
    result = lbs.place_bid(auction, players, teams, "Darkstar FC", auction.current_bid + 5)
    assert result.accepted is True
    assert result.current_bid == 12


def test_increment_rejected_when_it_would_exceed_max_legal_bid() -> None:
    """Current bid 7M, team's max legal bid is 10M: a +5 (-> 12M) must be
    rejected outright, never silently clamped to 10M."""
    auction, players, teams = build_state()
    blackout = team_by_name(teams, "Blackout FC")
    # Fill Blackout's roster to 7/8 so its reserve-adjusted max legal bid
    # is small and easy to reason about (no reserve needed for the last slot).
    blackout.roster.extend(range(901, 907))
    blackout.remaining_budget = 10
    blackout.auction_spending = 90
    blackout.players_purchased = 6
    assert blackout.maximum_legal_bid == 10

    lbs.place_bid(auction, players, teams, "Darkstar FC", 7)
    result = lbs.place_bid(auction, players, teams, "Blackout FC", auction.current_bid + 5)
    assert result.accepted is False
    assert auction.current_bid == 7  # unchanged — never silently clamped
    assert auction.leading_team_id == team_by_name(teams, "Darkstar FC").id


# ============================================================
# 8-11. ELIGIBILITY REUSE
# ============================================================


def test_max_legal_bid_enforced() -> None:
    auction, players, teams = build_state()
    blackout = team_by_name(teams, "Blackout FC")
    blackout.remaining_budget = 3
    blackout.auction_spending = 97
    result = lbs.place_bid(auction, players, teams, "Blackout FC", 4)
    assert result.accepted is False


def test_budget_reserve_enforced() -> None:
    """A team with 5/8 roster and only 3M left can legally bid at most 1M
    (2M must stay reserved for its 2 remaining slots) — see PROJECT_CONTEXT
    .md's PRE-M9 BUDGET RESERVE RULE."""
    auction, players, teams = build_state()
    blackout = team_by_name(teams, "Blackout FC")
    blackout.roster.extend([901, 902, 903, 904])
    blackout.remaining_budget = 3
    blackout.auction_spending = 97
    blackout.players_purchased = 4
    assert blackout.maximum_legal_bid == 1

    result = lbs.place_bid(auction, players, teams, "Blackout FC", 2)
    assert result.accepted is False
    accepted = lbs.place_bid(auction, players, teams, "Blackout FC", 1)
    assert accepted.accepted is True


def test_full_team_rejected() -> None:
    auction, players, teams = build_state()
    blackout = team_by_name(teams, "Blackout FC")
    blackout.roster.extend(range(901, 908))
    blackout.players_purchased = 7
    assert blackout.roster_size == 8
    result = lbs.place_bid(auction, players, teams, "Blackout FC", 5)
    assert result.accepted is False
    assert "full" in result.reason.lower()


def test_gk_conflict_rejected() -> None:
    auction, players, teams = build_state(seed=1)
    players_by_id = {p.id: p for p in players}
    current_player = get_current_player(auction, players)
    # Force the current player to a GK for this test's purposes.
    current_player.position = Position.GK

    blackout = team_by_name(teams, "Blackout FC")
    existing_gk = next(p for p in players if p.position == Position.GK and p.id != current_player.id)
    blackout.roster.append(existing_gk.id)

    result = lbs.place_bid(auction, players, teams, "Blackout FC", 5)
    assert result.accepted is False
    assert "goalkeeper" in result.reason.lower()


# ============================================================
# 12-14. STATE MUTATION
# ============================================================


def test_accepted_bid_updates_current_bid() -> None:
    auction, players, teams = build_state()
    lbs.place_bid(auction, players, teams, "Blackout FC", 6)
    assert auction.current_bid == 6


def test_accepted_bid_updates_leading_team() -> None:
    auction, players, teams = build_state()
    darkstar = team_by_name(teams, "Darkstar FC")
    lbs.place_bid(auction, players, teams, "Darkstar FC", 6)
    assert auction.leading_team_id == darkstar.id


def test_invalid_bid_changes_nothing() -> None:
    auction, players, teams = build_state()
    lbs.place_bid(auction, players, teams, "Blackout FC", 6)
    result = lbs.place_bid(auction, players, teams, "Darkstar FC", 6)  # not > 6
    assert result.accepted is False
    assert auction.current_bid == 6
    assert auction.leading_team_id == team_by_name(teams, "Blackout FC").id


# ============================================================
# 15. CONCURRENCY
# ============================================================


def test_simultaneous_same_value_bids_produce_exactly_one_accepted() -> None:
    auction, players, teams = build_state()
    lbs.place_bid(auction, players, teams, "Blackout FC", 5)

    results: list[lbs.BidResult] = [None, None]  # type: ignore[list-item]
    barrier = threading.Barrier(2)

    def _bid(index: int, team_name: str) -> None:
        barrier.wait()
        results[index] = lbs.place_bid(auction, players, teams, team_name, 6)

    t1 = threading.Thread(target=_bid, args=(0, "Darkstar FC"))
    t2 = threading.Thread(target=_bid, args=(1, "Goli Underdogs"))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    accepted_count = sum(1 for r in results if r.accepted)
    assert accepted_count == 1
    assert auction.current_bid == 6


# ============================================================
# 17-18. TERMINAL STATUSES REJECT BIDDING
# ============================================================


def test_complete_status_rejects_bidding() -> None:
    auction, players, teams = build_state()
    auction.status = AuctionStatus.COMPLETE
    result = lbs.place_bid(auction, players, teams, "Blackout FC", 5)
    assert result.accepted is False
    assert "complete" in result.reason.lower()


def test_blocked_status_rejects_bidding() -> None:
    auction, players, teams = build_state()
    auction.status = AuctionStatus.BLOCKED
    result = lbs.place_bid(auction, players, teams, "Blackout FC", 5)
    assert result.accepted is False
    assert "blocked" in result.reason.lower()


# ============================================================
# 16. RESET ON ADVANCEMENT (auction_service integration)
# ============================================================


def test_sold_resets_live_bid_state() -> None:
    auction, players, teams = build_state()
    lbs.place_bid(auction, players, teams, "Blackout FC", 5)
    assert auction.current_bid == 5

    team = pick_eligible_team(get_current_player(auction, players), teams, players)
    process_sale(auction, players, teams, winning_team=team.id, sale_price=5)

    assert auction.current_bid is None
    assert auction.leading_team_id is None


def test_unsold_resets_live_bid_state() -> None:
    auction, players, teams = build_state()
    lbs.place_bid(auction, players, teams, "Darkstar FC", 3)
    assert auction.current_bid == 3

    process_unsold(auction, players, teams)

    assert auction.current_bid is None
    assert auction.leading_team_id is None


def test_new_bid_after_reset_does_not_see_stale_previous_bid() -> None:
    auction, players, teams = build_state()
    lbs.place_bid(auction, players, teams, "Blackout FC", 20)
    process_unsold(auction, players, teams)

    result = lbs.place_bid(auction, players, teams, "Darkstar FC", 1)
    assert result.accepted is True
    assert result.current_bid == 1
