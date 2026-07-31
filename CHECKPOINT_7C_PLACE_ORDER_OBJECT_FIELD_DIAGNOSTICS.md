# Checkpoint 7C — Place Order-Object Field Diagnostics

Offline diagnostic-fidelity work. No broker, MCP, network, launcher, or
wrapper activity occurred. **The parser accepts and rejects exactly what it did
before this branch.** This identifies the violation; it does not repair it.

## The successful live run

Run `cp7c-live-aapl-one-share-20260731-05`, evidence SHA-256
`c533716beab08061f3fe1ffeaad9b827e6ace7e93b0f1fe01ac582f39e220327`, completed
end to end:

| | result |
| --- | --- |
| final state | `COMPLETE`, `verified_flat=true` |
| entry / stop / cancel / exit attempts | 1 / 1 / 1 / 1 |
| cancel retries · exit retries | 0 · 0 |
| entry | `ENTRY_PLACE_RESPONSE_MALFORMED` → `ENTRY_RECONCILED_BY_ORDER` |
| stop | `STOP_PLACE_RESPONSE_MALFORMED` → `STOP_RECONCILED_BY_ORDER` |
| cancel | `documented_envelope`, accepted, → `STOP_CANCEL_RECONCILED_BY_ORDER` |
| exit | `EXIT_PLACE_RESPONSE_MALFORMED` → `EXIT_RECONCILED_BY_ORDER` |

One share bought, protective stop cancelled, one share sold, flat and verified,
no manual recovery. Every placement was rejected locally and every one was
recovered by bounded read-only reconciliation. The cancel repair (PR #34) and
the ambiguous-exit reconciliation (PR #36) both held on their live exercise.

## Why the envelope is exonerated

All three roles reported `envelope_classification = order_object_returned`,
with `data_object_present=true`, `order_key_present=true`,
`order_value_null=false`, `order_object_present=true`,
`identifier_field_present=true`, and `unexpected_data_keys_present=false`.

That refutes both candidates carried by the previous investigation. `data.order`
was not null, and `data` carried no unexpected key. The envelope is exactly the
advertised `{"data": {"order": {...}}, "guide": "..."}`. The rejection is inside
the order object.

## Why the earlier symmetry argument was wrong

The prior investigation argued: `parse_order_status` and `parse_place_response`
validate the order object identically, reads succeed, therefore the order object
cannot be the cause. The live evidence contradicts that conclusion, so the
premise is incomplete.

The two parsers are identical, but **they do not see the same object**. The
place response returns the order as it exists at creation; the read path returns
the same order seconds later, after reconciliation polling. A field the contract
requires as a non-nullable string can legitimately be absent or null at creation
and populated by the time it is read back — `fees`, `cumulative_quantity`, and
`placed_agent` are the obvious candidates, none of them yet confirmed. The
symmetry holds for the schema and fails for the lifecycle.

This branch does not assume which field. It makes the runtime say so.

## Diagnostic design

A pure inspector, `classify_place_order_object_violation`, runs **only after**
`parse_place_response` has already raised. It re-walks the order object in the
parser's own field order and names the first violation. It never parses on the
runtime's behalf, never decides whether a write succeeded, and cannot change
what the parser accepts. When the order object is contract-valid it returns
`None`, meaning the rejection lay elsewhere.

Three fixed vocabularies:

- **scope** — `place_order_object`
- **field alias** — one per canonical schema field, plus `order_object` and
  `unexpected_field`. Only `stop_price` needed renaming, to
  `stop_trigger_price`, because the canonical name contains a prohibited
  evidence term. A test asserts no alias collides with any prohibited term.
- **violation kind** — `absent`, `null`, `wrong_type`, `empty_text`,
  `invalid_decimal`, `invalid_enum`, `invalid_format`, `unexpected_field`,
  `contradictory_fields`, `unknown`

`ResponseContractViolationError` gained `safe_contract_scope`,
`safe_field_alias`, and `safe_violation_kind`, so no caller parses an exception
message. The class name was added to the sanitized exception allowlist; before
this it surfaced as `UNKNOWN_EXCEPTION`.

Recorded as `{role}_contract_scope`, `{role}_contract_field_alias`, and
`{role}_contract_violation_kind` in the existing `place_diagnostics` container,
at the single placement error boundary, for entry, stop, and exit alike.

### Privacy

A field VALUE is never returned or recorded — only an alias and a kind, both
from fixed vocabularies. An unexpected field is reported as
`unexpected_field`, never by its actual name, so a surprising key cannot leak
through the diagnostic. Unknown facts are dropped rather than defaulted: a value
outside the allowlist is silently skipped, so nothing is invented and a
diagnostic can never fail a run that is already handling a write ambiguity.

### Fidelity

A test mutates **every** required field through seven hostile values each and
asserts the inspector claims a violation exactly when the parser rejects —
never more, never less. Absence of every required field, null of every
non-nullable field, and type, emptiness, decimal, range, and contradiction
failures are each covered by name.

## Parser acceptance is unchanged

`parse_place_response` and `parse_order_status` accept and reject exactly what
they did on main. No nullable field is newly accepted, no required field became
optional, and no type, enum, decimal, or range check was relaxed. The inspector
is additive and runs only on the already-failed path.

## How the next live result selects the repair

Read `place_diagnostics.entry_contract_field_alias` and
`entry_contract_violation_kind` from the next run. Together they name one field
and one failure mode, which selects the correction directly:

- `absent` or `null` on a field the schema calls non-nullable → the advertised
  schema overstates what a *creation* response carries. The correction is a
  creation-time variant of the order-object contract for that field in
  `parse_place_response` only. `parse_order_status` must not be relaxed: it
  works, and it is what establishes state.
- `wrong_type` or `invalid_decimal` → the wire type differs from the
  advertisement for that field; correct the field's type handling.
- `unexpected_field` → the advertised `additionalProperties: false` is wrong for
  creation responses.
- `contradictory_fields` → creation-time quantities are not yet consistent and
  the derivation must not run before a fill.

No repair is justified before that value is read.

## Spent run status

`cp7c-live-aapl-one-share-20260731-05` is permanently spent: its completed
evidence directory and wrapper-used marker both exist. It was not rerun. No new
live Run ID or wrapper was created by this work.
