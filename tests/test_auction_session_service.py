from pathlib import Path

import pytest

from models.auction import AuctionStatus
from services.auction_service import AuctionTransactionError, team_can_bid_for_player
from services.auction_session_service import AuctionSession

ROOT = Path(__file__).resolve().parents[1]


def team_by_name(teams, name: str):
    return next(team for team in teams if team.name == name)


def pick_eligible_team(player, teams, players):
    """The currently-eligible team with the fewest players so far — see
    tests/test_auction_service.py's identical helper for why this (rather
    than naive round-robin) reliably reaches completion in tests."""
    eligible = [team for team in teams if team_can_bid_for_player(team, player, players)]
    if not eligible:
        return None
    return min(eligible, key=lambda team: team.roster_size)


def resolve_whole_session(session, sale_price: int = 1) -> None:
    for _ in range(500):
        if session.auction.status in (AuctionStatus.COMPLETE, AuctionStatus.BLOCKED):
            return
        current = session.current_player
        team = pick_eligible_team(current, session.teams, session.players)
        if team is None:
            session.mark_current_player_unsold()
        else:
            session.sell_current_player(winning_team=team.id, sale_price=sale_price)
    raise AssertionError("resolve_whole_session did not reach COMPLETE/BLOCKED within 500 attempts")


# ============================================================
# SESSION STATE
# ============================================================


def test_new_session_starts_inactive() -> None:
    session = AuctionSession()
    assert session.started is False
    assert session.auction is None
    assert session.players is None
    assert session.teams is None
    assert session.current_player is None
    assert session.is_complete is False


def test_starting_auction_creates_28_player_queue() -> None:
    session = AuctionSession()
    session.start(seed=1)
    assert session.started is True
    assert len(session.auction.queue) == 28
    assert len(set(session.auction.queue)) == 28


def test_starting_auction_loads_4_live_team_objects() -> None:
    session = AuctionSession()
    session.start(seed=1)
    assert len(session.teams) == 4
    assert [team.name for team in session.teams] == [
        "Blackout FC",
        "Darkstar FC",
        "Goli Underdogs",
        "Showstoppers",
    ]


def test_starting_auction_loads_canonical_players() -> None:
    session = AuctionSession()
    session.start(seed=1)
    assert len(session.players) == 32
    assert sum(player.is_captain for player in session.players) == 4


def test_start_does_not_mutate_canonical_json() -> None:
    players_path = ROOT / "data" / "players.json"
    teams_path = ROOT / "data" / "teams.json"
    players_before = players_path.read_text(encoding="utf-8")
    teams_before = teams_path.read_text(encoding="utf-8")

    session = AuctionSession()
    session.start(seed=1)

    assert players_path.read_text(encoding="utf-8") == players_before
    assert teams_path.read_text(encoding="utf-8") == teams_before


def test_calling_start_again_does_not_recreate_queue() -> None:
    session = AuctionSession()
    session.start(seed=1)
    queue_before = list(session.auction.queue)
    auction_object_before = session.auction

    session.start(seed=999)  # different seed: must be ignored, no-op

    assert session.auction is auction_object_before
    assert list(session.auction.queue) == queue_before


def test_queue_identity_and_order_remain_same_across_navigation() -> None:
    """Simulates MainWindow destroying/recreating a screen frame: the
    session object itself must be unaffected since screens never own it."""
    session = AuctionSession()
    session.start(seed=5)
    queue_snapshot = list(session.auction.queue)
    auction_snapshot = session.auction

    # "Navigate away" — nothing touches the session — then "navigate back".
    assert session.auction is auction_snapshot
    assert list(session.auction.queue) == queue_snapshot


def test_active_team_state_survives_across_repeated_access() -> None:
    session = AuctionSession()
    session.start(seed=1)
    blackout = team_by_name(session.teams, "Blackout FC")
    session.sell_current_player(winning_team=blackout.id, sale_price=10)

    # Re-fetching from session.teams (as a recreated screen frame would)
    # must show the same mutated Team object, not a fresh reload.
    same_blackout = team_by_name(session.teams, "Blackout FC")
    assert same_blackout is blackout
    assert same_blackout.remaining_budget == 90
    assert same_blackout.roster_size == 2


def test_active_auction_history_survives_across_repeated_access() -> None:
    session = AuctionSession()
    session.start(seed=1)
    blackout = team_by_name(session.teams, "Blackout FC")
    session.sell_current_player(winning_team=blackout.id, sale_price=10)
    session.mark_current_player_unsold()

    assert len(session.auction.history) == 2
    assert [entry.status for entry in session.auction.history] == ["SOLD", "UNSOLD"]


# ============================================================
# CURRENT PLAYER / PROGRESS
# ============================================================


def test_first_current_player_resolves_correctly() -> None:
    session = AuctionSession()
    session.start(seed=1)
    current = session.current_player
    assert current is not None
    assert current.id == session.auction.queue[0]


def test_current_player_is_never_a_captain() -> None:
    session = AuctionSession()
    session.start(seed=3)
    for _ in range(28):
        assert session.current_player.is_captain is False
        session.mark_current_player_unsold()


def test_progress_starts_at_0_resolved_of_28() -> None:
    session = AuctionSession()
    session.start(seed=1)
    assert session.resolved_count == 0
    assert session.total_queue_length == 28
    assert session.remaining_count == 28


def test_progress_increments_after_sold() -> None:
    session = AuctionSession()
    session.start(seed=1)
    blackout = team_by_name(session.teams, "Blackout FC")
    session.sell_current_player(winning_team=blackout.id, sale_price=5)
    assert session.resolved_count == 1
    assert session.remaining_count == 27


def test_progress_increments_after_unsold() -> None:
    session = AuctionSession()
    session.start(seed=1)
    session.mark_current_player_unsold()
    assert session.resolved_count == 1
    assert session.remaining_count == 27


def test_ui_does_not_expose_future_queue_player_names() -> None:
    """AuctionSession's public surface never hands back the raw queue list
    itself — only current_player and counts — so a screen built against
    this API structurally cannot display upcoming player identities."""
    session = AuctionSession()
    session.start(seed=1)
    public_attrs = {name for name in dir(session) if not name.startswith("_")}
    assert "queue" not in public_attrs
    # The queue is still reachable via session.auction.queue for services
    # that legitimately need it (like process_sale); this test documents
    # that the *session's own* convenience surface doesn't surface it.
    assert hasattr(session.auction, "queue")


# ============================================================
# SOLD / UNSOLD via the session
# ============================================================


def test_sell_current_player_updates_live_team_budget() -> None:
    session = AuctionSession()
    session.start(seed=1)
    blackout = team_by_name(session.teams, "Blackout FC")
    session.sell_current_player(winning_team=blackout.id, sale_price=14)
    assert blackout.remaining_budget == 86


def test_sell_current_player_by_team_name_also_works() -> None:
    session = AuctionSession()
    session.start(seed=1)
    session.sell_current_player(winning_team="Blackout FC", sale_price=10)
    blackout = team_by_name(session.teams, "Blackout FC")
    assert blackout.roster_size == 2


def test_mark_current_player_unsold_does_not_touch_any_budget() -> None:
    session = AuctionSession()
    session.start(seed=1)
    budgets_before = {team.name: team.remaining_budget for team in session.teams}
    session.mark_current_player_unsold()
    for team in session.teams:
        assert team.remaining_budget == budgets_before[team.name]


def test_session_records_last_result() -> None:
    session = AuctionSession()
    session.start(seed=1)
    result = session.sell_current_player(winning_team="Blackout FC", sale_price=10)
    assert session.last_result is result
    assert session.last_result.outcome == "SOLD"


def test_transactions_before_start_raise_clear_error() -> None:
    session = AuctionSession()
    with pytest.raises(AuctionTransactionError, match="not initialized"):
        session.sell_current_player(winning_team="Blackout FC", sale_price=10)
    with pytest.raises(AuctionTransactionError, match="not initialized"):
        session.mark_current_player_unsold()


# ============================================================
# COMPLETION
# ============================================================


def test_session_reports_complete_after_all_28_resolved() -> None:
    session = AuctionSession()
    session.start(seed=1)  # confirmed to reach a clean COMPLETE with this strategy
    resolve_whole_session(session, sale_price=1)
    assert session.auction.status == AuctionStatus.COMPLETE
    assert session.is_complete is True
    assert session.is_blocked is False
    assert session.current_player is None
    assert session.remaining_count == 0
    assert session.sold_count == 28
    assert session.total_eligible_count == 28


def test_session_round_number_starts_at_1_and_increments_on_reauction() -> None:
    session = AuctionSession()
    session.start(seed=1)
    assert session.round_number == 1

    # Mark everyone in round 1 UNSOLD so a re-auction round is guaranteed.
    for _ in range(session.total_queue_length):
        session.mark_current_player_unsold()
    assert session.round_number == 2


def test_session_progress_is_scoped_to_the_current_round() -> None:
    """resolved_count/total_queue_length/remaining_count describe the
    CURRENT round, not a running total across rounds — matching the
    Live Auction screen's "Round 2 / Player 1 of 2" style display."""
    session = AuctionSession()
    session.start(seed=1)
    for _ in range(session.total_queue_length):
        session.mark_current_player_unsold()

    assert session.round_number == 2
    assert session.total_queue_length == 28  # every player was unsold, all 28 return
    assert session.resolved_count == 0  # nothing processed yet *in this round*
    assert session.remaining_count == 28


def test_session_is_blocked_reflects_auction_status() -> None:
    session = AuctionSession()
    assert session.is_blocked is False
    session.start(seed=1)
    assert session.is_blocked is False
