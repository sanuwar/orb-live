# M46: Liquid Small/Mid-Cap ORB Momentum Lane Feasibility

Research-only. `strategy.enabled` remains `false`. No broker calls, no
production universe or config change.

## Question

Does a frozen 20-stock small/mid-cap basket produce useful positive ORB
momentum, and materially more trade opportunity than the existing 15-stock
large-cap production lane, under identical core rules (`GAP_050_RVOL_175`,
`WAITED_FIRST_VALID_0950`, `BE_AFTER_050R`)? This is a feasibility screen
only, not universe redesign, rotation design, or a two-position experiment.

## Universe source and selection

- **Source**: SEC EDGAR NPORT-P filings for iShares Russell Mid-Cap ETF
  (IWR) and iShares Russell 2000 ETF (IWM), report period exactly
  2025-12-31 (accessions `0002071691-26-004214` / `0002071691-26-004226`,
  filed 2026-02-25). Series/class identity independently cross-verified
  against SEC's own ticker→series/class mapping
  (`sec.gov/files/company_tickers_mf.json`).
- **Ticker resolution**: CUSIP-first, ISIN-fallback via OpenFIGI. 2,647 of
  2,766 holdings resolved (95.7%); 119 preserved as unresolved/ambiguous,
  never guessed.
- **Materiality filter**: holdings below 0.01% of fund net assets are
  excluded as non-intentional residual/data-quality entries, not genuine
  index membership. Introduced as a data-quality rule *before* the P3
  feasibility run (not tuned against, and never re-tuned against, its
  outcome -- see the dedicated section below for the full accounting).
- **Liquidity screen**: median daily dollar volume ≥ $75M, median daily
  share volume ≥ 750K, median close ≥ $10, session coverage ≥ 95%, over
  the 60 sessions strictly preceding P3 (2026-01-08..2026-04-06). Computed
  from Alpaca's **SIP** (consolidated-tape) daily bars -- the IEX feed
  alone was found to capture roughly 7% of true volume for a sample name,
  which would have failed every candidate against these floors.
- **Selection**: deterministic, sector-capped (SEC SIC major group,
  max 2/sector), sorted by median dollar volume descending
  (ticker-ascending tie-break), first 10 per bucket. Both buckets reached
  the full target of 10 with no shortfall (MID pool 503 eligible, SMALL
  pool 95 eligible).

Full provenance (source hashes, resolution ledger, selection audit) is
under `artifacts/research-small-midcap-momentum-lane/` (gitignored --
large cache/vendor data; hashes are recorded in
`artifact_hashes.json` files at each stage).

### Final basket

**MID (from IWR)**: SNDK, LITE, WDC, COIN, HOOD, GLW, RKLB, COHR, VRT, ASTS

**SMALL (from IWM)**: BE, CRDO, IONQ, ECHO, APLD, CDE, HIMS, OKLO, HL, WULF

## Bugs found and fixed before finalizing this report

All three fixes are load-bearing and are documented here so the numbers
below are reproducible against the *fixed* pipeline, not a buggy one:

1. **IEX vs SIP feed** for the liquidity screen (above) -- IEX-only volume
   made every candidate fail the floors; switched to SIP.
2. **`SignalEngine.evaluate` universe-membership gate.** `SignalEngine`
   rejects any symbol not in `config["universe"]["symbols"]` regardless of
   gap/RVOL qualification. The shared `sensitivity_config` helper (reused
   from M40-M45) only overrides `minimum_gap_percent`/
   `minimum_relative_volume`, never `universe.symbols` -- so the first
   feasibility run against the 20-symbol basket silently produced **zero**
   candidate occurrences (not just zero closed trades) across all 90 P3
   sessions, which is implausible for 20 volatile momentum names and was
   traced to this gate. Fixed with an in-memory-only config override:
   `research_config_for_universe` (`src/orb_small_midcap_momentum_research.py`)
   loads the tracked `config/strategy_v0.yaml` as an immutable baseline via
   `src.config.load_config()`, deep-copies it, and replaces only the copy's
   `universe.symbols` field before calling
   `precompute_inputs_table`/`evaluate_combo`/`build_all_entry_and_exit_rows`
   -- mirroring the same in-memory-override convention
   `orb_profit_protection_exit_research._waited_shadow_decision` already
   uses for `strategy.enabled`. The tracked config file itself is loaded as
   a baseline (not "never read") but is never mutated or written back;
   `strategy.enabled` remains `false` throughout, and no production
   universe/config change is committed.
3. **Symbol/sector concentration denominator (criterion #8).** The first
   analysis pass divided the best contributor by the lane's *net* total R.
   For a losing lane, net R is negative while the top contributor can still
   be positive, so a positive numerator over a negative denominator
   produces a negative "share" that trivially clears any `<=50%` gate --
   an arithmetic artifact, not evidence of low concentration. Fixed with
   `positive_contribution_concentration` (`src/orb_small_midcap_momentum_research.py`):
   the denominator is now always the sum of strictly positive per-symbol
   (or per-sector) R contributions only; a lane with no positive
   contributor at all reports an explicit `NO_POSITIVE_CONTRIBUTORS`
   status rather than an automatic pass. This also corrected a unit bug
   (the prior calculation used dollar `realized_pnl`, not R-multiples) and
   a key-name mismatch that had silently zeroed out the MFE-edge criterion
   comparison (`pct_reaching_050R`/`pct_reaching_100R` vs the real
   `pct_reaching_05R`/`pct_reaching_10R` keys `mfe_stats` actually
   produces) -- the MFE-edge conclusion (large-cap's follow-through is
   better) does not change, but it is now grounded in the real percentages
   rather than a silent `0 > 0` comparison. See "Corrected concentration
   result" below for the exact numerators/denominators.

## Results (P3: 2026-04-07..2026-08-13, 90 sessions)

All figures: `WAITED_FIRST_VALID_0950` entry timing, `BE_AFTER_050R` exit
policy, `GAP_050_RVOL_175` signal, frozen and unmodified.

| | MID (IWR) | SMALL (IWM) | SMALL_MID combined | LARGE_CAP reference |
|---|---|---|---|---|
| Closed trades | 9 | 11 | 20 | 10 |
| Trades/month | 2.12 | 2.60 | 4.72 | 2.36 |
| Win rate | 11.1% | 18.2% | 15.0% | 40.0% |
| Expectancy R/trade | +0.007 | -0.400 | -0.217 | +0.166 |
| Profit factor (R) | 1.04 | 0.14 | 0.35 | 2.66 |
| Cumulative R | +0.06 | -4.40 | -4.34 | +1.66 |
| Max drawdown (R) | 1.52 | 4.51 | 4.45 | 1.00 |
| Longest losing streak | 5 | 7 | 8 | 3 |

Exit composition (closed trades): SMALL_MID 15 protective-stop / 5
time-exit (0 target hits); LARGE_CAP 6 protective-stop / 4 time-exit (0
target hits). Of SMALL_MID's 15 stop-outs, 6 were full initial-stop losses
(protection never activated) and 9 had breakeven protection activate but
still gave back to breakeven-or-worse.

### Momentum follow-through (MFE, post-entry information only)

| | MID | SMALL | Combined | LARGE_CAP |
|---|---|---|---|---|
| Median MFE_R | 0.76 | 0.49 | 0.56 | 0.76 |
| Mean MFE_R | 0.74 | 0.45 | 0.58 | 0.71 |
| % reaching ≥0.50R | 77.8% | 36.4% | 55.0% | 80.0% |
| % reaching ≥1.00R | 22.2% | 0.0% | 10.0% | 30.0% |

The large-cap reference shows *better* momentum follow-through than the
combined small/mid lane, not worse.

### Symbol/sector concentration (corrected)

Per-symbol R contributions in the combined lane (sum of `r_multiple` over
closed trades; positive contributors only enter the concentration
denominator):

| Symbol | R contribution |
|---|---|
| HOOD | **+1.064** |
| BE, SNDK, ASTS, GLW, COHR | 0.000 |
| CRDO | -0.104 |
| APLD | -0.295 |
| HIMS, CDE, LITE | -1.000 |
| WULF | -2.000 |

Positive total = **1.064** (HOOD is the only positive contributor).
**HOOD's share of positive cumulative R = 1.064 / 1.064 = 100%**, which
fails the frozen `<=50%` gate (criterion #8). This corrects the original
report, which had (incorrectly) shown this criterion as passing due to
the net-total-R denominator bug described above. Sector-level
concentration (sector `62`, Security Brokers/Dealers -- HOOD's sector) is
the same 100% for the same reason, but sector concentration is a
reporting diagnostic only, not one of the 9 frozen classification gates.

At least 4 sectors produced closed trades (criterion #7, unaffected by
this correction). Directions observed: 100% LONG in every lane (long-only
by construction, verified empirically, not merely assumed).

### Data/integrity checks

Zero duplicate session-date candidates (exactly one candidate/day across
each universe, as designed), zero symbols outside their declared universe,
zero `DATA_INVALID` outcomes in either lane, zero trades still open at the
data boundary. No lookahead: `simulate_exit_arm`'s replay only ever uses
bars strictly after the reference entry bar and decides each bar's outcome
from the stop already in effect entering that bar (structural property of
the shared, already-tested M43 simulator -- not re-derived here).

## Classification (corrected)

Predeclared criteria (frozen before this run), evaluated with the
corrected positive-contribution concentration math:

| # | Criterion | Result |
|---|---|---|
| 1 | SMALL_MID closed trades ≥ 20 | PASS (20) |
| 2 | SMALL_MID expectancy_r > 0 | **FAIL** (-0.217) |
| 3 | SMALL_MID PF_R > 1.10 | **FAIL** (0.35) |
| 4 | SMALL_MID trades/month > LARGE_CAP trades/month | PASS (4.72 > 2.36) |
| 5 | SMALL_MID cumulative R > 0 | **FAIL** (-4.34) |
| 6 | Expectancy excluding best symbol > 0 | **FAIL** |
| 7 | ≥4 sectors with closed trades | PASS |
| 8 | No symbol >50% of positive cumulative R | **FAIL** (HOOD = 100%, corrected from an erroneous PASS) |
| 9 | MFE edge over LARGE_CAP at ≥0.50R or ≥1.00R | **FAIL** (large-cap edge is *better*: 55% vs 80% at 0.50R, 10% vs 30% at 1.00R) |

**3 of 9 criteria pass (#1, #4, #7); 6 fail (#2, #3, #5, #6, #8, #9).**
**Verdict: NOT_PROMISING** (confirmed after correction, not assumed --
criteria #2/#3/#5/#6/#9 already failed before this correction, and #8 now
correctly fails too; the corrected concentration math changes the
criterion #8 result but not the overall verdict).

## Materiality-filter verification

`MIN_MATERIAL_POSITION_WEIGHT = 0.01%` is a data-quality/genuine-membership
rule, introduced during universe selection, *before* the P3 feasibility run
was ever computed. It is not tuned against, and was not re-tuned against,
P3 performance, and this correction does not touch it (no threshold sweep
was run).

**Exact accounting.** Across the two raw NPORT-P holdings files, 9 rows
(3 IWR + 6 IWM) report `pct_net_assets < 0.01%`:

| Issuer | Fund | Weight | Fate |
|---|---|---|---|
| PDL BioPharma, Motomova, Sterling Bancorp, Third Harmonic Bio, Zspace, Triller Group | IWM (6 rows) | 0.0000000002%–0.0001% | Never resolved to a ticker (`UNRESOLVED_AMBIGUOUS`) -- excluded upstream of the materiality filter |
| GCI Liberty, Inc. (CUSIP `361ESC049`, one of 3 rows for this issuer) | IWR | 0.0000000057% | Same issuer also holds a separate, legitimately-weighted position (ticker `GLIBK`, 0.94%); the tiny row was absorbed by share-class deduplication (`EXCLUDED_DUPLICATE_SHARE_CLASS`), not the materiality filter |
| Perrigo Co. plc (one of 2 ISIN-duplicate rows) | IWR | 0.0000092% | Same issuer also holds a separate, legitimately-weighted position (ticker `PRGO`, 1.52%); likewise absorbed by deduplication |
| **Chevron Corp.** | **IWR** | **0.0000011% ($491.52 of a $46.38B fund)** | **The only row actually excluded by the materiality filter** (`EXCLUDED_NEGLIGIBLE_POSITION_WEIGHT`, stage `MATERIALITY_FILTER`) -- no other Chevron position exists in the fund to absorb it via dedup |

**Rows the materiality filter itself excluded: exactly 1** (Chevron/CVX).
The other 8 sub-threshold rows were already gone by the time the
materiality-filter stage ran, for unrelated reasons (identifier
resolution, share-class dedup).

**Counterfactual basket impact (selection-only, not re-simulated).** This
is read directly from the actual, already-computed first (pre-fix)
selection run earlier in this same investigation -- not a new run. With
Chevron included, it ranked **#6 in the MID (IWR) sleeve** by median
dollar volume ($2,120,989,199.69/day, between HOOD at #5 and GLW at #7 in
the corrected ordering). Its presence would have displaced **ASTS**
(the corrected sleeve's #10) out of the top 10 entirely; GLW, RKLB, COHR,
and VRT would each have shifted down one rank without changing identity.
No other bucket or basket slot is affected. This is a statement about
*which symbol occupies the 10th MID slot*, not a claim about P3
performance with or without Chevron -- P3 performance was never used to
justify keeping or removing the floor.

## Next step

Per the predeclared roadmap: park this lane. Do not run a threshold-sweep
retry, do not hand-tune the basket, do not attempt a combined-universe
variant. The MID (IWR) sleeve alone is roughly breakeven (not negative)
and could be revisited independently in a future, separately-scoped
milestone if there's reason to; the SMALL (IWM) sleeve is clearly negative
and is the primary driver of the combined lane's failure.

## Reproducibility

- Trusted implementation digest: `f79587b9e58da0465973f8ac08ce26f7cb648a52952df6407c3c0a57abd4c53a` (unchanged).
- `scripts/normalize_nport_holdings_snapshot.py` -- NPORT-P → normalized holdings.
- `scripts/m46_resolve_holdings_tickers.py` -- CUSIP/ISIN → ticker.
- `scripts/m46_fetch_daily_bars.py` -- 60-session SIP daily bars.
- `scripts/m46_screen_and_select.py` -- liquidity screen + sector-capped selection.
- `scripts/m46_run_feasibility_experiment.py` -- P3 simulation, both lanes.
- `scripts/m46_analyze_feasibility_results.py` -- metrics, integrity checks, classification.
- `src/orb_small_midcap_momentum_research.py` -- tested pure selection,
  liquidity, concentration, and classification logic (61 focused tests:
  36 universe-selection/liquidity + 6 `research_config_for_universe` +
  7 `positive_contribution_concentration` + 12 `classify_feasibility`).
- All stage artifacts (source hashes, resolution ledger, selection audit,
  trade ledger, results JSON, config, artifact hashes) under
  `artifacts/research-small-midcap-momentum-lane/` (gitignored; hashes
  committed via each stage's own `artifact_hashes.json`).
