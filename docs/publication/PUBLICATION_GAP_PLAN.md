# Publication Gap Plan

## Work that can begin now

| Work package | Required output | Completion criterion |
|---|---|---|
| Claim discipline | Freeze CLAIM_EVIDENCE_MATRIX.md and require manuscript claims to inherit its status/guardrail | Two-person human review; zero unsupported claims in manuscript |
| Hypothesis ledger | Resolve 14-versus-17 counting ambiguity and assign each variant a family, control status, primary endpoint, and decision role | Machine-readable and human-readable ledgers agree |
| Literature/novelty review | Review preregistration, multiverse/multiple testing, reproducible computational finance, research-software correction practice, and LLM-assisted science governance | Narrow novelty statement with primary scholarly citations |
| Environment | Lock Python/dependency versions; document platform assumptions; add one-command synthetic/offline demonstration | Clean external machine reproduces non-proprietary fixtures and tables |
| Artifact archive design | Define code release, manifests, checksums, correction lineage, and release notes | Release candidate can be archived without secrets/vendor data |
| Data-license review | Review SEC EDGAR, OpenFIGI, Alpaca SIP/IEX, fund-holdings, and proxy-source terms | Written redistribution/access matrix reviewed by a knowledgeable human |
| Actor provenance | Label human, Claude, other-AI, automated-test, and mixed review/detection roles where records support it | No “human detected” or “AI detected” statement without evidence |
| External review | Recruit one finance/econometrics reviewer and one research-software/reproducibility reviewer | Written review and resolved comment log |
| Journal pre-check | Send a concise presubmission scope inquiry to JORS/MethodsX if permitted | Written editorial response retained |

## Formal multiple-testing treatment

1. Freeze the P1 hypothesis-counting rule and define the family hierarchy before computing p-values.
2. Identify one primary endpoint per family. Secondary outcomes remain descriptive.
3. Resample all strategies sharing a window jointly so contemporaneous market dependence is preserved.
4. Use stationary or moving-block bootstrap with block length selected transparently and sensitivity-tested.
5. Report a family-wise procedure such as Romano–Wolf stepdown and an FDR procedure such as Benjamini–Hochberg as a secondary view.
6. Consider White's Reality Check or Hansen's SPA for data-snooping-aware strategy comparison.
7. Include pre-M43 discovery work qualitatively in the selection-bias narrative even if raw candidate counts cannot be reconstructed.
8. Never pool M55 prospective observations into retrospective selection-adjustment calculations.

## Confidence intervals and resampling

- Monthly mean return and mean monthly dollars: block-bootstrap percentile and BCa intervals.
- CAGR and volatility ratios: bootstrap with explicit finite-sample cautions.
- Drawdown, worst month, and recovery time: empirical path distribution; do not attach naive normal-theory intervals.
- Trade-level ORB outcomes: resample by session/month, not individual trades, to preserve clustering and no-trade days.
- Momentum: resample time blocks jointly across constituents/portfolio returns; preserve cross-sectional dependence within each date.
- Report sensitivity across defensible block lengths and cost cases.

## Factor regressions and benchmarks

For M49 and M52/M53, construct investable daily or monthly return series without altering selection rules.

- Regress excess returns on market, size, value, profitability, investment, and momentum factors (Fama–French five factors plus UMD), with HAC/Newey–West uncertainty.
- Report beta, intercept, adjusted R-squared, residual diagnostics, and subperiod stability.
- Compare with SPY, IWB, equal-weight and matched-risk benchmarks; include transaction costs consistently.
- For M52/M53, distinguish loading on the known momentum factor from implementation-specific residual return.
- For M49, compare shared-account performance with a simple fixed allocation benchmark of the same sleeves.
- Do not call an intercept “alpha” without robust inference and multiple-testing context.

## Independent and untouched validation

### Historical route

Acquire licensed minute data covering the frozen 2019–2020 M50 window and rerun the exact M49 protocol without changing feed, universe, window, or gates. If the exact frozen vendor/feed cannot lawfully or technically cover it, define a new validation protocol before inspecting results and explicitly classify it as a new study, not a repair that preserves comparability.

### Prospective route

Maintain M55 unchanged for its minimum 12 completed monthly signal cycles. Interim checkpoints are descriptive only. Do not promote, activate, retune, or select based on interim results. Any correction must preserve prior checkpoints and append an explicit superseding record.

## Data and reproducibility package

- Public source release with OSI-approved license if permissible.
- CITATION.cff, contributor guide, code of conduct, security policy, versioned documentation, examples, and tagged release.
- Frozen environment/lockfile and container image digest.
- Synthetic or redistributable fixture data sufficient to exercise every gate and reproduce example tables.
- Manifest of non-redistributable inputs with vendor, endpoint, parameters, timestamps, hashes, and acquisition instructions.
- One-command generation of publication tables from committed derived artifacts.
- Explicit separation between raw licensed data, derived shareable data, and non-shareable caches.
- Reproduction log on a clean external environment.

## OSF / Zenodo / archive plan

1. Use GitHub release plus Zenodo DOI for the exact code version, if the repository can be made public and licensed.
2. Use OSF for the study dossier, protocol documents, analysis plan, disclosure, and permissible derived artifacts.
3. Cross-link DOI, commit SHA, issue/PR, and correction records.
4. Archive the environment manifest and checksums.
5. Deposit no secrets, credentials, personal data, or vendor data without written redistribution permission.
6. Version future M55 checkpoint deposits; never replace an earlier archive silently.

## Work that must wait for M55 evidence

- Any statement about prospective return, drawdown, hit rate, robustness, or degradation.
- Comparison of prospective versus retrospective M52 performance.
- Prospective confidence intervals that require observed cycles.
- Any decision to continue, stop, activate, or retune the strategy.
- Final empirical-finance manuscript conclusions.

The first eligible signal is 2026-08-31, first possible simulated execution is 2026-09-01, and the first useful checkpoint run is 2026-09-02 morning. Do not run early.

## Submission gates

A methods/software submission may proceed when the public software/reproducibility, licensing, novelty, actor-provenance, and external-review gates pass.

An empirical-finance submission additionally requires completed multiple-testing treatment, confidence intervals, factor/benchmark analysis, and at least one completed independent historical or prospectively accumulated validation stream. M55's protocol calls for a minimum 12 completed monthly cycles; a shorter interim sample is not enough for promotion.
