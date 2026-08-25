# P3B Phase 1 Readiness Decision

**Evidence cutoff:** main at `eef11e7b368918c35ffb3ecb4b7bca2d2413cfb2`.

## Headline

**The original maximum of 11 executable candidates remains possible.** No required cache directory is missing, so no test instance is `NOT_READY_FOR_PHASE2` on presence grounds.

**That is a statement about presence only.** Nine of the eleven depend on raw caches with no directly comparable committed hash, and four of those additionally lack explicit output-to-generation-source linkage. Phase 1 moved every instance from "cache status unknown" to "cache present, integrity unresolved" — a real advance, and a smaller one than a bare candidate count suggests.

TI-08's input location, recorded as unresolved in an earlier draft of this package, has since been traced statically and all three of its input trees confirmed present; it therefore remains a candidate on resolved rather than assumed evidence. See `CACHE_TO_TEST_INSTANCE_MAP.md`.

## Counts

| Status | Count | Instances |
|---|---:|---|
| `CANDIDATE_AVAILABLE_FOR_PHASE2` | **11** | TI-01 … TI-11 |
| `NOT_READY_FOR_PHASE2` | **0** | — |
| `NOT_APPLICABLE` | 3 | DO-01, DO-02, SN-01 |

## Per-family readiness

| Family | Instances | Cache tree | Verification strength | Readiness |
|---|---|---|---|---|
| FH-1 | TI-01, TI-02, TI-03, TI-04 | M43 and M45 period checkpoints | **Weakest.** No committed artifact-hash manifest for M43/M45, so no explicit output-to-generation-source linkage; indirect Git-tree provenance exists. Pickle serialization adds a load-failure ambiguity. | `CANDIDATE_AVAILABLE_FOR_PHASE2`, lowest confidence |
| FH-2 | TI-05, TI-06, TI-07, TI-08 | M48 `daily-bar-cache/sip/` shared by all four; TI-08 additionally consumes M43's period checkpoints and the `research-universe-expansion/bar-cache/` minute cache | **Mixed.** M48 and M49 results both hash-match and both carry explicit source pins; the raw bar cache is unverified; TI-08's extra inputs are resolved and present. | `CANDIDATE_AVAILABLE_FOR_PHASE2`; TI-08 carries two additional dependencies |
| FH-3 | TI-09, TI-10, TI-11 | the shared M52 cross-sectional-momentum tree | **Strongest.** M52 and M53 results both hash-match, both have source-file pins, all three sub-caches present, JSON throughout. | `CANDIDATE_AVAILABLE_FOR_PHASE2`, highest confidence |

## Per-instance readiness

| TI | Milestone | Status | Principal qualification |
|---|---|---|---|
| TI-01 | M43 | `CANDIDATE_AVAILABLE_FOR_PHASE2` | No committed artifact-hash manifest; no explicit source linkage; pickle |
| TI-02 | M43 | `CANDIDATE_AVAILABLE_FOR_PHASE2` | as TI-01 |
| TI-03 | M43 | `CANDIDATE_AVAILABLE_FOR_PHASE2` | as TI-01 |
| TI-04 | M45 | `CANDIDATE_AVAILABLE_FOR_PHASE2` | as TI-01; also needs the per-session no-trade record |
| TI-05 | M48 | `CANDIDATE_AVAILABLE_FOR_PHASE2` | Raw bar cache unverified; results hash-match |
| TI-06 | M48 | `CANDIDATE_AVAILABLE_FOR_PHASE2` | as TI-05 |
| TI-07 | M48 | `CANDIDATE_AVAILABLE_FOR_PHASE2` | as TI-05 |
| TI-08 | M49 | `CANDIDATE_AVAILABLE_FOR_PHASE2` | Input locations statically resolved and all present; depends on three trees (M48 `sip/`, M43 checkpoints, `research-universe-expansion/bar-cache/`) |
| TI-09 | M52 | `CANDIDATE_AVAILABLE_FOR_PHASE2` | Raw caches unverified; results hash-match |
| TI-10 | M53 | `CANDIDATE_AVAILABLE_FOR_PHASE2` | Delegates to M52 caches by design |
| TI-11 | M53 | `CANDIDATE_AVAILABLE_FOR_PHASE2` | as TI-10 |

## Shared-cache concentration risk

An earlier draft of this document said every Phase-2 failure would remove an entire family. That was too coarse, and the TI-08 trace shows why: the blast radius depends on *which* tree fails.

| Cache tree | Instances affected by its failure |
|---|---|
| M52 cross-sectional-momentum tree | **TI-09, TI-10, TI-11** — the whole of FH-3, since TI-10 and TI-11 are paired against TI-09 on the identical cache |
| M48 `daily-bar-cache/sip/` | **TI-05, TI-06, TI-07, TI-08** — the whole of FH-2, since all four consume it |
| M43 `period-checkpoint/` | **TI-01, TI-02, TI-03, and TI-08** — a cross-family radius: three FH-1 instances plus one FH-2 instance |
| `research-universe-expansion/bar-cache/` | **TI-08 only** |
| M45 `period-checkpoint/` | **TI-04 only** |

So a failure of TI-08's additional ORB minute inputs removes TI-08 alone and leaves TI-05–TI-07 intact — **not every Phase-2 failure removes a whole family.** Conversely, a failure of M43's checkpoints reaches *across* families, which no earlier document anticipated. The eleven candidates remain far less independent than the count implies, mirroring the dependence P3A recorded for multiplicity purposes.

## What this decision explicitly does not say

- **No instance is statistically executable.** Phase 1 establishes cache presence, nothing more. Statistical execution additionally requires the 13 unresolved decisions in `docs/publication/p3a/STATISTICAL_DECISION_REGISTER.md` to be resolved by a qualified external finance/econometrics reviewer — six of which block all execution — and a successful Phase 2.
- **No P2 input classification changes.** Every input remains `SUMMARY_ONLY`, `NOT_AVAILABLE`, or `PROSPECTIVE_PENDING` as P2 recorded it.
- **No integrity claim is made for any raw cache.** Presence is confirmed; integrity is unresolved pending Phase 2.
- **The protocol is not frozen and P3 has not begun.**

## Standing safety state

`strategy.enabled=false`. M55 remains `INITIALIZED_WAITING_FOR_FIRST_SIGNAL` with zero transactions and empty base and stress positions, ledger blob `ce8f022d1fc9f734042de4c08feef35b9de00465`, untouched by this milestone.
