# Cache Baseline Digests

**Label: `PRE_PHASE2_LOCAL_BASELINE_ONLY`.**

These digests fix the **current local state** of each cache tree so that any mutation during a later Phase-2B run is detectable. They are **not** evidence of historical originality, vendor integrity, or that these bytes are the bytes the original runs consumed. No committed hash exists anywhere in the repository for any raw cache file, so no such claim is available.

## Digest algorithm

Deterministic and reproducible without disclosing contents:

1. Walk the tree, collecting every regular file.
2. For each file, form one line: `SHA256(relative_path_utf8) : byte_size : SHA256(file_bytes)`.
3. Sort the lines lexicographically.
4. Join with `\n` and take SHA-256 of the result.

Relative paths are hashed rather than recorded, so the digest is reproducible by anyone holding the same tree while committing no filename, symbol, timestamp, or absolute path. **File bytes were read and hashed as opaque bytes only — never deserialized, decoded, or semantically parsed.**

## Baselines

| Tree (repository-relative root) | Files | Bytes | Aggregate digest |
|---|---:|---:|---|
| `artifacts/research-profit-protection-exit/period-checkpoint` | 6 | 728,555 | `81363c06ebbdb8cdf0dae0bece68b6f93423da255aed9d38a1d0720635089df4` |
| `artifacts/research-later-first-entry-recovery/period-checkpoint` | 6 | 27,738,279 | `bd897e32d31992aea3606c9d8d0d437f4d7a4af4bddf1f9c0ae349ae360b910f` |
| `artifacts/research-capital-target-strategy-family/daily-bar-cache/sip` | 20 | 3,401,202 | `c9467f7f87866dd80287c17ae74e8d0f278e094de64485c663b69e0f02efe568` |
| `artifacts/research-universe-expansion/bar-cache` | 3,141 | 3,394,132,549 | `96e4a738d667f9d6b78d8c2e59edb520ee6bba6ccdd581c78af1af58d5176a97` |
| `artifacts/research-cross-sectional-momentum/daily-bar-cache` | 942 | 177,025,917 | `0698d51e8f3c1d599e5d85884fa192b2abf8bfde1cd8495574404681626ebf8d` |
| `artifacts/research-cross-sectional-momentum/sec-nport-cache` | 29 | 25,130,891 | `889fb5dc0a8b5d5f6ecf6693cd48bb0beae62cd38524fa73c308f44cf546c4fc` |
| `artifacts/research-cross-sectional-momentum/openfigi-cache` | 5,243 | 786,008 | `b2ad68c940fe3854bca92918290a94211a056cee06887e1e79a0a9020dd5ef86` |
| `artifacts/research-capital-target-strategy-family/results` | 2 | 335,904 | `983c1c5dc64cdf3373c8f5d8c9d584bcbea25c66a1001941f43db0cb9f3ef4da` |
| `artifacts/research-practical-core-satellite-portfolio/results` | 2 | 490,532 | `2af0c58b0784fcbc22d968326316264598a27ce4cfa339e111461298de7be791` |
| `artifacts/research-cross-sectional-momentum/results` | 1 | 34,124 | `c8ae470acbb5597b2aa51e9dee131090b08c8400a2ca202e1c4f5636fdc63c54` |
| `artifacts/research-momentum-risk-control/results` | 1 | 28,715 | `95e28ffc31f005ae7b704e6fa805bb86027f8adc86d1ab941be57977f2c59cc6` |
| `artifacts/research-untouched-2019-2020-validation/results` | 1 | 22,255 | `ed6f8668ceea36599133d4b5f1041c6af1957ab69988b54a27d5e4241294f489` |

**Totals: 9,394 files, 3,629,155,631 bytes (~3.4 GB) across 12 trees.**

## Post-hash mutation check

Immediately after hashing, every tree's file count and aggregate byte size was re-measured and compared. **All 12 trees unchanged. No cache mutation occurred.** Hashing is read-only by construction, and this confirms it empirically rather than assuming it.

## Scale note relevant to Phase 2B

The M49 ORB minute cache is **3.39 GB across 3,141 files** — larger than every other tree combined and roughly 19× the M52 daily-bar tree. Any Phase-2B step consuming it should expect materially longer runtime and disk pressure, which is one reason the recommended execution order defers M49.

## What these digests do and do not support

**Do:** detect any change to a cache tree between now and a later Phase-2B run; provide a reproducible fingerprint anyone with the same tree can recompute; establish a tamper-evident boundary around Phase 2B.

**Do not:** prove these bytes are the bytes the original historical runs consumed; prove vendor-side integrity; substitute for the absent committed raw-cache hashes; or upgrade any P2 `SUMMARY_ONLY` classification. Every one of those limits is unchanged from Phase 1.
