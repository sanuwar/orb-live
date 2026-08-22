# M51: $30K Higher-Return Strategy-Family Selection Gate

**Issue:** [#354](https://github.com/sanuwar/orb-live/issues/354)
**Branch:** `agent/m51-higher-return-strategy-selection`
**Depends on:** M43-M50 (frozen large-cap ORB lane + M48/M49 ETF family), parked, not reopened.

> **This section is committed BEFORE any evidence is scored or conclusion recorded**, exactly as Issue #354 requires. It records the frozen selection protocol only: the four candidate families, the ten-criterion rubric, and the deterministic selection rule (`src/strategy_family_selection_gate_research.py`). The evidence tables, scores, classification, and (if applicable) frozen M52 proposal are added in a later, separate results commit.

## Purpose

M43-M49 built and honestly evaluated the frozen large-cap ORB lane plus a $30K ETF strategy family sharing capital with it:

- Frozen large-cap ORB frequency: ~1.898 trades/month.
- M46 (small/mid-cap ORB momentum lane): `NOT_PROMISING`.
- M47 (sequential second trade): `INSUFFICIENT_EVIDENCE`.
- M48's three ETF arms were positive but insufficient; the generous ORB-plus-top-two upper bound was ~$482.87/month.
- M49's shared-capital portfolio: ~$184.75/month base, ~$122.02/month stress, 14.60% base max drawdown -- `SAFE_BUT_BELOW_TARGET`.
- M50 could not independently validate 2019-2020 because Alpaca's frozen IEX minute history begins 2020-07-27 -- `INSUFFICIENT_EVIDENCE`.

**M51 does not reopen, retune, or further squeeze the ORB/M48/M49 family.** That path is parked unless new independent evidence arises. M51 asks a different, bounded question:

> Identify at most one genuinely different, practical strategy family that deserves a separately frozen empirical M52 experiment for a $30,000 account.

Primary practical target: **$500/month** after realistic costs. Stretch target: **$1,000/month**. Neither target is guaranteed; the required risk and mathematical credibility of each target must be reported explicitly, not implied.

Research only. No activation, no broker writes, no production configuration change.

## Candidate families (exactly four, frozen -- no expanding the list)

1. **Concentrated long-only cross-sectional equity momentum** with a market/trend filter.
2. **Post-earnings-announcement drift (PEAD) / earnings-momentum trading** using point-in-time announcement data.
3. **Defined-risk liquid-index option premium strategies** (e.g. fully collateralized vertical spreads) -- no naked options, no unlimited-loss structures.
4. **Tactical trend following** using liquid index/sector ETFs, permitting at most 2x leveraged ETFs for research comparison only -- no margin borrowing, no leveraged position may be proposed for activation.

## Evidence standard

Primary or authoritative evidence only: peer-reviewed papers, exchange/OCC/SEC/FINRA materials, official broker/data-provider documentation, and reproducible datasets. Influencer claims, marketing yields, isolated backtests, and unsourced aggregators are excluded.

## Frozen rubric (ten criteria, `src/strategy_family_selection_gate_research.py::SELECTION_RUBRIC`)

1. `evidence_strength` -- strength and independence of published evidence
2. `data_availability_cost` -- point-in-time data availability and cost
3. `survivorship_lookahead_risk` -- survivorship/lookahead risk
4. `account_size_feasibility` -- implementability with $30,000 and whole-contract/share constraints
5. `realistic_costs` -- realistic transaction costs, spreads, assignment, liquidity, tax/operational complexity
6. `monthly_dollar_range` -- plausible net monthly-dollar range
7. `drawdown_tail_risk` -- expected drawdown and tail-loss characteristics
8. `repo_robinhood_compatibility` -- compatibility with the existing repository and eventual Robinhood execution
9. `engineering_effort` -- engineering effort required for a trustworthy backtest
10. `target_plausibility` -- whether $500/month and $1,000/month are mathematically plausible without hiding unacceptable risk

Each criterion is scored `PASS`/`FAIL` per family, with a documented reason and source citations.

## Selection rule (frozen, `select_family()`)

A family is **eligible** only if all four gating dimensions pass:

- the `evidence_strength` gate,
- the `data_availability_cost` gate,
- the `account_size_feasibility` gate,
- the composite **"realistic after-cost economics"** gate (`realistic_costs` AND `monthly_dollar_range` AND `target_plausibility` all `PASS`).

`survivorship_lookahead_risk`, `drawdown_tail_risk`, `repo_robinhood_compatibility`, and `engineering_effort` are scored and reported for every family but do not independently veto eligibility -- they inform the written comparison and (only if needed) the deterministic tiebreak.

- **Zero** eligible families -> `NO_CREDIBLE_CANDIDATE`. Stop.
- **One** eligible family -> `SELECTED_FOR_M52`, that family, one frozen M52 proposal.
- **More than one** eligible family -> `SELECTED_FOR_M52`, broken deterministically by the highest total rubric pass-count (all 10 criteria), then by the families' own frozen declaration order. Never a subjective "least bad" judgment call made after the fact.

No threshold is tuned, no family is combined with another, no fifth candidate is introduced, and a family is never promoted merely because it ranks first among failing candidates. One bounded evidence pass, one deterministic ranking, one conclusion.

## Old M49 result (frozen reference only, never combined with M51's own evaluation)

- Base: $184.75/month mean, 14.60% max drawdown. Stress: $122.02/month mean, 18.54% max drawdown.
- Classification: `SAFE_BUT_BELOW_TARGET`.

---

## Evidence, scoring, and conclusion (added in the results commit)

_Pending -- see the results commit for the full evidence/source table, the ten-criterion scoring table with pass/fail reasons per family, the realistic $30K monthly-dollar range, drawdown/tail-risk discussion, data-source cost estimates, Robinhood/platform compatibility assessment, the final `SELECTED_FOR_M52`/`NO_CREDIBLE_CANDIDATE` classification, and (if selected) the frozen M52 protocol._
