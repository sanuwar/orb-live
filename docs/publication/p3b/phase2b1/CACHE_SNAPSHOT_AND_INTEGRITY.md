# Cache Snapshot and Integrity

## Scope and method

Only daily-bar-cache, sec-nport-cache, and openfigi-cache were exposed. Each was copied normally into each detached worktree; no original cache was passed to a CLI and no symlink or junction was used. The method reads opaque bytes only: SHA-256 over sorted SHA256(relative_path_utf8):byte_size:SHA256(file_bytes) records.

## Baseline, before, and after

All values below matched before copying and after M52. M53's copies were never executed against.

| Tree | Files | Bytes | Aggregate SHA-256 | Original pre/post | M52 scratch pre/post | M53 scratch post |
|---|---:|---:|---|---|---|---|
| daily-bar-cache | 942 | 177,025,917 | 0698d51e8f3c1d599e5d85884fa192b2abf8bfde1cd8495574404681626ebf8d | match / match | match / match | match |
| sec-nport-cache | 29 | 25,130,891 | 889fb5dc0a8b5d5f6ecf6693cd48bb0beae62cd38524fa73c308f44cf546c4fc | match / match | match / match | match |
| openfigi-cache | 5,243 | 786,008 | b2ad68c940fe3854bca92918290a94211a056cee06887e1e79a0a9020dd5ef86 | match / match | match / match | match |

No count, byte total, or aggregate-digest mutation was found in original or scratch cache trees.

## Digest-description discrepancy

Phase 2A prose illustrates spaces around record colons. The frozen values reproduce exactly with the unspaced form above. This implementation-documentation discrepancy is recorded as a warning; it is not a cache mutation.

## Isolation and cleanup

The exact temporary root is C:\tmp\orb-live-p3b-phase2b1-20260826T000000Z, containing detached wt-m52 and wt-m53 worktrees, logs, and the scratch-only regenerated M52 result. Because M52 failed its exact-hash gate, these exact targets are preserved for diagnosis. No original cache, repository worktree, or unrelated temporary worktree is a cleanup target.
