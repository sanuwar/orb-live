# ORB-LIVE

A strategy-research, simulation, shadow-runtime, and **attended** live-commissioning
platform for a single opening-range-breakout equity strategy.

**ORB-LIVE does not trade by itself today.** Every live broker write that this
repository can currently perform requires a human operator present at a
terminal, answering explicit authorization prompts, for a single one-share
trade. There is no unattended mode, no scheduler, and no automated strategy
execution in the current code.

The approved destination **is** fully unattended automated ORB strategy
execution, reached through the ordered milestones in
[`ROADMAP.md`](ROADMAP.md). Attended commissioning is the completed safety
foundation for that work, not the endpoint. Nothing in the automation path may
be built ahead of its milestone.

## Status

Milestone 7 — attended tiny-risk live-write commissioning — is **complete** and
validated against a real broker. See
[`MILESTONE_7_COMPLETION_RECORD.md`](MILESTONE_7_COMPLETION_RECORD.md) for the
formal record and [`ROADMAP.md`](ROADMAP.md) for the approved destination, the
authorized capital ceiling, and the ordered milestones that lead there.

## Architecture: two intentionally disconnected stacks

The repository contains two halves that **do not connect to each other**. There
is no signal-to-live-execution bridge, and building one would require a
separately approved milestone.

### 1. Strategy and simulation stack — never reaches a real broker

| Component | Role |
| --- | --- |
| `config/strategy_v0.yaml` | The `orb_long_v0` definition: universe, signal thresholds, exit rules, risk limits. **`strategy.enabled: false`.** |
| `src/signal_engine.py`, `src/live_scanner.py` | Opening-range and relative-volume signal evaluation. |
| `src/risk_engine.py` | Position sizing and the configured risk limits. |
| `src/broker_saga_executor.py` | Durable saga execution. **Refuses any transport that does not self-declare as simulated**, and fails closed otherwise. |
| `src/simulation_runner.py`, `src/dry_run_engine.py` | Offline simulation. |
| `src/shadow_runtime.py`, `src/hypothetical_strategy_runtime.py` | Shadow evaluation against live market data with no broker writes. |

Nothing in this stack can place, cancel, or modify a real order.

### 2. Attended live-commissioning stack — the only path that can write

| Component | Role |
| --- | --- |
| `checkpoint_7c_one_share_commissioning_launcher.py` | The attended entry point. Validates repository posture, retired Run IDs, and the time policy before any session exists. |
| `src/robinhood_commissioning_session.py` | One silent MCP session, browser authentication blocked. |
| `src/robinhood_commissioning_adapter.py` | A narrow six-tool surface with one-shot write reservations. |
| `src/one_share_commissioning.py` | The fail-closed state machine and sanitized evidence store. |
| `src/one_share_commissioning_orchestrator.py` | Entry, protective stop, cancellation, exit, and bounded read-only reconciliation. |
| `src/live_order_contract.py` | Strict response parsers grounded in the advertised broker schema. |

Its sequence is: one one-share entry, an immediate protective stop, an
operator-authorized controlled flatten, one stop cancellation, one exit, and a
verified-flat completion — each with durable sanitized evidence.

## Safety posture

- `strategy.enabled=false`
- Long-only, exactly one whole share, planned risk $15
- No margin · no short selling · no fractional shares · no averaging down
- Write maximums: entry 1, protective stop 1, stop cancel 1, exit 1
- Retries: cancel 0, exit 0
- No repeated write after an ambiguous outcome
- Reconciliation after an ambiguous write is **read-only and bounded**
- Two independent hidden operator authorizations (entry, flatten); only an exact
  `y` or `Y` approves, and every prompt defaults to No
- All 20 historical live Run IDs are refused from tracked source before a
  session can open — see
  [`CHECKPOINT_7C_RETIRED_RUN_ID_ENFORCEMENT.md`](CHECKPOINT_7C_RETIRED_RUN_ID_ENFORCEMENT.md)
- Evidence is sanitized by an allowlist: no raw responses, order identifiers,
  account numbers, prices, tokens, headers, or authorization input
- Unresolved, conflicting, or ambiguous states stop and require an operator

These controls constrain *what the software may do*. They are not a guarantee
about market outcomes: a live order can still fill at an unfavourable price, a
protective stop can be passed through in a fast market, and actual loss may
exceed the planned risk. The commissioning path is deliberately sized so that a
bad outcome is small, not impossible.

## Verification status

| Check | Result |
| --- | --- |
| Authoritative tracked test suite | **2,287 passed** |
| Canonical reviewed source hashes | **9 of 9** |
| Runtime broker call-site guard | **PASS** |
| Milestone 7 live workflow | validated against a real broker |
| Final live validation run | **COMPLETE**, verified flat |

CI (`.github/workflows/tests.yml`) runs the full suite and `git diff --check` on
every pull request and every push to `main`.

## Development commands

Windows PowerShell, from the repository root:

```powershell
# One-time environment setup
py -3.12 -m venv .venv
.venv\Scripts\activate
python -m pip install -r requirements.txt
```

```powershell
# The authoritative tracked suite (pytest.ini sets collection)
python -m pytest -q

# Runtime broker call-site guard
python -m pytest -q tests/test_runtime_call_site_guard.py

# Canonical reviewed source hashes
python -c "import checkpoint_7c_one_share_commissioning_launcher as x; x.verify_reviewed_hashes(x.REPOSITORY_ROOT, x.REVIEWED_HASHES); print('hashes ok')"

# Whitespace / conflict-marker check, as CI runs it
git diff --check
```

Running `python checkpoint_7c_one_share_commissioning_launcher.py` with no
arguments is inert by design: it performs no broker, MCP, configuration, or
token I/O and refuses to proceed.

## Current limitations

Each of these is a current boundary scheduled into a specific milestone in
[`ROADMAP.md`](ROADMAP.md) — none is an accidental gap, and none may be built
ahead of its milestone:

- **No unattended scheduler.** Every live action is operator-initiated.
  *(Milestone 10.)*
- **No live signal-to-execution bridge.** The strategy stack cannot reach a
  broker. *(Milestone 11.)*
- **No persistent strategy activation.** `strategy.enabled` stays `false` until
  a milestone explicitly authorizes changing it. *(Milestone 12.)*
- **No runtime-connected operator dashboard, kill switch, or alerting.**
  *(Milestone 10.)*
- **Active-protection state mapping is ungrounded.**
  `ADAPTER_PERMITTED_ACTIVE_PROTECTION_STATES` is empty, so
  `VERIFIED_ACTIVE_PROTECTION` is not available from that adapter mapping.
  Attended commissioning uses a provisional-protection assessment instead. This
  is dormant today and is a hard blocker for unattended operation, where no
  operator is watching the stop. *(Milestone 9 — the first automation
  milestone.)*
- **`site/` is documentation and journal only.** The React dashboard is
  statically bundled, has no backend connection, no broker integration, no live
  data, and no trading controls.

## Repository layout

```
src/                     runtime modules (strategy stack and commissioning stack)
tests/                   the authoritative tracked test suite
config/strategy_v0.yaml  strategy definition (disabled)
scripts/                 offline broker-schema inspection helpers
site/                    isolated documentation dashboard (React)
CHECKPOINT_7C_*.md       tracked investigation and closure records
.github/workflows/       CI
```

Untracked files in the working directory — operator wrappers, handoffs, and
evidence artifacts — are historical local material and are not part of the
product. `pytest.ini` excludes one archived acceptance module that is pinned to
a superseded commit; see
[`CHECKPOINT_7C_FINAL_EVIDENCE_PERSISTENCE_HOUSEKEEPING.md`](CHECKPOINT_7C_FINAL_EVIDENCE_PERSISTENCE_HOUSEKEEPING.md).

## Operating rules

`CLAUDE.md` holds the permanent safety rules for anyone — human or agent —
working in this repository. Read it before making changes.
