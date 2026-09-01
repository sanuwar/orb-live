# Isolated Checkout and Source Audit

## Clone method

The isolated checkout was created under the Phase 2B-2M scratch root with:

```
git clone --no-checkout --local --no-hardlinks <local repository> <scratch>/checkout
```

- **`--local`** with the existing local repository as the source: **no network clone or fetch of any kind occurred.**
- **`--no-hardlinks`** was used deliberately. `--local` alone hardlinks the object store by default; passing `--no-hardlinks` guarantees that no file in the isolated clone shares an inode with anything in the primary repository, so nothing written in scratch can reach back into it.
- **`--no-checkout`** so that not a single working-tree byte was materialized before the EOL policy was in place. Checking out first and fixing endings afterwards would have produced the right bytes by accident rather than by construction.
- The clone has its own `.git` metadata and clone-local configuration. **Main's Git configuration, attributes, and tracked files were not touched.**

## Configuration and attribute neutralisation

The primary repository has `core.autocrlf=true` and no root `.gitattributes`, so an ordinary checkout on this machine would materialize CRLF bytes for all 30 files and fail the frozen manifest. Global and system Git configuration could also silently supply a different EOL or attributes policy. Both were neutralised:

- Clone-local `core.autocrlf=false`, `core.eol=lf`, and `core.attributesFile` pointed at a deliberately empty file inside the clone's own `.git/info/`.
- `GIT_CONFIG_NOSYSTEM=1`, `GIT_CONFIG_GLOBAL=<empty file>`, and `GIT_ATTR_NOSYSTEM=1` for the checkout itself.
- A clone-local `.git/info/attributes` with **exactly 30 rules**: `text eol=crlf` for the two CRLF paths and `text eol=lf` for the other 28.

The effective configuration was recorded with `git config --show-origin` and confirmed to resolve to the clone-local file, not to any global or system source:

```
file:.git/config    core.autocrlf false
file:.git/config    core.eol lf
file:.git/config    core.attributesfile <clone>/.git/info/empty-global-attributes
```

The attributes and configuration files are clone-local Git metadata, not tracked source edits.

## Commit and closure verification

`git rev-parse HEAD` in the isolated checkout returns exactly:

```
4c43fe96ab80d739fd10a9bd6bfa4c1319f67865
```

All **30 of 30** files were verified against the merged Phase 2B-2G manifest by **size, SHA-256, and actual line-ending form**. The EOL check is independent of the hash check on purpose: a file can only pass both if it is genuinely the right bytes materialized under the right policy.

| Materialization | Files | Result |
|---|---:|---|
| LF | 28 | all match the raw Git-object bytes and digests |
| CRLF | 2 | all match the frozen CRLF provenance sizes and digests |
| **Total** | **30** | **30/30 verified, zero failures** |

The two CRLF files are `scripts/research_cross_sectional_momentum.py` (34,521 bytes, `ee5276b6…b724a187`) and `src/cross_sectional_momentum_research.py` (54,292 bytes, `cde0af38…1ce0352`); the other 28 materialize to their raw Git-object bytes.

## Clean-source status

Verified using only the two reviewed Git command vectors, run from the isolated checkout:

| Vector | Result |
|---|---|
| `git rev-parse HEAD` | `4c43fe96ab80d739fd10a9bd6bfa4c1319f67865` |
| `git status --porcelain -- <the three provenance files>` | empty (clean) |

As an additional check beyond the two reviewed vectors, `git diff --name-only` over all 30 closure paths is also empty. The per-path `eol` attributes make the working tree agree with the index, so the deliberate CRLF materialization does not register as a modification — the frozen bytes were produced without editing a single tracked source file.

## What was not done

**No file from this checkout was imported.** **No project CLI or Python module was invoked from it.** The checkout exists to be hashed and inspected, and that is all that happened to it. It contains no M53 output artifact: the intended result path was confirmed absent before the gate ran.
