# ORB Universe Expansion Study

Issue: [#298](https://github.com/sanuwar/orb-live/issues/298)
Status: **pagination and funnel-reporting bugs fixed and verified by the operator's corrected re-run; remaining incompleteness diagnosed as genuine IEX single-venue trading sparsity (confirmed in the raw provider response, not a code defect), compounded by an RVOL-lookback warmup-window gap now fixed; coverage still below the 95% floor -- see "IEX feed-sparsity diagnosis" for the recommended path to decision-quality coverage**

## Revision history

1. First pass: tooling implemented, no live run attempted (no credentials in this environment).
2. Operator ran the real 90-session study on this branch. Results were **not decision-quality**: DATA_INCOMPLETE dominated every universe size (95-99% incomplete), and the size-100 funnel showed `orb_breakout_survivors=54` with `total_candidates=0` -- an internal inconsistency.
3. Diagnosed both as code defects: single-page Alpaca fetch (no pagination) and a funnel-survivor-counter ordering bug. Fixed both, added regression tests, re-validated against the operator's real cache without a new network call.
4. Operator re-ran the corrected, paginated study. Coverage rose substantially (e.g. 15-symbol universe: 4.30% -> 87.56%) but is **still below the 95% floor** at every size (15: 87.56%, 25: 82.93%, 50: 70.02%, 75: 58.09%, 100: 51.24%), dominated by `MISSING_OPENING_RANGE_BARS` (3,687 of 4,388 top-100 incomplete symbol-days) with `INSUFFICIENT_RVOL_LOOKBACK_HISTORY` (701) a smaller secondary contributor. This revision diagnoses both remaining causes with raw-provider-response evidence (see below), adds a research-only `--feed iex|sip` comparison capability, and fixes the RVOL-lookback warmup-window gap. **A SIP-vs-IEX comparison and a re-run with the wider warmup buffer both still require operator-supplied credentials.**

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
- **`tests/test_orb_universe_expansion_research.py`** -- 33 focused tests,
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
  of an invalid bar, and cache-integrity eviction of corrupt-but-not-truncated
  entries; and, added this revision, the `feed` parameter defaulting to
  `iex` and correctly retagging `sip` bar provenance, rejection of an
  unsupported feed value, `historical_fetch_start`'s buffer-widening math,
  and a synthetic reproduction of the exact RVOL-lookback failure mode (a
  symbol complete on only every other session) proving the widened buffer
  resolves it while the plain buffer does not.

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

**Not yet decision-quality (two runs so far).**

Run 1 (pre-pagination-fix, `2026-04-07` - `2026-08-13`): coverage 1.24%
(top_100) to 4.30% (top_15) -- see the diagnostic table earlier in this doc.

Run 2 (post-pagination-fix, same 90 sessions), current state:

| Universe | Sessions | Possible symbol-days | Complete | Completeness | Coverage status |
|---|---:|---:|---:|---:|---|
| current_production / top_15 | 90 | 1,350 | 1,182 | 87.56% | INVALID_INSUFFICIENT_COVERAGE |
| top_25 | 90 | 2,250 | 1,866 | 82.93% | INVALID_INSUFFICIENT_COVERAGE |
| top_50 | 90 | 4,500 | 3,151 | 70.02% | INVALID_INSUFFICIENT_COVERAGE |
| top_75 | 90 | 6,750 | 3,921 | 58.09% | INVALID_INSUFFICIENT_COVERAGE |
| top_100 | 90 | 9,000 | 4,612 | 51.24% | INVALID_INSUFFICIENT_COVERAGE |

For reference only (still gated INVALID by the coverage floor, not a valid
finding): the 15-symbol universe's run-2 funnel shows 14/90 sessions with
>=1 candidate (15.56% candidate-day rate), 18 total candidates. This number
is **not reported as a finding** -- it is included only to show what the
fail-closed gate is correctly withholding, per "do not treat numbers like
these as if they were valid" below.

Every size still fails the 95% coverage floor, so **no candidate-day rate,
funnel-survivor comparison, top-symbol ranking, or universe-size
recommendation is reported from this data** -- the fail-closed coverage
gate exists specifically to prevent treating numbers like these as if they
were valid, and continues to do so correctly at this improved-but-still-
insufficient coverage level. See "IEX feed-sparsity diagnosis" above for why
coverage plateaus here and what a third run (SIP comparison, then the
warmed-up-buffer re-run) is expected to show. Once an operator completes
that and reaches `VALID` coverage, this section should be replaced with the
actual figures and:

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
