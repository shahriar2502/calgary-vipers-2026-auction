import copy
from pathlib import Path

import pytest

from models.auction import Auction, AuctionHistoryEntry, AuctionStatus
from models.player import Player, PlayerAuctionStatus, Position
from models.team import Team
from services.auction_service import (
    AuctionTransactionError,
    eligible_for_reauction,
    get_current_player,
    process_sale,
    process_unsold,
    resolved_player_ids,
    sold_player_ids,
    team_can_bid_for_player,
    team_has_goalkeeper,
)
from services.player_service import load_players, load_teams
from services.randomization_service import create_auction

ROOT = Path(__file__).resolve().parents[1]


def build_state(seed: int = 1):
    """Fresh, independent Player/Team/Auction objects for one test."""
    players = load_players()
    teams = load_teams()
    auction = create_auction(players, seed=seed)
    return auction, players, teams


def team_by_name(teams: list[Team], name: str) -> Team:
    return next(team for team in teams if team.name == name)


def player_by_id(players: list[Player], player_id: int) -> Player:
    return next(player for player in players if player.id == player_id)


def pick_eligible_team(player: Player, teams: list[Team], players: list[Player]) -> Team | None:
    """The currently-eligible team with the fewest players so far (roster
    space + at-most-one-GK), or None if no team is eligible right now.

    Spreading purchases evenly like this (rather than always picking the
    first eligible team) is what an organizer distributing a limited pool
    of GKs across teams would naturally do, and is enough to reliably
    reach a clean completion on the canonical data in tests — a purely
    greedy "first eligible" strategy can legitimately paint itself into a
    dead end (fill a team up before it gets its mandatory GK), which is
    correct BLOCKED-detection behavior, not a bug; see the dedicated
    blocked-state tests for that scenario specifically.
    """
    eligible = [team for team in teams if team_can_bid_for_player(team, player, players)]
    if not eligible:
        return None
    return min(eligible, key=lambda team: team.roster_size)


def resolve_whole_queue(auction: Auction, players: list[Player], teams: list[Team], sale_price: int = 1) -> None:
    """Drive `auction` to completion or a blocked state, selling every
    player to the first team that can legally take them and marking
    UNSOLD only when no team currently can. A safety cap avoids an
    infinite loop if production code regresses."""
    for _ in range(500):
        if auction.status in (AuctionStatus.COMPLETE, AuctionStatus.BLOCKED):
            return
        current = get_current_player(auction, players)
        team = pick_eligible_team(current, teams, players)
        if team is None:
            process_unsold(auction, players, teams)
        else:
            process_sale(auction, players, teams, winning_team=team.id, sale_price=sale_price)
    raise AssertionError("resolve_whole_queue did not reach COMPLETE/BLOCKED within 500 attempts")


def full_roster_team(team: Team) -> Team:
    """A copy of `team` with its roster already filled to max_squad_size."""
    extra_needed = team.max_squad_size - team.roster_size
    extra_ids = [900 + i for i in range(extra_needed)]
    spending = extra_needed  # 1M per extra player, well within budget
    return Team(
        id=team.id,
        name=team.name,
        short_name=team.short_name,
        captain_player_id=team.captain_player_id,
        captain_name=team.captain_name,
        starting_budget=team.starting_budget,
        remaining_budget=team.starting_budget - spending,
        max_squad_size=team.max_squad_size,
        roster=team.roster + extra_ids,
        auction_spending=spending,
        players_purchased=extra_needed,
    )


# ============================================================
# SOLD SUCCESS
# ============================================================


def test_valid_sale_succeeds() -> None:
    auction, players, teams = build_state()
    current = get_current_player(auction, players)
    result = process_sale(auction, players, teams, winning_team="Blackout FC", sale_price=10)
    assert result.outcome == "SOLD"
    assert result.player.id == current.id


def test_sold_player_added_to_winning_team_roster() -> None:
    auction, players, teams = build_state()
    current = get_current_player(auction, players)
    process_sale(auction, players, teams, winning_team="Blackout FC", sale_price=10)
    blackout = team_by_name(teams, "Blackout FC")
    assert current.id in blackout.roster


def test_sold_deducts_correct_price() -> None:
    auction, players, teams = build_state()
    process_sale(auction, players, teams, winning_team="Blackout FC", sale_price=14)
    blackout = team_by_name(teams, "Blackout FC")
    assert blackout.remaining_budget == 86


def test_sold_increases_auction_spending() -> None:
    auction, players, teams = build_state()
    process_sale(auction, players, teams, winning_team="Blackout FC", sale_price=14)
    blackout = team_by_name(teams, "Blackout FC")
    assert blackout.auction_spending == 14
    assert blackout.players_purchased == 1


def test_sold_creates_history_entry() -> None:
    auction, players, teams = build_state()
    process_sale(auction, players, teams, winning_team="Blackout FC", sale_price=10)
    assert len(auction.history) == 1


def test_sold_history_contains_correct_player_team_price() -> None:
    auction, players, teams = build_state()
    current = get_current_player(auction, players)
    process_sale(auction, players, teams, winning_team="Blackout FC", sale_price=10)
    entry = auction.history[0]
    assert entry.player_id == current.id
    assert entry.status == "SOLD"
    assert entry.team == "Blackout FC"
    assert entry.sold_price == 10
    assert entry.auction_sequence == 1


def test_sold_advances_queue_exactly_once() -> None:
    auction, players, teams = build_state()
    position_before = auction.current_queue_position
    process_sale(auction, players, teams, winning_team="Blackout FC", sale_price=10)
    assert auction.current_queue_position == position_before + 1


def test_sold_next_current_player_is_correct() -> None:
    auction, players, teams = build_state()
    expected_next_id = auction.queue[1]
    result = process_sale(auction, players, teams, winning_team="Blackout FC", sale_price=10)
    assert result.next_player_id == expected_next_id
    assert auction.current_player_id == expected_next_id


# ============================================================
# SALE PRICE
# ============================================================


def test_zero_sale_price_rejected() -> None:
    auction, players, teams = build_state()
    with pytest.raises(AuctionTransactionError, match="positive integer"):
        process_sale(auction, players, teams, winning_team="Blackout FC", sale_price=0)


def test_negative_sale_price_rejected() -> None:
    auction, players, teams = build_state()
    with pytest.raises(AuctionTransactionError, match="positive integer"):
        process_sale(auction, players, teams, winning_team="Blackout FC", sale_price=-5)


def test_float_sale_price_rejected() -> None:
    auction, players, teams = build_state()
    with pytest.raises(AuctionTransactionError, match="positive integer"):
        process_sale(auction, players, teams, winning_team="Blackout FC", sale_price=10.5)


def test_boolean_sale_price_rejected() -> None:
    auction, players, teams = build_state()
    with pytest.raises(AuctionTransactionError, match="positive integer"):
        process_sale(auction, players, teams, winning_team="Blackout FC", sale_price=True)


def test_sale_at_exact_maximum_legal_bid_succeeds() -> None:
    # Pre-Milestone-9 budget reserve fix: a fresh team (roster 1/8, 100M)
    # still needs 7 more players, so it may not spend its entire 100M on
    # the first one — only up to maximum_legal_bid (94M, reserving 6M for
    # the 6 slots still needed after this purchase). See
    # PROJECT_CONTEXT.md's "PRE-M9 BUDGET RESERVE RULE".
    auction, players, teams = build_state()
    blackout = team_by_name(teams, "Blackout FC")
    max_legal = blackout.maximum_legal_bid
    assert max_legal == 94
    result = process_sale(auction, players, teams, winning_team="Blackout FC", sale_price=max_legal)
    assert result.outcome == "SOLD"
    assert blackout.remaining_budget == 100 - max_legal


def test_sale_above_remaining_budget_rejected() -> None:
    auction, players, teams = build_state()
    with pytest.raises(AuctionTransactionError, match="budget"):
        process_sale(auction, players, teams, winning_team="Blackout FC", sale_price=101)


# ============================================================
# SQUAD / ROSTER
# ============================================================


def test_full_team_cannot_buy() -> None:
    auction, players, teams = build_state()
    teams[0] = full_roster_team(team_by_name(teams, "Blackout FC"))
    with pytest.raises(AuctionTransactionError, match="roster is full"):
        process_sale(auction, players, teams, winning_team="Blackout FC", sale_price=5)


def test_failed_full_roster_sale_changes_nothing() -> None:
    auction, players, teams = build_state()
    teams[0] = full_roster_team(team_by_name(teams, "Blackout FC"))
    snapshot_team = copy.deepcopy(teams[0])
    snapshot_players = copy.deepcopy(players)
    snapshot_position = auction.current_queue_position

    with pytest.raises(AuctionTransactionError):
        process_sale(auction, players, teams, winning_team="Blackout FC", sale_price=5)

    assert teams[0] == snapshot_team
    assert players == snapshot_players
    assert auction.current_queue_position == snapshot_position
    assert auction.history == []


def test_player_already_on_winning_team_cannot_be_sold_again() -> None:
    auction, players, teams = build_state()
    current = get_current_player(auction, players)
    blackout = team_by_name(teams, "Blackout FC")
    blackout.roster.append(current.id)  # simulate corrupted duplicate state
    with pytest.raises(AuctionTransactionError, match="already assigned"):
        process_sale(auction, players, teams, winning_team="Blackout FC", sale_price=5)


def test_player_already_on_another_team_cannot_be_sold() -> None:
    auction, players, teams = build_state()
    current = get_current_player(auction, players)
    darkstar = team_by_name(teams, "Darkstar FC")
    darkstar.roster.append(current.id)  # simulate corrupted duplicate state
    with pytest.raises(AuctionTransactionError, match="already assigned"):
        process_sale(auction, players, teams, winning_team="Blackout FC", sale_price=5)


def test_failed_duplicate_assignment_changes_nothing() -> None:
    auction, players, teams = build_state()
    current = get_current_player(auction, players)
    darkstar = team_by_name(teams, "Darkstar FC")
    darkstar.roster.append(current.id)
    snapshot_teams = copy.deepcopy(teams)
    snapshot_players = copy.deepcopy(players)
    snapshot_position = auction.current_queue_position

    with pytest.raises(AuctionTransactionError):
        process_sale(auction, players, teams, winning_team="Blackout FC", sale_price=5)

    assert teams == snapshot_teams
    assert players == snapshot_players
    assert auction.current_queue_position == snapshot_position


# ============================================================
# PLAYER / TEAM VALIDATION
# ============================================================


def test_invalid_winning_team_rejected() -> None:
    auction, players, teams = build_state()
    with pytest.raises(AuctionTransactionError, match="Winning team does not exist"):
        process_sale(auction, players, teams, winning_team="Calgary Vipers", sale_price=5)


def test_missing_current_player_rejected() -> None:
    _auction, players, teams = build_state()
    broken_auction = Auction(queue=[999999], current_queue_position=0, status=AuctionStatus.IN_PROGRESS)
    with pytest.raises(AuctionTransactionError, match="could not be found"):
        process_sale(broken_auction, players, teams, winning_team="Blackout FC", sale_price=5)


def test_captain_cannot_be_sold() -> None:
    _auction, players, teams = build_state()
    captain_id = team_by_name(teams, "Blackout FC").captain_player_id
    broken_auction = Auction(queue=[captain_id, 2, 4], status=AuctionStatus.IN_PROGRESS)
    with pytest.raises(AuctionTransactionError, match="Captains cannot be auctioned"):
        process_sale(broken_auction, players, teams, winning_team="Darkstar FC", sale_price=5)


def test_non_auction_eligible_player_cannot_be_sold() -> None:
    _auction, players, teams = build_state()
    ineligible = Player(
        id=500,
        full_name="Ineligible Player",
        short_name="Ineligible",
        position=players[1].position,
        overall_rating=70,
        is_captain=False,
        auction_eligible=False,
        auction_status=PlayerAuctionStatus.UNSOLD,
    )
    players_with_ineligible = players + [ineligible]
    broken_auction = Auction(queue=[500], status=AuctionStatus.IN_PROGRESS)
    with pytest.raises(AuctionTransactionError, match="not auction-eligible"):
        process_sale(broken_auction, players_with_ineligible, teams, winning_team="Blackout FC", sale_price=5)


def test_already_resolved_player_cannot_be_sold() -> None:
    """A player with a SOLD history entry is permanently done — this is
    the ONLY permanent resolution; see test_unsold_history_does_not_block
    _a_later_sale below for the (deliberately different) UNSOLD case."""
    auction, players, teams = build_state()
    current = get_current_player(auction, players)
    auction.history.append(
        AuctionHistoryEntry(
            auction_sequence=1,
            player_id=current.id,
            player_name=current.full_name,
            position=current.position.value,
            overall_rating=current.overall_rating,
            base_price=None,
            status="SOLD",
            team="Blackout FC",
            sold_price=5,
        )
    )
    with pytest.raises(AuctionTransactionError, match="already been resolved"):
        process_sale(auction, players, teams, winning_team="Darkstar FC", sale_price=5)


def test_unsold_history_does_not_block_a_later_sale() -> None:
    """UNSOLD is never permanent: a player who already has an UNSOLD
    history entry (simulating an earlier round) must still be sellable."""
    auction, players, teams = build_state()
    current = get_current_player(auction, players)
    auction.history.append(
        AuctionHistoryEntry(
            auction_sequence=1,
            player_id=current.id,
            player_name=current.full_name,
            position=current.position.value,
            overall_rating=current.overall_rating,
            base_price=None,
            status="UNSOLD",
        )
    )
    result = process_sale(auction, players, teams, winning_team="Blackout FC", sale_price=5)
    assert result.outcome == "SOLD"
    assert result.player.id == current.id


# ============================================================
# UNSOLD
# ============================================================


def test_valid_unsold_succeeds() -> None:
    auction, players, teams = build_state()
    current = get_current_player(auction, players)
    result = process_unsold(auction, players, teams)
    assert result.outcome == "UNSOLD"
    assert result.player.id == current.id
    assert result.team is None
    assert result.sale_price is None


def test_unsold_writes_correct_history() -> None:
    auction, players, teams = build_state()
    current = get_current_player(auction, players)
    process_unsold(auction, players, teams)
    entry = auction.history[0]
    assert entry.player_id == current.id
    assert entry.status == "UNSOLD"
    assert entry.team is None
    assert entry.sold_price is None


# ============================================================
# HISTORY ROUND NUMBER (Auction History screen support)
# ============================================================


def test_sold_history_entry_records_round_number() -> None:
    auction, players, teams = build_state()
    process_sale(auction, players, teams, winning_team="Blackout FC", sale_price=10)
    assert auction.history[0].round_number == 1


def test_unsold_history_entry_records_round_number() -> None:
    auction, players, teams = build_state()
    process_unsold(auction, players, teams)
    assert auction.history[0].round_number == 1


def _force_one_unsold_then_resolve_round_one(auction: Auction, players: list[Player], teams: list[Team]) -> int:
    """Force one round-1 player UNSOLD (deterministically guaranteeing a
    re-auction round, rather than hoping a given seed's queue naturally
    produces one via `pick_eligible_team`'s conflict-avoiding balancing —
    which, empirically, resolves entirely within round 1 for a wide range
    of seeds), then sell every other round-1 player to whichever team can
    legally take them at a uniform 1M. Returns the forced player's id.

    Deliberately forces the first *non-goalkeeper* player it sees: with a
    uniform 1M sale price, at most 27 of the other teams' combined 28
    purchase slots get filled by the remaining round-1 sales, so by the
    pigeonhole principle at least one team is guaranteed to still have
    roster room (and, since the forced player isn't a GK, the one-GK rule
    can never be the reason it's excluded) once round 2 begins — making
    this helper reliable regardless of which seed calls it.
    """
    forced_unsold_player_id: int | None = None
    while auction.round_number == 1 and not auction.is_complete and auction.status != AuctionStatus.BLOCKED:
        current = get_current_player(auction, players)
        if forced_unsold_player_id is None and current.position != Position.GK:
            forced_unsold_player_id = current.id
            process_unsold(auction, players, teams)
            continue
        team = pick_eligible_team(current, teams, players)
        if team is None:
            process_unsold(auction, players, teams)
        else:
            process_sale(auction, players, teams, winning_team=team.id, sale_price=1)
    assert forced_unsold_player_id is not None
    return forced_unsold_player_id


def test_reauction_round_history_entries_record_the_new_round_number() -> None:
    """Confirm a transaction processed after round 1 exhausts is stamped
    with the round it actually happened in, not round 1."""
    auction, players, teams = build_state()
    _force_one_unsold_then_resolve_round_one(auction, players, teams)
    assert auction.round_number == 2
    history_length_after_round_one = len(auction.history)

    current = get_current_player(auction, players)
    team = pick_eligible_team(current, teams, players)
    assert team is not None  # guaranteed by the pigeonhole argument above
    process_sale(auction, players, teams, winning_team=team.id, sale_price=1)

    latest_entry = auction.history[-1]
    assert latest_entry.round_number == 2

    round_one_entries = [entry for entry in auction.history if entry.round_number == 1]
    assert len(round_one_entries) == history_length_after_round_one


def test_history_preserves_every_attempt_for_a_player_marked_unsold_then_sold() -> None:
    """A player UNSOLD in round 1 and SOLD in round 2 must appear as two
    independent history entries, not one collapsed record — this is what
    the Auction History screen's re-auction behavior depends on (see
    services/auction_history_service.py)."""
    auction, players, teams = build_state()
    forced_unsold_player_id = _force_one_unsold_then_resolve_round_one(auction, players, teams)
    assert auction.round_number == 2

    # Round 2's queue may contain other round-1 leftovers too, not only our
    # forced player — resolve until we specifically reach and sell it.
    for _ in range(100):
        current = get_current_player(auction, players)
        if current.id == forced_unsold_player_id:
            team = pick_eligible_team(current, teams, players)
            assert team is not None  # guaranteed by the pigeonhole argument above
            process_sale(auction, players, teams, winning_team=team.id, sale_price=1)
            break
        team = pick_eligible_team(current, teams, players)
        if team is None:
            process_unsold(auction, players, teams)
        else:
            process_sale(auction, players, teams, winning_team=team.id, sale_price=1)
    else:
        raise AssertionError("Did not reach the forced-unsold player within 100 attempts")

    entries_for_player = [entry for entry in auction.history if entry.player_id == forced_unsold_player_id]
    assert [entry.status for entry in entries_for_player] == ["UNSOLD", "SOLD"]
    assert entries_for_player[0].round_number == 1
    assert entries_for_player[1].round_number >= 2


def test_unsold_does_not_change_any_team_budget() -> None:
    auction, players, teams = build_state()
    snapshot = copy.deepcopy(teams)
    process_unsold(auction, players, teams)
    for before, after in zip(snapshot, teams):
        assert before.remaining_budget == after.remaining_budget


def test_unsold_does_not_change_any_roster() -> None:
    auction, players, teams = build_state()
    snapshot = copy.deepcopy(teams)
    process_unsold(auction, players, teams)
    for before, after in zip(snapshot, teams):
        assert before.roster == after.roster


def test_unsold_does_not_change_auction_spending() -> None:
    auction, players, teams = build_state()
    snapshot = copy.deepcopy(teams)
    process_unsold(auction, players, teams)
    for before, after in zip(snapshot, teams):
        assert before.auction_spending == after.auction_spending
        assert before.players_purchased == after.players_purchased


def test_unsold_advances_exactly_once() -> None:
    auction, players, teams = build_state()
    position_before = auction.current_queue_position
    process_unsold(auction, players, teams)
    assert auction.current_queue_position == position_before + 1


def test_already_resolved_player_cannot_be_marked_unsold_again() -> None:
    """A player with a SOLD history entry is permanently done — cannot be
    revisited via UNSOLD either."""
    auction, players, teams = build_state()
    current = get_current_player(auction, players)
    auction.history.append(
        AuctionHistoryEntry(
            auction_sequence=1,
            player_id=current.id,
            player_name=current.full_name,
            position=current.position.value,
            overall_rating=current.overall_rating,
            base_price=None,
            status="SOLD",
            team="Blackout FC",
            sold_price=5,
        )
    )
    with pytest.raises(AuctionTransactionError, match="already been resolved"):
        process_unsold(auction, players, teams)


def test_unsold_history_does_not_block_being_marked_unsold_again() -> None:
    """UNSOLD is never permanent: marking an already-UNSOLD player UNSOLD
    again (simulating a second unsuccessful round) must still succeed."""
    auction, players, teams = build_state()
    current = get_current_player(auction, players)
    auction.history.append(
        AuctionHistoryEntry(
            auction_sequence=1,
            player_id=current.id,
            player_name=current.full_name,
            position=current.position.value,
            overall_rating=current.overall_rating,
            base_price=None,
            status="UNSOLD",
        )
    )
    result = process_unsold(auction, players, teams)
    assert result.outcome == "UNSOLD"
    assert result.player.id == current.id


# ============================================================
# QUEUE / COMPLETION
# ============================================================


def test_failed_transaction_does_not_advance_queue() -> None:
    auction, players, teams = build_state()
    position_before = auction.current_queue_position
    with pytest.raises(AuctionTransactionError):
        process_sale(auction, players, teams, winning_team="Blackout FC", sale_price=0)
    assert auction.current_queue_position == position_before
    assert auction.history == []


def test_successful_transaction_advances_exactly_one_position() -> None:
    auction, players, teams = build_state()
    position_before = auction.current_queue_position
    process_sale(auction, players, teams, winning_team="Blackout FC", sale_price=5)
    assert auction.current_queue_position == position_before + 1


def test_mixed_sold_and_unsold_progress_correctly() -> None:
    auction, players, teams = build_state()
    process_sale(auction, players, teams, winning_team="Blackout FC", sale_price=5)
    process_unsold(auction, players, teams)
    process_sale(auction, players, teams, winning_team="Darkstar FC", sale_price=8)

    assert auction.current_queue_position == 3
    assert len(auction.history) == 3
    assert [entry.status for entry in auction.history] == ["SOLD", "UNSOLD", "SOLD"]
    assert [entry.auction_sequence for entry in auction.history] == [1, 2, 3]


def _build_synthetic_players(specs: list[tuple[int, str, Position]]) -> list[Player]:
    """Minimal non-captain, auction-eligible Player objects for isolated
    round/completion tests. Deliberately independent of the full 32-player
    canonical set: completion is computed from ALL auction_eligible
    players, so a small, fully-controlled pool is needed to test
    "final sale completes the auction" without the other ~26 real
    auction-eligible players being counted as still-pending."""
    return [
        Player(id=player_id, full_name=name, short_name=name, position=position, overall_rating=80)
        for player_id, name, position in specs
    ]


def test_final_sold_marks_auction_complete() -> None:
    """"Final" now means every eligible player is actually SOLD — a tiny,
    fully-synthetic 2-player pool keeps this fast and unambiguous, since
    jumping straight to the last queue index (the old approach) no longer
    implies completion once UNSOLD players can return in later rounds."""
    players = _build_synthetic_players([(901, "Player One", Position.DEF), (902, "Player Two", Position.MID)])
    teams = load_teams()
    auction = Auction(queue=[901, 902], status=AuctionStatus.IN_PROGRESS)

    process_sale(auction, players, teams, winning_team="Blackout FC", sale_price=5)
    assert auction.status == AuctionStatus.IN_PROGRESS

    last_result = process_sale(auction, players, teams, winning_team="Darkstar FC", sale_price=5)
    assert auction.is_complete is True
    assert auction.status == AuctionStatus.COMPLETE
    assert last_result.auction_status == AuctionStatus.COMPLETE
    assert last_result.auction_complete is True
    assert last_result.next_player_id is None


def test_final_unsold_in_a_round_triggers_reauction_not_completion() -> None:
    """UNSOLD is never a permanent outcome, so marking the last remaining
    player of a round UNSOLD (while a team could still legally buy them)
    must start a new round containing just that player, not complete the
    auction — matching the milestone's own round-2/round-3 example."""
    players = _build_synthetic_players([(901, "Player One", Position.DEF)])
    teams = load_teams()
    auction = Auction(queue=[901], status=AuctionStatus.IN_PROGRESS, random_seed=5)

    result = process_unsold(auction, players, teams)
    assert result.outcome == "UNSOLD"
    assert result.auction_status == AuctionStatus.IN_PROGRESS
    assert result.auction_complete is False
    assert auction.status == AuctionStatus.IN_PROGRESS
    assert auction.round_number == 2
    assert auction.queue == [901]
    assert auction.current_queue_position == 0


def test_no_transaction_allowed_after_completion() -> None:
    auction, players, teams = build_state()
    auction.current_queue_position = len(auction.queue)
    auction.status = AuctionStatus.COMPLETE

    with pytest.raises(AuctionTransactionError, match="already complete"):
        process_sale(auction, players, teams, winning_team="Blackout FC", sale_price=5)
    with pytest.raises(AuctionTransactionError, match="already complete"):
        process_unsold(auction, players, teams)


def test_all_28_queued_players_can_be_resolved_exactly_once() -> None:
    """Drives the real 28-player queue to completion using a GK/roster-
    aware team pick (naive round-robin can legitimately hit a GK conflict
    and need a re-auction round — see the re-auction test section for
    that behavior specifically); the end state must always be everyone
    sold exactly once, regardless of how many attempts/rounds it took."""
    auction, players, teams = build_state(seed=42)
    assert len(auction.queue) == 28

    resolve_whole_queue(auction, players, teams, sale_price=1)

    assert auction.is_complete is True
    assert auction.status == AuctionStatus.COMPLETE
    assert len(sold_player_ids(auction)) == 28
    assert len(auction.history) >= 28
    for team in teams:
        assert team.roster_size == 8
        assert team.players_purchased == 7


def test_history_length_equals_number_of_successful_resolutions() -> None:
    auction, players, teams = build_state()
    process_sale(auction, players, teams, winning_team="Blackout FC", sale_price=5)
    process_unsold(auction, players, teams)
    with pytest.raises(AuctionTransactionError):
        process_sale(auction, players, teams, winning_team="Blackout FC", sale_price=0)  # fails, no history added
    process_unsold(auction, players, teams)

    assert len(auction.history) == 3


# ============================================================
# ATOMICITY
# ============================================================


def test_insufficient_budget_failure_leaves_everything_unchanged() -> None:
    auction, players, teams = build_state()
    snapshot_teams = copy.deepcopy(teams)
    snapshot_players = copy.deepcopy(players)
    snapshot_position = auction.current_queue_position

    with pytest.raises(AuctionTransactionError, match="budget"):
        process_sale(auction, players, teams, winning_team="Blackout FC", sale_price=101)

    assert teams == snapshot_teams
    assert players == snapshot_players
    assert auction.current_queue_position == snapshot_position
    assert auction.history == []


def test_full_roster_failure_leaves_everything_unchanged() -> None:
    auction, players, teams = build_state()
    teams[0] = full_roster_team(team_by_name(teams, "Blackout FC"))
    other_teams_snapshot = copy.deepcopy(teams[1:])
    full_team_snapshot = copy.deepcopy(teams[0])
    snapshot_players = copy.deepcopy(players)
    snapshot_position = auction.current_queue_position

    with pytest.raises(AuctionTransactionError):
        process_sale(auction, players, teams, winning_team="Blackout FC", sale_price=5)

    assert teams[0] == full_team_snapshot
    assert teams[1:] == other_teams_snapshot
    assert players == snapshot_players
    assert auction.current_queue_position == snapshot_position
    assert auction.history == []


def test_invalid_team_failure_leaves_everything_unchanged() -> None:
    auction, players, teams = build_state()
    snapshot_teams = copy.deepcopy(teams)
    snapshot_players = copy.deepcopy(players)
    snapshot_position = auction.current_queue_position

    with pytest.raises(AuctionTransactionError):
        process_sale(auction, players, teams, winning_team="Nonexistent FC", sale_price=5)

    assert teams == snapshot_teams
    assert players == snapshot_players
    assert auction.current_queue_position == snapshot_position
    assert auction.history == []


def test_invalid_price_failure_leaves_everything_unchanged() -> None:
    auction, players, teams = build_state()
    snapshot_teams = copy.deepcopy(teams)
    snapshot_players = copy.deepcopy(players)
    snapshot_position = auction.current_queue_position

    with pytest.raises(AuctionTransactionError):
        process_sale(auction, players, teams, winning_team="Blackout FC", sale_price=-1)

    assert teams == snapshot_teams
    assert players == snapshot_players
    assert auction.current_queue_position == snapshot_position
    assert auction.history == []


# ============================================================
# REGRESSION
# ============================================================


def test_randomized_queue_order_not_regenerated_by_service() -> None:
    auction, players, teams = build_state(seed=99)
    queue_before = list(auction.queue)
    process_sale(auction, players, teams, winning_team="Blackout FC", sale_price=5)
    process_unsold(auction, players, teams)
    assert list(auction.queue) == queue_before


def test_captains_never_enter_transaction_flow_through_canonical_queue() -> None:
    auction, players, teams = build_state(seed=7)
    captain_ids = {player.id for player in players if player.is_captain}
    assert not (captain_ids & set(auction.queue))

    for _ in range(5):
        process_unsold(auction, players, teams)
    resolved_ids = resolved_player_ids(auction)
    assert not (captain_ids & resolved_ids)


def test_canonical_players_json_unchanged() -> None:
    path = ROOT / "data" / "players.json"
    before = path.read_text(encoding="utf-8")
    auction, players, teams = build_state()
    process_sale(auction, players, teams, winning_team="Blackout FC", sale_price=5)
    process_unsold(auction, players, teams)
    assert path.read_text(encoding="utf-8") == before


def test_canonical_teams_json_unchanged() -> None:
    path = ROOT / "data" / "teams.json"
    before = path.read_text(encoding="utf-8")
    auction, players, teams = build_state()
    process_sale(auction, players, teams, winning_team="Blackout FC", sale_price=5)
    process_unsold(auction, players, teams)
    assert path.read_text(encoding="utf-8") == before


def test_teams_screen_module_still_imports_after_auction_service_changes() -> None:
    from services.team_service import load_team_summaries
    from ui.screens.teams_screen import TeamsScreen  # noqa: F401

    assert len(load_team_summaries()) == 4


def test_players_setup_screen_module_still_imports_after_auction_service_changes() -> None:
    from services.player_service import load_players as _load_players
    from ui.screens.players_setup_screen import PlayersSetupScreen  # noqa: F401

    assert len(_load_players()) == 32


# ============================================================
# INTEGRATION / MANUAL DEMONSTRATION
# ============================================================


def test_integration_demonstration_sell_then_unsold_updates_only_expected_state() -> None:
    """End-to-end walkthrough matching the milestone's manual demonstration."""
    players = load_players()
    teams = load_teams()
    auction = create_auction(players, seed=2026)

    budgets_before = {team.name: team.remaining_budget for team in teams}

    first_player = get_current_player(auction, players)
    sale_result = process_sale(auction, players, teams, winning_team="Blackout FC", sale_price=10)
    assert sale_result.player.id == first_player.id

    blackout = team_by_name(teams, "Blackout FC")
    assert blackout.remaining_budget == 90
    assert blackout.auction_spending == 10
    assert blackout.roster_size == 2

    second_player = get_current_player(auction, players)
    assert second_player is not None
    assert second_player.id != first_player.id
    unsold_result = process_unsold(auction, players, teams)
    assert unsold_result.player.id == second_player.id

    for team in teams:
        if team.name == "Blackout FC":
            continue
        assert team.remaining_budget == budgets_before[team.name]

    assert len(auction.history) == 2
    assert auction.current_queue_position == 2


def test_canonical_json_files_unchanged_after_full_integration_run() -> None:
    players_path = ROOT / "data" / "players.json"
    teams_path = ROOT / "data" / "teams.json"
    players_before = players_path.read_text(encoding="utf-8")
    teams_before = teams_path.read_text(encoding="utf-8")

    players = load_players()
    teams = load_teams()
    auction = create_auction(players, seed=2026)
    process_sale(auction, players, teams, winning_team="Blackout FC", sale_price=10)
    process_unsold(auction, players, teams)

    assert players_path.read_text(encoding="utf-8") == players_before
    assert teams_path.read_text(encoding="utf-8") == teams_before
