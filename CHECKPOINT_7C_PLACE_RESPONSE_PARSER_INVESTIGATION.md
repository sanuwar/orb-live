# Checkpoint 7C — place_equity_order Response Parser Investigation

Offline diagnosis of the recurring place-response rejection. No broker, MCP,
network, launcher, or wrapper activity occurred during this investigation.

**Outcome: the root cause is narrowed but NOT proven. The parser was not
changed.** This branch adds structural diagnostics only, so the next attended
run identifies the exact envelope layer instead of a fourth investigation.

## Recurring live symptom

Every attended run has classified every placement as ambiguous:

| Run | entry | stop | exit |
| --- | --- | --- | --- |
| `…20260730-04` | `ENTRY_PLACE_RESPONSE_MALFORMED` | `STOP_PLACE_RESPONSE_MALFORMED` | not reached |
| `…20260730-05` | `ENTRY_PLACE_RESPONSE_MALFORMED` | `STOP_PLACE_RESPONSE_MALFORMED` | not reached |
| `…20260731-02` | `ENTRY_PLACE_RESPONSE_MALFORMED` | `STOP_PLACE_RESPONSE_MALFORMED` | ambiguous, then `FLATTEN_FAILED` |

Robinhood showed every one of those orders accepted or filled. Entry and stop
recovered through bounded reconciliation; on base main the exit had no
equivalent, which is what PR #36 repaired. PR #36 deliberately did **not**
touch the parser, and neither does this branch.

## What the evidence proves

1. **The order object is not the problem.** `parse_order_status` and
   `parse_place_response` validate the order object with the *same*
   `ORDER_REQUIRED_FIELDS`, the same `ORDER_OPTIONAL_FIELDS`, the same
   required-text set, and the same decimal helpers. Reads succeeded 31–32
   times per run through that identical validation — including reads that
   reconciled the very entry and stop orders whose placements were rejected.
   A broker order object that satisfies the strict contract on read cannot be
   the reason the placement of that same order was rejected.
2. **The outer MCP envelope is not the problem.** Reads and writes share the
   adapter's `_data()` helper, which handles `isError`, `structuredContent`,
   and the `{data, guide}` unwrap. Reads work, so that layer works.
3. **Therefore the failure is confined to the place-only envelope layer** —
   the code between `_data()` and the order object, which no read traverses.

That layer has exactly three failure modes, confirmed by direct probe:

| Payload | Result |
| --- | --- |
| `{"data": {"order": <valid order>}, "guide": …}` | accepted |
| `{"data": {"order": null}, "guide": …}` | **rejected** |
| `{"data": {"order": …, <any other key>}, "guide": …}` | **rejected** |
| `{"data": {}, "guide": …}` | **rejected** |

## Root cause — narrowest supported classification

`PLACE_RESPONSE_NESTING_MISMATCH` at the `data` level, with two candidates
that existing material cannot separate:

- **`data.order` is null.** The advertised outputSchema declares
  `"order": {"type": ["null", "object"]}`, so a null order is **schema-legal**.
  The parser rejects it by deliberate design: a submission response without an
  order object establishes nothing. If this is what the server returns, then
  every placement is a *valid broker result rejected by the parser* — failure
  mode 4 — and the `RESPONSE_MALFORMED` label is itself misleading.
- **`data` carries a key besides `order`.** `additionalProperties: false`
  forbids it, and the parser enforces exact keys. This would be a genuine
  envelope violation — failure mode 3.

Distinguishing the five modes:

1. transport ambiguity — **excluded**; the call returned a result.
2. explicit broker rejection — **excluded**; `isError` would have raised
   `BrokerRejectedError`, and no run recorded one.
3. successful tool response with an unexpected envelope — **possible**
   (extra key in `data`).
4. valid broker result rejected by the parser — **possible and most likely**
   (null `order`, which the schema permits).
5. genuinely malformed or empty response — **unlikely**; the same broker's
   order objects pass the strict contract on every read.

**Why no repair was made.** Modes 3 and 4 need different corrections, and no
recorded evidence separates them: `expected_vs_observed_json_types` is empty in
all three runs, so no structural fact about the place response was ever
captured. Guessing would mean loosening a write-response parser on an
assumption, which is exactly the wrong thing to do to a safety-critical
placement path.

## Old accepted schema — unchanged

```
{"data": {"order": {…20 required fields, optional reject_reason…}}, "guide": "…"}
```

Top level requires exactly `data` and `guide`. `data` requires exactly `order`.
`order` must be an object matching the advertised 20 required fields with
`additionalProperties: false`. Verified field-for-field and type-for-type
against the reviewed schema snapshot: the parser's required set, optional set,
required-text set, and nullable handling all match the advertisement exactly.

## Corrected accepted schema

**None.** The parser is byte-identical to before on this branch.

## Rejected schemas — unchanged, now pinned by test

Null order · missing `order` · extra key in `data` · missing `guide` · extra
top-level key · `data` not an object · `order` not an object · missing `id` ·
empty order object. All still rejected, for all three roles.

## Diagnostics added

A `place_diagnostics` evidence map, recorded at the orchestrator's single
placement choke point so all three roles are described identically. Keys are
role-prefixed; both keys and values are allowlisted.

| Fact (per role) | Values |
| --- | --- |
| `data_object_present` | true / false / unknown |
| `order_key_present` | true / false / unknown |
| `order_value_null` | true / false / unknown |
| `order_object_present` | true / false / unknown |
| `identifier_field_present` | true / false / unknown |
| `accepted_field_present` | true / false / unknown |
| `unexpected_data_keys_present` | true / false / unknown |
| `envelope_classification` | `order_object_returned` · `order_null_schema_legal` · `unexpected_data_keys` · `order_key_absent` · `data_not_an_object` · `envelope_absent` · `unknown` |

One run is now enough to separate the two candidates:
`order_null_schema_legal` proves candidate 4; `unexpected_data_keys` proves
candidate 3.

Also corrected: an ambiguous **exit** placement previously reported
`WRITE_RESULT_AMBIGUOUS`, which the orchestrator normalized to
`EXIT_PLACE_TRANSPORT_AMBIGUOUS` — labelling a response failure as a transport
failure. It now reports `EXIT_PLACE_RESPONSE_MALFORMED`, matching entry and
stop. This is a classification fix; no behaviour changed.

## Privacy constraints

The classifier reads structure only. It never returns a key name, a value, an
identifier, a price, or any part of the response body — only booleans and one
fixed classification. Unknown facts are dropped rather than raised on, so a
diagnostic can never fail a run that is already handling a write ambiguity.

During development the evidence sanitizer rejected the first name chosen for
one fact, `order_identifier_field_present`, because it contains the prohibited
substring `order_id`. The guard was left intact and the fact was renamed to
`identifier_field_present`. A diagnostic is not a reason to weaken a privacy
guard.

## Why reconciliation remains

Bounded read-only reconciliation stays in place for entry, stop, and exit,
untouched. It is not a workaround for this defect — it is the mechanism that
kept three live runs safe while the defect was present, and it must survive any
eventual parser correction. A parser can only ever establish *submission*;
only a subsequent read establishes state.

## Criteria for another live run

1. This branch reviewed and merged, CI green.
2. A completely fresh wrapper pinned to the new merged main, and a fresh,
   never-used Run ID.
3. Both prior stops and positions confirmed resolved in the UI beforehand.
4. Accept in advance that placements will still be classified ambiguous and
   recovered by reconciliation — **this branch does not fix that**.
5. After the run, read `place_diagnostics.entry_envelope_classification`. That
   single value selects the repair:
   - `order_null_schema_legal` → the parser must decide what a schema-legal
     null order means, rather than calling it malformed;
   - `unexpected_data_keys` → the envelope's exact-key rule needs the real
     advertised key set;
   - `order_object_returned` → the rejection is inside the order object after
     all, and the read/place symmetry argument above needs revisiting.

## Status of the prepared wrapper

`checkpoint_7c_live_run_20260731_03.ps1` was **not executed**, and
`cp7c-live-aapl-one-share-20260731-03` has no evidence directory and no
wrapper-used marker. If this branch merges, that wrapper becomes **obsolete**
because it pins `6b4439907e726d262349da660d590a039ad5a71a`. It must be
preserved as evidence, never edited to point at a new SHA, and never executed.
A future run needs a completely fresh wrapper and Run ID.
