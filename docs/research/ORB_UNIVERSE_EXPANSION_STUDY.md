# ORB Universe Expansion Study

Issue: [#298](https://github.com/sanuwar/orb-live/issues/298)
Status: **tooling implemented and tested; empirical run BLOCKED (no market-data credentials in this environment)**

## Goal

Does expanding the equity universe materially increase how often a valid
`orb_long_v0` setup appears, while every strategy rule stays exactly as
configured in `config/strategy_v0.yaml`? This study never proposes changing
a threshold -- it only asks whether looking at more symbols surfaces more
already-valid setups.

Canonical rules, taken unmodified from production config and
`src/signal_engine.py`:

- gap >= 1.0% (`minimum_gap_percent`)
- opening range = first 5 minutes (`opening_range_minutes`)
- opening RVOL >= 3.0x vs. 20-session median opening-range volume
  (`minimum_relative_volume`, `relative_volume_lookback_days`)
- SPY > VWAP gate enabled (`require_spy_above_vwap`)
- completed-bar close > opening-range high (breakout)
- entry window 09:40-11:00 ET (`entry_start` / `entry_end`)
- long only

## Why there is no empirical result yet

Real historical minute-bar retrieval requires an Alpaca market-data-only API
key/secret (`ALPACA_API_KEY_ID` / `ALPACA_API_SECRET_KEY`). This research
session's environment has neither set, and `config/local.yaml` -- the file
where an operator-supplied credential would normally live -- is explicitly
off-limits to this session under the repository's permanent safety rules
("Never access `local.yaml` or equivalent private configuration unless a
future milestone explicitly authorizes it"). Making a real, credential-using,
network-authenticated call is also a listed stop condition in this
repository's autonomous-workflow rules ("any real ... network-authenticated,
credential-using ... operation would be required" / "credentials ... are
needed").

So rather than fabricate candidate-day rates, funnel survivor counts, or a
"diminishing returns" conclusion, **this PR ships the complete, tested
tooling and stops before the live run**. An operator with the market-data
credentials (broker-write scope is never required -- this only ever touches
Alpaca's read-only IEX market-data feed, the same boundary M15/M19
production code already uses) can produce the actual study by running:

```
ALPACA_API_KEY_ID=... ALPACA_API_SECRET_KEY=... \
  python scripts/research_orb_universe_expansion.py --sessions 90
```

This writes `artifacts/research-universe-expansion/results/
orb_universe_expansion_study.json` and a matching `.csv` summary (both
gitignored, per "do not commit huge raw market-data dumps"), and caches raw
Alpaca responses under `artifacts/research-universe-expansion/bar-cache/`
(also gitignored) so a repeat or resumed run does not re-spend the request
budget on symbol/date ranges already fetched -- which is the common case,
since every larger studied universe is a strict superset of every smaller
one.

## What is implemented and verified

- **`src/orb_universe_expansion_research.py`** -- the reusable core:
  - `UNIVERSE_POOL`: a static, documented, reproducible 100-symbol
    liquidity-tiered pool (see "Universe construction" below).
    `liquid_universe(n)` returns `UNIVERSE_POOL[:n]`, so every studied size
    is a deterministic prefix and every larger universe is a strict superset
    of every smaller one.
  - `evaluate_symbol_session`: evaluates one symbol on one closed session
    using the **unmodified** `src.signal_engine.SignalEngine` and the
    **unmodified** opening-range/VWAP/median-volume math in
    `src.market_data`. It iterates each completed minute in the configured
    entry window and returns the first approval, or the terminal
    elimination gate, exactly mirroring what repeatedly invoking the live
    scanner every minute would have found. `strategy.enabled` is flipped to
    `True` only on an in-memory `copy.deepcopy` of the loaded config -- the
    same technique `LiveScanner` and `_evaluate` (`src/live_shadow_session.
    py`) already use for read-only monitoring; the tracked
    `config/strategy_v0.yaml` is never written.
  - `build_study_report` / `csv_summary_rows`: pure, deterministic
    aggregation from a completed set of per symbol/session evaluations into
    the metrics below. No I/O, no randomness -- fully unit-testable.
  - `CachingHttpClient`: wraps the existing injected `_HttpClientLike`
    transport (`src/market_data_provider.py`'s own convention) with a
    gitignored on-disk cache keyed by request URL + params.
- **`scripts/research_orb_universe_expansion.py`** -- the CLI entry point.
  Refuses to run below a 60-session minimum, refuses to run without
  environment-supplied Alpaca credentials (never reads `config/local.yaml`),
  reuses `AlpacaMarketDataProvider` and `UrllibMarketDataHttpClient`
  unchanged, and writes the JSON + CSV outputs described above.
- **`tests/test_orb_universe_expansion_research.py`** -- 18 focused tests,
  all passing, all offline (synthetic `MinuteBar` fixtures, no network):
  universe-selection determinism and superset ordering; production-universe
  parity with tier 1; incremental-symbol slicing; trading-calendar walk-back;
  and, most importantly, `evaluate_symbol_session` against constructed
  fixtures for each canonical outcome -- an approved candidate, a
  gap-eliminated day, an RVOL-eliminated day, an SPY/VWAP-eliminated day, a
  breakout-eliminated day, and a data-incomplete day -- plus report/CSV
  aggregation correctness.

## Universe construction

`UNIVERSE_POOL` in `src/orb_universe_expansion_research.py` is a static,
citable, liquidity-tiered list of 100 US common equities:

- Tier 1 (1-15): the current production universe verbatim.
- Tier 2 (16-25): next 10 highest-liquidity large caps.
- Tier 3 (26-50): next 25.
- Tier 4 (51-75): next 25.
- Tier 5 (76-100): next 25.

Construction rules: mega/large-cap US common equities only, each judged (at
authorship time, 2026-08) to carry deep multi-year Alpaca IEX history and
average daily dollar volume comfortably in the multi-hundred-million to
multi-billion range; no OTC names, no penny stocks, no leveraged/inverse
ETFs, no duplicate share classes (e.g. `GOOG` is omitted because `GOOGL`
already represents that issuer). SPY is excluded from the candidate pool
and used only as the shared market-context symbol the SPY/VWAP gate already
requires, matching current architecture
(`src.live_shadow_session._watchlist` enforces the identical rule for
production runs).

This is a documented, reproducible starting methodology, not a live
liquidity screen -- it does not query a real-time dollar-volume ranking
API. If a future milestone wants the pool re-derived from live ADV data
rather than this curated table, that is a natural follow-up once market-data
credentials are available in an executing environment.

## Historical period

Not yet selected, because no run has executed. `scripts/
research_orb_universe_expansion.py --sessions N` defaults to the most recent
90 trading sessions (a floor of 60 is enforced) as of the day it is run,
walked back via `src.orb_universe_expansion_research.trading_sessions_before`,
which in turn uses the repository's authoritative
`src.exchange_calendar.regular_session_bounds` (no naive weekday-only
guessing, correctly skips holidays and observes early closes). Whether the
full 90-120 session target is reachable depends on the Alpaca plan's request
budget and the operator's tolerance for run time; if it is not, the operator
running this should shrink `--sessions` to the largest defensible sample
(minimum 60) and record the actual reason (budget, plan tier, runtime) in
this section when the study is executed.

## Results

**Not available.** See "Why there is no empirical result yet" above. Once an
operator runs the script with real credentials, this section should be
replaced with:

- exact date range and session count used, and why (if below 120)
- candidate-day rate by universe size (current production, 15, 25, 50, 75,
  100)
- zero-candidate-session counts by universe size
- total candidates, mean candidates/session, median candidates on candidate
  days, by universe size
- funnel survivor counts (symbols evaluated -> gap survivors -> RVOL
  survivors -> SPY/VWAP survivors -> ORB-breakout survivors) by universe
  size, and which gate eliminates the most symbols
- top candidate symbols and candidate concentration
- which incrementally-added symbols (per `incremental_symbols`) contribute
  the most additional candidate days
- overlap between smaller/larger universes (structural: every studied
  universe is a strict superset of the smaller ones by construction, so
  "overlap" here reduces to how much of the larger universe's candidate
  days are attributable to the smaller universe's own symbols vs. the newly
  added tail)
- API/data cost implications (request count actually spent, cache hit rate)
- where diminishing returns begin
- a defensible production-universe size recommendation, if the data
  supports one

## Explicit non-goals honored

No P&L, expectancy, or win-rate figures are computed or claimed --
`evaluate_symbol_session` only classifies signal-generation frequency, and
the repository has no lifecycle/exit simulator this study could truthfully
reuse without building a major new simulator, which was explicitly out of
scope. No threshold in `config/strategy_v0.yaml` was weakened, no production
universe was changed, `strategy.enabled` in the tracked config file remains
`false`, and no digest-pinned execution-safety file was touched.
