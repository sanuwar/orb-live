# Publication P3B Phase 2B-2M — M53 Isolated Materialization and Verification-Only Preflight

Issue: [#386](https://github.com/sanuwar/orb-live/issues/386). Starting main: `e99aea9c50088c20ca8e4ee6ad7741d745ff9b56`.

Phase 2B-2G merged a verification-only M53 execution gate that could only report `BLOCKED_SOURCE_OR_MATERIALIZATION`, because every input it needed was deliberately unmaterialized and PR #385 was forbidden to create them. This phase materializes those inputs under a narrow explicit authorization and runs the merged gate against them.

## Result

**`READY_FOR_SEPARATELY_AUTHORIZED_M53_EXECUTION`** — zero findings in all seven categories, with `execution_authorized=false` and `m53_executed=false`.

This is the first time the gate has reached readiness against real inputs. **It is readiness only. It is not permission to execute M53, and nothing in this package grants it.**

| Category | Findings |
|---|---:|
| `source_or_materialization` | 0 |
| `input_artifact` | 0 |
| `parameters` | 0 |
| `cache_or_isolation` | 0 |
| `guard_or_executable` | 0 |
| `output_criterion` | 0 |
| `evidence_configuration` | 0 |

## What was materialized

| Input | Result |
|---|---|
| Isolated historical checkout | commit `4c43fe96ab80d739fd10a9bd6bfa4c1319f67865`, **30/30** files verified, **28 LF + 2 CRLF** |
| M52 historical handoff | `590672f029a519b665b856106d0b35d701b464aa3f12b5a0b1978f18b18ac8f9`, 34,124 bytes |
| Cache snapshots | 3 trees, **6,214 files, 202,942,816 bytes**, all three frozen aggregate digests reproduced |
| Guard files | LF Git-object bytes at merged main; `df625207…d47b9` / 23,251 and `34fdd6c4…313dc` / 620 |
| Executable pins | Python and Git pinned by resolved file-byte content |
| Output and evidence directories | three separate, empty, non-overlapping scratch directories |

The scratch root is **preserved, not deleted**, for independent review. Its location is reported to the operator only and is deliberately absent from every committed file.

Preservation is local, not archival: the materialized inputs sit in one directory on one machine, unpublished and uncustodied. See the evidentiary-limits section of [`PHASE2B2M_READINESS_DECISION.md`](PHASE2B2M_READINESS_DECISION.md).

## Boundary

**M52 and M53 were not imported or executed.** No historical simulation, regeneration, or statistical analysis ran. No market, factor, SEC, OpenFIGI, broker, or account data was fetched. No broker or account was accessed. The guard was materialized but **not activated**, and neither guard file was imported. **No regenerated M53 artifact was created or observed.**

No original cache file was repaired, deleted, normalized, deserialized, rewritten, or renamed — all three original trees were re-verified after copying and are byte-identical to their pre-copy state. The preserved Phase 2B-1 diagnostic scratch evidence was neither read nor modified.

`strategy.enabled=false`. M55 remains `INITIALIZED_WAITING_FOR_FIRST_SIGNAL` with its ledger blob unchanged at `ce8f022d1fc9f734042de4c08feef35b9de00465`. **The M55 checkpoint was not run**: 2026-08-31 is only the first eligible signal date, the first possible simulated execution is 2026-09-01, and the first useful checkpoint remains the morning of 2026-09-02.

## Package map

- [`MATERIALIZATION_AUTHORIZATION_AND_BOUNDARY.md`](MATERIALIZATION_AUTHORIZATION_AND_BOUNDARY.md) — what this phase was and was not allowed to do, and how the scratch root was chosen and proven safe.
- [`ISOLATED_CHECKOUT_AND_SOURCE_AUDIT.md`](ISOLATED_CHECKOUT_AND_SOURCE_AUDIT.md) — clone method, EOL neutralisation, and the 30-file verification.
- [`CACHE_SNAPSHOT_COPY_AND_INTEGRITY.md`](CACHE_SNAPSHOT_COPY_AND_INTEGRITY.md) — per-tree pre-copy, scratch, and post-copy evidence.
- [`M52_HANDOFF_MATERIALIZATION.md`](M52_HANDOFF_MATERIALIZATION.md) — the historical handoff and why the regenerated artifact stays ineligible.
- [`EXECUTABLE_AND_GUARD_PIN_RECORD.md`](EXECUTABLE_AND_GUARD_PIN_RECORD.md) — guard LF materialization, shadow audit, and executable content pins.
- [`REAL_PREFLIGHT_EVIDENCE_SUMMARY.md`](REAL_PREFLIGHT_EVIDENCE_SUMMARY.md) — the gate invocation, decision, and evidence determinism.
- [`PHASE2B2M_READINESS_DECISION.md`](PHASE2B2M_READINESS_DECISION.md) — what readiness does and does not authorize, and the gates that remain.
- [`M53_REAL_PREFLIGHT_RESULT.json`](M53_REAL_PREFLIGHT_RESULT.json) — the gate's own sanitized evidence record, committed verbatim.

This pull request must not be merged without separate explicit operator authorization.
