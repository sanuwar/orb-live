# Phase 1 Integrity Findings

## What Phase 1 confirmed

1. **All nine P3A-relevant cache directories exist locally**, plus the M50 stage-0 cache. No expected cache directory is missing.
2. **Five result artifacts match their committed hashes exactly** (M48, M49, M50, M52, M53). Each comparison used a manifest field that names the artifact it hashes, so each is a genuine same-object byte-level comparison. What that proves is narrow and exact: **the local result artifact is byte-identical to the artifact named and hashed by the committed manifest.** See the limits below.
3. **No cache files are tracked in the current repository tree.** `git ls-files artifacts/` returns zero files at the evidence cutoff.
4. **M53's dependence on the M52 caches is real and by design**, matching P3A's description of M53 operating on the immutable M52 caches.

## What Phase 1 did **not** confirm — and cannot

**No raw cache file has a directly comparable committed hash.** This is the central finding.

The committed manifests hash three kinds of thing: a generated results JSON, the *source files* that generated it, and internal derived digests such as `master_calendar_sha256`, `per_symbol_series_sha256`, and `ledger_hashes`. None of those is the SHA-256 of a raw bar-cache file. The derived digests are computed over in-memory structures whose exact serialization is defined by generation code, so comparing them to a file hash would be comparing different objects — precisely the error this milestone was told to avoid, and it was not committed.

The consequence is stated plainly: **for every one of the nine raw sub-caches, integrity remains unresolved pending Phase 2 regeneration.** Existence is established. Integrity is not.

### Direct hash match versus metadata-only, side by side

| Evidence class | Items | What it proves | What it does not prove |
|---|---|---|---|
| **Direct hash match** | 5 result artifacts (M48, M49, M50, M52, M53) | The local result artifact is byte-identical to the artifact named and hashed by the committed manifest | Nothing about the raw inputs; nothing about which code version generated it; and **not** that every headline figure in the report was derived from that file |
| **Metadata only** | M46 (`universe-source/provenance_manifest.json`), M48 and M49 (`results/provenance_manifest.json`) | A sidecar describes the artifact | That the described bytes are unmodified |
| **Unverified** | All nine raw sub-caches; M43, M44, M45, M47 entirely | The files exist, with the recorded counts and sizes | Anything at all about their contents |

### What the five hash matches do not establish

Because Phase 1 deliberately did not parse the result artifacts, the match alone does **not** independently establish any of:

- that every headline figure in the corresponding report was derived from that file — verifying that would require reading the file's contents and comparing them to the report, which Phase 1 did not do;
- that the file was generated from intact raw inputs — the inputs have no comparable committed hash at all;
- that the generating code was the expected version — for M48–M53 a `generation_source_file_sha256` map exists and could support that check, but running it was left to Phase 2.

An earlier draft of this document asserted the first of these. That assertion exceeded the evidence and is withdrawn.

## Source-provenance taxonomy for M43–M47

An earlier draft of this document said M43–M47 have "no committed hash of any kind" and that Phase 2 could not establish what source version it runs. Both statements were too strong and are corrected here.

**What is actually absent:** M43, M44, M45, M46, and M47 have **no committed artifact-hash manifest**, and therefore no explicit `generation_source_file_sha256` map tying a generated result artifact to each research-wrapper source file. Manifests exist only for M48, M49, M50, M52, and M53.

**What is actually present:**

- **Committed Git-tree provenance** at each milestone's own result commit. The full source tree as of that commit is recoverable, and blob hashes for every research wrapper and CLI can be read from it.
- **The trusted implementation digest** `f79587b9e58da0465973f8ac08ce26f7cb648a52952df6407c3c0a57abd4c53a`, carried in all five reports (twice each in M43, M44, M45, M47; once in M46). That digest covers the **pinned implementation closure** — not necessarily every research wrapper or CLI used to produce a given milestone's results.

**What Phase 2 could therefore establish for M43–M47:** running from an exact milestone Git commit or worktree and recording the source blob hashes it used establishes precisely what code the rerun executed.

**What Phase 2 could not establish for M43–M47:** that the *original historical run* used precisely that complete research-source tree. In short — **indirect Git-tree provenance exists, but explicit output-to-generation-source linkage is absent.** For M48–M53 that linkage is explicit, which is the real difference between the two groups.

**M46 specifically:** it has a local, gitignored stage sidecar at `artifacts/research-small-midcap-momentum-lane/universe-source/provenance_manifest.json`. That is a *local stage manifest*, not a repository-committed artifact-hash manifest, and it is not evidence of the same kind — it is itself untracked and unverifiable against anything committed.

## Serialization risk for M43–M47

The M43, M44, M45, and M47 period checkpoints are Python pickles (six `.pkl` files each). Two consequences:

1. **Python pickle deserialization is code-execution-capable and may import or invoke referenced globals and classes; it is not passive data inspection.** That is why Phase 1 did not open them, and why Phase 2 must treat doing so as authorized code execution carrying security and compatibility risk, rather than as file reading.
2. **Pickle compatibility is version-coupled.** A pickle written by one version of a class hierarchy may fail to load, or load differently, under another. With no explicit output-to-generation-source linkage for these milestones, a Phase-2 load failure would be ambiguous between "cache is damaged" and "cache is fine but the code moved on" — though running from the milestone's own Git commit would narrow that considerably.

The M48–M53 caches are JSON, which has neither problem.

## Timestamps

Filesystem timestamps were observed as diagnostic metadata and are deliberately **not** reported as integrity evidence and not reproduced in this package. A plausible mtime proves nothing about content, and recording them alongside integrity statuses would invite exactly the conflation this milestone was instructed to avoid.

## Licensing and reproducibility implications

Reconciled against `docs/publication/p2/DATA_LICENSE_REDISTRIBUTION_MATRIX.md`. **The caches are not uniformly Alpaca-derived**, and an earlier draft of this document wrongly treated them as though they were. The M52/M53 tree alone spans three sources with three different statuses.

| Source | Where it appears in the caches | Redistribution status (per the P2 matrix) |
|---|---|---|
| Alpaca SIP / IEX raw bars | all ORB period checkpoints and minute caches; M46 daily bars; M48 `daily-bar-cache/sip/`; M52 `daily-bar-cache/` | **Not permitted** absent separate written permission or an applicable governing agreement |
| Alpaca-derived aggregate results | the five generated result artifacts | **Unresolved**, pending human/legal review |
| SEC EDGAR (N-PORT-P) raw and derived | M52 `sec-nport-cache/` (29 files, 25 MB); M46 `universe-source/` | **Unresolved**, pending human/legal review — free public access and a stated rate limit are access facts, not a redistribution right |
| OpenFIGI symbology / mapping | M52 `openfigi-cache/` (5,243 files, 8.5 MB) | **Permitted** under the documented MIT licence and the official FAQ's explicit no-restriction statement |

Further implications:

1. **Local presence is not external reproducibility.** An external reviewer has no route to these caches; the 204 MB M52 tree and the 3.2 GB ORB minute cache exist on one machine. Any reproducibility claim must rest on the synthetic-fixture path in P2's reproducibility blueprint.
2. **The generated results are a *candidate* publication layer, not automatically a shareable one.** Their own redistribution status is unresolved (row 2 above). They are the most promising layer because they are small, aggregate, and hash-verified — but "promising" is not "cleared", and only a human/legal reviewer can clear them.
3. **Factor data remains absent.** No cache contains Fama–French or UMD series; P2's `NOT_AVAILABLE` classification is unchanged.
4. **No archive of raw cache data was created**, and none may be.

## Explicit non-upgrade statement

**No P2 `SUMMARY_ONLY` input is upgraded to `AVAILABLE_COMMITTED` by any finding here.** P2 anticipated this exact situation — a cache's presence on a prior session's disk is not verified evidence — and Phase 1's result is that the anticipation was correct: presence is confirmed, integrity is not, and the classification stands.
