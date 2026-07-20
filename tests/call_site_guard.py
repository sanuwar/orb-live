"""
AST analysis of broker tool-invocation call sites.

Analyzes real call sites rather than searching for forbidden strings, so
a tool name appearing only as test data or as a rejection fixture cannot
create a false positive, and a write call cannot hide behind a variable.

Runtime code only. Tests are excluded by design: they legitimately name
write tools in order to assert those tools are rejected.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


REPOSITORY_ROOT = Path(__file__).parent.parent

RUNTIME_DIRECTORIES = ("src", "scripts")

EXCLUDED_DIRECTORY_NAMES = frozenset(
    {
        "tests",
        ".venv",
        ".git",
        ".pytest_cache",
        "__pycache__",
        "scratchpad",
        "data",
        "logs",
        "build",
        "dist",
    }
)

# Attribute names that invoke a broker tool.
CALL_TOOL_ATTRIBUTE = "call_tool"
GATEWAY_READ_ATTRIBUTE = "read"

# The single documented safe wrapper: a call whose tool name is a
# variable assigned from assert_tool_allowed(...) in the same function
# has already been validated against the allowlist by construction.
SAFE_WRAPPER_VALIDATOR = "assert_tool_allowed"

LITERAL = "literal"
SAFE_WRAPPER = "safe_wrapper"
DYNAMIC = "dynamic"


@dataclass(frozen=True)
class CallSite:
    path: str
    line: int
    receiver: str
    attribute: str
    kind: str
    tool: str | None

    def describe(self) -> str:
        return (
            f"{self.path}:{self.line} "
            f"{self.receiver}.{self.attribute} "
            f"({self.kind}: {self.tool or 'unresolved'})"
        )


def _receiver_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id

    if isinstance(node, ast.Attribute):
        return f"{_receiver_name(node.value)}.{node.attr}"

    if isinstance(node, ast.Call):
        return _receiver_name(node.func)

    return "<expr>"


def _is_broker_invocation(call: ast.Call) -> bool:
    func = call.func

    if not isinstance(func, ast.Attribute):
        return False

    if func.attr == CALL_TOOL_ATTRIBUTE:
        return True

    if func.attr == GATEWAY_READ_ATTRIBUTE:
        # `.read` is a common name, so it counts as a broker call only
        # when the receiver is a gateway, or when the first argument is
        # a string literal. A gateway bound to an unrevealing local
        # name is still caught; file reads take an int or no argument,
        # so they are not.
        if "gateway" in _receiver_name(func.value).lower():
            return True

        first = call.args[0] if call.args else None

        return isinstance(first, ast.Constant) and isinstance(
            first.value, str
        )

    return False


def _module_constants(tree: ast.Module) -> dict[str, str]:
    constants: dict[str, str] = {}

    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue

        if not isinstance(node.value, ast.Constant):
            continue

        if not isinstance(node.value.value, str):
            continue

        for target in node.targets:
            if isinstance(target, ast.Name):
                constants[target.id] = node.value.value

    return constants


def _validated_names(tree: ast.Module) -> set[str]:
    """Names assigned from the documented allowlist validator."""

    names: set[str] = set()

    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue

        value = node.value

        if not isinstance(value, ast.Call):
            continue

        func = value.func
        called = getattr(func, "id", None) or getattr(
            func, "attr", None
        )

        if called != SAFE_WRAPPER_VALIDATOR:
            continue

        for target in node.targets:
            if isinstance(target, ast.Name):
                names.add(target.id)

    return names


def analyze_source(
    source: str,
    *,
    path: str,
) -> list[CallSite]:
    tree = ast.parse(source)
    constants = _module_constants(tree)
    validated = _validated_names(tree)

    sites: list[CallSite] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue

        if not _is_broker_invocation(node):
            continue

        receiver = _receiver_name(node.func.value)
        attribute = node.func.attr
        argument = node.args[0] if node.args else None

        if isinstance(argument, ast.Constant) and isinstance(
            argument.value, str
        ):
            kind, tool = LITERAL, argument.value
        elif isinstance(argument, ast.Name) and (
            argument.id in validated
        ):
            kind, tool = SAFE_WRAPPER, argument.id
        elif isinstance(argument, ast.Name) and (
            argument.id in constants
        ):
            kind, tool = LITERAL, constants[argument.id]
        else:
            kind, tool = DYNAMIC, None

        sites.append(
            CallSite(
                path=path,
                line=node.lineno,
                receiver=receiver,
                attribute=attribute,
                kind=kind,
                tool=tool,
            )
        )

    return sites


def runtime_files(
    root: Path = REPOSITORY_ROOT,
) -> list[Path]:
    """Tracked runtime Python files under src/ and scripts/."""

    found: list[Path] = []

    for directory in RUNTIME_DIRECTORIES:
        base = root / directory

        if not base.exists():
            continue

        for path in sorted(base.rglob("*.py")):
            if any(
                part in EXCLUDED_DIRECTORY_NAMES
                for part in path.parts
            ):
                continue

            found.append(path)

    return found


def analyze_runtime_tree(
    root: Path = REPOSITORY_ROOT,
) -> list[CallSite]:
    sites: list[CallSite] = []

    for path in runtime_files(root):
        sites.extend(
            analyze_source(
                # utf-8-sig so a byte-order mark does not break
                # parsing; some tracked files carry one.
                path.read_text(encoding="utf-8-sig"),
                path=str(path.relative_to(root)).replace(
                    "\\", "/"
                ),
            )
        )

    return sites


def violations(
    sites: Iterable[CallSite],
    allowlist: frozenset[str],
) -> list[CallSite]:
    """
    Call sites that are not provably read-only.

    A site fails when its tool name is dynamic or unresolvable, or when
    a resolved literal is absent from the immutable allowlist.
    """

    offending: list[CallSite] = []

    for site in sites:
        if site.kind == SAFE_WRAPPER:
            continue

        if site.kind == DYNAMIC:
            offending.append(site)
            continue

        if site.tool not in allowlist:
            offending.append(site)

    return offending
