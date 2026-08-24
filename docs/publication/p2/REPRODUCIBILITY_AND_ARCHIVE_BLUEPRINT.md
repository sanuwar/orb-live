# Reproducibility and Archive Blueprint

**This is a blueprint only.** Nothing in this document is executed by P2: the repository's visibility is unchanged, nothing is published, uploaded, licensed, or archived, and no OSF/Zenodo deposit was created.

## 1. Candidate public-source release boundary

- **Include:** all research/simulation source code (`src/`, `scripts/research_*`, `scripts/monitor_*`, `scripts/publication/`), all tests, all committed `docs/research/*.md` reports and hash manifests, all `docs/publication/` content (P1 and P2), the M55 append-only checkpoint ledger, and CI configuration.
- **Exclude unconditionally, per `CLAUDE.md`:** `data/.rh_tokens.json`, any production SQLite database under `data/`, `local.yaml` or equivalent private configuration, and any credential/account material.
- **Exclude:** every gitignored `artifacts/research-*/results/` Alpaca-derived raw-vendor cache — confirmed not redistributable per `DATA_LICENSE_REDISTRIBUTION_MATRIX.md` (Alpaca's official redistribution page states directly that Alpaca API data cannot be redistributed). OpenFIGI raw responses and SEC filing text are not blocked by the same prohibition (both confirmed shareable/likely-shareable respectively), but excluding them from a first public release remains reasonable pending the full license review, since they were fetched as part of the same Alpaca-dependent research pipeline.
- **Exclude:** the unrelated pre-existing untracked files this session has consistently preserved (`.claude/`, `.runtime/`, `CHECKPOINT_*.md`, `CLAUDE.md`, `MILESTONE_*.md`, `checkpoint_7c_*` scripts, `site/.vite/`) — these are operator working files unrelated to the publication package and are not research artifacts.

## 2. OSI-license decision requirements

A license has not been chosen. Before any public release: confirm the operator's intended license (a permissive license such as Apache-2.0 or MIT is typical for research-software releases and is compatible with OpenFIGI's own MIT terms, but this is a decision for the human operator, not this document); confirm no third-party code embedded in the repository carries an incompatible license; add a `LICENSE` file and per-file SPDX headers only after that decision is made.

## 3. CITATION.cff and contributor documentation

A `CITATION.cff` (title, authors, version, DOI placeholder, repository URL) would be added at release time, not now. A `CONTRIBUTING.md` and `CODE_OF_CONDUCT.md` are prerequisites JOSS specifically requires (per P1's `JOURNAL_TARGET_MATRIX.md`) and do not exist in the repository today.

## 4. Environment and dependency locking

Confirm the repository's dependency manifest (`requirements.txt`/`pyproject.toml`, whichever is authoritative) pins exact versions, not ranges, before any archival release; document the Python version and OS assumptions actually used to produce the committed M43–M55 results (this P2 session ran on Windows with PowerShell/Git-Bash tooling, which should be stated explicitly if it affects any path-handling or line-ending behavior — see the CRLF observation already logged in the P1 review comment).

## 5. Container/image digest plan

A frozen container image (e.g., a pinned Python base image plus the locked dependency manifest) is not yet built. At release time, build one, tag it with the exact repository commit SHA it was built from, and record the resulting image digest in the release notes so "clean-machine reproduction" (item 10) is unambiguous about which container was used.

## 6. Synthetic or redistributable fixtures

None exist yet. A future work item: hand-craft small synthetic OHLCV/N-PORT-P/OpenFIGI-response fixtures (not derived from real Alpaca/SEC data, since real Alpaca-sourced data is confirmed non-redistributable per `DATA_LICENSE_REDISTRIBUTION_MATRIX.md`) sufficient to exercise every Stage-0 gate, parity check, and classification rule end-to-end, so a reader can run the pipeline without any vendor account.

## 7. One-command generation of publication tables

Partially present: `scripts/publication/generate_p2_hypothesis_ledger.py` already regenerates `HYPOTHESIS_LEDGER_M43_M55.md` from committed JSON in one command with no network access. No equivalent single command yet regenerates the M43–M55 dollar-figure tables from their own committed hash-manifest-pinned regeneration path; each milestone's own CLI (e.g. `scripts/research_momentum_risk_control.py`) already exists per-milestone but has not been unified into one publication-table command.

## 8. Raw, derived-shareable, and non-shareable data separation

| Tier | Contents | Shareable today? |
|---|---|---|
| Raw vendor data | Alpaca bars, SEC filing XML, OpenFIGI raw responses (gitignored `artifacts/research-*/results/`) | **No.** Alpaca's official redistribution page states directly that Alpaca API data cannot be redistributed (see `DATA_LICENSE_REDISTRIBUTION_MATRIX.md`) — this now applies to all Alpaca-sourced raw bars without qualification. SEC EDGAR is likely fine (public government record) but its exact current automated-access/fair-use text was not independently reconfirmed this session. OpenFIGI raw responses are confirmed shareable (MIT license + explicit FAQ redistribution statement). |
| Derived shareable data — non-Alpaca-derived | Committed hash manifests, the P2 hypothesis ledger, any committed scalar result that does not derive from Alpaca market data (e.g., OpenFIGI resolution statistics, SEC filing counts/dates) | Yes — already committed |
| Derived data — Alpaca-derived scalars | Committed mean-monthly-$, drawdown, CAGR, and similar scalar figures in `docs/research/*.md` for M43–M53 (all computed from Alpaca SIP/IEX bars) | **Not called unconditionally shareable.** Alpaca's redistribution prohibition is confirmed for *raw* API data; whether it extends to a derived aggregate statistic (a single committed number computed from many bars, not the bars themselves) is an open question `DATA_LICENSE_REDISTRIBUTION_MATRIX.md` explicitly leaves unresolved pending human/legal review. These figures remain committed to this private repository (permitted, since P2 makes no visibility change) but must not be characterized as cleared for public redistribution until that review happens. |
| Non-shareable | `data/.rh_tokens.json`, production SQLite, `local.yaml` | Never (permanent `CLAUDE.md` prohibition) |

## 9. Artifact checksums and manifests

Already substantially in place: every M48–M55 milestone commits a hash manifest (`*_ARTIFACT_HASHES.json`) pinning `generation_git_head_sha`, `generation_source_file_sha256`, and a results-JSON SHA-256. A release-level manifest aggregating all of these into one top-level `RELEASE_MANIFEST.json` does not exist yet and would be a release-time task.

## 10. Clean-machine reproduction procedure

Not yet performed. Would require: (a) the frozen environment/container from item 5, (b) either the redistributable fixtures from item 6 or a licensed reviewer's own vendor credentials, (c) a documented step-by-step command sequence, and (d) a reproduction log capturing exact output hashes matching the committed manifests from item 9.

## 11. GitHub release structure

Not yet created. Would tag the exact commit SHA corresponding to a chosen manuscript version, with release notes cross-linking the issue/PR history for that version.

## 12. Zenodo DOI plan

Not created (explicitly out of scope for P2 — "do not create an OSF or Zenodo deposit"). At release time: connect the repository to Zenodo's GitHub integration, mint a DOI on the tagged release from item 11, and ensure the DOI is versioned so future M55 checkpoint deposits (item 16) do not overwrite it.

## 13. OSF dossier plan

Not created (same out-of-scope boundary). At release time: an OSF project would host the study dossier, protocol documents (including this P2 package), the disclosure documents, and any permissible derived artifacts, cross-linked to the Zenodo code DOI.

## 14. Cross-linking of DOI, commit, issue, PR, and correction history

Every M43–M55 milestone already links its issue, PR, and (via the hash manifests) its generation commit SHA. A release-time DOI would need one additional cross-link layer (DOI → tagged commit → this existing issue/PR/hash chain), not a redesign of the existing chain.

## 15. Secret/private/vendor-data exclusion checks

At release time, a mechanical check (e.g., `git log --all --full-history -- data/.rh_tokens.json` returning nothing, and a `.gitignore` audit confirming every `artifacts/research-*/results/` path stays excluded) must run and pass before any public push. Not run in P2, since P2 makes no repository-visibility change.

## 16. Versioning of future M55 checkpoint deposits

Each future M55 checkpoint (per its own append-only, hash-chained ledger design) would be deposited as a new, separately versioned Zenodo record or release asset, never overwriting a prior deposit — mirroring the ledger's own "never rewrite a previous checkpoint" rule at the archival layer.

## Explicit non-actions confirmed for this document

No public-source release occurred. No license was applied. No container was built. No fixtures were created. No manifest beyond what M43–M55 already committed was generated. No clean-machine reproduction was run. No GitHub release was tagged. No Zenodo or OSF deposit was created. No secret-exclusion check was run (none was needed, since nothing changed visibility).
