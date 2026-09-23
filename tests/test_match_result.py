"""tests/test_match_result.py — MatchResult model + match-money award
rules (models/match_result.py). Numbered to match the ticket's own
"TESTING — MATCH RESULTS" list (1-15).
"""
import pytest

from models.match_result import DRAW_AWARD_M, LOSS_AWARD_M, MatchResult, WIN_AWARD_M


def _match(**overrides) -> MatchResult:
    defaults = dict(
        id="match_abc123",
        match_number=1,
        team1_id=1,
        team2_id=2,
        team1_goals=3,
        team2_goals=1,
        created_at="2026-01-01T00:00:00+00:00",
    )
    defaults.update(overrides)
    return MatchResult(**defaults)


# 1. MatchResult model creation
def test_1_match_result_model_creation() -> None:
    match = _match()
    assert match.id == "match_abc123"
    assert match.match_number == 1
    assert match.team1_id == 1
    assert match.team2_id == 2


# 2. valid score
def test_2_valid_score_accepted() -> None:
    match = _match(team1_goals=2, team2_goals=0)
    assert match.team1_goals == 2
    assert match.team2_goals == 0


# 3. negative score rejected
def test_3_negative_score_rejected() -> None:
    with pytest.raises(ValueError, match="cannot be negative"):
        _match(team1_goals=-1)
    with pytest.raises(ValueError, match="cannot be negative"):
        _match(team2_goals=-1)


# 4. decimal score rejected
def test_4_decimal_score_rejected() -> None:
    with pytest.raises(ValueError, match="whole number"):
        _match(team1_goals=2.5)


# 5. blank score rejected
def test_5_blank_score_rejected() -> None:
    with pytest.raises(ValueError, match="whole number"):
        _match(team1_goals=None)
    with pytest.raises(ValueError, match="whole number"):
        _match(team1_goals="")


# 6. same-team fixture rejected
def test_6_same_team_fixture_rejected() -> None:
    with pytest.raises(ValueError, match="two different teams"):
        _match(team1_id=1, team2_id=1)


# 7. invalid team (id type) rejected -- model-level: a non-int team id is
# meaningless; the real "does this team exist in the tournament" check is
# service-level (see tests/test_match_result_service.py).
def test_7_boolean_goal_value_rejected() -> None:
    with pytest.raises(ValueError, match="whole number"):
        _match(team1_goals=True)


# 8. unique match IDs
def test_8_unique_match_ids() -> None:
    match_a = _match(id="match_aaa")
    match_b = _match(id="match_bbb")
    assert match_a.id != match_b.id


# 9. repeat fixtures permitted with different IDs
def test_9_repeat_fixtures_permitted_with_different_ids() -> None:
    match_a = _match(id="match_aaa", match_number=1)
    match_b = _match(id="match_bbb", match_number=5)
    assert match_a.team1_id == match_b.team1_id
    assert match_a.team2_id == match_b.team2_id
    assert match_a.id != match_b.id


# 10. (accidental duplicate submission prevented -- UI-level; see
# tests/test_match_results_screen.py)


# 11. team1 win awards 4M/1M
def test_11_team1_win_awards_4m_1m() -> None:
    match = _match(team1_id=10, team2_id=20, team1_goals=3, team2_goals=1)
    assert match.winner_id == 10
    assert match.loser_id == 20
    assert match.awards() == {10: WIN_AWARD_M, 20: LOSS_AWARD_M}
    assert match.awards() == {10: 4, 20: 1}


# 12. team2 win awards 1M/4M
def test_12_team2_win_awards_1m_4m() -> None:
    match = _match(team1_id=10, team2_id=20, team1_goals=1, team2_goals=3)
    assert match.winner_id == 20
    assert match.loser_id == 10
    assert match.awards() == {10: LOSS_AWARD_M, 20: WIN_AWARD_M}
    assert match.awards() == {10: 1, 20: 4}


# 13. draw awards 2M/2M
def test_13_draw_awards_2m_2m() -> None:
    match = _match(team1_id=10, team2_id=20, team1_goals=2, team2_goals=2)
    assert match.is_draw is True
    assert match.winner_id is None
    assert match.loser_id is None
    assert match.awards() == {10: DRAW_AWARD_M, 20: DRAW_AWARD_M}
    assert match.awards() == {10: 2, 20: 2}


# 14. 0-0 draw handled
def test_14_0_0_draw_handled() -> None:
    match = _match(team1_goals=0, team2_goals=0)
    assert match.is_draw is True
    assert match.awards() == {match.team1_id: DRAW_AWARD_M, match.team2_id: DRAW_AWARD_M}


# 15. valid high score handled safely
def test_15_valid_high_score_handled_safely() -> None:
    match = _match(team1_goals=12, team2_goals=0)
    assert match.winner_id == match.team1_id
    assert match.awards()[match.team1_id] == WIN_AWARD_M

    with pytest.raises(ValueError, match="maximum reasonable value"):
        _match(team1_goals=51)


def test_match_number_must_be_positive() -> None:
    with pytest.raises(ValueError, match="Match number must be positive"):
        _match(match_number=0)


def test_id_must_be_non_empty_string() -> None:
    with pytest.raises(ValueError, match="non-empty string"):
        _match(id="")


def test_to_dict_from_dict_round_trip() -> None:
    match = _match()
    restored = MatchResult.from_dict(match.to_dict())
    assert restored == match
