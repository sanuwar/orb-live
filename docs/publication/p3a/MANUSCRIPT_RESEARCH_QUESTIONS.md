# Manuscript Research Questions

Two separate question sets. The **primary methods/research-software paper** asks whether the repository demonstrates a set of workflow properties; the **secondary empirical questions** ask what the trading evidence supports. These are deliberately not merged: the methods paper's contribution does not depend on the empirical questions being answered, and P1's `PUBLICATION_READINESS_DOSSIER.md` already established the methods route as the defensible one.

## Part 1 — Primary methods/research-software paper questions

These are **descriptive/analytic questions about an observable committed record**, not performance-style statistical hypotheses. Forcing them into null-hypothesis form would be false precision: there is no sampling model, no population, and no comparison group of alternative research programs against which a p-value would mean anything. Where a question *could* support a quantitative summary, that is stated; where it can only support a documented case description, that is stated too.

| ID | Research question | Observable evidence in the committed record | Answerable now? | What it can support | What it cannot support |
|---|---|---|---|---|---|
| MQ-1 | Does the record preserve time-stamped, version-controlled research protocols committed **before** the corresponding results? | Per-milestone protocol commits preceding results commits (e.g. M53 protocol `4c43fe9` before results `f31889b`; M55 protocol `aab1cd3` before results `27d703a`); each milestone's frozen-protocol section | **Yes** | A verifiable claim that protocol text and its commit timestamp precede the result commit, per milestone | That this constitutes formal preregistration; an independent registry was never used, and the author controls the repository |
| MQ-2 | Are positive, negative, mixed, and insufficient-evidence outcomes all traceable and retained? | M43 `SUPPORTED`; M45 `MIXED`; M46 `NOT_PROMISING`; M44/M47 `INSUFFICIENT_EVIDENCE`; M48/M52/M53 `POSITIVE_BUT_INSUFFICIENT`; M49 `SAFE_BUT_BELOW_TARGET`; M50 data-blocked | **Yes** | That unfavourable outcomes were retained and classified rather than dropped, with a per-outcome census | Absence of publication bias generally; this speaks only to what happened inside this repository |
| MQ-3 | Is the defect → detection → diagnosis → correction → rerun → verification chain traceable per defect? | P1 `HUMAN_LLM_WORKFLOW_AUDIT.md`; P2 `ACTOR_PROVENANCE_AND_CORRECTION_TAXONOMY.md`; superseded figures retained (M48 Round-1/2, M52's 97%-drawdown artifact) | **Partially** | That the *chain* is traceable and superseded figures are preserved | That the *actor* at each step is known — P2 records `UNKNOWN` for every `CORRECTION` and `DEFECT_INTRODUCTION` cell, and for six of seven `DEFECT_DETECTION` cells |
| MQ-4 | Is retrospective evidence separated from prospective M55 evidence, structurally rather than only by convention? | M55's append-only hash-chained ledger; `assert_frozen_candidate_config` runtime guard; P2 protocol candidate §16 prohibiting pooling | **Yes** | That the separation is documented, runtime-guarded against configuration drift, and so far unviolated (zero M55 transactions) | That the code makes violation impossible — an authorized human can still change code or start a new protocol outside the guard |
| MQ-5 | Are safety boundaries enforced and documented, not merely asserted? | `strategy.enabled=false` across every milestone; no broker/account call in any research path; `_no_broker_call_path_check`; repository-data guards; committed test suite | **Yes** | That specific boundaries were machine-checked and held across the program | That the boundaries are sufficient for live trading, which was never attempted |

**MQ-3 is the weakest of the five** and should be presented as such: the correction *chain* is well evidenced, but actor attribution is largely `UNKNOWN`, which limits any claim about who or what caught each defect.

## Part 2 — Secondary empirical questions

These map to the conceptual hypotheses catalogued in `FORMAL_HYPOTHESIS_CATALOG_CANDIDATE.md`. None is answerable now.

| ID | Question | Conceptual hypotheses | Blocking condition |
|---|---|---|---|
| EQ-1 | Does intraday stop-management improve risk-normalized expectancy of an already-selected ORB entry rule? | CH-1 | Granular per-trade series unverified; protocol unfrozen |
| EQ-2 | Can additional qualifying ORB trades be recovered without degrading trade quality? | CH-2 | Only one of three variants is inferentially evaluable (M45); M44 n=2, M47 n=0 |
| EQ-3 | Does the ORB signal transfer to a small/mid-cap universe? | CH-3 | **No executable test instance exists** — single 90-session period, 20 trades |
| EQ-4 | Do standalone systematic ETF strategies produce materially positive monthly returns on a fixed $30,000 account? | CH-4 | Granular daily series unverified; comparator undecided |
| EQ-5 | Does shared-capital combination improve risk-adjusted outcomes over standalone sleeves? | CH-5 | Same, plus the comparator question is itself unresolved (Decision 6) |
| EQ-6 | Does concentrated cross-sectional momentum with a trend filter produce materially positive monthly returns at $30,000? | CH-6 | Granular daily series unverified; factor series `NOT_AVAILABLE` |
| EQ-7 | Do breadth or lagged-volatility overlays improve the momentum candidate's risk-return trade-off? | CH-7 | Same |
| EQ-8 | Does the frozen M52 candidate retain performance on data it never influenced? | — (prospective) | **Requires M55**: zero completed cycles today; minimum 12 required. Also unresolved by M50, which was data-blocked |

## What separates Part 1 from Part 2

Part 1 questions are answerable from artifacts that already exist and require no new computation beyond census/tabulation. Part 2 questions require: (a) granular series that P2 classified `SUMMARY_ONLY` pending a cache-integrity preflight, (b) resolution of the decisions in `STATISTICAL_DECISION_REGISTER.md`, and for EQ-8 (c) at least 12 completed M55 cycles that do not yet exist. A manuscript may proceed on Part 1 while Part 2 remains open, provided it does not imply Part 2 has been answered.
