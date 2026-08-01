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

**$2,680 maximum trading capital.**

This is a **capital ceiling, not a guaranteed maximum loss.** Broker behavior,
overnight and intraday gaps, slippage, venue outages, API failures, and
unresolved order states can produce losses outside any intended software limit.
No configuration value in this repository can promise otherwise. The system's
obligation is to *minimize* that exposure through cash-only operation, strict
position and loss limits, fail-closed controls, monitoring, and automatic
suspension — not to guarantee a bound.

Capital is deployed gradually under Milestone 13. The full ceiling is never
authorized for a first unattended run.

## Ordered milestones to automation

Each milestone depends on the one before it. None may be skipped, and none is
approved to begin before its predecessor is complete and reviewed.

### Milestone 9 — Read-Only Broker-State Grounding

- Ground active-protection status using **non-order broker reads** only.
- Document the actual Robinhood order states observed, rather than inferring
  them from the advertised schema.
- **Keep all writes disabled.**
- Establish reliable position, order, buying-power, and market-session
  snapshots.
- Fail closed on unknown or conflicting states.

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

### Milestone 13 — Capital Scaling Toward $2,680

- Increase deployment gradually.
- **Never enable all capital in the first unattended run.**
- Define per-trade, daily, weekly, and total-capital limits.
- Preserve cash-only operation.
- Retain automatic suspension and operator override.
- Require reviewed evidence before every scaling tier.

## Known configuration debt

`config/strategy_v0.yaml` was sized for one-share attended commissioning, not
for capital deployment. At least one value already conflicts with the authorized
ceiling: `maximum_position_usd: 3000` exceeds $2,680. The per-trade, daily,
weekly, and total-drawdown limits (`risk_per_trade_usd: 15`,
`daily_loss_limit_usd: 45`, `weekly_loss_limit_usd: 120`,
`total_drawdown_halt_usd: 300`) were likewise chosen for a single share.

These must be deliberately re-derived under Milestone 13 rather than adjusted
ad hoc. `strategy.enabled` remains `false` until a milestone explicitly
authorizes changing it, as required by `CLAUDE.md`.

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
