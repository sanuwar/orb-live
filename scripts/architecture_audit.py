"""Deterministic, read-only architecture inventory for ORB-LIVE.

The audit reports static evidence, not semantic proof.  In particular, a public
surface with no repository caller is never labelled dead automatically because
it may be an operator entrypoint or a persisted compatibility contract.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import subprocess
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, Sequence


CLASSIFICATIONS = (
    "KEEP",
    "SIMPLIFY",
    "CONSOLIDATE",
    "DELETE_CANDIDATE",
    "NEEDS_RUNTIME_EVIDENCE",
    "NO_ACTION",
)
CONFIDENCE_LEVELS = ("HIGH", "MEDIUM", "LOW")
_TABLE_PATTERN = re.compile(
    r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?([A-Za-z_][A-Za-z0-9_]*)",
    re.IGNORECASE,
)
_HELPER_PURPOSES = {
    "identifier_validation": {"_identifier", "_validated_identifier", "_validate_identifier"},
    "path_validation": {"_database_path", "_path", "is_repository_data_path"},
    "timestamp_validation": {"_moment", "_parse_moment", "_parse_timestamp"},
    "permission_validation": {"_false"},
    "serialization": {"to_dict", "from_dict", "sanitized_facts"},
}


@dataclass(frozen=True)
class PublicSurface:
    module: str
    name: str
    kind: str
    line: int
    repository_references: int


@dataclass(frozen=True)
class EnumSurface:
    module: str
    name: str
    members: tuple[str, ...]
    unused_members: tuple[str, ...]


@dataclass(frozen=True)
class ModuleInventory:
    path: str
    loc: int
    public_classes: int
    public_functions: int
    public_enums: int
    public_protocols: int
    sqlite_tables: tuple[str, ...]
    production_import_callers: tuple[str, ...]


@dataclass(frozen=True)
class Finding:
    rank: int
    classification: str
    confidence: str
    title: str
    evidence: str
    interpretation: str
    estimated_removable_production_loc: int = 0
    estimated_removable_test_loc: int = 0

    def __post_init__(self) -> None:
        if self.classification not in CLASSIFICATIONS:
            raise ValueError("unknown finding classification")
        if self.confidence not in CONFIDENCE_LEVELS:
            raise ValueError("unknown finding confidence")


def _tracked_python_files(root: Path) -> tuple[Path, ...]:
    completed = subprocess.run(
        ["git", "ls-files", "-z", "--", "*.py"],
        cwd=root,
        check=True,
        capture_output=True,
    )
    paths = [root / value.decode("utf-8") for value in completed.stdout.split(b"\0") if value]
    return tuple(sorted(paths, key=lambda value: value.relative_to(root).as_posix()))


def _logical_loc(text: str) -> int:
    return sum(bool(line.strip()) and not line.lstrip().startswith("#") for line in text.splitlines())


def _module_name(relative: str) -> str:
    return relative.removesuffix(".py").replace("/", ".")


def _public_nodes(tree: ast.AST) -> Iterable[ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef]:
    for node in getattr(tree, "body", ()):
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and not node.name.startswith("_"):
            yield node


def _class_kind(node: ast.ClassDef) -> str:
    bases = {getattr(base, "id", None) or getattr(base, "attr", None) for base in node.bases}
    if "Enum" in bases or "IntEnum" in bases:
        return "enum"
    if "Protocol" in bases:
        return "protocol"
    return "class"


def _imports(tree: ast.AST) -> set[str]:
    values: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            values.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            values.add(node.module)
    return values


def _enum_members(node: ast.ClassDef) -> tuple[str, ...]:
    return tuple(
        child.targets[0].id
        for child in node.body
        if isinstance(child, ast.Assign)
        and len(child.targets) == 1
        and isinstance(child.targets[0], ast.Name)
        and child.targets[0].id.isupper()
    )


def _reference_indexes(
    trees: dict[str, ast.AST],
) -> tuple[Counter[tuple[str, str]], Counter[tuple[str, str, str]]]:
    surfaces: Counter[tuple[str, str]] = Counter()
    enum_members: Counter[tuple[str, str, str]] = Counter()
    enum_names = {
        _module_name(relative): {
            node.name for node in _public_nodes(tree)
            if isinstance(node, ast.ClassDef) and _class_kind(node) == "enum"
        }
        for relative, tree in trees.items()
    }
    for relative, tree in trees.items():
        own_module = _module_name(relative)
        imported: dict[str, tuple[str, str]] = {}
        module_aliases: dict[str, str] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                for alias in node.names:
                    imported[alias.asname or alias.name] = (node.module, alias.name)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    module_aliases[alias.asname or alias.name.split(".")[0]] = alias.name
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                target = imported.get(node.id)
                surfaces[target or (own_module, node.id)] += 1
            elif isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
                direct = imported.get(node.value.id)
                if direct and direct[1] in enum_names.get(direct[0], set()):
                    enum_members[(direct[0], direct[1], node.attr)] += 1
                elif node.value.id in enum_names.get(own_module, set()):
                    enum_members[(own_module, node.value.id, node.attr)] += 1
                module = module_aliases.get(node.value.id)
                if module:
                    surfaces[(module, node.attr)] += 1
    return surfaces, enum_members


def _helper_inventory(trees: dict[str, ast.AST]) -> dict[str, tuple[str, ...]]:
    purposes: dict[str, set[str]] = defaultdict(set)
    for relative, tree in trees.items():
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for purpose, names in _HELPER_PURPOSES.items():
                if node.name in names:
                    purposes[purpose].add(relative)
    return {key: tuple(sorted(value)) for key, value in sorted(purposes.items())}


def _exact_helper_groups(trees: dict[str, ast.AST]) -> tuple[dict[str, object], ...]:
    groups: dict[tuple[str, str], list[tuple[str, int]]] = defaultdict(list)
    selected = set().union(*_HELPER_PURPOSES.values())
    for relative, tree in trees.items():
        if not relative.startswith(("src/", "scripts/")):
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in selected:
                normalized = "|".join((
                    ast.dump(node.args, include_attributes=False),
                    ast.dump(ast.Module(body=node.body, type_ignores=[]), include_attributes=False),
                    ast.dump(ast.Module(body=node.decorator_list, type_ignores=[]), include_attributes=False),
                    ast.dump(node.returns, include_attributes=False) if node.returns is not None else "None",
                ))
                groups[(node.name, normalized)].append((relative, (node.end_lineno or node.lineno) - node.lineno + 1))
    result = []
    for (name, _), placements in sorted(groups.items(), key=lambda item: (item[0][0], item[1])):
        modules = sorted({relative for relative, _ in placements})
        if len(modules) < 2:
            continue
        removable = sum(lines for _, lines in placements) - max(lines for _, lines in placements)
        result.append({"helper": name, "modules": modules, "estimated_removable_loc": removable})
    return tuple(result)


def _table_purposes(tables: Iterable[str]) -> dict[str, tuple[str, ...]]:
    groups: dict[str, list[str]] = defaultdict(list)
    for table in tables:
        purpose = next(
            (name for name in ("meta", "transition", "spent", "origin", "evidence", "checkpoint") if name in table),
            "domain_record",
        )
        groups[purpose].append(table)
    return {key: tuple(sorted(value)) for key, value in sorted(groups.items())}


def _duplicate_test_bodies(trees: dict[str, ast.AST]) -> tuple[dict[str, object], ...]:
    groups: dict[str, list[str]] = defaultdict(list)
    for relative, tree in trees.items():
        if not (relative.startswith("tests/") or relative.startswith("test_")):
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_"):
                body = ast.dump(ast.Module(body=node.body, type_ignores=[]), include_attributes=False)
                groups[body].append(f"{relative}:{node.name}")
    return tuple(
        {"tests": sorted(values), "count": len(values)}
        for _, values in sorted(groups.items(), key=lambda item: item[1])
        if len(values) > 1
    )


def _findings(
    modules: Sequence[ModuleInventory],
    public: Sequence[PublicSurface],
    enums: Sequence[EnumSurface],
    helpers: dict[str, tuple[str, ...]],
    exact_helpers: Sequence[dict[str, object]],
    table_purposes: dict[str, tuple[str, ...]],
    test_heavy: Sequence[tuple[str, int, int]],
) -> tuple[Finding, ...]:
    no_callers = [item for item in public if item.repository_references == 0]
    unused_members = sum(len(item.unused_members) for item in enums)
    repeated_helpers = sum(max(0, len(paths) - 1) for paths in helpers.values())
    transition_tables = table_purposes.get("transition", ())
    spent_tables = table_purposes.get("spent", ())
    fingerprint_modules = tuple(sorted(item.path for item in modules if item.path.startswith(("src/", "scripts/")) and ("fingerprint" in item.path or "attestation" in item.path or "readiness" in item.path)))
    largest_prod = sorted((item for item in modules if item.path.startswith("src/")), key=lambda item: (-item.loc, item.path))[:5]
    largest_tests = sorted((item for item in modules if item.path.startswith("tests/")), key=lambda item: (-item.loc, item.path))[:5]
    exact_removable = sum(int(item["estimated_removable_loc"]) for item in exact_helpers)
    candidates = (
        Finding(1, "NEEDS_RUNTIME_EVIDENCE", "MEDIUM", "Repeated constrained-value validators",
                f"{repeated_helpers} repeated helper placements across " + ", ".join(f"{key}={len(value)}" for key, value in helpers.items()),
                f"Exact normalized bodies shared across modules: {len(exact_helpers)}. Similar names alone do not establish contract equivalence.",
                exact_removable, 0),
        Finding(2, "NEEDS_RUNTIME_EVIDENCE", "HIGH", "Transition journals are intentional durable evidence",
                f"{len(transition_tables)} transition tables: {', '.join(transition_tables)}",
                "Preserve by default. Static table purpose cannot prove equivalent aggregate, atomicity, or restart contracts."),
        Finding(3, "NEEDS_RUNTIME_EVIDENCE", "HIGH", "Spent-identity ledgers protect different replay domains",
                f"{len(spent_tables)} spent-identity tables: {', '.join(spent_tables) or 'none'}",
                "Preserve by default. Do not merge without equivalent atomicity and persisted compatibility evidence."),
        Finding(4, "NEEDS_RUNTIME_EVIDENCE", "MEDIUM", "Public surfaces without static repository callers",
                f"{len(no_callers)} public definitions have zero name/attribute references outside their definitions.",
                "This is uncertainty, not proven dead code; dynamic dispatch, operator entrypoints, and external consumers remain invisible."),
        Finding(5, "NEEDS_RUNTIME_EVIDENCE", "LOW", "Enum members without qualified references",
                f"{unused_members} enum members have no qualified construction/reference site.",
                "Persisted values, iteration, and indirect deserialization can make these reachable; require runtime evidence before deletion."),
        Finding(6, "NEEDS_RUNTIME_EVIDENCE", "HIGH", "Fingerprint and provenance layers bind distinct trust boundaries",
                f"Trust-related modules include {', '.join(fingerprint_modules[:8])}.",
                "Preserve by default. Filename and vocabulary overlap cannot prove equivalent identities or trust semantics."),
        Finding(7, "NO_ACTION", "MEDIUM", "Layered status vocabularies overlap intentionally",
                "Runner, driver, scheduler, liveness, operational status, alert, acknowledgment, and readiness enums reuse failure/review/suspension concepts.",
                "Each vocabulary is a typed boundary. Prefer adapters over a single cross-layer mega-enum."),
        Finding(8, "NEEDS_RUNTIME_EVIDENCE", "LOW", "Serialization allowlists repeat exact-key mechanics",
                f"serialization helpers appear in {len(helpers.get('serialization', ()))} modules.",
                "A shared primitive may reduce boilerplate, but field privacy and error contracts require equivalence evidence."),
        Finding(9, "NO_ACTION", "HIGH", "Large modules concentrate durable invariants",
                "largest production modules: " + ", ".join(f"{item.path} ({item.loc})" for item in largest_prod),
                "Size alone is not deletion evidence; transaction and restart invariants require cohesive review."),
        Finding(10, "NO_ACTION", "HIGH", "Test-heavy modules are visible, not automatically excessive",
                "largest tests: " + ", ".join(f"{item.path} ({item.loc})" for item in largest_tests) + f"; {len(test_heavy)} modules have more test LOC than production LOC.",
                "Higher test LOC is expected for state, boundary, restart, and adversarial matrices; remove only behaviorally duplicate cases."),
    )
    return candidates


def audit_repository(root: Path) -> dict[str, object]:
    root = root.resolve()
    files = _tracked_python_files(root)
    texts = {path.relative_to(root).as_posix(): path.read_text(encoding="utf-8-sig") for path in files}
    trees = {relative: ast.parse(text, filename=relative) for relative, text in texts.items()}
    surface_references, enum_member_references = _reference_indexes(trees)
    imports = {relative: _imports(tree) for relative, tree in trees.items()}
    production = tuple(relative for relative in trees if relative.startswith("src/") or relative.startswith("scripts/"))

    public: list[PublicSurface] = []
    enums: list[EnumSurface] = []
    modules: list[ModuleInventory] = []
    all_tables: list[str] = []
    for relative in sorted(trees):
        tree = trees[relative]
        surface = tuple(_public_nodes(tree))
        kinds = Counter(_class_kind(node) if isinstance(node, ast.ClassDef) else "function" for node in surface)
        module = _module_name(relative)
        tables = tuple(sorted(set(_TABLE_PATTERN.findall(texts[relative]))))
        if relative in production:
            all_tables.extend(tables)
        callers = tuple(sorted(
            caller for caller in production
            if caller != relative and any(value == module or value.startswith(module + ".") for value in imports[caller])
        ))
        modules.append(ModuleInventory(
            relative, _logical_loc(texts[relative]), kinds["class"], kinds["function"],
            kinds["enum"], kinds["protocol"], tables, callers,
        ))
        if relative not in production:
            continue
        for node in surface:
            kind = _class_kind(node) if isinstance(node, ast.ClassDef) else "function"
            public.append(PublicSurface(
                relative, node.name, kind, node.lineno,
                surface_references[(module, node.name)],
            ))
            if isinstance(node, ast.ClassDef) and kind == "enum":
                members = _enum_members(node)
                unused = tuple(
                    member for member in members
                    if enum_member_references[(module, node.name, member)] == 0
                )
                enums.append(EnumSurface(relative, node.name, members, unused))

    module_map = {item.path: item for item in modules}
    test_heavy: list[tuple[str, int, int]] = []
    for relative, item in module_map.items():
        if not relative.startswith("src/"):
            continue
        test = module_map.get("tests/test_" + Path(relative).name)
        if test and test.loc > item.loc:
            test_heavy.append((relative, item.loc, test.loc))

    helpers = _helper_inventory(trees)
    exact_helpers = _exact_helper_groups(trees)
    table_purposes = _table_purposes(all_tables)
    duplicate_test_bodies = _duplicate_test_bodies(trees)
    reason_codes_without_construction = [
        {"module": item.module, "enum": item.name, "members": item.unused_members}
        for item in enums if item.name.endswith("Reason") and item.unused_members
    ]
    pattern_inventory = {
        "fingerprint_or_provenance_modules": sorted(
            relative for relative in production
            if "fingerprint" in texts[relative] or "provenance" in texts[relative] or "sha256" in texts[relative]
        ),
        "serialization_allowlist_modules": sorted(
            relative for relative in production
            if re.search(r"\b[A-Z][A-Z0-9_]*KEYS\s*=\s*frozenset", texts[relative])
        ),
        "with_statement_modules": sorted(
            relative for relative in production if any(isinstance(node, ast.With) for node in ast.walk(trees[relative]))
        ),
    }
    findings = _findings(modules, public, enums, helpers, exact_helpers, table_purposes, sorted(test_heavy))
    estimated_prod = sum(item.estimated_removable_production_loc for item in findings)
    estimated_test = sum(item.estimated_removable_test_loc for item in findings)
    report = {
        "schema_version": 1,
        "scope": "tracked-python-only",
        "production_loc": sum(item.loc for item in modules if item.path.startswith("src/") or item.path.startswith("scripts/")),
        "test_loc": sum(item.loc for item in modules if item.path.startswith("tests/") or item.path.startswith("test_")),
        "modules": [asdict(item) for item in modules],
        "public_surfaces": [asdict(item) for item in sorted(public, key=lambda value: (value.module, value.line, value.name))],
        "enums": [asdict(item) for item in sorted(enums, key=lambda value: (value.module, value.name))],
        "reason_codes_without_construction": reason_codes_without_construction,
        "helper_purposes": helpers,
        "exact_helper_groups": exact_helpers,
        "pattern_inventory": pattern_inventory,
        "table_purposes": table_purposes,
        "duplicate_test_bodies": duplicate_test_bodies,
        "test_heavy_modules": [dict(module=module, production_loc=prod, test_loc=test) for module, prod, test in sorted(test_heavy)],
        "findings": [asdict(item) for item in findings],
        "estimated_removable_production_loc": estimated_prod,
        "estimated_removable_test_loc": estimated_test,
        "exact_body_consolidation_candidates": exact_helpers,
        "consolidation_decision": (
            "Review exact helper-body groups for contract equivalence before consolidation."
            if exact_helpers else "No shared-helper change is justified by exact body-equivalence evidence."
        ),
        "limitations": [
            "Static references do not prove runtime reachability or dead code.",
            "Reference indexing can miss nested or relative module access and can count shadowed local names.",
            "Persisted enum and schema compatibility require runtime evidence.",
            "Similarity of safety ledgers or fingerprints does not prove equivalent atomicity or trust semantics.",
            "SQL inventory uses a conservative raw-source pattern and may include non-executable examples.",
            "Exact duplicate test bodies can have distinct signatures, decorators, parametrization, or fixture semantics.",
            "Exact helper candidates still require global-binding and caller-contract equivalence review.",
        ],
    }
    return json.loads(json.dumps(report, sort_keys=True))


def render_markdown(report: dict[str, object]) -> str:
    findings = report["findings"]
    lines = [
        "# Lean Architecture Audit",
        "",
        "Deterministic scope: tracked Python files only. Static absence is uncertainty, not proof of dead code.",
        "",
        f"- Production LOC: {report['production_loc']}",
        f"- Test LOC: {report['test_loc']}",
        f"- Estimated removable production LOC: {report['estimated_removable_production_loc']}",
        f"- Estimated removable test LOC: {report['estimated_removable_test_loc']}",
        f"- Exact helper-body candidates: {len(report['exact_body_consolidation_candidates'])}",
        f"- Decision: {report['consolidation_decision']}",
        "- Detailed reason-code, pattern, and duplicate-body inventories: JSON output only",
        "",
        "## Prioritized findings",
        "",
    ]
    for candidate in report["exact_body_consolidation_candidates"]:  # type: ignore[assignment]
        lines.insert(11, f"- Candidate detail: {candidate['helper']} in {', '.join(candidate['modules'])}; gross duplicate LOC {candidate['estimated_removable_loc']}")
    for item in findings:  # type: ignore[assignment]
        lines.extend([
            f"{item['rank']}. **{item['classification']} / {item['confidence']} -- {item['title']}**",
            f"   Evidence: {item['evidence']}",
            f"   Interpretation: {item['interpretation']}",
            f"   Estimated removable LOC: production {item['estimated_removable_production_loc']}, tests {item['estimated_removable_test_loc']}",
            "",
        ])
    lines.extend(["## Limitations", ""])
    lines.extend(f"- {value}" for value in report["limitations"])  # type: ignore[arg-type]
    return "\n".join(lines) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audit tracked ORB-LIVE architecture without modifying it")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--format", choices=("json", "markdown"), default="markdown")
    args = parser.parse_args(argv)
    report = audit_repository(args.root)
    print(json.dumps(report, sort_keys=True, separators=(",", ":")) if args.format == "json" else render_markdown(report), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
