# M53 Output Schema and Volatility Audit

This audit statically traces historical wrapper blob `cf049fa9cae08a4948b59f220f9f97c02621e7f4` at commit `4c43fe96ab80d739fd10a9bd6bfa4c1319f67865` and the expected artifact before any regenerated M53 output exists.  The writer constructs `result_doc` at lines 281-304 and writes sorted, indented JSON at line 308.  Therefore output object key ordering is deterministic (`sort_keys=True`); there are no arrays in the emitted artifact and hence no array ordering exception.

## Complete schema inventory

| Top-level field | Type / nested inventory | Source expression |
|---|---|---|
| `milestone`, `issue` | string, integer | literals, line 283 |
| `generated_at` | timestamp string | lines 281,283; the sole volatile field |
| `baseline_parity` | object: `ok`, `compared_scalar_fields`, `compared_segments`, `m52_generated_at`, `m52_git_head_sha` | parity result, line 284; values formed at lines 171-177 |
| `baseline` | object: `base_metrics`, `stress_metrics`, `stage0`, `classification` | lines 285-288 |
| `arm_a_breadth_30` | object: `top_n`, `base_metrics`, `stress_metrics`, `stage0`, `classification` | lines 289-293 |
| `arm_b_market_vol_15` | object: `top_n`, `vol_lookback_sessions`, `vol_target_annualized`, `base_metrics`, `stress_metrics`, `stage0`, `classification` | lines 294-298 |
| `opportunity_cost_vs_m52_baseline` | object: `arm_a_base`, `arm_a_stress`, `arm_b_base`, `arm_b_stress`; each has five numerical delta fields | lines 274-279; helper lines 96-108 in the risk-control module |
| `m53_decision` | object: `decision`, `selected_arm`, `reason` | lines 266-271; frozen selection rule module lines 122-163 |
| `generation_git_head_sha` | string or null provenance | lines 106-111,301 |
| `generation_source_file_sha256` | object keyed by the three source paths | lines 66-70,93-95,302 |
| `generation_working_tree_clean` | boolean or null provenance | lines 97-103,303 |

Each of `baseline`, `arm_a_breadth_30`, and `arm_b_market_vol_15` has two metrics objects.  Each metrics object has exactly: `annualized_downside_deviation_pct`, `annualized_volatility_pct`, `avg_cash_utilization_pct`, `avg_invested_positions`, `best_symbol_positive_contribution_share`, `blocked_intent_count`, `cagr`, `cost_label`, `endpoint_marked_net_pnl`, `equity_net_pnl`, `fifth_percentile_month_pct`, `fill_count`, `final_cash`, `final_equity`, `gross_pnl`, `intended_order_count`, `longest_losing_streak_months`, `max_drawdown_dollars`, `max_drawdown_pct`, `max_invested_positions`, `mean_monthly_dollars`, `median_monthly_dollars`, `profitable_month_pct`, `realized_net_pnl`, `recovery_days`, `segments`, `sharpe_ratio`, `signal_count`, `sortino_ratio`, `spy_buy_and_hold_comparison`, `total_buy_notional`, `total_costs`, `total_sell_notional`, `turnover`, `worst_month_dollars`, and `worst_month_pct`.  `avg_invested_positions` is a JSON number; `blocked_intent_count`, `fill_count`, `intended_order_count`, `longest_losing_streak_months`, `max_invested_positions`, and `signal_count` are integers; `recovery_days` is null in the expected artifact; `cost_label` and all other scalar metrics are strings (decimal research quantities use `_json_default`, lines 114-119).

Each `segments` object has exactly the six objects `2021`, `2022`, `2023`, `2024`, `2025`, `2026_YTD`; each has string `net_pnl`, integer `month_count`, and boolean `positive` (metrics source lines 1027-1034).  Each `spy_buy_and_hold_comparison` is an object with string `final_equity`, `net_pnl`, and `max_drawdown_pct`.  Each observed `classification` is `{verdict: string, gate_checks: object}`.  The exact `gate_checks` object has these eleven boolean keys: `at_least_1_segment_positive`, `at_least_5_of_6_segments_positive`, `base_max_drawdown_le_25pct`, `base_max_drawdown_le_35pct`, `base_mean_monthly_dollars_ge_500`, `best_symbol_share_le_35pct`, `pooled_base_endpoint_marked_net_pnl_gt_0`, `pooled_stress_endpoint_marked_net_pnl_gt_0`, `profitable_months_ge_55pct`, `stress_mean_monthly_dollars_gt_0`, and `worst_month_ge_neg_10pct`.

Each `stage0` is an object with boolean `_all_ok`, `item1_universe_snapshot_available_every_signal_date`, `item3_identifier_resolution`, `item4_daily_bar_coverage`, `item5_corporate_actions`, `item6_calendar`, `item7_parity`, `item8_synthetic_proofs`, `item9_no_duplicates_no_unclassified_blocked`, and `item10_generation_provenance`.  Its nested exact inventories are: item 1 `{ok: boolean, signal_date_count: integer}`; item 3 `{total_holdings_across_all_snapshots: integer, total_unresolved_or_ambiguous: integer, unresolved_pct: string}`; item 4 `{spy_available: boolean, symbols_requested: integer, symbols_with_any_data: integer}`; items 5--8 each `{note: string}`; item 9 `{duplicate_transaction_count: integer, ok: boolean, unclassified_blocked_intent_count: integer}`; and item 10 `{generation_git_head_sha: string, generation_source_file_sha256: object, generation_working_tree_clean: boolean}`.  Its source is the statically called M52 `run_stage0_checks` at wrapper lines 226-229.

`baseline_parity` is exactly `{ok: boolean, compared_scalar_fields: integer, compared_segments: integer, m52_generated_at: string, m52_git_head_sha: string}`.  `generation_source_file_sha256` is the three-string object keyed by the M53 wrapper, cross-sectional module, and risk-control module paths.  `opportunity_cost_vs_m52_baseline` has exactly `arm_a_base`, `arm_a_stress`, `arm_b_base`, and `arm_b_stress`; each is an object of five strings: `cagr_delta`, `endpoint_marked_net_pnl_delta`, `max_drawdown_pct_delta`, `mean_monthly_dollars_delta`, and `turnover_delta`.  `m53_decision` is exactly `{decision: string, selected_arm: null|string, reason: string}` in the expected artifact (`selected_arm` is null).  These fields include research counts, booleans, strings, and provenance; none are excluded.

## Volatility decision table

| Potentially varying field | Exact historical source expression | Decision |
|---|---|---|
| `/generated_at` | `datetime.now(timezone.utc)` then `.isoformat()` (wrapper 281,283) | Exclude exactly this pointer. |
| `/baseline_parity/m52_generated_at` | `m52_result["generated_at"]` (wrapper 175) | Retain: original M52 handoff fixes it. |
| `/baseline_parity/m52_git_head_sha` | `m52_result["generation_git_head_sha"]` (176) | Retain: original historical input fixes it. |
| all source-hash values | `hashlib.sha256(p.read_bytes())` (93-95) | Retain: materialization gate controls them. |
| `generation_git_head_sha` | `git rev-parse HEAD` (106-109) | Retain: historical checkout gate controls it. |
| `generation_working_tree_clean` | `git status --porcelain` over the three hashed files (97-103) | Retain: isolated clean-source gate controls it. |
| research, classification, parity, cache-derived and decision fields | deterministic data-flow values; no execution-time expression in writer | Retain.  Names alone are not a volatility basis. |

The expected object has no platform paths, absolute paths, environment strings, or output arrays.  Output ordering is sorted by the writer, and all source dictionaries contributing to results are checked by exact JSON comparison rather than assumed harmless.
