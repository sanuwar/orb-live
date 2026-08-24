# Human–LLM Workflow Audit

## Scope and evidence boundary

This audit describes the workflow documented by the committed M43–M55 record. It does not infer unrecorded conversations or claim that every “independent review” was performed by a human. M54 reports substantial Claude Code / Claude Sonnet 5 assistance under human direction; exact product, model, version, and usage dates must be verified from retained records before submission.

## Responsibility map

| Activity | Human responsibility | LLM/Claude responsibility documented in the record | Accountability boundary |
|---|---|---|---|
| Research question and practical objective | Define the question, risk tolerance, and what decision matters | Help structure a bounded work order | Human owns relevance and interpretation |
| Protocol and scope | Approve frozen parameters, windows, classifications, stop gates, and prohibited actions | Translate the specification into code, tests, and report structure | Human approves protocol; repository commit timestamps the protocol |
| Safety | Require strategy.enabled=false, no broker writes/account access, no activation, no premature M55 run | Implement/check fail-closed guards and report safety status | Human retains operational authority; AI cannot authorize trading |
| Implementation | Review architecture and approve changes | Generate/refactor research modules, CLIs, tests, and documentation | AI output is untrusted until verified |
| Data provenance | Approve sources and licensing posture | Implement retrieval/parsing/cache/provenance logic | Human owns lawful use and source acceptance |
| Execution | Decide whether and when a bounded run is authorized | Run deterministic scripts and collect artifacts when authorized | Human controls go/no-go; M55 timing remains a hard stop |
| Review and correction | Demand independent checks and accept/reject corrections | Assist with code review, mismatch diagnosis, regression tests, and reruns | Human owns final truth claim and disclosure |
| Interpretation | Decide classifications and manuscript wording within frozen rules | Compute rule outputs and draft explanations | Human authors verify all numbers and conclusions |
| Publication | Select venue, authorship, disclosures, archive, and final submission | Assist with dossier and drafting | AI is not an author and cannot accept responsibility |

## Observed defect-and-correction record

| Milestone | Important defect produced or missed in the automated workflow | Detection evidence | Correction and verification |
|---|---|---|---|
| M45 | Downstream aggregation omitted the period-specific session calendar, creating false DATA_INVALID outcomes | Manual cross-check against P2 row-level outcomes | Forwarded the correct provider, reused unaffected checkpoints, regenerated results, added regression tests |
| M46 | Wrong market feed for liquidity; research basket rejected by production universe gate; concentration denominator/unit/key mismatches | Implausible zero-candidate run and later review of concentration math | SIP selection, in-memory research universe, positive-contribution denominator, corrected keys/units, rerun |
| M47 | Wrong gap/RVOL config and wrong evaluation universe; DST display and concentration logic issues | Baseline parity failure and independent review | Correct config, period-native universe assertion/audit field, full rerun, DST-aware conversion, tests |
| M48 | Nineteen findings across four review rounds, including IEX/SIP, compounding, rebalance, same-day stop, endpoint accounting, warm-up trading, one-session lookahead, anchor, downside-deviation, count labeling, provenance and segment completeness | Repeated independent-review passes and internal reconciliation failures | Preserved superseded figures, added tests/hashes/ledgers, reran from clean commits; Round 4 authoritative |
| M50 | First revision incorrectly treated universe sourcing as the blocker | Review supplied an alternate route to the same official SEC documents | Universe gate passed; full data check exposed the true IEX pre-2020-07-27 coverage blocker; no economics computed |
| M52 | Placeholder tickers, rate limiting, cross-scenario duplicate false positive, and zero-marking on a missing bar that fabricated 97% drawdown | Live failures and Stage-0 checks | Ticker validation, retry logic, per-scenario dedup, last-known-close marking, tests, corrected rerun |
| M55 | Parity master dates extended beyond M52's cutoff; safety grep matched its own denylist | Initialization gate failures | Separate parity calendar and self-definition exclusion; regression tests; all ten gates passed |

## What the record demonstrates

- AI-assisted code can produce plausible but consequential defects in configuration propagation, calendar handling, data semantics, accounting, and reporting.
- Deterministic parity, fail-closed Stage-0 gates, ledger reconciliation, exact-date checks, and independent review repeatedly prevented erroneous figures from becoming authoritative.
- Preserving superseded figures makes the correction process auditable.
- LLM assistance increased implementation throughput in this case, but the repository does **not** measure net time saved, defect rate versus human-only development, or causal quality improvement.

## Provenance limitations

1. “Independent review” is not consistently actor-labeled. The publication package must not say humans detected every defect unless external records verify that.
2. Prompt transcripts, exact model identifiers, context settings, and Claude Code versions are not committed with every milestone.
3. Human review depth is described as responsibility but not quantified.
4. Git authorship/committer metadata does not by itself distinguish human-authored from AI-assisted lines.
5. Test success demonstrates consistency with specified behavior, not correctness of the financial hypothesis.

## Reproducibility and accountability rules for the paper

- Name every AI tool/model/version that materially affected code, analysis, or prose, using verified records rather than memory.
- Preserve prompts or summarized work orders where lawful and practical; redact secrets and licensed data.
- Identify which conclusions were rule-generated and which required human judgment.
- Report corrections with original/superseding artifact identifiers.
- Require two human sign-offs before submission: numerical reconciliation and claim-language review.
- Do not use an AI system as an author, reviewer substitute, source of unverified citations, or final arbiter of novelty/significance.
