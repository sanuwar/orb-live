# Milestone 13 acceptance package

Milestone 13, "Protection-State Grounding Completion," closed out across
Issues 1-10. This document records the infrastructure-acceptance evidence
gathered by independently re-verifying the integrated final tree from
scratch at Issue 10 -- not by trusting Issues 1-9's own reports.

## 1. Milestone objective

Build the pipeline that collects, records, classifies, and explains
protection-state evidence from an attended, read-only broker checkpoint,
without ever creating protective state, without ever writing to the
broker, and without ever changing runtime authority on its own. See
`MILESTONE_13_PROTECTION_GROUNDING.md` for the full architecture.

## 2. Accepted starting lineage

Milestone 13 began from the milestone-9/10 read-only observation
checkpoint and durable safety-stack infrastructure already merged to
`main` before Issue 1.

## 3. Final reviewed SHA

`d5b0358535fa0d638bb1a70ead5c41fb57eb3957` (post-Issue-9 `main`, the
starting point for Issue 10).

## 4-5. Issues 1-9 and their pull requests

Reconstructed from GitHub issue/PR metadata and merge commits, not
guessed:

| Issue | GitHub issue | PR | Merge SHA |
|---|---|---|---|
| 1 -- Canonical protection-observation model | *(no dedicated GitHub issue found -- see note below)* | #190 | `e237fea168c635572ac915b134c67081bacd6f07` |
| 2 -- Cross-run observation ledger | #191 | #192 | `cac8b8b53d74b61f1811ba0785b3002ac1ae98f0` |
| 3 -- Versioned promotion rubric | #193 | #194 | `389062d3d8f332cc2fab801100e3de48b58fba8f` |
| 4 -- Grounding assessment engine | #195 | #196 | `2b493e9cdbdc2babee3c0fc4e69abedcb5499701` |
| 5 -- Read-only checkpoint integration | #197 | #198 | `ac4f3111448f396c40e858562d07306f6decab79` |
| 6 -- Broker call-site guard hardening | #199 | #200 | `5a13281564a8fff095607dfb97b18d19597e0f0d` |
| 7 -- Operator evidence views and exports | #201 | #202 | `911ea101ccf1ef89b89fbe980c824071b379c882` |
| 8 -- Independent M13 verifier | #203 | #204 | `345297e03db785045515f8e61a06f8b86f0c262e` |
| 9 -- Documentation and operator runbook | #205 | #206 | `d5b0358535fa0d638bb1a70ead5c41fb57eb3957` |
| 10 -- Integration verification and acceptance package | #207 | *(this PR)* | *(pending)* |

**Note on Issue 1:** no GitHub issue matching "Milestone 13 Issue 1" was
found in repository history. PR #190's body does not reference a
`Closes #` issue number. This is recorded honestly rather than guessed;
it does not affect the correctness of the merged code, which is
independently re-verified below regardless of its issue-tracking
provenance.

## 6. Final implementation digest

```
448f1e2739eb01d8606536723c7752c0e06a86e770c655489262d94553d9e114
```

Independently reconstructed (separate hashing routine, separate
AST-based execution-closure walk -- see `scripts/verify_m13_protection_
grounding.py::check_implementation_digest_and_closure`) and confirmed to
match the declared value exactly at the final reviewed SHA.

This value is kept live, not frozen at M13's own close: M14 Issue 1 added
`src/execution_safety_governance.py` to `PINNED_IMPLEMENTATION_CLOSURE`
(reached from `src/robinhood_commissioning_adapter.py` and
`src/one_share_commissioning_orchestrator.py`, both already pinned for
M13), which moved the digest and count below. The milestone's own
findings are otherwise unchanged. M14 Issue 6 (broker write-boundary
decision authenticity) subsequently changed the CONTENT of two already-
pinned files (`src/execution_safety_governance.py`,
`src/robinhood_commissioning_adapter.py`) without adding or removing any
pinned path, moving the digest again. M14 Issue 7 (commissioning
suspension gate) then added 8 newly-reachable files: `src/robinhood_
commissioning_session.py` (already pinned) now imports `src/commissioning_
suspension_identity.py`, whose `make_gate()` deferred-imports `src/
execution_safety_governance_deferred_gates.py`, whose three gate-factory
functions each deferred-import a different module (`durable_suspension_
controller.py` -> `attestation_authority.py`, `durable_scheduler.py`,
`execution_gates.py`; plus `operational_monitor.py`; plus `risk_limits.
py`). This is reachability, not activation: `DEFAULT_COMMISSIONING_GATES`
gained only the one suspension gate; the risk and operational-health
factories remain unwired. M14 Issue 2 (commissioning risk-state provider)
then added one more: `src/commissioning_risk_state.py`, reached because
`execution_safety_governance_deferred_gates.py` (already pinned via the
Issue 7 chain) deferred-imports it inside `make_commissioning_risk_state_
gate` -- again reachability, not activation, since the risk-state gate is
not in `DEFAULT_COMMISSIONING_GATES` (Issue 2's own conclusion: no honest
kill-switch/realized-daily-loss source exists yet). M20 Issue 1 later
changed the content of the already-pinned `src/protection_grounding_
assessment.py` to add broker-authoritative, reversible working-stop
grounding without adding a pinned path or granting execution authority.
Issue #305 (injectable calendar seam, an out-of-sample-research enabling
change) then changed the CONTENT of the already-pinned `src/market_data_
canonical.py`, adding an optional, keyword-only `session_bounds_provider`
parameter (default `regular_session_bounds`, i.e. current production
behavior) to `session_open_for` and `validate_market_bar`, without adding
or removing any pinned path or granting execution authority. Those
reviewed changes moved the live digest to the value above.

## 7. Pinned file count

**51**, independently confirmed: every path exists, no duplicates, all
required M13 runtime files present, no test-only tool improperly pinned,
and the declared closure equals a fresh AST-based execution-closure walk
from the checkpoint's entry points exactly (0 discovered-but-not-pinned,
0 pinned-but-not-discovered). (28 through M14 Issue 6; +8 in M14 Issue 7;
+1 in M14 Issue 2, see above.)

## 8. Route counts

**57** registered routes; **57/57/57/57** registered, matchable,
executable, and contract-compliant (`scripts/verify_operator_
milestone.py`). All 8 M13 protection-evidence routes (observation
list/detail, assessment list/detail, base + `/api/` each) independently
confirmed registered (`scripts/verify_m13_protection_grounding.py`).

## 9. Generated-link counts

`scripts/verify_operator_milestone.py` collects generated internal links
from real catalogue pages and executes every one through the real WSGI
application: **0** failures, **0** generated-link-registered-but-not-
executable results, on every environment tested. That pass/fail invariant
is what this item certifies.

**The exact link count is not asserted here.** An earlier draft of this
document recorded a specific count; independent final-acceptance review
(see `C:\dev\orb-live-m13-final-review-report.md`, Finding 1) found the
count differs by environment even at an identical, byte-diffed-empty
commit tree and an identical hardcoded `as_of` fixture constant (observed:
90 on one local Windows run set, 106 in this repository's own Linux CI
run). The mechanism was not isolated; see the follow-up issue tracking
that investigation. Do not copy a specific count from this document as a
verification target -- re-run the verifier and check `status: PASS` and
`generated_link_count` against your own environment's own output, not
against a number recorded here.

## 10. Export-link counts

Both M13 export kinds (`protection_observation`, `protection_assessment`)
execute successfully through `/exports/{kind}/{id}.json`; schema
completeness (source classification, digest, privacy, zero-write,
release/implementation identity, advisory-not-approval fields)
independently verified by `scripts/verify_m13_protection_grounding.py::
check_exports`.

## 11. Broker call-site counts

**21 total: 17 literal, 4 safe_wrapper, 0 dynamic, 0 unresolved.**
Independently re-derived at Issue 10 (not carried forward from Issue 6's
or Issue 8's report), and cross-checked against the hardened guard's own
enumeration -- the two agree exactly. The single reviewed write-capable
path, `src/robinhood_commissioning_adapter.py:203`, remains visible and
classified `safe_wrapper`. Zero call sites of any kind across the seven
M13 pipeline modules or the operator application.

## 12. Independent verifier result

`scripts/verify_m13_protection_grounding.py` -- **PASS**, 53/53 checks,
0 failed. Human-readable and `--json` machine-readable output agree
exactly.

## 13. Full-suite result

**5951 passed, 7 skipped, 0 failed** (484.05s). Command:
`python -m pytest -q`.

## 14. Architecture audit

`python scripts/architecture_audit.py --format markdown` reproduces the
checked-in `LEAN_ARCHITECTURE_AUDIT.md` byte-for-byte at the reviewed
SHA -- no regeneration was required for this acceptance pass.

## 15. Deployment-readiness audit

`python scripts/deployment_readiness_audit.py` -- clean, exit code 0.

## 16. Milestone verifier

`python scripts/verify_operator_milestone.py` -- `"status":"PASS"`,
57/57/57/57 routes, digest matches.

## 17. Privacy result

No tracked or generated acceptance artifact examined during this issue
(canonical observations, ledger records, assessment store rows, operator
pages, exports, verifier output, runbook examples) contains a credential,
token, raw account number, raw account ID, raw order ID, raw position ID,
session cookie, broker challenge material, or full private broker
payload. Every identifier used in fixtures and the runbook's command
template is either a SHA-256 pseudonymous reference or an explicit
`<PLACEHOLDER>` token -- confirmed by `scripts/verify_m13_protection_
grounding.py`'s independent route/export probes and by
`tests/test_m13_protection_grounding_documentation.py`'s runbook
safety-language contract tests.

## 18. Zero-write result

Independently confirmed: `ProtectionObservationRecord.__post_init__`
structurally refuses a nonzero `write_count` (raises
`ProtectionObservationError` unconditionally); the ledger contains no
`UPDATE`/`DELETE`/`DROP` against the observations table; the checkpoint
integration module contains no broker-write-shaped call text and imports
no broker transport module; the M13 pipeline and the operator application
each contribute zero broker call sites of any kind, per item 11.

## 19. Authority result

Repository-wide AST scan (this issue, independent of Issues 1-8's own
scans) across every M13 module for a `True`-valued assignment to
`protection_grounded`, `activation_approved`, `permits_automated_action`,
`live_ready`, `unattended_ready`, `ACTIVATION_APPROVALS`, or
`ADAPTER_PERMITTED_ACTIVE_PROTECTION_STATES` -- **zero found**.
`strategy.enabled: false` confirmed in the tracked
`config/strategy_v0.yaml`. Final state:

```
strategy.enabled=false
approved protection-state count=0
ACTIVATION_APPROVALS=empty
protection_grounded=false
activation_approved=false
permits_automated_action=false
live_ready=false
unattended_ready=false
```

## 20. Q1 result

**SATISFIED.** M13 remains authority level A1: no order creation,
modification, cancellation, replacement, or flattening capability exists
anywhere in the seven M13 pipeline modules or the operator application
(confirmed by item 11's zero call sites and by source inspection for
order-creation-shaped text). `BROKER_AUTHORITATIVE_SOURCE_CLASSES`
contains exactly one member, `LIVE_READ_ONLY_BROKER_OBSERVATION` --
simulated and test-fixture evidence cannot satisfy live grounding by
construction. `NO_PROTECTIVE_ORDER_OBSERVED` remains a valid, complete,
successful observation outcome, not a failure.

## 21. Q2 result

**UNRESOLVED_BY_DESIGN.** No product-approved fixed observation count
exists anywhere in tracked source. `minimum_distinct_eligible_live_runs`
and `minimum_distinct_observation_times` are policy configuration
(`ProtectionPromotionPolicy`, current version
`m13-promotion-policy-v1-pending-independent-review`), not an approved
answer. `automatic_promotion_permitted` cannot be constructed `True` --
`ProtectionPromotionPolicyError` raises unconditionally.
`promotion_enabled=False` and `independent_review_required=True` in the
default policy. This does not block infrastructure acceptance; it blocks
a future promotion decision until a human answers it.

## 22. Live-evidence status

**ELIGIBLE LIVE PROTECTION EVIDENCE: NOT YET COLLECTED.** No live
checkpoint was executed as part of Issues 1-10. No Robinhood access, no
credential access, and no private-account read occurred during this
milestone's implementation or acceptance. This is a deliberate scope
boundary, not an oversight -- see
`M13_LIVE_READ_ONLY_PROTECTION_OBSERVATION_RUNBOOK.md` for the procedure
a future, separately authorized run would follow.

## 23. Final milestone classification

```
M13_INFRASTRUCTURE_ACCEPTED_AWAITING_ELIGIBLE_LIVE_EVIDENCE
```

with `protection_grounded=false`, `activation_approved=false`,
`permits_automated_action=false`, `live_ready=false`,
`unattended_ready=false`. This is neither a weaker "pending" status
(infrastructure genuinely passed every independently re-verified gate
above) nor a stronger "protection grounded" status (no live evidence has
been collected to ground anything).

## 24. Explicit non-authorization boundaries

This acceptance package does not authorize, and no code path it
describes permits: automated broker access, live order submission,
supervised order submission, unattended execution, strategy activation,
live readiness, unattended readiness, automatic promotion to
`APPROVED_FOR_ACTIVE_PROTECTION`, or any answer to Q2. It does not
authorize running the runbook in
`M13_LIVE_READ_ONLY_PROTECTION_OBSERVATION_RUNBOOK.md` -- that requires
a separate, explicit, per-run operator authorization outside this
document.

## 25. Remaining evidence dependency

Before any future promotion decision: (a) a human must answer Q2; (b) at
least one legitimate live read-only observation must be collected under
explicit per-run authorization, following the runbook; (c) that evidence
must independently satisfy the promotion policy's existing rubric
(source authority, freshness, consistency, privacy, zero-write, coverage,
non-contradiction, independence across runs); (d) a human must perform
the independent review Issue 4's `OperatorReviewContext` and the
milestone handoff anticipate. None of this is Issue 10's responsibility
to perform.

## 26. Next-milestone recommendation

**Known limitation, disclosed rather than silently resolved:** the
tracked `ROADMAP.md`'s own "Milestone 13" entry describes "Capital
Tiering and Scaling," not protection-state grounding. That document's
milestone numbering has diverged from what this repository actually
built (M9's read-only observation checkpoint, M10's durable
scheduler/suspension controller, and this M13). `ROADMAP.md` should be
reconciled with actual repository history in a documentation-focused
follow-up; this issue does not attempt that reconciliation, and does not
redesign the roadmap.

**Addendum (final-acceptance follow-up):** independent final-acceptance
review confirmed this divergence and traced the completed protection-
grounding work to `ROADMAP.md`'s own Milestone 9 ("steps 4-5 pending"),
not its Milestone 13. Disambiguating cross-reference notes were added to
`ROADMAP.md` accordingly, without renumbering, deleting, or moving the
legitimate planned capital-tiering work. See
`C:\dev\orb-live-m13-final-review-report.md` and the tracked issue for
this follow-up.

Given that limitation, the recommendation below is grounded in the
architectural gap this milestone's own review history has already
identified, not in `ROADMAP.md`'s stale numbering:

**Recommended next milestone: wire the existing M10-M12 safety stack
(durable scheduler, suspension controller, offline safety orchestrator)
to have actual authority over the one reachable broker-write call site**
(`src/robinhood_commissioning_adapter.py:203`, still `safe_wrapper` per
item 11). Today that stack and the write path are independently correct
but structurally disconnected -- the safety apparatus cannot yet halt,
suspend, or gate a real write it does not have authority over.

- **Why next:** M13 completed the read-only evidence side of protection
  grounding; the write-authority side is the other half of the same
  "can this system be trusted to act" question, and is the load-bearing
  gap standing between today's infrastructure and any future authority
  increase.
- **Authority ceiling:** still no higher than the current reviewed
  ceiling -- wiring authority into the safety stack is not the same as
  granting it a new capability; the milestone should not increase
  `strategy.enabled`, readiness, or unattended authority on its own.
- **What it unlocks:** a future, separately authorized milestone could
  then consider supervised live writes gated by a safety stack that
  actually has veto power, rather than one that observes a write path it
  cannot stop.
- **What it still does not authorize:** live order submission,
  unattended execution, or any answer to Q2. Those remain separate,
  later, explicit decisions.

This issue does not start that milestone.
