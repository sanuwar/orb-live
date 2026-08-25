"""Deterministically generate docs/publication/p2/HYPOTHESIS_LEDGER_M43_M55.md
from the canonical docs/publication/p2/HYPOTHESIS_LEDGER_M43_M55.json ledger.

Publication-utility script only: reads the committed JSON ledger and commit
metadata already present in the repository, does no network access, reads no
market-data cache, and modifies no trading, strategy, or M55 checkpoint
artifact. Safe to rerun; output is byte-identical across runs for the same
input (idempotent, no timestamps or non-deterministic ordering).
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LEDGER_JSON_PATH = ROOT / "docs" / "publication" / "p2" / "HYPOTHESIS_LEDGER_M43_M55.json"
LEDGER_MD_PATH = ROOT / "docs" / "publication" / "p2" / "HYPOTHESIS_LEDGER_M43_M55.md"


def load_ledger() -> dict:
    return json.loads(LEDGER_JSON_PATH.read_text(encoding="utf-8"))


def _is_novel_decision_eligible(entry: dict) -> bool:
    return (
        entry["entry_policy"] == "primary"
        and entry["decision_eligible"] is True
        and entry["control_status"] is False
        and entry["diagnostic_sleeve"] is False
    )


def _is_displayed_arm_label(entry: dict) -> bool:
    return entry["entry_policy"] == "primary"


def _short(value: str, limit: int = 60) -> str:
    return value if len(value) <= limit else value[: limit - 1] + "…"


def _escape_pipes(value: str) -> str:
    return value.replace("|", "\\|")


def render_markdown(ledger: dict) -> str:
    entries = ledger["entries"]
    rules = ledger["counting_rules"]

    novel = [e for e in entries if _is_novel_decision_eligible(e)]
    displayed = [e for e in entries if _is_displayed_arm_label(e)]
    all_evals = list(entries)

    assert len(novel) == rules["novel_decision_eligible_variants"]["count"]
    assert len(displayed) == rules["displayed_arm_labels"]["count"]
    assert len(all_evals) == rules["arm_by_entry_policy_diagnostic_evaluations"]["count"]

    lines: list[str] = []
    lines.append("# Hypothesis Ledger — M43 through M55")
    lines.append("")
    lines.append(
        "**Generated deterministically from `docs/publication/p2/HYPOTHESIS_LEDGER_M43_M55.json` "
        "by `scripts/publication/generate_p2_hypothesis_ledger.py`. Do not hand-edit this file; "
        "edit the JSON ledger and regenerate.**"
    )
    lines.append("")
    lines.append(f"**Evidence cutoff:** main at `{ledger['evidence_cutoff_main_sha']}`.")
    lines.append("")

    lines.append("## Counting-rule reconciliation")
    lines.append("")
    lines.append(
        "All three totals below are produced by filtering the same 21-entry ledger; "
        "no hidden or invented experiments were created to force any total."
    )
    lines.append("")
    lines.append("| Count | Definition | Value |")
    lines.append("|---|---|---:|")
    lines.append(
        "| Novel decision-eligible variants | "
        + rules["novel_decision_eligible_variants"]["definition"]
        + " | **"
        + str(rules["novel_decision_eligible_variants"]["count"])
        + "** |"
    )
    lines.append(
        "| Displayed arm labels | "
        + rules["displayed_arm_labels"]["definition"]
        + " | **"
        + str(rules["displayed_arm_labels"]["count"])
        + "** |"
    )
    lines.append(
        "| Arm-by-entry-policy/diagnostic evaluations | "
        + rules["arm_by_entry_policy_diagnostic_evaluations"]["definition"]
        + " | **"
        + str(rules["arm_by_entry_policy_diagnostic_evaluations"]["count"])
        + "** |"
    )
    lines.append("")
    lines.append(rules["note"])
    lines.append("")
    lines.append(
        "**14 is the decision-eligible tested-variant count, not necessarily the final number of "
        "valid hypothesis tests.** A variant being decision-eligible (predeclared, not a control or "
        "diagnostic sleeve) is a different question from whether it is presently evaluable by a formal "
        "inferential procedure. See the Inferential evaluability breakdown below and "
        "`docs/publication/p2/STATISTICAL_ANALYSIS_PROTOCOL_CANDIDATE.md` for that distinction; no "
        "p-value or resampled interval is computed anywhere in this repository for any variant."
    )
    lines.append("")

    lines.append("## Authoritative table (all 21 arm-by-entry-policy evaluations)")
    lines.append("")
    lines.append(
        "| Milestone | Arm | Entry policy | Control | Diagnostic | Decision-eligible | Inferential evaluability | Classification | Artifact |"
    )
    lines.append("|---|---|---|---|---|---|---|---|---|")
    for e in all_evals:
        lines.append(
            "| {m} | {a} | {ep} | {c} | {d} | {de} | {ie} | {cl} | `{art}` |".format(
                m=e["milestone"],
                a=e["arm_id"],
                ep=e["entry_policy"],
                c="yes" if e["control_status"] else "no",
                d="yes" if e["diagnostic_sleeve"] else "no",
                de="yes" if e["decision_eligible"] else "no",
                ie=e["inferential_evaluability"],
                cl=_escape_pipes(_short(e["final_classification"])),
                art=e["authoritative_artifact_path"],
            )
        )
    lines.append("")

    lines.append("## Inferential evaluability breakdown (14-count basis: novel decision-eligible variants only)")
    lines.append("")
    lines.append(
        "Classifies each of the 14 novel decision-eligible variants by whether it is presently "
        "evaluable by a formal inferential procedure, independent of its decision-eligible status. "
        "No observation was invented and no p-value was assigned to any variant to produce this table."
    )
    lines.append("")
    ie_totals: dict[str, int] = {}
    for e in novel:
        ie_totals[e["inferential_evaluability"]] = ie_totals.get(e["inferential_evaluability"], 0) + 1
    lines.append("| Inferential evaluability | Count | Variants |")
    lines.append("|---|---:|---|")
    for category in sorted(ie_totals):
        members = ", ".join(f"{e['milestone']}:{e['arm_id']}" for e in novel if e["inferential_evaluability"] == category)
        lines.append(f"| {category} | {ie_totals[category]} | {members} |")
    lines.append("")
    lines.append("| Milestone | Arm | Inferential evaluability | Rationale |")
    lines.append("|---|---|---|---|")
    for e in novel:
        lines.append(
            "| {m} | {a} | {ie} | {r} |".format(
                m=e["milestone"],
                a=e["arm_id"],
                ie=e["inferential_evaluability"],
                r=_escape_pipes(e["inferential_evaluability_rationale"]),
            )
        )
    lines.append("")

    lines.append("## Strategy-family totals (14-count basis: novel decision-eligible variants only)")
    lines.append("")
    family_totals: dict[str, int] = {}
    for e in novel:
        family_totals[e["strategy_family"]] = family_totals.get(e["strategy_family"], 0) + 1
    lines.append("| Strategy family | Novel decision-eligible variants |")
    lines.append("|---|---:|")
    for family in sorted(family_totals):
        lines.append(f"| {family} | {family_totals[family]} |")
    lines.append("")

    lines.append("## Evidence-class totals")
    lines.append("")
    class_totals: dict[str, int] = {}
    for e in all_evals:
        class_totals[e["evidence_class"]] = class_totals.get(e["evidence_class"], 0) + 1
    for stage in ledger.get("non_ledger_evidence_stages", []):
        class_totals[stage["evidence_class"]] = class_totals.get(stage["evidence_class"], 0) + 1
    lines.append("| Evidence class | Count (arm-evaluations + non-ledger stages) |")
    lines.append("|---|---:|")
    for cls in sorted(class_totals):
        lines.append(f"| {cls} | {class_totals[cls]} |")
    lines.append("")

    lines.append("## Historical versus prospective distinction")
    lines.append("")
    lines.append(
        "All 21 arm-by-entry-policy evaluations above (M43-M53) are **retrospective**: each "
        "used a within-milestone frozen protocol, but none is independent confirmation after "
        "the broader research sequence selected families and questions. M50 is a **blocked "
        "retrospective validation attempt** (no economics computed). M51 is a **retrospective "
        "literature/feasibility screen** (no new market simulation). M54 is a **retrospective "
        "synthesis**. M55 is the only **prospective** evidence stage in the record, and it "
        "contains a frozen protocol and an initialization checkpoint only — "
        "`INITIALIZED_WAITING_FOR_FIRST_SIGNAL`, zero transactions, zero positions in both base "
        "and stress ledgers. M55 must never be pooled into retrospective multiplicity "
        "calculations for the same reason: it has not yet produced an observation."
    )
    lines.append("")
    for stage in ledger.get("non_ledger_evidence_stages", []):
        lines.append(f"- **{stage['milestone']}** (`{stage['evidence_class']}`): {stage['note']}")
    lines.append("")

    lines.append("## Superseded versus authoritative figures")
    lines.append("")
    superseded_rows = [(e["milestone"], e["arm_id"], s) for e in all_evals for s in e["superseded_artifacts_or_figures"]]
    if superseded_rows:
        lines.append("| Milestone | Arm | Superseded artifact or figure |")
        lines.append("|---|---|---|")
        for milestone, arm, note in superseded_rows:
            lines.append(f"| {milestone} | {arm} | {_escape_pipes(note)} |")
    else:
        lines.append("None recorded.")
    lines.append("")

    lines.append(
        "## Non-ledger evidence stages (not counted as tested hypotheses: M50, M51, M54, M55)"
    )
    lines.append("")
    lines.append("| Milestone | Evidence class | Artifact | Commit SHA | Blob hash |")
    lines.append("|---|---|---|---|---|")
    for stage in ledger.get("non_ledger_evidence_stages", []):
        lines.append(
            "| {m} | {ec} | `{art}` | `{sha}` | `{blob}` |".format(
                m=stage["milestone"],
                ec=stage["evidence_class"],
                art=stage["authoritative_artifact_path"],
                sha=stage["commit_sha"],
                blob=stage["git_blob_hash"],
            )
        )
    lines.append("")

    text = "\n".join(lines)
    return text.rstrip("\n") + "\n"


def main() -> None:
    ledger = load_ledger()
    markdown = render_markdown(ledger)
    LEDGER_MD_PATH.write_text(markdown, encoding="utf-8", newline="\n")
    print(f"Wrote {LEDGER_MD_PATH}")


if __name__ == "__main__":
    main()
