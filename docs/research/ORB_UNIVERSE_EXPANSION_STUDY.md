# ORB Universe Expansion Study

Issue: [#298](https://github.com/sanuwar/orb-live/issues/298)
Status: **pivoted from a fixed-size universe to an objectively liquidity-qualified one; the resulting 23-symbol pool is the first universe in this entire study to cross the 95% fail-closed coverage floor (98.50%, VALID), computed and evaluated entirely from the operator's real (fully paginated) IEX cache -- but its candidate-day evidence does not, on this 90-session sample, show more candidates than the current (data-invalid) 15-symbol production universe; see "Results" for the full, honest breakdown**

## Revision history

1. First pass: tooling implemented, no live run attempted (no credentials in this environment).
2. Operator ran the real 90-session study on this branch. Results were **not decision-quality**: DATA_INCOMPLETE dominated every universe size (95-99% incomplete), and the size-100 funnel showed `orb_breakout_survivors=54` with `total_candidates=0` -- an internal inconsistency.
3. Diagnosed both as code defects: single-page Alpaca fetch (no pagination) and a funnel-survivor-counter ordering bug. Fixed both, added regression tests, re-validated against the operator's real cache without a new network call.
4. Operator re-ran the corrected, paginated study. Coverage rose substantially (e.g. 15-symbol universe: 4.30% -> 87.56%) but was **still below the 95% floor** at every fixed size (15: 87.56%, 25: 82.93%, 50: 70.02%, 75: 58.09%, 100: 51.24%), dominated by `MISSING_OPENING_RANGE_BARS` (3,687 of 4,388 top-100 incomplete symbol-days) with `INSUFFICIENT_RVOL_LOOKBACK_HISTORY` (701) a smaller secondary contributor. Diagnosed both remaining causes against real cached responses: genuine single-venue IEX trading sparsity (confirmed raw-vs-canonical, not a code defect) as the dominant cause, and an RVOL-lookback warmup-window gap as a smaller secondary one. Added a research-only `--feed iex|sip` comparison capability and fixed the warmup gap.
5. **This revision pivots the research question itself**, per operator direction: rather than keep expanding a *fixed-size* universe into names an already-diagnosed feed limitation cannot reliably cover, build a universe from *objective, historical-data-derived* liquidity/data-quality criteria and study nested tiers of that qualified pool instead. Computed entirely from the operator's existing real cache (no new network call): only 23 of the 100-symbol research pool clear the documented qualification bar, so the requested 25/40/50 tiers all collapse to that same 23-symbol set -- reported exactly, not padded. See "Liquidity-qualified universe methodology" and "Results" below for the full table, thresholds, and tiered study outcome.

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

## IEX feed-sparsity diagnosis (second run)

The operator's corrected, paginated re-run raised coverage substantially
(4.30% -> 87.56% for the 15-symbol universe) but every studied size still
falls short of the 95% floor:

| Universe | Coverage | Status |
|---|---:|---|
| current_production / top_15 | 87.56% | INVALID_INSUFFICIENT_COVERAGE |
| top_25 | 82.93% | INVALID_INSUFFICIENT_COVERAGE |
| top_50 | 70.02% | INVALID_INSUFFICIENT_COVERAGE |
| top_75 | 58.09% | INVALID_INSUFFICIENT_COVERAGE |
| top_100 | 51.24% | INVALID_INSUFFICIENT_COVERAGE |

Dominant reason (top_100): `MISSING_OPENING_RANGE_BARS` 3,687 symbol-days,
`INSUFFICIENT_RVOL_LOOKBACK_HISTORY` 701 symbol-days (3,687 + 701 = 4,388,
matching `9,000 - 4,612 complete = 4,388` exactly).

### Feed used by every component (confirmed by direct source read)

| Component | File | Feed |
|---|---|---|
| Production live watcher | `src/morning_live_shadow_watcher.py:104` | `"iex"` (hardcoded) |
| Production LIVE_SHADOW session | `src/live_shadow_session.py:196` | `"iex"` (hardcoded) |
| Production provider (`AlpacaMarketDataProvider.fetch_minute_bars`) | `src/market_data_provider.py:212`, source identity `src/market_data_provider.py:73` | `"iex"` (hardcoded, plus the fixed `ALPACA_IEX_SOURCE` identity every normalized bar carries) |
| This research module's fetch, before this revision | `src/orb_universe_expansion_research.py` | `"iex"` (hardcoded) |
| This research module's fetch, this revision | `src/orb_universe_expansion_research.py` (`fetch_full_minute_history`) | **parameterized**, `--feed iex\|sip`, default `"iex"` (unchanged behavior); production files above are untouched |

Every component in this repository, production and research alike, has
always requested Alpaca's `iex` feed -- a single execution venue, not the
full US consolidated tape. This makes the operator's hypothesis directly
plausible and worth testing empirically (see "IEX vs SIP comparison"
below).

### Raw-vs-canonical evidence: which of (a)-(e) is actually happening

Representative symbols from the operator's real (now fully paginated)
cache, using the coordinator's suggested tiers: nearly-complete (`AAPL`,
`AMD`, `BAC`), moderately incomplete (`COST`, `LLY`, `XOM`), and
near-fully-incomplete (`LOW`, `UNP`, `HON`, and similarly `APD`/`BDX`, also
present in cache). Loaded directly from
`artifacts/research-universe-expansion/bar-cache/` (gitignored) via
`fetch_full_minute_history` pointed at a client that raises on any cache
miss -- confirming, first, that **every page pagination now requests is
already cached with zero misses** for all 12 symbols checked (rules out
**(d) pagination/caching loss**: the corrected pagination is retrieving the
complete requested range, `2026-03-09` through `2026-08-13`, 110 distinct
trading dates, for every symbol including the fully-incomplete ones).

Incomplete-symbol-day counts (of 90 sessions): `AAPL` 0, `AMD` 0, `BAC` 1,
`COST` 67, `LLY` 41, `XOM` 53, `LOW` 90, `UNP` 90, `HON` 90 -- confirming
incompleteness correlates with relative liquidity tier (the highest-volume
mega-caps are essentially fully covered; lower-but-still-large-cap names
are not), which is exactly the pattern single-venue IEX sparsity predicts
and a code bug would not (a code bug would not selectively spare `AAPL` and
`AMD`).

For `LOW`, `UNP`, `HON`, `COST`, `LLY`, `XOM` on session `2026-06-10`
(chosen as a mid-window date with typical coverage), comparing the **raw**
provider response against the **post-`eligible_minute_bars`** (canonically
quality-filtered) bars for the 09:25-09:40 ET window:

| Symbol | Raw bars 09:25-09:40 ET | Raw bars strictly in 09:30-09:35 | Post-filter bars in same window |
|---|---|---:|---:|
| `LOW` | 09:33, 09:34, 09:38, 09:39 | 2/5 | **identical to raw** |
| `UNP` | 09:30, 09:32, 09:33, 09:34, 09:35, 09:36, 09:37, 09:40 | 4/5 | **identical to raw** |
| `HON` | 09:30, 09:31, 09:32, 09:33, 09:35, 09:36, 09:37, 09:38, 09:39, 09:40 | 4/5 | **identical to raw** |
| `COST` | 09:30, 09:33, 09:34, 09:35, 09:36, 09:39, 09:40 | 3/5 | **identical to raw** |
| `LLY` | 09:31, 09:32, 09:34, 09:36, 09:38, 09:40 | 3/5 | **identical to raw** |
| `XOM` | 09:30, 09:31, 09:32, 09:33, 09:35, 09:36, 09:37, 09:38, 09:39, 09:40 | 4/5 | **identical to raw** |

In every case the raw response and the canonically-filtered response
contain exactly the same minutes -- **rules out (b) canonical filtering
discarding valid bars**. The present minutes are legitimate ET wall-clock
times squarely inside the expected window (no minutes at a suspicious
constant offset like 05:30 or 13:30 that a UTC/ET conversion bug would
produce), and the entry-window/timezone logic already carries its own
regression test (`test_evaluate_symbol_session_eliminates_on_spy_vwap` and
neighbors) -- **rules out (c) a timezone/session-slicing bug**. Every
symbol has raw bars on all 110 distinct trading dates in the fetch window
(no symbol-wide date gap), and the specific missing minutes are scattered
irregularly rather than falling in one contiguous blackout window that
would suggest a corporate action or listing change -- **no evidence
supports (e)**, though this is not something this tooling can rule out with
certainty for every symbol/date without an external corporate-actions feed.

**Conclusion: (a) is what is actually happening.** Alpaca's IEX feed
genuinely did not see a trade in these specific minutes for these specific
(still large-cap, but not top-tier-liquidity) symbols -- both in the raw
provider response and, consequently, after canonical filtering. This is
real feed sparsity, not a bug in this repository's code.

### IEX vs SIP comparison

**Not run in this environment** -- no Alpaca credentials are available
here (same constraint as the historical retrieval itself), and this session
does not access `config/local.yaml`. Added the capability instead:
`scripts/research_orb_universe_expansion.py --compare-feeds
--compare-symbols LOW,UNP,HON,COST,LLY,XOM --compare-dates
2026-06-10,2026-07-01` fetches the 09:25-09:40 ET window for each
symbol/date under both `feed=iex` and `feed=sip` and prints exactly which
opening-range minutes each feed's raw response contains -- this is a small,
bounded comparison (a handful of symbol/dates), not a re-run of the full
study. `--feed sip` on the main study command
(`--sessions 90 --feed sip`) would run the complete study against SIP if
the operator wants to skip straight to that.

Running this requires the operator's Alpaca account to carry a SIP
market-data entitlement (a paid tier -- IEX-only access is free). If the
account lacks that entitlement, Alpaca returns an authorization error for
`feed=sip`, which `_run_feed_comparison` surfaces directly rather than
swallowing, so the operator will see plainly whether SIP is even available
to compare.

**Recommendation:** run the comparison above before deciding on a
production data source for this study. If SIP recovers the missing
09:30-09:35 minutes for these symbols, it is very likely the correct
recommendation for a decision-quality version of this study (see "Explicit
non-goals honored" -- this recommendation is about data source, not a
strategy-threshold change). If SIP shows the same gaps, the sparsity is
structural to real market activity in that minute for that symbol, not a
feed-coverage limitation, and no realistic data source will close it.

### RVOL-lookback warmup-window issue and fix

`calculate_median_opening_volume` (`src/market_data.py`, **unmodified** by
this change) does not require its nearest `lookback_days` calendar sessions
to each be complete -- it walks backward through every historical date it
is given, skips any without a complete opening range, and stops once it
has found `lookback_days` (20) complete ones. Its accuracy is therefore
bounded entirely by how far back the **fetch window** reaches, not by that
function's own logic.

Before this revision, the fetch window's pre-study buffer
(`fetch_from`) was sized at exactly `lookback_days` (20) trading sessions
before the first study session -- implicitly assuming all 20 would have a
complete opening range. Diagnosed directly: for `COST`, of the 20 trading
sessions immediately preceding `2026-06-10`, only **11** have a complete
5-bar opening range (the same IEX sparsity above). For a study session
early in the 90-session window, where little or no extra historical buffer
exists beyond that 20-session window, `calculate_median_opening_volume`
can run out of candidates before finding 20 complete ones ->
`INSUFFICIENT_RVOL_LOOKBACK_HISTORY`. This matches the observed pattern:
top-100 incomplete-symbol-day counts fall from 84/100 on the first study
session (`2026-04-07`) to roughly 40-55/100 by August, as more historical
buffer accumulates for later sessions -- but never reaches zero, because
`MISSING_OPENING_RANGE_BARS` (the *current* session's own sparse coverage,
unrelated to lookback) remains the dominant, structural cause throughout
(84% of top-100 incompleteness overall).

**Fix (research-only, `calculate_median_opening_volume` itself untouched):**
added `historical_fetch_start(first_session_date, *, lookback_days,
warmup_multiplier=3)` (`src/orb_universe_expansion_research.py`), which
fetches `lookback_days * warmup_multiplier` (default 60, not 20) trading
sessions of buffer before the first study session, giving the existing
walk-back logic more candidates to search through. `scripts/
research_orb_universe_expansion.py` now uses this by default (override with
`--warmup-multiplier`). This is a heuristic, not a guarantee: proven with
an offline synthetic-fixture test
(`test_larger_warmup_buffer_lets_median_volume_find_enough_complete_sessions`)
that reproduces the exact failure mode (a symbol whose opening range is
complete on only every other session) and shows the plain 20-session buffer
raises `ValueError` while the widened buffer succeeds. For a symbol whose
per-session completion rate is low enough, even this buffer can still come
up short -- a genuine IEX-coverage limit that only a different data source
(SIP) or a materially larger warmup multiplier can address, not something
this fetch-window heuristic alone can guarantee.

Because the warmed-up buffer reaches further back than what is currently
cached (`fetch_from` was previously `2026-03-09`; a 3x buffer reaches
further into March or earlier), applying this fix requires fetching new
historical pages the operator has not yet retrieved -- another reason the
next re-run needs live credentials.

### Constraint check: no synthesized or interpolated bars

Verified, not just asserted: `calculate_opening_range` (`src/market_data.
py`) filters to real, non-interpolated bars and raises `ValueError` when
fewer than `opening_range_minutes` are present -- it never fabricates a
missing minute or treats a no-trade minute as zero volume. This module's
own `eligible_minute_bars` and `evaluate_symbol_session` do not add,
average, or otherwise synthesize a bar anywhere; a missing minute simply
narrows the set of real bars considered, exactly as it already did before
this revision. No change was needed or made here.

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
"diminishing returns" conclusion, **this revision ships the tested feed
comparison capability and RVOL-warmup fix, plus the raw-response-verified
diagnosis above, and stops before the third live run**. An operator with
the market-data credentials (broker-write scope is never required -- this
only ever touches Alpaca's read-only market-data feed, the same boundary
M15/M19 production code already uses) should run, in order:

1. **The IEX vs SIP comparison** (small, bounded -- not the full study):
   ```
   ALPACA_API_KEY_ID=... ALPACA_API_SECRET_KEY=... \
     python scripts/research_orb_universe_expansion.py --compare-feeds \
     --compare-symbols LOW,UNP,HON,COST,LLY,XOM \
     --compare-dates 2026-06-10,2026-07-01
   ```
   to see directly whether the account has SIP entitlement and whether SIP
   recovers the missing minutes identified above.
2. **The corrected full study**, with the widened RVOL warmup buffer, and
   `--feed sip` if step 1 shows SIP is both available and materially
   better:
   ```
   ALPACA_API_KEY_ID=... ALPACA_API_SECRET_KEY=... \
     python scripts/research_orb_universe_expansion.py --sessions 90 [--feed sip]
   ```

Both reuse every already-cached page as far as it goes (no re-fetch, no
wasted request budget) and fetch only the additional pages the wider
warmup buffer and/or a different feed now require, printing per-symbol
fetch/evaluation progress and a completeness summary as they run. Output
goes to `artifacts/research-universe-expansion/results/
orb_universe_expansion_study.json` (or `..._sip.json` under `--feed sip`)
and a matching `.csv` summary (both gitignored, per "do not commit huge raw
market-data dumps"); raw Alpaca responses continue to accumulate under
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
  - `fetch_full_minute_history`: follows Alpaca's `next_page_token`
    pagination cursor to completion, accepts a research-only `feed`
    parameter (`iex`/`sip`, default `iex`, correctly retagging a SIP bar's
    provenance since the reused `normalize_alpaca_bar` always stamps
    `ALPACA_IEX_SOURCE`); `eligible_minute_bars`: applies the canonical
    quality gate before bridging to `MinuteBar`; `validate_cache_integrity`:
    removes only genuinely corrupt cache entries, preserving valid
    truncated pages for reuse; `historical_fetch_start`: widens the
    pre-study historical fetch buffer by a configurable multiplier (default
    3x) so `calculate_median_opening_volume`'s existing walk-back search has
    enough candidate sessions to find 20 complete ones despite sparse IEX
    coverage.
- **`scripts/research_orb_universe_expansion.py`** -- the CLI entry point.
  Refuses to run below a 60-session minimum, refuses to run without
  environment-supplied Alpaca credentials (never reads `config/local.yaml`),
  reuses `UrllibMarketDataHttpClient` unchanged, and writes the JSON + CSV
  outputs described above. Adds `--feed iex|sip` (research-only; production
  feed untouched), `--warmup-multiplier` (default 3), and `--compare-feeds`
  with `--compare-symbols`/`--compare-dates` for a small, bounded IEX-vs-SIP
  raw-response comparison (not the full study).
  - `compute_symbol_liquidity_profile`: derives one symbol's objective
    liquidity/data-quality profile (avg daily dollar/share volume, median
    price, opening-window minute coverage, session opening-range-complete
    %, RVOL-history-satisfiable %) directly from its real bars in
    `O(bars + sessions)`, not `O(sessions x bars)` -- see "Liquidity-
    qualified universe methodology" below for why that distinction mattered
    in practice. `rank_liquidity_qualified_symbols`: ranks eligible symbols
    descending by dollar volume, ties broken alphabetically.
    `build_liquidity_tiers`: nested, strictly-superset tiers over that
    ranking that never pad a short tier.
  - `build_study_report(..., named_universes=...)`: folds an arbitrary
    label -> symbol-list mapping (e.g. a liquidity tier) into the same
    report structure the fixed-size tiers already use, so both share one
    aggregation path.
- **`scripts/research_orb_universe_expansion.py`** -- the CLI entry point.
  Refuses to run below a 60-session minimum, refuses to run without
  environment-supplied Alpaca credentials (never reads `config/local.yaml`),
  reuses `UrllibMarketDataHttpClient` unchanged, and writes the JSON + CSV
  outputs described above. Adds `--feed iex|sip` (research-only; production
  feed untouched), `--warmup-multiplier` (default 3), `--compare-feeds`
  with `--compare-symbols`/`--compare-dates` for a small, bounded IEX-vs-SIP
  raw-response comparison (not the full study), and `--liquidity-qualify`
  with `--liquidity-tier-sizes` for the two-phase liquidity-qualified run
  (phase 1: liquidity-profile the pool; phase 2: run the full canonical
  study only for `current_production union liquidity-qualified`, not the
  entire pool) -- writing an additional liquidity quality-table CSV and a
  `liquidity_profiles`/`liquidity_tiers`/`liquidity_thresholds` block in the
  JSON output.
- **`tests/test_orb_universe_expansion_research.py`** -- 49 focused tests,
  all passing, all offline (synthetic `MinuteBar`/`MarketBar` fixtures and a
  fake HTTP client, no network): universe-selection determinism and
  superset ordering; production-universe parity with tier 1;
  incremental-symbol slicing; trading-calendar walk-back;
  `evaluate_symbol_session` against constructed fixtures for each canonical
  outcome -- an approved candidate, a gap-eliminated day, an RVOL-eliminated
  day, an SPY/VWAP-eliminated day, a breakout-eliminated day, and a
  data-incomplete day; report/CSV aggregation correctness; regression tests
  for the funnel-counting bug
  (`test_funnel_survivor_counts_match_actual_gate_outcomes_not_entrants`),
  the fail-closed coverage gate at and below the 95% threshold, the
  incomplete-reason/symbol/session breakdown, pagination following a fake
  multi-page HTTP client, the runaway-pagination guard, quality filtering
  of an invalid bar, cache-integrity eviction of corrupt-but-not-truncated
  entries, the `feed` parameter defaulting to `iex` and correctly retagging
  `sip` bar provenance, rejection of an unsupported feed value,
  `historical_fetch_start`'s buffer-widening math, and a synthetic
  reproduction of the exact RVOL-lookback failure mode (a symbol complete
  on only every other session) proving the widened buffer resolves it
  while the plain buffer does not; and, added this revision, liquidity-
  profile qualification/exclusion for each threshold in isolation (price,
  dollar volume, share volume, opening-range coverage, RVOL-history
  coverage, no-historical-data), profile-consistency validation, ranking
  determinism and alphabetical tie-breaking under equal dollar volume,
  nested-tier superset construction, tier shortfall reporting without
  padding, rejection of non-ascending/duplicate/invalid tier sizes and
  thresholds, and `build_study_report`'s `named_universes` integration.

## Universe construction (original fixed-size methodology, superseded below)

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
API. `UNIVERSE_POOL`'s ordering (reputation/market-cap-based) is exactly
what the liquidity-qualified methodology below replaces as the basis for
ranking and tiering; the pool itself still supplies the *candidate set* the
new methodology ranks and filters, so it is still load-bearing, just no
longer used as the ordering itself.

## Liquidity-qualified universe methodology (pivot)

The naive fixed-size tiers above expand strictly by `UNIVERSE_POOL`'s
reputation-based order, with no data-quality floor -- exactly what produced
the IEX-sparsity-dominated incompleteness diagnosed above (every fixed size
landed below the 95% coverage floor, worsening as the universe grew,
because later-tier symbols are systematically less liquid on the single-
venue IEX feed). This section replaces that ordering with one built
entirely from objective, historical-data-derived measures.

### Ranking methodology

For every one of the same 100 `UNIVERSE_POOL` candidates (plus any
production symbol not already in the pool), `compute_symbol_liquidity_
profile` (`src/orb_universe_expansion_research.py`) derives, from real
historical minute bars only:

- **avg daily dollar volume** -- mean, across every trading day observed in
  the fetched history, of `sum(bar.volume * bar.close)` over that day's
  bars.
- **avg daily share volume** -- mean of `sum(bar.volume)` over the same
  days.
- **median price** -- median close across every observed bar (the
  minimum-price / penny-stock guard).
- **opening-window (09:30-09:35 ET) minute coverage %** -- total opening-
  window minutes actually present, divided by `5 x trading days observed`
  (a raw, minute-level measure across all observed history).
- **session opening-range-complete %** -- the fraction of the 90 *study*
  session dates specifically (not the whole observed history) on which
  `calculate_opening_range` (`src/market_data.py`, unmodified) would
  succeed for that symbol -- i.e. a real, non-interpolated bar exists for
  every one of the 5 opening minutes.
- **RVOL-history-satisfiable %** -- of the session dates whose own opening
  range is complete, the fraction for which walking backward through
  earlier complete-opening-range dates would find `relative_volume_
  lookback_days` (20) of them before running out of history -- i.e.
  whether `calculate_median_opening_volume`'s own precondition would be
  satisfiable, without recomputing the median value itself.

This computation runs in `O(bars + sessions)` per symbol (bars are bucketed
by date once; each date's completeness is checked via `calculate_opening_
range` on just that date's own small bucket, not a re-scan of the entire
multi-month history), not `O(sessions x bars)` -- ranking and filtering a
100-symbol pool this way takes well under a minute against the real cache,
which is what makes it practical to run before any study symbol selection
happens (a prior, slower implementation that instead ran the full
`evaluate_symbol_session` pipeline 90 times per candidate, purely to rank
them, was replaced with this design for exactly that reason).

### Qualification thresholds

| Criterion | Threshold | Rationale |
|---|---|---|
| Session opening-range-complete % | >= 95% | Weighted heaviest, per direction on this study: a 5-minute ORB strategy lives or dies on 09:30-09:35 coverage. Pinned to the same `MINIMUM_COVERAGE` the study-level fail-closed gate already uses, so a "qualified" symbol is one whose own data quality could support that gate, not an independently invented number. |
| RVOL-history-satisfiable % | >= 95% | Same rationale and same threshold -- the 20-session RVOL baseline is equally load-bearing to the strategy's own gate order. |
| Median price | >= $5 | Standard penny-stock exclusion guard. Not expected to bind for this pool (median prices among the 100 candidates start in the tens/hundreds of dollars). |
| Avg daily dollar volume | >= $1,000,000 | Conservative liquidity floor. Measured from IEX-only volume (a fraction of true consolidated volume for these names), so this is deliberately set low relative to true market liquidity -- it is a sanity guard, not the discriminator. |
| Avg daily share volume | >= 10,000 shares/day | Same rationale as dollar volume; guards against a technically-high-priced but thinly-traded name slipping through on dollar volume alone. |

A symbol must clear **every** threshold to qualify; each failed threshold
is recorded as an explicit exclusion reason (`BELOW_MINIMUM_PRICE`,
`BELOW_MINIMUM_DOLLAR_VOLUME`, `BELOW_MINIMUM_SHARE_VOLUME`,
`INSUFFICIENT_OPENING_RANGE_COVERAGE`, `INSUFFICIENT_RVOL_HISTORY_COVERAGE`,
or `NO_HISTORICAL_DATA` for a symbol with no cached bars at all), not
silently dropped.

**Caveat:** this pass reuses the fetch window already cached from the prior
round (`fetch_from = 2026-03-09`, the pre-warmup-multiplier boundary, ~20
trading sessions of buffer before the study's first session), not the wider
3x-lookback warmup buffer added in the previous revision. That buffer
requires fetching further back than what is currently cached -- credentials
this environment does not have. A handful of symbols fail only on the RVOL
criterion by a narrow margin with a perfect opening-range record (e.g.
`BKNG`: 100% opening-range-complete, 90.0% RVOL-history-satisfiable,
just under the 95% floor) and are plausible candidates to qualify once a
future run applies the wider buffer -- see "Results" for the full
near-miss list.

### Ranking and tier construction

Every symbol that clears all five thresholds is ranked descending by avg
daily dollar volume (the standard objective liquidity measure), ties broken
alphabetically for full determinism (`rank_liquidity_qualified_symbols`).
Nested tiers are built as strict prefixes of that one ranked list
(`build_liquidity_tiers`), so a larger requested tier is always a superset
of a smaller one by construction. Per explicit direction: **a tier is never
padded with a lower-quality symbol to hit a requested size** -- if fewer
symbols qualify than requested, the tier reports its actual achieved size
and a `size_shortfall: true` flag rather than silently loosening the
criteria.

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

### Fixed-size runs (superseded)

Run 1 (pre-pagination-fix, `2026-04-07` - `2026-08-13`): coverage 1.24%
(top_100) to 4.30% (top_15).

Run 2 (post-pagination-fix, same 90 sessions): coverage rose to 51.24%
(top_100) - 87.56% (top_15), still below the 95% floor at every size:

| Universe | Sessions | Possible symbol-days | Complete | Completeness | Coverage status |
|---|---:|---:|---:|---:|---|
| current_production / top_15 | 90 | 1,350 | 1,182 | 87.56% | INVALID_INSUFFICIENT_COVERAGE |
| top_25 | 90 | 2,250 | 1,866 | 82.93% | INVALID_INSUFFICIENT_COVERAGE |
| top_50 | 90 | 4,500 | 3,151 | 70.02% | INVALID_INSUFFICIENT_COVERAGE |
| top_75 | 90 | 6,750 | 3,921 | 58.09% | INVALID_INSUFFICIENT_COVERAGE |
| top_100 | 90 | 9,000 | 4,612 | 51.24% | INVALID_INSUFFICIENT_COVERAGE |

Both runs are superseded by the liquidity-qualified methodology below --
neither ever reached the 95% coverage floor, so neither ever produced a
reportable candidate-day rate, and the operator directed a pivot to
liquidity-qualified tiers rather than continuing to push the fixed-size
universe to 75/100.

### Liquidity quality table

Computed from the same real, fully-paginated IEX cache (`2026-04-07` -
`2026-08-13`, 90 sessions; buffer back to `2026-03-09`), no new network
call. Of the 100 `UNIVERSE_POOL` candidates:

- **23 symbols qualify** under every threshold above.
- **74** fail on `INSUFFICIENT_OPENING_RANGE_COVERAGE`, **67** on
  `INSUFFICIENT_RVOL_HISTORY_COVERAGE` (heavy overlap -- most failing
  symbols fail both), **2** (`FI`, `MMC`) on `NO_HISTORICAL_DATA` (the same
  zero-bar anomaly noted earlier in this doc).
- **Zero** symbols fail on price, dollar-volume, or share-volume alone --
  confirms these guard thresholds are not the discriminator for this pool;
  even the least-liquid qualifying-on-everything-else symbols clear $1M/day
  and 10,000 shares/day comfortably (e.g. the lowest observed among
  excluded symbols, `LOW`, still averages ~$25M/day).

The 23 qualified symbols, ranked descending by avg daily dollar volume
(the full per-symbol table -- all 100 rows, every metric -- is written to
`artifacts/research-universe-expansion/results/
orb_liquidity_quality_table.csv`, gitignored, by `--liquidity-qualify`):

| Rank | Symbol | Avg daily $ volume | Opening-range complete % | RVOL-history satisfiable % |
|---:|---|---:|---:|---:|
| 1 | NVDA | $928.4M | 100.0% | 100.0% |
| 2 | AMZN | $492.1M | 100.0% | 100.0% |
| 3 | AAPL | $459.4M | 100.0% | 100.0% |
| 4 | MSFT | $433.8M | 100.0% | 100.0% |
| 5 | GOOGL | $352.9M | 100.0% | 100.0% |
| 6 | META | $350.1M | 100.0% | 100.0% |
| 7 | TSLA | $343.1M | 100.0% | 100.0% |
| 8 | INTC | $331.7M | 100.0% | 100.0% |
| 9 | AMD | $290.7M | 100.0% | 100.0% |
| 10 | AVGO | $274.7M | 100.0% | 100.0% |
| 11 | NFLX | $211.7M | 100.0% | 100.0% |
| 12 | BAC | $123.2M | 100.0% | 98.9% |
| 13 | V | $116.5M | 96.7% | 98.9% |
| 14 | CSCO | $109.1M | 100.0% | 100.0% |
| 15 | NOW | $101.9M | 100.0% | 100.0% |
| 16 | WMT | $96.4M | 98.9% | 100.0% |
| 17 | CRM | $87.4M | 95.6% | 95.3% |
| 18 | KO | $81.1M | 98.9% | 97.8% |
| 19 | JPM | $76.6M | 95.6% | 97.7% |
| 20 | VZ | $70.2M | 100.0% | 98.9% |
| 21 | BSX | $69.6M | 97.8% | 96.6% |
| 22 | NKE | $52.9M | 98.9% | 100.0% |
| 23 | SLB | $47.9M | 98.9% | 100.0% |

Near-miss symbols worth flagging (excluded now, but plausible to qualify
with the wider RVOL warmup buffer from a future credentialed run): `BKNG`
(100.0% opening-range-complete, 90.0% RVOL-history-satisfiable -- fails
*only* on the buffer-limited RVOL criterion, by the narrowest margin of any
excluded symbol). No other excluded symbol is this close on a single
criterion; the rest fail the *opening-range* criterion by a wide-enough
margin (see the diagnostic table above) that a larger lookback buffer would
not change their outcome, since that criterion is about a session's own
same-day data, not historical depth.

### Nested tier construction

Requested tiers were 25/40/50. **Only 23 symbols qualify** -- fewer than
even the smallest requested tier -- so, per the explicit no-padding
instruction, `liquidity_top_25`, `liquidity_top_40`, and `liquidity_top_50`
all resolve to the **identical 23-symbol set** above (`size_shortfall:
true` on all three; `achieved_size: 23` against `requested_size: 25/40/50`
respectively). There is currently only one liquidity-qualified tier to
study, not three nested ones -- reported exactly as that, not padded to
create an artificial appearance of three distinct universes.

`current_production` (15, literal, unfiltered) and the 23-symbol
liquidity-qualified pool overlap on 12 symbols (`AAPL`, `AMZN`, `AVGO`,
`AMD`, `GOOGL`, `META`, `MSFT`, `NFLX`, `NVDA`, `TSLA`, `JPM`, `WMT`).
Production includes 3 symbols the liquidity criteria reject (`XOM`, `LLY`,
`COST` -- all diagnosed earlier in this doc as moderately IEX-sparse); the
liquidity-qualified pool adds 11 symbols production does not include
(`INTC`, `BAC`, `V`, `CSCO`, `NOW`, `CRM`, `KO`, `VZ`, `BSX`, `NKE`, `SLB`).

### Tiered study results

Run via `--liquidity-qualify` against the real cache (no new network call;
phase 2 evaluated the 26-symbol union of `current_production` and the
23-symbol qualified pool). **The liquidity-qualified universe is the first
universe construction anywhere in this study -- fixed-size or otherwise --
to cross the 95% fail-closed coverage floor:**

| Universe | Symbols | Coverage | Status | Total candidates | Sessions w/ candidate | Candidate-day rate | Zero-candidate sessions | Mean candidates/session | Median candidates on candidate days |
|---|---:|---:|---|---:|---:|---:|---:|---:|---:|
| current_production | 15 | 87.56% | INVALID_INSUFFICIENT_COVERAGE | 18 | 14/90 | 15.56% | 76 | 0.20 | 1.0 |
| liquidity_qualified_23 | 23 | **98.50%** | **VALID** | 15 | 12/90 | 13.33% | 78 | 0.167 | 1.0 |

**Funnel (corrected, non-cumulative-bug counters):**

| Universe | Symbols evaluated | Gap survivors | RVOL survivors | SPY/VWAP survivors | ORB-breakout survivors (= total candidates) |
|---|---:|---:|---:|---:|---:|
| current_production | 1,182 | 252 | 29 | 25 | 18 |
| liquidity_qualified_23 | 2,039 | 1,197 | 990 | 987 | 15 |

`orb_breakout_survivors` equals `total_candidates` in both rows, confirming
the funnel-counter fix from the prior revision holds under real data. The
gap gate is overwhelmingly the largest eliminator in both universes (production:
252 of 1,182 pass, i.e. 79% eliminated on gap alone; liquidity-23: 1,197 of
2,039, 41% eliminated -- the qualified pool's higher-liquidity names show a
materially higher gap-clearance rate, consistent with more actively-traded
names gapping >=1% more often).

**Top candidate symbols (identical in both universes):** `AMD` (4 days),
`AMZN` (3), `AVGO` (2), `META` (2), `MSFT` (2), `AAPL` (1), `WMT` (1). No
newly-added liquidity-qualified symbol (`INTC`, `BAC`, `V`, `CSCO`, `NOW`,
`CRM`, `KO`, `VZ`, `BSX`, `NKE`, `SLB` -- 11 symbols, the entire liquidity-
23-only tail) produced a single candidate day across the 90 sessions in
this sample. `LLY` (production-only, excluded from the liquidity pool for
its own data-quality reasons) produced 3 of production's 18 candidates;
those 3 are the entire difference between production's 18 total candidates
and liquidity-23's 15 (18 - 3 = 15, exactly).

**Concentration:** `AMD` alone accounts for 4/18 (22.2%) of production's
candidates and 4/15 (26.7%) of liquidity-23's -- the single most
concentrated contributor in both. The top 3 symbols (`AMD`, `AMZN`, plus
`LLY` in production / `AVGO` or `META`/`MSFT` tied in liquidity-23) account
for well over half of all candidates in both universes; candidate
generation in this sample is concentrated in a small handful of names, not
spread evenly across the universe.

**Honest reading of this result:** liquidity-qualification solved the
*data-validity* problem this entire study chain has been chasing (coverage
finally clears 95%), but in this specific 90-session sample it did **not**
increase candidate-day frequency -- it produced *fewer* total candidates
(15 vs. 18) than the (data-invalid) production baseline, because the one
excluded production symbol that happened to generate candidates (`LLY`)
outweighs the eleven newly-qualified symbols that generated none. This is
reported plainly rather than framed as a win: liquidity qualification is a
*data-quality fix*, not, on this evidence, a *candidate-frequency*
improvement. A longer sample (more sessions) or the wider RVOL warmup
buffer recovering `BKNG` and other near-misses could change this -- 90
sessions with candidates concentrated in ~7 symbols is a thin base for a
frequency claim either way.

### API/data cost implications

Phase 1 (liquidity profiling) fetched all 100 pool symbols; phase 2 (full
canonical study) fetched only the 26-symbol union of production and the
qualified pool -- 26% of the pool, not the full 100, because
`compute_symbol_liquidity_profile`'s fast `O(bars + sessions)` design lets
ranking/filtering happen before committing to the expensive full
`evaluate_symbol_session` pass. Every fetch in both phases hit the existing
on-disk cache (`artifacts/research-universe-expansion/bar-cache/`,
gitignored) with zero new Alpaca requests, since this run reused the prior
round's fully-paginated real data rather than fetching fresh.

### Diminishing returns (15 -> 25 -> 40 -> 50)

**Not measurable as requested.** Only 23 of the 100-symbol pool qualify --
fewer than even the smallest requested tier (25) -- so `liquidity_top_25`,
`liquidity_top_40`, and `liquidity_top_50` are all the *same* 23-symbol
set (see "Nested tier construction" above). There is exactly one step to
measure (`current_production` 15 -> `liquidity_qualified_23`), not three:
incremental candidates over that one step is **-3** (18 -> 15, i.e.
negative -- see "Honest reading" above), not a diminishing-returns curve.
Producing an actual 15 -> 25 -> 40 -> 50 curve requires either a
substantially larger candidate pool (more than 100 mega/large-cap names to
rank -- this pool was never designed to have 50 symbols clear a 95%
opening-range/RVOL bar under IEX) or the wider RVOL warmup buffer
recovering enough near-misses (starting with `BKNG`, the one symbol that
fails only on the buffer-limited RVOL criterion) to populate a second tier
above 23. Neither is available without further credentialed data
collection.

### Recommended universe size

**Cautiously: the 23-symbol liquidity-qualified pool as the *methodology*
for future studies, not as a confident "bigger is better" conclusion.**
It is the only universe in this entire study to reach valid (>=95%)
coverage, so it is the only one whose candidate-day numbers can be trusted
at all -- that alone makes it the defensible choice for any *future* run of
this study, and for extending the sample window. But the same run shows no
candidate-frequency benefit from the 11 symbols beyond production's
existing 12 overlapping names in this specific 90-session sample, so this
is **not** a recommendation to expand the live production universe from 15
to 23 symbols on frequency grounds -- the evidence does not support that
claim, and no change to `config/strategy_v0.yaml`'s production universe is
proposed here regardless (out of scope for this research-only study). A
future longer-sample run on this same qualified pool, or one extended by
recovering `BKNG`-class near-misses via the wider RVOL buffer, is the
natural next step before any production-universe decision.

## Explicit non-goals honored

No P&L, expectancy, or win-rate figures are computed or claimed --
`evaluate_symbol_session` only classifies signal-generation frequency, and
the repository has no lifecycle/exit simulator this study could truthfully
reuse without building a major new simulator, which was explicitly out of
scope. No threshold in `config/strategy_v0.yaml` was weakened, no production
universe was changed, `strategy.enabled` in the tracked config file remains
`false`, and no digest-pinned execution-safety file was touched.

The liquidity-qualification pivot selects a *research universe* only, per
explicit instruction: it does not touch `minimum_gap_percent`,
`minimum_relative_volume`, `opening_range_minutes`,
`require_spy_above_vwap`, `entry_start`/`entry_end`, or any other
`orb_long_v0` threshold, and the 95% fail-closed coverage gate
(`MINIMUM_COVERAGE`) is unchanged and was not weakened to make a tier
"pass." No bar is synthesized or interpolated anywhere in the liquidity
path either: `compute_symbol_liquidity_profile` calls the same unmodified
`calculate_opening_range` the rest of this module already uses, which
raises rather than fabricates a missing minute -- consistent with the
constraint check earlier in this doc, re-verified for the new code path
rather than assumed.
