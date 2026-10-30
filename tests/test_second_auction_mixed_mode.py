"""tests/test_second_auction_mixed_mode.py — Second Auction Milestone 3's
mixed-mode requirement: 0-4 captain phones may connect at once, and the
organizer always retains manual bidding/SOLD/UNSOLD authority for every
team regardless of phone connection state. Numbered to match the
ticket's own "TESTING — MIXED MODE" list (39-46).
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from models.auction import AuctionStatus
from models.player import OUTFIELD_BASE_PRICE, player_base_price
from services.auction_service import team_can_bid_for_player
from services.auction_session_service import AuctionSession
from services.captain_auth_service import CaptainAuthService
from services.captain_bidding_server import CaptainBiddingServer
from services.second_auction_service import is_locked_player


def team_by_name(teams, name: str):
    return next(team for team in teams if team.name == name)


def pick_eligible_team(player, teams, players):
    eligible = [team for team in teams if team_can_bid_for_player(team, player, players)]
    if not eligible:
        return None
    return min(eligible, key=lambda team: team.roster_size)


def non_locked_roster_ids(team, players_by_id) -> list[int]:
    return [pid for pid in team.roster if pid in players_by_id and not is_locked_player(players_by_id[pid])]


@pytest.fixture
def auth(tmp_path) -> CaptainAuthService:
    return CaptainAuthService(config_path=tmp_path / "captain_bidding.json")


@pytest.fixture
def second_auction_session() -> AuctionSession:
    session = AuctionSession()
    session.autosave = None
    session.start(seed=1)
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

    players_by_id = {player.id: player for player in session.players}
    for team in session.teams:
        for player_id in non_locked_roster_ids(team, players_by_id)[:4]:
            session.toggle_second_auction_release(team.id, player_id)
    session.confirm_second_auction_release_plan()
    session.start_second_auction(seed=1)
    return session


@pytest.fixture
def client(second_auction_session, auth) -> TestClient:
    server = CaptainBiddingServer(second_auction_session, auth=auth)
    with TestClient(server.app) as test_client:
        yield test_client


def login(client: TestClient, auth: CaptainAuthService, team_name: str) -> str:
    team_id = next(tid for tid in auth.team_ids if auth.team_name(tid) == team_name)
    pin = auth.get_pin(team_id)
    response = client.post("/api/login", json={"pin": pin})
    return response.json()["token"]


def auth_headers(token: str) -> dict:
    return {"X-Captain-Token": token}


# ============================================================
# 39-40. organizer manual bidding remains enabled
# ============================================================


def test_39_organizer_manual_bidding_remains_enabled_with_phone_connected(client, second_auction_session, auth) -> None:
    login(client, auth, "Blackout FC")
    darkstar = team_by_name(second_auction_session.teams, "Darkstar FC")
    result = second_auction_session.place_second_auction_live_bid(darkstar.id, OUTFIELD_BASE_PRICE)
    assert result.accepted is True


def test_40_organizer_manual_bidding_remains_enabled_with_no_phones(second_auction_session) -> None:
    team = second_auction_session.teams[0]
    result = second_auction_session.place_second_auction_live_bid(team.id, OUTFIELD_BASE_PRICE)
    assert result.accepted is True


# ============================================================
# 41-42. organizer can bid for connected/disconnected captain's team
# ============================================================


def test_41_organizer_can_bid_for_connected_captains_team(client, second_auction_session, auth) -> None:
    blackout = team_by_name(second_auction_session.teams, "Blackout FC")
    login(client, auth, "Blackout FC")
    result = second_auction_session.place_second_auction_live_bid(blackout.id, OUTFIELD_BASE_PRICE)
    assert result.accepted is True


def test_42_organizer_can_bid_for_disconnected_captains_team(second_auction_session) -> None:
    blackout = team_by_name(second_auction_session.teams, "Blackout FC")
    # Never connected at all.
    result = second_auction_session.place_second_auction_live_bid(blackout.id, OUTFIELD_BASE_PRICE)
    assert result.accepted is True


# ============================================================
# 43-44. captain phones never get SOLD/UNSOLD controls
# ============================================================


def test_43_captain_phones_never_get_sold_control(client) -> None:
    # No /api/sold (or equivalent) route exists at all on this server.
    response = client.post("/api/sold", json={})
    assert response.status_code == 404


def test_44_captain_phones_never_get_unsold_control(client) -> None:
    response = client.post("/api/unsold", json={})
    assert response.status_code == 404


# ============================================================
# 45-46. organizer SOLD/UNSOLD advances phone state
# ============================================================


def test_45_organizer_sold_advances_phone_state(client, second_auction_session, auth) -> None:
    state = second_auction_session.second_auction_state
    players_by_id = {player.id: player for player in second_auction_session.players}
    before_id = state.current_player_id
    team = second_auction_session.teams[0]
    second_auction_session.sell_second_auction_player(
        winning_team=team.id, sale_price=player_base_price(players_by_id[before_id])
    )
    data = client.get("/api/state").json()
    assert data["current_player"]["full_name"] != players_by_id[before_id].full_name


def test_46_organizer_unsold_advances_phone_state(client, second_auction_session) -> None:
    state = second_auction_session.second_auction_state
    players_by_id = {player.id: player for player in second_auction_session.players}
    before_id = state.current_player_id
    second_auction_session.mark_second_auction_player_unsold()
    data = client.get("/api/state").json()
    # Either a different player (normal case) or, on a single-player
    # queue edge case, at least the round/position has advanced —
    # resolved count strictly increases either way.
    assert data["progress"]["resolved"] >= 1 or data["current_player"]["full_name"] != players_by_id[before_id].full_name
