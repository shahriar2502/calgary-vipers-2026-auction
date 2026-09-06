"""Shared, in-memory auction session state for the running application.

`MainWindow` owns exactly one `AuctionSession` and passes it to every
screen builder. Screens read/act through this object instead of
independently reloading data/players.json or data/teams.json, so
navigating between screens (Live Auction -> Teams -> Live Auction) never
re-randomizes the queue or discards in-progress budgets/rosters/history.

An auction may span multiple rounds (UNSOLD players return in a later
re-auction round until sold or until no team can legally take them — see
services/auction_service.py). Progress properties here (`resolved_count`,
`total_queue_length`, `remaining_count`) are scoped to the *current
round*, matching what the Live Auction screen shows ("Round 2 / Player 1
of 2"); `sold_count`/`total_eligible_count` give the tournament-wide
totals shown once the whole auction completes.

Milestone 9 (persistence): a session now carries MOCK/LIVE mode and
identity/timestamp metadata, and autosaves itself after every successful
state-changing operation via an injected `autosave` hook — this module has
no file-I/O of its own (that all lives in services/persistence_service.py,
which this module cannot import without a circular dependency, since
persistence_service needs AuctionSession/SessionMode to restore one). A
caller that wants real persistence wires `session.autosave =
persistence_service.autosave_hook` once (see `ui/main_window.py`); without
that wiring, `AuctionSession` behaves exactly as it did before this
milestone — nothing here writes to disk on its own.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum

from models.auction import Auction, AuctionStatus
from models.player import Player
from models.team import Team
from services.auction_service import (
    AuctionTransactionError,
    TransactionResult,
    get_current_player,
    process_sale,
    process_unsold,
    sold_player_ids,
)
from services.live_bid_service import BidResult, place_bid
from services.player_service import load_players, load_teams, validate_setup
from services.randomization_service import create_auction


class SessionMode(str, Enum):
    """Whether a session is practice/testing data or the real tournament.

    MOCK sessions are safe to create freely and abandon; multiple may
    exist side by side. LIVE is the one protected, real-tournament
    session — see services/persistence_service.py for how each mode maps
    to a save location (mocks/ vs. the single live/live_active.json).
    """

    MOCK = "MOCK"
    LIVE = "LIVE"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _generate_session_id(mode: SessionMode) -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    suffix = uuid.uuid4().hex[:8]
    return f"{mode.value.lower()}_{timestamp}_{suffix}"


@dataclass
class AuctionSession:
    """Owns the Auction/Player/Team objects for one running auction, if any."""

    auction: Auction | None = None
    players: list[Player] | None = None
    teams: list[Team] | None = None
    last_result: TransactionResult | None = None

    # Milestone 9: session identity/metadata, persisted verbatim by
    # services/persistence_service.py — see that module's save-file shape.
    mode: SessionMode | None = None
    session_id: str | None = None
    name: str | None = None
    created_at: str | None = None
    updated_at: str | None = None  # set only on a *successful* save, never optimistically
    last_save_error: str | None = None  # None means the last save attempt (if any) succeeded

    # Runtime-only: never persisted, never copied by `adopt()`. Wired once
    # by the application (ui/main_window.py) to services.persistence_service
    # .autosave_hook; left None in tests that don't want real disk I/O.
    autosave: Callable[["AuctionSession"], None] | None = None

    @property
    def started(self) -> bool:
        return self.auction is not None

    @property
    def is_complete(self) -> bool:
        return self.auction is not None and self.auction.status == AuctionStatus.COMPLETE

    @property
    def is_blocked(self) -> bool:
        return self.auction is not None and self.auction.status == AuctionStatus.BLOCKED

    @property
    def round_number(self) -> int:
        return self.auction.round_number if self.auction is not None else 1

    @property
    def current_player(self) -> Player | None:
        if self.auction is None or self.players is None:
            return None
        return get_current_player(self.auction, self.players)

    @property
    def current_bid(self) -> int | None:
        """Captain Phone Bidding (Phase 1): the live current highest bid
        for whoever is currently up for auction, or `None` before the
        first bid. Attempt-scoped — reset to `None` the instant SOLD/
        UNSOLD reveals the next player (see `services/auction_service.py`
        `_finish_transaction`)."""
        return self.auction.current_bid if self.auction is not None else None

    @property
    def leading_team(self) -> Team | None:
        """The `Team` currently holding the live bid, or `None` before the
        first bid. Resolves `Auction.leading_team_id` against `self.teams`
        rather than exposing the raw id, since every caller (desktop UI,
        the phone API) wants the team object/name, not just its id."""
        if self.auction is None or self.auction.leading_team_id is None or self.teams is None:
            return None
        return next((team for team in self.teams if team.id == self.auction.leading_team_id), None)

    @property
    def resolved_count(self) -> int:
        """How many players have been processed in the *current round*."""
        return self.auction.current_queue_position if self.auction is not None else 0

    @property
    def total_queue_length(self) -> int:
        """The *current round's* queue length (not the original 28-player total)."""
        return len(self.auction.queue) if self.auction is not None else 0

    @property
    def remaining_count(self) -> int:
        return self.total_queue_length - self.resolved_count

    @property
    def sold_count(self) -> int:
        """How many players are permanently SOLD, across all rounds."""
        return len(sold_player_ids(self.auction)) if self.auction is not None else 0

    @property
    def total_eligible_count(self) -> int:
        """Total auction-eligible players for this tournament (e.g. 28)."""
        if self.players is None:
            return 0
        return sum(1 for player in self.players if player.auction_eligible)

    def start(
        self,
        seed: int | None = None,
        mode: SessionMode = SessionMode.MOCK,
        name: str | None = None,
        session_id: str | None = None,
    ) -> None:
        """Load canonical data and create a fresh randomized 28-player queue.

        A no-op if a session is already active: calling this again must
        never re-randomize or discard in-progress state, so it's safe to
        call from a UI button handler without first checking `started`.

        Raises `AuctionTransactionError` (reusing the existing "auction
        cannot proceed" exception type rather than introducing a second
        one) if the canonical tournament data itself isn't setup-ready —
        this should never trigger on real canonical data, but a new
        session deliberately checks rather than silently building a queue
        on top of broken data.

        On success, immediately attempts to persist the new session (the
        Milestone 9 "persist it immediately" requirement) via the
        `autosave` hook, exactly like every other state-changing call —
        see `_trigger_autosave`.
        """
        if self.started:
            return
        players = load_players()
        teams = load_teams()
        validation = validate_setup(players, teams)
        if not validation.is_ready:
            failures = "; ".join(f"{check.label}: {check.detail}" for check in validation.failures)
            raise AuctionTransactionError(f"Cannot start auction: tournament setup is not ready ({failures}).")

        self.players = players
        self.teams = teams
        self.auction = create_auction(self.players, seed=seed)
        self.last_result = None
        self.mode = mode
        self.session_id = session_id or _generate_session_id(mode)
        self.name = name
        self.created_at = _now_iso()
        self.updated_at = None
        self.last_save_error = None
        self._trigger_autosave()

    def adopt(self, other: "AuctionSession") -> None:
        """Replace this session's data in place with `other`'s.

        Used when resuming a saved session: `services/persistence_service
        .load_session()` returns a brand-new `AuctionSession` object, but
        `MainWindow`/every screen already holds a reference to *this*
        session object. Mutating in place means every screen sees the
        resumed session on its next render without `MainWindow` needing to
        swap object identity across screens.

        `autosave` is deliberately NOT copied from `other` — a
        freshly-restored session never carries a hook, and this session's
        existing wiring (set once by `MainWindow`) should keep working
        after adopting.
        """
        self.auction = other.auction
        self.players = other.players
        self.teams = other.teams
        self.last_result = other.last_result
        self.mode = other.mode
        self.session_id = other.session_id
        self.name = other.name
        self.created_at = other.created_at
        self.updated_at = other.updated_at
        self.last_save_error = other.last_save_error

    def sell_current_player(self, winning_team: int | str, sale_price: int) -> TransactionResult:
        """Sell the current player via services.auction_service.process_sale.

        Raises AuctionTransactionError (unchanged) if the session has not
        been started yet, or for any reason process_sale itself rejects —
        a rejected transaction raises *before* `last_result` is updated
        and before autosave is triggered, so neither in-memory nor
        persisted state changes for a failed sale.
        """
        if not self.started:
            raise AuctionTransactionError("Auction is not initialized.")
        result = process_sale(self.auction, self.players, self.teams, winning_team, sale_price)
        self.last_result = result
        self._trigger_autosave()
        return result

    def mark_current_player_unsold(self) -> TransactionResult:
        """Mark the current player UNSOLD via services.auction_service.process_unsold."""
        if not self.started:
            raise AuctionTransactionError("Auction is not initialized.")
        result = process_unsold(self.auction, self.players, self.teams)
        self.last_result = result
        self._trigger_autosave()
        return result

    def place_live_bid(self, team_id_or_name: int | str, bid_amount: int) -> BidResult:
        """Captain Phone Bidding (Phase 1): the one entry point both the
        desktop's own bid buttons and every phone call — see
        `services/live_bid_service.place_bid` for the actual validation
        (team eligibility, budget reserve, must-exceed-current-bid). This
        never sells or marks unsold; SOLD/UNSOLD remain organizer-only via
        `sell_current_player`/`mark_current_player_unsold` above.

        An accepted bid autosaves immediately, same as a SOLD/UNSOLD
        transaction — bids are cheap, infrequent (human-paced, not
        machine-speed) JSON writes, so maximizing crash-recovery safety
        was preferred over skipping the write (see PROJECT_CONTEXT.md's
        "Captain Phone Bidding — Phase 1" for the measured write cost). A
        rejected bid never touches the session's `updated_at`/save state,
        exactly like a rejected SOLD/UNSOLD.
        """
        if not self.started:
            raise AuctionTransactionError("Auction is not initialized.")
        result = place_bid(self.auction, self.players, self.teams, team_id_or_name, bid_amount)
        if result.accepted:
            self._trigger_autosave()
        return result

    def _trigger_autosave(self) -> None:
        """Attempt to persist via the injected `autosave` hook, if any.

        Deliberately catches any exception the hook raises rather than
        letting it propagate: a disk/persistence failure must never undo
        or crash a transaction that has already succeeded in memory (see
        PROJECT_CONTEXT.md's Milestone 9 "AUTOSAVE FAILURE" policy). The
        failure is instead recorded on `last_save_error` for the UI to
        surface prominently.
        """
        if self.autosave is None:
            return
        try:
            self.autosave(self)
            self.last_save_error = None
        except Exception as exc:  # noqa: BLE001 - intentional hook boundary; see docstring
            self.last_save_error = str(exc)
