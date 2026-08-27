# Publication P3B Phase 2B-1 — M52/M53 Deterministic Regeneration Verification

## Scope and authorization

This bounded pilot regenerated M52 only from its exact historical commit and only against scratch copies of the three existing local JSON cache trees. Its primary outcome was exact SHA-256 equality with the predeclared Phase 2A result hash. M53 was authorized only after an exact M52 match and a verified scratch-only M52-result handoff.

The M52 CLI completed, but its result hash did not match. The hard stop applied: no handoff and no M53 CLI. The temporary evidence is preserved outside the repository for diagnosis.

## Exclusions retained

No market, factor, SEC, OpenFIGI, broker, or account data was fetched or accessed. No package was installed or updated. No tracked source or frozen parameter was intentionally changed. M55 and M56 were not run. strategy.enabled remained false. No statistical testing, confidence interval, bootstrap, factor analysis, or strategy comparison was performed. No raw cache or regenerated result artifact is committed.

## Required sequence and stop gates

1. Verify main and origin/main at a89a47b71c6d1165f2213c669824256f7ec86ed6.
2. Read the M52/M53 hashes from the merged Phase 2A freeze.
3. Fingerprint original caches; use detached historical worktrees and verified scratch copies.
4. Run M52 cache-only and require exact equality.
5. Only on equality, hand off the verified M52 result and run M53.

Step 4 was FAIL. Step 5 was deliberately not performed.

## Independent-review correction (2026-08-27)

The original exact-byte failure remains unchanged. The preserved M52 worktree materialized `scripts/research_cross_sectional_momentum.py` with CRLF line endings under `core.autocrlf=true` and no root `.gitattributes`: its raw SHA-256 was `ee5276b67febba75792faf4a7a72d05720145bce25ac9a512017d7f7b724a187`, rather than the historical Git-object SHA-256 `411391271c0e8adf5f866233fe9f9ebc9515eddd99adb5dd8bb31cee651dff66`. LF-normalized content is identical. This is a recorded rerun-environment/provenance byte difference, not an intentional source edit or frozen-parameter change.
