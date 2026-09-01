# Phase 2B-2M Readiness Decision

## Decision

**`READY_FOR_SEPARATELY_AUTHORIZED_M53_EXECUTION`**, with `execution_authorized=false` and `m53_executed=false`.

Zero findings in all seven gate categories, against real materialized inputs, reproduced byte-identically across **three** independent runs to three separate evidence paths — two during materialization and one during independent review, none overwriting another.

## What this means

Every input the merged Phase 2B-2G gate requires has been materialized and mechanically verified against the frozen records:

- the historical source closure at commit `4c43fe96…`, **30/30** files, **28 LF + 2 CRLF**, clean;
- the original historical M52 handoff, `590672f0…ac8f9` / 34,124 bytes, with its `EXACT_BYTE_REPRODUCTION_FAIL` and `RETROSPECTIVE_SEMANTIC_PAYLOAD_MATCH` classifications intact;
- the 49-record parameter inventory, 5,018 bytes, `7c2b2da4…77e4a0ad`;
- three cache trees, 6,214 files, 202,942,816 bytes, all frozen aggregate digests reproduced, originals byte-identical before and after copying;
- both guard files from immutable LF Git-object bytes, with the trusted external pre-launch comparison performed and the shadow audit clean;
- Python and Git pinned by resolved file-byte content;
- the frozen output criterion declared and unmodified;
- separate, empty, non-overlapping output and evidence directories, with no pre-existing result or evidence file.

## What this does not mean

**This is readiness, not authorization.** The distinction is mechanical, not rhetorical: every record the gate can emit carries `execution_authorized=false` and `m53_executed=false`, and no option, argument, manifest field, environment variable, or profile can change either.

This decision specifically does **not** establish that:

- M53 has been reproduced — **no regenerated M53 artifact was created or observed**, and none was compared against the frozen criterion;
- anyone is authorized to run M53;
- the strategy is profitable, deployable, or production-ready;
- the guard is an operating-system sandbox — it is a **process-scoped Python guard**, and it was not even activated here;
- Git commits constitute formal preregistration;
- mechanical reproducibility proves scientific validity;
- the historical environment has been recreated. It has not, and cannot be: the OS image, Python build, dependency resolution, clock, locale, and filesystem behaviour of the original run are unrecorded. This phase records *its own* environment.

Cache **originality** also remains unestablished. No committed hash exists anywhere in the repository for any raw cache file, so the frozen aggregate digests fix the current local state and make mutation detectable — they are not evidence that these bytes are the bytes the original runs consumed, nor of vendor-side integrity. That Phase 1 limit is unchanged.

## Evidentiary limits of the preserved scratch evidence

The scratch root is preserved rather than deleted, which lets a reviewer with access to this machine re-verify every claim in this package against the actual materialized inputs. Independent review did exactly that.

**That is not the same as a durable, public, independently archived reproduction package, and must not be presented as one.** The materialized inputs live in one local directory on one operator machine. They are not published, not mirrored, not content-addressed in any external store, not covered by any retention guarantee, and not independently custodied — an accidental deletion, a disk failure, or a machine rebuild would end their availability, and nothing in this repository would detect it. The cache trees in particular cannot be published at all: they are Alpaca-derived and confirmed non-redistributable.

What *is* durable is the committed aggregate evidence — counts, byte totals, digests, and the gate's own record — which lets a reader detect that something no longer matches, but does not let them reconstruct the inputs. Anyone re-running this preflight elsewhere would have to re-materialize from their own licensed sources.

## Gates that remain before any M53 execution

1. **Independent review** of this materialization package and its evidence.
2. **Merge** of this pull request under explicit operator authorization.
3. **A separate, explicit, single-use operator authorization** naming the run — which this package does not request and cannot supply.
4. **A fresh gate pass immediately before invocation**, on the environment as it stands at that moment. This record is a snapshot; time-of-check to time-of-use is narrowed by re-running, not eliminated.
5. **Guard activation** with the full `ORB_M53_GUARD_*` configuration, the guard directory first on `PYTHONPATH`, no `-S`, and the trusted external pre-launch hash check repeated at launch time.
6. **Post-run `verify_guard_run()`**, requiring the affirmative `guard_ready` record, a terminal `normal_interpreter_exit`, an intact untruncated chain, and zero denied events.
7. **Pre- and post-command cache integrity checks** around the invocation, with any changed count, byte total, or digest a stop without retry.

Failing any one of these blocks the run. Passing all of them still would not make the reproduction scientifically valid.

## Defects and blockers found

**None.** No blocker was encountered, so nothing had to be preserved-and-stopped, worked around, or relaxed. The frozen manifest was not edited, no expected hash was normalized, and the merged gate was not modified in this workstream.

No defect in the merged gate was discovered. Had one been, it would have been documented and referred to a separate proposed correction scope rather than repaired silently inside a materialization workstream.

## Safety state

`strategy.enabled=false`. M55 remains `INITIALIZED_WAITING_FOR_FIRST_SIGNAL` — one `INITIALIZATION` checkpoint, zero transactions, empty positions, ledger blob `ce8f022d1fc9f734042de4c08feef35b9de00465`. **The M55 checkpoint was not run.** 2026-08-31 is only the first eligible signal date; the first possible simulated execution is 2026-09-01, and the first useful checkpoint remains the morning of 2026-09-02.

**M52 and M53 were not imported or executed.** No statistical analysis, P3C, M55, M56, or later workstream was started. This pull request must not be merged without separate explicit operator authorization.
