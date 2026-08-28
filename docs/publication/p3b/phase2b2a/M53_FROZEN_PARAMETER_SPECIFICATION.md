# M53 Frozen Parameter Specification

The only later command is the cache-only invocation, from the isolated M53 checkout:

```text
<approved-python-executable> -m scripts.research_momentum_risk_control
```

No argument follows the module name.  In particular `--fetch-missing` is omitted, so its `store_true` default is `false` (historical wrapper line 260).  The child process must receive cleared `ALPACA_API_KEY`, `ALPACA_SECRET_KEY`, `ALPACA_API_KEY_ID`, `ALPACA_API_SECRET_KEY`, `APCA_API_KEY_ID`, `APCA_API_SECRET_KEY`, `ALPACA_BASE_URL`, and `ALPACA_FEED`; any credential request or network attempt stops the run.

## Complete digest record

This is the complete static parameter record.  Each record is one UTF-8 line `name<TAB>type<TAB>value<TAB>source<TAB>mode<LF>`, sorted by bytewise name.  Literal `null` is the four-byte string shown.  The 49 lines total `5018` bytes and SHA-256 to `7c2b2da4168333c198e44bb573655d995c129931ac2ae8b973a0985b77e4a0ad`.  A later execution must mechanically reconstruct these exact lines from the isolated source, declared command, and verified handoff before execution; otherwise it is blocked.

| Name | Type | Value | Source / mode |
|---|---|---|---|
| arm_a_label | string | BREADTH_30 | `src/momentum_risk_control_research.py:76`, constant |
| arm_a_top_n | integer | 30 | same:80, constant |
| arm_b_label | string | MARKET_VOL_15 | same:77, constant |
| arm_b_top_n; baseline_top_n | integer | 15 | same:83 via M52 `TOP_N`; wrapper:238 literal |
| baseline_vol_target_annualized | null | null | wrapper:238 literal |
| base_cost_bps; stress_cost_bps | decimal-string | 0.0005; 0.0010 | `src/cross_sectional_momentum_research.py:140-141` |
| cache roots | repo-relative-path | `artifacts/research-cross-sectional-momentum/{daily-bar-cache,openfigi-cache,sec-nport-cache}` | `scripts/research_cross_sectional_momentum.py:70-72` |
| calendar_segments | json-array | `["2021","2022","2023","2024","2025","2026_YTD"]` | cross-sectional source:147 |
| cli_fetch_missing | boolean | false | wrapper:260, omitted default |
| command_module | string | scripts.research_momentum_risk_control | wrapper:328-330, explicit |
| evaluation_start; evaluation_end; warmup_target_start | date | 2021-01-04; 2026-08-13; 2020-01-02 | cross-sectional source:143-145 |
| iwb_cik; iwb_series_id; iwb_class_id | string | 0001100663; S000004347; C000012077 | same:155-157 |
| liquidity_lookback_sessions | integer | 60 | same:134 |
| min_median_close; min_median_dollar_volume; min_median_share_volume; min_session_coverage_pct | decimal-string/integer | 10; 50000000; 500000; 0.95 | same:135-138 |
| momentum_lookback_sessions; momentum_skip_sessions; trend_filter_sma_sessions | integer | 252; 21; 200 | same:130-132 |
| market_vol_lookback_sessions; market_vol_target_annualized | integer/decimal-string | 63; 0.15 | risk-control source:86,89 |
| m52_input_path; m52_input_bytes; m52_input_sha256 | path/integer/sha256 | fixed M52 path; 34124; `590672f...ac8f9` | wrapper:62; historical artifact |
| nport_search_start | date | 2019-06-01 | M52 wrapper:78 |
| starting_capital | decimal-string | 30000 | cross-sectional source:128 |
| target classification | decimal/integer values | `$500`, 25%, 5 segments, 55%, -10%, 35% max symbol | cross-sectional source:169-178 |
| selection_rule | string | one feasible; else stress mean descending; base drawdown ascending; turnover ascending; else fail | risk-control source:122-163 |

The exact 49-record serialization follows; it is normative.  The compact table is explanatory only.  It includes every direct CLI/default, source constant, date, cost, universe identity, baseline/stress definition, M53 arm definition, classification/selection threshold, cache selection, and M52 input that can affect output.  The static import closure is separately pinned in the source-closure record; no external configuration file is read by this historical command.  `config/strategy_v0.yaml` is not an M53 input and remains `strategy.enabled=false`.

The pre-run evidence must include the reconstructed 49-line byte stream and hash, line-level source verification at the historical commit, no unapproved environment variable that changes data retrieval, exact command/executable, and an explicit assertion that no seed exists in the source or command.  Equality is never inferred from an absence of intentional edits.

## Normative 49-line record

```text
alpaca_feed	string	sip	scripts/research_cross_sectional_momentum.py:76	constant
alpaca_fetch_end_exclusive	date	2026-08-14	scripts/research_cross_sectional_momentum.py:77	constant
arm_a_label	string	BREADTH_30	src/momentum_risk_control_research.py:76	constant
arm_a_top_n	integer	30	src/momentum_risk_control_research.py:80	constant
arm_b_label	string	MARKET_VOL_15	src/momentum_risk_control_research.py:77	constant
arm_b_top_n	integer	15	src/momentum_risk_control_research.py:83	constant via TOP_N
base_cost_bps	decimal-string	0.0005	src/cross_sectional_momentum_research.py:140	constant
baseline_top_n	integer	15	scripts/research_momentum_risk_control.py:238	literal
baseline_vol_target_annualized	null	null	scripts/research_momentum_risk_control.py:238	literal
cache_daily_bar_root	repo-relative-path	artifacts/research-cross-sectional-momentum/daily-bar-cache	scripts/research_cross_sectional_momentum.py:72	constant
cache_openfigi_root	repo-relative-path	artifacts/research-cross-sectional-momentum/openfigi-cache	scripts/research_cross_sectional_momentum.py:71	constant
cache_sec_nport_root	repo-relative-path	artifacts/research-cross-sectional-momentum/sec-nport-cache	scripts/research_cross_sectional_momentum.py:70	constant
calendar_segments	json-array	["2021","2022","2023","2024","2025","2026_YTD"]	src/cross_sectional_momentum_research.py:147	constant
cli_fetch_missing	boolean	false	scripts/research_momentum_risk_control.py:260	omitted store_true default
command_module	string	scripts.research_momentum_risk_control	scripts/research_momentum_risk_control.py:328-330	explicit
evaluation_end	date	2026-08-13	src/cross_sectional_momentum_research.py:144	constant
evaluation_start	date	2021-01-04	src/cross_sectional_momentum_research.py:143	constant
iwb_cik	string	0001100663	src/cross_sectional_momentum_research.py:155	constant
iwb_class_id	string	C000012077	src/cross_sectional_momentum_research.py:157	constant
iwb_series_id	string	S000004347	src/cross_sectional_momentum_research.py:156	constant
iwb_ticker	string	IWB	src/cross_sectional_momentum_research.py:158	constant
liquidity_lookback_sessions	integer	60	src/cross_sectional_momentum_research.py:134	constant
m52_input_bytes	integer	34124	artifacts/research-cross-sectional-momentum/results/m52_result.json	historical artifact
m52_input_path	repo-relative-path	artifacts/research-cross-sectional-momentum/results/m52_result.json	scripts/research_momentum_risk_control.py:62	fixed reader path
m52_input_sha256	sha256	590672f029a519b665b856106d0b35d701b464aa3f12b5a0b1978f18b18ac8f9	Phase 2A expected-result freeze	historical artifact
market_vol_lookback_sessions	integer	63	src/momentum_risk_control_research.py:86	constant
market_vol_target_annualized	decimal-string	0.15	src/momentum_risk_control_research.py:89	constant
min_median_close	decimal-string	10	src/cross_sectional_momentum_research.py:135	constant
min_median_dollar_volume	decimal-string	50000000	src/cross_sectional_momentum_research.py:136	constant
min_median_share_volume	integer	500000	src/cross_sectional_momentum_research.py:137	constant
min_session_coverage_pct	decimal-string	0.95	src/cross_sectional_momentum_research.py:138	constant
momentum_lookback_sessions	integer	252	src/cross_sectional_momentum_research.py:130	constant
momentum_skip_sessions	integer	21	src/cross_sectional_momentum_research.py:131	constant
not_promising_max_drawdown_pct	decimal-string	0.35	src/cross_sectional_momentum_research.py:177	constant
not_promising_min_positive_segments	integer	3	src/cross_sectional_momentum_research.py:178	constant
nport_search_start	date	2019-06-01	scripts/research_cross_sectional_momentum.py:78	constant
positive_insufficient_max_drawdown_pct	decimal-string	0.35	src/cross_sectional_momentum_research.py:176	constant
selection_rule	string	one feasible; else stress mean desc; then base drawdown asc; then turnover asc; else fail	src/momentum_risk_control_research.py:122-163	frozen source rule
starting_capital	decimal-string	30000	src/cross_sectional_momentum_research.py:128	constant
stress_cost_bps	decimal-string	0.0010	src/cross_sectional_momentum_research.py:141	constant
target_max_base_drawdown_pct	decimal-string	0.25	src/cross_sectional_momentum_research.py:170	constant
target_max_symbol_contribution_share	decimal-string	0.35	src/cross_sectional_momentum_research.py:175	constant
target_min_base_mean_monthly_dollars	decimal-string	500	src/cross_sectional_momentum_research.py:169	constant
target_min_positive_segments	integer	5	src/cross_sectional_momentum_research.py:171	constant
target_min_profitable_month_pct	decimal-string	0.55	src/cross_sectional_momentum_research.py:173	constant
target_worst_month_floor_pct	decimal-string	-0.10	src/cross_sectional_momentum_research.py:174	constant
total_segments	integer	6	src/cross_sectional_momentum_research.py:172	constant
trend_filter_sma_sessions	integer	200	src/cross_sectional_momentum_research.py:132	constant
warmup_target_start	date	2020-01-02	src/cross_sectional_momentum_research.py:145	constant
```
