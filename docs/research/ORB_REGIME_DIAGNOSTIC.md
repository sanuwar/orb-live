# ORB Regime Diagnostic (Issue #310, resumed as Issue #315)

Research-only. Explains, using only decision-time-knowable information,
why gap/RVOL configurations that looked strong in the 90-session discovery
period (2026-04-07..2026-08-13) degraded out-of-sample, and whether any
decision-time regime signal survives in **both** periods.

**This report uses the corrected trade-quality evidence from Issue
#313/PR #314** (candidate frequency was always valid; trade-quality was
truncated by a calendar bug, Issue #311/PR #312, now fixed), not the
superseded numbers in the original `docs/research/ORB_OUT_OF_SAMPLE_VALIDATION.md`.
This study's own OOS-side outcome simulation was re-verified independently
in this run and its closed-trade counts match #314's corrected numbers
exactly (cross-validation, not reuse of #314's artifact).

> **Metric-unit note (Issue #321/PR #322), independent of the calendar
> bug above.** Legacy "expectancy" figures throughout this document
> (including the `PROMISING` classification for the RVOL 1.50-1.99x band,
> e.g. "expectancy 1.785" / "expectancy 0.784") are computed from raw
> price-per-share realized P&L -- they are **NOT** R-normalized.
> Risk-normalized profitability must be evaluated using R-based metrics
> (`expectancy_r_per_trade`/`profit_factor_r`, introduced by PR #322).
> **Risk-normalized interpretation of the `PROMISING` verdict below is
> provisional pending a dedicated R-normalized re-evaluation** -- the
> same class of price/R sign flip Issue #321's own correction found for
> that study's P1 period is possible here too and was not checked. No
> trade counts, win rates, or dollar-based figures in this document have
> changed -- this is a labeling clarification only, not a rerun. See
> `docs/research/ORB_BASELINE_FOURTH_PERIOD_VALIDATION.md` for the
> corrected R-based methodology.

## Periods and configurations

| | |
|---|---|
| Discovery | 2026-04-07 .. 2026-08-13, 90 sessions |
| Out-of-sample | 2025-04-03 .. 2026-04-06, 252 sessions |
| Primary configs | A = 0.50%/1.25x, B = 0.50%/1.50x, C = 0.75%/1.50x |
| Control | 1.00%/3.00x (context only) |
| Minimum sample | 15 closed trades in **both** periods before any bucket is interpreted (declared before any result was computed) |

Config-letter note: this study's own A/B/C labels differ from #304/#307's
A-E labels. Mapping: this study's **A (0.50%/1.25x) = #307's E**; this
study's **B (0.50%/1.50x) = #307's C**; this study's **C (0.75%/1.50x) =
#307's B**; **CONTROL (1.00%/3.00x) = #307's A**.

## Overall trade quality (corrected, cross-validated against #314)

| Config | Period | Closed | Win Rate | Expectancy | PF |
|---|---|---|---|---|---|
| A (0.50/1.25) | Discovery | 75 | 54.7% | 1.529 | 2.270 |
| A (0.50/1.25) | OOS | 199 | 40.2% | 0.108 | 1.065 |
| B (0.50/1.50) | Discovery | 57 | 57.9% | 1.042 | 1.789 |
| B (0.50/1.50) | OOS | 150 | 40.7% | 0.258 | 1.156 |
| C (0.75/1.50) | Discovery | 50 | 56.0% | 0.685 | 1.472 |
| C (0.75/1.50) | OOS | 126 | 40.5% | 0.178 | 1.104 |
| CONTROL | Discovery | 12 | 50.0% | -0.464 | 0.836 |
| CONTROL | OOS | 30 | 40.0% | -0.946 | 0.558 |

**Validation:** OOS closed-trade counts (199/150/126/30) match candidate
counts almost exactly (202/152/127/30) -- confirming this study's own
`simulate_shadow_execution` calls are correctly using the research
calendar provider for the 2025 portion of the period, not silently
truncating as the pre-#312 code did.

## Primary question: why did results degrade?

**ORB did not lose money broadly across the older period -- it degraded
broadly but stayed marginally-to-moderately profitable, and the
degradation is concentrated in a specific, identifiable feature: the
loosest RVOL bucket.**

### RVOL magnitude: the clearest, most economically plausible finding

| Config | Bucket | Discovery exp | OOS exp | Verdict |
|---|---|---|---|---|
| A | 1.25-1.49x | **3.070** (best in period) | **-0.350** (near-worst) | Reversed -- drives A's degradation |
| A | 1.50-1.99x | 1.785 | 0.784 | Same direction, degraded but positive |
| A | 2.00-2.99x | 1.018 | 0.278 | Same direction, degraded but positive |
| A | >=3.00x | -0.409 | -0.854 | Weak in both periods |
| B | 1.50-1.99x | 1.785 | 0.784 | **PROMISING** (see below) |
| C | 1.50-1.99x | 1.361 | 1.027 | **PROMISING** (see below) |

Config A's 1.25-1.49x bucket (the loosest RVOL band, admitted only
because A's own threshold sits at 1.25x) was the single **best**-performing
bucket in the discovery period (expectancy 3.07, the outlier that likely
drove much of A/E's discovery-period appeal) and became one of the
**worst** out-of-sample (-0.35). Every other RVOL bucket degraded but
stayed directionally consistent. This is a genuinely economically
plausible story: RVOL just barely above a 1.25x gate is a much noisier
signal than RVOL meaningfully above it, and that noise happened to read
as strongly positive in the one 90-session sample that selected the
threshold in the first place.

### Breakout timing

| Bucket | Discovery exp (n) | OOS exp (n) |
|---|---|---|
| 09:40-09:59 | 1.959 (55) | 0.278 (148) |
| 10:00-10:29 | -0.560 (14, below minimum) | 0.338 (35) |
| 10:30-11:00 | 2.458 (6, below minimum) | **-1.972 (16)** |

**MIXED, not a stable signal.** The early window (09:40-09:59) is
consistently positive in both periods but the direction across buckets is
not consistent (discovery's worst bucket, 10:00-10:29, is OOS's best,
while OOS's worst, 10:30-11:00, was discovery's best on a 6-trade sample
below the minimum threshold). No timing bucket clears the promising
criteria; the late-window OOS result (n=16, win rate 18.8%) is the single
worst cell in the whole study and deserves caution, not a rule, given the
small sample.

### Gap magnitude

Both periods' best-*performing* bucket is the loosest gap band
(0.50-0.74%: discovery exp 2.936, OOS exp 0.584) and both periods show a
dip at 0.75-0.99% (discovery 0.037, OOS -0.950). This did not clear the
PROMISING bar mechanically (OOS's 0.584 sits below this config's own
two-period average baseline of 0.818), but the same-shape dip in both
periods independently is a real, if inconclusive, pattern -- worth noting,
not acting on.

### SPY context (gap direction, VWAP premium)

Neither cleared minimum-sample-and-same-direction requirements cleanly.
SPY-gap-UP was discovery's best regime (exp 1.823) but only marginally
positive OOS (0.146); SPY-gap-DOWN flipped from positive in discovery
(1.005) to negative OOS (-0.566). VWAP premium showed a monotonic-ish
pattern in one period and the opposite shape in the other. **No stable
SPY-context signal found.**

### Day of week

Two mechanically PROMISING findings surfaced (Friday for config A,
Monday for config B) -- both clear minimum sample (n=19-25 discovery,
n=25-38 OOS) and both improve in the same direction in both periods. **We
treat these with lower confidence than the RVOL finding**: day-of-week
effects lack a clear economic mechanism the way RVOL magnitude does, and
neither day replicates across more than one configuration (unlike RVOL
1.50-1.99x, which is promising in *both* B and C independently). Classified
MIXED-leaning-PROMISING pending a specific reason to trust it; not
recommended as a standalone follow-up.

## Discovery vs OOS regime shift

- **RVOL distribution**: the loosest band (1.25-1.49x, config A only)
  shows the most dramatic shift -- best-to-worst. Other bands degrade
  uniformly rather than reversing.
- **Breakout timing distribution**: both periods concentrate the majority
  of trades in the 09:40-09:59 window (55/75 discovery, 148/199 OOS for
  config A) -- candidate generation timing itself did not shift, only
  quality within each bucket shifted, and not uniformly.
- **Gap distribution**: both periods' candidate mix concentrates in the
  >=1.50% bucket (38/75 discovery, 98/199 OOS) -- distribution shape is
  similar; performance within it degraded (1.383 -> 0.191) but stayed
  positive.
- **Candidate multiplicity**: OOS has proportionally *more* multi-candidate
  days than discovery (config A: 47/47 candidate-days had 2+ in 8/47
  discovery vs 45/110 in OOS -- roughly similar 2+ share, ~17% vs ~41%,
  actually higher in OOS) -- more simultaneous-setup mornings occurred in
  the longer OOS window, consistent with it simply covering more calendar
  time and more market regimes.
- **Symbol concentration**: TSLA's role changed. In config A, TSLA was
  present but not singularly dominant in the 90-session sample; over the
  full 252-session OOS period TSLA became the entire source of net
  profitability (see below) -- a longer period gave one strong-trending
  name more opportunity to dominate the P&L, not evidence of a changed
  market regime per se.

## One-trade-per-day vs all-candidates

| Config | Period | ALL_CANDIDATES exp | ONE_TRADE_PER_DAY exp |
|---|---|---|---|
| A | Discovery | 1.529 | 1.393 (slightly worse) |
| A | OOS | 0.108 | **0.663** (much better) |
| B | Discovery | 1.042 | 0.712 (worse) |
| B | OOS | 0.258 | **0.816** (much better) |

**MIXED, direction not consistent between periods.** In the discovery
period, restricting to the first candidate of the day was *slightly worse*
than taking every candidate -- multi-candidate mornings in discovery
carried real additional edge (see single-vs-multi below). In the corrected
OOS period, restricting to one trade per day is substantially *better*.
This means "always take only the first breakout" is not unconditionally
supported by both periods -- it happens to be the better rule in the
larger, more representative OOS sample, but the discovery-period evidence
alone would have argued the opposite. Reported honestly as a real
inconsistency, not smoothed over.

### Single-candidate vs multi-candidate days (a related but distinct view)

| Config | Period | Single-day exp | Multi-day exp |
|---|---|---|---|
| A | Discovery | 0.235 | **2.299** |
| A | OOS | 0.271 | 0.031 |
| B | Discovery | 0.079 | **1.909** |
| B | OOS | 0.571 | 0.071 |

Also **MIXED / reversed between periods**: discovery's multi-candidate
mornings were dramatically better than single-candidate ones (a "broad
market strength -> many good setups" story that is economically
plausible); OOS shows the opposite, with multi-candidate mornings diluting
returns toward roughly breakeven. Combined with the one-trade-per-day
finding above, the honest read is: **candidate multiplicity is not a
stable signal in either direction** -- it happened to favor "more" in the
sample that selected the thresholds and favor "fewer" in the larger,
independent sample.

## Symbol concentration and TSLA dependence

| Config | OOS total P&L | TSLA contribution | P&L excluding TSLA | Best non-TSLA symbol |
|---|---|---|---|---|
| A (0.50/1.25) | +21.51 | +39.98 | **-18.47 (negative)** | AAPL +9.75 |
| B (0.50/1.50) | +38.68 | +36.88 | +1.80 (still positive, barely) | META +21.33 |

Confirms the #314 correction's finding independently (this study's own
simulation, not a read of #314's artifact): **config A's entire net
out-of-sample profitability depends on TSLA.** Config B is materially more
resilient to the same test. Top-5 non-TSLA OOS contributors for A: AAPL
(+9.75), AMD (+6.64), WMT (+5.56), META (+2.93) -- genuinely broad but not
enough to offset TSLA's absence. This is diagnostic only; no symbol
filter is implemented or proposed here.

## Promising signal classification (final)

| Signal | Configs | Verdict | Basis |
|---|---|---|---|
| RVOL 1.50-1.99x outperforms other bands | B, C (independently) | **PROMISING** | Decision-time-knowable, economically plausible (moderate-above-threshold RVOL is less noisy than barely-above-threshold), n=26/70 (B) and n=22/56 (C) both periods, same direction both periods, survives removing best symbol in both |
| RVOL 1.25-1.49x underperforms | A | PROMISING (as a *negative* filter) | Same criteria, opposite direction -- explains rather than fixes A's degradation |
| Day-of-week (Friday for A, Monday for B) | A, B (different days) | MIXED | Clears mechanical criteria but weak economic rationale, no cross-config replication |
| Breakout timing | none | MIXED | Directionally inconsistent between periods |
| Gap magnitude dip at 0.75-0.99% | A (both periods show the shape) | MIXED | Consistent shape but does not clear the material-improvement-over-baseline bar |
| SPY gap direction / VWAP premium | none | NO_EVIDENCE | No consistent direction |
| One-trade-per-day vs all-candidates | A, B | MIXED | Reverses direction between periods |
| Single vs multi-candidate days | A, B | MIXED | Reverses direction between periods |

## Answers to the specific questions

1. **Did ORB lose money broadly, or in identifiable regimes?** Neither
   cleanly -- it degraded broadly (every RVOL/gap/timing bucket got worse)
   but the *sign flip* (profitable to unprofitable) for configs A/CONTROL
   specifically traces to one identifiable bucket (RVOL 1.25-1.49x for A)
   and one identifiable symbol (TSLA dependence for A). B and C degraded
   broadly but stayed net positive throughout.
2. **Was the discovery period structurally different?** Not in candidate
   generation (timing and gap distributions are similar). It *was*
   different in how well the loosest RVOL band performed, and TSLA's
   absolute dominance is a longer-sample artifact more than a regime
   difference.
3. **Any decision-time regime signal that improves results in BOTH
   periods?** Yes: RVOL in the 1.50-1.99x range specifically (not just
   "above 1.50x"), for both B and C independently.
4. **Does breakout timing matter?** Not in a way that clears the
   predeclared evidence bar. Directionally inconsistent.
5. **Does stronger RVOL actually help?** Not monotonically. 1.50-1.99x is
   the sweet spot; both the loosest band (1.25-1.49x) and the tightest
   (>=3.00x) underperform in the corrected OOS data.
6. **Do larger gaps help?** No clear evidence either way; the loosest gap
   band (0.50-0.74%) was the best-performing bucket in both periods, but
   this did not clear the material-improvement bar.
7. **Driven by one or two stocks?** For config A, yes -- entirely
   TSLA-dependent out-of-sample. For config B, no -- resilient to removing
   its best contributor.
8. **Are multi-candidate mornings better or worse?** Neither consistently
   -- reversed between periods.
9. **Enough evidence for one targeted follow-up?** Yes -- see below.

## Final decision

**TARGETED_FOLLOWUP_JUSTIFIED**

**Recommended next experiment (exactly one, not implemented here):**
validate an RVOL-band filter that requires relative volume in the
**1.50x-1.99x range specifically** (not merely "RVOL >= 1.50x", which
still admits the untested-here >=2.00x/>=3.00x bands) as a candidate
qualifier, replacing the current single-sided `minimum_relative_volume`
threshold, for configs B (0.75%/1.50x-1.99x) and C (0.50%/1.50x-1.99x).
This is the one finding that (a) is decision-time-knowable, (b) has a
plausible economic mechanism, (c) meets the sample-size bar in both
periods for two independent configurations, (d) improves in the same
direction in both periods, and (e) survives removing each config's best
contributing symbol. It should be validated on a third, independent
period before any production consideration -- this study alone is not
sufficient to authorize a threshold change, and none is made here.

Do not: expand this into a new broad threshold grid, add a day-of-week
production rule, or add a symbol blacklist/whitelist. None of those clear
the bar this study set for itself.

## Reproducing

```
python scripts/research_orb_regime_diagnostic.py
```

Structured outputs:
`artifacts/research-regime-diagnostic/results/orb_regime_diagnostic_report.json`,
`orb_regime_diagnostic_trades.csv` (720 rows; decision-time feature columns
and outcome columns are disjoint by construction, asserted by test),
`orb_regime_diagnostic_regime_summary.csv` (278 rows),
`orb_regime_diagnostic_symbol_summary.csv` (82 rows),
`orb_regime_diagnostic_time_bucket_summary.csv` (24 rows).
