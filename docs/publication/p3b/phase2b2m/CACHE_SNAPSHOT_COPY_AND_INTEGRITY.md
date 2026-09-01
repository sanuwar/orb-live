# Cache Snapshot Copy and Integrity

## Method

The three cache-tree definitions, counts, byte totals, and aggregate digests are **reused unchanged** from the merged Phase 2B-2A and Phase 2B-2G records. Nothing here re-derives or re-freezes them.

For each tree, in order:

1. Measure the **original** tree: file count, byte total, and aggregate digest.
2. Compare against the frozen expected values — a mismatch stops immediately with `BLOCKED_CACHE_OR_ISOLATION`.
3. Create a distinct scratch destination under the Phase 2B-2M root, refusing to overwrite anything already present.
4. Copy every regular file **byte for byte** with an ordinary streaming copy.
5. Measure the scratch copy and compare its file count, byte total, relative-path set, and aggregate digest.
6. Re-measure the **original** tree and confirm it is byte-identical to its pre-copy state.

The aggregate is the Phase 2A algorithm: SHA-256 over LF-joined, lexicographically sorted lines `SHA256(relative_path_utf8):byte_size:SHA256(file_bytes)`, unspaced — the form Phase 2B-1 confirmed reproduces the frozen values. Relative paths are hashed rather than recorded, so the digest is reproducible by anyone holding the same tree while committing no filename. **File bytes were read and hashed as opaque bytes only — never decoded, deserialized, or semantically parsed.**

No symlink, junction, directory link, hard link, reflink, or copy-on-write alias was used. The walk refuses any symlink or non-regular file outright, so a special file or unexpected reparse point stops the copy rather than being silently followed. Path traversal is impossible by construction: destinations are built from the walked relative path under a fixed destination root.

## Results

| Tree | Files | Bytes | Aggregate SHA-256 | Original pre | Scratch copy | Original post |
|---|---:|---:|---|---|---|---|
| `daily_bar` | 942 | 177,025,917 | `0698d51e8f3c1d599e5d85884fa192b2abf8bfde1cd8495574404681626ebf8d` | match | match | match |
| `openfigi` | 5,243 | 786,008 | `b2ad68c940fe3854bca92918290a94211a056cee06887e1e79a0a9020dd5ef86` | match | match | match |
| `sec_nport` | 29 | 25,130,891 | `889fb5dc0a8b5d5f6ecf6693cd48bb0beae62cd38524fa73c308f44cf546c4fc` | match | match | match |
| **Total** | **6,214** | **202,942,816** | — | — | — | — |

Every tree reproduced its frozen count, byte total, and aggregate digest exactly, in all three measurements. The **relative-path set** of each scratch copy is identical to its original — a separate check from the aggregate digest, so a copy that somehow produced a colliding digest with different filenames would still be caught.

## Original-cache preservation

**Every original tree's pre-copy and post-copy evidence is identical**, on all three of count, byte total, and aggregate digest. No original cache file was repaired, deleted, normalized, deserialized, rewritten, or renamed. The copy is read-only with respect to the originals by construction, and this confirms it empirically rather than assuming it.

The originals were never passed, linked, or otherwise exposed to any executable historical code — nothing executable ran at all in this phase. The gate additionally verified structurally that the scratch root lies outside the repository working tree, that it neither contains nor is contained by any declared original cache root, and that the historical checkout is likewise outside the repository.

## Preserved Phase 2B-1 evidence

The preserved Phase 2B-1 diagnostic scratch root was confirmed present and **left completely untouched** — neither read nor modified. It is a separate root from this phase's, and non-overlap in both directions was proven before any write.

## Fetch boundary

`fetch_missing` is `false` in the frozen manifest, so **no cache miss could trigger a fetch**. In this phase the question never arose: nothing consumed the caches. They were hashed and copied, and that is all.
