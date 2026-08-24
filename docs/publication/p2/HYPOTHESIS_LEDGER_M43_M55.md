# Hypothesis Ledger — M43 through M55

**Generated deterministically from `docs/publication/p2/HYPOTHESIS_LEDGER_M43_M55.json` by `scripts/publication/generate_p2_hypothesis_ledger.py`. Do not hand-edit this file; edit the JSON ledger and regenerate.**

**Evidence cutoff:** main at `614473cf792d992256278dead0da508e4fffd4e2`.

## Counting-rule reconciliation

All three totals below are produced by filtering the same 21-entry ledger; no hidden or invented experiments were created to force any total.

| Count | Definition | Value |
|---|---|---:|
| Novel decision-eligible variants | Entries where decision_eligible == true AND control_status == false AND diagnostic_sleeve == false AND entry_policy == "primary". | **14** |
| Displayed arm labels | Entries where entry_policy == "primary" (includes controls and diagnostic sleeves, excludes only the M43 CURRENT_FIRST_VALID sensitivity replays). | **17** |
| Arm-by-entry-policy/diagnostic evaluations | All entries in this ledger (entry_policy == "primary" OR entry_policy == "sensitivity"). | **21** |

No hidden or invented experiments were created to force these totals. All three totals are produced by filtering the same 21-entry ledger on the fields above; see scripts/publication/generate_p2_hypothesis_ledger.py for the deterministic reconciliation used to generate HYPOTHESIS_LEDGER_M43_M55.md.

**14 is the decision-eligible tested-variant count, not necessarily the final number of valid hypothesis tests.** A variant being decision-eligible (predeclared, not a control or diagnostic sleeve) is a different question from whether it is presently evaluable by a formal inferential procedure. See the Inferential evaluability breakdown below and `docs/publication/p2/STATISTICAL_ANALYSIS_PROTOCOL_CANDIDATE.md` for that distinction; no p-value or resampled interval is computed anywhere in this repository for any variant.

## Authoritative table (all 21 arm-by-entry-policy evaluations)

| Milestone | Arm | Entry policy | Control | Diagnostic | Decision-eligible | Inferential evaluability | Classification | Artifact |
|---|---|---|---|---|---|---|---|---|
| M43 | BASELINE | primary | yes | no | no | CONTROL_OR_DIAGNOSTIC_ONLY | Unchanged simulator/ExitEngine behavior; not itself classif… | `docs/research/ORB_PROFIT_PROTECTION_EXIT_EXPERIMENT.md` |
| M43 | BE_AFTER_050R | primary | no | no | yes | INFERENTIALLY_EVALUABLE_PENDING_GRANULAR_DATA | SUPPORTED on primary (WAITED_FIRST_VALID_0950); MIXED on CU… | `docs/research/ORB_PROFIT_PROTECTION_EXIT_EXPERIMENT.md` |
| M43 | PROFIT_LADDER | primary | no | no | yes | INFERENTIALLY_EVALUABLE_PENDING_GRANULAR_DATA | Secondary protection arm; not the leading arm (BE_AFTER_050… | `docs/research/ORB_PROFIT_PROTECTION_EXIT_EXPERIMENT.md` |
| M43 | STRUCTURE_TRAIL_AFTER_1R | primary | no | no | yes | INFERENTIALLY_EVALUABLE_PENDING_GRANULAR_DATA | Weakest of the three protection arms on pooled expectancy | `docs/research/ORB_PROFIT_PROTECTION_EXIT_EXPERIMENT.md` |
| M43 | BASELINE | sensitivity | yes | yes | no | CONTROL_OR_DIAGNOSTIC_ONLY | Sensitivity benchmark, not independently classified | `docs/research/ORB_PROFIT_PROTECTION_EXIT_EXPERIMENT.md` |
| M43 | BE_AFTER_050R | sensitivity | no | yes | no | CONTROL_OR_DIAGNOSTIC_ONLY | MIXED on this sensitivity benchmark (does not beat CURRENT_… | `docs/research/ORB_PROFIT_PROTECTION_EXIT_EXPERIMENT.md` |
| M43 | PROFIT_LADDER | sensitivity | no | yes | no | CONTROL_OR_DIAGNOSTIC_ONLY | Sensitivity benchmark, not independently classified | `docs/research/ORB_PROFIT_PROTECTION_EXIT_EXPERIMENT.md` |
| M43 | STRUCTURE_TRAIL_AFTER_1R | sensitivity | no | yes | no | CONTROL_OR_DIAGNOSTIC_ONLY | Sensitivity benchmark, not independently classified | `docs/research/ORB_PROFIT_PROTECTION_EXIT_EXPERIMENT.md` |
| M44 | FALLBACK_FIRST_VALID_0950 | primary | no | no | yes | DESCRIPTIVE_ONLY_INSUFFICIENT_SAMPLE | INSUFFICIENT_EVIDENCE | `docs/research/ORB_FALLBACK_CANDIDATE_RECOVERY_EXPERIMENT.md` |
| M45 | LATER_FIRST_VALID_1020 | primary | no | no | yes | INFERENTIALLY_EVALUABLE_PENDING_GRANULAR_DATA | MIXED (later-only expectancy 0.021R, negative excluding NVD… | `docs/research/ORB_LATER_FIRST_ENTRY_RECOVERY_EXPERIMENT.md` |
| M46 | MID | primary | no | yes | no | CONTROL_OR_DIAGNOSTIC_ONLY | Diagnostic sleeve within NOT_PROMISING combined-lane decisi… | `docs/research/ORB_SMALL_MIDCAP_MOMENTUM_LANE_FEASIBILITY.md` |
| M46 | SMALL | primary | no | yes | no | CONTROL_OR_DIAGNOSTIC_ONLY | Diagnostic sleeve within NOT_PROMISING combined-lane decisi… | `docs/research/ORB_SMALL_MIDCAP_MOMENTUM_LANE_FEASIBILITY.md` |
| M46 | SMALL_MID | primary | no | no | yes | DESCRIPTIVE_ONLY_INSUFFICIENT_SAMPLE | NOT_PROMISING | `docs/research/ORB_SMALL_MIDCAP_MOMENTUM_LANE_FEASIBILITY.md` |
| M47 | SEQUENTIAL_SECOND_VALID_1020 | primary | no | no | yes | NOT_EVALUABLE_STRUCTURAL_NULL | INSUFFICIENT_EVIDENCE, structurally null | `docs/research/ORB_SEQUENTIAL_SECOND_TRADE_FEASIBILITY.md` |
| M48 | ETF_PULLBACK_RSI2 | primary | no | no | yes | INFERENTIALLY_EVALUABLE_PENDING_GRANULAR_DATA | POSITIVE_BUT_INSUFFICIENT | `docs/research/ORB_CAPITAL_TARGET_STRATEGY_FAMILY_FEASIBILITY.md` |
| M48 | SECTOR_MOMENTUM_12_1 | primary | no | no | yes | INFERENTIALLY_EVALUABLE_PENDING_GRANULAR_DATA | POSITIVE_BUT_INSUFFICIENT | `docs/research/ORB_CAPITAL_TARGET_STRATEGY_FAMILY_FEASIBILITY.md` |
| M48 | DIVERSIFIED_TREND_12M | primary | no | no | yes | INFERENTIALLY_EVALUABLE_PENDING_GRANULAR_DATA | POSITIVE_BUT_INSUFFICIENT | `docs/research/ORB_CAPITAL_TARGET_STRATEGY_FAMILY_FEASIBILITY.md` |
| M49 | PRACTICAL_CORE_SATELLITE_30K | primary | no | no | yes | INFERENTIALLY_EVALUABLE_PENDING_GRANULAR_DATA | SAFE_BUT_BELOW_TARGET; supported practical range $150-$250/… | `docs/research/ORB_PRACTICAL_CORE_SATELLITE_PORTFOLIO_FEASIBILITY.md` |
| M52 | TOP15_MOMENTUM_BASELINE | primary | no | no | yes | INFERENTIALLY_EVALUABLE_PENDING_GRANULAR_DATA | POSITIVE_BUT_INSUFFICIENT | `docs/research/CROSS_SECTIONAL_MOMENTUM_FEASIBILITY_M52.md` |
| M53 | BREADTH_30 | primary | no | no | yes | INFERENTIALLY_EVALUABLE_PENDING_GRANULAR_DATA | POSITIVE_BUT_INSUFFICIENT | `docs/research/MOMENTUM_RISK_CONTROL_FEASIBILITY_M53.md` |
| M53 | MARKET_VOL_15 | primary | no | no | yes | INFERENTIALLY_EVALUABLE_PENDING_GRANULAR_DATA | POSITIVE_BUT_INSUFFICIENT | `docs/research/MOMENTUM_RISK_CONTROL_FEASIBILITY_M53.md` |

## Inferential evaluability breakdown (14-count basis: novel decision-eligible variants only)

Classifies each of the 14 novel decision-eligible variants by whether it is presently evaluable by a formal inferential procedure, independent of its decision-eligible status. No observation was invented and no p-value was assigned to any variant to produce this table.

| Inferential evaluability | Count | Variants |
|---|---:|---|
| DESCRIPTIVE_ONLY_INSUFFICIENT_SAMPLE | 2 | M44:FALLBACK_FIRST_VALID_0950, M46:SMALL_MID |
| INFERENTIALLY_EVALUABLE_PENDING_GRANULAR_DATA | 11 | M43:BE_AFTER_050R, M43:PROFIT_LADDER, M43:STRUCTURE_TRAIL_AFTER_1R, M45:LATER_FIRST_VALID_1020, M48:ETF_PULLBACK_RSI2, M48:SECTOR_MOMENTUM_12_1, M48:DIVERSIFIED_TREND_12M, M49:PRACTICAL_CORE_SATELLITE_30K, M52:TOP15_MOMENTUM_BASELINE, M53:BREADTH_30, M53:MARKET_VOL_15 |
| NOT_EVALUABLE_STRUCTURAL_NULL | 1 | M47:SEQUENTIAL_SECOND_VALID_1020 |

| Milestone | Arm | Inferential evaluability | Rationale |
|---|---|---|---|
| M43 | BE_AFTER_050R | INFERENTIALLY_EVALUABLE_PENDING_GRANULAR_DATA | 122 pooled trades across six periods; per-trade R-multiple series exists but is not committed at granular level (see STATISTICAL_INPUT_INVENTORY.md). |
| M43 | PROFIT_LADDER | INFERENTIALLY_EVALUABLE_PENDING_GRANULAR_DATA | 122 pooled trades across six periods; per-trade R-multiple series not committed at granular level. |
| M43 | STRUCTURE_TRAIL_AFTER_1R | INFERENTIALLY_EVALUABLE_PENDING_GRANULAR_DATA | 122 pooled trades across six periods; per-trade R-multiple series not committed at granular level. |
| M44 | FALLBACK_FIRST_VALID_0950 | DESCRIPTIVE_ONLY_INSUFFICIENT_SAMPLE | Only 2 genuine closed fallback-only trades (7 of 9 nominal selections were DATA_INVALID); no resampling or hypothesis test can be credibly fit to n=2. |
| M45 | LATER_FIRST_VALID_1020 | INFERENTIALLY_EVALUABLE_PENDING_GRANULAR_DATA | 129 later-only closed trades across six periods, a real sample; granular per-trade series not committed. |
| M46 | SMALL_MID | DESCRIPTIVE_ONLY_INSUFFICIENT_SAMPLE | 20 trades over a single 90-session period (P3 only, not pooled); the report itself labels this a feasibility screen, not a full backtest -- too thin for a credible block-bootstrap or multiplicity-adjusted test. |
| M47 | SEQUENTIAL_SECOND_VALID_1020 | NOT_EVALUABLE_STRUCTURAL_NULL | Zero valid second-trade candidates and zero second trades (n=0); structurally null, not a statistical null from low power. No p-value or resampled interval can be computed on zero observations. |
| M48 | ETF_PULLBACK_RSI2 | INFERENTIALLY_EVALUABLE_PENDING_GRANULAR_DATA | Full chronological daily simulation, 2022-01-03 to 2026-08-13; granular daily-return series not committed (see STATISTICAL_INPUT_INVENTORY.md). |
| M48 | SECTOR_MOMENTUM_12_1 | INFERENTIALLY_EVALUABLE_PENDING_GRANULAR_DATA | Full chronological daily simulation, 2022-01-03 to 2026-08-13; granular daily-return series not committed. |
| M48 | DIVERSIFIED_TREND_12M | INFERENTIALLY_EVALUABLE_PENDING_GRANULAR_DATA | Full chronological daily simulation, 2022-01-03 to 2026-08-13; granular daily-return series not committed. |
| M49 | PRACTICAL_CORE_SATELLITE_30K | INFERENTIALLY_EVALUABLE_PENDING_GRANULAR_DATA | Full chronological shared-cash simulation, 2022-01-03 to 2026-08-13; granular daily-return series not committed. |
| M52 | TOP15_MOMENTUM_BASELINE | INFERENTIALLY_EVALUABLE_PENDING_GRANULAR_DATA | 69 monthly signals, full chronological simulation, 2021-01-04 to 2026-08-13; granular daily-return series not committed. |
| M53 | BREADTH_30 | INFERENTIALLY_EVALUABLE_PENDING_GRANULAR_DATA | Same window/signal cadence as M52; granular daily-return series not committed. |
| M53 | MARKET_VOL_15 | INFERENTIALLY_EVALUABLE_PENDING_GRANULAR_DATA | Same window/signal cadence as M52; granular daily-return series not committed. |

## Strategy-family totals (14-count basis: novel decision-eligible variants only)

| Strategy family | Novel decision-eligible variants |
|---|---:|
| Cross-sectional equity momentum | 3 |
| ETF pullback / sector momentum / diversified trend | 4 |
| Intraday ORB entry/exit/frequency/universe extension | 7 |

## Evidence-class totals

| Evidence class | Count (arm-evaluations + non-ledger stages) |
|---|---:|
| blocked_validation | 1 |
| literature_selection_gate | 1 |
| prospective | 1 |
| retrospective_frozen_protocol | 21 |
| synthesis | 1 |

## Historical versus prospective distinction

All 21 arm-by-entry-policy evaluations above (M43-M53) are **retrospective**: each used a within-milestone frozen protocol, but none is independent confirmation after the broader research sequence selected families and questions. M50 is a **blocked retrospective validation attempt** (no economics computed). M51 is a **retrospective literature/feasibility screen** (no new market simulation). M54 is a **retrospective synthesis**. M55 is the only **prospective** evidence stage in the record, and it contains a frozen protocol and an initialization checkpoint only — `INITIALIZED_WAITING_FOR_FIRST_SIGNAL`, zero transactions, zero positions in both base and stress ledgers. M55 must never be pooled into retrospective multiplicity calculations for the same reason: it has not yet produced an observation.

- **M50** (`blocked_validation`): Retrospective untouched-2019-2020-window validation attempt for the unchanged M49 portfolio. Protocol valid; stopped at Stage 0 before any economics were computed. Binding blocker: Alpaca IEX minute history absent before 2020-07-27. Not counted as a hypothesis (no result was produced).
- **M51** (`literature_selection_gate`): Retrospective literature/feasibility screen scoring four strategy families against a frozen ten-criterion rubric; no new market simulation. Cross-sectional momentum selected (9/10 criteria). PEAD and defined-risk option premium were screened, not backtested, and are not counted as tested hypotheses.
- **M54** (`synthesis`): Retrospective synthesis of M43-M53 evidence, capital scaling, search accounting, and forward-path decision. No new experiment. Declares 14 tested hypotheses; its own literal named-arm listing sums to 17 (see counting_rules above) -- this ledger resolves that ambiguity, it does not repeat it.
- **M55** (`prospective`): Prospective protocol freeze and initialization-only checkpoint for the exact M52 frozen candidate. Status INITIALIZED_WAITING_FOR_FIRST_SIGNAL; zero transactions; zero positions in both base and stress ledgers. Contains no performance result and must never be pooled into retrospective multiplicity calculations.

## Superseded versus authoritative figures

| Milestone | Arm | Superseded artifact or figure |
|---|---|---|
| M44 | FALLBACK_FIRST_VALID_0950 | ~$108.82/month simple R-times-$300 reference arithmetic, superseded for dollar economics by M49's ~$59.00/month chronological simulation |
| M45 | LATER_FIRST_VALID_1020 | Initial aggregation used the wrong session-calendar provider and produced spurious DATA_INVALID outcomes; corrected and regenerated. |
| M48 | ETF_PULLBACK_RSI2 | Round-1 and Round-2 headline figures superseded by Round-4 |
| M48 | SECTOR_MOMENTUM_12_1 | Round-1 and Round-2 headline figures superseded by Round-4 |
| M48 | DIVERSIFIED_TREND_12M | Round-1 and Round-2 headline figures superseded by Round-4 |
| M49 | PRACTICAL_CORE_SATELLITE_30K | M44/M45/M48/M51's ~$108.82-$108.90/month simple R-times-$300 ORB reference arithmetic, superseded for dollar economics by this milestone's ~$59.00/month chronological simulation |
| M52 | TOP15_MOMENTUM_BASELINE | A prior in-milestone run that fabricated a ~97% drawdown / NOT_PROMISING result via a missing-bar zero-marking artifact; superseded by the corrected last-known-close marking rerun. |

## Non-ledger evidence stages (not counted as tested hypotheses: M50, M51, M54, M55)

| Milestone | Evidence class | Artifact | Commit SHA | Blob hash |
|---|---|---|---|---|
| M50 | blocked_validation | `docs/research/ORB_UNTOUCHED_2019_2020_VALIDATION_FEASIBILITY.md` | `cdae8c8f0d7b114129053f6e81c10c8165eb5ba9` | `93f745639706665f194dd8f41398ec4c038b3843` |
| M51 | literature_selection_gate | `docs/research/STRATEGY_FAMILY_SELECTION_GATE_M51.md` | `98e7c9bd8fd592000ae0acdca357a6239196f47d` | `c9e63af325efc977ba28a16cc7a74e555fca0bc0` |
| M54 | synthesis | `docs/research/CAPITAL_TARGET_EVIDENCE_SYNTHESIS_M54.md` | `d3ed93d94f319a34bed5a11b1d68e4fc3d3a2399` | `28734e37f34b0c34094a7c7917cf0d2621c6479d` |
| M55 | prospective | `docs/research/MOMENTUM_PROSPECTIVE_MONITOR_M55.md` | `27d703a4a0175e4d73e5b091d63a1a3d85fe7a6f` | `3dde8ed524d63f006c01a86d9517b75ca413c0bb` |
