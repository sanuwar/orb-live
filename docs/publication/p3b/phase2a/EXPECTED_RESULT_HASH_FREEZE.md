# Expected-Result Hash Freeze

These hashes are **predeclared now**, before any regeneration is authorized, so that a future Phase-2B run cannot define its own success criterion. They are copied verbatim from committed manifests. **No performance figure was recomputed, and no expected value was derived from anything a rerun might produce.**

## Frozen expected outputs

| Milestone | Manifest | Field | Expected output artifact | Expected SHA-256 |
|---|---|---|---|---|
| M48 | `docs/research/ORB_CAPITAL_TARGET_STRATEGY_FAMILY_ARTIFACT_HASHES.json` | `study_results_json_sha256` | `results/study_results.json` | `3d6f79b14919249242deaf8de79ea354b92ef51014a6f7a8a4ab4c983fd84dc8` |
| M49 | `docs/research/ORB_PRACTICAL_CORE_SATELLITE_PORTFOLIO_ARTIFACT_HASHES.json` | `study_results_json_sha256` | `results/study_results.json` | `aaf79b6887307b7ab9ce19a3e1c654f2b011058e8237e13eb7e15754d7a23683` |
| M50 | `docs/research/ORB_UNTOUCHED_2019_2020_VALIDATION_ARTIFACT_HASHES.json` | `stage0_result_sha256` | `results/stage0_result.json` | `02e8482839cdd378a7fe42dbef5f89bb4ba770cced190147b507602a2865982c` |
| M52 | `docs/research/CROSS_SECTIONAL_MOMENTUM_ARTIFACT_HASHES.json` | `result_sha256` | `results/m52_result.json` | `590672f029a519b665b856106d0b35d701b464aa3f12b5a0b1978f18b18ac8f9` |
| M53 | `docs/research/MOMENTUM_RISK_CONTROL_ARTIFACT_HASHES.json` | `result_sha256` | `results/m53_result.json` | `a3d3ebbd62f45331115a74583f7f8c378e9f2e786fb693b12865fff8860122c1` |

All five were confirmed byte-identical to the corresponding local artifacts during P3B Phase 1 and are unchanged at this evidence cutoff.

## Which milestones support exact hash equality

| Milestone | Exact-equality target available? | Strength of a Phase-2B pass |
|---|---|---|
| M48 | **Yes** | Strong: exact expected output **and** verified source pins |
| M49 | **Yes** | Strong: same |
| M50 | **Yes** (stage-0 only) | Moderate: verifies the stage-0 gate, which is all M50 ever produced — no economics exist to reproduce |
| M52 | **Yes** | Strong, but only from the historical worktree (see the source-pin drift) |
| M53 | **Yes** | Strong: same |
| **M43** | **No** | Weak: reconciliation against report figures only |
| **M44** | **No** | Weak: same |
| **M45** | **No** | Weak: same |
| **M46** | **No** | Weak: same |
| **M47** | **No** | Weak: same, and its result is structurally null (n=0) |

## The M43–M47 gap

For these five there is **no committed expected-output hash of any kind**. A Phase-2B rerun could produce an artifact, but nothing predeclared exists to compare it against.

The only available reconciliation would be **weaker by construction**: comparing regenerated summary figures against the numbers written in each committed report. That is not hash equality. It depends on how the report rounds and presents values, it cannot detect changes to anything the report does not print, and it would require parsing regenerated output — which is a Phase-2B activity, not a Phase-2A one.

**This must not be presented as equivalent to the five exact-equality cases.** It is a materially weaker form of evidence, and the asymmetry is a permanent property of the record, not something Phase 2B can repair.

## Prohibitions carried into Phase 2B

1. **The expected hashes above are frozen as of this commit.** Phase 2B may not add, revise, or reinterpret them.
2. **No expected value may be derived from a rerun's own output.** If a regenerated artifact does not match, that is a finding to record, never a reason to update the target.
3. **A hash match proves byte-identity of the artifact, nothing more.** It does not establish that the raw inputs were the historical ones, nor that report headline figures were derived from that file — Phase 1 established that limit and it still holds.
4. **A mismatch is not automatically a cache defect.** It could equally indicate environment drift, dependency drift, or line-ending handling. Diagnosis must precede any conclusion.
