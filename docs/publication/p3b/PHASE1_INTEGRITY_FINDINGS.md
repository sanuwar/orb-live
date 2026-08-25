# Phase 1 Integrity Findings

## What Phase 1 confirmed

1. **All nine P3A-relevant cache directories exist locally**, plus the M50 stage-0 cache. No expected cache directory is missing.
2. **Five result artifacts match their committed hashes exactly** (M48, M49, M50, M52, M53). Each comparison used a manifest field that names the artifact it hashes, so each is a genuine same-object byte-level comparison.
3. **The caches are untracked and gitignored.** `git ls-files artifacts/` returns zero files, so no licensed market data has ever entered the repository.
4. **M53's dependence on the M52 caches is real and by design**, matching P3A's description of M53 operating on the immutable M52 caches.

## What Phase 1 did **not** confirm — and cannot

**No raw cache file has a directly comparable committed hash.** This is the central finding.

The committed manifests hash three kinds of thing: a generated results JSON, the *source files* that generated it, and internal derived digests such as `master_calendar_sha256`, `per_symbol_series_sha256`, and `ledger_hashes`. None of those is the SHA-256 of a raw bar-cache file. The derived digests are computed over in-memory structures whose exact serialization is defined by generation code, so comparing them to a file hash would be comparing different objects — precisely the error this milestone was told to avoid, and it was not committed.

The consequence is stated plainly: **for every one of the nine raw sub-caches, integrity remains unresolved pending Phase 2 regeneration.** Existence is established. Integrity is not.

### Direct hash match versus metadata-only, side by side

| Evidence class | Items | What it proves | What it does not prove |
|---|---|---|---|
| **Direct hash match** | 5 result artifacts (M48, M49, M50, M52, M53) | The committed headline figures correspond to intact local result files, byte for byte | Nothing about the raw inputs that produced them |
| **Metadata only** | M46 (`universe-source/provenance_manifest.json`), M48 and M49 (`results/provenance_manifest.json`) | A sidecar describes the artifact | That the described bytes are unmodified |
| **Unverified** | All nine raw sub-caches; M43, M44, M45, M47 entirely | The files exist, with the recorded counts and sizes | Anything at all about their contents |

## Manifest coverage limitation

**M43, M44, M45, M46, and M47 have no committed artifact-hash manifest.** Manifests exist only for M48, M49, M50, M52, and M53. For the five earlier milestones there is therefore no committed hash of *any* kind — not of the results, not of the sources, not of derived digests.

This has a specific consequence beyond integrity. The M48–M53 manifests each record `generation_source_file_sha256`, pinning the exact source files that produced the results; a Phase-2 regeneration can verify it is running the same code. **For M43–M47 no such pin exists**, so a Phase-2 regeneration of TI-01 through TI-04 could not establish that it was executing the same code that produced the committed figures. Those four instances are the weakest in the record on this axis, and it is a documentation gap from 2026, not something Phase 2 can repair.

## Serialization risk for M43–M47

The M43, M44, M45, and M47 period checkpoints are Python pickles (six `.pkl` files each). Two consequences:

1. **Unpickling executes code.** Opening these files is module execution by another name, which is why Phase 1 did not open them and why Phase 2 must treat doing so as a code-execution step requiring authorization, not as passive file reading.
2. **Pickle compatibility is version-coupled.** A pickle written by one version of a class hierarchy may fail to load, or silently load differently, under another. With no `generation_source_file_sha256` for these milestones, there is no committed record of which code version wrote them. A Phase-2 load failure here would be ambiguous between "cache is damaged" and "cache is fine but the code moved on."

The M48–M53 caches are JSON, which has neither problem.

## Timestamps

Filesystem timestamps were observed as diagnostic metadata and are deliberately **not** reported as integrity evidence and not reproduced in this package. A plausible mtime proves nothing about content, and recording them alongside integrity statuses would invite exactly the conflation this milestone was instructed to avoid.

## Licensing and reproducibility implications

1. **The caches cannot be redistributed.** They are Alpaca-derived, and P2's licensing matrix records Alpaca's official position that API data cannot be redistributed. Their local presence does not change that, and no archive of them was created.
2. **Local presence is not external reproducibility.** An external reviewer has no route to these caches. The 204 MB M52 tree and 22–27 MB ORB trees exist on one machine. Any reproducibility claim must rest on the synthetic-fixture path in P2's reproducibility blueprint, not on these directories.
3. **The generated results are the shareable layer, and they are intact.** The five direct hash matches are the strongest reproducibility evidence in the record: committed figures verifiably correspond to intact local artifacts. That is real, and it is also the limit.
4. **Factor data remains absent.** No cache contains Fama–French or UMD series; P2's `NOT_AVAILABLE` classification is unchanged.

## Explicit non-upgrade statement

**No P2 `SUMMARY_ONLY` input is upgraded to `AVAILABLE_COMMITTED` by any finding here.** P2 anticipated this exact situation — a cache's presence on a prior session's disk is not verified evidence — and Phase 1's result is that the anticipation was correct: presence is confirmed, integrity is not, and the classification stands.
