# Frozen Input and Hash Manifest

The machine-readable form is [`M53_EXECUTION_GATE_MANIFEST.json`](M53_EXECUTION_GATE_MANIFEST.json) — Git-object bytes 17,777, Git-object SHA-256 `915610581a0c4dc8e78418de346021b6bb7b756e7f629c3ed7c6f42a664746cd` (LF; a `core.autocrlf=true` checkout materializes it with CRLF and a different digest), schema version `orb-live/m53-execution-gate/1`, profile `M53_PHASE2B2A`.

Every value below was **reconciled from a merged record, not re-derived**. The 30 source rows were parsed mechanically out of the merged Phase 2B-2A closure table rather than retyped, so the manifest cannot drift from it by transcription. The two guard digests are the only newly computed values in this package.

## A. Historical M53 implementation

| Item | Frozen value | Source |
|---|---|---|
| Historical commit | `4c43fe96ab80d739fd10a9bd6bfa4c1319f67865` | Phase 2B-2A |
| Source-closure size | 30 tracked Python files | Phase 2B-2A |
| CRLF-materialized files | exactly 2 | Phase 2B-2A |
| Entrypoint | `scripts/research_momentum_risk_control.py`, blob `cf049fa9cae08a4948b59f220f9f97c02621e7f4` | Phase 2B-2A |

The reviewed mixed LF/CRLF materialization policy is reused unchanged. The two CRLF rows are `scripts/research_cross_sectional_momentum.py` (34,521 bytes, `ee5276b67febba75792faf4a7a72d05720145bce25ac9a512017d7f7b724a187`) and `src/cross_sectional_momentum_research.py` (54,292 bytes, `cde0af38c8c3c91b5a2b33d51449b67a03129d0f8bcf59630056194cbd1ce352`); the other 28 must materialize to their raw Git-object bytes. This closure was neither recreated nor silently changed.

## B. Historical M52 handoff

| Item | Frozen value |
|---|---|
| Path | `artifacts/research-cross-sectional-momentum/results/m52_result.json` |
| SHA-256 | `590672f029a519b665b856106d0b35d701b464aa3f12b5a0b1978f18b18ac8f9` |
| Bytes | 34,124 |
| Classification | `EXACT_BYTE_REPRODUCTION_FAIL` + `RETROSPECTIVE_SEMANTIC_PAYLOAD_MATCH` |
| Regenerated artifact eligible | `false` |

The regenerated M52 artifact is permanently ineligible as the M53 handoff. Selecting the historical artifact preserves the copied `/baseline_parity/m52_generated_at` value and **neither erases nor reclassifies M52's exact-byte failure**.

## C. Frozen M53 output criterion

| Item | Frozen value |
|---|---|
| Complete expected artifact SHA-256 | `a3d3ebbd62f45331115a74583f7f8c378e9f2e786fb693b12865fff8860122c1` |
| Complete expected bytes | 28,715 |
| Sole canonical exclusion | `/generated_at` |
| Expected canonical payload SHA-256 | `9747dec356d11d8cdbcb30d3ac3838fe962dad85674c0c4c635029c094b8a412` |
| Expected canonical payload bytes | 22,055 |
| Output artifact path | `artifacts/research-momentum-risk-control/results/m53_result.json` |

Raw-byte equality and canonical-payload equality are **separate classifications**, declared as such in the manifest and enforced by the gate. Exact-byte reproduction is not weakened.

## D. Frozen parameters

| Item | Frozen value |
|---|---|
| Unique reviewed parameter records | 49 |
| Serialization bytes | 5,018 |
| Parameter-record digest | `7c2b2da4168333c198e44bb573655d995c129931ac2ae8b973a0985b77e4a0ad` |
| Command | `<approved-python-executable> -m scripts.research_momentum_risk_control` with no argument after the module |
| `--fetch-missing` | omitted; `store_true` default `false` |

Reconciled mechanically against the merged Phase 2B-2A record; all three values match. **The parameters are not claimed to be verified beyond that reconciliation succeeding** — the 49 lines were not re-derived from source in this phase.

## E. Network/process guard

Only the guard merged through PR #383 is used. Its two files are pinned here for the first time — Phase 2B-2N required a trusted external pre-launch hash check "against the values recorded in the reviewed guard commit", but recorded no such values anywhere. This package closes that gap.

| File | Git-object SHA-256 | Git-object bytes | Required materialization |
|---|---|---:|---|
| `scripts/publication/m53_network_guard/guard_core.py` | `df625207220042ed88405b6f57dcbe357571f9fbe58df3a782ceebc39e4d47b9` | 23,251 | `lf` |
| `scripts/publication/m53_network_guard/sitecustomize.py` | `34fdd6c487d7a53449deccf0be56d508b5dabf8e4503d50f9daad8b21f2313dc` | 620 | `lf` |

**LF materialization is required, and this matters on this machine.** The repository has `core.autocrlf=true` and no root `.gitattributes`, so an ordinary Windows checkout materializes these files with CRLF endings — 23,749 bytes / `ac0938d141c42bbdf89b7de058a845a79f483e01b06764d7f7d4ebea6fdca1e3` for `guard_core.py` and 638 bytes / `73f9620da57a41a689ed595ad62b6d374097930402ba1cd0e8a75933509c9392` for `sitecustomize.py`. Those CRLF digests are recorded here **only to explain the difference**; they are not accepted by the gate. Pinning the immutable Git-object bytes is what makes the launcher's external check well defined, since "the reviewed guard commit" contains LF bytes regardless of any checkout's EOL configuration. A future launcher must expose the guard directory with LF endings and hash what it actually exposes.

Additionally required and enforced:

- **Trusted external pre-launch verification** of both files, performed outside the guarded interpreter, before export of the expected digests. The guard cannot establish this about itself.
- **Executable content pins.** `ORB_M53_GUARD_PYTHON_SHA256` and `ORB_M53_GUARD_GIT_SHA256` hash executable *file bytes*, not path strings.
- **Full credential/proxy clearing**, all 20 names: `ALL_PROXY`, `ALPACA_API_KEY`, `ALPACA_API_KEY_ID`, `ALPACA_API_SECRET_KEY`, `ALPACA_BASE_URL`, `ALPACA_FEED`, `ALPACA_SECRET_KEY`, `APCA_API_BASE_URL`, `APCA_API_DATA_URL`, `APCA_API_KEY_ID`, `APCA_API_SECRET_KEY`, `FTP_PROXY`, `HTTPS_PROXY`, `HTTP_PROXY`, `NO_PROXY`, `all_proxy`, `ftp_proxy`, `http_proxy`, `https_proxy`, `no_proxy`.
- **`guard_ready` before target import**, and **`verify_guard_run()` after termination**. An `activation` record alone is not a ready state.
- **Any denied event, guard failure, missing terminal record, truncated evidence, integrity mismatch, or missing `guard_ready` is a failed run.**

This is a **process-scoped Python guard, not an operating-system sandbox**. Declaring otherwise in the manifest is itself a gate failure.

## F. Caches and isolation

Reused unchanged from the merged Phase 2A baseline and the Phase 2B-1 pre/post confirmation.

| Role | Repository-relative root | Files | Bytes | Aggregate SHA-256 |
|---|---|---:|---:|---|
| `daily_bar` | `artifacts/research-cross-sectional-momentum/daily-bar-cache` | 942 | 177,025,917 | `0698d51e8f3c1d599e5d85884fa192b2abf8bfde1cd8495574404681626ebf8d` |
| `openfigi` | `artifacts/research-cross-sectional-momentum/openfigi-cache` | 5,243 | 786,008 | `b2ad68c940fe3854bca92918290a94211a056cee06887e1e79a0a9020dd5ef86` |
| `sec_nport` | `artifacts/research-cross-sectional-momentum/sec-nport-cache` | 29 | 25,130,891 | `889fb5dc0a8b5d5f6ecf6693cd48bb0beae62cd38524fa73c308f44cf546c4fc` |

Aggregate algorithm: SHA-256 over LF-joined, lexicographically sorted lines `SHA256(relative_path_utf8):byte_size:SHA256(file_bytes)`, unspaced — the form Phase 2B-1 confirmed reproduces the frozen values, in preference to the spaced illustration in Phase 2A prose.

Future scratch-copy requirements, enforced by the gate when a scratch root is supplied:

1. Original caches are never passed, linked, or otherwise exposed to executable historical code. Only scratch copies are.
2. The scratch root must lie outside the repository working tree, and must neither contain nor be contained by any declared original cache root. The historical checkout must likewise lie outside the repository working tree.
3. Copies are ordinary binary copies. No symlink or junction; a symlinked file inside a scratch tree makes the tree unmeasurable and fails.
4. Pre-run integrity: hash the original trees, copy, then re-hash the scratch trees immediately before the command. Post-run integrity: re-hash both scratch and original trees immediately after. Any changed count, byte total, or aggregate digest is a stop without retry.
5. `fetch_missing` is `false`, so **no cache miss may trigger a fetch**. A cache miss, credential request, or network attempt is a stop without retry.

**Nothing in this workstream copied, linked, normalized, deserialized, or mutated any cache.** The preserved Phase 2B-1 diagnostic scratch evidence under the recorded temporary root was neither used nor modified.

## Evidence-configuration pins

| Item | Frozen value |
|---|---|
| Guard evidence log basename | `m53-network-guard.jsonl` |
| Command module | `scripts.research_momentum_risk_control` |
| Arguments after module | none |
| Evidence directory | absolute, existing, non-symlink; must not already contain the log |
| Output artifact | must not already exist in the checkout |
