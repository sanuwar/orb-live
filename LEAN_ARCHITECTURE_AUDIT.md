# Lean Architecture Audit

Deterministic scope: tracked Python files only. Static absence is uncertainty, not proof of dead code.

- Production LOC: 93305
- Test LOC: 71213
- Estimated removable production LOC: 0
- Estimated removable test LOC: 0
- Exact helper-body candidates: 0
- Decision: No shared-helper change is justified by exact body-equivalence evidence.
- Detailed reason-code, pattern, and duplicate-body inventories: JSON output only

## Prioritized findings

1. **NEEDS_RUNTIME_EVIDENCE / MEDIUM -- Repeated constrained-value validators**
   Evidence: 80 repeated helper placements across identifier_validation=13, path_validation=7, permission_validation=2, serialization=50, timestamp_validation=13
   Interpretation: Exact normalized bodies shared across modules: 0. Similar names alone do not establish contract equivalence.
   Estimated removable LOC: production 0, tests 0

2. **NEEDS_RUNTIME_EVIDENCE / HIGH -- Transition journals are intentional durable evidence**
   Evidence: 4 transition tables: acknowledgment_transitions, alert_transitions, runner_transitions, scheduler_transitions
   Interpretation: Preserve by default. Static table purpose cannot prove equivalent aggregate, atomicity, or restart contracts.
   Estimated removable LOC: production 0, tests 0

3. **NEEDS_RUNTIME_EVIDENCE / HIGH -- Spent-identity ledgers protect different replay domains**
   Evidence: 4 spent-identity tables: acknowledgment_spent, spent_actions, spent_driver_identities, spent_identities
   Interpretation: Preserve by default. Do not merge without equivalent atomicity and persisted compatibility evidence.
   Estimated removable LOC: production 0, tests 0

4. **NEEDS_RUNTIME_EVIDENCE / MEDIUM -- Public surfaces without static repository callers**
   Evidence: 13 public definitions have zero name/attribute references outside their definitions.
   Interpretation: This is uncertainty, not proven dead code; dynamic dispatch, operator entrypoints, and external consumers remain invisible.
   Estimated removable LOC: production 0, tests 0

5. **NEEDS_RUNTIME_EVIDENCE / LOW -- Enum members without qualified references**
   Evidence: 100 enum members have no qualified construction/reference site.
   Interpretation: Persisted values, iteration, and indirect deserialization can make these reachable; require runtime evidence before deletion.
   Estimated removable LOC: production 0, tests 0

6. **NEEDS_RUNTIME_EVIDENCE / HIGH -- Fingerprint and provenance layers bind distinct trust boundaries**
   Evidence: Trust-related modules include scripts/check_morning_watch_readiness.py, scripts/deployment_readiness_audit.py, src/attestation_authority.py, src/shadow_readiness_report.py.
   Interpretation: Preserve by default. Filename and vocabulary overlap cannot prove equivalent identities or trust semantics.
   Estimated removable LOC: production 0, tests 0

7. **NO_ACTION / MEDIUM -- Layered status vocabularies overlap intentionally**
   Evidence: Runner, driver, scheduler, liveness, operational status, alert, acknowledgment, and readiness enums reuse failure/review/suspension concepts.
   Interpretation: Each vocabulary is a typed boundary. Prefer adapters over a single cross-layer mega-enum.
   Estimated removable LOC: production 0, tests 0

8. **NEEDS_RUNTIME_EVIDENCE / LOW -- Serialization allowlists repeat exact-key mechanics**
   Evidence: serialization helpers appear in 50 modules.
   Interpretation: A shared primitive may reduce boilerplate, but field privacy and error contracts require equivalence evidence.
   Estimated removable LOC: production 0, tests 0

9. **NO_ACTION / HIGH -- Large modules concentrate durable invariants**
   Evidence: largest production modules: src/trade_state.py (6006), src/broker_action_saga.py (3051), src/simulation_runner.py (2274), src/broker_saga_orchestrator.py (2058), src/one_share_commissioning_orchestrator.py (1728)
   Interpretation: Size alone is not deletion evidence; transaction and restart invariants require cohesive review.
   Estimated removable LOC: production 0, tests 0

10. **NO_ACTION / HIGH -- Test-heavy modules are visible, not automatically excessive**
   Evidence: largest tests: tests/test_broker_action_saga.py (2061), tests/test_broker_saga_executor.py (1809), tests/test_broker_saga_orchestrator.py (1729), tests/test_entry_recovery.py (1509), tests/test_read_only_observation_runtime.py (1504); 36 modules have more test LOC than production LOC.
   Interpretation: Higher test LOC is expected for state, boundary, restart, and adversarial matrices; remove only behaviorally duplicate cases.
   Estimated removable LOC: production 0, tests 0

## Limitations

- Static references do not prove runtime reachability or dead code.
- Reference indexing can miss nested or relative module access and can count shadowed local names.
- Persisted enum and schema compatibility require runtime evidence.
- Similarity of safety ledgers or fingerprints does not prove equivalent atomicity or trust semantics.
- SQL inventory uses a conservative raw-source pattern and may include non-executable examples.
- Exact duplicate test bodies can have distinct signatures, decorators, parametrization, or fixture semantics.
- Exact helper candidates still require global-binding and caller-contract equivalence review.
