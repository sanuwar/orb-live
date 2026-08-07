# M13 live read-only protection-observation runbook

This document describes how a future, explicitly authorized, attended,
read-only broker observation would be executed and folded into the M13
protection-grounding pipeline. **Writing this runbook does not authorize
running it.** No command in this document may be executed as part of
Issue 9, or as part of documenting, reviewing, or merging this file.

This procedure produces at most one observation. It never places,
cancels, replaces, or modifies an order, and it never changes runtime
authority. A completed observation is evidence for future human review --
see `MILESTONE_13_PROTECTION_GROUNDING.md` for why observation, grounding,
approval, and activation are four different things, none of which this
procedure or its output can conflate.

## 0. Scope reminder

- Maximum authority reachable: **A1** (attended, read-only, zero writes).
- This procedure does **not** authorize automated broker access, live
  order submission, supervised order submission, unattended execution,
  strategy activation, live readiness, or unattended readiness.
- `protection_grounded`, `activation_approved`, `permits_automated_action`,
  `live_ready`, and `unattended_ready` all remain `false` before, during,
  and after this procedure, unconditionally.

## 1. Preconditions

All of the following must be true before this procedure is executed for a
specific run:

- Explicit operator authorization exists for this exact run (not a
  standing authorization; a new Run ID requires a new authorization).
- The operator will be physically or logically present for the entire
  run.
- The accepted `main` SHA is recorded and matches what CI last validated.
- The implementation digest is recorded (see step 3) and matches the
  reviewed value.
- `PINNED_IMPLEMENTATION_CLOSURE` is independently verified, not merely
  read.
- `python scripts/verify_m13_protection_grounding.py` reports `FINAL
  RESULT: PASS`.
- `python scripts/architecture_audit.py --format markdown` reproduces the
  checked-in `LEAN_ARCHITECTURE_AUDIT.md` exactly.
- `python scripts/deployment_readiness_audit.py` runs clean.
- `python scripts/verify_operator_milestone.py` reports `"status":"PASS"`.
- The hardened broker call-site guard
  (`tests/call_site_guard.py::analyze_runtime_tree()`) reports zero
  `DYNAMIC`/`UNRESOLVED` sites.
- An account fingerprint reference is available through the repository's
  existing approved secure process -- never typed into a tracked file.
- The adapter/source hashes for the session transport are the reviewed,
  approved ones.
- The expected broker read budget for this run is known in advance.
- The expected broker write budget for this run is **zero**, and is
  understood to be structurally enforced, not merely intended.
- `strategy.enabled` is `false` in the effective configuration.
- No automated scheduler or unattended execution path is active.
- No operator-dashboard route will be used to trigger this checkpoint --
  the read-only operator application has no route that can (see
  `MILESTONE_13_PROTECTION_GROUNDING.md`, "Operator evidence views and
  exports").
- Privacy/redaction configuration is verified (this launcher constructs
  every observation with `privacy_redaction_status=VERIFIED_REDACTED`
  structurally; there is no configuration flag that weakens this).
- The external evidence destination (outside the repository) is prepared
  and reachable.

No precondition may be satisfied by storing a raw credential, token, or
account number in this repository.

## 2. Legitimate protective state

Before execution, the operator must classify whether a legitimate
protective order already exists on the account, independently of M13:

- **Allowed situation:** a protective order already exists because of a
  separate, explicitly authorized, supervised procedure with a genuine
  protection purpose. M13 did not create it and will not create it.
- **Also allowed:** no protective order exists. Proceeding with a
  read-only observation is still correct; the expected result is
  `NO_PROTECTIVE_ORDER_OBSERVED`, which is a complete, successful
  observation, not a failure.

**Do not create an order merely to make the checkpoint produce a
different result.** That would violate the Q1 decision documented in
`MILESTONE_13_PROTECTION_GROUNDING.md` and would itself be the kind of
evidence manufacturing this milestone exists to prevent.

## 3. Operator authorization record (external, not tracked)

Record the following externally, outside this repository, before the run:

| Field | Notes |
|---|---|
| Run purpose | Free text. |
| Operator | Who is present for this run. |
| Date/time | When the run is planned. |
| Reviewed main SHA | The exact commit this procedure was validated against. |
| Implementation digest | From `python -c "from pathlib import Path; from src.read_only_authorization_manifest import checkpoint_implementation_digest; print(checkpoint_implementation_digest(Path.cwd()))"`. |
| Verifier result | Full output of `scripts/verify_m13_protection_grounding.py --json`. |
| Account fingerprint reference | The pseudonymous SHA-256 reference, never the raw account number. |
| Expected read count/budget | A specific number, from the reviewed plan's `call_ceilings`. |
| Expected write count | Always `0`. |
| Adapter identity/hash | Which reviewed adapter build is in use. |
| Checkpoint version | The `checkpoint_version` string this run will record. |
| Approval reference for read-only observation | The specific authorization instrument used (manifest or interactive). |
| Legitimate protective-state source, if any | What separately-authorized procedure created it, if a protective order already exists. |
| Expected evidence storage location | The external, non-repository location evidence will be preserved to. |

Do not record a raw account number, order ID, or position ID in this
record.

## 4. Command preparation

The canonical command comes from the tracked launcher's own `argparse`
contract -- `read_only_observation_checkpoint_launcher.py::build_parser()`
-- never from memory or invention. At the reviewed SHA, the launcher
accepts:

- `--run-id` (required) -- a new, unused Run ID.
- `--account-number` (required) -- supplied at invocation time, never
  written to a tracked file.
- `--symbol` (required)
- `--expected-quantity` (required)
- `--evidence-root` (required) -- outside the repository.
- `--authorization` (required) -- must equal the fixed flag value
  `AUTHORIZE_MILESTONE_9_READ_ONLY_OBSERVATION`
  (`src/read_only_observation_runtime.py::READ_ONLY_AUTHORIZATION_FLAG`).
- `--operator-present` (flag) -- required for interactive mode.
- `--i-understand-this-performs-real-broker-reads` (flag) -- one of
  several independent, necessary-but-not-sufficient gates.
- **Exactly one** of `--authorization-manifest PATH` (with
  `--authorization-ledger PATH`) or `--interactive` -- supplying both, or
  neither, is refused.
- `--protection-ledger-root PATH` (optional) -- omit to run with no M13
  involvement at all; supply to have this run's observation folded into
  the M13 pipeline.
- `--protection-assessment-store PATH` (optional, only meaningful
  alongside `--protection-ledger-root`) -- persists the resulting
  grounding assessment for the operator application to display.

The launcher also requires the environment variable
`ENABLE_MILESTONE_9_READ_ONLY_OBSERVATION=1`. This variable is necessary
and **not sufficient** on its own -- it never substitutes for the
manifest or interactive authorization.

A command template, with every private value as a placeholder:

```text
ENABLE_MILESTONE_9_READ_ONLY_OBSERVATION=1 \
python read_only_observation_checkpoint_launcher.py \
  --run-id <RUN_ID> \
  --account-number <ACCOUNT_NUMBER> \
  --symbol <SYMBOL> \
  --expected-quantity <EXPECTED_QUANTITY> \
  --evidence-root <EXTERNAL_EVIDENCE_ROOT> \
  --authorization AUTHORIZE_MILESTONE_9_READ_ONLY_OBSERVATION \
  --operator-present \
  --i-understand-this-performs-real-broker-reads \
  --interactive \
  --protection-ledger-root <EXTERNAL_PROTECTION_LEDGER_PATH> \
  --protection-assessment-store <EXTERNAL_PROTECTION_ASSESSMENT_STORE_PATH>
```

Substitute `--authorization-manifest <PATH> --authorization-ledger <PATH>`
for `--interactive` if using the scheduled/manifest authorization mode
instead. Never both. `<ACCOUNT_NUMBER>` is supplied at invocation time
only, through the operator's approved secure process -- never placed in
a file, a shell history that is retained, or this document.

## 5. Double-check immediately before pressing enter

Verify all of the following, immediately before execution, not earlier:

- `git rev-parse HEAD` matches the reviewed main SHA recorded in step 3.
- `git status --porcelain` shows a clean tracked tree.
- The implementation digest recomputed right now matches step 3's record.
- The account fingerprint reference matches step 3's record.
- The adapter/source hash matches step 3's record.
- The exact command matches step 4's template with every placeholder
  filled from step 3's record, nothing else.
- The authorization mode is exactly one of manifest or interactive, per
  step 4.
- The expected read budget and the zero-write allowance are understood.
- The hardened call-site guard passed (step 1) at this exact SHA.
- No unexpected process is already running against this evidence root or
  Run ID.
- No automation scheduler is active that could interfere or double-fire.

**If any of these does not match: ABORT.** Do not proceed on a
best-effort basis.

## 6. During execution

The operator remains present for the entire run. Stop immediately, do not
"click through," on any of:

- Unexpected authentication or challenge behavior.
- An account fingerprint that does not match the one recorded in step 3.
- Any broker call outside the reviewed, bounded read sequence.
- Any write attempt, of any kind.
- The read budget being exceeded.
- An unknown or unmapped broker state.
- A privacy/redaction failure.
- A digest or source/hash mismatch discovered mid-run.
- Any network behavior outside the approved broker reads.
- The checkpoint process deviating from the reviewed flow in any way.
- An unresolved call-site-guard-shaped finding surfacing at runtime.
- Any unexpected change to a runtime authority field.

## 7. Expected valid outcomes

At minimum, two outcomes are expected and both are complete, successful
observations:

- **`NO_PROTECTIVE_ORDER_OBSERVED`** -- no protective order was found;
  `normalized_protection_state=NOT_PROTECTED`; nothing is promoted.
- **An observation of active protective state**, if a legitimate
  protective order already exists (per step 2) -- recorded with
  `normalized_protection_state` reflecting what was actually observed,
  `evidence_source_class=LIVE_READ_ONLY_BROKER_OBSERVATION`, and
  `write_count=0`.

Neither outcome automatically changes any authority field. Both are
folded into the ledger and, if `--protection-assessment-store` was
supplied, into a freshly computed grounding assessment -- which is
evidence for review, not an approval, per
`MILESTONE_13_PROTECTION_GROUNDING.md`.

## 8. Post-run checklist

1. Record the exact exit code and outcome.
2. Confirm the read count matches the expected budget from step 3.
3. Confirm the write count is exactly `0`.
4. Confirm privacy/redaction status is `VERIFIED_REDACTED` on the
   resulting observation.
5. Save the permitted evidence artifact to the external destination
   recorded in step 3 -- never to this repository.
6. Inspect the observation record (via the operator evidence views, or
   directly).
7. Inspect the ledger append result (`ACCEPTED_OBSERVATION`, `STALE_
   OBSERVATION`, `INELIGIBLE_SOURCE`, `CONTAMINATED_EVIDENCE`, or a
   duplicate state -- and if a duplicate state, understand why before
   proceeding further).
8. Inspect the resulting grounding assessment, if one was produced.
9. Run `scripts/verify_m13_protection_grounding.py` again against the
   resulting, permitted artifacts, if the verifier's scope supports doing
   so for this evidence.
10. Compare pre/post broker state where meaningful and safely observable
    through the same read-only means.
11. Verify no authority field changed: `strategy.enabled`,
    `ACTIVATION_APPROVALS`, `protection_grounded`, `activation_approved`,
    `permits_automated_action`, `live_ready`, `unattended_ready`.
12. If anything unexpected occurred at any point, file an incident before
    doing anything else.
13. If the resulting evidence is contradictory or contaminated, stop
    further live runs until it is understood -- do not simply retry.

## 9. Post-run authority statement

**A successful observation does not authorize another authority level.**
After this procedure, unconditionally:

- `protection_grounded` remains `false` unless a later, separately
  approved promotion process explicitly changes it -- which requires Q2
  to have been answered by a human first.
- `activation_approved` remains `false`.
- `permits_automated_action` remains `false`.
- `live_ready` remains `false`.
- `unattended_ready` remains `false`.

## 10. Rollback / abort

M13 is read-only, so "rollback" here means containment, never correction
through a broker write:

- Terminate the checkpoint process.
- Preserve all logs and evidence exactly as produced.
- **Do not** attempt a corrective broker write of any kind.
- **Do not** cancel or replace any order through this procedure or any
  M13 code path -- M13 has no cancel/replace capability at all.
- Mark the resulting evidence contaminated or invalid in the ledger's own
  vocabulary if warranted, rather than deleting it.
- File an incident.
- Return to offline investigation before considering another live run.

If a pre-existing real protective order requires operational management
(adjustment, cancellation, replacement), that belongs entirely to the
separate, already-authorized procedure that created or manages it. This
procedure has no authority over that order and must not be used to try to
manage it.

## 11. Evidence preservation

This repository must never receive: credentials, tokens, raw private
broker payloads, raw account identifiers, raw order identifiers, or raw
position identifiers. Everything this procedure produces that is safe to
retain (pseudonymous references, enum states, digests) may be preserved
through the repository's existing approved external evidence practice.
This runbook does not invent a new private-data storage location -- if
one is not already established, resolve that before running this
procedure, not during it.
