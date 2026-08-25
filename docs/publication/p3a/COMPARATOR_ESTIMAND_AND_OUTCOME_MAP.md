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
| TI-10 | M52 baseline, same 69 signal dates, parity-verified | Design-determined, paired | Month, paired by signal date (return endpoint only) | **Return:** population mean paired difference in monthly dollars. **Risk:** separate path-level functionals, not paired monthly observations (see D13) | Paired monthly-dollar delta vs. baseline | Positive-segment count, profitable-month rate, turnover delta | Max drawdown, worst month, downside deviation, each computed on the **whole** equity path per arm and compared as a path-level functional, never as a per-month paired mean | Daily series for both arms from the identical cache | `SUMMARY_ONLY`, pending preflight |
| TI-11 | M52 baseline, same 69 signal dates, parity-verified | Design-determined, paired | Month, paired by signal date (return endpoint only) | Same as TI-10 | Same | Same, plus exposure-multiplier engagement frequency | Same as TI-10, path-level | Same | `SUMMARY_ONLY`, pending preflight |

## Mixed estimand units and the union-level procedure

FH-1 measures in **R-multiples** (risk-normalized, unitless). FH-2 and FH-3 measure in **dollars on a fixed $30,000 account**. An earlier draft of this document concluded from this that a union-level procedure was effectively impossible. That conclusion was too strong and has been corrected.

**Different raw units do not by themselves prevent a union-level Romano-Wolf procedure.** Stepdown procedures operate on test statistics, not on raw estimands, and a **studentized** statistic — an estimated effect divided by its own bootstrap standard error — is dimensionless. An R-multiple effect and a dollar effect can therefore be placed on comparable testing scales without converting one economic quantity into the other. Studentization is a standard inferential device, not an arbitrary economic-unit conversion, and it should not be described as one.

What the mixed units do mean is narrower: the raw point estimates (0.19 R/trade, $456.87/month) remain non-comparable and must never be ranked, averaged, or plotted on a shared axis. That is a reporting constraint, not an inferential barrier.

The genuine obstacles to a union-level procedure are elsewhere, and none is about units:

- the joint resampling design must be settled (D1-D4), including whether family-specific block lengths can coexist with a joint cross-family draw;
- observations must be alignable across families whose natural units differ (session-clustered for FH-1, monthly for FH-2 and FH-3);
- the heavily overlapping windows must have their contemporaneous dependence preserved rather than destroyed by independent per-family resampling;
- the model set must be defined (D8, D9).

**Within-family versus union-level control therefore remains an open methodological choice (D7), not a foregone conclusion.** A union-level procedure using reviewer-approved studentized statistics may well be technically feasible; whether it is preferable to three within-family procedures depends on the items above and on how much the reviewer values union-level error control against the added assumptions it requires. This document does not recommend separate families merely because their raw estimands carry different units.

## Estimand precision note

Every estimand above is a **population mean** quantity, not the observed sample statistic already committed in the M43-M55 reports. The committed figures are point estimates of these estimands with no uncertainty attached; that is precisely the gap P3 would close. Nothing in this map re-derives, re-estimates, or restates a committed figure as if it carried an interval.

## Required-series summary

Every one of the eleven test instances depends on at least one series that P2 classified `SUMMARY_ONLY` pending the cache-integrity preflight. **Zero test instances are executable from committed evidence alone.** The factor series required for any Fama-French/UMD attribution of TI-08 through TI-11 is separately classified `NOT_AVAILABLE` in the record, and acquiring it is a new-data decision outside P3A scope.

## Path-dependent risk estimands are not paired monthly observations

Maximum drawdown, worst month, downside deviation, and expected shortfall are **functionals of the complete equity path**, not quantities observed independently per signal date or per month. Two consequences bind every instance in this map, and TI-10 and TI-11 most directly:

1. **No per-month paired drawdown observation exists.** A drawdown is defined by the running maximum over the whole path; it has no monthly decomposition that can be differenced pair by pair. Treating "paired difference in max drawdown" as an ordinary paired-mean estimand — as an earlier draft of this document did — is a category error, and it has been corrected. The comparison is between two scalar path functionals, one per arm, evaluated on paths that happen to be paired by construction.
2. **Return and risk endpoints require separate inferential treatment.** The paired monthly-dollar difference has a well-defined per-period observation structure and can enter a block-resampling scheme directly. Drawdown-type functionals must instead be handled through the empirical distribution of *resampled paths*, with uncertainty read off the distribution of the functional rather than from a mean-difference standard error.

**No joint null combining mean monthly return and maximum drawdown may be constructed without an explicitly reviewer-approved multivariate decision rule.** Declaring an overlay "better" because return held while drawdown fell is a two-dimensional claim, and collapsing it into one test requires stating in advance how the two dimensions trade off. That rule does not exist yet. It is recorded as **D13** in `STATISTICAL_DECISION_REGISTER.md`.

Whether the risk functionals are primary or secondary endpoints is itself unresolved. This matters for CH-7 in particular, whose research question is explicitly about the risk-return trade-off rather than return alone: if drawdown is demoted to a secondary descriptive endpoint, the milestone's own motivating question is answered only informally.
