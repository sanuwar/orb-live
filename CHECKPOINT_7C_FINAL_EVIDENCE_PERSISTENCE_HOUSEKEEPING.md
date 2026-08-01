# Checkpoint 7C — Final Evidence-Persistence Housekeeping

Offline refactor plus one artifact disposition. No broker, MCP, network,
launcher, or wrapper activity occurred, and no trading behaviour changed.

## Placement-diagnostic persistence

### Before

`_record_place_shape` looped over the collected facts and called
`record_place_diagnostic` once per fact, and each of those performed a full
atomic evidence write. Measured on the real path:

| failed placement | persistence calls while recording |
| --- | --- |
| entry, shape facts only | **8** |
| entry, shape + contract violation | **11** |
| stop, shape facts only | **8** |
| stop, shape + contract violation | **11** |
| exit, shape facts only | **8** |
| exit, shape + contract violation | **11** |

Each call also mutated the live evidence object *before* persisting, so a
failure partway through left a partially populated `place_diagnostics` in
memory.

### After

| failed placement | persistence calls while recording |
| --- | --- |
| any role, any classification | **1** |
| any role, nothing observed (e.g. transport failure) | **0** |

`CommissioningMachine.record_place_diagnostics` accepts the whole update.
`record_place_diagnostic` remains as a single-fact wrapper over it.

### Why this narrows the fail-closed exposure window

Diagnostics are recorded *after* the broker write is already spent, inside the
ambiguous-placement handler. Every persistence call in that window is an
opportunity for an evidence failure to pre-empt reconciliation. Reducing 8–11
calls to 1 reduces that window proportionally. It does not eliminate it, and it
is not meant to: the failure policy is unchanged.

### No partial state

The batch is transaction-like:

1. validate every key and value against the fixed allowlists — an invalid batch
   raises before anything is applied, so a rejected batch never reaches the
   evidence object;
2. build a staged copy of the current diagnostics and apply the update to it;
3. swap the staged copy in and persist exactly **once**;
4. on failure, restore the previous map and re-raise.

A failed batch therefore leaves `place_diagnostics` untouched in memory and on
disk. The failure is never swallowed.

### Failure policy unchanged

A genuinely broken evidence store still produces exactly what it did before:
one broker write, zero retries, zero reconciliation reads, no later stop,
cancel, or exit write, `CommissioningDiagnosticError` with
`INTERNAL_INVARIANT_FAILURE` at stage `INTERNAL_INVARIANT`, and the session
closed exactly once.

One nuance worth recording. The pre-existing regression fake failed persistence
*whenever `place_diagnostics` was non-empty*. Under the new rollback that
condition un-breaks itself, because the map reverts to empty — so the fake
stopped failing and the run ended `OPERATOR_REQUIRED` instead. That was an
artefact of the fake's arming condition, not a policy change: a real broken
store (full disk, lost permission) fails regardless of content. The fake is now
sticky once triggered, which models a real failure, and the documented
`INTERNAL_INVARIANT_FAILURE` outcome is preserved and asserted.

### Privacy and validation

Unchanged. Keys and values must both appear in the fixed allowlists, so no raw
response, response key, response value, exception message, symbol, order
identifier, account information, price, response quantity, request argument,
authorization input, token, or header can pass. Unexpected response keys are
still reported only as `unexpected_field`, never by name. Unknown facts are
dropped rather than invented. The batch API takes a mapping that is validated
in full; there is no path accepting an arbitrary evidence field or an
unvalidated dictionary.

## The untracked 25-test module

`test_checkpoint_7c_adapter_initialization_launcher.py` is classified as an
**obsolete local artifact** and preserved unchanged. This is disposition **B**,
and it was not chosen for convenience — option A is structurally unavailable:

- the module it tests, `checkpoint_7c_adapter_initialization_launcher.py`, is
  **itself untracked**. Moving the tests into `tests/` would make CI import and
  hash a file that does not exist in the tracked tree;
- it pins `PINNED_IMPLEMENTATION_COMMIT = 1b378605`, the PR #18 merge —
  **56 commits behind** current main — plus hashes of source files that have
  legitimately changed since. Re-pinning it would destroy the exact property it
  asserts, namely that the reviewed artifact still matches its reviewed state;
- nothing tracked references it.

It is an archived acceptance record for the superseded initialization-only
launcher. Its behaviours are covered against the **successor** launcher, which
is tracked:

| archived test | tracked coverage |
| --- | --- |
| launcher hash/import offline | `test_direct_launcher_is_inert_and_fail_closed`, `test_normal_strategy_does_not_import_commissioning_launcher` |
| CLI argument strictness, defaults refuse | `test_parser_requires_explicit_real_and_live_flags`, `test_injected_entry_point_rejects_non_fake_modes` |
| narrow New-York-only window | `test_live_request_rejects_offset_bearing_runtime_times`, `test_runtime_time_parser_fails_closed`, `tests/test_authorization_input_provider.py` |
| pinned hashes exist and match | `test_reviewed_hash_manifest_required`, `test_reviewed_hash_verification_and_source_drift`, `test_pinned_commissioning_source_hashes_match` |
| import closure pinned / self-hash | same three, plus `tests/test_runtime_call_site_guard.py` |
| static preflight call order | `tests/test_runtime_call_site_guard.py`, `test_real_session_execution_constructs_exactly_one_binding` |
| failure before baseline initializes nothing | `tests/test_checkpoint_7c_incident_regression.py`, `test_fake_session_mode_never_creates_real_client` |
| output path / partial fail-closed | `test_used_run_id_fails_before_session_creation`, evidence-store tests in `tests/test_one_share_commissioning.py` |
| browser auth blocked | `tests/test_checkpoint_7c_incident_regression.py` silent-auth paths |
| token metadata never read | `tests/test_runtime_call_site_guard.py`, `tests/test_account_state_gateway.py` |
| atomic pair / no partial leftover | evidence-store atomicity tests in `tests/test_one_share_commissioning.py` |
| one adapter, one session, call budget | `test_launcher_fake_session_mode_creates_one_adapter_and_orchestrator`, `test_real_session_execution_constructs_exactly_one_binding` |
| no retry / pagination / raw evidence path | `tests/test_runtime_call_site_guard.py`, `test_direct_launcher_is_inert_and_fail_closed` |

Two of its assertions — direct file-descriptor capture and that launcher's own
fixed report field order — have no tracked equivalent, because they describe the
output format of the obsolete artifact rather than any behaviour still in use.

A tracked `pytest.ini` now ignores that one path, so a local run collects
exactly what CI collects and a stale pin can no longer distort the authoritative
count. `--ignore` of an absent path is a no-op, so CI is unaffected. The file is
neither deleted nor re-pinned.

## Authoritative tracked-suite composition

One tracked root-level module (`test_checkpoint_7c_one_share_commissioning_launcher.py`)
plus `tests/`. Everything else at the repository root is untracked operator
material — wrappers, handoffs, proposals, and the archived acceptance module —
and is deliberately outside the suite.

## Safety

No parser acceptance, symbol-pending or symbol-mismatch routing, reconciliation
decision, cancellation behaviour, or write limit changed. Entry 1, stop 1,
cancel 1, cancel retries 0, exit 1, exit retries 0. `strategy.enabled=false`.

## No live validation required

This is an offline refactor of evidence bookkeeping plus a test-collection
setting. It cannot place, cancel, or exit an order, and it changes no decision
the runtime makes.
