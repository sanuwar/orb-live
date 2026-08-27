# Reproduction Outcome Taxonomy

This taxonomy is frozen by P3B Phase 2B-1C.  Labels are additive only where their stated predicates hold; none is a synonym for another.

| Label | Necessary condition | Allowed language | Prohibited language |
|---|---|---|---|
| `EXACT_BYTE_REPRODUCTION_PASS` | Complete artifact bytes and SHA-256 are identical. | “exact-byte reproduction passed.” | “semantic match” as a substitute for bytes. |
| `EXACT_BYTE_REPRODUCTION_FAIL` | Any artifact byte differs. | “exact-byte reproduction failed.” | “exact reproduction passed.” |
| `SEMANTIC_PAYLOAD_MATCH` | Parsed UTF-8 JSON values match under the frozen exclusion map, with type-sensitive scalar comparison, ordered arrays, and no numerical tolerance. | “semantic research-payload match under the frozen exclusion map.” | “byte-identical,” “exact reproduction,” or an unqualified “reproduced.” |
| `SOURCE_CONTENT_EQUIVALENT_WITH_MATERIALIZATION_VARIANCE` | Historical Git-object and checkout content agree after the declared normalization; raw hashes remain reported. | “source content is equivalent after LF normalization, with checkout materialization variance.” | “exact-byte source reproduction.” |
| `REPRODUCED_WITH_METADATA_VARIANCE` | Byte reproduction failed; semantic payload matches; normalized source content is equivalent; frozen parameters match; cache integrity passes; and every excluded metadata field is disclosed. | “reproduced with disclosed metadata variance.” | “exact reproduction.” |

`REPRODUCED_WITH_METADATA_VARIANCE` is an optional composite description, never a replacement for the underlying `EXACT_BYTE_REPRODUCTION_FAIL`.  It is unavailable if any required predicate is unknown or fails.

## Post-hoc discipline

M52 was already observed to differ before this criterion was written.  Its use here is therefore `RETROSPECTIVE_DIAGNOSTIC_APPLICATION`: it can establish a secondary semantic-payload finding but cannot turn the original byte-level failure into a confirmatory pass.  The criterion may be prospective for M53 only after this PR is independently reviewed and merged.  This Git history is a time-stamped, version-controlled frozen criterion, not formal preregistration.

## Allowed M52 sentence

“M52 had `EXACT_BYTE_REPRODUCTION_FAIL`; under the subsequently frozen criterion it had `RETROSPECTIVE_SEMANTIC_PAYLOAD_MATCH`, with disclosed timestamp and source-provenance variance.”
