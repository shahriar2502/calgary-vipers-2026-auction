"""Milestone 9: persistence, autosave, and session recovery.

All tests redirect `persistence_service`'s save-directory constants to a
temp directory (`isolated_saves` fixture) so nothing here ever touches the
real `saves/` folder. Canonical-data-immutability tests additionally
redirect `services.player_service`'s file paths to a temp fixture rather
than touching the real `data/players.json`/`data/teams.json`.
"""
import copy
import json

import pytest

from models.auction import AuctionStatus
from models.player import Position
from services import player_service
from services import persistence_service as ps
from services.auction_service import AuctionTransactionError, team_can_bid_for_player
from services.auction_session_service import AuctionSession, SessionMode


# ----------------------------------------------------------------------
# Fixtures / helpers
# ----------------------------------------------------------------------


@pytest.fixture
def isolated_saves(tmp_path, monkeypatch):
    """Redirect every persistence_service path constant to a fresh temp
    directory so tests can never read or write the real saves/ folder."""
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


def new_saved_session(mode: SessionMode = SessionMode.MOCK, seed: int = 1, name: str | None = None) -> AuctionSession:
    """A started, autosave-wired session (so `start()` itself performs the
    first save) — the common starting point for most tests below."""
    session = AuctionSession()
    session.autosave = ps.autosave_hook
    session.start(seed=seed, mode=mode, name=name)
    return session


# ----------------------------------------------------------------------
# 1-2: new sessions can be saved
# ----------------------------------------------------------------------


def test_new_mock_session_can_be_saved(isolated_saves) -> None:
    session = new_saved_session(mode=SessionMode.MOCK)
    path = ps.session_save_path(session.mode, session.session_id)
    assert path.is_file()


def test_new_live_session_can_be_saved(isolated_saves) -> None:
    session = new_saved_session(mode=SessionMode.LIVE)
    path = ps.session_save_path(session.mode, session.session_id)
    assert path.is_file()
    assert path == ps.LIVE_ACTIVE_PATH


# ----------------------------------------------------------------------
# 3-15: save-file content
# ----------------------------------------------------------------------


def test_save_file_contains_schema_version(isolated_saves) -> None:
    session = new_saved_session()
    data = json.loads(ps.session_save_path(session.mode, session.session_id).read_text(encoding="utf-8"))
    assert data["schema_version"] == ps.SCHEMA_VERSION


@pytest.mark.parametrize("mode", [SessionMode.MOCK, SessionMode.LIVE])
def test_session_mode_preserved(isolated_saves, mode) -> None:
    session = new_saved_session(mode=mode)
    data = json.loads(ps.session_save_path(session.mode, session.session_id).read_text(encoding="utf-8"))
    assert data["session_mode"] == mode.value


def test_players_snapshot_preserved(isolated_saves) -> None:
    session = new_saved_session()
    restored = ps.load_session(ps.session_save_path(session.mode, session.session_id))
    assert [p.to_dict() for p in restored.players] == [p.to_dict() for p in session.players]


def test_teams_snapshot_preserved(isolated_saves) -> None:
    session = new_saved_session()
    restored = ps.load_session(ps.session_save_path(session.mode, session.session_id))
    assert [t.to_dict() for t in restored.teams] == [t.to_dict() for t in session.teams]


def test_queue_and_current_position_preserved(isolated_saves) -> None:
    session = new_saved_session()
    session.sell_current_player(winning_team="Blackout FC", sale_price=5)
    restored = ps.load_session(ps.session_save_path(session.mode, session.session_id))
    assert restored.auction.queue == session.auction.queue
    assert restored.auction.current_queue_position == session.auction.current_queue_position


def test_round_number_preserved(isolated_saves) -> None:
    session = new_saved_session()
    for _ in range(session.total_queue_length):
        session.mark_current_player_unsold()
    assert session.round_number == 2
    restored = ps.load_session(ps.session_save_path(session.mode, session.session_id))
    assert restored.round_number == 2


def test_history_preserved(isolated_saves) -> None:
    session = new_saved_session()
    session.sell_current_player(winning_team="Blackout FC", sale_price=5)
    session.mark_current_player_unsold()
    restored = ps.load_session(ps.session_save_path(session.mode, session.session_id))
    assert [entry.status for entry in restored.auction.history] == ["SOLD", "UNSOLD"]
    assert len(restored.auction.history) == len(session.auction.history)


def test_budgets_preserved(isolated_saves) -> None:
    session = new_saved_session()
    session.sell_current_player(winning_team="Blackout FC", sale_price=14)
    restored = ps.load_session(ps.session_save_path(session.mode, session.session_id))
    assert team_by_name(restored.teams, "Blackout FC").remaining_budget == 86


def test_rosters_preserved(isolated_saves) -> None:
    session = new_saved_session()
    sold = session.sell_current_player(winning_team="Blackout FC", sale_price=5)
    restored = ps.load_session(ps.session_save_path(session.mode, session.session_id))
    assert sold.player.id in team_by_name(restored.teams, "Blackout FC").roster


def test_spending_preserved(isolated_saves) -> None:
    session = new_saved_session()
    session.sell_current_player(winning_team="Blackout FC", sale_price=17)
    restored = ps.load_session(ps.session_save_path(session.mode, session.session_id))
    blackout = team_by_name(restored.teams, "Blackout FC")
    assert blackout.auction_spending == 17
    assert blackout.players_purchased == 1


def test_sold_ids_state_preserved(isolated_saves) -> None:
    session = new_saved_session()
    result = session.sell_current_player(winning_team="Blackout FC", sale_price=5)
    restored = ps.load_session(ps.session_save_path(session.mode, session.session_id))
    from services.auction_service import sold_player_ids

    assert result.player.id in sold_player_ids(restored.auction)


def test_blocked_state_preserved(isolated_saves) -> None:
    session = new_saved_session()
    current_player_id = session.current_player.id
    # `_finish_transaction`'s blocked check considers *every* still-unsold
    # auction-eligible player (28 of them), not just the one that was just
    # marked UNSOLD — so blocking requires every team to be unable to buy
    # *any* of them, which (with a real, mixed GK/non-GK 28-player pool)
    # only happens when every team is out of roster space or budget, not
    # merely a single GK conflict. Fill 3 teams completely (8/8) and starve
    # the 4th of budget at 7/8 (budget-reserve rule blocks it too), using
    # exactly the 27 real non-captain, non-current player ids available —
    # deterministic, no synthetic/unresolvable ids, no lucky seed needed.
    fillers = iter(p.id for p in session.players if not p.is_captain and p.id != current_player_id)
    for team in session.teams[:3]:
        extra_ids = [next(fillers) for _ in range(7)]
        team.roster.extend(extra_ids)
        team.players_purchased += 7
        team.auction_spending += 7
        team.remaining_budget -= 7
    starved_team = session.teams[3]
    extra_ids = [next(fillers) for _ in range(6)]
    starved_team.roster.extend(extra_ids)
    starved_team.players_purchased += 6
    starved_team.auction_spending = starved_team.remaining_budget  # spend it all
    starved_team.remaining_budget = 0

    session.auction.queue = [current_player_id]
    session.auction.current_queue_position = 0
    session.mark_current_player_unsold()
    assert session.is_blocked is True

    restored = ps.load_session(ps.session_save_path(session.mode, session.session_id))
    assert restored.is_blocked is True


def test_complete_state_preserved(isolated_saves) -> None:
    session = new_saved_session(seed=1)
    resolve_whole_session(session, sale_price=1)
    assert session.is_complete is True
    restored = ps.load_session(ps.session_save_path(session.mode, session.session_id))
    assert restored.is_complete is True
    assert restored.sold_count == 28


# ----------------------------------------------------------------------
# 16-20: restored session works with existing gameplay
# ----------------------------------------------------------------------


def test_load_reconstructs_equivalent_auction_session(isolated_saves) -> None:
    session = new_saved_session()
    session.sell_current_player(winning_team="Blackout FC", sale_price=5)
    restored = ps.load_session(ps.session_save_path(session.mode, session.session_id))

    assert restored.mode == session.mode
    assert restored.session_id == session.session_id
    assert restored.round_number == session.round_number
    assert restored.resolved_count == session.resolved_count
    assert restored.sold_count == session.sold_count
    assert restored.current_player.id == session.current_player.id


def test_resumed_session_can_process_another_sold(isolated_saves) -> None:
    session = new_saved_session()
    session.sell_current_player(winning_team="Blackout FC", sale_price=5)
    restored = ps.load_session(ps.session_save_path(session.mode, session.session_id))

    team = team_by_name(restored.teams, "Darkstar FC")
    result = restored.sell_current_player(winning_team=team.id, sale_price=3)
    assert result.outcome == "SOLD"
    assert restored.sold_count == 2


def test_resumed_session_can_process_unsold_and_reauction(isolated_saves) -> None:
    session = new_saved_session()
    for _ in range(session.total_queue_length):
        session.mark_current_player_unsold()
    assert session.round_number == 2
    restored = ps.load_session(ps.session_save_path(session.mode, session.session_id))
    assert restored.round_number == 2
    assert restored.total_queue_length == 28

    result = restored.mark_current_player_unsold()
    assert result.outcome == "UNSOLD"


def test_resumed_gk_rule_still_works(isolated_saves) -> None:
    session = new_saved_session(seed=1)
    # Drive the queue forward, selling GKs to whichever team is eligible,
    # until we reach a GK player, then confirm a second GK sale to the
    # same team is rejected after resuming.
    from models.player import Position as _Position

    gk_ids = [p.id for p in session.players if p.position == _Position.GK]
    blackout = team_by_name(session.teams, "Blackout FC")
    first_gk = next(p for p in session.players if p.id == gk_ids[0])

    # Force the first GK to the front of the queue for a deterministic setup.
    queue = list(session.auction.queue)
    queue.remove(first_gk.id)
    session.auction.queue = [first_gk.id] + queue
    session.auction.current_queue_position = 0
    session.sell_current_player(winning_team=blackout.id, sale_price=5)

    restored = ps.load_session(ps.session_save_path(session.mode, session.session_id))
    restored_blackout = team_by_name(restored.teams, "Blackout FC")

    second_gk = next(p for p in restored.players if p.position == _Position.GK and p.id != first_gk.id)
    queue2 = list(restored.auction.queue)
    queue2.remove(second_gk.id)
    restored.auction.queue = [second_gk.id] + queue2
    restored.auction.current_queue_position = 0

    with pytest.raises(AuctionTransactionError, match="already has a goalkeeper"):
        restored.sell_current_player(winning_team=restored_blackout.id, sale_price=5)


def test_resumed_budget_reserve_rule_still_works(isolated_saves) -> None:
    session = new_saved_session()
    blackout = team_by_name(session.teams, "Blackout FC")

    def force_next_non_gk_current() -> None:
        queue = list(session.auction.queue)
        remaining = queue[session.auction.current_queue_position :]
        non_gk_id = next(pid for pid in remaining if next(p for p in session.players if p.id == pid).position != Position.GK)
        remaining.remove(non_gk_id)
        session.auction.queue = queue[: session.auction.current_queue_position] + [non_gk_id] + remaining

    # Fill Blackout FC to 5/8 with cheap non-GK purchases, leaving little budget.
    for _ in range(4):
        force_next_non_gk_current()
        session.sell_current_player(winning_team=blackout.id, sale_price=1)
    restored = ps.load_session(ps.session_save_path(session.mode, session.session_id))
    restored_blackout = team_by_name(restored.teams, "Blackout FC")
    assert restored_blackout.roster_size == 5
    assert restored_blackout.maximum_legal_bid == restored_blackout.remaining_budget - 2

    with pytest.raises(AuctionTransactionError):
        restored.sell_current_player(winning_team=restored_blackout.id, sale_price=restored_blackout.remaining_budget)


# ----------------------------------------------------------------------
# 21-24: autosave triggers + atomicity
# ----------------------------------------------------------------------


def test_autosave_after_sold(isolated_saves) -> None:
    session = new_saved_session()
    before = ps.session_save_path(session.mode, session.session_id).read_text(encoding="utf-8")
    session.sell_current_player(winning_team="Blackout FC", sale_price=5)
    after = ps.session_save_path(session.mode, session.session_id).read_text(encoding="utf-8")
    assert after != before
    assert json.loads(after)["auction"]["history"]


def test_autosave_after_unsold(isolated_saves) -> None:
    session = new_saved_session()
    before = ps.session_save_path(session.mode, session.session_id).read_text(encoding="utf-8")
    session.mark_current_player_unsold()
    after = ps.session_save_path(session.mode, session.session_id).read_text(encoding="utf-8")
    assert after != before


def test_autosave_after_round_transition(isolated_saves) -> None:
    session = new_saved_session()
    for _ in range(session.total_queue_length - 1):
        session.mark_current_player_unsold()
    before_round = json.loads(
        ps.session_save_path(session.mode, session.session_id).read_text(encoding="utf-8")
    )["auction"]["round_number"]
    session.mark_current_player_unsold()  # final unsold -> triggers round 2
    after_round = json.loads(
        ps.session_save_path(session.mode, session.session_id).read_text(encoding="utf-8")
    )["auction"]["round_number"]
    assert before_round == 1
    assert after_round == 2


def test_rejected_transaction_does_not_alter_persisted_save(isolated_saves) -> None:
    session = new_saved_session()
    saved_before = ps.session_save_path(session.mode, session.session_id).read_text(encoding="utf-8")

    with pytest.raises(AuctionTransactionError):
        session.sell_current_player(winning_team="Blackout FC", sale_price=0)

    saved_after = ps.session_save_path(session.mode, session.session_id).read_text(encoding="utf-8")
    assert saved_after == saved_before


def test_atomic_save_leaves_previous_file_intact_if_temp_write_fails(isolated_saves, tmp_path) -> None:
    target = tmp_path / "target.json"
    ps.atomic_write_json(target, {"good": "data"})
    original = target.read_text(encoding="utf-8")

    class Unserializable:
        pass

    with pytest.raises(TypeError):
        ps.atomic_write_json(target, {"bad": Unserializable()})

    assert target.read_text(encoding="utf-8") == original


def test_save_session_wraps_serialization_failure_as_persistence_error(isolated_saves, monkeypatch) -> None:
    session = new_saved_session()
    saved_before = ps.session_save_path(session.mode, session.session_id).read_text(encoding="utf-8")

    def _boom(_path, _data):
        raise TypeError("simulated serialization failure")

    monkeypatch.setattr(ps, "atomic_write_json", _boom)
    with pytest.raises(ps.PersistenceError):
        ps.save_session(session)

    # updated_at must not have advanced, and the file on disk is untouched.
    saved_after = ps.session_save_path(session.mode, session.session_id).read_text(encoding="utf-8")
    assert saved_after == saved_before


# ----------------------------------------------------------------------
# 26-27: corrupt / unsupported saves
# ----------------------------------------------------------------------


def test_corrupt_save_invalid_json_handled_safely(isolated_saves, tmp_path) -> None:
    bad_file = tmp_path / "bad.json"
    bad_file.write_text("{not valid json", encoding="utf-8")
    with pytest.raises(ps.PersistenceError):
        ps.load_session(bad_file)


def test_corrupt_save_not_a_json_object_handled_safely(isolated_saves, tmp_path) -> None:
    bad_file = tmp_path / "bad.json"
    bad_file.write_text("[1, 2, 3]", encoding="utf-8")
    with pytest.raises(ps.PersistenceError):
        ps.load_session(bad_file)


def test_corrupt_save_missing_field_handled_safely(isolated_saves) -> None:
    with pytest.raises(ps.PersistenceError):
        ps.restore_session({"schema_version": 1, "session_id": "x", "session_mode": "MOCK"})


def test_corrupt_save_bad_player_record_handled_safely(isolated_saves) -> None:
    session = new_saved_session()
    data = json.loads(ps.session_save_path(session.mode, session.session_id).read_text(encoding="utf-8"))
    data["players"][0]["position"] = "NOT_A_POSITION"
    with pytest.raises(ps.PersistenceError):
        ps.restore_session(data)


def test_corrupt_save_roster_references_unknown_player_handled_safely(isolated_saves) -> None:
    session = new_saved_session()
    data = json.loads(ps.session_save_path(session.mode, session.session_id).read_text(encoding="utf-8"))
    # Keep Team's own invariants satisfied (roster size vs. players_purchased/
    # spending) so reconstruction succeeds and the *cross-reference* check
    # under test — not Team.__post_init__'s own bookkeeping check — is what
    # catches the unresolvable id.
    data["teams"][0]["roster"].append(999999)
    data["teams"][0]["players_purchased"] += 1
    data["teams"][0]["auction_spending"] += 1
    data["teams"][0]["remaining_budget"] -= 1
    with pytest.raises(ps.PersistenceError, match="roster"):
        ps.restore_session(data)


def test_corrupt_save_queue_references_unknown_player_handled_safely(isolated_saves) -> None:
    session = new_saved_session()
    data = json.loads(ps.session_save_path(session.mode, session.session_id).read_text(encoding="utf-8"))
    data["auction"]["queue"][0] = 999999
    with pytest.raises(ps.PersistenceError, match="queue"):
        ps.restore_session(data)


def test_corrupt_save_history_references_unknown_player_handled_safely(isolated_saves) -> None:
    session = new_saved_session()
    session.sell_current_player(winning_team="Blackout FC", sale_price=5)
    data = json.loads(ps.session_save_path(session.mode, session.session_id).read_text(encoding="utf-8"))
    data["auction"]["history"][0]["player_id"] = 999999
    with pytest.raises(ps.PersistenceError, match="history"):
        ps.restore_session(data)


def test_corrupt_save_duplicate_player_ids_handled_safely(isolated_saves) -> None:
    session = new_saved_session()
    data = json.loads(ps.session_save_path(session.mode, session.session_id).read_text(encoding="utf-8"))
    data["players"][1]["id"] = data["players"][0]["id"]
    with pytest.raises(ps.PersistenceError, match="duplicate"):
        ps.restore_session(data)


def test_corrupt_save_does_not_crash_app_and_is_reported_via_summary(isolated_saves, tmp_path) -> None:
    bad_file = tmp_path / "mock_bad.json"
    bad_file.write_text("not json at all", encoding="utf-8")
    summary = ps.summarize_save_file(bad_file)
    assert summary.is_corrupt is True
    assert summary.error


def test_unsupported_newer_schema_version_rejected_cleanly(isolated_saves) -> None:
    session = new_saved_session()
    data = json.loads(ps.session_save_path(session.mode, session.session_id).read_text(encoding="utf-8"))
    data["schema_version"] = ps.SCHEMA_VERSION + 1
    with pytest.raises(ps.PersistenceError, match="newer"):
        ps.restore_session(data)


def test_unsupported_older_schema_version_rejected_cleanly(isolated_saves) -> None:
    session = new_saved_session()
    data = json.loads(ps.session_save_path(session.mode, session.session_id).read_text(encoding="utf-8"))
    data["schema_version"] = 0
    with pytest.raises(ps.PersistenceError, match="older"):
        ps.restore_session(data)


def test_missing_schema_version_rejected_cleanly(isolated_saves) -> None:
    session = new_saved_session()
    data = json.loads(ps.session_save_path(session.mode, session.session_id).read_text(encoding="utf-8"))
    del data["schema_version"]
    with pytest.raises(ps.PersistenceError):
        ps.restore_session(data)


def test_corrupt_save_does_not_overwrite_itself(isolated_saves, tmp_path) -> None:
    """Loading (even a failed load) must never write anything back."""
    bad_file = tmp_path / "mocks" / "mock_bad.json"
    bad_file.parent.mkdir(parents=True)
    bad_file.write_text("not json", encoding="utf-8")
    original = bad_file.read_text(encoding="utf-8")
    with pytest.raises(ps.PersistenceError):
        ps.load_session(bad_file)
    assert bad_file.read_text(encoding="utf-8") == original


# ----------------------------------------------------------------------
# 28-34: multi-session management
# ----------------------------------------------------------------------


def test_multiple_mocks_remain_separate(isolated_saves) -> None:
    session_a = new_saved_session(name="Practice A", seed=1)
    session_b = new_saved_session(name="Practice B", seed=2)
    assert session_a.session_id != session_b.session_id

    restored_a = ps.load_session(ps.session_save_path(session_a.mode, session_a.session_id))
    restored_b = ps.load_session(ps.session_save_path(session_b.mode, session_b.session_id))
    assert restored_a.name == "Practice A"
    assert restored_b.name == "Practice B"
    assert restored_a.auction.queue != restored_b.auction.queue or restored_a.session_id != restored_b.session_id


def test_mock_cannot_overwrite_live(isolated_saves) -> None:
    live_session = new_saved_session(mode=SessionMode.LIVE)
    live_path_before = ps.LIVE_ACTIVE_PATH.read_text(encoding="utf-8")

    new_saved_session(mode=SessionMode.MOCK)

    assert ps.LIVE_ACTIVE_PATH.read_text(encoding="utf-8") == live_path_before
    assert len(ps.list_mock_sessions()) == 1


def test_deleting_one_mock_does_not_affect_others(isolated_saves) -> None:
    session_a = new_saved_session(name="A")
    session_b = new_saved_session(name="B")

    ps.delete_mock_session(session_a.session_id)

    remaining = ps.list_mock_sessions()
    assert len(remaining) == 1
    assert remaining[0].session_id == session_b.session_id


def test_live_save_detection_works(isolated_saves) -> None:
    assert ps.detect_active_live_session() is None
    new_saved_session(mode=SessionMode.LIVE)
    summary = ps.detect_active_live_session()
    assert summary is not None
    assert summary.mode == SessionMode.LIVE


def test_completed_session_can_still_load(isolated_saves) -> None:
    session = new_saved_session(seed=1)
    resolve_whole_session(session, sale_price=1)
    assert session.is_complete is True
    restored = ps.load_session(ps.session_save_path(session.mode, session.session_id))
    assert restored.is_complete is True


def test_archive_live_session_preserves_old_save_and_returns_none_when_nothing_to_archive(isolated_saves) -> None:
    assert ps.archive_live_session() is None

    live_session = new_saved_session(mode=SessionMode.LIVE)
    archived_path = ps.archive_live_session()
    assert archived_path is not None
    assert archived_path.is_file()
    archived_data = json.loads(archived_path.read_text(encoding="utf-8"))
    assert archived_data["session_id"] == live_session.session_id
    assert not ps.LIVE_ACTIVE_PATH.exists()


def test_new_live_after_archive_does_not_touch_archived_copy(isolated_saves) -> None:
    old_live = new_saved_session(mode=SessionMode.LIVE, name="Old Live")
    archived_path = ps.archive_live_session()
    new_live = new_saved_session(mode=SessionMode.LIVE, name="New Live")

    archived_data = json.loads(archived_path.read_text(encoding="utf-8"))
    assert archived_data["name"] == "Old Live"
    active_data = json.loads(ps.LIVE_ACTIVE_PATH.read_text(encoding="utf-8"))
    assert active_data["name"] == "New Live"
    assert new_live.session_id != old_live.session_id


# ----------------------------------------------------------------------
# 30-31: canonical-data snapshot immutability
# ----------------------------------------------------------------------


@pytest.fixture
def temp_canonical_data(tmp_path, monkeypatch):
    """Copies the real canonical players/teams JSON into a temp location
    and points services.player_service's loader paths at the copy, so a
    test can freely mutate "canonical" data without ever touching the
    real data/players.json."""
    real_players_path = player_service.DEFAULT_PLAYERS_PATH
    real_teams_path = player_service.DEFAULT_TEAMS_PATH

    temp_players_path = tmp_path / "players.json"
    temp_teams_path = tmp_path / "teams.json"
    temp_players_path.write_text(real_players_path.read_text(encoding="utf-8"), encoding="utf-8")
    temp_teams_path.write_text(real_teams_path.read_text(encoding="utf-8"), encoding="utf-8")

    monkeypatch.setattr(player_service, "DEFAULT_PLAYERS_PATH", temp_players_path)
    monkeypatch.setattr(player_service, "DEFAULT_TEAMS_PATH", temp_teams_path)
    return temp_players_path, temp_teams_path


def _set_player_rating(players_path, player_id: int, rating: int) -> None:
    data = json.loads(players_path.read_text(encoding="utf-8"))
    for record in data:
        if record["id"] == player_id:
            record["overall_rating"] = rating
    players_path.write_text(json.dumps(data), encoding="utf-8")


def test_fresh_session_uses_newest_canonical_data(isolated_saves, temp_canonical_data) -> None:
    players_path, _ = temp_canonical_data
    _set_player_rating(players_path, player_id=2, rating=77)

    session = new_saved_session(seed=1)
    rahmat = next(p for p in session.players if p.id == 2)
    assert rahmat.overall_rating == 77


def test_old_session_continues_to_use_old_snapshot_after_canonical_data_changes(isolated_saves, temp_canonical_data) -> None:
    players_path, _ = temp_canonical_data
    _set_player_rating(players_path, player_id=2, rating=50)

    old_session = new_saved_session(seed=1, name="Old Mock")
    rahmat_in_old = next(p for p in old_session.players if p.id == 2)
    assert rahmat_in_old.overall_rating == 50

    # Organizer changes canonical data after the mock was created/saved.
    _set_player_rating(players_path, player_id=2, rating=99)

    reloaded_old = ps.load_session(ps.session_save_path(old_session.mode, old_session.session_id))
    rahmat_reloaded = next(p for p in reloaded_old.players if p.id == 2)
    assert rahmat_reloaded.overall_rating == 50  # unchanged: still the OLD snapshot

    new_session = new_saved_session(seed=2, name="New Mock")
    rahmat_in_new = next(p for p in new_session.players if p.id == 2)
    assert rahmat_in_new.overall_rating == 99  # NEW session sees the updated canonical value


# ----------------------------------------------------------------------
# End-to-end recovery scenario (per the ticket's "IMPORTANT RECOVERY TEST")
# ----------------------------------------------------------------------


def test_end_to_end_crash_recovery_scenario(isolated_saves) -> None:
    # 1. Start MOCK auction with deterministic seed.
    session = new_saved_session(seed=1, name="Recovery Test")
    save_path = ps.session_save_path(session.mode, session.session_id)

    # 2. Sell several players.
    blackout = team_by_name(session.teams, "Blackout FC")
    darkstar = team_by_name(session.teams, "Darkstar FC")
    session.sell_current_player(winning_team=blackout.id, sale_price=10)
    session.sell_current_player(winning_team=darkstar.id, sale_price=7)

    # 3. Mark one UNSOLD.
    session.mark_current_player_unsold()

    # 4. Confirm budgets/rosters/history/current player.
    expected_budgets = {team.name: team.remaining_budget for team in session.teams}
    expected_rosters = {team.name: list(team.roster) for team in session.teams}
    expected_history_len = len(session.auction.history)
    expected_current_player_id = session.current_player.id
    expected_round = session.round_number
    expected_position = session.auction.current_queue_position

    # 5. Save (already autosaved after each step above; force one more
    #    explicit save to mirror the ticket's separate "Save" step).
    ps.save_session(session)

    # 6. Destroy all in-memory session objects.
    del session

    # 7. Load from disk.
    restored = ps.load_session(save_path)

    # 8. Verify restored state exactly matches.
    assert {team.name: team.remaining_budget for team in restored.teams} == expected_budgets
    assert {team.name: list(team.roster) for team in restored.teams} == expected_rosters
    assert len(restored.auction.history) == expected_history_len
    assert restored.current_player.id == expected_current_player_id
    assert restored.round_number == expected_round
    assert restored.auction.current_queue_position == expected_position

    # 9. Continue the auction.
    # 10. Sell next player.
    restored.autosave = ps.autosave_hook
    next_team = team_by_name(restored.teams, "Goli Underdogs")
    result = restored.sell_current_player(winning_team=next_team.id, sale_price=4)
    assert result.outcome == "SOLD"

    # 11. Verify autosave updates file.
    updated_on_disk = json.loads(save_path.read_text(encoding="utf-8"))
    assert len(updated_on_disk["auction"]["history"]) == expected_history_len + 1

    # 12. Reload again.
    restored_again = ps.load_session(save_path)

    # 13. Verify second restored state exactly matches.
    assert len(restored_again.auction.history) == expected_history_len + 1
    assert team_by_name(restored_again.teams, "Goli Underdogs").remaining_budget == (
        expected_budgets["Goli Underdogs"] - 4
    )


# ----------------------------------------------------------------------
# Player Cards + Last-Season FPL (September 2026): persistence
# backward/forward compatibility for the new additive Player field.
# ----------------------------------------------------------------------


def test_old_persistence_snapshot_without_fpl_field_loads_safely(isolated_saves) -> None:
    session = new_saved_session()
    path = ps.session_save_path(session.mode, session.session_id)
    data = json.loads(path.read_text(encoding="utf-8"))

    # Simulate a save written before last_season_fpl_points existed.
    for player_dict in data["players"]:
        del player_dict["last_season_fpl_points"]
    path.write_text(json.dumps(data), encoding="utf-8")

    restored = ps.load_session(path)
    assert all(player.last_season_fpl_points is None for player in restored.players)
    assert len(restored.players) == 32


def test_new_snapshot_preserves_fpl_value(isolated_saves) -> None:
    session = new_saved_session()
    rizvi = next(p for p in session.players if p.full_name == "Rizvi Ibrahim")
    assert rizvi.last_season_fpl_points == 111

    restored = ps.load_session(ps.session_save_path(session.mode, session.session_id))
    restored_rizvi = next(p for p in restored.players if p.full_name == "Rizvi Ibrahim")
    assert restored_rizvi.last_season_fpl_points == 111

    no_data_player = next(p for p in session.players if p.full_name == "Munem")
    assert no_data_player.last_season_fpl_points is None
    restored_no_data = next(p for p in restored.players if p.full_name == "Munem")
    assert restored_no_data.last_season_fpl_points is None


# ----------------------------------------------------------------------
# Auction History (September 2026): persistence backward/forward
# compatibility for the new additive AuctionHistoryEntry.round_number field.
# ----------------------------------------------------------------------


def test_old_persistence_snapshot_without_round_number_loads_safely(isolated_saves) -> None:
    session = new_saved_session()
    team = team_by_name(session.teams, "Blackout FC")
    session.sell_current_player(winning_team=team.id, sale_price=5)

    path = ps.session_save_path(session.mode, session.session_id)
    data = json.loads(path.read_text(encoding="utf-8"))

    # Simulate a save written before round_number existed on history entries.
    for entry_dict in data["auction"]["history"]:
        del entry_dict["round_number"]
    path.write_text(json.dumps(data), encoding="utf-8")

    restored = ps.load_session(path)
    assert len(restored.auction.history) == 1
    assert restored.auction.history[0].round_number is None
    assert restored.auction.history[0].status == "SOLD"


def test_new_snapshot_preserves_round_number(isolated_saves) -> None:
    session = new_saved_session()
    team = team_by_name(session.teams, "Blackout FC")
    session.sell_current_player(winning_team=team.id, sale_price=5)
    assert session.auction.history[0].round_number == 1

    restored = ps.load_session(ps.session_save_path(session.mode, session.session_id))
    assert restored.auction.history[0].round_number == 1


# ----------------------------------------------------------------------
# Captain Phone Bidding (September 2026): `Auction.current_bid`/
# `leading_team_id` already existed as plain dataclass fields (defaulting
# to `None`) before this feature and were already unconditionally
# serialized by `Auction.to_dict()` — so no schema change was needed here.
# These tests confirm that pre-existing serialization actually round-trips
# the live bid state correctly, and that a save file predating this
# feature (missing the keys entirely) still loads safely.
# ----------------------------------------------------------------------


def test_old_persistence_snapshot_without_bidding_fields_loads_safely(isolated_saves) -> None:
    session = new_saved_session()
    path = ps.session_save_path(session.mode, session.session_id)
    data = json.loads(path.read_text(encoding="utf-8"))

    # Simulate a save written before current_bid/leading_team_id existed.
    del data["auction"]["current_bid"]
    del data["auction"]["leading_team_id"]
    path.write_text(json.dumps(data), encoding="utf-8")

    restored = ps.load_session(path)
    assert restored.auction.current_bid is None
    assert restored.auction.leading_team_id is None


def test_recovered_session_preserves_an_active_live_bid(isolated_saves) -> None:
    session = new_saved_session()
    blackout = team_by_name(session.teams, "Blackout FC")
    result = session.place_live_bid(blackout.id, 6)
    assert result.accepted is True

    restored = ps.load_session(ps.session_save_path(session.mode, session.session_id))
    assert restored.auction.current_bid == 6
    assert restored.auction.leading_team_id == blackout.id
    assert restored.leading_team.name == "Blackout FC"


def test_accepted_bid_triggers_autosave(isolated_saves) -> None:
    """Matches the chosen design (see PROJECT_CONTEXT.md's "Captain Phone
    Bidding — Phase 1"): every *accepted* bid autosaves immediately, same
    as a SOLD/UNSOLD transaction, since a measured save is only a few
    milliseconds — maximizing crash-recovery safety without perceptible
    UI cost."""
    session = new_saved_session()
    path = ps.session_save_path(session.mode, session.session_id)
    before = json.loads(path.read_text(encoding="utf-8"))
    assert before["auction"]["current_bid"] is None

    blackout = team_by_name(session.teams, "Blackout FC")
    session.place_live_bid(blackout.id, 4)

    after = json.loads(path.read_text(encoding="utf-8"))
    assert after["auction"]["current_bid"] == 4


def test_rejected_bid_does_not_trigger_another_autosave(isolated_saves) -> None:
    """A rejected bid must leave the persisted save byte-for-byte
    untouched — exactly like a rejected SOLD/UNSOLD — never mistaken for
    a persisted change."""
    session = new_saved_session()
    blackout = team_by_name(session.teams, "Blackout FC")
    session.place_live_bid(blackout.id, 4)

    path = ps.session_save_path(session.mode, session.session_id)
    saved_after_accepted = path.read_text(encoding="utf-8")

    darkstar = team_by_name(session.teams, "Darkstar FC")
    result = session.place_live_bid(darkstar.id, 4)  # not > 4, rejected
    assert result.accepted is False

    assert path.read_text(encoding="utf-8") == saved_after_accepted


def test_sold_after_a_live_bid_autosaves_correctly(isolated_saves) -> None:
    session = new_saved_session()
    blackout = team_by_name(session.teams, "Blackout FC")
    session.place_live_bid(blackout.id, 6)
    session.sell_current_player(winning_team=blackout.id, sale_price=6)

    path = ps.session_save_path(session.mode, session.session_id)
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["auction"]["current_bid"] is None
    assert saved["auction"]["leading_team_id"] is None
    assert len(saved["auction"]["history"]) == 1
    assert saved["auction"]["history"][0]["status"] == "SOLD"


def test_mock_live_save_separation_unaffected_by_bidding(isolated_saves) -> None:
    mock_session = new_saved_session(mode=SessionMode.MOCK)
    live_session = new_saved_session(mode=SessionMode.LIVE)

    team = team_by_name(mock_session.teams, "Blackout FC")
    mock_session.place_live_bid(team.id, 3)

    mock_path = ps.session_save_path(mock_session.mode, mock_session.session_id)
    live_path = ps.session_save_path(live_session.mode, live_session.session_id)
    assert mock_path != live_path

    live_saved = json.loads(live_path.read_text(encoding="utf-8"))
    assert live_saved["auction"]["current_bid"] is None  # the mock's bid never touched the live save
