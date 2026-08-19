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
  excluded as non-intentional residual entries -- caught one real case
  (a ~0.0001%-weight Chevron position in IWR whose *unrelated* liquidity
  would otherwise have out-ranked genuine mid-caps).
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

## A real bug found and fixed before finalizing the universe

Both fixes are load-bearing and are documented here so the numbers below
are reproducible against the *fixed* pipeline, not the buggy one:

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
   traced to this gate. Fixed with an in-memory-only config override
   (`research_config_for_universe` in
   `scripts/m46_run_feasibility_experiment.py`) that sets
   `universe.symbols` to the target basket before calling
   `precompute_inputs_table`/`evaluate_combo`/`build_all_entry_and_exit_rows`
   -- mirroring the same in-memory-override convention
   `orb_profit_protection_exit_research._waited_shadow_decision` already
   uses for `strategy.enabled`. `config/strategy_v0.yaml` itself is never
   read back or written.

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

### Symbol/sector concentration

No single symbol contributed more than 50% of positive cumulative R in
the combined lane; at least 4 sectors produced closed trades. Directions
observed: 100% LONG in every lane (long-only by construction, verified
empirically, not merely assumed).

### Data/integrity checks

Zero duplicate session-date candidates (exactly one candidate/day across
each universe, as designed), zero symbols outside their declared universe,
zero `DATA_INVALID` outcomes in either lane, zero trades still open at the
data boundary. No lookahead: `simulate_exit_arm`'s replay only ever uses
bars strictly after the reference entry bar and decides each bar's outcome
from the stop already in effect entering that bar (structural property of
the shared, already-tested M43 simulator -- not re-derived here).

## Classification

Predeclared criteria (frozen before this run):

| # | Criterion | Result |
|---|---|---|
| 1 | SMALL_MID closed trades ≥ 20 | PASS (20) |
| 2 | SMALL_MID expectancy_r > 0 | **FAIL** (-0.217) |
| 3 | SMALL_MID PF_R > 1.10 | **FAIL** (0.35) |
| 4 | SMALL_MID trades/month > LARGE_CAP trades/month | PASS (4.72 > 2.36) |
| 5 | SMALL_MID R/month > 0 | **FAIL** |
| 6 | Expectancy excluding best symbol > 0 | **FAIL** |
| 7 | ≥4 sectors with closed trades | PASS |
| 8 | No symbol >50% of positive cum R | PASS |
| 9 | MFE edge over LARGE_CAP at ≥0.50R or ≥1.00R | **FAIL** (large-cap edge is *better*) |

**Verdict: NOT_PROMISING.**

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
- `src/orb_small_midcap_momentum_research.py` -- tested pure selection logic.
- All stage artifacts (source hashes, resolution ledger, selection audit,
  trade ledger, results JSON, config, artifact hashes) under
  `artifacts/research-small-midcap-momentum-lane/` (gitignored; hashes
  committed via each stage's own `artifact_hashes.json`).
