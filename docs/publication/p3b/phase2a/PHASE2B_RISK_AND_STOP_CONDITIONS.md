# Phase 2B Risks and Mandatory Stop Conditions

## Risk register

| # | Risk | Evidence | Severity |
|---|---|---|---|
| 1 | **Working-tree manifest overwrite** | M48/M49/M52/M53 each call `COMMITTED_HASHES_PATH.write_text(...)` on a tracked `docs/research/*_ARTIFACT_HASHES.json`. Run from the main checkout they would overwrite the **working-tree copy**. Committed Git history is unaffected and the historical manifests stay recoverable; Phase 2A's separately frozen hashes also remain available. Severe as contamination, **not** as loss of history | **High** |
| 2 | **Result-artifact overwrite** | The same four write `RESULTS_DIR / *.json` at hardcoded paths, overwriting the Phase-1-verified local gitignored artifacts. These are **not** recoverable from Git, so this is the more consequential of the two overwrite risks | **Critical** |
| 3 | **Cache deletion by `validate_cache_integrity`** | M43/M45 call `validate_cache_integrity(args.cache_dir)`, which `path.unlink(missing_ok=True)`s any `*.json` that fails to parse or lacks a `bars` key. `--cache-dir` defaults to the **minute-bar cache**. Exposing the original tree does not leave it untouched | **Critical** |
| 4 | **Pickle deserialization** | **M48's full study**, M49, and M43/M45 all `pickle.load` M43 checkpoints. Deserialization is code-execution-capable and may import or invoke referenced globals or classes. **Optional for M43/M45** (`--no-checkpoint` skips both load and dump) and avoidable for M48 via `--data-only`, but **unavoidable for M48's exact-output run and for M49** | **High** |
| 5 | **Cache exposure to an isolated worktree** | No mechanism is authorized; copying is prohibited, symlinks are writes, and running in the main checkout forfeits isolation | **High** |
| 6 | **Environment and dependency drift** | No committed lockfile, container digest, or interpreter-version record exists per milestone | **Medium** |
| 7 | **Cache-miss fetch** | Mitigated: every CLI defaults `--fetch-missing` to `False`, so omitting it is cache-only by construction. Residual risk is a future edit changing that default | **Low, mitigated** |
| 8 | **Licensing** | Regenerated artifacts derive from Alpaca bars (redistribution not permitted), SEC EDGAR (unresolved), and OpenFIGI (permitted). Nothing regenerated may be committed or archived without human/legal review | **Medium** |
| 9 | **Line-ending false mismatch** | Three of eleven source pins verify only under CRLF. A naive raw-blob comparison would report false drift | **Medium** |
| 10 | **Disk and runtime pressure** | The M49 ORB minute cache alone is 3.39 GB across 3,141 files | **Low** |

## Mandatory stop conditions

Phase 2B **must halt immediately**, record the finding, and seek fresh authorization if any occurs:

1. Any write is attempted to a **tracked** file under `docs/`, `src/`, `scripts/`, `config/`, or `tests/`.
2. Any cache tree's file count, aggregate size, or aggregate digest differs from `CACHE_BASELINE_DIGESTS.md` at any checkpoint.
3. Any network call is attempted, including a cache-miss fetch.
4. Any process attempts broker or account access.
5. A regenerated artifact does not match its frozen expected hash **and** the cause is not conclusively identified. A mismatch is a finding; guessing at its cause is not permitted.
6. Pickle deserialization raises, or loads under a different class hierarchy than expected.
7. `strategy.enabled` is observed as anything other than `false`.
8. Any M55 file is read for mutation or written at all.
9. The cache-exposure mechanism has not been explicitly authorized before the first command runs.
10. Any parameter, threshold, feed, window, or universe differs from the frozen milestone configuration.

## Non-negotiable invariants

- No expected hash in `EXPECTED_RESULT_HASH_FREEZE.md` may be revised by Phase 2B under any circumstance.
- No cache file may be modified, copied, normalized, renamed, repaired, or deleted.
- No regenerated raw data may be committed to Git.
- A hash match proves byte-identity of one artifact. It does not establish historical input integrity, and it does not make any hypothesis statistically executable.


## Additions from the full call-path audit

| # | Risk | Evidence | Severity |
|---|---|---|---|
| 11 | **M48 full-run pickle dependency was previously unrecorded** | `run_full_study` line 433 calls `load_orb_monthly_r_series()`, which imports `pickle` and loads six M43 `*.pkl` files. Earlier documents described M48 as JSON-only | **High** |
| 12 | **M52 to M53 scratch handoff undefined** | M53 hard-fails without an M52 result, but separate worktrees at different commits do not share generated artifacts | **Medium** |
| 13 | **Checkpoint pickle writes** | M43/M45 `_save_period_checkpoint` writes unless `--no-checkpoint` is passed | **Medium, mitigable** |

## Additional mandatory stop conditions

11. `--cache-dir` for M43 or M45 resolves to the original minute-bar cache rather than an authorized read-only snapshot or scratch copy.
12. `validate_cache_integrity` reports any non-empty `removed` list.
13. An M53 run begins without an M52 result whose SHA-256 has been verified against `590672f029a519b665b856106d0b35d701b464aa3f12b5a0b1978f18b18ac8f9`.
14. An M48 exact-output run begins without M43 checkpoint availability having been confirmed and its deserialization explicitly authorized.
