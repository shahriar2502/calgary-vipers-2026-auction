"""Tests for services/captain_auth_service.py: Captain Phone Bidding
Phase 2's PIN storage, login/token authentication, one-device-per-team
enforcement, and heartbeat-based connection tracking.

Every test constructs its own `CaptainAuthService` pointed at a
`tmp_path`-scoped PIN file — never the real `config/captain_bidding.json`
— so running this suite never disturbs (or depends on) an organizer's
actual saved PINs.
"""

from __future__ import annotations

import json
import time

import pytest

from models.team import Team
from services import captain_auth_service as auth_module
from services.captain_auth_service import CaptainAuthService, load_pin_config, save_pin_config


def make_team(team_id: int, name: str) -> Team:
    return Team(
        id=team_id,
        name=name,
        short_name=name[:4],
        captain_player_id=team_id,
        captain_name=f"Captain {team_id}",
        starting_budget=100,
        remaining_budget=100,
        max_squad_size=8,
        roster=[team_id],
    )


TEAMS = [
    make_team(1, "Blackout FC"),
    make_team(2, "Darkstar FC"),
    make_team(3, "Goli Underdogs"),
    make_team(4, "Showstoppers"),
]


@pytest.fixture
def auth(tmp_path) -> CaptainAuthService:
    return CaptainAuthService(teams=TEAMS, config_path=tmp_path / "captain_bidding.json")


# ============================================================
# 1-10. PIN SERVICE
# ============================================================


def test_initial_pin_generation_creates_four_pins(auth) -> None:
    pins = auth.get_pins()
    assert set(pins.keys()) == {1, 2, 3, 4}


def test_pins_are_unique(auth) -> None:
    pins = auth.get_pins()
    assert len(set(pins.values())) == 4


def test_pin_format_is_four_digits(auth) -> None:
    for pin in auth.get_pins().values():
        assert isinstance(pin, str)
        assert len(pin) == 4
        assert pin.isdigit()


def test_pin_config_persists_to_disk(tmp_path) -> None:
    config_path = tmp_path / "captain_bidding.json"
    first = CaptainAuthService(teams=TEAMS, config_path=config_path)
    pins = first.get_pins()
    raw = json.loads(config_path.read_text(encoding="utf-8"))
    assert raw["pins"] == {str(team_id): pin for team_id, pin in pins.items()}


def test_pin_config_reloads_identically(tmp_path) -> None:
    config_path = tmp_path / "captain_bidding.json"
    first = CaptainAuthService(teams=TEAMS, config_path=config_path)
    second = CaptainAuthService(teams=TEAMS, config_path=config_path)
    assert first.get_pins() == second.get_pins()
    assert second.pin_config_recovered is False


def test_missing_config_recovers_with_fresh_pins(tmp_path) -> None:
    config_path = tmp_path / "does_not_exist.json"
    auth_service = CaptainAuthService(teams=TEAMS, config_path=config_path)
    assert auth_service.pin_config_recovered is True
    assert len(auth_service.get_pins()) == 4
    assert config_path.is_file()


def test_corrupt_config_recovers_safely(tmp_path) -> None:
    config_path = tmp_path / "captain_bidding.json"
    config_path.write_text("not valid json {{{", encoding="utf-8")
    auth_service = CaptainAuthService(teams=TEAMS, config_path=config_path)
    assert auth_service.pin_config_recovered is True
    assert len(auth_service.get_pins()) == 4


def test_malformed_pin_shape_recovers_safely(tmp_path) -> None:
    config_path = tmp_path / "captain_bidding.json"
    config_path.write_text(json.dumps({"pins": {"1": "abcd", "2": "22", "3": "3333", "4": "4444"}}), encoding="utf-8")
    auth_service = CaptainAuthService(teams=TEAMS, config_path=config_path)
    assert auth_service.pin_config_recovered is True
    for pin in auth_service.get_pins().values():
        assert auth_module._PIN_PATTERN.match(pin)


def test_duplicate_pins_in_file_recover_safely(tmp_path) -> None:
    config_path = tmp_path / "captain_bidding.json"
    config_path.write_text(json.dumps({"pins": {"1": "1234", "2": "1234", "3": "5678", "4": "9999"}}), encoding="utf-8")
    auth_service = CaptainAuthService(teams=TEAMS, config_path=config_path)
    assert auth_service.pin_config_recovered is True
    assert len(set(auth_service.get_pins().values())) == 4


def test_regenerate_changes_every_pin(auth) -> None:
    before = auth.get_pins()
    after = auth.regenerate_pins()
    assert after != before
    assert len(set(after.values())) == 4


def test_regenerate_invalidates_existing_auth_sessions(auth) -> None:
    pins = auth.get_pins()
    team_id, pin = next(iter(pins.items()))
    result = auth.login(pin)
    assert result.accepted is True

    auth.regenerate_pins()

    assert auth.authenticate(result.token) is None
    assert auth.is_authenticated(team_id) is False


def test_regenerate_does_not_touch_canonical_player_or_team_data(auth) -> None:
    from services.player_service import load_players, load_teams

    players_before = load_players()
    teams_before = load_teams()
    auth.regenerate_pins()
    assert load_players() == players_before
    assert load_teams() == teams_before


# ============================================================
# 11-21. AUTH
# ============================================================


def test_valid_pin_authenticates(auth) -> None:
    team_id, pin = next(iter(auth.get_pins().items()))
    result = auth.login(pin)
    assert result.accepted is True
    assert result.team_id == team_id
    assert result.token is not None


def test_invalid_pin_rejected(auth) -> None:
    result = auth.login("0000") if "0000" not in auth.get_pins().values() else auth.login("1111")
    assert result.accepted is False
    assert result.error_code == "invalid_pin"


def test_invalid_pin_does_not_reveal_any_team_name(auth) -> None:
    bad_pin = next(pin for pin in ("0000", "1111", "2222") if pin not in auth.get_pins().values())
    result = auth.login(bad_pin)
    assert result.accepted is False
    for team_name in auth._team_names.values():
        assert team_name not in result.message


def test_token_is_created_on_login(auth) -> None:
    _, pin = next(iter(auth.get_pins().items()))
    result = auth.login(pin)
    assert isinstance(result.token, str)
    assert len(result.token) > 10


def test_token_maps_to_correct_team(auth) -> None:
    team_id, pin = next(iter(auth.get_pins().items()))
    result = auth.login(pin)
    assert auth.authenticate(result.token) == team_id


def test_token_can_be_reused_across_multiple_calls(auth) -> None:
    team_id, pin = next(iter(auth.get_pins().items()))
    result = auth.login(pin)
    assert auth.authenticate(result.token) == team_id
    assert auth.authenticate(result.token) == team_id
    assert auth.authenticate(result.token) == team_id


def test_authenticate_never_returns_a_different_team_for_the_same_token(auth) -> None:
    """There is no parameter through which a caller can request a
    different team for an existing token — `authenticate` only ever
    resolves the team that logged in originally."""
    team_id, pin = next(iter(auth.get_pins().items()))
    result = auth.login(pin)
    for _ in range(5):
        assert auth.authenticate(result.token) == team_id


def test_second_device_same_team_pin_is_rejected(auth) -> None:
    team_id, pin = next(iter(auth.get_pins().items()))
    first = auth.login(pin)
    assert first.accepted is True

    second = auth.login(pin)
    assert second.accepted is False
    assert second.error_code == "already_connected"
    assert auth._team_names[team_id] in second.message


def test_organizer_reset_invalidates_team_session(auth) -> None:
    team_id, pin = next(iter(auth.get_pins().items()))
    result = auth.login(pin)
    auth.reset_team_session(team_id)
    assert auth.authenticate(result.token) is None
    assert auth.is_authenticated(team_id) is False


def test_reset_does_not_affect_other_teams(auth) -> None:
    pins = list(auth.get_pins().items())
    (team_a, pin_a), (team_b, pin_b) = pins[0], pins[1]
    result_a = auth.login(pin_a)
    result_b = auth.login(pin_b)

    auth.reset_team_session(team_a)

    assert auth.authenticate(result_a.token) is None
    assert auth.authenticate(result_b.token) == team_b


def test_reset_after_disconnect_allows_new_login(auth) -> None:
    team_id, pin = next(iter(auth.get_pins().items()))
    auth.login(pin)
    auth.reset_team_session(team_id)
    second = auth.login(pin)
    assert second.accepted is True


@pytest.mark.parametrize("connected_count", [0, 1, 2, 3, 4])
def test_n_teams_can_authenticate_simultaneously(auth, connected_count) -> None:
    pins = list(auth.get_pins().items())
    tokens = []
    for team_id, pin in pins[:connected_count]:
        result = auth.login(pin)
        assert result.accepted is True
        tokens.append((team_id, result.token))
    for team_id, token in tokens:
        assert auth.authenticate(token) == team_id
    assert sum(1 for tid in auth.team_ids if auth.is_authenticated(tid)) == connected_count


# ============================================================
# 27-30. CONNECTION STATUS
# ============================================================


def test_heartbeat_marks_connected(auth) -> None:
    team_id, pin = next(iter(auth.get_pins().items()))
    result = auth.login(pin)
    assert auth.is_connected(team_id) is True
    assert auth.heartbeat(result.token) is True


def test_default_active_window_no_longer_flaps_at_five_to_ten_seconds(auth) -> None:
    """RC1 stabilization ticket: the real, un-monkeypatched default was
    widened from 5s (too aggressive for a mobile browser throttling
    timers on screen-dim, brief Wi-Fi latency, or a packaged-app stall)
    to 12-20s. A captain seen 8 seconds ago must still read CONNECTED —
    this is exactly the flapping the ticket's real-world report described."""
    assert auth_module._ACTIVE_WINDOW_SECONDS >= 12.0
    team_id, pin = next(iter(auth.get_pins().items()))
    result = auth.login(pin)
    with auth._lock:
        auth._last_seen[result.token] = time.time() - 8.0
    assert auth.is_connected(team_id) is True


def test_stale_heartbeat_marks_disconnected(auth, monkeypatch) -> None:
    monkeypatch.setattr(auth_module, "_ACTIVE_WINDOW_SECONDS", 0.05)
    team_id, pin = next(iter(auth.get_pins().items()))
    auth.login(pin)
    import time as time_module

    time_module.sleep(0.15)
    assert auth.is_connected(team_id) is False


def test_stale_heartbeat_does_not_invalidate_authentication(auth, monkeypatch) -> None:
    monkeypatch.setattr(auth_module, "_ACTIVE_WINDOW_SECONDS", 0.05)
    team_id, pin = next(iter(auth.get_pins().items()))
    result = auth.login(pin)
    import time as time_module

    time_module.sleep(0.15)
    assert auth.is_connected(team_id) is False
    assert auth.is_authenticated(team_id) is True
    assert auth.authenticate(result.token) == team_id


def test_reconnect_restores_active_state(auth, monkeypatch) -> None:
    monkeypatch.setattr(auth_module, "_ACTIVE_WINDOW_SECONDS", 0.05)
    team_id, pin = next(iter(auth.get_pins().items()))
    result = auth.login(pin)
    import time as time_module

    time_module.sleep(0.15)
    assert auth.is_connected(team_id) is False

    auth.heartbeat(result.token)
    assert auth.is_connected(team_id) is True


def test_connection_overview_lists_all_teams_in_canonical_order(auth) -> None:
    overview = auth.connection_overview()
    assert [entry["team_id"] for entry in overview] == list(auth.team_ids)
    assert all(entry["authenticated"] is False for entry in overview)
    assert all(entry["connected"] is False for entry in overview)


def test_logout_frees_the_team_for_a_new_login(auth) -> None:
    team_id, pin = next(iter(auth.get_pins().items()))
    result = auth.login(pin)
    auth.logout(result.token)
    assert auth.is_authenticated(team_id) is False
    second = auth.login(pin)
    assert second.accepted is True


def test_logout_with_unknown_token_is_a_safe_no_op(auth) -> None:
    auth.logout("not-a-real-token")  # must not raise


# ============================================================
# Rate limiting (local hardening, not internet-banking-grade)
# ============================================================


def test_repeated_failed_logins_eventually_rate_limit(auth) -> None:
    bad_pin = next(pin for pin in ("0000", "1111", "2222", "3333", "4444", "5555") if pin not in auth.get_pins().values())
    results = [auth.login(bad_pin, remote_key="1.2.3.4") for _ in range(6)]
    assert any(result.error_code == "rate_limited" for result in results)


def test_rate_limiting_is_scoped_per_remote_key(auth) -> None:
    bad_pin = next(pin for pin in ("0000", "1111", "2222", "3333", "4444", "5555") if pin not in auth.get_pins().values())
    for _ in range(6):
        auth.login(bad_pin, remote_key="attacker-ip")

    _, good_pin = next(iter(auth.get_pins().items()))
    result = auth.login(good_pin, remote_key="captain-ip")
    assert result.accepted is True
