# Cache Inventory — Phase 1

**Evidence cutoff:** main at `eef11e7b368918c35ffb3ecb4b7bca2d2413cfb2`. All paths are repository-relative. All cache directories below are gitignored, and **no cache files are tracked in the current repository tree** (`git ls-files artifacts/` returns zero files at the evidence cutoff). That is a statement about the current tree only; no full Git-history audit was performed.

## Result-artifact hash comparisons (the only genuine direct comparisons available)

Five committed manifest fields name the exact artifact they hash, so a byte-level comparison is meaningful. All five match.

| Milestone | Manifest field | Artifact compared | Result |
|---|---|---|---|
| M48 | `study_results_json_sha256` | `artifacts/research-capital-target-strategy-family/results/study_results.json` | **MATCH** |
| M49 | `study_results_json_sha256` | `artifacts/research-practical-core-satellite-portfolio/results/study_results.json` | **MATCH** |
| M50 | `stage0_result_sha256` | `artifacts/research-untouched-2019-2020-validation/results/stage0_result.json` | **MATCH** |
| M52 | `result_sha256` | `artifacts/research-cross-sectional-momentum/results/m52_result.json` | **MATCH** |
| M53 | `result_sha256` | `artifacts/research-momentum-risk-control/results/m53_result.json` | **MATCH** |

**These five matches cover generated results artifacts only.** Each establishes exactly one thing: the local result artifact is byte-identical to the artifact named and hashed by the committed manifest. Because Phase 1 did not parse those artifacts, a match does **not** independently establish that every headline figure in the corresponding report was derived from that file, that the file was generated from intact raw inputs, or that the generating code was the expected version. They establish **nothing** about the raw bar caches that produced those results — no committed hash anywhere in the repository names a raw cache file, so no equivalent comparison is possible for raw inputs. This distinction is the substance of `PHASE1_INTEGRITY_FINDINGS.md`.

## Milestone cache inventory

| Milestone | Expected cache location (repo-relative) | Presence | Files | Aggregate size | Sidecar / metadata | Directly comparable committed hash | Status |
|---|---|---:|---:|---:|---|---|---|
| M43 | `artifacts/research-profit-protection-exit/` | present | 9 | 888 KB | none | **none** (no committed artifact-hash manifest) | `PRESENT_UNVERIFIED` |
| M44 | `artifacts/research-fallback-candidate-recovery/` | present | 9 | 22 MB | none | **none** (no committed artifact-hash manifest) | `PRESENT_UNVERIFIED` |
| M45 | `artifacts/research-later-first-entry-recovery/` | present | 9 | 27 MB | none | **none** (no committed artifact-hash manifest) | `PRESENT_UNVERIFIED` |
| M46 | `artifacts/research-small-midcap-momentum-lane/` | present | 3,282 | 29 MB | local gitignored stage sidecar `universe-source/provenance_manifest.json` | **none** (no committed artifact-hash manifest) | `PRESENT_METADATA_ONLY` |
| M47 | `artifacts/research-sequential-second-trade/` | present | 8 | 136 KB | none | **none** (no committed artifact-hash manifest) | `PRESENT_UNVERIFIED` |
| M48 | `artifacts/research-capital-target-strategy-family/` | present | 42 | 6.9 MB | `results/provenance_manifest.json` | results artifact only | `PRESENT_DIRECT_HASH_MATCH` (results) |
| M49 | `artifacts/research-practical-core-satellite-portfolio/` | present | 2 | 484 KB | `results/provenance_manifest.json` | results artifact only | `PARTIAL` — see note below |
| M52 | `artifacts/research-cross-sectional-momentum/` | present | 6,215 | 204 MB | none beyond the committed manifest | results artifact only | `PRESENT_DIRECT_HASH_MATCH` (results) |
| M53 | `artifacts/research-momentum-risk-control/` | present | 1 | 32 KB | none | results artifact only | `PARTIAL` — see note below |
| M50 | `artifacts/research-untouched-2019-2020-validation/` | present | — | — | committed manifest | stage-0 result artifact only | `NOT_APPLICABLE` (no economics were ever computed) |

**No expected cache directory is `MISSING`.**

## Raw input sub-caches

These are the inputs a Phase-2 regeneration would actually consume. None has a directly comparable committed hash.

| Milestone(s) | Sub-cache | Files | Size | Status |
|---|---|---:|---:|---|
| M43 | `research-profit-protection-exit/period-checkpoint/` | 6 | 724 KB | `PRESENT_UNVERIFIED` |
| M44 | `research-fallback-candidate-recovery/period-checkpoint/` | 6 | 22 MB | `PRESENT_UNVERIFIED` |
| M45 | `research-later-first-entry-recovery/period-checkpoint/` | 6 | 27 MB | `PRESENT_UNVERIFIED` |
| M46 | `research-small-midcap-momentum-lane/daily-bars-cache/` | 2,649 | 22 MB | `PRESENT_UNVERIFIED` |
| M47 | `research-sequential-second-trade/period-checkpoint/` | 6 | 124 KB | `PRESENT_UNVERIFIED` |
| M48, M49 | `research-capital-target-strategy-family/daily-bar-cache/` | 40 | 6.5 MB | `PRESENT_UNVERIFIED` |
| M52, M53 | `research-cross-sectional-momentum/daily-bar-cache/` | 942 | 172 MB | `PRESENT_UNVERIFIED` |
| M52, M53 | `research-cross-sectional-momentum/sec-nport-cache/` | 29 | 25 MB | `PRESENT_UNVERIFIED` |
| M52, M53 | `research-cross-sectional-momentum/openfigi-cache/` | 5,243 | 8.5 MB | `PRESENT_UNVERIFIED` |

## Notes on the two `PARTIAL` rows

**M49** holds only `results/study_results.json` and `results/provenance_manifest.json`. It has **no bar cache of its own**, and depends on three trees in other directories. Those locations were traced statically from committed source and all three are present; the evidence table is in `CACHE_TO_TEST_INSTANCE_MAP.md`. This is a structural fact about how M49 was built, not evidence of damage.

**M53** holds only `results/m53_result.json`. This is expected and documented: M53 was built to reproduce M52 exactly before varying one parameter, and P3A records that it operates on the *immutable M52 caches*. Its `PARTIAL` status reflects a deliberate design dependency, not a defect.

## File-type distribution

Recorded because it bears directly on Phase-2 feasibility.

| Milestone(s) | Extensions observed |
|---|---|
| M43, M44, M45 | 6 × `.pkl`, 1 × `.csv`, 1 × `.json`, 1 × `.log` each |
| M46 | 3,259 × `.json`, 12 × `.log`, 6 × `.csv`, 2 × `.pkl`, 2 × `.xml`, 1 × `.jsonl` |
| M47 | 6 × `.pkl`, 2 × `.log` |
| M48 | 42 × `.json` |
| M49 | 2 × `.json` |
| M52 | 6,215 × `.json` |
| M53 | 1 × `.json` |

The M43–M47 period checkpoints are **Python pickles**. Extensions were read from filenames; no pickle was opened. Python pickle deserialization is code-execution-capable and may import or invoke referenced globals and classes, so it is not passive data inspection and was out of Phase-1 scope. The consequences for Phase 2 are set out in `PHASE2_AUTHORIZATION_REQUIREMENTS.md`.

## Reconciliation with the P2 input inventory

Every row above corresponds to a `SUMMARY_ONLY` classification in `docs/publication/p2/STATISTICAL_INPUT_INVENTORY.md`. **Nothing in this inventory changes any of those classifications.** Establishing that a cache directory exists is precisely the "cache's mere presence is not verified evidence" case P2 already anticipated and refused to treat as availability.
