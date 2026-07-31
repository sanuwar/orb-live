# Checkpoint 7C — Cancel Transport Investigation

Offline diagnosis of two consecutive attended commissioning runs that both
ended `OPERATOR_REQUIRED` with reason code `STOP_CANCEL_TIMEOUT`. No broker,
MCP, network, or launcher activity occurred during this investigation.

## Retired identifiers

Both Run IDs and both wrappers are permanently retired and must never be
reused.

| Run | Run ID | Wrapper | Sanitized evidence SHA-256 |
| --- | --- | --- | --- |
| A | `cp7c-live-aapl-one-share-20260730-04` | `checkpoint_7c_live_run_20260730_04.ps1` | `1def5a3ad4b384731ad49a4bc56818b8f68500cb3c22275b77aafa12e5a031da` |
| B | `cp7c-live-aapl-one-share-20260730-05` | `checkpoint_7c_live_run_20260730_05.ps1` | `97adf727f85699ab0ca8d19f772e90124b7794f83c73629ca071201802d9b3f9` |

Both hashes were re-verified against the on-disk partial evidence before
analysis. Both wrappers remain untracked.

Both Run IDs are enforced in code: each appears in `RETIRED_LIVE_RUN_IDS` in
`checkpoint_7c_one_share_commissioning_launcher.py`, so
`validate_live_run_request` rejects either one before any session is opened.
Retirement therefore does not depend on the operator supplying a fresh
evidence root.

## Repeated reason code

`STOP_CANCEL_TIMEOUT`, failure stage `CANCEL_RECONCILIATION`, in both runs.

## Behavioural comparison

The two incidents are behaviourally identical. Every safety-relevant field
matches; only incidental counters differ.

| Field | Run A | Run B |
| --- | --- | --- |
| reason code | `STOP_CANCEL_TIMEOUT` | `STOP_CANCEL_TIMEOUT` |
| latest stage | `CANCEL_RECONCILIATION` | `CANCEL_RECONCILIATION` |
| terminal state | `OPERATOR_REQUIRED` | `OPERATOR_REQUIRED` |
| cancel attempts | 1 | 1 |
| cancel retries | 0 | 0 |
| cancel write boundary | `before` reached, `after` not reached | identical |
| cancel response validation | never performed — the call raised first | identical |
| post-cancel order reads | 24 | 24 |
| post-cancel position reads | 24 | 24 |
| normalized stop states observed | `confirmed` on every read | `confirmed` on every read |
| exit attempts | 0 | 0 |
| session closure | clean, single close | clean, single close |
| elapsed duration | not recorded (gap) | not recorded (gap) |

Differences: Run A performed 2 entry-reconciliation polls and 32 order/position
reads overall; Run B performed 1 poll and 31 reads. Run B additionally carries
`ENTRY_AUTHORIZATION_APPROVED` and `FLATTEN_AUTHORIZATION_APPROVED`
classifications because it ran on the new y/N authorization UX. Neither
difference touches the cancel path.

The decisive shared sequence in `conservative_classifications` is:

```
STOP_CANCEL_TRANSPORT_AMBIGUOUS
STOP_CANCEL_STILL_ACTIVE  x22
STOP_CANCEL_TIMEOUT
OPERATOR_REQUIRED
```

## Root cause

`CANCEL_ARGUMENT_CONTRACT_MISMATCH` — the cancel request omitted a required
input field, so it was rejected before reaching the broker.

The advertised `cancel_equity_order` inputSchema requires **both**
`account_number` and `order_id`, with `additionalProperties: false` and both
names listed under `required`. The adapter transmitted only `order_id`:

```python
data = await self._call("cancel_equity_order", arguments, write_key="cancel:stop")
# arguments was exactly {"order_id": ...}
```

`cancel_equity_order` was the only tool call in the adapter that omitted
`account_number`. `get_equity_positions`, `get_equity_orders`,
`review_equity_order`, and `place_equity_order` all transmit it, and all of
those calls succeeded in both runs — 31 to 32 successful reads each.

This is corroborated, not merely inferred, by the evidence:

1. The classification is `STOP_CANCEL_TRANSPORT_AMBIGUOUS`, which the adapter
   raises **only** when `session.call_tool(...)` itself raises. It is a
   different branch from `STOP_CANCEL_RESPONSE_MALFORMED`, which is raised when
   a result is returned but fails envelope parsing. The call therefore never
   produced a result to parse.
2. The protective stop remained in state `confirmed` — classified
   `ACTIVE_OR_WORKING` — across all 24 bounded reads spanning the full 30-second
   deadline. It never reached `pending_cancelled`. A cancel that the broker had
   accepted would have moved the order to `pending_cancelled` and then to
   `cancelled`.
3. The operator observed the protective stop still `Placed` in the Robinhood UI
   afterwards, which is consistent with a request the broker never saw.
4. Both runs are identical, which is the signature of a deterministic contract
   defect rather than a transient network condition.

### Which of the four failure modes this is

1. **Cancellation submission failing — YES.** The request was rejected at input
   validation and never reached the order book.
2. Cancellation response parsing failing — no. Parsing was never reached.
3. Cancellation accepted but broker state remaining active — no. The order never
   entered a cancelling state.
4. Cancellation successful but subsequent reads stale — no. Reads were healthy
   throughout; 24 order reads and 24 position reads all returned well-formed
   records.

A longer timeout would not have helped and is explicitly **not** the fix. The
30-second reconciliation deadline behaved exactly as designed; extending it
would only have delayed the same `OPERATOR_REQUIRED` outcome while leaving an
unprotected-flatten window open longer.

## Timeout layers

| Layer | Configured value | Applies to |
| --- | --- | --- |
| MCP `ClientSession` read timeout | not set (`None`) | would apply to submission |
| `streamablehttp_client` | no commissioning-level override | transport |
| adapter `_call` | none | submission |
| `STOP_CANCEL_DEADLINE_SECONDS` | 30 s | reconciliation only |
| `POLL_SECONDS` | 1.0 s | spacing between bounded reads |
| `EXIT_FILL_DEADLINE_SECONDS` | 30 s | post-exit reconciliation |
| `FINAL_FLAT_DEADLINE_SECONDS` | 15 s | final flat verification |

**No timeout applies to the cancel submission itself.** Every configured
deadline governs reconciliation after the write. Consequently no Python timeout
cancelled an underlying MCP request in either incident, and no MCP request could
have continued after a local timeout — because no local timeout fired at the
transport layer. `STOP_CANCEL_TIMEOUT` is a reconciliation verdict, not a
transport verdict; the name is easy to misread and the new
`cancel_timeout_layer` diagnostic now records the distinction explicitly.

`asyncio.CancelledError` derives from `BaseException` and is deliberately **not**
converted into a write ambiguity: it propagates, so structured cancellation is
never swallowed and a cancelled task is never mistaken for a completed write.
Because it propagates past every handler, no evidence write can follow it, so
there is deliberately no `cancel_task_cancelled` diagnostic and no
`CancelledError` entry in the classification allowlist — either would imply an
observation this code is structurally unable to make.

`cancel_timeout_layer` names a layer only for a recognized timeout. A cancel
call that raised did not necessarily time out: an argument-contract rejection is
recorded `not_applicable`, not `transport`. Only `TimeoutError` claims the
transport layer, and only the reconciliation deadline claims
`reconciliation_deadline`.

An explicit broker rejection has its own response-shape classification,
`broker_rejected`. It is **neither a timeout nor a malformed response**: the
adapter detects the rejection from the error flag on the result, before any
payload field is read, so no cancel envelope is ever parsed. `documented_envelope`
would claim a well-formed cancel response arrived, `malformed_envelope` would
claim one arrived and failed parsing, and `not_reached` would deny that the
transport returned at all. Its timeout layer is `not_applicable`, because a
rejection is terminal — no deadline governed it and no reconciliation follows,
so the `none` used on the success path would carry the wrong implication. Both
accepted-field diagnostics stay `unknown`: the rejection short-circuits before
the payload is inspected, so whether an `accepted` field was present is
genuinely unobserved and must not be reported as `false`.

No raw rejection payload, response body, or exception message is persisted —
only the allowlisted class name `BrokerRejectedError` and the allowlisted shape
value. This is **diagnostic fidelity only**: broker-write behaviour and
reconciliation behaviour are unchanged. A rejected cancel still attempts exactly
one cancel, performs zero retries, enters no reconciliation, submits no exit,
leaves the protective stop executable, and hands off as `STOP_CANCEL_REJECTED`
under `OPERATOR_REQUIRED`.

### The rejection now has its own failure category

An explicit rejection previously recorded `failure_category =
CANCEL_RECONCILIATION_FAILED` and `failure_stage = CANCEL_RECONCILIATION`. Both
were wrong, and for the same reason: `STOP_CANCEL_REJECTED` was not an allowed
failure category, so the classifier fell back to a stage-derived name — and the
stage was set to `CANCEL_RECONCILIATION` before the cancel write, even though
**bounded reconciliation never started**. Every reconciliation read counter was
zero while the evidence claimed reconciliation had failed.

`STOP_CANCEL_REJECTED` is now an allowed failure category, matching the handoff
reason code exactly, the way `STOP_CANCEL_TIMEOUT` already does. A new stage,
`STOP_CANCEL_SUBMISSION`, names the cancel write and its explicit verdict; it is
scoped to the write alone and restored to `CANCEL_RECONCILIATION` before any
reconciliation record, so a cancel that does proceed to bounded reconciliation
and times out there is still attributed to reconciliation. An unclassified
failure inside the submission window maps to `STOP_CANCEL_TRANSPORT_AMBIGUOUS`,
because a cancel of unknown fate is an ambiguous write, not a reconciliation
failure.

The evidence therefore no longer calls an explicit rejection a reconciliation
failure, and no longer implies that reconciliation ran. This is **classification
naming only**. Broker-write, retry, handoff, and exit behaviour are unchanged:
one cancel attempt, zero retries, zero reconciliation reads, zero exit attempts,
the protective stop left executable, the session closed once, and the
`STOP_CANCEL_REJECTED` handoff under `OPERATOR_REQUIRED` preserved. Historical
incident reason codes and the retired-run records above are untouched.

## Response-contract findings

- The documented cancel envelope is `{"data": {"accepted": <bool>}, "guide": "..."}`.
  The parser matches it, requires a real boolean, and refuses to coerce `1`,
  `"true"`, or `None`. That behaviour is correct and unchanged.
- `accepted=true` means the broker accepted the *request*, never that the order
  is cancelled. Reconciliation remains mandatory in both directions.
- The adapter unwraps `{"data"}` / `{"data","guide"}` and re-wraps with its own
  synthetic guide before parsing, so a broker response missing the prose `guide`
  is tolerated. The `accepted` boolean itself is still required from the broker,
  so no cancellation conclusion can be manufactured. This is now pinned by test.
- **Separate open finding, not repaired here:** both runs also recorded
  `ENTRY_PLACE_RESPONSE_MALFORMED` and `STOP_PLACE_RESPONSE_MALFORMED`. Both
  placements really did reach the broker and were recovered by order
  reconciliation, so the run stayed safe, but the strict place-response parser
  does not match what the server actually returns. The evidence does not name
  the offending field, so no repair is justified yet. See "Remaining risks".

## Diagnostics added

A new sanitized `cancel_diagnostics` evidence map, with a strict allowlist on
both keys and values. Any key or value outside the allowlist raises rather than
being written, so free text cannot reach evidence by construction.

| Key | Allowed values |
| --- | --- |
| `cancel_call_started` | `true` / `false` / `unknown` |
| `cancel_call_returned` | `true` / `false` / `unknown` |
| `session_healthy_after_cancel` | `true` / `false` / `unknown` |
| `cancel_response_contained_accepted` | `true` / `false` / `unknown` |
| `cancel_accepted_value` | `true` / `false` / `unknown` |
| `cancel_elapsed_duration_bucket` | `under_1_second` / `1_to_5_seconds` / `5_to_15_seconds` / `over_15_seconds` / `unknown` |
| `cancel_timeout_layer` | `none` / `not_applicable` / `reconciliation_deadline` / `transport` / `unknown` |
| `cancel_transport_classification` | allowlisted exception class names, `UNKNOWN_EXCEPTION`, or `NONE` |
| `cancel_response_shape_classification` | `broker_rejected` / `documented_envelope` / `empty_result` / `malformed_envelope` / `not_reached` / `unknown` |

Duration is recorded only as a coarse bucket. Raw exception text, raw responses,
request arguments, order identifiers, account information, tokens, credentials,
headers, authorization input, and fingerprints are never persisted. Had these
diagnostics existed during Run A, the exception class and
`cancel_response_shape_classification=not_reached` would have identified the
submission failure immediately instead of requiring two incidents.

## Runtime repair

One repair, at the proven mismatch only: the adapter now transmits both
advertised required fields.

```python
payload = {
    "account_number": self.commissioned_account_number,
    "order_id": order_id,
}
```

`account_number` is taken from the already verified commissioned identity rather
than from the caller, so a cancel can never be addressed to an account this
adapter did not resolve. The caller-facing contract is unchanged: the
orchestrator still passes exactly `{"order_id": stop_id}`, and any other caller
payload is still rejected.

No trading decision, deadline, state transition, write maximum, or
reconciliation rule was changed.

## Why a second cancel remains prohibited

Even though this specific failure is now believed to be a request that never
reached the broker, a second cancel attempt stays forbidden:

- the local process cannot distinguish "never submitted" from "submitted, result
  lost" without a read, and a read is exactly what reconciliation already does;
- a duplicate cancel against an order that did reach `pending_cancelled` risks
  an ambiguous second write with no added information;
- the protective stop is the position's only downside protection while it is
  working, so any additional write near it must be operator-directed;
- the repair removes the *cause*, so a retry would buy nothing that the fix does
  not already provide.

The maximums are unchanged: entry place 1, stop place 1, stop cancel 1, cancel
retries 0, exit place 1.

## Remaining risks

1. The root cause is proven against the locally reviewed schema snapshot and the
   evidence, but it has not been confirmed against the live server. The next
   attended run is the confirmation.
2. The place-response parser mismatch is still unexplained. It has been benign
   twice because reconciliation recovers, but it means every write currently
   takes the ambiguous path. The new diagnostics do not yet cover the place
   calls.
3. `cancel_transport_classification` will read `UNKNOWN_EXCEPTION` for MCP SDK
   exception types, since only a fixed allowlist of class names may be
   persisted. That still distinguishes "raised" from "returned", which is the
   distinction the incidents lacked. `BrokerRejectedError` is allowlisted
   explicitly, so an outright broker refusal is no longer flattened into
   `UNKNOWN_EXCEPTION`.
4. If the cancel now succeeds, the run will exercise the exit-place path live for
   the first time. That path has offline coverage only.
5. A fill that races the cancel remains possible and is handled as a
   partial-fill / already-terminal case rather than as a cancellation.

## Criteria for another live test

1. This branch reviewed and merged, with CI green.
2. Approved release SHA pinned in a fresh wrapper with a fresh, never-used
   Run ID.
3. Operator present, Robinhood UI open, and prepared to assume manual control.
4. Both prior stops confirmed resolved in the UI before any new run; no
   unresolved AAPL position or working order may remain.
5. `strategy.enabled=false` and the tracked tree clean at the pinned SHA.
6. Accept in advance that an `OPERATOR_REQUIRED` outcome is a valid result and
   that no automatic retry will occur.
7. After the run, compare `cancel_diagnostics` against this document: a
   `cancel_call_returned=true` with `cancel_response_shape_classification=documented_envelope`
   confirms the repair regardless of the eventual order state.
