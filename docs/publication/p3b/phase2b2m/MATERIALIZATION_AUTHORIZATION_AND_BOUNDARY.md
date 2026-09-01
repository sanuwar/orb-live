# Materialization Authorization and Boundary

## What this phase was authorized to do

Issue [#386](https://github.com/sanuwar/orb-live/issues/386) authorized, for verification only: one new isolated historical checkout outside the repository; byte-for-byte scratch copies of the three required local cache trees; materialization of the frozen historical M52 handoff into scratch; materialization of the reviewed guard files into a separate scratch directory; content pins for the Python and Git executables; an empty scratch output and evidence structure; one run of the merged `scripts/publication/m53_execution_gate.py`; and preservation of the resulting evidence.

## What it was not authorized to do, and did not do

- **M53 execution or import** — no M53 module was imported, executed, or launched.
- **M52 execution or import** — the M52 artifact was copied as opaque bytes and hash-verified; it was never parsed beyond what the gate itself requires, and no M52 module was imported or run.
- **Historical simulation or regeneration** — none ran. **No regenerated M53 artifact was created or observed.**
- **Any data fetch** — no market, factor, SEC, OpenFIGI, broker, or account-data request was made. Nothing in this phase opened a socket.
- **Broker or account access** — none.
- **Strategy activation** — `strategy.enabled` remains `false`.
- **Advancing M55** — the checkpoint was not run.
- **Merging this pull request**, or starting M56, P3C, statistical execution, or any other workstream.

The guard was materialized but **not activated**, and neither guard file was imported. Materializing a guard is not running one.

## Scratch-root selection and proof

One new, uniquely named scratch root was created under a narrow trusted temporary base, named `orb-live-p3b-phase2b2m-<UTC timestamp>`. **Its exact path is reported to the operator only and appears in no committed file**, because a private absolute path is not publication-safe evidence.

Before anything was written, the target was resolved and printed, and every one of these was proven:

1. It did not already exist, and was not an existing link.
2. It is not a broad or forbidden target — the drive root, the trusted base itself, the repository root, and `HOME`/`~` are all explicitly refused, as is any path shallower than three components or not carrying the expected unique name prefix.
3. It lies under the trusted base and **outside** the repository working tree in both directions.
4. It does not overlap any of the three original cache roots in either direction.
5. It does not overlap the preserved Phase 2B-1 diagnostic scratch root in either direction.
6. It is not the operating-system temporary directory root, nor inside it.
7. No ancestor at or below the trusted base is a symlink, junction, or unexpected reparse point, checked by comparing each ancestor's literal path against its resolved path.

The preserved Phase 2B-1 root was confirmed present and left **completely untouched** — it was neither read nor modified.

## Destructive-operation boundary

No `git clean`, `reset --hard`, `checkout --`, force-push, history rewrite, or recursive deletion was used at any point. **The scratch root is deliberately preserved, not deleted**, so an independent reviewer can re-verify every claim in this package against the actual materialized inputs.

Nothing in the repository working tree was modified outside the committed documentation package: main's Git configuration, `.gitattributes` (which does not exist), and all tracked files are unchanged. All EOL neutralisation was clone-local to the isolated checkout.

## Committed-evidence discipline

This package commits aggregate, publication-safe evidence only. It contains no cache file, no historical checkout file, no raw market row, no private absolute path, no username, no credential, no environment dump, no raw scratch command log, no Phase 2B-1 evidence, and no regenerated M53 artifact.

The one machine-generated file committed verbatim, `M53_REAL_PREFLIGHT_RESULT.json`, is the gate's own record. It was audited before commit and contains no username, no drive letter, no absolute or scratch path, no credential-shaped token, and no cache or market content — supplied paths appear in it only as SHA-256 digests of their normalized text.

Those digests are **pseudonymous identifiers, not anonymization**: they are unsalted hashes of low-entropy, guessable strings, and independent review confirmed all seven from guessed paths. They remove casual disclosure and give a stable correlation handle; they are not a confidentiality control. See `REAL_PREFLIGHT_EVIDENCE_SUMMARY.md`.
