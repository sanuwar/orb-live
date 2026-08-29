# Execution Authorization Boundary

## This package authorizes nothing

Phase 2B-2G assembles controls and reports a decision. It is not an authorization, does not become one when the decision is `READY_FOR_SEPARATELY_AUTHORIZED_M53_EXECUTION`, and cannot be converted into one by any option, flag, manifest field, or environment variable.

The distinction is deliberate and mechanical:

- **Readiness** is a statement about verified inputs: the frozen source, parameters, historical input, cache evidence, isolation structure, guard configuration, output criterion, and evidence paths all reconcile.
- **Authorization** is a decision by the human operator about whether a run may happen at all. Nothing in this repository can produce it.

Every evidence record the gate can emit carries `execution_authorized=false` and `m53_executed=false`. Those two names are assigned the literal `False` at their only two assignment sites in `scripts/publication/m53_execution_gate.py`, and a test enforces that by walking the module's AST.

## What did not happen in this workstream

- M53 was **not** imported or executed.
- M52 was **not** imported or executed.
- No historical simulation, regeneration, statistical analysis, or parameter experiment ran.
- No market, benchmark, factor, SEC, OpenFIGI, or broker data was fetched.
- No broker or account access occurred; no Robinhood or MCP tool was called.
- No cache was copied, linked, normalized, deserialized, or mutated.
- The preserved Phase 2B-1 diagnostic scratch evidence was neither read nor modified.
- No historical checkout was materialized; no guard directory was exposed; no scratch cache root was created.
- `strategy.enabled` remains `false`; M55 remains `INITIALIZED_WAITING_FOR_FIRST_SIGNAL` with its ledger blob unchanged.
- No P3C, M56, or later workstream was started.

## What a later authorization must separately establish

A future M53 reproduction requires **all** of the following, none of which this package supplies:

1. **Independent review** of this gate, its manifest, and its tests.
2. **Merge** of this pull request into `main` under explicit operator authorization.
3. **A separate, explicit, single-use operator authorization** naming the run.
4. **Materialization authorization** for the isolated historical checkout, the exposed guard directory, and the scratch cache copies — each of which touches real caches and is outside this workstream's boundary.
5. **A fresh gate pass** immediately before invocation, on the actual materialized environment, yielding `READY_FOR_SEPARATELY_AUTHORIZED_M53_EXECUTION`.
6. **The trusted external pre-launch guard hash check**, performed outside the guarded interpreter, against the pinned Git-object digests recorded in `FROZEN_INPUT_AND_HASH_MANIFEST.md`.
7. **Post-run `verify_guard_run()`**, passing.

Failing any one of these blocks the run. Passing all of them still does not make the reproduction scientifically valid — see `REMAINING_REPRODUCIBILITY_LIMITATIONS.md`.

## Claims this package does not make

It does not claim that M53 has been reproduced; that the strategy is profitable, deployable, or production-ready; that the guard is an operating-system sandbox (it is a process-scoped Python guard); that Git commits constitute formal preregistration; that this gate proves scientific validity; or that the frozen parameters are verified beyond the mechanical reconciliation actually succeeding.

## Permanent safety boundaries this package does not touch

Repository-data guards, durable halts, reconciliation rules, idempotency protections, privacy controls, and fail-closed behaviour are unchanged. No production SQLite file, token file, or private configuration was read. `config/strategy_v0.yaml` is not an M53 input and its `enabled: false` is unchanged.
