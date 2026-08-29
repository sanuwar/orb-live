# Publication P3B Phase 2B-2G — Integrated M53 Execution-Gate Assembly

Issue: [#384](https://github.com/sanuwar/orb-live/issues/384). Starting main: `0fb8aecd9876045c16d0839a40497b63a969f318`.

This package assembles the already-reviewed M53 reproduction controls from Phases 2A, 2B-1, 2B-1C, 2B-2A, and 2B-2N into **one deterministic, fail-closed preflight**. It answers exactly one question:

> Could a later, *separately authorized* M53 reproduction begin without changing any frozen source, parameter, historical input, cache, output criterion, or safety boundary?

**This work does not authorize or execute M53.** No M53 or M52 module was imported or executed. No simulation, regeneration, statistical analysis, or parameter experiment was run. No market, benchmark, factor, SEC, OpenFIGI, or broker data was fetched. No cache was copied, linked, normalized, deserialized, or mutated. The preserved Phase 2B-1 diagnostic scratch evidence was neither read nor modified.

## Current preflight decision

**`BLOCKED_SOURCE_OR_MATERIALIZATION`.**

Running the gate against the committed manifest with no materialized environment — the only configuration this PR permits — blocks in six categories at once, because nothing has been materialized and nothing may be:

| Category | Finding |
|---|---|
| `source_or_materialization` | no isolated historical checkout was supplied |
| `input_artifact` | no checkout was supplied to hold the M52 handoff |
| `cache_or_isolation` | no scratch cache root was supplied |
| `guard_or_executable` | no guard directory was supplied; no Python executable pinned; no Git executable pinned |
| `evidence_configuration` | no evidence directory was supplied |
| `parameters`, `output_criterion` | **no findings** — these reconcile exactly against the merged records |

The decision is `BLOCKED_SOURCE_OR_MATERIALIZATION` because source precedes everything downstream in the fixed category precedence. `execution_authorized=false` and `m53_executed=false` on this and every other record the gate can produce.

## Package map

- [`INTEGRATED_M53_EXECUTION_GATE_SPECIFICATION.md`](INTEGRATED_M53_EXECUTION_GATE_SPECIFICATION.md) — what the gate checks, in what order, and what each check actually proves.
- [`FROZEN_INPUT_AND_HASH_MANIFEST.md`](FROZEN_INPUT_AND_HASH_MANIFEST.md) — every frozen hash, size, count, and path, and where each was reconciled from.
- [`M53_EXECUTION_GATE_MANIFEST.json`](M53_EXECUTION_GATE_MANIFEST.json) — the machine-readable manifest the gate consumes.
- [`PREFLIGHT_DECISION_AND_STOP_CONDITIONS.md`](PREFLIGHT_DECISION_AND_STOP_CONDITIONS.md) — the eight decisions, category precedence, and pre-/post-run stop conditions.
- [`EXECUTION_AUTHORIZATION_BOUNDARY.md`](EXECUTION_AUTHORIZATION_BOUNDARY.md) — what this package is not, and what a later authorization must separately establish.
- [`POST_EXECUTION_EVIDENCE_AND_CLASSIFICATION_PLAN.md`](POST_EXECUTION_EVIDENCE_AND_CLASSIFICATION_PLAN.md) — the evidence a later authorized run must produce and how its outcome is classified.
- [`REMAINING_REPRODUCIBILITY_LIMITATIONS.md`](REMAINING_REPRODUCIBILITY_LIMITATIONS.md) — what this gate cannot establish, stated plainly.

## Implementation

`scripts/publication/m53_execution_gate.py` is verification-only. It contains **no code path that imports, launches, simulates, regenerates, or fetches anything**: it starts no subprocess, opens no socket, and imports no project module. `tests/test_publication_m53_execution_gate.py` proves this statically by parsing the gate's own AST, and proves that `execution_authorized` and `m53_executed` are assigned the literal `False` at their only two assignment sites.

`tests/test_publication_m53_execution_gate.py` uses only synthetic `tmp_path` fixtures and the committed manifest. It runs 118 focused tests (3 additional symlink tests skip on accounts that cannot create symlinks, with the ambiguity branch covered directly instead).

## Safety state at this branch

`strategy.enabled=false`. M55 remains `INITIALIZED_WAITING_FOR_FIRST_SIGNAL` with its checkpoint-ledger blob unchanged at `ce8f022d1fc9f734042de4c08feef35b9de00465`. No P3C, M56, or later workstream was started. This pull request must not be merged without separate explicit operator authorization.
