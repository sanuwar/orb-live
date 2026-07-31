# Checkpoint 7C — Creation-Time Blank Symbol Fix

The place-response defect that made every placement ambiguous across five live
runs is now identified and repaired. No broker, MCP, network, launcher, or
wrapper activity occurred in this work.

## The evidence

Run `cp7c-live-aapl-one-share-20260731-06`, evidence SHA-256
`a78a5640f1b977abebb67a0f5394b82149c7ad38c28e0180787c545bf08073df`, returned
the same verdict for all three placements:

| role | contract_scope | field alias | violation kind | envelope |
| --- | --- | --- | --- | --- |
| entry | `place_order_object` | **`symbol`** | **`empty_text`** | `order_object_returned` |
| stop | `place_order_object` | **`symbol`** | **`empty_text`** | `order_object_returned` |
| exit | `place_order_object` | **`symbol`** | **`empty_text`** | `order_object_returned` |

The run itself completed `COMPLETE` / `verified_flat=true` with one write per
role, zero retries, and no related non-terminal orders. Every placement was
rejected locally and recovered by bounded read-only reconciliation.

`empty_text` is specific: the value *was* a string — not absent, not null, not
the wrong type — but blank.

## Why creation and status responses differ

The broker returns `symbol` **blank on the immediate creation response** and
populates it by the time the order is read back through
`get_equity_orders`. That is why `parse_order_status` succeeded 12 times in the
same run on the very orders whose placements were rejected.

The advertised schema supports this: `symbol` is declared
`{"type": "string"}` with **no `minLength`**. A blank string is schema-valid.
The parser's `_require_text` additionally rejected blank strings — a rule
stricter than the advertisement — and that extra strictness is the whole defect.

This also corrects the earlier "read/place symmetry" argument. The two parsers
are identical; they simply do not see the same object. The symmetry holds for
the schema and fails for the lifecycle.

## Why `parse_order_status` stays strict

A status read is what *establishes state*. By the time an order is read back
the symbol is populated, so a blank symbol there is a genuine contract
violation and must stay one. Only the creation path was relaxed.

## Why a blank symbol is not identity, and why the request is not substituted

A blank symbol identifies **nothing**. Treating it as "presumably what we asked
for" would convert an unidentified order into an assumed one, which is exactly
the class of inference this system refuses everywhere else. The broker's value
is carried through unchanged, blank included; the requested symbol is never
copied into the parsed response.

Identity is proven the only way it can be — by reading the order back. All
three bounded read-only reconcilers already require the reconciled order's
symbol to equal the requested symbol, and those reads go through the still-strict
`parse_order_status`.

## The three symbol states

| creation-response symbol | classification | reconciles? | outcome |
| --- | --- | --- | --- |
| non-blank, matching | — | n/a | direct accepted path, unchanged |
| **blank / whitespace** | `{ROLE}_PLACE_SYMBOL_PENDING` | **yes** | bounded read-only reconciliation proves the symbol |
| **non-blank, different** | `{ROLE}_PLACE_SYMBOL_MISMATCH` | **no** | fails closed at the write boundary |

The mismatch case deliberately does **not** reuse `*_PLACE_RESPONSE_MALFORMED`,
because that category *is* routed into reconciliation. A response that
positively names another instrument cannot be resolved by reading more; it is
terminal. It is raised as `PlacementSymbolMismatchError` — not an
`AmbiguousWriteError` — and handled on the same established pattern as
`BrokerRejectedError`: record the disposition, hand off, no reconciliation, no
further write. The offending symbol never appears in the error or in evidence.

Pending is neither a rejection nor a transport failure, and its diagnostics say
so: `call_returned=true`, `response_shape=documented_envelope`,
`transport_classification=NONE`.

## Parser acceptance change

Exactly one field, in one direction, on one path:

- `parse_place_response` accepts `symbol` when it is a **present, non-null
  string**, even if blank or whitespace-only
- missing, null, and non-string `symbol` are still rejected
- **no other field** was relaxed — every other required text field keeps the
  strict non-blank rule, and no numeric, enum, range, or format check moved
- `parse_order_status` is untouched

The field-violation inspector was updated in step so it no longer accuses
`symbol` / `empty_text` for an input the creation parser now accepts. Naming a
field the parser did not reject would send the next investigation after the
wrong thing.

## Write limits — unchanged

entry 1 · stop 1 · cancel 1 · cancel retries 0 · exit 1 · exit retries 0.
Reconciliation remains read-only and bounded; the only change is that an
accepted blank creation symbol is routed into the reconciliation that already
existed. No second placement is possible in any of the three symbol states.
`strategy.enabled=false`.

## What the next live run should show

A placement whose creation response carries a blank symbol should now record
`{ROLE}_PLACE_SYMBOL_PENDING` and resolve through reconciliation — and should
**no longer** record `*_PLACE_RESPONSE_MALFORMED` or
`contract_field_alias=symbol` / `contract_violation_kind=empty_text`.

If placements instead parse directly, the broker began populating the symbol at
creation; that is also a valid outcome and needs no further change.
