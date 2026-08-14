# ORB-LIVE

A strategy-research, simulation, shadow-runtime, and **attended** live-commissioning
platform for a single opening-range-breakout equity strategy.

**ORB-LIVE does not trade by itself today.** Every live broker write that this
repository can currently perform requires a human operator present at a
terminal, answering explicit authorization prompts, for a single one-share
trade. There is no unattended broker-write scheduler or automated strategy execution in the current code.

The approved destination **is** fully unattended automated ORB strategy
execution, reached through the ordered milestones in
[`ROADMAP.md`](ROADMAP.md). Attended commissioning is the completed safety
foundation for that work, not the endpoint. Nothing in the automation path may
be built ahead of its milestone.

## Status

The current production operator path is bounded LIVE_SHADOW observation followed
by attended commissioning only after a genuine candidate. M24 produces a saved,
sanitized handoff for the final read-only rehearsal; it does not authorize a
broker write. See [`ROADMAP.md`](ROADMAP.md) for milestone history and planned
automation work.

## Daily ORB-LIVE Operator Flow

All commands run from the repository root. Never put secret values, raw account
identifiers, or tokens in commands, evidence, or chat.

### Before market

For the browser-based local morning flow, use the fixed launcher:

```powershell
python scripts/start_orb_live_operator.py
```

It starts or safely reuses only the local control service at 127.0.0.1:8766 and the frontend at 127.0.0.1:5173, then opens `http://127.0.0.1:5173/#/operations`. It never terminates an existing process. If a service is already listening but is not compatible, startup fails clearly. `Start ORB-LIVE.cmd` is an equivalent double-click launcher. To create a desktop shortcut, create a shortcut to that `.cmd` file; it does not add startup or registry entries.

Open the Operations page and use **Check readiness**. The service exposes only readiness, watcher start, watcher stop, and owned-watcher status. It cannot accept commands, scripts, arguments, or PIDs. The canonical CLI remains available for diagnostics:

```powershell
python scripts/check_morning_watch_readiness.py
```
Authenticated remote viewing is a separate read-only service on
`127.0.0.1:8777`; it never exposes or proxies the local-control port. Build the
remote UI, configure its scrypt password hash and HTTPS Origin outside source
control, then publish only port `8777` through an outbound Cloudflare Tunnel.
See [`M25_REMOTE_READ_ONLY_MONITORING.md`](M25_REMOTE_READ_ONLY_MONITORING.md)
for the exact local commands, endpoint inventory, allowlisted payload, and
operator-owned hostname/tunnel setup.

This is local-only: it checks source/configuration/calendar facts, reports only
whether Alpaca credentials are present, and confirms that broker writes remain
disabled. Optionally validate the shape of the external suspension path:

```powershell
python scripts/check_morning_watch_readiness.py --suspension-db <absolute-path-outside-repository>
```

Required local configuration is `ALPACA_API_KEY_ID`,
`ALPACA_API_SECRET_KEY`, and either `broker.expected_account_fingerprint` in
gitignored `config/local.yaml` or `ORB_EXPECTED_ACCOUNT_FINGERPRINT`. The
fingerprint is a SHA-256 digest, never a raw account number. The M22 unattended
pilot state store is separate, external, and **not activated** today.

### During the entry window

```powershell
python scripts/run_morning_live_shadow_watch.py --production
```

The canonical watcher evaluates the fixed 10-symbol watchlist every five minutes
from 09:40 through 11:00 ET and writes its session summary under
`artifacts/live-shadow-watch/` plus individual evidence under
`artifacts/live-shadow/`.

### If no candidate

`WINDOW_COMPLETE_NO_CANDIDATE` means no trade. Stop for the day.

### If a candidate is captured

`CANDIDATE_CAPTURED` is still non-writing. Use its authentic saved LIVE_SHADOW
candidate artifact to build the existing commissioning chain:

```powershell
python scripts/build_live_candidate_commissioning_handoff.py --live-shadow <live-shadow-evidence.json> --release-sha <current-40-character-sha> --suspension-db <absolute-path-outside-repository> --ack acknowledges_loss_may_exceed_plan --ack operator_present --ack robinhood_ui_open --ack no_second_trade
```

The command writes a sanitized handoff below `artifacts/commissioning-handoff/`
and prints the exact production read-only rehearsal command. Run that printed
command explicitly; it writes rehearsal evidence under
`artifacts/commissioning-rehearsal/`.

### Mandatory pre-write stop

`READY_FOR_EXPLICIT_ATTENDED_WRITE_AUTHORIZATION` is the final stop. **DO NOT
CONTINUE PAST THIS POINT WITHOUT EXPLICIT REVIEW/AUTHORIZATION.**
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

- **No unattended broker-write scheduler.** Every live action is operator-initiated.
  *(Milestone 10.)*
- **No live signal-to-execution bridge.** The strategy stack cannot reach a
  broker. *(Milestone 11.)*
- **No persistent strategy activation.** `strategy.enabled` stays `false` until
  a milestone explicitly authorizes changing it. *(Milestone 12.)*
- **No remote runtime control, broker-write control, recovery daemon, or alerting.**
  M25.3 adds only a loopback-bound controller for the observational morning
  watcher; remote access remains read-only. *(M25.)*
- **Active-protection state mapping is ungrounded.**
  `ADAPTER_PERMITTED_ACTIVE_PROTECTION_STATES` is empty, so
  `VERIFIED_ACTIVE_PROTECTION` is not available from that adapter mapping.
  Attended commissioning uses a provisional-protection assessment instead. This
  is dormant today and is a hard blocker for unattended operation, where no
  operator is watching the stop. The read-only observation checkpoint has now
  run once against a real account (2026-08-03) and found the account flat with
  no protective order, so it recorded `NOT_OBSERVED` and promoted nothing — the
  mapping is still empty and grounding is still incomplete. *(Milestone 9 — the
  first automation milestone.)*
- **`site/` has no broker or market-data connection.** Local Operations can
  reach only the M25.3 loopback controller. The separate M25.6 remote bundle
  reaches only same-origin authenticated session and sanitized projection
  endpoints; it contains no watcher-control client or broker path. *(M25.)*

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
