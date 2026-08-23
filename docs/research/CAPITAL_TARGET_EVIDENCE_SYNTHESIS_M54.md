# M54: $30K Capital-Target Evidence Synthesis and Forward-Path Decision Gate

**Issue:** [#360](https://github.com/sanuwar/orb-live/issues/360)
**Branch:** `agent/m54-capital-target-evidence-synthesis`
**Depends on:** the complete M43-M53 committed record. M54 is not a new strategy experiment; it introduces no new threshold, universe, lookback, stop, hedge, allocation, or risk overlay, and fetches no new market data.

> **This section is committed BEFORE the final evidence tables and conclusions are written**, matching the pre-results sequencing discipline established in M51-M53: the protocol, decision-label definitions, and capital-scaling formulas are frozen first; the reconciled evidence and the official M54 conclusion are added in a separate, later commit.

## Purpose

Consolidate the complete M43-M53 empirical record into one auditable answer to four questions:

1. What profit range is actually supported for a fixed $30,000 account?
2. Is either $500/month or $1,000/month supported under the established risk limits?
3. How much capital would the existing positive strategies approximately require to reach those dollar targets, without assuming improved percentage returns?
4. Should the next activity be prospective paper monitoring, a genuinely different strategy-family investigation, publication preparation, or termination of retrospective strategy searching?

## Anti-analysis-paralysis rule

- No new market data fetch. No new backtest. No strategy alteration or optimization.
- No new threshold, universe, lookback, stop, hedge, allocation, or risk overlay.
- No automatic M55.
- Only committed M43-M53 reports, result JSON/hashes, and merged Git history are used as source evidence.
- Where two historical documents conflict, reconciled against the final corrected committed artifact, with the discrepancy documented explicitly rather than silently resolved.
- Finished in one issue/branch/PR.

## Method

### Evidence extraction

Every M43-M53 milestone's final, currently-committed report document (and, where a milestone produced one, its machine-readable result JSON / committed hashes file) was read directly from the merged-`main` checkout. Where a milestone's report document itself records a correction (a later commit that changed a reported figure or a classification's stated reason), the CURRENT committed text -- already reflecting that correction -- is treated as authoritative; the original, superseded figure is reported alongside it only for audit-trail completeness, never as the number carried into a table total or a capital-scaling calculation.

Git commit history (`git log --oneline -- <path>`) was checked per document to confirm that no further, more recent correction exists beyond what the document's own text already describes.

### Capital-target mathematics (`src/capital_target_evidence_synthesis.py`)

Pure, tested, deterministic functions -- no strategy logic, no market data:

- `monthly_return_pct_for_target`: the monthly percent return a fixed dollar target implies on a given capital base (`target / capital`).
- `simple_annual_pct`: simple (non-compounding) annualization (`monthly_pct * 12`).
- `compounded_annual_pct`: compounded annualization (`(1 + monthly_pct)^12 - 1`).
- `required_capital_for_target`: the frozen linear-scaling formula, `observed_starting_capital * target_monthly_dollars / observed_mean_monthly_dollars` -- applied only to a strategy with a genuinely positive, actually-simulated observed mean monthly profit; refuses on a non-positive input rather than producing a nonsensical or infinite scaling result.
- `scaled_drawdown_dollars`: `required_capital * observed_max_drawdown_pct` -- the dollar drawdown implied at the scaled capital, holding the OBSERVED drawdown PERCENTAGE fixed. This does **not** assume drawdown improves with more capital; it is the most conservative default absent contrary evidence, and is always reported next to an explicit non-guarantee warning.

Every calculation in the results tables is reproducible from these functions and the committed source figures; the pinned unit tests reconcile representative values (including the exact $500/$1,000-on-$30,000 percentages Issue #360 specifies: 1.6667%/20% simple annual, and 3.3333%/40% simple annual) against hand-computed expectations.

### Frozen decision labels

Applied to the evidence exactly as defined by Issue #360, never weakened or redefined:

1. **`FORWARD_VALIDATE_RISK_CONTROLLED_CANDIDATE`** -- only if a prior candidate already satisfies ALL of: base mean monthly profit >= $500; stress-cost outcome positive; max drawdown <= 25%; worst month >= -10%; adequate calendar-segment consistency; no unresolved integrity failure.
2. **`PAPER_MONITOR_RESEARCH_ONLY_CANDIDATE`** -- a strategy that is consistently profitable and economically meaningful but violates one or more of the risk limits above. May be monitored prospectively with simulated capital; must never be activated.
3. **`SUPPORTED_BELOW_TARGET`** -- the highest defensible practical monthly-profit range supported by a design that satisfies its own applicable safety/risk evidence better than the high-return candidates.
4. **`NO_CREDIBLE_30K_TARGET_PATH`** -- neither $500/month nor $1,000/month is supported at the established risk limits by any completed milestone.
5. **`INSUFFICIENT_EVIDENCE`** -- reserved for a conclusion that genuinely cannot be made because required committed evidence is missing or irreconcilable (applied per-milestone where applicable, e.g. a milestone blocked by a data-coverage gate before any economic result was computed).

Multiple labels may coexist. A strategy is never promoted merely for being the best result observed among those examined.

## Required evidence inventory (scope)

Final corrected results for: M43 profit protection, M44 fallback recovery, M45 later-first-entry recovery, M46 small/mid-cap momentum lane, M47 sequential second trade, M48 three ETF strategy arms and combination plausibility, M49 shared-capital core-satellite portfolio, M50 untouched-validation attempt and its data limitation, M51 strategy-family selection, M52 cross-sectional momentum, M53 breadth and volatility risk controls.

For every relevant economic strategy/design: starting capital, evaluation period, mean monthly fixed-notional net profit, stress-cost monthly profit where available, CAGR/annualized return where available, maximum drawdown, worst month, positive calendar segments, classification, data/provenance limitation, and whether it is risk-controlled, research-only, parked, or unavailable for inference.

---

## Evidence tables and conclusions

_Pending -- added in the results commit, after the evidence above is extracted and reconciled against the final committed M43-M53 artifacts. See that commit for the milestone evidence table, the apples-to-apples strategy table, the capital-scaling table, the research-search accounting, the official M54 decision labels, the forward-path decision, and the publication-readiness appendix._
