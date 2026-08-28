# M53 Canonical Payload Criterion

## Frozen comparison

The expected artifact is `artifacts/research-momentum-risk-control/results/m53_result.json`.  Its independently recomputed raw SHA-256 is `a3d3ebbd62f45331115a74583f7f8c378e9f2e786fb693b12865fff8860122c1`, and its byte length is `28715`.

Decode both documents as UTF-8 with a JSON parser that rejects invalid UTF-8, duplicate object keys, `NaN`, `Infinity`, and `-Infinity`.  Recursively compare the resulting values after the exact exclusions below.  Objects must have identical key sets; arrays must have identical lengths and index-order values; scalar types must be identical (`null`, boolean, string, integer, and number are distinct); and number values must be exactly equal.  There is no coercion, numeric tolerance, wildcard, or key-order-based exception.

The sole M53 JSON Pointer exclusion is:

| Pointer | Historical value | Source basis and justification |
|---|---|---|
| `/generated_at` | `2026-08-23T05:03:25.525917+00:00` | `generated_at = datetime.now(timezone.utc)` at historical wrapper line 281 and `generated_at.isoformat()` at line 283.  It is wall-clock metadata that varies independently of the research result. |

The pointer exists in the expected artifact.  No other pointer is excluded.  In particular, no numerical result, classification, parity field, source hash, Git SHA, input hash, cache field, or M52 copied metadata is excluded.  `/baseline_parity/m52_generated_at` is read from the M52 artifact at line 175; using the original historical artifact freezes it.  `/generation_git_head_sha`, source hashes, and `generation_working_tree_clean` are controllable source-provenance fields, not exclusions.  A later non-excluded difference is `PROSPECTIVE_SEMANTIC_PAYLOAD_FAIL`; any desired additional exclusion requires a new reviewed criterion and disclosure as post-result.

## Canonical serialization and frozen result

Remove `/generated_at` from a copy of the parsed object.  Serialize the remaining value exactly with Python standard-library semantics:

```python
json.dumps(value, ensure_ascii=False, sort_keys=True,
           separators=(",", ":"), allow_nan=False).encode("utf-8")
```

Then SHA-256 the resulting bytes.  This is deliberately **not** called RFC 8785/JCS: it is a precisely specified Python JSON parser/serializer procedure.  Sorting applies only to object serialization; arrays remain ordered.

Expected canonical payload SHA-256: `9747dec356d11d8cdbcb30d3ac3838fe962dad85674c0c4c635029c094b8a412`.

Expected canonical byte length: `22055`.

The later evidence package must record the raw hashes/sizes, parse result, exclusion value from both sides, canonical hashes/sizes, and every first differing non-excluded JSON Pointer (with type and value) if comparison fails.  Exact-byte and semantic outcomes are always reported separately.
