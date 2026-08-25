# P3A Readiness Decision

**Evidence cutoff:** main at `e20ef011282fa08c3703b5af1811143e74b2c780`. Statuses: `READY`, `READY_WITH_LIMITATIONS`, `BLOCKED`.

## Summary

| Gate | Status |
|---|---|
| a. External methodological review | **READY** |
| b. P3B cache and data preflight | **READY** |
| c. Protocol freezing | **BLOCKED** |
| d. Statistical execution | **BLOCKED** |

This is the expected split: the package is ready to be *reviewed* and the data can be *checked*, but nothing can be frozen or run until the review returns.

## a. External methodological review — READY

`EXTERNAL_ECONOMETRICS_REVIEW_PACKET.md` is self-contained: it states the repository context, the four highest-stakes questions, the tables a reviewer needs, and the constraints that bound any advice. All 12 decisions in `STATISTICAL_DECISION_REGISTER.md` carry alternatives, consequences, and an exact reviewer question. Hypothesis levels are separated so a reviewer is not asked to accept 11 test instances as 11 independent hypotheses.

**Limitation:** no reviewer has been identified or contacted, and no named reviewer is proposed anywhere in this package. Readiness here means the packet is complete enough to send, not that sending has been authorized.

## b. P3B cache and data preflight — READY

The preflight is fully specified and can proceed independently of the review, because it asks a factual question (does the granular data exist and is it intact) rather than a methodological one. Requirements are already committed in `docs/publication/p2/STATISTICAL_INPUT_INVENTORY.md`: confirm each `artifacts/research-*/results/` cache exists; confirm contents are unmodified against the milestone hash manifest; confirm regeneration reproduces the pinned `study_results_json_sha256`; and commit the pass/fail result as its own artifact.

**Two gates can therefore run in parallel.** This is the main scheduling finding of P3A.

**Limitation:** if the preflight fails for a given milestone, the affected test instances fall below 11, and the multiplicity denominator debate in D8 changes shape. The preflight is not a formality.

## c. Protocol freezing — BLOCKED

**All 12 decisions block freezing.** A protocol cannot be frozen while its replication count, seed, block length, resampling scheme, nulls, comparators, standardization, non-evaluable treatment, model-comparison universe, inference level, family structure, and search denominator are all open. P3A deliberately did not resolve any of them; resolving them by convenience to make the protocol look finished is precisely the defect the second P2 review corrected.

Freezing requires, in order: external review returns; decisions are resolved and recorded; a **separate, later, time-stamped commit** freezes the resulting protocol. That commit is not this one and not P3A's.

## d. Statistical execution — BLOCKED

Six decisions (D1, D2, D3, D4, D5, D8) block **all** execution; no subset of the analysis can proceed. Separately, every test instance depends on data classified `SUMMARY_ONLY` pending preflight, so even a fully resolved protocol could not run today. Factor-attribution components are additionally blocked by the `NOT_AVAILABLE` factor series.

## Standing constraints unchanged by P3A

- `strategy.enabled` remains `false`.
- M55 remains `INITIALIZED_WAITING_FOR_FIRST_SIGNAL` with zero transactions and empty base and stress positions; its ledger blob is unchanged at `ce8f022d1fc9f734042de4c08feef35b9de00465`.
- M55 evidence must never be pooled into retrospective multiplicity accounting, and requires a minimum of 12 completed monthly cycles before any inferential use.
- No design has been confirmed on data it never influenced: M50 was data-blocked, M55 has zero cycles.
- Claim discipline from P1 and P2 carries forward unchanged, including the authoritative ~$59.00/month ORB standalone figure over the superseded ~$108.82-$108.90 reference arithmetic, and the research-only status of the ~$457/month momentum results given ~34% drawdown and ~-25% worst month.

## Recommended next steps, in order

1. Run the P3B cache-integrity preflight (independent of review; may start immediately).
2. Send the review packet once an operator authorizes contact and a reviewer is identified.
3. Record the reviewer's resolutions against all 12 decisions.
4. Freeze the resulting protocol in a separate time-stamped commit.
5. Only then execute P3 statistical analysis, scoped to whatever test instances survived the preflight.

Steps 3, 4, and 5 must not be reordered or merged.
