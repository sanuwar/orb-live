# Milestone 7 — Completion Record

**Status: COMPLETE.**

Attended tiny-risk live-write commissioning was implemented, exercised against a
real broker, and closed. This record is the tracked authority on that outcome.

## 1. Scope

Milestone 7 established that this software can perform a **single, attended,
one-share live trade** safely: place one entry, protect it immediately, cancel
that protection under operator authorization, exit once, and prove the account
is flat — with durable sanitized evidence and no possibility of a second write.

Milestone 7 did **not** establish unattended strategy automation. It explicitly
excluded unattended or automated trading, any signal-to-executable bridge,
strategy-defined automated live exits, an automated emergency market exit, any
global configuration switch into live execution, persistent strategy
enablement, and any second live trade. Those exclusions still hold; see
`ROADMAP.md`.

## 2. Checkpoint outcomes

Checkpoints 7A and 7B predate the tracked documentation set. Their outcomes
below are **reconstructed from untracked historical handoff material** and are
marked accordingly; 7C and 7D outcomes are established by tracked artifacts and
by sanitized live evidence verified by hash.

| Checkpoint | Outcome | Basis |
| --- | --- | --- |
| **7A** — offline live-transport contract and schema mapping | Completed offline. Order-tool schemas mapped; no authentication, network, or broker call. | *Reconstructed from untracked handoff material.* |
| **7B** — attended adapter initialization and identity verification | Completed. A live adapter was constructed and the commissioned identity verified, with no order submission. | *Reconstructed from untracked handoff material.* |
| **7C** — one attended real entry plus an immediate exact-quantity protective stop | **Completed and validated live.** Entry filled, protective stop placed and reconciled, provisional protection observed. | Tracked `CHECKPOINT_7C_*.md`; live evidence below. |
| **7D** — attended operator-approved full exit with flat verification and durable evidence | **Behavior exercised and validated live**, in the same runs that completed 7C: controlled flatten authorized, stop cancelled and reconciled terminal, one exit placed and reconciled, `verified_flat=true`, no related non-terminal orders. | Live evidence below. |

7D was exercised as the closing phase of the 7C runs rather than as a separately
labelled execution. The behavior it defines is fully covered.

## 3. Major fixes found and validated

Every one of these was discovered by a live run, diagnosed offline, repaired,
and confirmed by a subsequent live run.

| Fix | What was wrong | Tracked record |
| --- | --- | --- |
| **Cancel `account_number` contract correction** | `cancel_equity_order` omitted a required input field, so the request never reached the broker and the protective stop stayed working until the reconciliation deadline expired. | `CHECKPOINT_7C_CANCEL_TRANSPORT_INVESTIGATION.md` |
| **Bounded ambiguous-exit reconciliation** | An ambiguous exit submission stopped immediately with no read-back, so a filled sell was unrecognizable by construction. Now read-only bounded reconciliation resolves it. | `CHECKPOINT_7C_PLACE_RESPONSE_PARSER_INVESTIGATION.md` |
| **Creation-time blank-symbol parser handling** | The broker returns `symbol` blank on the immediate creation response and populates it on read-back. The parser rejected blank strings, which the advertised schema permits, making every placement ambiguous. | `CHECKPOINT_7C_PLACE_RESPONSE_BLANK_SYMBOL_FIX.md` |
| **Symbol-pending identity verification through status reads** | A blank symbol identifies nothing, so it is never treated as identity and the requested symbol is never substituted. Identity is proven from a subsequent status read through the still-strict status parser. | same |
| **Non-blank symbol mismatch fails closed** | A response naming a different instrument is terminal, not reconcilable; it is carried by a distinct error type so it cannot be routed into reconciliation. | same |
| **Tracked retirement registry** | Reuse protection depended on local evidence markers; nine of twenty identities had no on-disk trace. All 20 are now refused from tracked source. | `CHECKPOINT_7C_RETIRED_RUN_ID_ENFORCEMENT.md` |
| **Atomic placement-diagnostic persistence** | Diagnostics were written once per fact (8–11 evidence writes per failed placement) and could leave partial state. Now one validated all-or-nothing batch. | `CHECKPOINT_7C_FINAL_EVIDENCE_PERSISTENCE_HOUSEKEEPING.md` |

Supporting diagnostics that made the last two diagnoses possible are recorded in
`CHECKPOINT_7C_PLACE_ORDER_OBJECT_FIELD_DIAGNOSTICS.md`. Earlier attempt
outcomes are recorded in the tracked `CHECKPOINT_7C_ATTEMPT_*_INCIDENT_REVIEW.md`
files.

## 4. Completed live evidence

Sanitized identifiers and hashes only. No prices, order identifiers, account
data, or raw evidence content appear here or in the evidence itself.

### Run `cp7c-live-aapl-one-share-20260731-05`

- Evidence SHA-256: `c533716beab08061f3fe1ffeaad9b827e6ace7e93b0f1fe01ac582f39e220327`
- Purpose: structural envelope diagnostic
- Result: **COMPLETE**, verified flat
- Finding: all three placements reported `order_object_returned`, which
  eliminated every envelope-layer hypothesis and localized the defect to the
  order object

### Run `cp7c-live-aapl-one-share-20260731-06`

- Evidence SHA-256: `a78a5640f1b977abebb67a0f5394b82149c7ad38c28e0180787c545bf08073df`
- Purpose: exact contract-field diagnostic
- Result: **COMPLETE**, verified flat
- Finding: entry, stop, and exit each reported
  `place_order_object` / `symbol` / `empty_text` — one field, one failure mode,
  unanimous across all three roles

### Run `cp7c-live-aapl-one-share-20260731-07`

- Evidence SHA-256: `fe4825a9588b58eeb0c4532eb0394ff37a10c542b583016a0c52d98057317875`
- Purpose: final blank-symbol repair validation
- Result: **COMPLETE**, verified flat
- Findings: `ENTRY_PLACE_SYMBOL_PENDING`, `STOP_PLACE_SYMBOL_PENDING`, and
  `EXIT_PLACE_SYMBOL_PENDING`, each followed by `*_RECONCILED_BY_ORDER`; **no**
  `*_PLACE_RESPONSE_MALFORMED`; **no** `*_PLACE_SYMBOL_MISMATCH`; **no**
  `contract_field_alias=symbol`; no related non-terminal orders remaining

The blank symbol still appeared in run `-07` — the repair did not make the
broker behave differently. What changed is that a schema-legal blank response
now parses and routes to a pending state, and identity is proven by reading the
order back rather than by trusting the creation response.

## 5. Final invariants

| Invariant | Value |
| --- | --- |
| Entry place maximum | 1 |
| Protective stop place maximum | 1 |
| Stop cancel maximum | 1 |
| Cancel retries | 0 |
| Exit place maximum | 1 |
| Exit retries | 0 |
| Unresolved, conflicting, or ambiguous states | fail closed; operator required |
| `strategy.enabled` | `false` |

Also unchanged: no second entry or trade, no averaging down, no shorting, no
margin, no fractional shares, no repeated write after ambiguity, no exit while a
protective sell may remain executable, and no exit when the latest validated
position is flat.

## 6. Verification at closure

| Check | Result |
| --- | --- |
| Authoritative tracked test suite | 2,287 passed |
| Canonical reviewed source hashes | 9 of 9 |
| Runtime broker call-site guard | PASS |
| `git diff --check` | clean |

## 7. Closure statement

**Milestone 7 is COMPLETE.**

No further Milestone 7 live validation is required unless a later tracked change
modifies broker writes, reconciliation, authorization, identity verification, or
evidence integrity. A change in any of those areas reopens the question of live
validation on its own merits.
