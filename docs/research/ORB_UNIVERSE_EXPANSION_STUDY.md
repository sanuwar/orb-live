# ORB Universe Expansion Study

Issue: [#298](https://github.com/sanuwar/orb-live/issues/298)
Status: **root cause of DATA_INCOMPLETE dominance diagnosed and fixed; funnel-reporting bug fixed; both verified against the operator's real cached data and by new offline tests; a corrected 90-session live re-run still requires an operator with Alpaca credentials (unavailable in this environment)**

## Revision history

1. First pass: tooling implemented, no live run attempted (no credentials in this environment).
2. Operator ran the real 90-session study on this branch. Results were **not decision-quality**: DATA_INCOMPLETE dominated every universe size (95-99% incomplete), and the size-100 funnel showed `orb_breakout_survivors=54` with `total_candidates=0` -- an internal inconsistency.
3. This revision: diagnosed both defects precisely against the operator's real cached responses, fixed both in code, added regression tests reproducing each, and re-validated against the same real cache (see "Diagnostic findings" below). The corrected code has **not yet been run against fresh network data** -- that step still needs an operator with credentials; see "Status of the empirical run".

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

## Diagnostic findings (root causes)

### 1. DATA_INCOMPLETE dominance -- root cause: single-page Alpaca fetch, no pagination

**Confirmed directly from the operator's real cached responses**
(`artifacts/research-universe-expansion/bar-cache/`, gitignored, 101 files):
**99 of 101** cached Alpaca `/v2/stocks/{symbol}/bars` responses carried a
non-null `next_page_token` and contained **exactly 10,000 bars** -- Alpaca's
page cap. The original fetch path
(`AlpacaMarketDataProvider.fetch_minute_bars` in
`src/market_data_provider.py`, called directly by the first version of
`scripts/research_orb_universe_expansion.py`) reads only the `bars` field of
a single response and never inspects `next_page_token`. That is correct for
its one production caller (a single trading day's history, far short of the
page cap) but wrong for this study: a 90-session window needs roughly
30,000-45,000 minute bars per symbol -- 3-4x the page cap -- so the single
request silently returned only the **oldest ~26 trading days** of the
requested range (`2026-03-09` through roughly mid-April) and dropped
everything after it. Since the 90 study sessions run `2026-04-07` through
`2026-08-13`, only the first few sessions had any candidate-symbol coverage
at all, and even those lacked enough distinct historical sessions for the
20-session RVOL baseline. The remaining 2 of 101 cached responses (`FI`,
`MMC`) returned zero bars outright for the requested window -- a separate,
smaller anomaly, most likely an IEX feed data gap for those two names in
that window; it did not materially affect the diagnosis and remains logged
under `MISSING_OPENING_RANGE_BARS` like any other symbol with no bars for a
session.

Re-running the **corrected** funnel/report logic against that same real
(still-truncated) cache -- no new network calls, see "How this was verified"
below -- gives the exact breakdown:

| Reason | Symbol-days (top_100 universe) |
|---|---:|
| `MISSING_OPENING_RANGE_BARS` (no bars at all for that session -- outside the ~26-day page-1 coverage) | 8,429 |
| `INSUFFICIENT_RVOL_LOOKBACK_HISTORY` (bars exist for the session but fewer than 20 valid prior sessions are covered) | 371 |
| `MISSING_SPY_BARS_FOR_SESSION` (SPY's own cached response was truncated the same way) | 88 |
| **Total incomplete (top_100)** | **8,888 / 9,000 possible (98.8%)** |

By session date, incompleteness rises monotonically through the window --
exactly what page-1 truncation predicts: `2026-04-07` (first study session,
closest to the truncation cutoff) is 84/100 symbols incomplete; every
session from `2026-08-07` onward (deep past the cutoff) is 100/100
incomplete. By symbol, most alphabetically-later-tier symbols (e.g. `ABBV`,
`ABT`, `ACN`, `ADBE`, `ADI`, ...) are incomplete on all 90/90 sessions,
because their single cached page's coverage window ends before the study's
first session even begins.

**Fix:** `src/orb_universe_expansion_research.py` adds
`fetch_full_minute_history`, which issues the identical first request
(same URL + params, so it reuses the existing cache entry unchanged) and
then follows `next_page_token` across additional requests until Alpaca
returns none -- bounded by a 50-page fail-closed guard
(`MAX_PAGES_PER_SYMBOL`) against a runaway or adversarial token cycle. This
lives entirely in the research module; **`src/market_data_provider.py` (the
production single-page provider) was not modified**, since its one real
caller never needs more than one page.

A second, smaller correctness gap was fixed alongside it: the original
fetch bridged every raw Alpaca bar straight to `MinuteBar` via
`MarketBar.to_minute_bar()` without running it through the canonical
quality gate first, even though that method's own docstring says it must
only be called on bars already classified `MarketDataQuality.VALID`. Added
`eligible_minute_bars`, which applies `validate_market_bar` /
`validate_market_data_batch` (the same discipline
`_live_eligible_bars` applies in `src/live_shadow_session.py`) before the
bridge, so malformed, duplicate, or extended-hours bars are dropped rather
than silently treated as valid regular-session data.

### 2. Funnel-reporting bug -- root cause: survivor counters incremented before their own gate's elimination check

The size-100 funnel showed `gap_survivors=112` (identical to
`symbols_evaluated=112`), `opening_rvol_survivors=69`,
`spy_vwap_survivors=56`, `orb_breakout_survivors=54`, but
`total_candidates=0` -- internally inconsistent, since a breakout
"survivor" that isn't a candidate is a contradiction (breakout is the last
gate). In `_report_for_universe`
(`src/orb_universe_expansion_research.py`), each `*_survivors` counter was
incremented **before** checking whether that same gate's own elimination
state applied, e.g.:

```python
evaluated_count += 1
gap_survivors += 1                      # incremented unconditionally
if evaluation.state == GAP_ELIMINATED:  # ... then checked
    continue
```

So `gap_survivors` always equalled `symbols_evaluated` (every evaluated
symbol-day was counted as a "gap survivor," including the ones the gap gate
had just eliminated), and by the same pattern each later counter actually
reported *entrants to the next gate* rather than *survivors of its own
gate* -- `orb_breakout_survivors` counted every symbol-day that reached the
breakout gate, **including** the ones `BREAKOUT_ELIMINATED` (i.e. that
never broke out), which is exactly backwards.

**Fix:** each increment now happens strictly after that gate's own
elimination check, so a counter only advances for symbol-days that actually
cleared it:

```python
if evaluation.state == GAP_ELIMINATED:
    continue
gap_survivors += 1
```

With this fix, `orb_breakout_survivors` is now mathematically guaranteed to
equal `total_candidates` for every universe/session set (breakout is the
last gate: once a symbol-day clears SPY/VWAP and is not
`BREAKOUT_ELIMINATED`, `CANDIDATE` is the only remaining state). Re-running
the corrected code against the same real cache now reports
`gap_survivors=69, opening_rvol_survivors=56, spy_vwap_survivors=54,
orb_breakout_survivors=0, total_candidates=0` for `top_100` -- internally
consistent (0 breakout survivors, 0 candidates), even though the underlying
data is still the pre-pagination-fix truncated cache (see below).

### 3. Fail-closed coverage gate (new)

`UniverseSizeReport` now carries a `coverage` block: `possible_symbol_days`
(`universe_size * sessions`), `complete_symbol_days`, `completeness`, and a
`status` of `VALID` or `INVALID_INSUFFICIENT_COVERAGE` against a **95%**
minimum (`MINIMUM_COVERAGE`), per explicit direction on this study. Applied
retroactively to the operator's real (still pre-pagination-fix) cache, every
studied universe size correctly reports `INVALID_INSUFFICIENT_COVERAGE`
(completeness ranging `1.2%` to `4.3%`, all far below 95%) -- the gate is
verified to catch exactly the condition that produced the original
not-decision-quality run.

### How this was verified without a new network call

All of the above -- including the "re-running the corrected code" figures
-- was produced by loading the operator's **existing, unmodified** real
cached Alpaca responses from disk (same cache keys the corrected
`fetch_full_minute_history` would request as its first page) and running
them through the corrected `evaluate_symbol_session` /
`_report_for_universe`. No new Alpaca request was made. This proves the
code fixes are correct against real response shapes, but it cannot by
itself produce a complete study, because the cache still only contains each
symbol's first ~26 trading days -- the missing later pages were never
fetched. Producing the actual corrected candidate-day rates requires an
operator with Alpaca credentials to run the corrected script, which will
reuse this same cache for page 1 of every symbol and then fetch only the
new, missing pages (see below).

## Status of the empirical run

Real historical minute-bar retrieval requires an Alpaca market-data-only API
key/secret (`ALPACA_API_KEY_ID` / `ALPACA_API_SECRET_KEY`). This research
session's environment has neither set, and `config/local.yaml` -- the file
where an operator-supplied credential would normally live -- is explicitly
off-limits to this session under the repository's permanent safety rules
("Never access `local.yaml` or equivalent private configuration unless a
future milestone explicitly authorizes it"). So while an operator directing
this specific, already-approved data-retrieval run is not itself a stop
condition, this session still has no credentials with which to carry it
out.

So rather than fabricate candidate-day rates, funnel survivor counts, or a
"diminishing returns" conclusion, **this revision ships the corrected,
tested tooling, plus the real-cache-verified diagnosis above, and stops
before the corrected live run**. An operator with the market-data
credentials (broker-write scope is never required -- this only ever touches
Alpaca's read-only IEX market-data feed, the same boundary M15/M19
production code already uses) can produce the actual corrected study by
running:

```
ALPACA_API_KEY_ID=... ALPACA_API_SECRET_KEY=... \
  python scripts/research_orb_universe_expansion.py --sessions 90
```

This reuses every already-cached page-1 response (no re-fetch, no wasted
request budget) and fetches only the additional pages pagination now
requires, prints per-symbol fetch/evaluation progress and a completeness
summary as it runs, writes
`artifacts/research-universe-expansion/results/
orb_universe_expansion_study.json` and a matching `.csv` summary (both
gitignored, per "do not commit huge raw market-data dumps"), and continues
caching raw Alpaca responses under
`artifacts/research-universe-expansion/bar-cache/` (also gitignored).

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
  - `fetch_full_minute_history`: follows Alpaca's `next_page_token`
    pagination cursor to completion (see "Diagnostic findings" below for
    why this exists); `eligible_minute_bars`: applies the canonical
    quality gate before bridging to `MinuteBar`; `validate_cache_integrity`:
    removes only genuinely corrupt cache entries, preserving valid
    truncated pages for reuse.
- **`tests/test_orb_universe_expansion_research.py`** -- 26 focused tests,
  all passing, all offline (synthetic `MinuteBar`/`MarketBar` fixtures and a
  fake HTTP client, no network): universe-selection determinism and
  superset ordering; production-universe parity with tier 1;
  incremental-symbol slicing; trading-calendar walk-back;
  `evaluate_symbol_session` against constructed fixtures for each canonical
  outcome -- an approved candidate, a gap-eliminated day, an RVOL-eliminated
  day, an SPY/VWAP-eliminated day, a breakout-eliminated day, and a
  data-incomplete day; report/CSV aggregation correctness; and, added in
  this revision, regression tests for the funnel-counting bug
  (`test_funnel_survivor_counts_match_actual_gate_outcomes_not_entrants`),
  the fail-closed coverage gate at and below the 95% threshold, the
  incomplete-reason/symbol/session breakdown, pagination following a fake
  multi-page HTTP client, the runaway-pagination guard, quality filtering
  of an invalid bar, and cache-integrity eviction of corrupt-but-not-truncated
  entries.

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

`scripts/research_orb_universe_expansion.py --sessions N` defaults to the
most recent 90 trading sessions (a floor of 60 is enforced) as of the day it
is run, walked back via
`src.orb_universe_expansion_research.trading_sessions_before`, which in turn
uses the repository's authoritative `src.exchange_calendar.
regular_session_bounds` (no naive weekday-only guessing, correctly skips
holidays and observes early closes).

The operator's real run used the default 90 sessions, resolving to
`2026-04-07` through `2026-08-13` inclusive. The 90-120 session target is
reachable on the operator's Alpaca plan/request budget -- session *count*
was never the constraint; the pagination bug fixed above was. A corrected
re-run should use the same `--sessions 90` (or higher, up to 120, if the
operator wants a larger sample) once this fix is verified end-to-end.

## Results

**Not yet decision-quality.** The pre-fix real run's coverage table is
reproduced below for the record (all figures from the operator's actual
90-session run, `2026-04-07` through `2026-08-13`, before the pagination
fix -- see "Diagnostic findings" for the funnel/coverage figures behind
these numbers):

| Universe | Sessions | Possible symbol-days | Complete | Completeness | Coverage status |
|---|---:|---:|---:|---:|---|
| current_production / top_15 | 90 | 1,350 | 58 | 4.30% | INVALID_INSUFFICIENT_COVERAGE |
| top_25 | 90 | 2,250 | 71 | 3.16% | INVALID_INSUFFICIENT_COVERAGE |
| top_50 | 90 | 4,500 | 95 | 2.11% | INVALID_INSUFFICIENT_COVERAGE |
| top_75 | 90 | 6,750 | 99 | 1.47% | INVALID_INSUFFICIENT_COVERAGE |
| top_100 | 90 | 9,000 | 112 | 1.24% | INVALID_INSUFFICIENT_COVERAGE |

Every size fails the 95% coverage floor by a wide margin, so **no
candidate-day rate, funnel-survivor comparison, top-symbol ranking, or
universe-size recommendation is reported from this data** -- the fail-closed
coverage gate (see above) exists specifically to prevent treating numbers
like these as if they were valid. Once an operator runs the corrected
script with real credentials, this section should be replaced with the
`VALID`-coverage figures and:

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
