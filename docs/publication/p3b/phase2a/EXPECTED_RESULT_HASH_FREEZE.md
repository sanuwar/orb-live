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
| **M43** | **No** | Weak: predeclared report reconciliation (criterion frozen below) |
| **M44** | **No** | `NOT_APPLICABLE` — n=2, no statistical recovery possible |
| **M45** | **No** | Weak: predeclared report reconciliation (criterion frozen below) |
| **M46** | **No** | `NOT_APPLICABLE` — 20 trades, single period |
| **M47** | **No** | `NOT_APPLICABLE` — structurally null (n=0) |

## The M43–M47 gap, and the weak criterion that can still be predeclared

For these five there is **no committed expected-output hash of any kind**. Exact hash equality is unavailable and always will be.

An earlier draft treated that as a permanent disqualification. **That was too strong and is withdrawn.** The brief explicitly allowed weaker reconciliation where no output hash was committed. What matters is that the weaker criterion be **frozen now, before any rerun**, so a Phase-2B run cannot choose its own success condition after seeing output — the same discipline the five exact hashes enforce.

### Predeclared weak-reconciliation criterion — M43

- **Authoritative report:** `docs/research/ORB_PROFIT_PROTECTION_EXIT_EXPERIMENT.md`
- **Commit:** `264fdf3cf8ea264b14a974ce4ddaa48185b56599` · **Blob:** `9c8c4f9e5ee05621535832241fd74139adb9aa86`
- **Comparison scope:** the pooled `PRIMARY (WAITED_FIRST_VALID_0950)` results table only.
- **Fields to compare, per arm** (`BASELINE`, `BE_AFTER_050R`, `PROFIT_LADDER`, `STRUCTURE_TRAIL_AFTER_1R`): trade count; expectancy R/trade; profit factor; total R; max drawdown in R; R/month.
- **Tolerance rule:** each regenerated value must equal the committed value **exactly as printed, at the report's own decimal precision**, after rounding the regenerated value half-up to that same number of decimals. No wider tolerance is permitted, and the tolerance may not be relaxed after seeing output.
- **Fields not covered:** the `CURRENT_FIRST_VALID` sensitivity table; protection diagnostics; per-symbol contribution; any quantity the report does not print.

### Predeclared weak-reconciliation criterion — M45

- **Authoritative report:** `docs/research/ORB_LATER_FIRST_ENTRY_RECOVERY_EXPERIMENT.md`
- **Commit:** `051def4f9cce366ef128613941e634863b8efa07` · **Blob:** `91548473b15e0efdbd91c40881e323017595a541`
- **Comparison scope:** the combined-policy pooled results only.
- **Fields to compare:** later-only closed-trade count; combined trades/month; R/trade; combined R/month; max drawdown in R; later-only expectancy R.
- **Tolerance rule:** identical to M43 — exact equality at the report's printed precision, half-up rounding, no post-hoc relaxation.
- **Fields not covered:** the ex-NVDA sensitivity figure; per-period breakdowns; anything not printed.

**No figure above was recomputed.** Each is identified by report location and field name so a future run compares against committed text, not against anything derived here.

### This is weaker than byte-hash equality, explicitly

Report reconciliation cannot detect any change the report does not print; it is sensitive to the report's own rounding and presentation; and it requires parsing regenerated output, which is a Phase-2B activity. A pass under this criterion is **materially weaker evidence** than the five exact-hash cases and must never be presented as equivalent. The asymmetry is permanent; the *blockage* is not.

## Prohibitions carried into Phase 2B

1. **The expected hashes above are frozen as of this commit.** Phase 2B may not add, revise, or reinterpret them.
2. **No expected value may be derived from a rerun's own output.** If a regenerated artifact does not match, that is a finding to record, never a reason to update the target.
3. **A hash match proves byte-identity of the artifact, nothing more.** It does not establish that the raw inputs were the historical ones, nor that report headline figures were derived from that file — Phase 1 established that limit and it still holds.
4. **A mismatch is not automatically a cache defect.** It could equally indicate environment drift, dependency drift, or line-ending handling. Diagnosis must precede any conclusion.
