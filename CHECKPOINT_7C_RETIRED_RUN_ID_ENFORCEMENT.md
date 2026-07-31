# Checkpoint 7C — Retired Live Run ID Enforcement

Housekeeping. No broker, MCP, network, launcher, or wrapper activity occurred,
and no trading behaviour changed. This closes a protection gap that was flagged
repeatedly during the Checkpoint 7C live campaign and never closed while live
work was in progress.

## Why marker-only protection was insufficient

A Run ID was considered used when its evidence directory or its
`.wrapper-used` marker existed in the evidence root. Both live in the operator's
temporary directory. That is corroboration, not protection:

- clearing or losing the evidence root silently restores reuse of **every**
  historical identity at once;
- an identity that never reached the marker-writing step leaves no trace at all;
- an operator can copy a Run ID into a brand-new wrapper pinned to current
  `main`, and every on-disk check passes because nothing on disk names it.

The tracked registry has none of those weaknesses: it travels with the
repository, is reviewed like code, and cannot be erased by a temp-directory
cleanup.

## The two special cases

**`cp7c-live-aapl-one-share-20260731-03`** — wrapper prepared and statically
reviewed, then superseded before launch. It was never executed, so it has no
evidence directory and no marker. Under marker-only protection it was fully
reusable. Its wrapper also pinned a since-superseded `main` SHA, which would
have failed the HEAD gate — but that is a coincidence of timing, not a
protection, and it disappears the moment someone re-pins.

**`cp7c-live-aapl-one-share-20260731-04`** — launched, and blocked at an
operator acknowledgment (`REAL_SESSION_NOT_AUTHORIZED`) because `YEST` was
typed instead of `YES`. The wrapper-used marker is written **after** all nine
acknowledgments pass, so this run left no on-disk trace whatsoever despite
having been executed. It was spent by instruction only.

Both are now rejected from tracked source.

## Complete inventory

Nineteen identities were discovered across wrappers, evidence artifacts,
tracked history, and documentation; `20260730-03` was additionally named by the
operator as issued. All twenty are retired.

| Run ID | Classification | On-disk trace |
| --- | --- | --- |
| `…20260728-01` | abandoned / obsolete | none |
| `…20260728-03` | abandoned / obsolete | none |
| `…20260728-04` | abandoned / obsolete | none |
| `…20260728-05` | blocked after execution | partial only |
| `…20260728-06` | abandoned / obsolete | none |
| `…20260729-02` | blocked after execution | partial + marker |
| `…20260729-03` | blocked after execution | partial + marker |
| `…20260729-04` | blocked after execution | partial + marker |
| `…20260730-01` | abandoned / obsolete | none |
| `…20260730-02` | abandoned / obsolete | none |
| `…20260730-03` | reserved, never issued a wrapper | none |
| `…20260730-04` | blocked after execution | partial + marker |
| `…20260730-05` | blocked after execution | partial + marker |
| `…20260731-01` | blocked after execution | partial + marker |
| `…20260731-02` | blocked after execution | partial + marker |
| `…20260731-03` | prepared, superseded before launch | **none** |
| `…20260731-04` | launched, blocked before marker | **none** |
| `…20260731-05` | **completed** | completed + marker |
| `…20260731-06` | **completed** | completed + marker |
| `…20260731-07` | **completed** | completed + marker |

Eight were already registered; twelve were added.

**The broker-free authorization rehearsal**
(`checkpoint_7c_authorization_rehearsal_20260731.ps1`) is deliberately **not**
in the registry. It is not a live broker run: it opens no session, places no
order, and consumes no Run ID at all — it passes a literal non-identity string
and never calls `validate_live_run_request`. There is no live identity to
retire. It remains non-reusable in the ordinary sense: it is preserved
untracked, and any future rehearsal gets its own file.

## Where enforcement occurs

`validate_live_run_request` in the commissioning launcher, immediately after
the runtime-flag and Run-ID-pattern checks and **before**:

- the authorization provider is constructed, so neither hidden `y/N` prompt can
  be reached;
- the session factory or binding is wired, so no MCP session is initialised;
- any account, order, position, cancellation, or market-data call;
- any reliance on a wrapper-used marker.

The check consults only the tracked frozenset. It reads no file and no
directory, so it fails closed when the wrapper is missing, the evidence
directory is missing, the marker is missing, the wrapper pins current `main`,
or the identity was hand-copied into a fresh wrapper. The reason code is the
existing `run ID is permanently retired`, and the message contains no path,
symbol, identifier, or account data.

A test also pins that retirement is refused **before** the time policy is
validated, so the identity gate cannot be reached around by first tripping a
different validation an operator might then "fix".

## Why the registry is explicit rather than wildcard-based

A pattern broad enough to cover these identities — say, every
`cp7c-live-aapl-one-share-2026*` — would also reject identities that have never
been issued. That converts a precise refusal into an indiscriminate one and
would block the next legitimate run for no reason. Fixed literals state exactly
what is spent and nothing more, and they are auditable line by line.

Entries are sorted, never removed, and an entry is added when an identity is
**issued**, regardless of what happened afterwards. An inventory-consistency
test compares an explicit historical list against the registry in both
directions, so a future edit cannot silently drop an entry.

## What did not change

No parser, reconciliation, authorization, evidence, or write-limit behaviour.
Entry 1, stop 1, cancel 1, cancel retries 0, exit 1, exit retries 0 all stand
untouched. `strategy.enabled=false`.

## No further live validation required

This PR removes a reuse path; it cannot enable one. The only runtime effect is
that twelve additional literal strings are now refused before a session can
open. The blank-symbol repair was already validated live by
`cp7c-live-aapl-one-share-20260731-07`, and that identity is itself now
registered as retired.
