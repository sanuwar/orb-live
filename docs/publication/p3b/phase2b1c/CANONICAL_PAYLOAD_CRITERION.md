# Canonical Payload Criterion

## Frozen M52 procedure

Inputs are the expected historical M52 JSON and the preserved observed M52 JSON named in the package README.  Decode both as UTF-8 using Python 3.12.10 `json.loads` with an object-pairs hook that rejects duplicate object keys and a `parse_constant` handler that rejects `NaN`, `Infinity`, and `-Infinity`.  A decode failure, duplicate key, non-finite constant, or invalid UTF-8 is a comparison failure.

Before exclusions, recursively compare JSON values at every JSON Pointer.  Objects must have the same key sets; arrays are compared in index order and length; `null`, boolean, string, number, array, and object types must be identical.  Python `int` and `float` values must have identical types and values.  No coercion, string-to-number conversion, key reordering for comparison, or numerical tolerance is permitted.  JSON numeric lexemes are not a separate research datatype in this criterion; the Python runtime dependency is disclosed below.

Only the following JSON Pointers are removed from copies of both parsed documents, before comparison and hashing:

| JSON Pointer | Class | Individual justification |
|---|---|---|
| `/generated_at` | volatile generation timestamp | The historical source emits execution-time metadata; it cannot be equal across separately timed materializations and is not an M52 research/numerical datum. |
| `/generation_source_file_sha256/scripts~1research_cross_sectional_momentum.py` | top-level source provenance | It records raw checked-out bytes, which differ solely because the observed checkout materialized LF Git content as CRLF. |
| `/stage0/item10_generation_provenance/generation_source_file_sha256/scripts~1research_cross_sectional_momentum.py` | repeated Stage-0 source provenance | This is the same raw-byte provenance fact repeated inside Stage 0; it is separately enumerated rather than wildcarded. |

No wildcard exclusions are allowed.  The three paths were frozen after the observed mismatch and cannot be extended to accommodate a later difference.  Every excluded field remains mandatory in the separate provenance table below.

## Deterministic canonical-payload hash

For each post-exclusion parsed value, serialize with exactly:

```python
json.dumps(value, ensure_ascii=False, sort_keys=True,
           separators=(",", ":"), allow_nan=False).encode("utf-8")
```

Then compute SHA-256 over those bytes.  This is Python standard-library serialization, not RFC 8785/JCS: it depends on Python's JSON decoder/encoder behavior and runtime (recorded here as CPython 3.12.10 in the Phase 2B-1 environment).  Sorting object keys is only for the hash; array order is never sorted.  The hash is valid only with this exact procedure and exclusion map.

## Mandatory excluded-field comparison table

| Pointer | Expected | Observed | Result |
|---|---|---|---|
| `/generated_at` | `2026-08-23T03:34:17.371724+00:00` | `2026-08-26T21:21:06.726131+00:00` | different; disclosed timestamp metadata |
| `/generation_source_file_sha256/scripts~1research_cross_sectional_momentum.py` | `411391271c0e8adf5f866233fe9f9ebc9515eddd99adb5dd8bb31cee651dff66` | `ee5276b67febba75792faf4a7a72d05720145bce25ac9a512017d7f7b724a187` | different; raw LF versus CRLF materialization |
| `/stage0/item10_generation_provenance/generation_source_file_sha256/scripts~1research_cross_sectional_momentum.py` | `411391271c0e8adf5f866233fe9f9ebc9515eddd99adb5dd8bb31cee651dff66` | `ee5276b67febba75792faf4a7a72d05720145bce25ac9a512017d7f7b724a187` | different; repeated raw provenance |

All non-excluded values are research-payload values for this criterion and must match.  Provenance values are never silently dropped.
