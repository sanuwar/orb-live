# M53 Handoff Dependency Audit

## Static evidence and trace

This audit inspected only immutable historical commit `4c43fe96ab80d739fd10a9bd6bfa4c1319f67865`, `scripts/research_momentum_risk_control.py` (blob `cf049fa9cae08a4948b59f220f9f97c02621e7f4`).  It was not executed.  The fixed input path is line 62; JSON is loaded at lines 122-129; parity reads occur at lines 132-176; the parity object is copied into the M53 output at line 284.

M53 does **not** validate the complete input artifact SHA-256 and contains no hash of `m52_result.json`.  It reads M52 only to require parity between a newly recomputed M52 baseline and selected historical M52 metrics, then places two M52 metadata values in its own `baseline_parity` output.  It does not copy complete M52 metadata.

## Consumed paths

The following paths are consumed for numerical/structural parity in **both** prefixes `/base_metrics` and `/stress_metrics` (58 exact scalar paths total):

`/mean_monthly_dollars`, `/median_monthly_dollars`, `/gross_pnl`, `/realized_net_pnl`, `/equity_net_pnl`, `/endpoint_marked_net_pnl`, `/cagr`, `/sharpe_ratio`, `/sortino_ratio`, `/annualized_downside_deviation_pct`, `/max_drawdown_dollars`, `/max_drawdown_pct`, `/worst_month_dollars`, `/worst_month_pct`, `/fifth_percentile_month_pct`, `/profitable_month_pct`, `/longest_losing_streak_months`, `/total_costs`, `/turnover`, `/avg_invested_positions`, `/max_invested_positions`, `/avg_cash_utilization_pct`, `/best_symbol_positive_contribution_share`, `/signal_count`, `/intended_order_count`, `/fill_count`, `/blocked_intent_count`, `/final_equity`, `/final_cash`.

Thus, for example, the first is `/base_metrics/mean_monthly_dollars` and `/stress_metrics/mean_monthly_dollars`; each listed suffix is an exact consumed path under each prefix.  All are numerical research fields except `cost_label`, which is not read.  Source basis: lines 76-83 and 144-160.

For each of the six historical segment labels `2021`, `2022`, `2023`, `2024`, `2025`, and `2026_YTD`, M53 consumes the exact structural/numerical paths `/base_metrics/segments/<label>/net_pnl`, `/base_metrics/segments/<label>/positive`, `/base_metrics/segments/<label>/month_count`, and the same three under `/stress_metrics/segments/<label>` (36 paths).  `net_pnl` is numerical; `positive` is boolean structural; `month_count` is integer structural.  Source basis: lines 161-166.  The labels are iterated from the recomputed baseline, so an absent historical label is a hard mismatch rather than a substitute.

Two additional metadata/provenance paths are consumed:

| Exact path | Category | Use | Expected versus regenerated |
|---|---|---|---|
| `/generated_at` | metadata | copied as `baseline_parity.m52_generated_at` (line 175) | **different**; affects M53 metadata only |
| `/generation_git_head_sha` | provenance | copied as `baseline_parity.m52_git_head_sha` (line 176); also appears in a parity-failure message (line 168) | identical in the preserved pair |

All 94 parity paths and `/generation_git_head_sha` are identical between the expected and regenerated M52 artifacts.  `/generated_at` is the one consumed field that is not identical.  Neither differing source-provenance field is read: `/generation_source_file_sha256/scripts~1research_cross_sectional_momentum.py` and `/stage0/item10_generation_provenance/generation_source_file_sha256/scripts~1research_cross_sectional_momentum.py` have no M53 reader path.

## Consequence

Artifact choice cannot affect M53's recomputed numerical result through the observed differences: the M53 numerical parity reader sees identical values, and `generated_at` is copied only to metadata.  It can affect M53 output metadata and therefore complete M53 output bytes.  Because M53 consumes `generated_at`, the regenerated M52 artifact does not meet a literal “every consumed M52 field identical” condition.  This audit alone does not authorize execution.
