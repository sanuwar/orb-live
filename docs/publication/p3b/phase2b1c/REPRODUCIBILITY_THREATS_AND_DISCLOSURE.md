# Reproducibility Threats and Disclosure

## Required publication disclosure

M52's expected and preserved regenerated artifacts had different complete SHA-256 hashes.  The original result remains `EXACT_BYTE_REPRODUCTION_FAIL`.  A later, retrospectively designed and version-controlled criterion found a semantic payload match after excluding exactly one generation timestamp and two individually disclosed raw source-provenance values.  This is not formal preregistration and cannot be presented as a confirmatory byte-reproduction pass.

The source provenance difference is checkout materialization: immutable LF Git-object bytes became CRLF in the Windows worktree under `core.autocrlf=true` with no root `.gitattributes`.  Raw and normalized hashes must both be retained; normalized equality does not mean byte-identical source reproduction.

## Limitations

- Generation timestamps make byte identity sensitive to wall-clock time.  M53 also copies M52's `generated_at` into its output metadata.
- The Phase 2B-1 record does not recreate the historical OS image, Python build, dependency-resolution state, locale, filesystem behavior, or process state.  It records the rerun environment, including Python 3.12.10, but that is not an environment lock.
- Canonical payload hashes depend on the frozen Python standard-library JSON behavior.  The method is explicitly not RFC 8785/JCS.
- The criterion was developed after observing M52's mismatch.  It is diagnostic for M52, prospective only after review/merge, and must not be widened after a mismatch.
- Cache aggregate hashes passed in Phase 2B-1, but cache integrity is not proof of every possible execution-environment equality.
- M53 was not run in Phase 2B-1C.  Its static audit shows that regenerated M52 metadata variance would affect M53 metadata, not its numerical reader, but static inspection is not an execution result.

## Safe manuscript wording

Use: “The M52 regeneration did not reproduce the complete artifact byte-for-byte.  A retrospective, fixed JSON-payload comparison found no non-excluded research-payload difference; the disclosed differences were generation timestamp and raw source-provenance hashes explained by line-ending materialization.”

Do not use: “exactly reproduced,” “confirmed,” “preregistered,” “deterministic,” or any wording that omits the exclusions, retrospective timing, source materialization, and environment limitations.

## Independent-review correction (2026-08-27)

The M52 three-path exclusion map is not an M53 output gate. A separate M53-specific output criterion and exclusion map must be derived from static source and the historical expected M53 artifact before any regenerated M53 artifact is observed.
