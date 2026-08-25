# Publication P3B Phase 1 — Read-Only Cache Inventory and Integrity Preflight

**Evidence cutoff:** main at `eef11e7b368918c35ffb3ecb4b7bca2d2413cfb2`.
**Issue:** #372. **Scope:** `docs/publication/p3b/` only, Markdown only.

## Purpose

Determine which local, gitignored historical-research caches required by the P3A test instances still exist, and whether their metadata can be reconciled with committed manifests.

**This is inventory only.** It does not authorize deterministic regeneration, strategy simulation, statistical analysis, market-data or factor-data acquisition, or protocol freezing.

## What was actually done

Filesystem inspection: directory and file existence, relative paths, file counts, aggregate sizes, file extensions; reading committed hash manifests under `docs/research/`; reading cache sidecar manifests; and computing SHA-256 of five specific committed-and-named result artifacts for direct byte-level comparison.

## What was deliberately not done

No market, benchmark, factor, SEC, OpenFIGI, or broker data was fetched. No outbound network beyond routine GitHub issue/branch/PR operations. No strategy CLI, research CLI, backtest, simulation, or regeneration was run. **No strategy or research module was imported or executed.** No price row, trade, holding, or return series was parsed. No daily, monthly, trade-level, or equity-curve series was reconstructed. No performance statistic was computed. **No cache file was altered, normalized, renamed, moved, copied, repaired, truncated, or deleted.** No archive of raw data was created. Phase 2, M55, and M56 were not run.

## The integrity distinction this package turns on

A committed `study_results_json_sha256` is **not** the expected hash of a raw cache file. It is the hash of a *generated results artifact*. The two are different objects, and comparing them would be meaningless.

Accordingly, a hash comparison was performed **only** where a manifest field names the exact byte-level artifact it hashes — `study_results_json_sha256` against `results/study_results.json`, `result_sha256` against `results/m52_result.json`, and so on. Those five comparisons are genuine. **They say nothing about the raw bar caches that produced those results**, for which no directly comparable committed hash exists anywhere in the repository.

Existence, plausible filenames, plausible timestamps, and matching aggregate counts are **not** integrity evidence. Timestamps appear in this package as diagnostic metadata only.

**No P2 `SUMMARY_ONLY` input is upgraded to `AVAILABLE_COMMITTED` by anything in this package.**

## Status vocabulary

| Status | Meaning |
|---|---|
| `PRESENT_DIRECT_HASH_MATCH` | The artifact exists and its SHA-256 equals a committed hash that names that exact artifact. |
| `PRESENT_METADATA_ONLY` | The artifact exists and a sidecar/provenance manifest describes it, but no directly comparable committed hash exists. |
| `PRESENT_UNVERIFIED` | The artifact exists; no committed hash and no sidecar manifest can speak to its integrity. |
| `PARTIAL` | Some required components are present and others are absent or delegated elsewhere. |
| `MISSING` | The expected artifact does not exist locally. |
| `AMBIGUOUS` | Presence or identity cannot be determined from permitted read-only evidence. |
| `NOT_APPLICABLE` | The item has no Phase-2 statistical role. |
| `CANDIDATE_AVAILABLE_FOR_PHASE2` | Required inputs appear present; Phase 2 could attempt regeneration if authorized. |
| `NOT_READY_FOR_PHASE2` | A required input is missing, ambiguous, or otherwise blocks regeneration. |

`CANDIDATE_AVAILABLE_FOR_PHASE2` means only that Phase 2 could *attempt* regeneration. It does not mean the instance is statistically executable, and nothing in this package makes any instance statistically executable.

## Privacy and licensing

Only aggregate inventory information is committed: repository-relative directory paths, file counts, aggregate sizes, and file extensions. No raw price, trade, or holdings rows; no symbol-level cache contents; no API responses; no absolute paths, usernames, secrets, tokens, or vendor account identifiers; no redistributable copy of licensed market data.

## Deliverables

| File | Purpose |
|---|---|
| `README.md` | This file. |
| `CACHE_INVENTORY_PHASE1.md` | One authoritative row per required milestone cache. |
| `CACHE_TO_TEST_INSTANCE_MAP.md` | TI-01–TI-11, DO-01, DO-02, SN-01 mapped to required caches. |
| `PHASE1_INTEGRITY_FINDINGS.md` | Confirmed findings, unresolved questions, manifest limitations. |
| `P3B_PHASE1_READINESS_DECISION.md` | Per-family and per-instance Phase-2 readiness. |
| `PHASE2_AUTHORIZATION_REQUIREMENTS.md` | What Phase 2 would need, and why it needs separate authorization. |
