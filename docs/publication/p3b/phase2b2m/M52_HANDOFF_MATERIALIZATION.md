# M52 Handoff Materialization

## The artifact

Only the **original frozen historical M52 artifact** is eligible as the M53 handoff.

| Property | Frozen value |
|---|---|
| SHA-256 | `590672f029a519b665b856106d0b35d701b464aa3f12b5a0b1978f18b18ac8f9` |
| Size | 34,124 bytes |
| Path consumed by the M53 wrapper | `artifacts/research-cross-sectional-momentum/results/m52_result.json` |

## Authoritative source

The artifact lives under a gitignored `artifacts/*/results/` path, so it is **not** available as a committed Git object. Its authoritative source is therefore the original local artifact itself, which is exactly what Phase 2B-2A verified read-only and froze at the hash above. Materialization used that already-reviewed authoritative source, and re-verified it rather than trusting the earlier record.

## Verification sequence

| Step | Bytes | SHA-256 |
|---|---:|---|
| Source, before copying | 34,124 | `590672f029a519b665b856106d0b35d701b464aa3f12b5a0b1978f18b18ac8f9` |
| Expected (frozen) | 34,124 | `590672f029a519b665b856106d0b35d701b464aa3f12b5a0b1978f18b18ac8f9` |
| Destination in the isolated checkout | 34,124 | `590672f029a519b665b856106d0b35d701b464aa3f12b5a0b1978f18b18ac8f9` |
| Source, after copying | 34,124 | `590672f029a519b665b856106d0b35d701b464aa3f12b5a0b1978f18b18ac8f9` |

The source was verified **before** the copy, the destination **after** it, and the source **again afterwards** to confirm the copy was read-only with respect to the original. All four measurements agree.

The copy was an ordinary streaming binary copy to the wrapper's fixed relative path inside the isolated checkout. No transformation, normalization, or re-encoding was applied. **The file was never parsed** — not to validate its JSON, not to read a field, not for anything. It was handled as opaque bytes, which is all the gate requires and all this phase is entitled to do.

## Why the regenerated artifact stays ineligible

The manifest declares `regenerated_artifact_eligible: false`, and the gate refuses **any** handoff digest other than the frozen historical one — an exclusion by rule rather than by denylist, so it also refuses an artifact nobody anticipated. The Phase 2B-1 regenerated M52 artifact is scratch-only diagnostic evidence outside this repository; it was neither used nor read here.

Selecting the original historical artifact is a historical-chain control. It preserves the exact artifact M53 historically consumed and keeps the copied `/baseline_parity/m52_generated_at` and `/baseline_parity/m52_git_head_sha` values stable, which is what makes the frozen M53 output criterion well defined with `/generated_at` as its sole exclusion.

## Classifications preserved

M52's standing classifications are unchanged and are **not** repaired, softened, or reclassified by anything in this phase:

- `EXACT_BYTE_REPRODUCTION_FAIL`
- `RETROSPECTIVE_SEMANTIC_PAYLOAD_MATCH`

Choosing the historical artifact makes the M53 criterion well defined; it does not undo M52's exact-byte reproduction failure. The M53 chain still rests on an upstream artifact this project could not reproduce byte for byte, and any later M53 result must be read with that in view.
