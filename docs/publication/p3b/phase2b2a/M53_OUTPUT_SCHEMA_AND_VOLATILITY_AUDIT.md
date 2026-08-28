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

Each of `baseline`, `arm_a_breadth_30`, and `arm_b_market_vol_15` has two metrics objects.  Each metrics object has exactly: `annualized_downside_deviation_pct`, `annualized_volatility_pct`, `avg_cash_utilization_pct`, `avg_invested_positions`, `best_symbol_positive_contribution_share`, `blocked_intent_count`, `cagr`, `cost_label`, `endpoint_marked_net_pnl`, `equity_net_pnl`, `fifth_percentile_month_pct`, `fill_count`, `final_cash`, `final_equity`, `gross_pnl`, `intended_order_count`, `longest_losing_streak_months`, `max_drawdown_dollars`, `max_drawdown_pct`, `max_invested_positions`, `mean_monthly_dollars`, `median_monthly_dollars`, `profitable_month_pct`, `realized_net_pnl`, `recovery_days`, `segments`, `sharpe_ratio`, `signal_count`, `sortino_ratio`, `spy_buy_and_hold_comparison`, `total_buy_notional`, `total_costs`, `total_sell_notional`, `turnover`, `worst_month_dollars`, and `worst_month_pct`.  Decimal research quantities are serialized strings by `_json_default` (lines 114-119); counts are integers; `avg_invested_positions` is a JSON number; `recovery_days` is nullable.

Each `segments` object has exactly the six objects `2021`, `2022`, `2023`, `2024`, `2025`, `2026_YTD`; each has numerical-string `net_pnl`, integer `month_count`, and boolean `positive` (metrics source lines 1027-1034).  Each `spy_buy_and_hold_comparison` is an object with numerical-string `final_equity`, `net_pnl`, and `max_drawdown_pct`.  Each `classification` is an object with a verdict string and `gate_checks`; the latter is a boolean structural object whose exact keys depend only on the frozen classification branch, not iteration order (lines 1111-1163 of the cross-sectional module).

Each `stage0` is an object with `_all_ok`, `item1_universe_snapshot_available_every_signal_date`, `item3_identifier_resolution`, `item4_daily_bar_coverage`, `item5_corporate_actions`, `item6_calendar`, `item7_parity`, `item8_synthetic_proofs`, `item9_no_duplicates_no_unclassified_blocked`, and `item10_generation_provenance`.  Nested objects are respectively `{ok,signal_date_count}`, `{total_holdings_across_all_snapshots,total_unresolved_or_ambiguous,unresolved_pct}`, `{spy_available,symbols_requested,symbols_with_any_data}`, note-only objects for items 5–8, `{duplicate_transaction_count,ok,unclassified_blocked_intent_count}`, and `{generation_git_head_sha,generation_source_file_sha256,generation_working_tree_clean}`.  Its source is the statically called M52 `run_stage0_checks` at wrapper lines 226-229.  These fields include research counts, booleans, and provenance; none are excluded.

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
