"""Milestone 8.1: re-auction rounds, the one-goalkeeper-per-team rule,
full-team eligibility, and blocked-auction detection.

Uses small, fully-synthetic player pools (via _players()) for tests that
need precise control over "how many players are in this tournament" —
the real 32-player canonical set already has a fixed 28-eligible/4-GK
shape that isn't flexible enough to build a captain-is-GK scenario, an
oversupplied/undersupplied GK scenario, etc. Tests that specifically
exercise the real canonical data say so.
"""

from pathlib import Path

import pytest

from models.auction import Auction, AuctionStatus
from models.player import Player, PlayerAuctionStatus, Position
from models.team import Team
from services.auction_service import (
    AuctionTransactionError,
    eligible_for_reauction,
    get_current_player,
    process_sale,
    process_unsold,
    sold_player_ids,
    team_can_bid_for_player,
    team_has_goalkeeper,
)
from services.auction_session_service import AuctionSession
from services.player_service import load_players, load_teams, validate_setup

ROOT = Path(__file__).resolve().parents[1]


def _players(specs: list[tuple[int, str, Position]]) -> list[Player]:
    """Minimal non-captain, auction-eligible synthetic players."""
    return [
        Player(id=player_id, full_name=name, short_name=name, position=position, overall_rating=80)
        for player_id, name, position in specs
    ]


def _teams() -> list[Team]:
    """The real 4 canonical teams — team identity/captain rules aren't
    what's under test here, only roster/GK/budget mechanics."""
    return load_teams()


def team_by_name(teams: list[Team], name: str) -> Team:
    return next(team for team in teams if team.name == name)


# ============================================================
# RE-AUCTION
# ============================================================


def test_first_pass_unsold_player_enters_round_2() -> None:
    players = _players([(901, "P1", Position.DEF), (902, "P2", Position.MID)])
    teams = _teams()
    auction = Auction(queue=[901, 902], status=AuctionStatus.IN_PROGRESS)

    process_sale(auction, players, teams, winning_team="Blackout FC", sale_price=1)
    process_unsold(auction, players, teams)

    assert auction.round_number == 2
    assert auction.queue == [902]


def test_sold_player_does_not_enter_round_2() -> None:
    players = _players([(901, "P1", Position.DEF), (902, "P2", Position.MID), (903, "P3", Position.ATT)])
    teams = _teams()
    auction = Auction(queue=[901, 902, 903], status=AuctionStatus.IN_PROGRESS)

    process_sale(auction, players, teams, winning_team="Blackout FC", sale_price=1)
    process_unsold(auction, players, teams)
    process_unsold(auction, players, teams)

    assert auction.round_number == 2
    assert 901 not in auction.queue
    assert set(auction.queue) == {902, 903}


def test_two_unsold_players_produce_a_2_player_round_2_queue() -> None:
    players = _players(
        [(901, "P1", Position.DEF), (902, "P2", Position.MID), (903, "P3", Position.ATT), (904, "P4", Position.DEF)]
    )
    teams = _teams()
    auction = Auction(queue=[901, 902, 903, 904], status=AuctionStatus.IN_PROGRESS)

    process_sale(auction, players, teams, winning_team="Blackout FC", sale_price=1)
    process_sale(auction, players, teams, winning_team="Darkstar FC", sale_price=1)
    process_unsold(auction, players, teams)
    process_unsold(auction, players, teams)

    assert auction.round_number == 2
    assert len(auction.queue) == 2
    assert set(auction.queue) == {903, 904}


def test_reauction_queue_contains_no_duplicates() -> None:
    players = _players([(901, "P1", Position.DEF), (902, "P2", Position.MID)])
    teams = _teams()
    auction = Auction(queue=[901, 902], status=AuctionStatus.IN_PROGRESS)
    process_unsold(auction, players, teams)
    process_unsold(auction, players, teams)
    assert len(auction.queue) == len(set(auction.queue))


def test_reauction_queue_is_shuffled_once_per_new_round() -> None:
    """Same seed -> same round-2 order; different seed -> can differ."""
    specs = [(900 + i, f"P{i}", Position.DEF) for i in range(1, 9)]

    def run(seed: int) -> list[int]:
        players = _players(specs)
        teams = _teams()
        auction = Auction(queue=[p.id for p in players], status=AuctionStatus.IN_PROGRESS, random_seed=seed)
        for _ in players:
            process_unsold(auction, players, teams)
        return list(auction.queue)

    assert run(seed=7) == run(seed=7)
    orders = {tuple(run(seed=s)) for s in range(1, 8)}
    assert len(orders) > 1


def test_future_round_order_remains_hidden_via_session_api() -> None:
    """AuctionSession's own surface never exposes the raw queue, even
    across rounds — only current_player and round-scoped counts."""
    session = AuctionSession()
    session.start(seed=1)
    for _ in range(session.total_queue_length):
        session.mark_current_player_unsold()
    assert session.round_number == 2
    public_attrs = {name for name in dir(session) if not name.startswith("_")}
    assert "queue" not in public_attrs


def test_unsold_in_round_2_can_enter_round_3() -> None:
    players = _players([(901, "P1", Position.DEF), (902, "P2", Position.MID)])
    teams = _teams()
    auction = Auction(queue=[901, 902], status=AuctionStatus.IN_PROGRESS, random_seed=3)

    process_unsold(auction, players, teams)  # round 1 -> round 2 (order may be reshuffled)
    process_unsold(auction, players, teams)
    assert auction.round_number == 2

    sold_result = process_sale(auction, players, teams, winning_team="Blackout FC", sale_price=1)
    process_unsold(auction, players, teams)
    assert auction.round_number == 3
    # Whichever of the two wasn't just sold is the one still pending.
    still_pending_id = 902 if sold_result.player.id == 901 else 901
    assert auction.queue == [still_pending_id]


def test_player_unsold_twice_and_sold_third_time_works() -> None:
    players = _players([(901, "P1", Position.DEF), (902, "P2", Position.MID)])
    teams = _teams()
    auction = Auction(queue=[901, 902], status=AuctionStatus.IN_PROGRESS, random_seed=3)

    process_unsold(auction, players, teams)  # 901 UNSOLD (round 1)
    process_unsold(auction, players, teams)  # 902 UNSOLD (round 1) -> round 2 starts
    assert auction.round_number == 2

    process_sale(auction, players, teams, winning_team="Blackout FC", sale_price=1)  # sell whichever is first
    process_unsold(auction, players, teams)  # the other UNSOLD again -> round 3 with just them
    assert auction.round_number == 3
    remaining_id = auction.queue[0]

    result = process_sale(auction, players, teams, winning_team="Darkstar FC", sale_price=2)
    assert result.outcome == "SOLD"
    assert result.player.id == remaining_id

    entries_for_player = [entry for entry in auction.history if entry.player_id == remaining_id]
    assert [entry.status for entry in entries_for_player] == ["UNSOLD", "UNSOLD", "SOLD"]


def test_history_preserves_all_attempts() -> None:
    players = _players([(901, "P1", Position.DEF)])
    teams = _teams()
    auction = Auction(queue=[901], status=AuctionStatus.IN_PROGRESS, random_seed=1)

    process_unsold(auction, players, teams)
    process_unsold(auction, players, teams)
    process_sale(auction, players, teams, winning_team="Blackout FC", sale_price=5)

    entries = [entry for entry in auction.history if entry.player_id == 901]
    assert len(entries) == 3
    assert [entry.status for entry in entries] == ["UNSOLD", "UNSOLD", "SOLD"]
    assert entries[-1].team == "Blackout FC"
    assert entries[-1].sold_price == 5


def test_auction_does_not_complete_merely_because_first_28_attempts_finished() -> None:
    """The exact scenario that exposed the bug: 26 sold, 2 unsold after
    the first pass through 28 real players must NOT be AUCTION COMPLETE."""
    players = load_players()
    teams = load_teams()
    from services.randomization_service import create_auction

    auction = create_auction(players, seed=1)
    unsold_forced = 0
    for _ in range(28):
        current = get_current_player(auction, players)
        eligible = [t for t in teams if team_can_bid_for_player(t, current, players)]
        if unsold_forced < 2 and eligible:
            unsold_forced += 1
            process_unsold(auction, players, teams)
            continue
        if not eligible:
            process_unsold(auction, players, teams)
            continue
        team = min(eligible, key=lambda t: t.roster_size)
        process_sale(auction, players, teams, winning_team=team.id, sale_price=1)

    assert auction.round_number == 2
    assert auction.status == AuctionStatus.IN_PROGRESS
    assert auction.status != AuctionStatus.COMPLETE
    assert len(sold_player_ids(auction)) == 26


def test_auction_completes_only_when_all_28_eligible_players_are_sold() -> None:
    players = load_players()
    teams = load_teams()
    from services.randomization_service import create_auction

    auction = create_auction(players, seed=1)
    guard = 0
    while auction.status not in (AuctionStatus.COMPLETE, AuctionStatus.BLOCKED) and guard < 200:
        guard += 1
        current = get_current_player(auction, players)
        eligible = [t for t in teams if team_can_bid_for_player(t, current, players)]
        if not eligible:
            process_unsold(auction, players, teams)
        else:
            team = min(eligible, key=lambda t: t.roster_size)
            process_sale(auction, players, teams, winning_team=team.id, sale_price=1)

    assert auction.status == AuctionStatus.COMPLETE
    assert len(sold_player_ids(auction)) == 28


def test_sold_count_and_remaining_count_derive_correctly_across_rounds() -> None:
    session = AuctionSession()
    session.start(seed=1)
    for _ in range(session.total_queue_length):
        session.mark_current_player_unsold()

    assert session.round_number == 2
    assert session.sold_count == 0
    assert session.total_eligible_count == 28
    assert session.remaining_count == 28  # round-2 queue, all 28 still pending

    blackout = team_by_name(session.teams, "Blackout FC")
    session.sell_current_player(winning_team=blackout.id, sale_price=1)
    assert session.sold_count == 1
    assert session.remaining_count == 27  # one round-2 slot resolved


# ============================================================
# GOALKEEPER RULE
# ============================================================


def test_team_with_no_gk_can_buy_a_gk() -> None:
    players = _players([(901, "Keeper", Position.GK)])
    teams = _teams()
    auction = Auction(queue=[901], status=AuctionStatus.IN_PROGRESS)
    result = process_sale(auction, players, teams, winning_team="Blackout FC", sale_price=5)
    assert result.outcome == "SOLD"
    assert team_has_goalkeeper(team_by_name(teams, "Blackout FC"), players)


def test_team_with_one_gk_cannot_buy_another_gk() -> None:
    players = _players([(901, "Keeper One", Position.GK), (902, "Keeper Two", Position.GK)])
    teams = _teams()
    auction = Auction(queue=[901, 902], status=AuctionStatus.IN_PROGRESS)
    process_sale(auction, players, teams, winning_team="Blackout FC", sale_price=5)

    with pytest.raises(AuctionTransactionError, match="already has a goalkeeper"):
        process_sale(auction, players, teams, winning_team="Blackout FC", sale_price=5)


def test_failed_second_gk_sale_changes_nothing() -> None:
    players = _players([(901, "Keeper One", Position.GK), (902, "Keeper Two", Position.GK)])
    teams = _teams()
    auction = Auction(queue=[901, 902], status=AuctionStatus.IN_PROGRESS)
    process_sale(auction, players, teams, winning_team="Blackout FC", sale_price=5)

    blackout = team_by_name(teams, "Blackout FC")
    budget_before = blackout.remaining_budget
    roster_before = list(blackout.roster)
    position_before = auction.current_queue_position
    history_len_before = len(auction.history)

    with pytest.raises(AuctionTransactionError):
        process_sale(auction, players, teams, winning_team="Blackout FC", sale_price=5)

    assert blackout.remaining_budget == budget_before
    assert blackout.roster == roster_before
    assert auction.current_queue_position == position_before
    assert len(auction.history) == history_len_before


def test_gk_detection_comes_from_actual_roster_player_position() -> None:
    players = _players([(901, "Keeper", Position.GK), (902, "Outfield", Position.DEF)])
    blackout = team_by_name(_teams(), "Blackout FC")
    assert team_has_goalkeeper(blackout, players) is False
    blackout.roster.append(902)
    assert team_has_goalkeeper(blackout, players) is False  # outfield player doesn't count
    blackout.roster.append(901)
    assert team_has_goalkeeper(blackout, players) is True


def test_captain_who_is_gk_counts_toward_the_one_gk_limit() -> None:
    """No canonical captain is currently a GK, so this uses a synthetic
    captain to prove the rule is derived from roster/position data (any
    roster member), not special-cased to purchased players only."""
    captain = Player(
        id=1,
        full_name="GK Captain",
        short_name="GK Captain",
        position=Position.GK,
        overall_rating=85,
        is_captain=True,
        assigned_team="Blackout FC",
        auction_eligible=False,
        auction_status=PlayerAuctionStatus.PRE_ASSIGNED,
    )
    other_gk = Player(id=901, full_name="Auction Keeper", short_name="AK", position=Position.GK, overall_rating=80)
    players = [captain, other_gk]

    blackout = Team(
        id=1, name="Blackout FC", short_name="Samin", captain_player_id=1, captain_name="GK Captain"
    )
    teams = [blackout] + [team for team in _teams() if team.name != "Blackout FC"]
    auction = Auction(queue=[901], status=AuctionStatus.IN_PROGRESS)

    assert team_has_goalkeeper(blackout, players) is True
    with pytest.raises(AuctionTransactionError, match="already has a goalkeeper"):
        process_sale(auction, players, teams, winning_team="Blackout FC", sale_price=5)


def test_non_gk_purchases_are_unaffected_by_gk_rule() -> None:
    players = _players([(901, "Keeper", Position.GK), (902, "Defender", Position.DEF)])
    teams = _teams()
    auction = Auction(queue=[901, 902], status=AuctionStatus.IN_PROGRESS)
    process_sale(auction, players, teams, winning_team="Blackout FC", sale_price=5)

    result = process_sale(auction, players, teams, winning_team="Blackout FC", sale_price=3)
    assert result.outcome == "SOLD"
    assert team_by_name(teams, "Blackout FC").roster_size == 3


def test_exactly_one_gk_final_team_state_can_be_validated() -> None:
    players = load_players()
    teams = load_teams()
    from services.randomization_service import create_auction

    auction = create_auction(players, seed=1)
    guard = 0
    while auction.status not in (AuctionStatus.COMPLETE, AuctionStatus.BLOCKED) and guard < 200:
        guard += 1
        current = get_current_player(auction, players)
        eligible = [t for t in teams if team_can_bid_for_player(t, current, players)]
        if not eligible:
            process_unsold(auction, players, teams)
        else:
            team = min(eligible, key=lambda t: t.roster_size)
            process_sale(auction, players, teams, winning_team=team.id, sale_price=1)

    assert auction.status == AuctionStatus.COMPLETE
    for team in teams:
        gk_count = sum(1 for pid in team.roster if next(p for p in players if p.id == pid).position == Position.GK)
        assert gk_count == 1


def test_setup_validation_catches_insufficient_gk_coverage() -> None:
    """Synthetic data with only 3 auction-eligible GKs for 4 teams (none
    of whose captains are GK) must fail the GK-coverage check."""
    players = load_players()
    teams = load_teams()
    # Nabil (id 11) is currently GK; flip him to DEF so only 3 GKs remain.
    nabil = next(p for p in players if p.id == 11)
    nabil.position = Position.DEF

    result = validate_setup(players, teams)
    failed_labels = {check.label for check in result.failures}
    assert "Sufficient goalkeeper coverage" in failed_labels
    assert result.is_ready is False


# ============================================================
# FULL TEAM ELIGIBILITY
# ============================================================


def _team_at_size(base_team: Team, size: int) -> Team:
    extra_needed = size - base_team.roster_size
    extra_ids = [800 + i for i in range(extra_needed)]
    spending = extra_needed
    return Team(
        id=base_team.id,
        name=base_team.name,
        short_name=base_team.short_name,
        captain_player_id=base_team.captain_player_id,
        captain_name=base_team.captain_name,
        starting_budget=base_team.starting_budget,
        remaining_budget=base_team.starting_budget - spending,
        max_squad_size=base_team.max_squad_size,
        roster=base_team.roster + extra_ids,
        auction_spending=spending,
        players_purchased=base_team.players_purchased + extra_needed,
    )


def test_team_at_7_of_8_can_buy_one_more_player() -> None:
    players = _players([(901, "P1", Position.DEF)])
    teams = _teams()
    teams[0] = _team_at_size(teams[0], 7)
    auction = Auction(queue=[901], status=AuctionStatus.IN_PROGRESS)
    result = process_sale(auction, players, teams, winning_team=teams[0].id, sale_price=1)
    assert result.outcome == "SOLD"
    assert teams[0].roster_size == 8


def test_team_becomes_8_of_8_after_successful_purchase() -> None:
    players = _players([(901, "P1", Position.DEF)])
    teams = _teams()
    teams[0] = _team_at_size(teams[0], 7)
    auction = Auction(queue=[901], status=AuctionStatus.IN_PROGRESS)
    process_sale(auction, players, teams, winning_team=teams[0].id, sale_price=1)
    assert teams[0].roster_size == teams[0].max_squad_size


def test_team_at_8_of_8_cannot_buy_another_player() -> None:
    players = _players([(901, "P1", Position.DEF)])
    teams = _teams()
    teams[0] = _team_at_size(teams[0], 8)
    auction = Auction(queue=[901], status=AuctionStatus.IN_PROGRESS)
    with pytest.raises(AuctionTransactionError, match="roster is full"):
        process_sale(auction, players, teams, winning_team=teams[0].id, sale_price=1)


def test_failed_full_team_purchase_changes_nothing() -> None:
    players = _players([(901, "P1", Position.DEF)])
    teams = _teams()
    teams[0] = _team_at_size(teams[0], 8)
    auction = Auction(queue=[901], status=AuctionStatus.IN_PROGRESS)
    budget_before = teams[0].remaining_budget
    roster_before = list(teams[0].roster)

    with pytest.raises(AuctionTransactionError):
        process_sale(auction, players, teams, winning_team=teams[0].id, sale_price=1)

    assert teams[0].remaining_budget == budget_before
    assert teams[0].roster == roster_before


def test_full_team_cannot_be_selected_for_a_reauction_player() -> None:
    players = _players([(901, "P1", Position.DEF)])
    teams = _teams()
    teams[0] = _team_at_size(teams[0], 8)
    current = players[0]
    assert team_can_bid_for_player(teams[0], current, players) is False


# ============================================================
# BLOCKED STATE
# ============================================================


def test_blocked_state_is_detected_when_no_team_can_take_the_remaining_gk() -> None:
    """All 4 teams already have a GK; one GK player remains unsold ->
    no legal destination exists anywhere -> BLOCKED, not an infinite loop."""
    players = _players([(901, "Stuck Keeper", Position.GK)])
    teams = _teams()
    for i in range(4):
        teams[i] = _team_at_size(teams[i], teams[i].roster_size)  # keep size, just copy
        teams[i].roster.append(900 - i)  # fake GK ids not in `players`, but we set has_gk via a real GK below

    # Give each team a real GK on its roster so team_has_goalkeeper is True.
    gk_players = [Player(id=700 + i, full_name=f"Team GK {i}", short_name=f"GK{i}", position=Position.GK, overall_rating=75) for i in range(4)]
    for i, team in enumerate(teams):
        team.roster = [pid for pid in team.roster if pid != 900 - i] + [700 + i]
    all_players = players + gk_players

    auction = Auction(queue=[901], status=AuctionStatus.IN_PROGRESS)
    result = process_unsold(auction, all_players, teams)

    assert result.auction_status == AuctionStatus.BLOCKED
    assert auction.status == AuctionStatus.BLOCKED
    assert result.auction_complete is False


def test_blocked_state_when_all_teams_are_full() -> None:
    players = _players([(901, "P1", Position.DEF)])
    teams = _teams()
    for i in range(4):
        teams[i] = _team_at_size(teams[i], 8)
    auction = Auction(queue=[901], status=AuctionStatus.IN_PROGRESS)

    result = process_unsold(auction, players, teams)
    assert result.auction_status == AuctionStatus.BLOCKED
    assert auction.status == AuctionStatus.BLOCKED


def test_no_transaction_allowed_once_blocked() -> None:
    players = _players([(901, "P1", Position.DEF)])
    teams = _teams()
    for i in range(4):
        teams[i] = _team_at_size(teams[i], 8)
    auction = Auction(queue=[901], status=AuctionStatus.IN_PROGRESS)
    process_unsold(auction, players, teams)
    assert auction.status == AuctionStatus.BLOCKED

    with pytest.raises(AuctionTransactionError, match="blocked"):
        process_unsold(auction, players, teams)


def test_blocked_detection_does_not_loop_forever() -> None:
    """A single _finish_transaction call either starts exactly one new
    round or sets BLOCKED — it never recurses/loops internally."""
    players = _players([(901, "P1", Position.DEF)])
    teams = _teams()
    for i in range(4):
        teams[i] = _team_at_size(teams[i], 8)
    auction = Auction(queue=[901], status=AuctionStatus.IN_PROGRESS)
    result = process_unsold(auction, players, teams)
    assert result.auction_status == AuctionStatus.BLOCKED
    # round_number must NOT have advanced past 1 — no new round was (or
    # could be) started once blocked.
    assert auction.round_number == 1
