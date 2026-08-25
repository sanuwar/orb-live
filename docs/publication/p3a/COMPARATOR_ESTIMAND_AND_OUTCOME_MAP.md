# Comparator, Estimand, and Outcome Map

**CANDIDATE SPECIFICATION. NOTHING BELOW WAS COMPUTED.** Every cell describes what *would* be estimated if a reviewer approves the design and the granular data passes the P3B preflight.

## Why comparators split into two classes

A finding this map makes explicit, and which earlier P2 documents did not separate clearly:

- **Design-determined comparators (5 test instances):** TI-01, TI-02, TI-03 compare against M43 `BASELINE` on the identical 122 candidate occurrences; TI-10 and TI-11 compare against the M52 baseline on the identical 69 signal dates with machine-verified bit-for-bit parity. These are **paired within-milestone contrasts**. The comparator is not a matter of opinion, it is what the milestone was built to isolate. They are the strongest designs in the record.
- **Contested comparators (5 test instances):** TI-05 through TI-09 have no design-determined comparator. Each measures an absolute dollar return on a $30,000 account, so the null is only meaningful once someone decides what it is being compared against: zero, a buy-and-hold benchmark, a risk-matched benchmark, or a capital-cost hurdle. **This is Decision 6 and it is genuinely unresolved.** Choosing carelessly here would silently determine the sign and significance of five of the eleven results.
- **TI-04 sits between the two:** the comparator (baseline-only policy) is design-determined, but the contrast is not cleanly paired, because M45 adds trades on days the baseline had none. The comparison is between two policies over the same sessions, not between two exit rules on the same trades.

## Map

| TI | Comparator | Comparator class | Unit of observation | Primary estimand | Primary outcome | Secondary outcomes | Risk / tail outcomes | Required series | Availability (P2) |
|---|---|---|---|---|---|---|---|---|---|
| TI-01 | M43 `BASELINE`, same 122 occurrences | Design-determined, paired | Session (clustered), aggregated to month | Population mean paired difference in per-trade R-multiple expectancy | Pooled R-expectancy delta vs. baseline | Profit factor, trades/month, R/month, full-stop-out conversion rate | Max drawdown in R, longest losing streak | Per-trade R-multiple series with session timestamps | `SUMMARY_ONLY`, pending preflight |
| TI-02 | M43 `BASELINE`, same 122 occurrences | Design-determined, paired | Session, aggregated to month | Same as TI-01 | Same | Same | Same | Same | `SUMMARY_ONLY`, pending preflight |
| TI-03 | M43 `BASELINE`, same 122 occurrences | Design-determined, paired | Session, aggregated to month | Same as TI-01 | Same | Same | Same | Same | `SUMMARY_ONLY`, pending preflight |
| TI-04 | Baseline-only `WAITED_FIRST_VALID_0950` policy | Design-determined, not cleanly paired | Session/month, including zero-trade sessions | Population difference in combined-policy R/month vs. baseline-only R/month | Combined R/month delta | Later-only expectancy, trades/month, per-symbol contribution | Max drawdown in R | Per-trade R series plus the per-session no-trade record | `SUMMARY_ONLY`, pending preflight |
| TI-05 | **Undecided (Decision 6)** | Contested | Month (daily for resampling blocks) | Population mean monthly dollars on $30,000, base cost | Mean monthly dollars | CAGR, profitable-month rate, turnover, cost load | Max drawdown percent and dollars, worst month | Daily portfolio return/equity series | `SUMMARY_ONLY`, pending preflight |
| TI-06 | **Undecided (Decision 6)** | Contested | Month (daily for resampling) | Same as TI-05 | Same | Same, plus single-symbol concentration | Same | Same | `SUMMARY_ONLY`, pending preflight |
| TI-07 | **Undecided (Decision 6)** | Contested | Month (daily for resampling) | Same as TI-05 | Same | Same, plus the documented SPY/QQQ/IWM broad-beta overlap disclosure | Same | Same | `SUMMARY_ONLY`, pending preflight |
| TI-08 | **Undecided (Decision 6)** | Contested | Month (daily for resampling) | Population mean monthly dollars for the shared-cash account, base cost | Mean monthly dollars | Per-sleeve contribution share, cost load, stress-cost sensitivity | Max drawdown percent and dollars, base and stress | Daily shared-account equity series plus per-sleeve attribution | `SUMMARY_ONLY`, pending preflight |
| TI-09 | **Undecided (Decision 6)** | Contested | Month, 69 rebalance observations | Population mean monthly dollars on $30,000, base cost | Mean monthly dollars | CAGR, profitable-month rate, positive-segment count, turnover | Max drawdown percent, worst month | Daily portfolio series preserving within-date cross-sectional structure | `SUMMARY_ONLY`, pending preflight |
| TI-10 | M52 baseline, same 69 signal dates, parity-verified | Design-determined, paired | Month, paired by signal date | Population mean paired difference in monthly dollars, and paired difference in max drawdown | Monthly-dollar delta and drawdown delta vs. baseline | Positive-segment count, profitable-month rate, turnover delta | Worst-month delta, drawdown delta | Daily series for both arms from the identical cache | `SUMMARY_ONLY`, pending preflight |
| TI-11 | M52 baseline, same 69 signal dates, parity-verified | Design-determined, paired | Month, paired by signal date | Same as TI-10 | Same | Same, plus exposure-multiplier engagement frequency | Same | Same | `SUMMARY_ONLY`, pending preflight |

## The unit-mismatch problem

FH-1 measures in **R-multiples** (risk-normalized, unitless). FH-2 and FH-3 measure in **dollars on a fixed $30,000 account**. These are not commensurable:

- An R-multiple expectancy of 0.19 and a mean monthly return of $456.87 cannot be placed on one axis, ranked, or pooled into a single test statistic without a standardization rule.
- Any cross-family procedure, including a joint Romano-Wolf stepdown across all eleven test instances, therefore requires Decision 7 (test-statistic standardization) to be resolved first. Candidate approaches include standardizing each estimand by its own bootstrap standard error, converting everything to a return-on-capital basis, or declining to run a cross-family procedure at all and reporting three within-family procedures instead.
- **The last option is the preliminary recommendation of this document**, because within-family comparisons are unit-consistent and design-determined, while cross-family comparisons would require an arbitrary conversion that the reviewer has not sanctioned. This is recorded as a recommendation, not a decision.

## Estimand precision note

Every estimand above is a **population mean** quantity, not the observed sample statistic already committed in the M43-M55 reports. The committed figures are point estimates of these estimands with no uncertainty attached; that is precisely the gap P3 would close. Nothing in this map re-derives, re-estimates, or restates a committed figure as if it carried an interval.

## Required-series summary

Every one of the eleven test instances depends on at least one series that P2 classified `SUMMARY_ONLY` pending the cache-integrity preflight. **Zero test instances are executable from committed evidence alone.** The factor series required for any Fama-French/UMD attribution of TI-08 through TI-11 is separately classified `NOT_AVAILABLE` in the record, and acquiring it is a new-data decision outside P3A scope.
