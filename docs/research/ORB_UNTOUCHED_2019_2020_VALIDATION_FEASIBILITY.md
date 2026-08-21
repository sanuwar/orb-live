# M50: Untouched 2019-2020 Validation of the Frozen M49 Portfolio

**Issue:** [#352](https://github.com/sanuwar/orb-live/issues/352)
**Branch:** `agent/m50-untouched-portfolio-validation`
**Depends on:** M43 (frozen ORB checkpoints), M48 (frozen ARM_A/ARM_B ETF engines), M49 (frozen `PRACTICAL_CORE_SATELLITE_30K` shared allocator) -- all reused unmodified.

> **This section is committed BEFORE any empirical result is read**, exactly as Issue #352 requires. It records the frozen protocol and the Stage-0 provenance investigation only. The Stage-0 outcome and final classification are added in a later, separate results commit, after the Stage-0 gate is actually run from a clean implementation checkout.

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

### Item 1: point-in-time ORB universe

The 2026-authored `UNIVERSE_POOL` (`src/orb_universe_expansion_research.py`) and the later `ANCHORED_COHORT` are both forbidden for 2019-2020: `UNIVERSE_POOL` was authored in 2026 and cannot be applied backward, and `ANCHORED_COHORT`'s own documented survivorship/universe-selection bias disqualifies it from independent validation (confirmed by inspection: both of the repo's existing "validation" universes -- `P_MINUS_2_UNIVERSE` in `src/orb_0950_confirmation_independent_validation_research.py` and `P_MINUS_1_UNIVERSE` in `src/orb_independent_single_trade_core_validation_research.py` -- are `ANCHORED_COHORT` under another name).

Issue #352 requires a deterministic 2019 native universe and a deterministic 2020 native universe, each built only from information publicly available before its own effective date, via a fixed source search order:

1. official issuer historical holdings/constituent files with verifiable as-of and publication dates;
2. SEC EDGAR filings whose holdings were public by the universe effective date;
3. another primary official source with equivalent dated provenance.

**All three tiers were attempted and exhausted; none produced a complete, compliant point-in-time roster.** The full, itemized investigation -- every source attempted, its access outcome, and every dated membership-change event actually confirmed -- is recorded as auditable data in `src/orb_untouched_2019_2020_validation_research.py` (`STAGE0_ORB_UNIVERSE_SOURCE_ATTEMPTS`, `STAGE0_CONFIRMED_DJIA_CHANGE_EVENTS`) and covered by `tests/test_orb_untouched_2019_2020_validation_research.py`. Summary:

- **Tier 1 (official issuer files):** SSGA (SPY issuer) exposes only current holdings, no dated historical archive. BlackRock/iShares (IVV issuer) are blocked (HTTP 403). Vanguard (VOO issuer) is reachable but its JS-rendered page yielded no historical archive.
- **Tier 2 (SEC EDGAR):** completely inaccessible -- every endpoint tried returned HTTP 403, including the general browse interface, the submissions API, a specific SPDR S&P 500 ETF Trust filing, the full-text-search API, the sec.gov homepage itself, and a specific SPDR Dow Jones Industrial Average ETF Trust (DIA) shareholder report (whose own schedule of investments would otherwise have qualified as a strong tier-2 source). A direct HTTP workaround via Bash `curl` was denied by the tool permission system before any request could be attempted.
- **Tier 3 (another primary official source with equivalent dated provenance):** S&P Dow Jones Indices' main site (`spglobal.com/spdji`) is blocked (HTTP 403). Its press-release subdomain, `press.spglobal.com`, **is** reachable and yielded two dated, compliant, official change announcements (WBA replaces GE, effective 2018-06-26, announced 2018-06-19; Dow Inc. replaces DowDuPont, effective 2019-04-02, announced 2019-03-26) -- but a press release only ever announces a single membership change, never a complete roster, so this does not by itself anchor a full reconstruction.
- **Archival fallbacks (not themselves a fixed-order tier):** Wayback Machine and archive.today are both unreachable at the tool level ("unable to fetch"), independent of any specific URL.
- **A complete, accurate, well-sourced roster was found but is disqualified:** NBER Working Paper No. 28528, "Replicating the Dow Jones Industrial Average" (Lin, Selden, Shoven, Sialm), contains a complete 30-name DJIA table as of December 31, 2019, citing S&P Dow Jones Indices as its source. It is not used as Stage-0 provenance because the paper itself was published in March 2021 -- after both the 2019-01-02 and 2020-01-02 universe effective dates -- failing Issue #352's literal requirement that "source publication/filing date must be no later than the effective date." This module does not reinterpret or relax that requirement to make an otherwise-convenient source qualify.

**Conclusion: Stage-0 item 1 does not pass.** Per Issue #352: *"If no qualifying source can establish both yearly universes, stop with `INSUFFICIENT_EVIDENCE`."*

### Item 2: market data and calendar

Not evaluated. Item 1 did not establish an ORB universe roster, so there is no confirmed symbol set whose 2018-2020 minute-bar/calendar/corporate-action coverage could be checked. Evaluating coverage for a hypothetical or placeholder roster would not satisfy Issue #352's requirement and was not attempted.

### Item 3: parity before economics

Independent of item 1 (it uses only the already-existing, frozen M43/M48/M49 caches and checkpoints, never any 2019-2020 data), Stage-0 item 3 was still executed: the frozen M49 allocator was reproduced against its own existing 2022-2026 artifacts exactly, via `scripts/research_orb_untouched_2019_2020_validation.py`, which reuses M49's own `build_orb_intents` (ORB standalone replay parity against the M43 checkpoints) and `verify_arm_a_standalone_parity`/`verify_arm_b_standalone_parity` (against M48's committed ledger hashes) unchanged. Results are recorded in the Stage-0 result document below.

## Frozen classification

Applied once after all integrity gates pass. Because Stage-0 item 1 blocks, the milestone concludes at `INSUFFICIENT_EVIDENCE` per Issue #352's own decision tree: *"record the exact provenance/data blocker; do not substitute a biased universe or widen/change the window."* No sweep of dates, universes, sources, allocations, priorities, thresholds, indicators, exits, costs, or risk was performed, per the anti-analysis-paralysis rule.

## Old M49 2022-2026 result (frozen reference only, never combined with M50)

- Base: $184.75/month mean, 14.60% max drawdown. Stress: $122.02/month mean, 18.54% max drawdown.
- Classification: `SAFE_BUT_BELOW_TARGET`.
- PR #351, merge commit `8a9eb26ab8a30c9e2f333a25a0302586048e8d3e`.

---

## Stage-0 result (added in the results commit, after running the gate from a clean implementation checkout)

_Pending -- see the results commit for the actual run's classification, parity outcome, and generation provenance (commit SHA, source-file hashes, working-tree-clean status)._
