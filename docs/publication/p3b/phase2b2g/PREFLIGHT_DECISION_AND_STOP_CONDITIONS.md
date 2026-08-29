# Preflight Decision and Stop Conditions

## The eight decisions

The gate ends with exactly one of these, and nothing else:

| Decision | Meaning |
|---|---|
| `READY_FOR_SEPARATELY_AUTHORIZED_M53_EXECUTION` | Every mechanical check passed. This is a readiness statement, never an authorization. |
| `BLOCKED_SOURCE_OR_MATERIALIZATION` | Historical commit, 30-file closure, EOL materialization policy, or on-disk source bytes disagree. |
| `BLOCKED_INPUT_ARTIFACT` | The M52 handoff is not the frozen historical artifact, or its classification was altered, or the regenerated artifact was declared eligible. |
| `BLOCKED_PARAMETERS` | The 49-record count, 5,018-byte serialization, or `7c2b2da4…` digest disagrees. |
| `BLOCKED_CACHE_OR_ISOLATION` | Cache counts, byte totals, or aggregate digests disagree; scratch trees are absent or mutated; original caches are reachable; or `fetch_missing` is not `false`. |
| `BLOCKED_GUARD_OR_EXECUTABLE` | Guard file hashes, guard configuration, readiness/terminal/verifier requirements, credential clearing, or executable content pins disagree. |
| `BLOCKED_OUTPUT_CRITERION` | The expected artifact digest/size, exclusion set, or canonical payload disagrees, or the two classifications were merged. |
| `BLOCKED_EVIDENCE_CONFIGURATION` | The manifest is structurally invalid, an execution/output/evidence path is missing, ambiguous, or already exists, or an unexpected internal failure occurred. |

Every record, whatever the decision, carries `execution_authorized=false` and `m53_executed=false`. No option can change either. There is no input — malformed, hostile, or merely surprising — for which the gate emits no record: an unexpected internal exception is caught and reported as an `internal_error` finding carrying only the exception type name.

## Category precedence

When several categories block, the decision names the earliest of:

`source_or_materialization` → `input_artifact` → `parameters` → `cache_or_isolation` → `guard_or_executable` → `output_criterion` → `evidence_configuration`

A wrong source or wrong historical input invalidates everything downstream, so those are named first. All findings in all categories are still reported; precedence only picks the label, which keeps the decision a deterministic function of the finding set.

## Current decision

**`BLOCKED_SOURCE_OR_MATERIALIZATION`**, from the committed manifest with no materialized environment — the only configuration this PR permits. Seven findings across five categories:

```
cache_or_isolation      scratch_caches_not_materialized   no scratch cache root was supplied
evidence_configuration  evidence_dir_not_supplied         no evidence directory was supplied
guard_or_executable     executable_not_pinned             no git executable was supplied for content pinning
guard_or_executable     executable_not_pinned             no python executable was supplied for content pinning
guard_or_executable     guard_not_materialized            no guard directory was supplied
input_artifact          handoff_not_materialized          no checkout was supplied to hold the M52 handoff
source_or_materialization  checkout_not_materialized      no isolated historical checkout was supplied
```

`parameters` and `output_criterion` produced **zero** findings, and the frozen-reference reconciliation produced **zero** findings: the manifest's parameter digest, output criterion, historical commit, closure shape, M52 handoff, guard digests, and cache evidence all reconcile exactly against the merged Phase 2A/2B-1/2B-2A/2B-2N records.

Everything blocking today is a materialization gap that this PR is explicitly forbidden to close: materializing the historical checkout, copying caches to scratch, and exposing a guard directory all require separate operator authorization.

## Pre-run stop conditions

A later authorized run must stop **before** invocation, without retry or adjustment, if any of these holds:

1. The gate decision is anything other than `READY_FOR_SEPARATELY_AUTHORIZED_M53_EXECUTION`.
2. Explicit operator authorization for the M53 invocation is absent, ambiguous, or reused from an earlier run.
3. The historical commit is not `4c43fe96ab80d739fd10a9bd6bfa4c1319f67865`, or any of the 30 closure files fails its byte check.
4. The M52 handoff is not the frozen historical artifact by both hash and size, or fails a strict UTF-8 JSON read.
5. The parameter record does not reconstruct to 49 records, 5,018 bytes, and `7c2b2da4…`.
6. Any cache count, byte total, or aggregate digest differs from the frozen baseline, before or after copying.
7. Original caches are reachable from the checkout, or a scratch copy used a symlink or junction.
8. Credentials or proxy variables cannot be cleared, or `--fetch-missing` is not omitted.
9. The trusted external pre-launch hash check over both guard files does not match the pinned Git-object digests, or the guard directory is not first on `PYTHONPATH`, or the interpreter was started with `-S`.
10. The Python or Git executable cannot be pinned by file-byte hash.
11. The evidence log path already exists, is a symlink, or its parent is not an existing absolute non-symlink directory.
12. An M53 output artifact already exists at the target path.
13. The expected output criterion cannot be verified, or anyone proposes an additional exclusion.

## Post-run stop conditions

After a later authorized invocation, stop **without retry, adjustment, or a second run** if any of these holds. Preserve the failure as evidence.

1. The CLI exits non-zero, or is terminated for any reason.
2. A cache miss, credential request, or network attempt occurs.
3. Any cache mutates — scratch or original.
4. The guard produced no `guard_ready` record, or no terminal `normal_interpreter_exit` record.
5. `verify_guard_run()` fails for any reason: absent file, broken or truncated chain, a `guard_failure` record, or **any** `denied_*` record.
6. Evidence is truncated, unreadable, or missing.
7. Source or parameter verification changes between pre-run and post-run checks.
8. A non-excluded output JSON Pointer differs.
9. Anyone proposes a new exclusion, parameter, dependency, environment, source, or criterion adjustment after a regenerated result has been seen.

No parameter, dependency, environment, source, or criterion may be adjusted after a regenerated result is observed without new authorization **and** a new independently reviewed criterion, disclosed as post-result.

## Rerun policy

A used run identity is never reused. A failed run is not retried under the same authorization: it produces evidence, and any subsequent attempt requires a fresh authorization, a fresh evidence directory, and a fresh gate pass.
