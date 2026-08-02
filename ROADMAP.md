# ORB-LIVE Roadmap

This document separates what has been approved and built from what has
deliberately not been built, and states the one product decision that is
currently open.

## Current position

| Milestone | Status |
| --- | --- |
| **Milestone 7** — attended tiny-risk live-write commissioning | **Complete.** Validated against a real broker. See [`MILESTONE_7_COMPLETION_RECORD.md`](MILESTONE_7_COMPLETION_RECORD.md). |
| **Milestone 8** — tracked project record and formal Milestone 7 closure | **Complete on merge of this documentation-only change.** No runtime, test, or configuration file is touched. |

ORB-LIVE **today** is a strategy-research, simulation, shadow-runtime, and
attended live-commissioning platform. Every live broker write currently requires
an operator present at a terminal. That is the present state, not the approved
destination — see below.

## Not yet built

None of the following exists today, and none is an accidental omission or an
unfinished TODO. Each was explicitly excluded from Milestone 7 and is scheduled
into the ordered milestones below. **Nothing here may be built ahead of its
milestone.**

- **Unattended trading.** No path performs a live write without a human
  answering authorization prompts.
- **Scheduler.** Nothing starts a trading session on a timer.
- **Signal-to-live-execution bridge.** The strategy stack and the commissioning
  stack are disconnected; `broker_saga_executor` refuses any transport that does
  not self-declare as simulated.
- **Persistent strategy enablement.** `strategy.enabled` remains `false`.
- **Automated live exits.** Stop-to-breakeven, target, and time-based exits are
  defined in `config/strategy_v0.yaml` for research and simulation only.
- **Global live-execution switch.** No configuration flag turns live trading on.
- **Runtime monitoring and kill switch.** No supervisory process can observe or
  halt a live run.
- **Broker-connected dashboard.** `site/` is statically bundled documentation
  with no backend, no live data, and no trading controls.

## Known gap: active-protection grounding

This is dormant today and must be resolved before any automated, unattended, or
larger-size live operation.

- `ADAPTER_PERMITTED_ACTIVE_PROTECTION_STATES` in
  `src/live_order_state_model.py` is an **empty set**.
- Consequently `VERIFIED_ACTIVE_PROTECTION` is **not currently obtainable** from
  that adapter mapping: no working stop state can be asserted as armed and
  resting at the venue, and every such state assesses as indeterminate.
- Attended commissioning does not depend on it. That path uses a separate
  **provisional-protection assessment**, which is what the completed live runs
  recorded. The gap therefore **does not invalidate** the completed one-share
  attended commissioning, where an operator is watching a single share.
- It **becomes a blocker** the moment protection must be trusted without a human
  watching, or at a size where being wrong matters — which is exactly the
  approved destination.
- Grounding it requires a **non-order, read-only adapter-initialization
  checkpoint**. The mapping must not be inferred from the advertised schema
  alone, nor from a single observation. The source file states this requirement
  directly, and that constraint is intentional.
- **This is the subject of Milestone 9**, the first milestone on the path to
  automation.

## Approved product destination

**Fully unattended automated ORB strategy execution.**

The attended one-share commissioning completed in Milestone 7 is a **completed
prerequisite and safety foundation, not the endpoint**. It proved that this
software can place, protect, cancel, exit, and verify flat against a real broker
without a duplicate or ambiguous write. Automation builds on that foundation; it
does not replace the fail-closed behavior established there.

### Authorized capital

**$2,680 is the initial pilot deployment ceiling — not a permanent system
maximum.**

ORB-LIVE must support an **operator-configurable authorized capital ceiling**,
including future tiers of **$30,000 or more**. No single dollar amount defines
this system, and no dollar amount may be hard-coded as a permanent limit.

A ceiling is **not a guaranteed maximum loss.** Broker behavior, overnight and
intraday gaps, slippage, venue outages, API failures, and unresolved order
states can produce losses outside any intended software limit. No configuration
value in this repository can promise otherwise. The system's obligation is to
*minimize* that exposure through cash-only operation, strict position and loss
limits, fail-closed controls, monitoring, and automatic suspension — not to
guarantee a bound.

#### Rules that govern every tier

- Capital ceilings are **configuration-driven, validated, versioned, and
  auditable**. They are never derived at runtime from the account balance.
- **The trading agent may never raise its own authorized capital.** No code path
  may widen its own limit, and no automatic escalation of any kind is permitted.
- **Active deployment capital may be lower** than the account balance and lower
  than the authorized ceiling. The ceiling is a bound, not a target.
- Moving to a higher tier requires an **explicit operator-approved configuration
  change**, reviewed like code.
- **Risk-per-trade, position size, daily loss, weekly loss, total drawdown, and
  simultaneous-position limits must be recalculated coherently for each capital
  tier.** They may not be carried across tiers unchanged, and a tier whose limits
  are incoherent against its ceiling must be rejected rather than accepted.
- **A clean trial period does not automatically unlock a larger tier.** A
  successful week is evidence *supporting* review, never a trigger. Promotion is
  a human decision.
- `strategy.enabled` remains `false` until the unattended activation milestone,
  regardless of tier.

**$2,680 remains the initial pilot tier** until a later scaling milestone
explicitly approves a higher one. The full pilot ceiling is never authorized for
a first unattended run.

## Ordered milestones to automation

Each milestone depends on the one before it. None may be skipped, and none is
approved to begin before its predecessor is complete and reviewed.

### Milestone 9 — Read-Only Broker-State Grounding — **IN PROGRESS, HELD AT AN OPERATOR GATE**

> **⛔ OPERATOR GATE — the real-account read-only checkpoint is ON HOLD.**
>
> All software for step 2 of the definition of done is merged and verified.
> **It has never been executed against a real account, and it will not be until
> an operator explicitly lifts this hold.** Nothing in this repository may
> execute it, open a broker session, or issue an authorization manifest.
>
> Required pins for the eventual run:
>
> | Pin | Value |
> | --- | --- |
> | `checkpoint_implementation_digest` | `8516057134acc1540bfa90f76fc9f5663588fcff53d5d08d07d20d4c79131faf` |
> | `approved_main_sha` | **re-derive from `git rev-parse HEAD` at manifest-issue time** |
>
> The digest is the durable pin: it changes only when one of the five files in
> `PINNED_IMPLEMENTATION_FILES` changes. `approved_main_sha` must equal the
> resolved `HEAD`, so **any** commit to `main` — including documentation —
> moves it. It was `4fc75b6be7419d369c3c72dac046f23842eb3f6d` at the moment PR
> #44 merged. Do not copy that value forward; re-derive it.
>
> See [`MILESTONE_9_F2_ACQUISITION_CONSISTENCY_DESIGN.md`](MILESTONE_9_F2_ACQUISITION_CONSISTENCY_DESIGN.md)
> for why work that would touch a pinned file is batched into a single re-pin
> window rather than landed piecemeal.

The offline foundation — provenance framework, sanitized snapshot model,
fail-closed decision API, freshness policy, and the read-only checkpoint design
— is implemented in `src/read_only_broker_state.py`. The real observation
runtime that produces a snapshot from an actual broker, its
capability-restricted reader, and the tracked attended launcher are in
`src/read_only_observation_runtime.py` and
`read_only_observation_checkpoint_launcher.py`.

**No state has been approved for active protection, and the attended read-only
checkpoint has not been executed against a real account.** Every test is
fake-only. See
[`MILESTONE_9_READ_ONLY_BROKER_STATE_GROUNDING.md`](MILESTONE_9_READ_ONLY_BROKER_STATE_GROUNDING.md)
for the definition of done.

- Ground active-protection status using **non-order broker reads** only.
- Document the actual Robinhood order states observed, rather than inferring
  them from the advertised schema.
- **Keep all writes disabled.** All 49 advertised broker operations are
  classified; only 3 may be invoked, and an operation whose write capability is
  merely *ambiguous* is refused as strictly as a proven write.
- Establish reliable position, order, buying-power, and market-session
  snapshots. Buying power is closed **honestly**: no permitted read carries a
  balance, so cash availability reports `UNKNOWN` rather than widening the read
  surface for a value the decision does not use.
- Fail closed on unknown or conflicting states.

- Support **non-interactive authorization** so the checkpoint can run on a
  schedule. An operator-issued manifest pins scope, commit, implementation
  digest, capability list, zero write limits, one session, observation
  identity, and an expiry window; it is validated and consumed before any
  read. It is not an always-yes mode and nothing auto-answers a prompt.

Remaining: an operator must lift the hold above, issue a manifest, execute the
checkpoint, and review what it records. That is not software work, and
**approving zero states is a legitimate result.**

### Work that proceeds while the checkpoint is held

Every item below is buildable offline against fixtures and touches **no**
pinned file, so none of it disturbs the held checkpoint's digest.

| Order | Work | Lands in | Depends on |
| --- | --- | --- | --- |
| A | F2 design + re-pin policy | documentation | — |
| B | Authoritative exchange calendar | `src/exchange_calendar.py` | — |
| C | Order lifecycle model | `src/order_lifecycle_model.py` | — |
| D | Offline reconciliation and restart recovery | new module | C |
| E | Hard risk controls and kill switch | `src/risk_limits.py` | — |
| F | Operational monitoring and privacy-safe alerts | new module | E |
| G | Supervised-execution gate definition, **inert** | new module | — |
| H | Limited-unattended gate definition, **inert** | same module as G | G |

The gate definitions take already-decided facts rather than importing the risk,
recovery, lifecycle, and monitoring modules. That keeps each review separate,
and stops a gate from re-deriving inputs it could then silently disagree with.

**Blocked by the real-account checkpoint** — steps 3–5 of the Milestone 9
definition of done, anything requiring `VERIFIED_ACTIVE_PROTECTION` to be
reachable, Milestone 11's live-shadow phase, and all of Milestone 12.

**Deferred to a single re-pin window**, batched so the manifest is reissued
once rather than per change: F2 detection wiring, calendar delegation from
`classify_market_session`, and lifecycle-model adoption in
`live_order_state_model.py`.

**Disabled throughout, without exception:** `strategy.enabled` stays `false`;
the approved-state count stays `0`; exactly three read tools remain permitted;
no write capability, scheduler, or signal-to-execution bridge exists; and no
authorization manifest is issued.

### Authorization policy, across milestones

The read-only manifest authorizes **reads only** and cannot be widened.
Milestones 12 and 13 require a **separate** write-enabled policy carrying
operator-approved capital tier, strategy version, symbol allowlist, position
and exposure limits, daily/weekly/drawdown limits, permitted trading window,
deployment expiry, and global kill-switch state. Its required fields are named
in `src/read_only_authorization_manifest.py` as a specification only — nothing
implements or honours them, and a read-only manifest carrying any of them is
refused.

**The trading agent may never create, renew, broaden, or raise its own
authorization**, in any milestone. Authorization is always an operator input.

*Why first:* every later milestone depends on being able to assert that a
protective stop is genuinely armed. Today that assertion is unavailable — see
the grounding gap above — and no operator will be watching to compensate.

### Milestone 10 — Unattended Runtime Foundation

- Durable scheduler driven by the exchange calendar.
- Persistent run and position state.
- Process restart recovery.
- Idempotent execution identities.
- Stale-data detection.
- Broker and API outage handling.
- Global kill switch.
- Automatic trading suspension.
- Operator alerts.
- Health monitoring and heartbeats.
- **No live strategy activation yet.**

### Milestone 11 — Signal-to-Execution Bridge in Simulation and Shadow

- Connect `signal_engine` → `risk_engine` → saga executor.
- Simulation first.
- Then shadow execution against live read-only market and broker state.
- Verify deterministic sizing and risk decisions.
- **No live order writes.**

### Milestone 12 — Unattended Tiny-Live Commissioning

- Cash-only.
- One symbol initially.
- One position maximum.
- Very small authorized capital.
- Automatic entry, protection, exit, recovery, and alerts.
- Staged unattended operation.
- Immediate automatic suspension on ambiguity.
- Scale only after evidence-based review.

### Milestone 13 — Capital Tiering and Scaling

- Implement the **operator-configurable, validated, versioned, auditable**
  capital-tier model described above. No hard-coded permanent amount.
- Derive per-trade, position-size, daily-loss, weekly-loss, total-drawdown, and
  simultaneous-position limits **coherently for each tier**, and reject a tier
  whose limits are incoherent against its ceiling.
- Increase deployment gradually, starting from the $2,680 pilot tier and
  extending to higher operator-approved tiers such as $30,000 or more.
- **Never enable all capital in the first unattended run.**
- **The agent may never raise its own ceiling**; every promotion is an explicit
  operator-approved configuration change.
- Preserve cash-only operation.
- Retain automatic suspension and operator override.
- Require reviewed evidence before every scaling tier; a clean trial period
  supports review but never unlocks a tier on its own.

## Known configuration debt

`config/strategy_v0.yaml` was sized for one-share attended commissioning, not
for capital deployment, and it encodes limits as flat absolute dollars with no
notion of a tier. At least one value already conflicts with the pilot ceiling:
`maximum_position_usd: 3000` exceeds $2,680. The per-trade, daily, weekly, and
total-drawdown limits (`risk_per_trade_usd: 15`, `daily_loss_limit_usd: 45`,
`weekly_loss_limit_usd: 120`, `total_drawdown_halt_usd: 300`) were likewise
chosen for a single share, as were `maximum_open_positions: 1` and
`maximum_entries_per_day: 2`.

These must be deliberately re-derived per tier under Milestone 13 rather than
adjusted ad hoc, and the tier model must replace flat absolute constants.
`strategy.enabled` remains `false` until a milestone explicitly authorizes
changing it, as required by `CLAUDE.md`.

## Historical artifact disposition

`checkpoint_7c_adapter_initialization_launcher.py` and its accompanying test
module are archived local acceptance artifacts for a superseded
initialization-only launcher. They remain **outside the authoritative tracked
test suite**:

- `pytest.ini` intentionally excludes the test module so a local run collects
  exactly what CI collects;
- its historical commit pin **must not be silently updated** — re-pinning would
  destroy the property it asserts;
- successor coverage against the tracked commissioning launcher is
  authoritative.

The reasoning and a test-by-test coverage mapping are in
[`CHECKPOINT_7C_FINAL_EVIDENCE_PERSISTENCE_HOUSEKEEPING.md`](CHECKPOINT_7C_FINAL_EVIDENCE_PERSISTENCE_HOUSEKEEPING.md).

Other untracked material in the working directory — operator wrappers, handoffs,
proposals, and evidence artifacts — is preserved local history and is not part
of the product.
