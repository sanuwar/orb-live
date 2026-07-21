"""
Bounded live-market shadow mode: the hypothetical strategy runtime
(Milestone 6).

This runtime is the SOLE strategy-evaluation runtime for Milestone 6. It
consumes explicitly supplied, sanitized, read-only observations (a
validated account snapshot and synthetic/live market bars), evaluates the
frozen ORB V0 strategy and Stage 1 risk rules through the existing
engines, and records ONLY hypothetical decisions into the append-only,
non-executable shadow campaign store.

It adds observation, evaluation, and recording only:

- it has no broker-write capability of any kind and instantiates no
  broker transport or client;
- it never places, reviews, cancels, replaces, modifies, or submits an
  order, and creates no executable saga action;
- every recorded action is structurally non-executable;
- it is strictly bounded: an explicit maximum cycle count, an explicit
  broker-call budget, and an explicit observation window; reaching any
  limit terminates the run fail-closed with a reportable terminal status;
- it never reads the wall clock — time is injected through a deterministic
  clock — and it runs no daemon, scheduler, or unbounded loop;
- it does NOT extend, import, rename, or modify src/shadow_runtime.py,
  which is a different, pre-existing broker-monitoring runtime.

The persistent ``strategy.enabled`` flag remains false. Like the existing
read-only live scanner, this runtime evaluates signals against an
in-memory copy of the configuration whose ``strategy.enabled`` is set to
true purely for evaluation; the on-disk configuration, the supplied
configuration object, and the risk block are never mutated.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from enum import Enum
from typing import Any, Mapping, Sequence
from zoneinfo import ZoneInfo

try:
    from .market_data import (
        MinuteBar,
        calculate_estimated_vwap,
        calculate_opening_range,
    )
    from .live_scanner import (
        first_real_bar_for_date,
        latest_completed_bar,
        median_historical_opening_volume,
        previous_session_close,
    )
    from .signal_engine import (
        LiveData,
        OpeningData,
        SignalEngine,
    )
    from .risk_engine import (
        AccountSnapshot,
        RiskEngine,
        TradeRequest,
    )
    from .broker_snapshot import validate_snapshot
    from .broker_identity import CommissionedAccount
    from .shadow_campaign_store import (
        EVENT_HYPOTHETICAL_ENTRY,
        EVENT_HYPOTHETICAL_EXIT,
        EVENT_HYPOTHETICAL_REJECTION,
        EVENT_HYPOTHETICAL_STOP_TO_BREAKEVEN,
        EXIT_KIND_BREAKEVEN,
        EXIT_KIND_STOP,
        EXIT_KIND_TARGET,
        EXIT_KIND_TIME,
        NON_EXECUTABLE_PREFIX,
        ShadowCampaignStore,
        non_executable_correlation_id,
    )
except ImportError:  # pragma: no cover - direct-module execution fallback
    from market_data import (
        MinuteBar,
        calculate_estimated_vwap,
        calculate_opening_range,
    )
    from live_scanner import (
        first_real_bar_for_date,
        latest_completed_bar,
        median_historical_opening_volume,
        previous_session_close,
    )
    from signal_engine import (
        LiveData,
        OpeningData,
        SignalEngine,
    )
    from risk_engine import (
        AccountSnapshot,
        RiskEngine,
        TradeRequest,
    )
    from broker_snapshot import validate_snapshot
    from broker_identity import CommissionedAccount
    from shadow_campaign_store import (
        EVENT_HYPOTHETICAL_ENTRY,
        EVENT_HYPOTHETICAL_EXIT,
        EVENT_HYPOTHETICAL_REJECTION,
        EVENT_HYPOTHETICAL_STOP_TO_BREAKEVEN,
        EXIT_KIND_BREAKEVEN,
        EXIT_KIND_STOP,
        EXIT_KIND_TARGET,
        EXIT_KIND_TIME,
        NON_EXECUTABLE_PREFIX,
        ShadowCampaignStore,
        non_executable_correlation_id,
    )


ET = ZoneInfo("America/New_York")
SPY_SYMBOL = "SPY"

DEFAULT_MAX_BAR_STALENESS = timedelta(minutes=3)


class HypotheticalRuntimeError(RuntimeError):
    """Raised when the bounded runtime is misconfigured."""


class SessionClassification(str, Enum):
    REGULAR = "regular"
    HOLIDAY = "holiday"
    EARLY_CLOSE = "early_close"
    MISSING = "missing"
    UNCERTAIN = "uncertain"


class TerminalStatus(str, Enum):
    COMPLETED_NO_SIGNAL = "COMPLETED_NO_SIGNAL"
    COMPLETED_SHADOW_SIGNAL = "COMPLETED_SHADOW_SIGNAL"
    TERMINATED_CALL_LIMIT = "TERMINATED_CALL_LIMIT"
    TERMINATED_CYCLE_LIMIT = "TERMINATED_CYCLE_LIMIT"
    TERMINATED_STALE_DATA = "TERMINATED_STALE_DATA"
    TERMINATED_INCOMPLETE_EVIDENCE = "TERMINATED_INCOMPLETE_EVIDENCE"
    TERMINATED_ACCOUNT_MISMATCH = "TERMINATED_ACCOUNT_MISMATCH"
    TERMINATED_ACCOUNT_CONFLICT = "TERMINATED_ACCOUNT_CONFLICT"
    TERMINATED_MARKET_DATA_ERROR = "TERMINATED_MARKET_DATA_ERROR"
    TERMINATED_SAFETY = "TERMINATED_SAFETY"
    OPERATOR_REQUIRED = "OPERATOR_REQUIRED"


# Feed-level injected conditions, distinct from bar-content corruption
# that the runtime detects itself.
FEED_OK = "ok"
FEED_MARKET_DATA_ERROR = "market_data_error"
FEED_INCOMPLETE = "incomplete"
FEED_STALE = "stale"

# Account evidence conditions supplied by the read-only snapshot provider.
ACCOUNT_OK = "ok"
ACCOUNT_STALE = "stale"
ACCOUNT_INCOMPLETE = "incomplete"
ACCOUNT_UNAVAILABLE = "unavailable"


class _MarketDataError(RuntimeError):
    """Corrupt market data detected in the feed (malformed/dup/order)."""


class _StaleDataError(RuntimeError):
    """A required bar is older than the permitted freshness window."""


class _InsufficientSymbolEvidence(RuntimeError):
    """A single symbol lacks the evidence to evaluate this cycle."""


@dataclass(frozen=True)
class SessionSpec:
    """A single, explicitly classified trading session."""

    trading_date: date
    classification: SessionClassification = SessionClassification.REGULAR
    session_open: time = time(9, 30)
    session_close: time = time(16, 0)
    time_exit: time = time(15, 45)


@dataclass(frozen=True)
class AccountObservation:
    """One read-only account observation, or an evidence failure."""

    snapshot: Any = None
    evidence_status: str = ACCOUNT_OK

    @classmethod
    def ok(cls, snapshot: Any) -> "AccountObservation":
        return cls(snapshot=snapshot, evidence_status=ACCOUNT_OK)

    @classmethod
    def stale(cls) -> "AccountObservation":
        return cls(evidence_status=ACCOUNT_STALE)

    @classmethod
    def incomplete(cls) -> "AccountObservation":
        return cls(evidence_status=ACCOUNT_INCOMPLETE)

    @classmethod
    def unavailable(cls) -> "AccountObservation":
        return cls(evidence_status=ACCOUNT_UNAVAILABLE)


@dataclass(frozen=True)
class CycleObservation:
    """
    One bounded market observation. All bars are supplied explicitly; the
    runtime validates their content and never fetches anything itself.
    """

    now: datetime
    current_bars: Mapping[str, list[MinuteBar]] = field(
        default_factory=dict
    )
    historical_bars: Mapping[str, list[MinuteBar]] = field(
        default_factory=dict
    )
    feed_status: str = FEED_OK


@dataclass(frozen=True)
class ShadowRunResult:
    """The reportable, non-sensitive outcome of one bounded run."""

    status: TerminalStatus
    shadow_run_id: str
    shadow_campaign_id: str
    reason: str
    cycles_executed: int
    broker_calls_used: int
    hypothetical_signals: int
    recorded_event_ids: tuple[int, ...] = ()

    @property
    def is_clean_completion(self) -> bool:
        return self.status in {
            TerminalStatus.COMPLETED_NO_SIGNAL,
            TerminalStatus.COMPLETED_SHADOW_SIGNAL,
        }


class ShadowClock:
    """
    Deterministic, monotonic, timezone-aware clock.

    The runtime reads time only from this object; it never consults the
    wall clock and never sleeps.
    """

    def __init__(self, start: datetime):
        if start.tzinfo is None:
            raise HypotheticalRuntimeError(
                "The shadow clock start must be timezone-aware."
            )
        self._now = start.astimezone(ET)

    def now(self) -> datetime:
        return self._now

    def advance_to(self, target: datetime) -> datetime:
        if target.tzinfo is None:
            raise HypotheticalRuntimeError(
                "The shadow clock only accepts timezone-aware times."
            )
        target_et = target.astimezone(ET)
        if target_et < self._now:
            raise HypotheticalRuntimeError(
                "The shadow clock only moves forward."
            )
        self._now = target_et
        return self._now


def _validate_bar_content(
    bars: Sequence[MinuteBar],
) -> None:
    """
    Reject corrupt market data: naive timestamps, non-positive or
    inconsistent OHLC, negative volume, duplicate timestamps, or bars that
    arrive out of chronological order.
    """

    seen: set[datetime] = set()
    previous: datetime | None = None

    for bar in bars:
        if bar.begins_at.tzinfo is None:
            raise _MarketDataError("A bar timestamp is not timezone-aware.")

        prices = (
            bar.open_price,
            bar.high_price,
            bar.low_price,
            bar.close_price,
        )
        if any(price <= 0 for price in prices):
            raise _MarketDataError("A bar carries a non-positive price.")

        if bar.high_price < bar.low_price:
            raise _MarketDataError("A bar high is below its low.")

        if bar.high_price < max(bar.open_price, bar.close_price):
            raise _MarketDataError("A bar high is below open or close.")

        if bar.low_price > min(bar.open_price, bar.close_price):
            raise _MarketDataError("A bar low is above open or close.")

        if bar.volume < 0:
            raise _MarketDataError("A bar carries negative volume.")

        if bar.begins_at in seen:
            raise _MarketDataError("A duplicate bar timestamp was found.")
        seen.add(bar.begins_at)

        if previous is not None and bar.begins_at < previous:
            raise _MarketDataError("Bars are not in chronological order.")
        previous = bar.begins_at


class HypotheticalStrategyRuntime:
    """
    The bounded, read-only, record-only ORB V0 shadow runtime.

    It never submits, reviews, cancels, replaces, or modifies an order,
    creates no executable saga action, and records only structurally
    non-executable hypothetical evidence.
    """

    def __init__(
        self,
        config: Mapping[str, Any],
        *,
        campaign_store: ShadowCampaignStore,
        commissioned_account: CommissionedAccount,
        clock: ShadowClock,
        shadow_run_id: str,
        shadow_campaign_id: str,
        session: SessionSpec,
        max_cycles: int,
        max_broker_calls: int,
        observation_window: tuple[datetime, datetime],
        max_bar_staleness: timedelta = DEFAULT_MAX_BAR_STALENESS,
    ):
        if not isinstance(campaign_store, ShadowCampaignStore):
            raise HypotheticalRuntimeError(
                "An explicit ShadowCampaignStore is required."
            )
        if not isinstance(commissioned_account, CommissionedAccount):
            raise HypotheticalRuntimeError(
                "An explicit CommissionedAccount is required."
            )
        if not isinstance(clock, ShadowClock):
            raise HypotheticalRuntimeError(
                "An explicit ShadowClock is required."
            )
        if not shadow_run_id or not shadow_run_id.strip():
            raise HypotheticalRuntimeError(
                "A shadow_run_id is required."
            )
        if not shadow_campaign_id or not shadow_campaign_id.strip():
            raise HypotheticalRuntimeError(
                "A shadow_campaign_id is required."
            )
        if max_cycles <= 0:
            raise HypotheticalRuntimeError(
                "max_cycles must be a positive bound."
            )
        if max_broker_calls <= 0:
            raise HypotheticalRuntimeError(
                "max_broker_calls must be a positive bound."
            )
        if max_bar_staleness <= timedelta(0):
            raise HypotheticalRuntimeError(
                "max_bar_staleness must be positive."
            )

        window_start, window_end = observation_window
        if window_start.tzinfo is None or window_end.tzinfo is None:
            raise HypotheticalRuntimeError(
                "The observation window must be timezone-aware."
            )
        if window_start >= window_end:
            raise HypotheticalRuntimeError(
                "The observation window start must precede its end."
            )

        self.original_config = config
        self.campaign_store = campaign_store
        self.commissioned_account = commissioned_account
        self.clock = clock
        self.shadow_run_id = shadow_run_id
        self.shadow_campaign_id = shadow_campaign_id
        self.session = session
        self.max_cycles = max_cycles
        self.max_broker_calls = max_broker_calls
        self.window_start = window_start.astimezone(ET)
        self.window_end = window_end.astimezone(ET)
        self.max_bar_staleness = max_bar_staleness

        # Evaluate signals against an in-memory copy with the strategy
        # enabled. The supplied configuration and the on-disk file are
        # never mutated, so the persistent strategy.enabled stays false.
        evaluation_config = deepcopy(dict(config))
        evaluation_config["strategy"] = dict(
            evaluation_config.get("strategy", {})
        )
        evaluation_config["strategy"]["enabled"] = True

        self.signal_engine = SignalEngine(evaluation_config)
        self.risk_engine = RiskEngine(evaluation_config)

        self._universe = [
            symbol.upper().strip()
            for symbol in config["universe"]["symbols"]
        ]
        self._opening_minutes = int(
            config["signal"]["opening_range_minutes"]
        )
        self._lookback_days = int(
            config["signal"]["relative_volume_lookback_days"]
        )

        # Run-scoped accumulators.
        self._recorded_event_ids: list[int] = []
        self._hypothetical_signals = 0
        self._meaningful_evaluation = False

    # ------------------------------------------------------------------
    # entry point
    # ------------------------------------------------------------------

    def run(
        self,
        *,
        account_observation: AccountObservation,
        cycles: Sequence[CycleObservation],
    ) -> ShadowRunResult:
        broker_calls_used = 0
        cycles_executed = 0

        # 1. Regular full session only. Any other classification never
        #    produces a normal shadow completion.
        if self.session.classification is not SessionClassification.REGULAR:
            return self._result(
                TerminalStatus.OPERATOR_REQUIRED,
                cycles_executed,
                broker_calls_used,
                reason=(
                    "Session is not a confirmed regular full session "
                    f"({self.session.classification.value}); operator "
                    "confirmation is required."
                ),
            )

        # 2. Account snapshot read (one broker call).
        if broker_calls_used >= self.max_broker_calls:
            return self._result(
                TerminalStatus.TERMINATED_CALL_LIMIT,
                cycles_executed,
                broker_calls_used,
                reason="Broker-call budget exhausted before account read.",
            )
        broker_calls_used += 1

        account_terminal = self._evaluate_account(account_observation)
        if account_terminal is not None:
            status, reason = account_terminal
            return self._result(
                status, cycles_executed, broker_calls_used, reason=reason
            )

        settled_cash = Decimal(
            str(account_observation.snapshot.account_state.portfolio.cash)
        )

        # 3. Bounded cycle processing.
        for cycle in cycles:
            if cycles_executed >= self.max_cycles:
                return self._result(
                    TerminalStatus.TERMINATED_CYCLE_LIMIT,
                    cycles_executed,
                    broker_calls_used,
                    reason="Maximum cycle count reached.",
                )

            if broker_calls_used >= self.max_broker_calls:
                return self._result(
                    TerminalStatus.TERMINATED_CALL_LIMIT,
                    cycles_executed,
                    broker_calls_used,
                    reason="Broker-call budget exhausted.",
                )
            broker_calls_used += 1

            terminal = self._advance_clock(cycle)
            if terminal is not None:
                return self._result(
                    terminal,
                    cycles_executed,
                    broker_calls_used,
                    reason="Cycle timestamp left the observation window.",
                )

            cycles_executed += 1

            terminal = self._process_cycle(cycle, settled_cash)
            if terminal is not None:
                status, reason = terminal
                return self._result(
                    status,
                    cycles_executed,
                    broker_calls_used,
                    reason=reason,
                )

        # 4. Normal termination.
        if not self._meaningful_evaluation:
            return self._result(
                TerminalStatus.TERMINATED_INCOMPLETE_EVIDENCE,
                cycles_executed,
                broker_calls_used,
                reason=(
                    "No strategy evaluation had sufficient evidence to "
                    "occur; refusing to report a clean completion."
                ),
            )

        if self._hypothetical_signals > 0:
            return self._result(
                TerminalStatus.COMPLETED_SHADOW_SIGNAL,
                cycles_executed,
                broker_calls_used,
                reason="Bounded run completed with a hypothetical signal.",
            )

        return self._result(
            TerminalStatus.COMPLETED_NO_SIGNAL,
            cycles_executed,
            broker_calls_used,
            reason="Bounded run completed with no hypothetical signal.",
        )

    # ------------------------------------------------------------------
    # account evidence
    # ------------------------------------------------------------------

    def _evaluate_account(
        self, observation: AccountObservation
    ) -> tuple[TerminalStatus, str] | None:
        status = observation.evidence_status

        if status == ACCOUNT_STALE:
            return (
                TerminalStatus.TERMINATED_STALE_DATA,
                "The account snapshot is stale.",
            )
        if status in {ACCOUNT_INCOMPLETE, ACCOUNT_UNAVAILABLE}:
            return (
                TerminalStatus.TERMINATED_INCOMPLETE_EVIDENCE,
                "The account snapshot evidence is incomplete or "
                "unavailable.",
            )
        if status != ACCOUNT_OK or observation.snapshot is None:
            return (
                TerminalStatus.TERMINATED_INCOMPLETE_EVIDENCE,
                "No usable account snapshot was supplied.",
            )

        snapshot = observation.snapshot
        validation = validate_snapshot(
            snapshot, evaluated_at=self.clock.now()
        )
        if not validation.valid:
            if validation.result_code == "stale_snapshot":
                return (
                    TerminalStatus.TERMINATED_STALE_DATA,
                    "The account snapshot is older than the permitted "
                    "maximum age.",
                )
            return (
                TerminalStatus.TERMINATED_INCOMPLETE_EVIDENCE,
                f"The account snapshot is invalid: "
                f"{validation.result_code}.",
            )

        if not self.commissioned_account.matches(
            snapshot.metadata.account_fingerprint
        ):
            return (
                TerminalStatus.TERMINATED_ACCOUNT_MISMATCH,
                "The observed account does not match the commissioned "
                "account fingerprint.",
            )

        account_state = snapshot.account_state
        if (
            account_state.open_position_count > 0
            or len(account_state.open_orders) > 0
        ):
            return (
                TerminalStatus.TERMINATED_ACCOUNT_CONFLICT,
                "An existing real position or open order conflicts with "
                "shadow evaluation.",
            )

        return None

    # ------------------------------------------------------------------
    # clock and cycle processing
    # ------------------------------------------------------------------

    def _advance_clock(
        self, cycle: CycleObservation
    ) -> TerminalStatus | None:
        if cycle.now.tzinfo is None:
            return TerminalStatus.TERMINATED_SAFETY
        now_et = cycle.now.astimezone(ET)
        if not (self.window_start <= now_et <= self.window_end):
            return TerminalStatus.TERMINATED_SAFETY
        try:
            self.clock.advance_to(now_et)
        except HypotheticalRuntimeError:
            return TerminalStatus.TERMINATED_SAFETY
        return None

    def _process_cycle(
        self, cycle: CycleObservation, settled_cash: Decimal
    ) -> tuple[TerminalStatus, str] | None:
        if cycle.feed_status == FEED_MARKET_DATA_ERROR:
            return (
                TerminalStatus.TERMINATED_MARKET_DATA_ERROR,
                "The market-data feed reported an error.",
            )
        if cycle.feed_status == FEED_INCOMPLETE:
            return (
                TerminalStatus.TERMINATED_INCOMPLETE_EVIDENCE,
                "The market-data feed returned incomplete or paginated "
                "evidence.",
            )
        if cycle.feed_status == FEED_STALE:
            return (
                TerminalStatus.TERMINATED_STALE_DATA,
                "The market-data feed reported stale evidence.",
            )

        now_et = self.clock.now()

        # Detect corrupt bar content across every supplied series.
        try:
            for series in cycle.current_bars.values():
                _validate_bar_content(series)
            for series in cycle.historical_bars.values():
                _validate_bar_content(series)
        except _MarketDataError as error:
            return (
                TerminalStatus.TERMINATED_MARKET_DATA_ERROR,
                f"Corrupt market data: {error}",
            )

        # Manage an existing hypothetical position first.
        state = self.campaign_store.reconstruct_campaign_state(
            shadow_campaign_id=self.shadow_campaign_id,
            as_of_trading_date=now_et.date(),
            as_of_datetime=now_et,
        )

        if state.open_position is not None:
            try:
                self._manage_open_position(
                    state.open_position, cycle, now_et
                )
            except _StaleDataError as error:
                return (
                    TerminalStatus.TERMINATED_STALE_DATA,
                    f"Stale market data: {error}",
                )
            state = self.campaign_store.reconstruct_campaign_state(
                shadow_campaign_id=self.shadow_campaign_id,
                as_of_trading_date=now_et.date(),
                as_of_datetime=now_et,
            )

        # Scan for a new hypothetical entry only while flat.
        if state.open_position is None:
            try:
                terminal = self._scan_for_entry(
                    cycle, now_et, state, settled_cash
                )
            except _StaleDataError as error:
                return (
                    TerminalStatus.TERMINATED_STALE_DATA,
                    f"Stale market data: {error}",
                )
            if terminal is not None:
                return terminal

        return None

    # ------------------------------------------------------------------
    # hypothetical position lifecycle
    # ------------------------------------------------------------------

    def _assert_fresh(self, bar: MinuteBar, now_et: datetime) -> None:
        completed_at = bar.timestamp_et + timedelta(minutes=1)
        if now_et - completed_at > self.max_bar_staleness:
            raise _StaleDataError(
                "A required completed bar is older than the freshness "
                "window."
            )

    def _manage_open_position(
        self,
        position: Any,
        cycle: CycleObservation,
        now_et: datetime,
    ) -> None:
        symbol_bars = list(cycle.current_bars.get(position.symbol, []))
        try:
            bar = latest_completed_bar(symbol_bars, now_et=now_et)
        except ValueError:
            # No completed bar yet this cycle; keep holding.
            return

        self._assert_fresh(bar, now_et)
        self._meaningful_evaluation = True

        entry = position.entry_price
        shares = position.shares
        current_stop = position.current_stop_price
        target = position.target_price

        # Regular-session time exit at 15:45 ET (never on a shortened
        # session, because those never reach cycle processing).
        if now_et.time() >= self.session.time_exit:
            self._record_exit(
                position, EXIT_KIND_TIME, bar.close_price, now_et
            )
            return

        if bar.low_price <= current_stop:
            kind = (
                EXIT_KIND_BREAKEVEN
                if position.breakeven_moved and current_stop == entry
                else EXIT_KIND_STOP
            )
            self._record_exit(position, kind, current_stop, now_et)
            return

        if bar.high_price >= target:
            self._record_exit(
                position, EXIT_KIND_TARGET, target, now_et
            )
            return

        breakeven_trigger = entry + position.risk_per_share
        if not position.breakeven_moved and bar.high_price >= breakeven_trigger:
            self._record_breakeven(position, entry, now_et)

    def _record_exit(
        self,
        position: Any,
        kind: str,
        exit_price: Decimal,
        now_et: datetime,
    ) -> None:
        realized = (exit_price - position.entry_price) * Decimal(
            position.shares
        )
        event_id, inserted = self.campaign_store.record_event(
            shadow_campaign_id=self.shadow_campaign_id,
            shadow_run_id=self.shadow_run_id,
            event_type=EVENT_HYPOTHETICAL_EXIT,
            correlation_id=position.correlation_id,
            trading_date=now_et.date(),
            dedup_key=(
                f"{self.shadow_campaign_id}|exit|"
                f"{position.correlation_id}"
            ),
            symbol=position.symbol,
            observed_at=now_et,
            payload={
                "correlation_id": position.correlation_id,
                "exit_kind": kind,
                "exit_price": str(exit_price),
                "shares": position.shares,
                "realized_pnl": str(realized),
                "non_executable": True,
            },
        )
        if inserted and event_id is not None:
            self._recorded_event_ids.append(event_id)

    def _record_breakeven(
        self,
        position: Any,
        new_stop: Decimal,
        now_et: datetime,
    ) -> None:
        event_id, inserted = self.campaign_store.record_event(
            shadow_campaign_id=self.shadow_campaign_id,
            shadow_run_id=self.shadow_run_id,
            event_type=EVENT_HYPOTHETICAL_STOP_TO_BREAKEVEN,
            correlation_id=position.correlation_id,
            trading_date=now_et.date(),
            dedup_key=(
                f"{self.shadow_campaign_id}|breakeven|"
                f"{position.correlation_id}"
            ),
            symbol=position.symbol,
            observed_at=now_et,
            payload={
                "correlation_id": position.correlation_id,
                "new_stop_price": str(new_stop),
                "non_executable": True,
            },
        )
        if inserted and event_id is not None:
            self._recorded_event_ids.append(event_id)

    # ------------------------------------------------------------------
    # entry scanning
    # ------------------------------------------------------------------

    def _build_spy_reference(
        self, cycle: CycleObservation, now_et: datetime
    ) -> tuple[Decimal, Decimal]:
        spy_bars = list(cycle.current_bars.get(SPY_SYMBOL, []))
        spy_latest = latest_completed_bar(spy_bars, now_et=now_et)
        self._assert_fresh(spy_latest, now_et)

        completed = [
            bar
            for bar in spy_bars
            if (
                not bar.interpolated
                and bar.timestamp_et.date() == now_et.date()
                and bar.timestamp_et + timedelta(minutes=1) <= now_et
            )
        ]
        spy_vwap = calculate_estimated_vwap(completed)
        return spy_latest.close_price, spy_vwap

    def _scan_for_entry(
        self,
        cycle: CycleObservation,
        now_et: datetime,
        state: Any,
        settled_cash: Decimal,
    ) -> tuple[TerminalStatus, str] | None:
        try:
            spy_price, spy_vwap = self._build_spy_reference(cycle, now_et)
        except ValueError:
            # The market reference is not available yet this cycle.
            return None

        account = AccountSnapshot(
            settled_cash=settled_cash,
            daily_pnl=state.daily_realized_pnl,
            weekly_pnl=state.weekly_realized_pnl,
            strategy_drawdown=state.campaign_drawdown,
            open_positions=state.open_positions,
            entries_today=state.entries_today,
        )

        for symbol in self._universe:
            try:
                opening, live = self._assemble_symbol(
                    symbol,
                    cycle,
                    now_et,
                    spy_price,
                    spy_vwap,
                    already_entered=(
                        state.entries_for_symbol(symbol) >= 1
                    ),
                )
            except _InsufficientSymbolEvidence:
                continue

            self._meaningful_evaluation = True

            decision = self.signal_engine.evaluate(opening, live)
            if not decision.approved:
                self._record_rejection(
                    symbol, now_et, "signal", decision.reason
                )
                continue

            request = TradeRequest(
                symbol=symbol,
                entry_price=decision.entry_price,
                stop_price=decision.stop_price,
            )
            risk_decision = self.risk_engine.evaluate(request, account)
            if not risk_decision.approved:
                self._record_rejection(
                    symbol, now_et, "risk", risk_decision.reason
                )
                # Risk limits are portfolio-wide; a rejection here (open
                # position, daily cap, halt) will reject every symbol, so
                # stop scanning this cycle.
                if self._is_portfolio_wide_rejection(risk_decision.reason):
                    break
                continue

            self._record_entry(
                symbol,
                now_et,
                state,
                decision.entry_price,
                decision.stop_price,
                risk_decision.shares,
                risk_decision.planned_risk,
                risk_decision.position_value,
            )
            # One hypothetical position at a time; stop scanning.
            break

        return None

    @staticmethod
    def _is_portfolio_wide_rejection(reason: str) -> bool:
        portfolio_wide = (
            "Maximum open positions reached.",
            "Maximum daily entries reached.",
            "Daily loss limit reached.",
            "Weekly loss limit reached.",
            "Strategy drawdown halt reached.",
        )
        return reason in portfolio_wide

    def _assemble_symbol(
        self,
        symbol: str,
        cycle: CycleObservation,
        now_et: datetime,
        spy_price: Decimal,
        spy_vwap: Decimal,
        *,
        already_entered: bool,
    ) -> tuple[OpeningData, LiveData]:
        current = list(cycle.current_bars.get(symbol, []))
        historical = list(cycle.historical_bars.get(symbol, []))

        try:
            current_bar = latest_completed_bar(current, now_et=now_et)
            opening_range = calculate_opening_range(
                current,
                session_date=now_et.date(),
                opening_range_minutes=self._opening_minutes,
            )
            today_first = first_real_bar_for_date(
                current, session_date=now_et.date()
            )
            previous_close = previous_session_close(
                historical, before_date=now_et.date()
            )
            median_volume = median_historical_opening_volume(
                historical,
                before_date=now_et.date(),
                lookback_days=self._lookback_days,
            )
        except (ValueError, KeyError) as error:
            raise _InsufficientSymbolEvidence(str(error))

        self._assert_fresh(current_bar, now_et)

        opening = OpeningData(
            symbol=symbol,
            previous_close=previous_close,
            today_open=today_first.open_price,
            opening_range_high=opening_range.high_price,
            opening_range_low=opening_range.low_price,
            opening_range_volume=opening_range.volume,
            median_opening_volume=median_volume,
        )
        live = LiveData(
            timestamp=now_et,
            completed_bar_close=current_bar.close_price,
            spy_price=spy_price,
            spy_vwap=spy_vwap,
            already_entered_today=already_entered,
        )
        return opening, live

    def _record_entry(
        self,
        symbol: str,
        now_et: datetime,
        state: Any,
        entry_price: Decimal,
        stop_price: Decimal,
        shares: int,
        planned_risk: Decimal,
        position_value: Decimal,
    ) -> None:
        risk_per_share = entry_price - stop_price
        target_price = entry_price + (risk_per_share * Decimal("2"))
        ordinal = state.entries_today
        correlation_id = non_executable_correlation_id(
            shadow_campaign_id=self.shadow_campaign_id,
            trading_date=now_et.date(),
            symbol=symbol,
            ordinal=ordinal,
        )

        event_id, inserted = self.campaign_store.record_event(
            shadow_campaign_id=self.shadow_campaign_id,
            shadow_run_id=self.shadow_run_id,
            event_type=EVENT_HYPOTHETICAL_ENTRY,
            correlation_id=correlation_id,
            trading_date=now_et.date(),
            dedup_key=(
                f"{self.shadow_campaign_id}|entry|{now_et.date().isoformat()}"
                f"|{symbol}|{ordinal}"
            ),
            symbol=symbol,
            observed_at=now_et,
            payload={
                "shares": shares,
                "entry_price": str(entry_price),
                "stop_price": str(stop_price),
                "target_price": str(target_price),
                "risk_per_share": str(risk_per_share),
                "planned_risk": str(planned_risk),
                "position_value": str(position_value),
                "non_executable": True,
            },
        )
        if inserted and event_id is not None:
            self._recorded_event_ids.append(event_id)
            self._hypothetical_signals += 1

    def _record_rejection(
        self,
        symbol: str,
        now_et: datetime,
        stage: str,
        reason: str,
    ) -> None:
        correlation_id = (
            f"{NON_EXECUTABLE_PREFIX}rejection-"
            f"{self.shadow_campaign_id}-{symbol}"
        )
        event_id, inserted = self.campaign_store.record_event(
            shadow_campaign_id=self.shadow_campaign_id,
            shadow_run_id=self.shadow_run_id,
            event_type=EVENT_HYPOTHETICAL_REJECTION,
            correlation_id=correlation_id,
            trading_date=now_et.date(),
            dedup_key=(
                f"{self.shadow_campaign_id}|rejection|{self.shadow_run_id}"
                f"|{now_et.isoformat()}|{stage}|{symbol}"
            ),
            symbol=symbol,
            observed_at=now_et,
            payload={
                "stage": stage,
                "reason": reason,
                "non_executable": True,
            },
        )
        if inserted and event_id is not None:
            self._recorded_event_ids.append(event_id)

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    def _result(
        self,
        status: TerminalStatus,
        cycles_executed: int,
        broker_calls_used: int,
        *,
        reason: str,
    ) -> ShadowRunResult:
        return ShadowRunResult(
            status=status,
            shadow_run_id=self.shadow_run_id,
            shadow_campaign_id=self.shadow_campaign_id,
            reason=reason,
            cycles_executed=cycles_executed,
            broker_calls_used=broker_calls_used,
            hypothetical_signals=self._hypothetical_signals,
            recorded_event_ids=tuple(self._recorded_event_ids),
        )
