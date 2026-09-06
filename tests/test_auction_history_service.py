"""Tests for services/auction_history_service.py: pure filter/sort/summary
logic over an existing (never mutated) AuctionHistoryEntry history list.
"""

from models.auction import AuctionHistoryEntry
from services.auction_history_service import (
    ALL_RESULTS,
    ALL_ROUNDS,
    ALL_TEAMS,
    NEWEST_FIRST,
    OLDEST_FIRST,
    build_short_name_lookup,
    filter_history,
    round_numbers,
    sort_history,
    summarize_history,
)


def _sold(sequence: int, player_id: int, name: str, team: str, price: int, round_number: int | None = 1):
    return AuctionHistoryEntry(
        auction_sequence=sequence,
        player_id=player_id,
        player_name=name,
        position="MID",
        overall_rating=80,
        base_price=None,
        status="SOLD",
        team=team,
        sold_price=price,
        round_number=round_number,
    )


def _unsold(sequence: int, player_id: int, name: str, round_number: int | None = 1):
    return AuctionHistoryEntry(
        auction_sequence=sequence,
        player_id=player_id,
        player_name=name,
        position="DEF",
        overall_rating=80,
        base_price=None,
        status="UNSOLD",
        round_number=round_number,
    )


def _sample_history() -> list[AuctionHistoryEntry]:
    return [
        _sold(1, 1, "Munem", "Blackout FC", 5, round_number=1),
        _unsold(2, 2, "Tahsin Islam", round_number=1),
        _sold(3, 3, "Rizvi Ibrahim", "Darkstar FC", 7, round_number=1),
        _unsold(4, 2, "Tahsin Islam", round_number=2),
        _sold(5, 2, "Tahsin Islam", "Showstoppers", 8, round_number=3),
    ]


# ============================================================
# SUMMARY
# ============================================================


def test_summarize_history_total_attempts() -> None:
    summary = summarize_history(_sample_history())
    assert summary.total_attempts == 5


def test_summarize_history_sold_count() -> None:
    summary = summarize_history(_sample_history())
    assert summary.sold_count == 3


def test_summarize_history_unsold_attempt_count_counts_every_attempt() -> None:
    """Tahsin Islam was UNSOLD twice (rounds 1 and 2) before finally being
    SOLD in round 3 — both UNSOLD attempts must count, not just the final
    per-player state."""
    summary = summarize_history(_sample_history())
    assert summary.unsold_count == 2


def test_summarize_history_total_spent_sums_only_sold_prices() -> None:
    summary = summarize_history(_sample_history())
    assert summary.total_spent == 5 + 7 + 8


def test_summarize_history_of_empty_history() -> None:
    summary = summarize_history([])
    assert summary.total_attempts == 0
    assert summary.sold_count == 0
    assert summary.unsold_count == 0
    assert summary.total_spent == 0


# ============================================================
# ROUND NUMBERS
# ============================================================


def test_round_numbers_returns_distinct_sorted_rounds() -> None:
    assert round_numbers(_sample_history()) == [1, 2, 3]


def test_round_numbers_excludes_legacy_entries_missing_round_number() -> None:
    history = _sample_history() + [_sold(6, 4, "Azmi", "Blackout FC", 3, round_number=None)]
    assert round_numbers(history) == [1, 2, 3]


# ============================================================
# FILTER — RESULT
# ============================================================


def test_filter_all_returns_every_entry() -> None:
    assert filter_history(_sample_history(), result=ALL_RESULTS) == _sample_history()


def test_filter_sold_only() -> None:
    filtered = filter_history(_sample_history(), result="SOLD")
    assert all(entry.status == "SOLD" for entry in filtered)
    assert len(filtered) == 3


def test_filter_unsold_only() -> None:
    filtered = filter_history(_sample_history(), result="UNSOLD")
    assert all(entry.status == "UNSOLD" for entry in filtered)
    assert len(filtered) == 2


# ============================================================
# FILTER — ROUND
# ============================================================


def test_filter_by_round_number() -> None:
    filtered = filter_history(_sample_history(), round_number=2)
    assert len(filtered) == 1
    assert filtered[0].player_name == "Tahsin Islam"
    assert filtered[0].status == "UNSOLD"


def test_filter_all_rounds_returns_every_entry() -> None:
    assert filter_history(_sample_history(), round_number=ALL_ROUNDS) == _sample_history()


# ============================================================
# FILTER — TEAM
# ============================================================


def test_filter_by_team_returns_only_that_teams_sales() -> None:
    filtered = filter_history(_sample_history(), team="Blackout FC")
    assert len(filtered) == 1
    assert filtered[0].team == "Blackout FC"


def test_filter_by_team_never_includes_unsold_rows() -> None:
    """UNSOLD attempts are never attributed to a team, so filtering by any
    specific team must exclude them (their `team` field is always None)."""
    filtered = filter_history(_sample_history(), team="Showstoppers")
    assert all(entry.status == "SOLD" for entry in filtered)


def test_filter_all_teams_returns_every_entry() -> None:
    assert filter_history(_sample_history(), team=ALL_TEAMS) == _sample_history()


# ============================================================
# SEARCH
# ============================================================


def test_search_by_full_name_case_insensitive() -> None:
    filtered = filter_history(_sample_history(), search="rizvi")
    assert len(filtered) == 1
    assert filtered[0].player_name == "Rizvi Ibrahim"


def test_search_by_short_name_via_snapshot_lookup() -> None:
    short_names = build_short_name_lookup(None) | {3: "Rizvi"}
    filtered = filter_history(_sample_history(), search="rizvi", short_names=short_names)
    assert len(filtered) == 1
    assert filtered[0].player_id == 3


def test_search_with_no_match_returns_empty() -> None:
    assert filter_history(_sample_history(), search="nonexistent player") == []


def test_build_short_name_lookup_from_none_players_is_empty() -> None:
    assert build_short_name_lookup(None) == {}


# ============================================================
# COMBINED FILTERS
# ============================================================


def test_combined_filters_compose() -> None:
    filtered = filter_history(_sample_history(), result="UNSOLD", round_number=1)
    assert len(filtered) == 1
    assert filtered[0].player_name == "Tahsin Islam"
    assert filtered[0].round_number == 1


# ============================================================
# SORT
# ============================================================


def test_sort_oldest_first_is_default_chronological_order() -> None:
    sorted_entries = sort_history(_sample_history(), order=OLDEST_FIRST)
    assert [entry.auction_sequence for entry in sorted_entries] == [1, 2, 3, 4, 5]


def test_sort_newest_first_reverses_chronological_order() -> None:
    sorted_entries = sort_history(_sample_history(), order=NEWEST_FIRST)
    assert [entry.auction_sequence for entry in sorted_entries] == [5, 4, 3, 2, 1]


def test_sort_never_reorders_by_price_or_name() -> None:
    """Sorting is always by auction_sequence — a timeline, not a
    leaderboard — even though this sample's prices/names aren't already in
    sequence order."""
    sorted_entries = sort_history(_sample_history(), order=OLDEST_FIRST)
    assert [entry.player_name for entry in sorted_entries] == [
        "Munem",
        "Tahsin Islam",
        "Rizvi Ibrahim",
        "Tahsin Islam",
        "Tahsin Islam",
    ]


# ============================================================
# RE-AUCTION / MULTI-ATTEMPT BEHAVIOR
# ============================================================


def test_multiple_attempts_for_same_player_all_appear_independently() -> None:
    """Tahsin Islam appears 3 times (UNSOLD, UNSOLD, SOLD) — never
    deduplicated by player_id."""
    history = _sample_history()
    tahsin_entries = [entry for entry in history if entry.player_id == 2]
    assert len(tahsin_entries) == 3
    assert [entry.status for entry in tahsin_entries] == ["UNSOLD", "UNSOLD", "SOLD"]
    assert [entry.round_number for entry in tahsin_entries] == [1, 2, 3]
