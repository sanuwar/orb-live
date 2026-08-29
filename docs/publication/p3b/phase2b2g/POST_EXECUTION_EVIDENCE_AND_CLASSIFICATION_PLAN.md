# Post-Execution Evidence and Classification Plan

This plan is frozen **before** any regenerated M53 artifact exists or is observed. That ordering is what makes the criterion prospective for reproducibility-evaluation purposes. It is **not** formal preregistration, and a Git commit is not a preregistration record.

## Required evidence package

A later authorized run must produce, and retain, all of the following:

**Preflight.** The gate's evidence record from the pre-run pass, including the decision, all finding counts, the manifest profile, and the supplied-input path digests.

**Source and materialization.** The clone-local Git EOL and attributes configuration with `--show-origin`, the `git check-attr --all` result over all 30 paths, the per-file hash/size result for all 30, the verification of each displayed Git blob against the immutable object, and empty `git diff --name-only` / `git status --porcelain` over those paths.

**Parameters.** The reconstructed 49-line byte stream, its length, and its SHA-256, plus line-level source verification at the historical commit and an explicit assertion that no seed exists in the source or command.

**Input handoff.** Source and destination path digests, the copy command, pre-copy, post-copy, and post-placement hash and size for the M52 artifact, and its strict UTF-8 JSON parse result.

**Caches.** Original pre-copy, scratch post-copy, scratch immediately-pre-command, and both scratch and original immediately-post-command counts, byte totals, and aggregate digests for all three trees.

**Guard.** The trusted external pre-launch hashes of both guard files computed outside the guarded interpreter; the complete `m53-network-guard.jsonl` evidence log; the `verify_guard_run()` result; confirmation that every record carries the two externally verified guard digests; and confirmation of an `activation` record, a `guard_ready` record, and a terminal `normal_interpreter_exit` record with zero `denied_*` and zero `guard_failure` records.

**Environment.** OS and build; Python executable content hash and version; Git executable content hash; `requirements.txt` hash; relevant installed package versions; locale and timezone; the explicit child-environment allowlist by **name only**; and the exact command vector.

**Output.** The complete regenerated artifact's raw SHA-256 and byte length; the parse result; the excluded `/generated_at` value from both sides; the canonical payload SHA-256 and byte length from both sides; and, on failure, every first differing non-excluded JSON Pointer with its type and value from both sides.

## Classification

Three outcomes are reported **independently**. A semantic match never conceals an exact-byte failure.

### 1. Exact-byte reproduction

| Label | Condition |
|---|---|
| `EXACT_BYTE_REPRODUCTION_PASS` | The regenerated artifact's raw bytes hash to `a3d3ebbd62f45331115a74583f7f8c378e9f2e786fb693b12865fff8860122c1` and its length is 28,715. |
| `EXACT_BYTE_REPRODUCTION_FAIL` | Anything else. |

### 2. Prospective semantic payload

Decode both documents as UTF-8 with a parser that rejects invalid UTF-8, duplicate keys, `NaN`, `Infinity`, and `-Infinity`. Remove `/generated_at` — the sole exclusion — from a copy of each. Serialize with `json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")` and SHA-256 the result. This is a precisely specified Python procedure and is deliberately **not** called RFC 8785/JCS.

| Label | Condition |
|---|---|
| `PROSPECTIVE_SEMANTIC_PAYLOAD_MATCH` | The canonical payload hashes to `9747dec356d11d8cdbcb30d3ac3838fe962dad85674c0c4c635029c094b8a412` with length 22,055. |
| `PROSPECTIVE_SEMANTIC_PAYLOAD_FAIL` | Any non-excluded difference. |

Comparison is exact: identical key sets, identical array lengths and index order, identical scalar types (`null`, boolean, string, integer, number all distinct), exact numeric equality. No coercion, tolerance, wildcard, or key-order exception.

### 3. Supporting status

Source-provenance status, frozen-parameter integrity status, cache-integrity status, guard-run status, and environment-provenance limitations are each reported separately, and none of them may be used to upgrade either label above.

## Interpretation rules

- **Both labels are always reported.** Reporting only the semantic one is a protocol violation.
- **A failure is preserved as evidence and never retried.** No adjustment to parameters, dependencies, environment, source, or criterion is permitted after a regenerated result has been seen, absent new authorization and a new independently reviewed criterion disclosed as post-result.
- **No new exclusion may be proposed after an output is observed.** Anyone proposing one triggers a stop.
- **Neither label speaks to the strategy.** A byte-exact reproduction would establish that this repository's recorded workflow regenerates a recorded artifact. It would say nothing about whether the strategy is profitable, deployable, statistically sound, or fit for any purpose.
- **A mismatch must not be attributed to caches by default.** The historical CLIs record execution-time metadata, and the historical environment is unknown; attributing a difference to any single cause requires separately authorized diagnosis.
