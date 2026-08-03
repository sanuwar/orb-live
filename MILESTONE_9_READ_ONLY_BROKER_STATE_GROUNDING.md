# Milestone 9 — Read-Only Broker-State Grounding

**Status: IN PROGRESS — the real-account checkpoint has been executed; steps 4
and 5 remain.** The offline foundation and the real read-only observation
runtime are both implemented, merged, and verified. The attended checkpoint
**was executed once, successfully**, and recorded that no protective order
existed. **No broker state has been approved for active protection**, and the
approved mapping is still empty.

## ✅ Operator gate lifted — the real-account checkpoint ran once

An operator lifted the hold and executed step 2 of the definition of done on
**2026-08-03 at approximately 09:58 ET**, inside regular hours.

| Fact | Value |
| --- | --- |
| `approved_main_sha` | `a93d13f1027552b8d01194cf925438c916bb0ed6` |
| `checkpoint_implementation_digest` | `8516057134acc1540bfa90f76fc9f5663588fcff53d5d08d07d20d4c79131faf` |
| Run ID | `m9-obs-20260803-01` |
| Outcome | `NO_PROTECTIVE_ORDER_OBSERVED` |
| Decision | `VERIFIED_FLAT` |
| Freshness | `FRESH` |
| Broker sessions | exactly 1, opened and closed cleanly |
| Read calls | `get_accounts` 1 · `get_equity_positions` 1 · `get_equity_orders` 1 |
| Write counters | all `0` |
| Approved active-protection state count | `0` |

The account was **verified flat**, with no position and no executable related
sell order. Sanitized evidence was written to an untracked tree outside the
repository and validated: every required field present, each permitted read
exactly `1`, every write counter `0`, and no account identifier, symbol, order
identifier, quantity, price, or balance persisted anywhere in it.

### A flat account is a real result, not a failed run

`NO_PROTECTIVE_ORDER_OBSERVED` is one of the two **completion** outcomes in
`COMPLETED_OUTCOMES`. The checkpoint observes a protective stop only if one
already exists from separate, safe circumstances; if none exists it reports
`NOT_OBSERVED` and stops, which is exactly what step 3 of the definition of
done contemplates. The run is complete and is not to be repeated in search of
a different answer.

**No active protection state was observed, and nothing was promoted.**
`ADAPTER_PERMITTED_ACTIVE_PROTECTION_STATES` remains empty, `strategy.enabled`
remains `false`, and `ACTIVATION_APPROVALS` remains empty.

### The Run ID and manifest are permanently spent

`m9-obs-20260803-01` and the manifest issued against it are consumed. Neither
may be reused, repointed, or reissued — the consumption ledger records the
identity outside the evidence tree precisely so that clearing evidence cannot
silently restore authorization.

### Pins for any future checkpoint

A later checkpoint is a **new** run, and every pin must be derived again at
that time:

| Pin | How it is obtained |
| --- | --- |
| Run ID | a fresh, never-used identity |
| authorization manifest | newly authored by an operator, single-use |
| `approved_main_sha` | re-derived from `git rev-parse HEAD` at manifest-issue time |
| `checkpoint_implementation_digest` | the **then-current** digest over `PINNED_IMPLEMENTATION_FILES` |

### The re-pin window has since moved the digest

| Digest | Value | Status |
| --- | --- | --- |
| Pre-re-pin (historical) | `8516057134acc1540bfa90f76fc9f5663588fcff53d5d08d07d20d4c79131faf` | What run `m9-obs-20260803-01` actually executed. **Never valid again.** |
| Current | `2d9e25ba0643a102725ac22a1a8551b293f9e6d014c791a0f7df937934f72d50` | The digest a new manifest must carry |

All five pinned files changed in the re-pin window described below, so
**every manifest issued against the old digest is now invalid** — including
the one already spent. That is the mechanism working as designed: an operator
approved *that* implementation, and this is no longer that implementation.

The historical values are records, not values to copy forward. That applies to
the old digest, to `approved_main_sha`
`a93d13f1027552b8d01194cf925438c916bb0ed6`, and to the pre-execution
`4fc75b6be7419d369c3c72dac046f23842eb3f6d` from when PR #44 merged.
`approved_main_sha` must equal the resolved `HEAD`, so any commit to `main` —
this documentation included — moves it.

Manifests remain **operator-created only**. Nothing in this repository can emit
one, and a manifest is single-use against a fresh, unretired Run ID.

## Why active protection cannot be inferred from schema

Attended commissioning could rely on a human watching a single share. If a stop
was not really armed, the operator was there. Unattended operation removes that
backstop entirely, so the runtime must be able to assert *this order state means
the stop is resting at the venue and would fire* — and it cannot.

The advertised schema lists order states. It does not say which of them mean a
stop is working. `new`, `queued`, `unconfirmed`, and `confirmed` are all
plausible-sounding and materially different: a stop sitting in `unconfirmed` may
not be at the venue at all. Reading a state name tells you the broker's label,
not the venue's behaviour.

Three of these states were observed in completed attended runs
(`confirmed`, `unconfirmed`, plus terminal `filled` and `cancelled`). **That is
still not grounds for approval.** Seeing a label once, in one account, on one
day, under one set of conditions, does not establish what it guarantees.

## Current mapping: empty, deliberately

`ADAPTER_PERMITTED_ACTIVE_PROTECTION_STATES` in `src/live_order_state_model.py`
is an empty frozenset. Consequently `assess_protective_coverage` can never
return `VERIFIED_ACTIVE_PROTECTION`, and every working state assesses as
indeterminate.

`src/read_only_broker_state.py` derives its approved set from a provenance
registry rather than restating it, and a test asserts the two agree. Today both
are empty, and the completed 2026-08-03 checkpoint did not change that: it
observed a flat account, so it had nothing to propose and promoted nothing.

Attended commissioning is unaffected: it uses a separate
`assess_provisional_protection`, which is what the three completed live runs
recorded. **The completed one-share attended commissioning is not invalidated by
this gap.**

## Provenance framework

Every candidate state carries the honest source of what is known about it:

| Provenance | Meaning | Sufficient for protection? |
| --- | --- | --- |
| `SCHEMA_ONLY` | Appears in the advertised output schema | No |
| `FAKE_ONLY` | Only ever produced by a local fixture | No |
| `HISTORICAL_SANITIZED_EVIDENCE` | Observed in completed sanitized run evidence | No |
| `READ_ONLY_LIVE_OBSERVATION` | Seen by a reviewed read-only checkpoint | No |
| `APPROVED_FOR_ACTIVE_PROTECTION` | Reviewed and approved | **Yes** |

`can_protect` and `can_coexist_with_executable_sell` are recorded as `None`
while unknown. Leaving them unset is honest; guessing them is the failure this
module exists to prevent. A candidate cannot be constructed as approved unless
`can_protect` is true.

`proposed_state_promotion` deliberately **cannot** return
`APPROVED_FOR_ACTIVE_PROTECTION`. The most a single observation earns is
`READ_ONLY_LIVE_OBSERVATION`. Promotion to approved requires human review of
recorded observations — the act of observing is not the act of approving.

## Snapshot model

`ReadOnlyBrokerSnapshot` is a frozen record of one decision cycle. Every field
is a fixed enum, a bool, an integer count, or a monotonic offset. **There is no
field capable of carrying free-form broker text**, which a test asserts
structurally rather than by naming forbidden words.

It carries read availability per component (account, position, order, session),
cash-availability classification, position classification, market session,
related and executable order counts, protective-candidate count, protection
observation, read timestamps, and two contradiction flags.

It distinguishes known-safe, known-unsafe, unknown, stale, conflicting, and
unavailable. **Unknown is never treated as safe.**

## Fail-closed decision model

`decide_broker_state` is pure — no I/O, no session, no broker, no `await`,
asserted by parsing the function rather than reading its prose. It returns
exactly one of:

`VERIFIED_FLAT` · `VERIFIED_ACTIVE_PROTECTION` · `UNPROTECTED_POSITION` ·
`CONFLICTING_EXECUTABLE_ORDERS` · `STALE_BROKER_STATE` ·
`INCOMPLETE_BROKER_STATE` · `UNKNOWN_BROKER_STATE` · `BROKER_READ_UNAVAILABLE`

Only `VERIFIED_FLAT` and `VERIFIED_ACTIVE_PROTECTION` permit an automated caller
to proceed.

Evaluation order is itself a safety property: availability → completeness →
freshness → internal contradiction → interpretation. A conclusion drawn from an
unavailable, partial, stale, or self-contradictory snapshot is worse than no
conclusion.

Specific guarantees, each pinned by test:

- Only an explicitly approved state can yield `VERIFIED_ACTIVE_PROTECTION`. A
  snapshot claiming `OBSERVED_APPROVED_STATE` while nothing is approved yields
  `UNKNOWN_BROKER_STATE` — the claim is refused, not trusted.
- Flat **plus** an executable related sell is `CONFLICTING_EXECUTABLE_ORDERS`,
  never `VERIFIED_FLAT`.
- A held position with no approved protection is `UNPROTECTED_POSITION`, or
  stricter when the state is unapproved or unrecognized.
- Stale, incomplete, or contradictory inputs can never produce a safe result.

## Freshness rules

`SnapshotFreshnessPolicy` defaults to a maximum snapshot age of 5 seconds and a
maximum read span of 2 seconds. Both are validated: positive, at most 60
seconds, and span never exceeding age. A cycle that cannot gather account,
position, order, and session state within a couple of seconds is not observing
one coherent moment, and an unattended system acting on a smeared snapshot is
acting on a state that never existed.

Timestamps that do not move forward across the cycle yield `UNKNOWN`, not a
guess.

## Privacy rules

Snapshot facts are an allowlist of fixed keys and fixed values. Counts are
reported as `none` / `one` / `many` buckets — an exact tally of broker orders is
not decision-relevant and an exact count is one more thing to leak.
`validate_snapshot_facts` rejects any unknown key, any value outside its
allowlist, and any non-mapping input.

Never represented: account numbers, buying-power amounts, balances, order
identifiers, broker symbols, prices, tokens, headers, raw exception text, raw
responses, or request arguments.

## The read-only observation checkpoint

Designed here, and **executed once on 2026-08-03** — see the completion record
above. `ReadOnlyObservationPlan` encodes the constraints in tracked code so they
were reviewable in advance rather than argued about next to a live terminal.

- Permitted tools: `get_accounts`, `get_equity_positions`, `get_equity_orders` —
  one call each.
- Order placements, cancellations, and replacements are fixed at **zero**, and
  the constructor raises if any is non-zero.
- Write tools are named in `READ_ONLY_CHECKPOINT_PROHIBITED_TOOLS` and rejected
  from the plan by construction.
- No strategy activation and no market-data subscription that could initiate
  trading.

It observes a protective stop **only if one already exists** from separate,
safe circumstances. If none exists it reports `NOT_OBSERVED` and stops.
`manufactures_observation=True` raises: **a live trade placed to generate an
observation would be a write, and this milestone performs none.**

This constraint is permanent and survives the completed run. The 2026-08-03
checkpoint found a flat account and therefore observed nothing, and the correct
response to that is to **wait for naturally occurring protective state** — not
to open a position, place a stop, or otherwise manufacture something to look
at. **A protective order must never be created merely to generate an
observation.** Doing so would be a broker write, would defeat the entire claim
of this milestone, and would produce evidence about a stop the system itself
arranged rather than about how the broker behaves.

## Zero-write guarantee

Asserted, not claimed:

- the module parses with no `await` and no call to any broker or session method;
- it imports no MCP, client, session, or adapter module;
- `decide_broker_state` contains no I/O call when inspected via its AST;
- the plan object refuses every write-shaped configuration.

## The real observation runtime

`src/read_only_broker_state.py` defines what a snapshot *means*.
`src/read_only_observation_runtime.py` is what actually produces one from a
real broker, and it is deliberately the narrowest thing that can.

### The capability surface is a partition, not a filter

All 49 advertised broker operations are classified in
`MCP_TOOL_CLASSIFICATION`. A tool **absent** from the mapping is refused, so a
newly advertised operation cannot be reached before it is reviewed.

| Capability | Count | May be invoked? |
| --- | --- | --- |
| `READ_ONLY_PERMITTED` | 3 | Yes |
| `UNNECESSARY` | 27 | No |
| `AMBIGUOUS_CAPABILITY` | 4 | No |
| `WRITE_CAPABLE` | 15 | No |

Ambiguity is grouped with proven writes on purpose. "We could not establish
that this is read-only" and "this writes" must produce identical behaviour,
because the cost of being wrong is identical.

`review_equity_order` is the notable case. Attended commissioning permits it;
this checkpoint does not. Its description says non-committing, but this
repository has never proven it leaves no broker-side artifact, and a checkpoint
whose entire claim is *zero writes* cannot rest on a description. The
divergence is deliberate and pinned by test: the write path accepts review
because it is about to write anyway.

### Two things are deliberately not read

- **Cash.** `get_accounts` carries no cash, balance, or buying-power field —
  its record is identity and status only. `get_portfolio` would carry one and
  is classified `UNNECESSARY`. So cash availability is reported `UNKNOWN`,
  honestly, rather than sourced by widening the surface for a value that
  grounding active protection does not need. `decide_broker_state` does not
  gate on it, and `UNKNOWN` is never treated as sufficient.
- **Market session.** Taken from a local exchange-calendar equivalent, not a
  broker or market-data read. A year outside the modelled table, a date past
  its expiry, or a naive timestamp yields `UNKNOWN` — never "probably open".

This closes step 6 of the definition of done for position, order, and
market-session acquisition, and closes it *honestly* for buying power: the
snapshot reports that it does not know, instead of guessing or reaching
further than a read-only checkpoint should.

### Zero writes, asserted structurally

- The reader exposes exactly `read_accounts`, `read_positions`, `read_orders`
  — no placement, cancellation, replacement, strategy activation, or generic
  `call_tool` passthrough to implement or misuse.
- The module contains **exactly three** `call_tool` sites, each naming a
  module-level constant that resolves to a permitted read at parse time. There
  is no path where a caller-supplied or response-supplied string becomes a
  tool name. The repository-wide call-site guard sees all three as `literal`.
- The module imports nothing write-capable — no adapter, client, session, MCP,
  or orchestrator — so there is no write-capable object in reach to fall back
  to. Asserted by parsing the imports.
- Write-tool names appear in the file **only** as classification literals, and
  a test proves the textual count matches the denylist count exactly.
- Evidence carries `placement_writes`, `cancellation_writes`,
  `replacement_writes`, and `strategy_activations`, all required to be exactly
  zero, and refuses to persist a recorded call to a non-permitted tool.

### Failure is never relabelled

A *recognized* read failure degrades one component to `UNAVAILABLE`. A
capability violation and an unrecognized exception both escape instead, and
become `CAPABILITY_VIOLATION` or `INTERNAL_INVARIANT_FAILURE`. Neither may be
quietly reported as "the broker was unavailable" — that label reads as a
normal, expected condition, and these are not.

## Corrections from independent review

Six findings were raised against the first two commits and are now fixed. Each
is pinned by focused regression tests.

**F1 — the session handle was public.** `RestrictedReadOnlyReader` held
`session` as a public dataclass field, so a generic write-capable call was one
attribute access away from any future caller, and the docstring's claim that
it was "held privately" was simply false. It is now a plain class with
`__slots__` and a private `_session`; there is no `__dict__` to attach a
public alias to, and `__repr__` renders neither the session nor the account.

**F3 — early closes were classified as regular sessions.** At 14:00 ET on a
half-day the calendar returned `REGULAR_HOURS` while the market was shut.
`2026-11-27`, `2026-12-24`, and `2027-11-26` now close the regular session at
13:00 ET. After an early close the result is `CLOSED` rather than
`EXTENDED_HOURS`: post-close extended trading does exist on those days, but
this table cannot bound it, and reporting a session it cannot bound would
repeat the original error. A test sweeps every day in 2026–2027 and asserts no
closed day ever reads as open. The table also now **expires**: nothing after
`2027-12-31` classifies as anything but `UNKNOWN`.

**F4 — the limitation was neither displayed nor recorded.** Evidence now
carries `calendar_source`, `calendar_supported_range`, `calendar_limitation`,
and `calendar_expires_after`, and the launcher prints all four plus an
explicit note that there is no authoritative feed. A reviewer never has to
infer where a `market_session` value came from.

**F5 — a declined operator had already opened a session.** The session was
opened first and the operator prompted inside it, so a decline authenticated a
session it never used. Authorization is now fully resolved *before* any
transport exists, in both modes, via `PreResolvedAuthorization`. A test drives
the declined path with a session factory that raises on entry and asserts it
was entered **zero** times.

**F6 — session open and close failures escaped as tracebacks.**
`asyncio.run(...)` was unwrapped, so a transport failure printed a raw
traceback, produced no evidence, and returned no controlled code. Both phases
now yield the `SESSION_UNAVAILABLE` outcome with
`INVESTIGATE_SESSION_OPEN`/`INVESTIGATE_SESSION_CLOSE`, a nonzero exit, and
privacy-safe evidence where the run identity is still unused. Tests raise from
the fake transport with a secret embedded in the message and assert none of it
reaches evidence or disk. A close failure keeps the observation already
written rather than rewriting immutable history.

**F7 — an unknown broker state was persisted verbatim.**
`observed_protection_state` was the one evidence field that accepted arbitrary
broker text. It is now constrained to `KNOWN_ORDER_STATES` plus the approved
set, with `UNRECOGNIZED` recorded for anything else. Known states are still
recorded verbatim — that is the point of the checkpoint — and the approved set
is folded in dynamically so a later promotion cannot make a state unrecordable.

### Twelve outcomes, two of which are completions

`OBSERVATION_RECORDED` · `NO_PROTECTIVE_ORDER_OBSERVED` ·
`PROTECTION_AMBIGUOUS` · `ATTRIBUTION_AMBIGUOUS` · `BROKER_READ_UNAVAILABLE` ·
`SNAPSHOT_NOT_FRESH` · `DECLINED_BY_OPERATOR` · `AUTHORIZATION_REJECTED` ·
`PRECONDITION_REJECTED` · `CAPABILITY_VIOLATION` · `SESSION_UNAVAILABLE` ·
`INTERNAL_INVARIANT_FAILURE`

`SESSION_UNAVAILABLE` is kept distinct from `BROKER_READ_UNAVAILABLE` on
purpose: "no session" and "a session, but the read failed" call for different
operator action.

Classification order is the same safety property as in the decision model:
availability → coherence → attribution → interpretation. Reading a protection
state out of an unavailable, stale, or unattributable snapshot would be worse
than reporting nothing, because it would look like a result.

Attribution is by exact normalized symbol. Nothing is inferred from ordering,
recency, quantity coincidence, or "the only order there is" — an
unattributable observation is reported ambiguous rather than assigned to the
symbol we happened to be asking about. An unrecognized order state counts as
*live*, not harmless.

### Neither completion approves anything

`OBSERVATION_RECORDED` records a state and proposes at most
`READ_ONLY_LIVE_OBSERVATION`. The runner raises rather than emit
`APPROVED_FOR_ACTIVE_PROTECTION`, and a test asserts that running a full clean
checkpoint against an existing `confirmed` stop leaves
`ADAPTER_PERMITTED_ACTIVE_PROTECTION_STATES` empty. Recording an observation
is not approving it.

### The launcher

`read_only_observation_checkpoint_launcher.py` is **tracked**, not an
untracked day-of wrapper. A commissioning wrapper carries only a date and a
Run ID; this carries reviewed safety behaviour, so it belongs in review.

Execution requires all of: the
`--i-understand-this-performs-real-broker-reads` acknowledgment,
`ENABLE_MILESTONE_9_READ_ONLY_OBSERVATION=1`, the authorization flag, an
unused Run ID, and **exactly one** authorization mode.

The session object never leaves the launcher's session scope: the runtime
receives only the restricted reader. The session factory is imported inside
the function so that importing the launcher — which tests do — never reaches
transport code.

## Authorization: two modes, no default

Repeated `y/N` prompts do not survive contact with scheduled execution, and
the approved destination is unattended operation. So there are two modes, and
supplying **neither is refused** — a scheduled run must never silently fall
back to a prompt nobody is present to answer, and a manual run must never
silently skip the manifest. Supplying **both** is also refused.

`ENABLE_MILESTONE_9_READ_ONLY_OBSERVATION=1` remains necessary and remains
**insufficient**. Environment presence is never itself approval.

### `--interactive` — manual development

Two hidden `y/N` prompts, unchanged. Only exact `y`/`Y` approves; empty,
`yes`, `Y `, EOF, and an unreadable prompt all decline. Each stage may be
asked once; a second request is a rejection, not a re-prompt. Both are
answered **before any read occurs**, pinned by a test asserting the reader is
never called on decline.

### `--authorization-manifest` — scheduled, unattended

An operator-issued JSON document, validated and consumed **before** the
checkpoint starts, so an invalid or spent manifest costs zero broker reads.

`src/read_only_authorization_manifest.py` is deliberately **not** an
always-yes mode. It authorizes one checkpoint identity, once. Nothing in it
produces the string `y`, and a test parses the launcher's AST to assert no
`"y"`, `"Y"`, or `"yes"` literal exists anywhere in it.

Every field is pinned and independently enforced — each one alone can refuse:

| Field | Rule |
| --- | --- |
| `scope` | exactly `READ_ONLY_BROKER_OBSERVATION` |
| `approved_main_sha` | must equal resolved `HEAD` |
| `checkpoint_implementation_digest` | must equal the digest over 5 pinned files |
| `allowed_broker_operations` | exactly the three reviewed reads, no duplicates |
| `write_limits` | all four counters exactly `0`, exact key set |
| `maximum_sessions` | exactly `1` |
| `checkpoint_observation_id` | must equal this run's identity |
| `issued_at` / `expires_at` | timezone-aware, window ≤ 24h, currently valid |
| `issued_by` | refused if it names an agent or automation |
| field set | exact — missing **and** unexpected keys both refused |

The implementation digest covers `read_only_observation_runtime`,
`read_only_broker_state`, `read_only_authorization_manifest`,
`live_order_state_model`, and the launcher. Change any of them and every
previously issued manifest stops validating — an operator approved *this
implementation*, not this file name.

`HEAD` is resolved by reading the git directory directly. No subprocess: this
is the authorization path, and shelling out there would add a process-spawn
surface to the one module whose value is being small enough to audit.

### One use, recorded outside evidence

`ManifestConsumptionLedger` records spent identities in a directory supplied
separately from `--evidence-root`. This is deliberate. Evidence records what a
run *saw*; the ledger is a control input governing whether a run may *happen*.
Storing the second inside the first would mean clearing evidence silently
restores authorization — the same class of gap that retired Run IDs closed for
commissioning. Consumption is directory creation, atomic on POSIX and Windows,
so two concurrent launches cannot both win. A manifest that fails validation
is **not** consumed: a refusal must not burn an identity the operator can
legitimately reissue.

### The agent never authors its own authorization

There is no template generator, no renewal helper, and no serializer. A test
walks the module's AST for any call that could *emit* a manifest — `open`,
`write_text`, `write_bytes`, `json.dump`, `json.dumps` — and asserts there are
none. The only permitted filesystem mutation is the ledger's `mkdir`, which
*spends* authorization rather than granting it. A second test sweeps all of
`src/` for anything that both mentions the scope and serializes JSON.

### Unattended is a validated mode, not a skipped check

`ReadOnlyCheckpointRequest` carries `authorization_mode`. `ATTENDED_MODE`
requires an operator present; `UNATTENDED_MODE` does not, because its approval
is a manifest issued earlier rather than a person now. An unrecognized mode is
refused. "No operator at the keyboard" is therefore first-class and
validated — not a precondition quietly bypassed.

## What this manifest is not

A future unattended **write** milestone needs a different, richer policy.
`UNATTENDED_WRITE_POLICY_REQUIRED_FIELDS` names its required contents —
capital tier, strategy version, symbol allowlist, position and exposure
limits, daily/weekly/drawdown limits, permitted trading window, deployment
expiry, global kill-switch state.

That is a **specification, not an implementation**. Nothing reads or honours
those fields today, none of them may appear in a read-only manifest, and
`assert_not_write_authorization` refuses a manifest that carries one. This
format cannot be widened into write authorization: a write-enabled runtime
must be authorized by an object that does not exist yet, reviewed on its own
terms. **The trading agent may never create, renew, broaden, or raise its own
authorization.**

## Definition of done for Milestone 9

Milestone 9 is complete only when **all** of the following hold:

1. ~~The offline foundation is reviewed and merged.~~ **Done** — PR #43.
2. ~~A reviewed attended read-only checkpoint executes against a real account,
   performing only the permitted reads and zero writes.~~ **Done** —
   2026-08-03, run `m9-obs-20260803-01`: one session, three permitted reads,
   zero writes.
3. ~~Its sanitized observations are recorded, including `NOT_OBSERVED` if no
   safe protective order existed.~~ **Done** — the account was verified flat,
   `NOT_OBSERVED` was recorded, and the evidence passed privacy validation.
   This is the branch of step 3 the definition explicitly allows for.
4. A human reviews those observations and explicitly approves zero or more
   states, promoting them to `APPROVED_FOR_ACTIVE_PROTECTION`. **Not done, and
   not yet actionable** — the completed run observed no protective state, so
   there is nothing to review for promotion. This step is now waiting on
   **naturally occurring** protective state from a future checkpoint, not on
   software.
5. `ADAPTER_PERMITTED_ACTIVE_PROTECTION_STATES` is updated to match, with the
   consistency test still passing. **Not done** — the set is still empty, which
   is the correct state given step 4.
6. ~~Reliable position, order, buying-power, and market-session snapshot
   acquisition is wired to real reads.~~ **Done**, with buying power closed
   honestly as `UNKNOWN` rather than by widening the read surface — see above.

Steps 4 and 5 require an operator at a terminal and are **not** software work.
Approving zero states remains a legitimate outcome: if the observations do not
justify approval — including because there were none — the mapping stays empty
and unattended operation stays blocked on it. **Protection grounding is not
complete**, and the completed checkpoint did not advance it; it established
that the read path works end to end against a real account and that the account
was flat.

## The re-pin window

Four deferred changes to the digest-pinned files were batched and landed
together, so the pin moved **once** rather than once per improvement. The
window was opened deliberately at the cheapest possible moment: the only
manifest ever issued was already spent, so no outstanding authorization was
invalidated by moving the digest.

### F2 — the confirmation read

Positions and orders were each read once, and
`order_state_changed_during_acquisition` was populated from the *market
session* rather than from any comparison of orders. The name asserted something
the code did not measure.

Orders are now read twice and the two are compared on an attributed, normalized
projection — the set of `(identifier, normalized state)` pairs for the observed
symbol, plus the executable-sell and protective-candidate counts. Raw payloads
are never compared: they differ every cycle in fields nothing depends on.

- Any difference sets the flag truthfully and yields `UNKNOWN_BROKER_STATE`.
- A change on an unrelated symbol does not set it.
- Read ordering does not set it — the projection is a set.
- A failed confirmation read degrades orders to `UNAVAILABLE`, never "no
  change": not knowing whether the set moved is not knowing that it did not.
- **Report, never retry.** A detected change ends the cycle. Retrying would
  widen the span, and deciding whether to back off belongs to the Milestone 10
  scheduler, not to an observation primitive.

Fields now say what they measure: `position_observations_disagreed` became
`position_response_self_contradictory` (positions are read once; the flag only
ever described a self-contradictory single response), and
`market_session_changed_during_acquisition` is its own field.

**Positions are still read once**, so intra-cycle *position* consistency
remains outside the guarantee, exactly as the accepted design specifies. Order
changes are the leading indicator — position changes are a consequence of order
execution — and re-reading positions would double the added span for a strictly
weaker signal.

The budget is four calls: `get_equity_orders` twice, the other two once each.
The ceiling is **per tool**, not uniform, so a change here cannot silently grant
`get_accounts` or `get_equity_positions` a second call. No new tool was added —
the confirmation reuses an already-permitted read — and the module still has
exactly three literal `call_tool` sites. The span cap still wins: four reads
that do not fit inside it are `SPAN_TOO_WIDE`.

Order identifiers are held in memory to make the comparison possible. They are
never persisted, and a test asserts one cannot reach evidence.

### Authoritative calendar delegation

`classify_market_session` no longer keeps a private copy of the holiday and
early-close tables; it delegates to `src/exchange_calendar.py`. The temporary
drift guard that kept the two copies in step is retired in the same change, as
its own docstring required — replaced by a test asserting the private names are
gone.

Behaviour is preserved and is stricter in one respect: a wall time that does not
name a single instant across a DST fold is now `UNKNOWN` rather than classified.
Unsupported, expired, malformed, ambiguous, and naive inputs remain
non-tradeable; early closes remain correct. Evidence metadata is preserved and
now derived from `SCHEDULE_METADATA`, so a calendar extension cannot leave a
stale claim behind; `calendar_source` reads
`authoritative_static_exchange_calendar`.

### Lifecycle-model adoption

`src/order_lifecycle_model.py` is adopted in the narrowest way that is still
load-bearing: it may **veto** a protection claim and may never grant one.

If the two vocabularies disagree — this module reading a label as live and
workable while the lifecycle model positively refuses it as terminal, replaced,
or unrecognized — the disagreement is itself disqualifying and the safe reading
wins.

It deliberately does **not** veto `WORKING`. `WORKING` is the state a future
grounded approval would legitimately name, and vetoing it would make step 5 of
the definition of done unreachable: populating
`ADAPTER_PERMITTED_ACTIVE_PROTECTION_STATES` would no longer enable anything.
Adoption must not quietly foreclose the milestone it serves. A test proves an
approval of `confirmed` still works end to end.

Nothing was promoted. The approved set, the lifecycle-approved set, and
`PROVENANCE_SUFFICIENT_FOR_PROTECTION` are all still empty, and no projection
can assert protection or permit automated action.

### Manifest encoding, and the ordering hazard behind it

`load_manifest` read with `encoding="utf-8"`, so a manifest saved with a UTF-8
byte-order mark was refused as `manifest is not valid JSON`. PowerShell's `>`,
`Out-File`, and `Set-Content` all emit a BOM by default, which made this the
default outcome of authoring a manifest the obvious way on Windows. It cost a
preparation attempt on 2026-08-03.

It is now read with `utf-8-sig`, which accepts UTF-8 with or without a BOM and
is otherwise identical. **Nothing else is relaxed**: malformed JSON, a missing
key, an unexpected key, a wrong commit, and a wrong digest all still refuse, and
a file that is not UTF-8 at all is refused rather than escaping as a decoding
traceback. This was never a safety defect — it failed closed in `load_manifest`,
before `ManifestAuthorization` is constructed, so nothing was consumed and the
identity stayed reusable.

The same preparation exposed a real ordering hazard. `main()` ran
`build_authorization` — which loads **and consumes** the manifest — before the
request was validated, so a blank account number alongside a perfectly good
manifest would spend the manifest and the Run ID and then finish as
`PRECONDITION_REJECTED`. The authorization would have been burned to discover a
typo.

Everything checkable without a broker, a session, or a secret is now checked
first, in `assert_preconditions_before_authorization`: authorization flag, mode,
operator presence, account identity, symbol, quantity, plan, Run ID path safety,
and whether the Run ID is already used. A refusal there costs nothing. The
in-runner `validate()` still runs — this is an earlier gate, not a replacement,
because a caller reaching the runner by another path must still be validated.

Authorization is still resolved **before** any transport exists, which is what
F5 fixed and which this preserves.

### Open finding: the digest does not cover everything the checkpoint reaches

Raised by review of the re-pin window, **not fixed**, because fixing it changes
what an operator is asked to approve.

`checkpoint_implementation_digest` covers five files. Those five can reach
fifteen more by import, and a change to any of them alters what the checkpoint
does **without moving the digest** — so a manifest issued earlier would still
validate against changed behaviour. The digest's claim is that an operator
approved *this implementation*; today that claim is narrower than it sounds.

Most of the gap is structural and long-standing rather than new. The launcher
must reach a session factory to open a session at all, and that factory pulls in
the commissioning transport chain: `robinhood_commissioning_session`,
`robinhood_commissioning_adapter`, `robinhood_client`, `one_share_commissioning`
and its orchestrator, `broker_action_saga`, `broker_identity`, `broker_snapshot`,
`trade_state`, `config`, `canonical_source_hash`, `live_broker_capabilities`,
and `live_order_contract`. This window added exactly two:
`exchange_calendar` and `order_lifecycle_model`.

What is in scope here is a guard, and it has been added: the reviewed closure is
frozen in `REVIEWED_UNPINNED_CLOSURE`, and a test fails if it grows. A pinned
file cannot quietly gain a new import without a human seeing that the digest's
guarantee just got weaker.

**Widening `PINNED_IMPLEMENTATION_FILES` is an operator decision.** It would
change the authorization contract, and it is not something to do as a side
effect of a test. Two directions are worth considering, and neither should be
chosen here:

1. Extend the pinned set to the full closure. Strongest, and makes the digest
   move whenever anything reachable changes — including routine edits to
   `config.py`, which would invalidate manifests often.
2. Extract the launcher's session-factory import behind a narrow reviewed
   boundary, shrinking the closure rather than pinning all of it.

Separately, and unrelated to this window: `src/config.py` and
`tests/test_robinhood_live_adapter.py` are stored with UTF-8 byte-order marks.
Python imports them correctly, so nothing is broken, but tooling that parses
them as plain UTF-8 will fail. Cosmetic, untouched here, noted so it is not
rediscovered as a mystery.
