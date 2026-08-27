# M52 Retrospective Classification

## Status

Application status: `RETROSPECTIVE_DIAGNOSTIC_APPLICATION`.

The original Phase 2B-1 result remains `EXACT_BYTE_REPRODUCTION_FAIL`, because the expected artifact SHA-256 was `590672f029a519b665b856106d0b35d701b464aa3f12b5a0b1978f18b18ac8f9` and the preserved observed artifact SHA-256 was `a5c604ab63e145ef2e8878677aabcc2e4cb8694082bd8edf5de1c4e6360f9efd`.  The complete artifacts are each 34,124 bytes, but any changed byte is a failure under the original gate.

Applying the frozen procedure in `CANONICAL_PAYLOAD_CRITERION.md` mechanically gave type-sensitive equality after exactly its three exclusions.  Expected and observed canonical-payload SHA-256 are both:

```
d0962cd3b36aee45318ec2be0efd1369053a99444a487a14d0757dc4c9a4f41f
```

The canonical serialization length is 24,938 UTF-8 bytes for each document.  No non-excluded JSON Pointer differs.  The secondary finding is therefore `RETROSPECTIVE_SEMANTIC_PAYLOAD_MATCH`; no numerical research output differs.

## Source-provenance finding

Historical M52 commit `3d345ebca19c7159678aaad801cdb66299f9ba6b`, path `scripts/research_cross_sectional_momentum.py`, resolves to immutable Git blob `2f2f8b89cec05b520345931f4fb395c69cb90a79`.

| Measure | Historical Git object | Preserved M52 checkout |
|---|---:|---:|
| Raw SHA-256 | `411391271c0e8adf5f866233fe9f9ebc9515eddd99adb5dd8bb31cee651dff66` | `ee5276b67febba75792faf4a7a72d05720145bce25ac9a512017d7f7b724a187` |
| Bytes | 33,847 | 34,521 |
| LF count | 674 | 674 |
| CRLF count | 0 | 674 |
| Normalized SHA-256 | `411391271c0e8adf5f866233fe9f9ebc9515eddd99adb5dd8bb31cee651dff66` | `411391271c0e8adf5f866233fe9f9ebc9515eddd99adb5dd8bb31cee651dff66` |

Normalization is exactly bytewise replacement of each `0D 0A` sequence with `0A`; no other transformation is permitted.  Normalized bytes are equal.  This supports `SOURCE_CONTENT_EQUIVALENT_WITH_MATERIALIZATION_VARIANCE`, not byte-identical source reproduction.  The Phase 2B-1 environment record reports `core.autocrlf=true` and no root `.gitattributes`; no intentional source or frozen-parameter edit was recorded.

## Integrity predicates and limitations

Phase 2B-1 cache records report all three original and scratch cache aggregates unchanged before/after M52: daily-bar cache `0698d51e...`, SEC N-PORT cache `889fb5dc...`, and OpenFIGI cache `b2ad68c...`.  Frozen M52 parameters were not intentionally edited, and the semantic comparison found all non-provenance payload fields equal.  These facts satisfy the documented cache/frozen-parameter integrity predicate, subject to the historical environment gap below.

The strongest permitted statement is exactly: `EXACT_BYTE_REPRODUCTION_FAIL` plus `RETROSPECTIVE_SEMANTIC_PAYLOAD_MATCH`.  It is not a confirmatory pass, does not establish internal determinism from one completed invocation, and does not remove the need to disclose timestamp, checkout, environment, and serializer limitations.
