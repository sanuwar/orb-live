# Milestone 13: protection-state grounding

Protection-state grounding is the question "does broker-authoritative
evidence exist that a position is actually covered by a resting protective
order, collected across enough independent observations to be trusted."
Milestone 13 builds the pipeline that collects, records, classifies, and
explains that evidence. It never answers "is this trusted enough to change
what the system is allowed to do" -- that is a separate, later, human
decision, and no code path in this milestone can make it.

## Five things this milestone is careful never to confuse

1. **An observation exists.** One read-only checkpoint ran and recorded one
   canonical fact about broker state at one instant
   (`src/protection_observation_model.py`).
2. **Evidence qualifies.** That observation was accepted into the durable
   cross-run ledger, not rejected as stale, contaminated, or from an
   ineligible source (`src/protection_observation_ledger.py`).
3. **Evidence is eligible for review.** A batch of qualifying evidence
   satisfies the configured promotion rubric well enough that a human could
   reasonably review it next (`src/protection_promotion_policy.py`,
   `src/protection_grounding_assessment.py`).
4. **Human approval occurred.** A person reviewed the evidence package and
   recorded a decision. Nothing in Issues 1-8 produces this record -- it is
   milestone-handoff paperwork a human fills in outside any code path.
5. **Runtime authority changed.** `ADAPTER_PERMITTED_ACTIVE_PROTECTION_
   STATES`, `protection_grounded`, `activation_approved`, and the other
   authority fields moved. Nothing in this milestone writes to any of them.

Every module below sits at exactly one of the first three stages. None of
them, alone or composed, reaches the fourth or fifth.

## The implemented chain

```
Protection Observation (Issue 1)
    |  src/protection_observation_model.py
    v
Cross-Run Observation Ledger (Issue 2)
    |  src/protection_observation_ledger.py
    v
Versioned Promotion Policy (Issue 3)
    |  src/protection_promotion_policy.py
    v
Grounding Assessment (Issue 4)
    |  src/protection_grounding_assessment.py
    v
Checkpoint Integration (Issue 5)
    |  src/protection_checkpoint_integration.py
    |  read_only_observation_checkpoint_launcher.py
    v
Broker Call-Site Guard (Issue 6)
    |  tests/call_site_guard.py
    v
Operator Evidence Views / Exports (Issue 7)
    |  src/protection_assessment_store.py
    |  src/protection_operator_evidence.py
    |  src/operator_web_app.py
    v
Independent Verification (Issue 8)
       scripts/verify_m13_protection_grounding.py
```

A second chain exists conceptually but has no code in this milestone, by
design:

```
Future human review
    v
Future explicit promotion decision
    v
Future authority change
```

Nothing in the first chain feeds the second automatically. The boundary
between them is a person, not a function call.

## Product-owner decisions this milestone must not relitigate

### Q1 -- how may protective state legitimately exist for M13 to observe?

**Decided.** Milestone 13 remains authority level **A1**, read-only. It may
never create, submit, modify, cancel, replace, or flatten an order, and it
may never manufacture broker evidence. A protective order counts as
broker-authoritative evidence only when it already exists because of a
separate, explicitly authorized, operator-supervised procedure with a
genuine protection purpose -- never because M13 placed it.

If no legitimate protective order exists when a checkpoint runs, the
correct, complete, successful result is `NO_PROTECTIVE_ORDER_OBSERVED`.
Nothing is promoted, `protection_grounded` stays `false`, and the
checkpoint still finishes normally once every other validation passes. A
flat account is not a failed observation.

Simulated and test-fixture evidence
(`EvidenceSourceClass.SIMULATED_BROKER`, `EvidenceSourceClass.TEST_FIXTURE`)
validate that this software behaves correctly. They can never satisfy live
grounding: `BROKER_AUTHORITATIVE_SOURCE_CLASSES` in
`src/protection_observation_model.py` contains exactly one member,
`LIVE_READ_ONLY_BROKER_OBSERVATION`, and `src/protection_promotion_policy.py`
rejects every other source class outright
(`SOURCE_INELIGIBLE`/`require_broker_authoritative_source`) before evidence
is ever considered protective.

### Q2 -- how many observations justify promotion to `APPROVED_FOR_ACTIVE_PROTECTION`?

**Unresolved during M13, deliberately.** `ProtectionPromotionPolicy` carries
`minimum_distinct_eligible_live_runs` and `minimum_distinct_observation_
times` as *configuration*, not as an approved product answer. Configuring a
threshold is not the same as a product owner approving it, and no threshold
value -- current or future -- automatically grants authority on its own.
`promotion_enabled=False` and `independent_review_required=True` in
`DEFAULT_PROTECTION_PROMOTION_POLICY` mean the rubric can produce
`ELIGIBLE_FOR_REVIEW` at best; it can never itself become an approval.
`automatic_promotion_permitted` cannot be constructed as `True` at all --
`ProtectionPromotionPolicy.__post_init__` raises unconditionally if it is
attempted. Q2 must be answered by a human, separately, before any future
milestone makes a promotion decision that depends on an observation-count
requirement.

## Authority ladder

Using this repository's existing vocabulary (see
`SUPERVISED_READ_ONLY_DEPLOYMENT_CHECKLIST.md`,
`HOST_PROCESS_LIFECYCLE_RUNBOOK.md`, and the M9/M13 checkpoint code itself):

- **A0 (offline / fixture level):** simulation, test fixtures, imported
  historical records, no broker session. `EvidenceSourceClass.
  SIMULATED_BROKER`, `TEST_FIXTURE`, `IMPORTED_HISTORICAL_RECORD`,
  `OPERATOR_DECLARATION`, `OFFLINE_REPLAY`, and `UNKNOWN_SOURCE` all live
  here or below broker-authoritative eligibility.
- **A1 (attended, read-only):** an operator explicitly invokes the
  checkpoint, is present throughout, a bounded number of broker reads
  occur, and zero broker writes occur. This is the checkpoint M9 built and
  M13 layers evidence collection on top of.

**M13's maximum reachable authority is A1.** It does not authorize, and
contains no code path toward, automated broker access, live order
submission, supervised order submission, unattended execution, strategy
activation, live readiness, or unattended readiness. `strategy.enabled`,
`ACTIVATION_APPROVALS`, `activation_approved`, `permits_automated_action`,
`live_ready`, and `unattended_ready` are all independently confirmed false
or empty by `scripts/verify_m13_protection_grounding.py`'s
`check_authority_state` (Issue 8), which scans every M13 module by AST for
any assignment of `True` to any of them and finds none.

## Evidence source classes and their authority

`src/protection_observation_model.py::EvidenceSourceClass`:

| Class | Authority |
|---|---|
| `LIVE_READ_ONLY_BROKER_OBSERVATION` | The only class that may be broker-authoritative. |
| `OFFLINE_REPLAY` | Test/analysis evidence only. |
| `SIMULATED_BROKER` | Test evidence only -- can never satisfy live grounding. |
| `TEST_FIXTURE` | Test evidence only. |
| `OPERATOR_DECLARATION` | Contextual evidence only, never broker-authoritative. |
| `IMPORTED_HISTORICAL_RECORD` | Historical evidence only. |
| `UNKNOWN_SOURCE` | Ineligible; the ledger records it as `INELIGIBLE_SOURCE` rather than discarding it silently. |

Source authority, per-observation qualification, and batch-level
recommendation are three different questions, answered by three different
layers (the model, the ledger's append classification, and the promotion
policy, respectively) -- never conflated into one boolean.

## The observation model (Issue 1)

`src/protection_observation_model.py`. `ProtectionObservationRecord` is a
frozen dataclass; every field is either an enum, a bounded scalar, or a
SHA-256 pseudonymous reference -- never a raw account number, order ID,
position ID, or broker payload. Three structural guarantees are enforced in
`__post_init__`, not by convention:

- `write_count` must be exactly `0`. A nonzero value raises
  `ProtectionObservationError` unconditionally: this type cannot represent
  a broker write, by construction.
- `privacy_redaction_status` must already be `VERIFIED_REDACTED` at
  construction time. An unverified record cannot be built at all.
- `broker_adapter_identity` and `evidence_source_class` must agree about
  whether this is genuinely a live read-only observation; a mismatch is
  rejected unless the record is explicitly marked contaminated.

`evidence_digest` is computed once, in `__post_init__`, over a canonical,
hand-rolled encoding of every other field (`encode_canonical_value`/
`canonical_bytes`, domain-separated with `orb-live/protection-observation-
model/v1`) -- deterministic, and independently reproducible (Issue 8
recomputes it with a separate encoder and confirms an exact match).

## The cross-run ledger (Issue 2)

`src/protection_observation_ledger.py`. A SQLite-backed, append-only index
over observations, deliberately **not authoritative**: it is disposable and
rebuildable by replaying the same `ProtectionObservationRecord` stream
through `append()` again (`rebuild()`). If the database is lost, nothing
about the milestone's safety guarantees is lost with it.

- **Append-only.** No `UPDATE`/`DELETE`/`DROP` against the observations
  table anywhere in the module (confirmed by Issue 8's source scan).
- **Cross-run.** A run (`run_id`) opens once, accepts appends, and closes;
  `all_observations()` returns every accepted record across every run in
  deterministic order.
- **Duplicate-equivalent.** Re-delivering an observation with the same
  `observation_id` and the same `evidence_digest` is idempotent --
  recorded once, reported as `DUPLICATE_EQUIVALENT` on repeat.
- **Duplicate-conflict.** Re-delivering the same `observation_id` with a
  *different* `evidence_digest` is refused: the conflict is recorded in a
  separate `protection_observation_conflicts` table for later audit, and
  the original row is never overwritten.
- **Stale / ineligible-source / contaminated evidence is still recorded**,
  never silently dropped -- the promotion rubric needs honest evidence of
  a problem in order to correctly exclude it later. Only `DUPLICATE_
  CONFLICT` and structurally invalid records are refused a row.
- Release identity, implementation digest, account fingerprint reference,
  source classification, privacy state, and write count are all retained
  unchanged in the persisted row -- verified directly against the raw
  SQLite table by Issue 8, bypassing the ledger's own query methods.

The ledger records evidence. It grants no authority: no method on this
class can set `protection_grounded`, touch `ADAPTER_PERMITTED_ACTIVE_
PROTECTION_STATES`, or otherwise change what any runtime component may do.

## The promotion policy (Issue 3)

`src/protection_promotion_policy.py`. `ProtectionPromotionPolicy` is a
versioned, explicit configuration (`policy_version`, currently
`m13-promotion-policy-v1-pending-independent-review`) evaluated by
`evaluate_promotion_eligibility()` against a batch of ledger evidence, in a
fixed, documented priority order (conflict -> no evidence -> nonzero write
-> privacy -> contamination -> account mismatch -> release mismatch ->
implementation mismatch -> source ineligible -> stale -> contradictory ->
no active protection -> incomplete coverage -> internally inconsistent ->
insufficient runs/times/attestation -> `PROMOTION_NOT_PERMITTED` /
`REVIEW_REQUIRED` / `ELIGIBLE_FOR_REVIEW`). Every branch is batch-wide: one
bad record blocks the whole batch rather than being silently filtered out.

`DEFAULT_PROTECTION_PROMOTION_POLICY` ships with `promotion_enabled=False`
and `independent_review_required=True`. The best outcome the rubric can
ever produce today is `REVIEW_REQUIRED`, and `REVIEW_REQUIRED` is not
`APPROVED_FOR_ACTIVE_PROTECTION` and never sets `protection_grounded=True`
-- that field does not exist on `PromotionAssessment` at all.
`automatic_promotion_permitted` is validated to always be `False`; there is
no configuration that changes this.

## The grounding assessment (Issue 4)

`src/protection_grounding_assessment.py`. `evaluate_protection_grounding()`
calls the promotion policy exactly once per assessment and adds the
missing per-observation explanation layer: for every candidate observation,
whether it was `INCLUDED`, `EXCLUDED_BLOCKING`, or `EXCLUDED_INFORMATIONAL`,
and the precise `ObservationDispositionReason` why. It reports twelve named
dimension analyses (source authority, freshness, account/release/
implementation consistency, privacy, zero-write, quantity coverage, active
state, conflicting evidence, contamination, independence) plus a
`recommendation` (a `PromotionOutcome`) and reason codes.

`assessment_digest` is a SHA-256 over a canonical, domain-separated payload
(`orb-live/protection-grounding-assessment/v1`) of the assessment's
identity-relevant fields -- deterministic given the same evidence and the
same caller-injected `evaluated_at_monotonic`, and independently
reproducible (Issue 8 recomputes it with a separate encoder).

`promotion_permitted` and `protection_grounded` are hard-coded `False` on
every `ProtectionGroundingAssessment` instance; `__post_init__` raises if
either is somehow `True`. `OperatorReviewContext`, the type that carries
metadata about a pending or completed human review, has no `approved`
field and never will -- approval is a document a human fills in outside
any code path in this milestone.

## Checkpoint integration (Issue 5)

`src/protection_checkpoint_integration.py` and
`read_only_observation_checkpoint_launcher.py`. `integrate_checkpoint_
result()` wraps an already-completed, already-evidence-written
`CheckpointResult` from the unmodified M9 runtime
(`src/read_only_observation_runtime.py`) with the Issues 1-4 pipeline. It
performs no broker call, no session, no read, and no write of any kind --
it never touches the runtime that actually talks to the broker. A failure
inside this layer can only report its own `IntegrationStatus.FAILED_
CLOSED`; it never rewrites the underlying `CheckpointResult` or the
evidence file the checkpoint already finalized.

The launcher wires this in through two optional flags, `--protection-
ledger-root` and `--protection-assessment-store`. Omitting either preserves
exact pre-M13 checkpoint behavior with no M13 involvement at all.

## Broker call-site guard (Issue 6)

`tests/call_site_guard.py`. A bounded, conservative static analysis over
`src/` and `scripts/` proving every broker dispatch site (`session.call_
tool(...)`, `gateway.read(...)`) is either a fixed `LITERAL` tool name (in
the read-only allowlist), a reviewed `SAFE_WRAPPER`, or -- if the tool name
cannot be proven statically -- `DYNAMIC`/`UNRESOLVED`, which always fails
closed. Coverage includes direct calls, `getattr`-mediated dispatch, bound-
method aliases, wrapper functions, `functools.partial`, lambdas, and
dict/registry dispatch with literal keys.

At the Issue 8 review state, the guard's own re-derivation from repository
truth reports **21 total call sites: 17 literal, 4 safe_wrapper, 0
unresolved**. This number is derived, not pinned -- it will change
honestly if the reviewed inventory changes, and no test in this repository
should assert it as an unchanging architectural constant. The single
reviewed write-capable path,
`src/robinhood_commissioning_adapter.py:203`, remains visible in the
inventory as `safe_wrapper`; the M13 checkpoint chain contributes zero
call sites of any kind. The guard is a conservative static proof over the
shapes it recognizes, not a proof about arbitrary Python behavior --
unresolved dispatch fails closed precisely because the guard does not
attempt to be complete.

## Operator evidence views and exports (Issue 7)

`src/protection_assessment_store.py`, `src/protection_operator_evidence.py`,
`src/operator_web_app.py`. Read-only GET routes let an operator inspect:
protection observation list and detail, cross-run evidence (via the
observation list plus filters), grounding assessment list and detail, and
JSON/HTML exports of both, all served from the existing read-only operator
application (documented separately in `LOCAL_OPERATOR_WEB_APP.md` and
`OPERATOR_ROUTE_CONTRACTS.md`). Every route declares `safety_authority=
NONE`, `permits_database_write=False`, `permits_runtime_write=False`,
`permits_outbound_network=False`, `exposes_private_data=False`, GET-only --
enforced structurally by `ApplicationRouteTable.validate()`, not by
convention. An assessment detail page always states: **"Recommendation is
evidence for review. It is not approval and does not change runtime
authority."**

Viewing evidence through this application cannot execute a checkpoint (no
route imports `protection_checkpoint_integration.py` or `read_only_
observation_runtime.py`), cannot connect to a broker (no route imports any
broker transport module), and cannot approve or promote grounding (no
mutating route exists at all -- POST/PUT/PATCH/DELETE are rejected by every
registered route).

## Independent verification (Issue 8)

`scripts/verify_m13_protection_grounding.py`. A second opinion on Issues
1-7, not a re-run of their own tests: for every capability it checks, it
independently re-derives expected truth (a separate digest-hashing
routine, a separate AST-based execution-closure walk, a separate canonical
encoder for digest spot-checks, a from-scratch reimplementation of the
promotion policy's documented priority order, a separate bounded call-site
scanner, freshly authored route/export probes) and compares that against
exactly one call to the real implementation -- never by calling the same
implementation function to produce both sides of a comparison. This is
described accurately as an *independent reimplementation of critical
claims*, not as a mathematical proof of correctness: it is bounded by what
its own separately-authored logic can check, exactly like the call-site
guard it cross-checks.

Its 53 checks and 54 tamper/independence tests are documented in the
Issue 8 pull request and in the module's own docstrings. Output is
strictly `PASS` or `FAIL` -- there is no "mostly passed" state -- in both
a human-readable report and a `--json` machine-readable report that always
agree.

## Current M13 status

Issues 1-8 are complete and merged. This is documentation infrastructure,
not new evidence: no eligible live protection has been observed under this
milestone, no promotion is recommended, Q2 remains unresolved, and
`protection_grounded` remains `false`. Final milestone acceptance is
Issue 10's responsibility, not this document's -- this document does not
declare M13 accepted.

## After M13 acceptance

Legitimate live read-only evidence may be collected under explicit,
per-run operator authorization, following the runbook in
`M13_LIVE_READ_ONLY_PROTECTION_OBSERVATION_RUNBOOK.md`. Promotion remains a
separate decision, gated on Q2 being answered by a human. Runtime
integration, market data, execution, supervised live trading, and eventual
unattended readiness belong to later milestones and are out of scope here.
