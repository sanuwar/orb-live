# Phase 2B Risks and Mandatory Stop Conditions

## Risk register

| # | Risk | Evidence | Severity |
|---|---|---|---|
| 1 | **Committed-manifest overwrite** | M48/M49/M52/M53 each call `COMMITTED_HASHES_PATH.write_text(...)` on a tracked `docs/research/*_ARTIFACT_HASHES.json`. Run from the main checkout they would overwrite the frozen expected hashes with whatever the rerun produced -- destroying the success criterion and any ability to detect failure | **Critical** |
| 2 | **Result-artifact overwrite** | The same four write `RESULTS_DIR / *.json` at hardcoded paths, overwriting the Phase-1-verified byte-identical artifacts | **Critical** |
| 3 | **Cache mutation via pickle writes** | M43/M45 call `pickle.dump` into the checkpoint directory; a default-path run writes into the cache tree | **High** |
| 4 | **Pickle deserialization** | M43/M45 call `pickle.load`, and M49 loads M43 checkpoints. Deserialization is code-execution-capable and may import or invoke referenced globals or classes; it is not passive data inspection | **High** |
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
