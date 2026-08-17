# ORB Existing Configs: R-Normalized Re-Evaluation

**Issue:** #325
**Branch:** `agent/m39-orb-existing-configs-r-reevaluation`
**Depends on:** PR #322 (metric-unit correction, `expectancy_r_per_trade` / `profit_factor_r` introduced),
PR #324 (documentation-only unit clarification)
**Implementation digest (unchanged throughout):** `f79587b9e58da0465973f8ac08ce26f7cb648a52952df6407c3c0a57abd4c53a`

## Purpose

This is **not a new parameter search**. Every gap/RVOL configuration previously tested across
Issue #300 (gap x RVOL sensitivity), Issue #302 (fine RVOL validation), Issue #304 (out-of-sample
validation), Issue #310 (regime diagnostic), Issue #317/#319/#321 (baseline stability studies) was
originally judged, in part, using **legacy price-per-share expectancy/profit-factor**, which PR #322
proved can disagree in *sign* with the risk-normalized (`R`) equivalent for a fixed-risk strategy.

This study re-evaluates the same, frozen set of already-tested configurations using
`expectancy_r_per_trade` and `profit_factor_r` as the **primary** decision metrics, across the same
four already-established independent historical periods, and applies a predeclared, numeric
classification rule to decide whether any existing configuration deserves to become the candidate
single-trade-per-day ORB-LIVE V1 core.

No new gap threshold, no new RVOL threshold, no new regime filter, no day-of-week filter, no
symbol whitelist, and no exit/stop/target change was introduced. This is a **re-ranking** exercise
only.

## Frozen Configuration Inventory (18 configs, no new thresholds)

The set is the exact union of the two historical grids that were empirically run:

- Issue #300 grid (`orb_gap_rvol_sensitivity_research.py`): `GAP ∈ {1.00%, 0.75%, 0.50%}` x
  `RVOL ∈ {3.0x, 2.5x, 2.0x, 1.5x}` (12 combos)
- Issue #302 grid (`orb_fine_rvol_validation_research.py`): `GAP ∈ {1.00%, 0.75%, 0.50%}` x
  `RVOL ∈ {2.00x, 1.75x, 1.50x, 1.25x}` (12 combos)

Union after removing 6 shared grid points (rvol=2.00x and rvol=1.50x per gap): **18 unique
configurations**. Historical bare "B"/"C" letters are **not** used as canonical labels because
their meaning changed between studies (e.g. #304's B/C/D/E differ from #310's A/B/C); explicit
`GAP_XXX_RVOL_YYY` labels are used instead, with every prior informal name traced below.

| CONFIG_LABEL | Gap | RVOL | First appeared | Prior names traced |
|---|---|---|---|---|
| GAP_100_RVOL_300 | 1.00% | 3.0x | Issue #300 | #300 baseline/A; #304 CONFIG_A_BASELINE; #310 BASELINE_CONFIG/CONTROL |
| GAP_100_RVOL_250 | 1.00% | 2.5x | Issue #300 | #300 grid point |
| GAP_100_RVOL_200 | 1.00% | 2.00x | Issue #300, #302 | #300 grid point; #302 grid point |
| GAP_100_RVOL_175 | 1.00% | 1.75x | Issue #302 | #302 grid point |
| GAP_100_RVOL_150 | 1.00% | 1.5x | Issue #300, #302 | #300 grid point; #302 grid point |
| GAP_100_RVOL_125 | 1.00% | 1.25x | Issue #302 | #302 grid point |
| GAP_075_RVOL_300 | 0.75% | 3.0x | Issue #300 | #300 grid point |
| GAP_075_RVOL_250 | 0.75% | 2.5x | Issue #300 | #300 grid point |
| GAP_075_RVOL_200 | 0.75% | 2.00x | Issue #300, #302 | #300 grid point; #302 grid point |
| GAP_075_RVOL_175 | 0.75% | 1.75x | Issue #302 | #302 grid point |
| GAP_075_RVOL_150 | 0.75% | 1.5x | Issue #300, #302 | #300/#302 grid point; #304 CONFIG_B_CONSERVATIVE_RELAXED; #310 PRIMARY_CONFIG_C; #317 CONFIG_B; #319 B_BASE; #321 comparator B_BASE |
| GAP_075_RVOL_125 | 0.75% | 1.25x | Issue #302 | #302 grid point; #304 CONFIG_D_HIGHER_GAP_LOWER_RVOL |
| GAP_050_RVOL_300 | 0.50% | 3.0x | Issue #300 | #300 grid point |
| GAP_050_RVOL_250 | 0.50% | 2.5x | Issue #300 | #300 grid point |
| GAP_050_RVOL_200 | 0.50% | 2.00x | Issue #300, #302 | #300 grid point; #302 grid point |
| GAP_050_RVOL_175 | 0.50% | 1.75x | Issue #302 | #302 grid point |
| GAP_050_RVOL_150 | 0.50% | 1.5x | Issue #300, #302 | #300/#302 grid point; #304 CONFIG_C_FINE_STUDY_RUNNER_UP; #310 PRIMARY_CONFIG_B; #317 CONFIG_C; #319 C_BASE; #321 P0_CONFIG |
| GAP_050_RVOL_125 | 0.50% | 1.25x | Issue #302 | #302 grid point; #304 CONFIG_E_RESEARCH_LEADER; #310 PRIMARY_CONFIG_A |

This set was printed and frozen **before** any empirical work began (see
`scripts/research_orb_existing_configs_reevaluation.py` output, first block). Focused tests assert
this table is exactly the union of the two source grids and that every entry is traceable to
Issue #300 or Issue #302 (`tests/test_orb_existing_configs_reevaluation_research.py`).

## Method

- **Periods** (fixed, unmoved): P0 = 2023-03-31..2024-04-02 (252 sessions, research calendar), P1 =
  2024-04-03..2025-04-02 (252, research calendar), P2 = 2025-04-03..2026-04-06 (252, research
  calendar), P3 = 2026-04-07..2026-08-13 (90, production calendar).
- **Universes**: each period reuses its own already-established liquidity-qualified native universe
  (P0: 7 symbols, P1: 9, P2: 17, P3: 23 symbols, plus SPY for the VWAP gate) — no re-derivation, no
  broadened symbol pool.
- **Data reuse**: cache integrity check reported **2748 usable cached bar files, 0 removed**. The
  entire study ran in cache-only mode (`fetch_missing=False`); **zero new Alpaca network fetches**
  were required for any of the four periods. This fully satisfies the "reuse existing artifacts,
  no unnecessary re-fetching" efficiency requirement.
- **Memory-bounded, one-period-at-a-time design** (inherited unchanged from Issue #319/#320): raw
  bars for one period are loaded, a `SessionSignalInputs` table is precomputed once per period
  (`precompute_inputs_table`), all 18 configs are evaluated cheaply against that one precomputed
  table (`evaluate_combo`), then the period's data is discarded before moving to the next. Observed
  resident memory stayed bounded and healthy throughout (~1-2GB depending on universe size).
- **Freshness policy**: derived per period from the actual required lookback window via
  `derive_freshness_policy_for_window` (not a hardcoded magic number) — P0: 1293 days, P1: 925 days,
  P2: 559 days, P3: 191 days.
- **Two execution views** evaluated for every configuration in every period:
  - **ALL_CANDIDATES**: every valid breakout candidate.
  - **ONE_TRADE_PER_DAY**: the deterministic **first valid candidate of the day**
    (`select_first_candidate_per_day`, chronological by signal timestamp, alphabetical tie-break).
    This is *not* "best candidate" — no ranking of same-day candidates was introduced anywhere in
    this study.
- **Primary metrics**: `expectancy_r_per_trade`, `profit_factor_r`, `average_winner_r`,
  `average_loser_r`, `gross_profit_r`, `gross_loss_r`, `cumulative_r`, win rate, stop-out rate — all
  computed from `r_multiple`, via `trade_quality_from_rows` (PR #322). Legacy price-per-share
  metrics were retained in the raw CSV/JSON output only as secondary context and were never used to
  drive classification (enforced by dedicated tests using a "contaminated" fixture with strongly
  positive price fields but negative R fields).

**Empirical runtime:** 11,628.5 seconds (≈3h 14m), cache-only, across all 4 periods x 18 configs x
2 views.

## Results by Configuration and Period (ONE_TRADE_PER_DAY view, primary)

`exp_r` = expectancy_r_per_trade, `PF_r` = profit_factor_r. Full per-period, per-view CSV:
`artifacts/research-existing-configs-reevaluation/results/orb_existing_configs_period_metrics.csv`
(not committed — regenerable from cache).

| Config | P0 exp_r | P1 exp_r | P2 exp_r | P3 exp_r | Combined exp_r | Combined PF_r |
|---|---|---|---|---|---|---|
| GAP_100_RVOL_300 | -0.1145 | -0.2694 | 0.0979 | -0.0174 | -0.0369 | 0.914 |
| GAP_100_RVOL_250 | +0.0157 | +0.0525 | +0.0399 | +0.1711 | **+0.0544** | **1.131** |
| GAP_100_RVOL_200 | -0.0771 | -0.0860 | +0.0859 | -0.0143 | -0.0139 | 0.968 |
| GAP_100_RVOL_175 | -0.1229 | -0.0887 | +0.0402 | -0.0205 | -0.0425 | 0.899 |
| GAP_100_RVOL_150 | -0.1301 | -0.1407 | +0.1490 | +0.2106 | +0.0187 | 1.047 |
| GAP_100_RVOL_125 | -0.0853 | -0.1337 | +0.0656 | +0.2842 | +0.0099 | 1.024 |
| GAP_075_RVOL_300 | -0.1588 | -0.4023 | +0.0409 | -0.0174 | -0.0961 | 0.794 |
| GAP_075_RVOL_250 | -0.0184 | -0.0432 | -0.0078 | +0.1711 | +0.0047 | 1.011 |
| GAP_075_RVOL_200 | -0.0201 | -0.0786 | +0.0446 | -0.0251 | -0.0120 | 0.971 |
| GAP_075_RVOL_175 | -0.0241 | -0.0735 | +0.0308 | -0.0295 | -0.0175 | 0.958 |
| GAP_075_RVOL_150 | -0.0246 | -0.1263 | +0.1215 | +0.1698 | +0.0304 | 1.076 |
| GAP_075_RVOL_125 | -0.0676 | -0.0890 | +0.0526 | +0.2236 | +0.0077 | 1.018 |
| GAP_050_RVOL_300 | -0.1689 | -0.2021 | +0.0050 | -0.0297 | -0.0862 | 0.816 |
| GAP_050_RVOL_250 | -0.0352 | +0.0457 | +0.0177 | +0.1617 | +0.0265 | 1.062 |
| GAP_050_RVOL_200 | -0.1150 | +0.0379 | +0.1080 | +0.0917 | +0.0263 | 1.063 |
| GAP_050_RVOL_175 | -0.0975 | +0.0377 | +0.1330 | +0.0702 | **+0.0359** | **1.087** |
| GAP_050_RVOL_150 | -0.1009 | -0.0623 | +0.1415 | +0.2156 | +0.0380 | 1.094 |
| GAP_050_RVOL_125 | -0.1290 | -0.0497 | +0.1390 | +0.2388 | +0.0330 | 1.080 |

**Observation (directionally consistent with PR #322 — different execution views, not identical
figures):** this table's ONE_TRADE_PER_DAY figures for `GAP_050_RVOL_150` are P0=-0.101 and
P1=-0.062. PR #322's headline per-period figures for the same configuration were computed on the
ALL_CANDIDATES view: P0≈-0.127, P1≈-0.022. The two views use different trade sets and are not
expected to match numerically, but both agree in direction (negative `expectancy_r_per_trade` in
P0 and P1), consistent with PR #322's corrected finding that R-normalized scoring reverses this
configuration's apparent edge in the earliest periods.

**Pattern**: P0 and P1 are net-negative for nearly every configuration under R-normalized scoring.
P2 and P3 are net-positive for nearly every configuration. Only configurations with combined
robustness surviving this P0/P1 drag qualify as CORE_CANDIDATE below.

## Combined All-Candidates View (robustness diagnostic, not primary selection)

Full table: `orb_existing_configs_all_candidates_comparison.csv`. Every configuration's
all-candidates combined `expectancy_r_per_trade` is within [-0.11, +0.013] — i.e. even the best
all-candidates figure (`GAP_100_RVOL_250`: +0.0129, PF_r=1.030) is modest, and several
one-trade-per-day winners (e.g. `GAP_050_RVOL_175`: all-candidates exp_r=-0.0091, PF_r=0.979) are
mildly negative in the noisier all-candidates view while still clearing the "not materially
negative" bar (threshold -0.05). This is expected and by design: ORB-LIVE V1's product is one
bounded daily trade, so ALL_CANDIDATES is diagnostic context, not the primary selection metric.

## Symbol-Concentration / Robustness Diagnostics (CORE_CANDIDATE configs only)

| Config | Best R symbol (one/day) | Top-3 R symbols (one/day) | Excl-TSLA one/day exp_r | Excl-best-symbol one/day exp_r | Periods positive (of 4) |
|---|---|---|---|---|---|
| GAP_100_RVOL_250 | WMT | WMT, MSFT, AVGO | +0.0505 | +0.0054 | 4/4 |
| GAP_050_RVOL_175 | MSFT | MSFT, TSLA, GOOGL | +0.0208 | +0.0023 | 3/4 |

Neither configuration's one/day edge depends on a single best-contributing symbol: excluding the
best-R symbol from each still leaves a positive (if thin) combined expectancy_r. Excluding TSLA
specifically leaves both configurations still positive.

- `GAP_100_RVOL_250`: positive in **all four** periods (P0/P1/P2/P3), PF_r>1 in all four. Best
  period P3 (+0.171 exp_r), worst period P0 (+0.016 exp_r, still positive).
- `GAP_050_RVOL_175`: positive in **3 of 4** periods (P1/P2/P3), negative in P0 (-0.0975 exp_r,
  PF_r=0.809). Best period P2 (+0.133 exp_r), worst period P0.

Combined chronological max drawdown across all four periods was **not computed** for this study —
only per-period max drawdown was reconstructed from real trade rows (`max_drawdown_and_streak_from_rows`,
stored per period/view in the period-metrics CSV). Estimating a cross-period figure without the
existing computation already covering it was avoided per the anti-estimation instruction.

## Predeclared Selection Criteria (frozen before reading final results)

A configuration is **CORE_CANDIDATE** only if **all** of the following hold:

1. Combined (all 4 periods) ONE_TRADE_PER_DAY `expectancy_r_per_trade` > 0
2. Combined ONE_TRADE_PER_DAY `profit_factor_r` > 1
3. Combined ALL_CANDIDATES `expectancy_r_per_trade` >= -0.05 (not materially negative)
4. Excluding the single best-R-contributing symbol, ONE_TRADE_PER_DAY combined
   `expectancy_r_per_trade` remains > 0 (not single-symbol dependent)
5. At least 3 of 4 periods have non-negative ONE_TRADE_PER_DAY `expectancy_r_per_trade`
6. Combined ONE_TRADE_PER_DAY closed trades >= 15 (adequate sample; `MIN_SAMPLE_CLOSED_TRADES`)

**PROMISING_BUT_UNSTABLE**: positive combined ONE_TRADE_PER_DAY R evidence (criteria 1-2, 6) but
fails one or more of criteria 3-5. **WEAK_OR_NO_EDGE**: no positive combined ONE_TRADE_PER_DAY
edge. **INSUFFICIENT_EVIDENCE**: fewer than 15 combined closed trades (did not occur — smallest
config had 63).

## Classification of All 18 Configurations

| Config | Verdict | Combined one/day exp_r | Combined one/day PF_r |
|---|---|---|---|
| GAP_100_RVOL_300 | WEAK_OR_NO_EDGE | -0.0369 | 0.914 |
| **GAP_100_RVOL_250** | **CORE_CANDIDATE** | **+0.0544** | **1.131** |
| GAP_100_RVOL_200 | WEAK_OR_NO_EDGE | -0.0139 | 0.968 |
| GAP_100_RVOL_175 | WEAK_OR_NO_EDGE | -0.0425 | 0.899 |
| GAP_100_RVOL_150 | PROMISING_BUT_UNSTABLE | +0.0187 | 1.047 |
| GAP_100_RVOL_125 | PROMISING_BUT_UNSTABLE | +0.0099 | 1.024 |
| GAP_075_RVOL_300 | WEAK_OR_NO_EDGE | -0.0961 | 0.794 |
| GAP_075_RVOL_250 | PROMISING_BUT_UNSTABLE | +0.0047 | 1.011 |
| GAP_075_RVOL_200 | WEAK_OR_NO_EDGE | -0.0120 | 0.971 |
| GAP_075_RVOL_175 | WEAK_OR_NO_EDGE | -0.0175 | 0.958 |
| GAP_075_RVOL_150 | PROMISING_BUT_UNSTABLE | +0.0304 | 1.076 |
| GAP_075_RVOL_125 | PROMISING_BUT_UNSTABLE | +0.0077 | 1.018 |
| GAP_050_RVOL_300 | WEAK_OR_NO_EDGE | -0.0862 | 0.816 |
| GAP_050_RVOL_250 | PROMISING_BUT_UNSTABLE | +0.0265 | 1.062 |
| GAP_050_RVOL_200 | PROMISING_BUT_UNSTABLE | +0.0263 | 1.063 |
| **GAP_050_RVOL_175** | **CORE_CANDIDATE** | **+0.0359** | **1.087** |
| GAP_050_RVOL_150 | PROMISING_BUT_UNSTABLE | +0.0380 | 1.094 |
| GAP_050_RVOL_125 | PROMISING_BUT_UNSTABLE | +0.0330 | 1.080 |

**0 INSUFFICIENT_EVIDENCE, 8 WEAK_OR_NO_EDGE, 8 PROMISING_BUT_UNSTABLE, 2 CORE_CANDIDATE.**

Note `GAP_050_RVOL_150` (combined exp_r=+0.0380, PF_r=1.094 — a positive aggregate result, though
not the highest of all 18; `GAP_100_RVOL_250` is higher at +0.0544) is classified
**PROMISING_BUT_UNSTABLE**, not CORE_CANDIDATE, because it fails the period-robustness check (its
P0 and P1 combined one/day `expectancy_r_per_trade` were both negative, i.e. only 2 of 4 periods
non-negative — below the 3-of-4 bar). This illustrates the predeclared rule working as intended:
a positive combined figure alone is not sufficient — period robustness is also required.

## Decision

**Both CORE_CANDIDATE configurations found: `GAP_100_RVOL_250` and `GAP_050_RVOL_175`.**

Per the decision rule, at least one existing configuration clears the predeclared bar under
R-normalized, one-trade-per-day scoring. Selected next milestone:

**A. VALIDATE_SINGLE_TRADE_CORE**

This is a **research candidate finding, not production authorization**. `strategy.enabled` remains
`false`; no production config, universe, feed, simulator, broker, order, or protection code was
touched.

## Anti-Data-Mining Confirmation

- No new gap threshold was tested (all 18 configs come from the frozen union of Issue #300/#302
  grids; focused tests assert this exactly).
- No new RVOL threshold was tested.
- No new regime filter, day-of-week filter, or P&L-based symbol whitelist was introduced.
- SPY VWAP gate logic, breakout timing, exit logic, stop logic, and target logic were unchanged.
- No ranking of same-day candidates was introduced; ONE_TRADE_PER_DAY remains strictly the
  deterministic first valid candidate of the day.

## Data Reuse

- 2748 cached bar files verified usable, 0 removed by integrity check.
- **100% of the study ran cache-only** — zero new Alpaca network fetches for any of the four
  periods' native universes.
- Per-period liquidity-qualified universes, period boundaries, memory-bounded per-period design,
  and derived freshness policies were all reused unchanged from Issue #317/#319/#320/#321.
- `precompute_inputs_table` was called exactly once per period (4 total), not once per config
  (72 would have been the naive alternative) — all 18 configs per period were evaluated cheaply
  against the same precomputed signal-inputs table.

## Empirical Runtime

11,628.5 seconds (≈3 hours 14 minutes) for all 4 periods x 18 configs x 2 views, cache-only.

## Queue (unchanged, still blocked pending single-trade-core validation)

Profit-protection / adaptive-stop experiment; final one-trade/day policy; cross-sector
multi-candidate / portfolio-risk experiment; 1 trade vs 2 diversified trades; portfolio risk
(0.5%+0.5%, 1.0%+0.5%, 1.0%+1.0%); margin evaluation; $30K chronological capital path; explicit
~$1K/month feasibility target; frontend Research & Evidence Ledger; supervised live validation;
small unattended deployment. All remain blocked behind establishing the one-trade core.

## Structured Artifacts

Generated under `artifacts/research-existing-configs-reevaluation/results/` (gitignored,
regenerable from cache via `python scripts/research_orb_existing_configs_reevaluation.py`):

- `orb_existing_configs_inventory.csv` — the 18-row frozen config inventory
- `orb_existing_configs_period_metrics.csv` — per-config, per-period, per-view R and price metrics
- `orb_existing_configs_one_trade_per_day_comparison.csv` — combined one/day comparison + classification
- `orb_existing_configs_all_candidates_comparison.csv` — combined all-candidates comparison
- `orb_existing_configs_symbol_sensitivity.csv` — per-symbol R breakdown per config
- `orb_existing_configs_combined_summary.json` — core/promising lists + next-milestone decision
- `orb_existing_configs_reevaluation_report.json` — full structured report (all fields above)
