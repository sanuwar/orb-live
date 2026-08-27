# M53 Handoff Gate Decision

## Alternatives

| Alternative | Scientific meaning and reproducibility value | Contamination / chain claim | Exact-byte M53 comparability | Required disclosure |
|---|---|---|---|---|
| 1. Use preserved regenerated M52 | Reuses a payload shown retrospectively equal under this frozen criterion. | Metadata variance enters the M53 `baseline_parity` object; it weakens an unbroken historical-artifact chain.  Do not authorize merely because numerical values match. | M53 metadata changes through `m52_generated_at`; complete-byte comparability is additionally limited by M53's own timestamp. | Original M52 byte failure, three exclusions, M53 consumer audit, timestamp propagation, source materialization. |
| 2. Use original frozen historical M52 | Preserves the historical M52 artifact as the M53 baseline and the clearest chain-reproduction claim. | No regenerated-M52 metadata contaminates the handoff; it is the least interpretively ambiguous choice. | Best available baseline chain, but not a promise of M53 exact-byte equality because M53 itself writes generation-time metadata. | The M52 failure still applies to the separate regeneration attempt; state that historical artifact selection is a chain-control choice. |
| 3. Do not authorize a handoff | Avoids any new M53 result. | No execution or contamination risk; no added reproducibility evidence. | Not applicable. | Retain `NOT_RUN_DUE_TO_M52_STOP` in the Phase 2B-1 record. |

## Recommendation and decision

Recommend alternative 2, the original frozen historical M52 artifact, for any later M53 regeneration proposal.  This is not a finding that the regenerated M52 is unfit for research payload comparison; it follows from the literal M53 consumer audit and the need to keep metadata variance out of the baseline handoff when a historical artifact is available.

Decision: `READY_FOR_SEPARATELY_AUTHORIZED_M53_REGENERATION_WITH_LIMITATIONS`, **only with alternative 2** and only after this criterion is independently reviewed and merged.  This workstream does not authorize, schedule, or run M53.  Alternative 1 remains unapproved by this gate because M53 consumes a different `generated_at`, even though static inspection limits the effect to output metadata.

Blocking conditions for any separate authorization are: criterion PR not merged; original frozen M52 artifact unavailable or fails a fresh read-only raw-hash check; any new non-identical consumed field; a changed M53 commit/source/parameter set; cache-integrity preflight failure; or an attempt to broaden the frozen exclusion map.  A separately authorized execution must use its own approved evidence plan and disclose that exact-byte M53 comparability is limited by generation metadata.

## Independent-review correction (2026-08-27)

The decision is READY_TO_PREPARE_SEPARATE_M53_EXECUTION_PLAN_WITH_LIMITATIONS, only with alternative 2 after this PR is reviewed and merged. It does not authorize M53 execution. The M52 exclusion map is an input-handoff decision aid, not an M53 output criterion. Before execution, a separate plan must freeze an M53-specific output criterion and exclusion map, derived from static M53 source and the historical expected M53 artifact before seeing a regenerated M53 artifact. The original historical M52 artifact must pass an immediate pre-handoff raw-hash check.
