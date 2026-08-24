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

## Authoritative table (all 21 arm-by-entry-policy evaluations)

| Milestone | Arm | Entry policy | Control | Diagnostic | Decision-eligible | Classification | Artifact |
|---|---|---|---|---|---|---|---|
| M43 | BASELINE | primary | yes | no | no | Unchanged simulator/ExitEngine behavior; not itself classif… | `docs/research/ORB_PROFIT_PROTECTION_EXIT_EXPERIMENT.md` |
| M43 | BE_AFTER_050R | primary | no | no | yes | SUPPORTED on primary (WAITED_FIRST_VALID_0950); MIXED on CU… | `docs/research/ORB_PROFIT_PROTECTION_EXIT_EXPERIMENT.md` |
| M43 | PROFIT_LADDER | primary | no | no | yes | Secondary protection arm; not the leading arm (BE_AFTER_050… | `docs/research/ORB_PROFIT_PROTECTION_EXIT_EXPERIMENT.md` |
| M43 | STRUCTURE_TRAIL_AFTER_1R | primary | no | no | yes | Weakest of the three protection arms on pooled expectancy | `docs/research/ORB_PROFIT_PROTECTION_EXIT_EXPERIMENT.md` |
| M43 | BASELINE | sensitivity | yes | yes | no | Sensitivity benchmark, not independently classified | `docs/research/ORB_PROFIT_PROTECTION_EXIT_EXPERIMENT.md` |
| M43 | BE_AFTER_050R | sensitivity | no | yes | no | MIXED on this sensitivity benchmark (does not beat CURRENT_… | `docs/research/ORB_PROFIT_PROTECTION_EXIT_EXPERIMENT.md` |
| M43 | PROFIT_LADDER | sensitivity | no | yes | no | Sensitivity benchmark, not independently classified | `docs/research/ORB_PROFIT_PROTECTION_EXIT_EXPERIMENT.md` |
| M43 | STRUCTURE_TRAIL_AFTER_1R | sensitivity | no | yes | no | Sensitivity benchmark, not independently classified | `docs/research/ORB_PROFIT_PROTECTION_EXIT_EXPERIMENT.md` |
| M44 | FALLBACK_FIRST_VALID_0950 | primary | no | no | yes | INSUFFICIENT_EVIDENCE | `docs/research/ORB_FALLBACK_CANDIDATE_RECOVERY_EXPERIMENT.md` |
| M45 | LATER_FIRST_VALID_1020 | primary | no | no | yes | MIXED (later-only expectancy 0.021R, negative excluding NVD… | `docs/research/ORB_LATER_FIRST_ENTRY_RECOVERY_EXPERIMENT.md` |
| M46 | MID | primary | no | yes | no | Diagnostic sleeve within NOT_PROMISING combined-lane decisi… | `docs/research/ORB_SMALL_MIDCAP_MOMENTUM_LANE_FEASIBILITY.md` |
| M46 | SMALL | primary | no | yes | no | Diagnostic sleeve within NOT_PROMISING combined-lane decisi… | `docs/research/ORB_SMALL_MIDCAP_MOMENTUM_LANE_FEASIBILITY.md` |
| M46 | SMALL_MID | primary | no | no | yes | NOT_PROMISING | `docs/research/ORB_SMALL_MIDCAP_MOMENTUM_LANE_FEASIBILITY.md` |
| M47 | SEQUENTIAL_SECOND_VALID_1020 | primary | no | no | yes | INSUFFICIENT_EVIDENCE, structurally null | `docs/research/ORB_SEQUENTIAL_SECOND_TRADE_FEASIBILITY.md` |
| M48 | ETF_PULLBACK_RSI2 | primary | no | no | yes | POSITIVE_BUT_INSUFFICIENT | `docs/research/ORB_CAPITAL_TARGET_STRATEGY_FAMILY_FEASIBILITY.md` |
| M48 | SECTOR_MOMENTUM_12_1 | primary | no | no | yes | POSITIVE_BUT_INSUFFICIENT | `docs/research/ORB_CAPITAL_TARGET_STRATEGY_FAMILY_FEASIBILITY.md` |
| M48 | DIVERSIFIED_TREND_12M | primary | no | no | yes | POSITIVE_BUT_INSUFFICIENT | `docs/research/ORB_CAPITAL_TARGET_STRATEGY_FAMILY_FEASIBILITY.md` |
| M49 | PRACTICAL_CORE_SATELLITE_30K | primary | no | no | yes | SAFE_BUT_BELOW_TARGET; supported practical range $150-$250/… | `docs/research/ORB_PRACTICAL_CORE_SATELLITE_PORTFOLIO_FEASIBILITY.md` |
| M52 | TOP15_MOMENTUM_BASELINE | primary | no | no | yes | POSITIVE_BUT_INSUFFICIENT | `docs/research/CROSS_SECTIONAL_MOMENTUM_FEASIBILITY_M52.md` |
| M53 | BREADTH_30 | primary | no | no | yes | POSITIVE_BUT_INSUFFICIENT | `docs/research/MOMENTUM_RISK_CONTROL_FEASIBILITY_M53.md` |
| M53 | MARKET_VOL_15 | primary | no | no | yes | POSITIVE_BUT_INSUFFICIENT | `docs/research/MOMENTUM_RISK_CONTROL_FEASIBILITY_M53.md` |

## Strategy-family totals (14-count basis: novel decision-eligible variants only)

| Strategy family | Novel decision-eligible variants |
|---|---:|
| Cross-sectional equity momentum | 3 |
| ETF pullback / sector momentum / diversified trend | 5 |
| Large-cap intraday ORB entry/exit/frequency | 6 |

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
