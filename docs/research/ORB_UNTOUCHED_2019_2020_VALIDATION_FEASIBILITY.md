# M50: Untouched 2019-2020 Validation of the Frozen M49 Portfolio

**Issue:** [#352](https://github.com/sanuwar/orb-live/issues/352)
**Branch:** `agent/m50-untouched-portfolio-validation`
**Depends on:** M43 (frozen ORB checkpoints), M48 (frozen ARM_A/ARM_B ETF engines), M49 (frozen `PRACTICAL_CORE_SATELLITE_30K` shared allocator) -- all reused unmodified.

> **Correction (second revision).** The first revision concluded `INSUFFICIENT_EVIDENCE` at Stage-0 item 1, having stopped after every direct fetch against sec.gov returned HTTP 403 -- correct as far as it went, but incomplete: it never tried an alternate technical retrieval path for the *same* official documents. An independent review supplied two specific SEC EDGAR accession numbers for SPDR Dow Jones Industrial Average ETF Trust (DIA) N-30D filings. Item 1 now passes for both years (see below). A real, full-scale fetch of the required Alpaca IEX minute-bar data then surfaced a genuine, different Stage-0 item-2 blocker: a hard coverage floor at 2020-07-27. The final classification is unchanged (`INSUFFICIENT_EVIDENCE`), but for the correct, verified reason.

## Purpose

M49 found the frozen shared-capital portfolio `SAFE_BUT_BELOW_TARGET` ($184.75/month base, $122.02/month stress, 14.60% base max drawdown) over 2022-01-03 through 2026-08-13. That result is not yet an independent historical validation. M50 asks one bounded question:

> Does the exact frozen M49 portfolio architecture retain a practically useful positive result on a genuinely untouched pre-2021 period, using point-in-time universes and no survivorship/lookahead substitution?

Research only. No activation, no broker writes, no production configuration change, margin, leverage, short selling, options, futures, inverse ETFs, or leveraged ETFs.

## Frozen validation window

- Outcome window: **2019-01-02 through 2020-12-31** (24 calendar months).
- Daily-indicator warmup may reach back only as far as the already-frozen lookbacks require (target start: 2018-01-02).
- ORB RVOL/prior-close warmup may reach before 2019-01-02 only as required by the existing 20-session logic.
- Warmup observations form indicators only; no pre-window position, transaction, cost, return, or equity contribution may enter the outcome.
- Endpoint marking must use the exact 2020-12-31 close.
- No 2021-or-later observation may influence universe selection, indicators, fills, exits, marks, or classification.

This window does not overlap M48/M49's 2022-01-03..2026-08-13 evaluation window, and predates M42-M47's earliest 2021 ORB validation period -- see `tests/test_orb_untouched_2019_2020_validation_research.py::test_outcome_window_does_not_overlap_m48_m49_evaluation_window`.

## Exact frozen portfolio carried forward (unchanged)

Reuses M49's `PRACTICAL_CORE_SATELLITE_30K` implementation and rules exactly, with no signal, exit, allocation, event-order, risk, cost, or classification input changed: ORB (`GAP_050_RVOL_175` + `WAITED_FIRST_VALID_0950` + `BE_AFTER_050R`), ARM_A (`ETF_PULLBACK_RSI2`), ARM_B (`SECTOR_MOMENTUM_12_1`, capped at 60% of current marked equity), one shared $30,000 cash account, whole shares, no leverage, $300/$600 tactical risk caps, 3 max concurrent tactical positions, M49's exact 7-step chronological event ordering and blocked-intent accounting, base 5bps/stress 10bps costs, fixed-$30,000 primary view without compounding, no forced cross-sleeve liquidation, no catch-up trades.

ARM_A/ARM_B reuse the already-frozen ETF universes (`UNIVERSE_A`/`UNIVERSE_B`) unchanged -- Issue #352 explicitly exempts them from the point-in-time-universe problem, since they are stable ETF tickers, not stock-picks. A security without sufficient real history remains naturally ineligible until the frozen lookback is actually present; nothing is backfilled, synthesized, or substituted.

## Stage 0 -- mandatory provenance/data feasibility gate

Per Issue #352, this gate must complete -- and be committed -- before any empirical portfolio outcome is read.

### Item 1: point-in-time ORB universe -- PASSES for both years

The 2026-authored `UNIVERSE_POOL` (`src/orb_universe_expansion_research.py`) and the later `ANCHORED_COHORT` are both forbidden for 2019-2020: `UNIVERSE_POOL` was authored in 2026 and cannot be applied backward, and `ANCHORED_COHORT`'s own documented survivorship/universe-selection bias disqualifies it from independent validation.

Issue #352 requires a deterministic 2019 native universe and a deterministic 2020 native universe, each built only from information publicly available before its own effective date, via a fixed source search order:

1. official issuer historical holdings/constituent files with verifiable as-of and publication dates;
2. SEC EDGAR filings whose holdings were public by the universe effective date;
3. another primary official source with equivalent dated provenance.

**Both years are established from a single source type that satisfies tiers 1 and 2 simultaneously**: SPDR Dow Jones Industrial Average ETF Trust (DIA) is both the official issuer of the fund tracking this eligibility universe *and* an SEC EDGAR filer -- its semi-annual N-30D shareholder reports carry an official, dated Schedule of Investments.

| Universe year | Effective date | Accession | Filed | Period of report | Tickers |
| --- | --- | --- | --- | --- | --- |
| 2019 | 2019-01-02 | `0001193125-18-351994` | 2018-12-18 | 2018-10-31 | 30 (incl. `DWDP`) |
| 2020 | 2020-01-02 | `0001193125-19-320843` | 2019-12-20 | 2019-10-31 | 30 (incl. `DOW`) |

Both filing dates are before their own universe's effective date, satisfying the "no later than the effective date" requirement literally. sec.gov itself remains completely blocked to this environment's direct fetch tooling (confirmed via 403 on every endpoint tested, including these two specific filings); the documents were retrieved through a text-rendering proxy (`r.jina.ai`) reaching the same official URLs. The retrieved rosters were cross-validated three ways:

1. **Internal consistency** with the dated DJIA change events already on record: the 2018-10-31 filing shows `DWDP` (DowDuPont), not `DOW` (Dow Inc.) -- correct, since the DOW/DWDP swap did not take effect until 2019-04-02; the 2019-10-31 filing shows `DOW`, not `DWDP` -- also correct.
2. **A third, independent source** -- the "Dow Additions and Deletions Since 1929" changelog at dogsofthedow.com -- confirms no DJIA membership change occurred between either filing's report date and its corresponding universe effective date (nearest changes: 2018-06-26 WBA/GE and 2019-04-02 DOW/DWDP for the 2019 window; 2019-04-02 DOW/DWDP and 2020-08-31 CRM/HON/AMGN for the 2020 window -- both outside their respective windows).
3. **Exact agreement** with the NBER working paper's (No. 28528) December 31, 2019 DJIA table that the first revision had already found but correctly disqualified for its own late (March 2021) publication date -- the SEC filing succeeds where the NBER paper failed, specifically on the "no later than the effective date" criterion.

The full, itemized investigation -- all 21 source attempts across all 3 tiers, the two qualifying `UniverseSource` records, and both `NoChangeAuditWindow` audits -- is recorded as auditable data in `src/orb_untouched_2019_2020_validation_research.py` and covered by 41 tests in `tests/test_orb_untouched_2019_2020_validation_research.py`, including non-vacuous checks that each year is evaluated **independently** (never a shared boolean) and that Stage-0 fails closed when only one year's evidence is present.

### Item 2: market data and calendar -- FAILS

**Calendar coverage: resolved.** The production `src/exchange_calendar.py` (digest-pinned, live-trading window only) covers 2026-2027 -- nowhere near M50's window. The existing research-only fallback, `src/orb_research_historical_calendar.py` (explicitly outside the pinned closure, already used by earlier out-of-sample studies), covered only 2021-2025. It was extended back to 2018-01-01 with NYSE's official 2018/2019/2020 holiday and early-close schedule (source: NYSE Group's official "2018, 2019 and 2020 Holiday and Early Closings Calendar" press release, cross-checked against fixed-position US market-holiday rules), following the exact same precedent as every prior year already in that file. 146 tests cover the extension, including full-window gap-free verification for the entire M50 outcome window.

**Corporate-action policy: resolved.** Two DJIA members in the point-in-time universe have real, dated corporate actions inside the outcome window: DowDuPont (`DWDP`, 2019 universe) completed its three-way separation and was renamed in mid-2019; United Technologies (`UTX`, 2020 universe) merged into Raytheon Technologies (`RTX`) on 2020-04-03. Per Issue #352 ("never silently replace them with current successors"), the recorded policy (`ORB_CORPORATE_ACTION_POLICIES`) is conservative: no successor ticker is substituted. Each symbol's own real trading data is used through its own documented last-active date, and ORB simply generates no further candidates for that universe slot afterward -- exactly as it already does for any illiquid session.

**Daily SIP coverage (ARM_A/ARM_B): passes.** A real fetch of Alpaca SIP daily bars for the full `UNIVERSE_A`/`UNIVERSE_B` set across 2018-01-02..2020-12-31 found complete coverage for 19 of 20 symbols. The one exception, `XLC` (Communication Services Select Sector SPDR), has bars starting exactly 2018-06-19 -- its real launch date (the fund launched 2018-06-18, first trading day the next session) -- and is correctly excluded from the failure count via `ETF_LAUNCH_POLICIES`, matching Issue #352's own anticipated case: "A security without sufficient real history (for example, a recently launched ETF) remains naturally ineligible until the frozen lookback is actually present."

**Minute IEX coverage (ORB): fails -- the binding blocker.** A real, full-scale fetch of Alpaca's IEX minute-bar history (the same feed the frozen ORB baseline uses, `feed="iex"`) for the complete 31-ticker point-in-time universe plus SPY, across the full outcome window, found a hard coverage floor: **zero minute bars exist for any session before 2020-07-27.** This was confirmed by bisection across four independent symbols (SPY, AAPL, MSFT, XLK) and dozens of individual trading days on both sides of the boundary -- the last trading day before the boundary (2020-07-24) returns 0 bars for all four; the boundary date itself (2020-07-27) returns a full session's bars for all four. It is not a code bug (the same fetch path returns complete, correct data for 2021 and later) and not a transient gap (every date tested on the far side of the boundary, including dates deep into 2019, returns exactly zero). Concretely: **0 of 252 required 2019 sessions and 142 of 253 required 2020 sessions are missing** for every symbol in the union universe (30 symbols missing all 252/252 2019 sessions; `DWDP`'s own truncated 2019 window, ending 2019-05-31, is also entirely missing).

Per Issue #352: *"If required minute/daily bars... are incomplete, report the exact gap and stop with `INSUFFICIENT_EVIDENCE`; do not change vendor/feed/window."* No feed substitution (e.g. SIP minute data) or window change was made.

### Item 3: parity before economics -- PASSES

Reproduced against the existing M43/M48/M49 artifacts, using only the already-frozen caches/checkpoints (no 2019-2020 data):

- **ORB standalone replay parity:** all 6 frozen periods (`P_MINUS_2`, `P_MINUS_1`, `P0`, `P1`, `P2`, `P3`) reproduced bit-for-bit against their own committed M43 checkpoints. 209 total replayed candidates, 0 mismatches.
- **ARM_A (`ETF_PULLBACK_RSI2`) standalone parity:** reproduced against M48's own frozen engine and committed ledger hashes. `ok: true`, `ledger_hash_match: true`.
- **ARM_B (`SECTOR_MOMENTUM_12_1`) standalone parity:** reproduced against M48's own frozen engine and committed ledger hashes. `ok: true`, `ledger_hash_match: true`.

## Frozen classification

Applied once after all integrity gates pass. Item 1 passes; item 3 passes; **item 2 blocks**. Per Issue #352's decision tree: *"`INSUFFICIENT_EVIDENCE` -> record the exact provenance/data blocker; do not substitute a biased universe or widen/change the window."* No date, universe, source, allocation, threshold, indicator, exit, cost, feed, or risk parameter was swept or tuned to try to recover a different outcome, per the anti-analysis-paralysis rule. No M50 economic outcome was computed -- item 2 blocked before any empirical portfolio result could be read, per Issue #352's own sequencing.

## Old M49 2022-2026 result (frozen reference only, never combined with M50)

- Base: $184.75/month mean, 14.60% max drawdown. Stress: $122.02/month mean, 18.54% max drawdown.
- Classification: `SAFE_BUT_BELOW_TARGET`.
- PR #351, merge commit `8a9eb26ab8a30c9e2f333a25a0302586048e8d3e`.

---

## Stage-0 result (corrected)

Run once from the clean corrected-implementation head `7c5fc9b3cb8f1fd4ab529b3151016ee7abf89641` via `scripts/research_orb_untouched_2019_2020_validation.py` (items 1, 2, and 3 together).

### Item 1: point-in-time ORB universe -- ESTABLISHED for both 2019 and 2020

See the corrected item-1 section above for the full, verified sourcing chain.

### Item 2: market data and calendar -- FAILS

- ORB minute (IEX): 0/60 per-symbol-year checks pass. Every 2019-universe symbol is missing 252/252 (or, for `DWDP`, its own truncated 104/104) sessions; every 2020-universe symbol is missing 142/253 sessions (all before 2020-07-27).
- ETF daily (SIP): 14/15 checks pass outright; the 15th (`XLC`) passes once its real 2018-06-19 launch date is accounted for.
- **Binding blocker:** the IEX minute-bar coverage floor at 2020-07-27.

### Item 3: parity before economics -- PASSED

Same result as the first revision (unaffected by the correction): ORB 209/209 replayed candidates matched, 0 mismatches across all 6 M43 periods; ARM_A and ARM_B both `ok: true`, `ledger_hash_match: true` against M48's committed hashes.

### Final classification: `INSUFFICIENT_EVIDENCE`

For the correct, verified reason (item 2 minute-bar coverage), not the original (incorrect) reason (item 1 universe sourcing).

### Generation provenance

| Field | Value |
| --- | --- |
| `generation_git_head_sha` | `7c5fc9b3cb8f1fd4ab529b3151016ee7abf89641` |
| `generation_working_tree_clean` | `true` (scoped to the two hashed source files) |
| `src/orb_untouched_2019_2020_validation_research.py` SHA-256 | `2941ca9cd95021e69d24ad885cb2a797ccc38e3a93da4537422c6faf3f811060` |
| `scripts/research_orb_untouched_2019_2020_validation.py` SHA-256 | `4992e3b6bb830b4ea463a49f0af5beea6c69f06252a0accd60d7d53ee46a6762` |

Full generation record, including per-symbol coverage detail: `docs/research/ORB_UNTOUCHED_2019_2020_VALIDATION_ARTIFACT_HASHES.json` (committed) and the gitignored `artifacts/research-untouched-2019-2020-validation/results/stage0_result.json` (regenerable from the committed source, per the established M43-M49 convention of committing hashes and reports, not raw generated data).
