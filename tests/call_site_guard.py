"""
AST analysis of broker tool-invocation call sites (M13 Issue 6: hardened).

Analyzes real call sites rather than searching for forbidden strings, so a
tool name appearing only as test data or as a rejection fixture cannot
create a false positive, and a write call cannot hide behind a variable.

Runtime code only. Tests are excluded by design: they legitimately name
write tools in order to assert those tools are rejected.

## What Issue 6 hardens

The pre-Issue-6 guard only recognized a broker dispatch expressed as a
literal attribute call: `X.call_tool(...)` or `X.read(...)`. Session 2's
independent review identified the exact gap: `_is_broker_invocation`
required `call.func` to be an `ast.Attribute`, so any call reached through
`getattr(...)`, a bound-method alias, a wrapper, a `functools.partial`, or
a dict/registry dispatch was invisible — not merely misclassified, absent
from `sites` entirely.

This module closes that gap with a conservative, bounded analysis. It does
not attempt arbitrary interprocedural dataflow or general Python theorem
proving. Every pattern it resolves is a single, explicit, reviewable shape
(direct call, getattr-literal call, name-to-attribute alias resolved to a
later call in the same file, a `functools.partial` wrapping one of those,
a dict/registry with literal keys whose values are one of those). Anything
outside those bounded shapes — a dynamically constructed attribute name, a
broker-dispatch reference passed as a bare argument or returned without
being resolved through one of the bounded patterns above, an unresolved
registry key drawn from a registry containing any write-capable entry — is
`UNRESOLVED` and is *always* a violation. Unknown broker dispatch never
means safe.

## Two dispatch styles, one engine

This repository's actual broker transport uses exactly one dispatch style:
`session.call_tool(<tool-name-string>, ...)`, where the operation identity
is the *first call argument*, not the attribute name. The guard's tests
also model the general threat class using directly-named methods (as a
hypothetical broker client might expose, e.g. `client.place_order(...)`),
where the operation identity *is* the attribute name. Both are handled by
the same engine: `known_read_methods` / `known_write_methods` name
directly-dispatched operations (empty by default — this repository does
not use that style in production); `DISPATCH_ATTRIBUTES` /
`GATEWAY_READ_ATTRIBUTE` name argument-dispatched attributes, resolved via
the existing literal/safe-wrapper/dynamic three-way classification on the
first call argument. Real repository scans (`analyze_runtime_tree`) use
only the argument-dispatched style, unchanged from before Issue 6; the
named-method style exists so the hardening can be exercised directly and
deterministically without depending on repository-specific naming.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping


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

# Attribute names that invoke a broker tool via argument-style dispatch:
# the operation identity is the call's first argument, not the attribute.
CALL_TOOL_ATTRIBUTE = "call_tool"
GATEWAY_READ_ATTRIBUTE = "read"
DISPATCH_ATTRIBUTES: frozenset[str] = frozenset({CALL_TOOL_ATTRIBUTE})

# The single documented safe wrapper: a call whose tool name is a
# variable assigned from assert_tool_allowed(...) in the same function
# has already been validated against the allowlist by construction.
SAFE_WRAPPER_VALIDATOR = "assert_tool_allowed"

# Existing classifications (unchanged names/values -- backward compatible
# with every pre-Issue-6 consumer).
LITERAL = "literal"
SAFE_WRAPPER = "safe_wrapper"
DYNAMIC = "dynamic"

# New in Issue 6: named-method-style dispatch (operation identity IS the
# attribute name, no argument inspection needed) and the catch-all for
# anything this bounded analysis cannot prove safe.
KNOWN_WRITE = "known_write"
PROVEN_READ_ONLY = "proven_read_only"
UNRESOLVED = "unresolved"

#: Kinds that `violations()` never fails, given a resolvable tool/allowlist
#: relationship. SAFE_WRAPPER and PROVEN_READ_ONLY are both "already proven
#: safe by construction" -- the former via the allowlist validator, the
#: latter via an explicit, reviewed known-read-methods set.
_ALWAYS_SAFE_KINDS = frozenset({SAFE_WRAPPER, PROVEN_READ_ONLY})
#: Kinds that `violations()` always fails, regardless of allowlist content.
_ALWAYS_UNSAFE_KINDS = frozenset({DYNAMIC, UNRESOLVED, KNOWN_WRITE})


@dataclass(frozen=True)
class CallSite:
    path: str
    line: int
    receiver: str
    attribute: str
    kind: str
    tool: str | None
    reason: str = ""

    def describe(self) -> str:
        return (
            f"{self.path}:{self.line} "
            f"{self.receiver}.{self.attribute} "
            f"({self.kind}: {self.tool or 'unresolved'})"
            + (f" -- {self.reason}" if self.reason else "")
        )


def _receiver_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id

    if isinstance(node, ast.Attribute):
        return f"{_receiver_name(node.value)}.{node.attr}"

    if isinstance(node, ast.Call):
        return _receiver_name(node.func)

    return "<expr>"


def _string_constant(node: ast.AST | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _is_partial_call(node: ast.AST) -> bool:
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if isinstance(func, ast.Name):
        return func.id == "partial"
    if isinstance(func, ast.Attribute):
        return func.attr == "partial"
    return False


def _getattr_target(node: ast.AST) -> tuple[ast.AST, ast.AST] | None:
    """If `node` is `getattr(X, Y[, default])`, return `(X, Y)`."""
    if not isinstance(node, ast.Call):
        return None
    if not isinstance(node.func, ast.Name) or node.func.id != "getattr":
        return None
    if len(node.args) < 2:
        return None
    return node.args[0], node.args[1]


@dataclass(frozen=True)
class _DispatchAttr:
    """One recognized broker-dispatch attribute reference.

    `argument_style` is True for `call_tool`/gateway `read` (operation
    identity is the first call argument); False for a directly-named
    method in `known_read_methods`/`known_write_methods` (operation
    identity is the attribute name itself).
    """

    receiver: ast.AST
    attribute: str
    argument_style: bool
    known_kind: str | None  # PROVEN_READ_ONLY / KNOWN_WRITE for named-method style


def _gateway_receiver_only(receiver: ast.AST) -> bool:
    """Whether `receiver` alone (no call argument available) is enough to
    treat a bare `.read` reference as gateway-relevant. `.read` is an
    extremely common name (files, streams, buffers) -- without a call's
    first argument to inspect, only an explicitly gateway-named receiver
    qualifies, matching the existing call-site heuristic's more permissive
    "gateway name OR string first argument" for the strictly narrower case
    where no argument exists to check at all."""
    return "gateway" in _receiver_name(receiver).lower()


def _match_dispatch_attribute(
    node: ast.AST,
    *,
    known_read_methods: frozenset[str],
    known_write_methods: frozenset[str],
    bare: bool = False,
) -> _DispatchAttr | None:
    """Recognize `X.<dispatch-attr>` or `getattr(X, "<dispatch-attr>")`.

    `bare` is True when there is no enclosing call whose first argument
    could otherwise qualify a `.read` reference as gateway-relevant (an
    alias assignment, a registry value, or an escaped/leaked reference).
    In that case a `.read` reference only matches when the receiver's own
    name is gateway-qualified -- otherwise ordinary file/stream `.read`
    would be flagged on every buffered read in the codebase.

    Returns None for a dynamically-named getattr (caller must separately
    flag that as UNRESOLVED) and for anything not matching a known
    dispatch or known-method attribute name.
    """
    if isinstance(node, ast.Attribute):
        attr = node.attr
        if attr in DISPATCH_ATTRIBUTES:
            return _DispatchAttr(node.value, attr, True, None)
        if attr == GATEWAY_READ_ATTRIBUTE:
            if bare and not _gateway_receiver_only(node.value):
                return None
            return _DispatchAttr(node.value, attr, True, None)
        if attr in known_write_methods:
            return _DispatchAttr(node.value, attr, False, KNOWN_WRITE)
        if attr in known_read_methods:
            return _DispatchAttr(node.value, attr, False, PROVEN_READ_ONLY)
        return None

    target = _getattr_target(node)
    if target is not None:
        receiver, attr_expr = target
        literal_attr = _string_constant(attr_expr)
        if literal_attr is None:
            return None  # dynamically-named getattr: handled by caller
        if literal_attr in DISPATCH_ATTRIBUTES:
            return _DispatchAttr(receiver, literal_attr, True, None)
        if literal_attr == GATEWAY_READ_ATTRIBUTE:
            if bare and not _gateway_receiver_only(receiver):
                return None
            return _DispatchAttr(receiver, literal_attr, True, None)
        if literal_attr in known_write_methods:
            return _DispatchAttr(receiver, literal_attr, False, KNOWN_WRITE)
        if literal_attr in known_read_methods:
            return _DispatchAttr(receiver, literal_attr, False, PROVEN_READ_ONLY)
        return None

    return None


def _is_dynamic_getattr(node: ast.AST) -> bool:
    """`getattr(X, <non-literal>)` -- the attribute name is not provable."""
    target = _getattr_target(node)
    if target is None:
        return False
    return _string_constant(target[1]) is None


def _is_gateway_call(receiver: ast.AST, first_arg: ast.AST | None) -> bool:
    if "gateway" in _receiver_name(receiver).lower():
        return True
    literal = _string_constant(first_arg)
    return literal is not None


def _resolve_argument_style(
    call: ast.Call,
    *,
    constants: Mapping[str, str],
    validated: frozenset[str],
) -> tuple[str, str | None]:
    """The existing literal/safe-wrapper/dynamic three-way resolution,
    applied to the tool-name argument of a call_tool/read-style call."""
    argument = call.args[0] if call.args else None

    literal = _string_constant(argument)
    if literal is not None:
        return LITERAL, literal
    if isinstance(argument, ast.Name) and argument.id in validated:
        return SAFE_WRAPPER, argument.id
    if isinstance(argument, ast.Name) and argument.id in constants:
        return LITERAL, constants[argument.id]
    return DYNAMIC, None


class _CallSiteBuilder:
    """One pass over one module: collects aliases, registries, and finally
    every call/leak site. Kept as a class only to share the constants/
    validated/alias/registry maps across the phases without a long
    parameter list -- every method is still a pure function of `self`."""

    def __init__(
        self,
        tree: ast.Module,
        *,
        path: str,
        known_read_methods: frozenset[str],
        known_write_methods: frozenset[str],
    ) -> None:
        self.tree = tree
        self.path = path
        self.known_read_methods = known_read_methods
        self.known_write_methods = known_write_methods
        self.constants = _module_constants(tree)
        self.validated = _validated_names(tree)
        # name -> (kind, tool, receiver, attribute)
        self.aliases: dict[str, tuple[str, str | None, str, str]] = {}
        # registry name -> {literal key -> (kind, tool, receiver, attribute)}
        self.registries: dict[str, dict[str, tuple[str, str | None, str, str]]] = {}
        # registry name -> True if every value is a proven-safe read
        self.registry_all_safe: dict[str, bool] = {}
        # id() of AST nodes already accounted for by alias/registry/direct
        # resolution, so the "leak" scan does not double-report them.
        self.consumed: set[int] = set()
        # root receiver names (e.g. "self", "session") with at least one
        # PROVEN literal broker-dispatch call somewhere in this file.
        self.broker_capable_receivers: set[str] = set()

    # -- phase 0: which receivers are demonstrated broker-capable? -------

    def _scan_broker_capable_receivers(self) -> None:
        """A dynamically-named getattr that is later called (or merely
        referenced and never proven safe) is only flagged as broker-
        relevant when its receiver is DEMONSTRATED broker-capable
        elsewhere in the same file -- i.e. that same root name is also the
        receiver of a proven literal `.call_tool(...)`, gateway `.read`,
        or known-method call. This is evidence-based, not a naming
        heuristic: `getattr(self, f"_scenario_{kind}")()` inside a
        simulation runner that never once calls `self.call_tool(...)`
        anywhere is legitimate internal dispatch, not broker dispatch, and
        this scan is exactly what tells the two apart without guessing
        from the word "self" or "session" alone.
        """
        for node in ast.walk(self.tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not isinstance(func, ast.Attribute):
                continue
            matched = _match_dispatch_attribute(
                func,
                known_read_methods=self.known_read_methods,
                known_write_methods=self.known_write_methods,
            )
            if matched is None:
                continue
            if matched.argument_style and matched.attribute == GATEWAY_READ_ATTRIBUTE:
                first_arg = node.args[0] if node.args else None
                if not _is_gateway_call(matched.receiver, first_arg):
                    continue
            root = _receiver_name(matched.receiver).split(".")[0]
            self.broker_capable_receivers.add(root)

    # -- resolving a single "dispatch-shaped" expression to (kind, tool) --

    def _resolve_expression(
        self, node: ast.AST
    ) -> tuple[str, str | None, str, str] | None:
        """Resolve a bare expression (attribute ref, getattr call, alias
        name, or an immediately-called form of any of those) to a
        (kind, tool, receiver, attribute) tuple, or None if it is not
        broker-dispatch-relevant at all."""
        # getattr(...) and partial(...) must be checked before the
        # generic "any other call result is not a callable reference"
        # bailout below -- both are references to a (possibly dispatch-
        # relevant) callable/attribute, not an already-produced value.
        if _getattr_target(node) is not None:
            matched = _match_dispatch_attribute(
                node,
                known_read_methods=self.known_read_methods,
                known_write_methods=self.known_write_methods,
                bare=True,
            )
            if matched is not None:
                receiver = _receiver_name(matched.receiver)
                if matched.argument_style:
                    return DYNAMIC, None, receiver, matched.attribute
                return (
                    matched.known_kind,
                    matched.attribute,
                    receiver,
                    matched.attribute,
                )
            if _is_dynamic_getattr(node):
                # A dynamically-named getattr that is not itself provably
                # a dispatch attribute. Tracked as UNRESOLVED (not simply
                # ignored): if this expression is later assigned to a name
                # and that name is *never called*, no CallSite is ever
                # emitted from it -- ordinary dataclass-field
                # introspection (`value = getattr(self, name)`, never
                # invoked as `value()`) produces nothing. But if the name
                # *is* later called (`fn = getattr(client, method_name);
                # fn()`), the call site correctly resolves through this
                # alias to UNRESOLVED and fails closed -- but only when
                # the receiver is DEMONSTRATED broker-capable elsewhere in
                # this file (see `_scan_broker_capable_receivers`).
                # Ordinary internal dispatch (`getattr(self, f"_scenario_
                # {kind}")` on an object that never once calls
                # `self.call_tool(...)` anywhere) is not tracked at all.
                receiver_node = _getattr_target(node)[0]
                receiver = _receiver_name(receiver_node)
                root = receiver.split(".")[0]
                if root not in self.broker_capable_receivers:
                    return None
                return UNRESOLVED, None, receiver, "<dynamic>"
            return None

        if isinstance(node, ast.Call) and not _is_partial_call(node):
            # An already-invoked call: e.g. a dict value that is itself a
            # call result is not a *callable* reference, so it is not
            # dispatch-relevant here (nothing to invoke later).
            return None

        if _is_partial_call(node):
            call = node  # type: ignore[assignment]
            assert isinstance(call, ast.Call)
            if not call.args:
                return None
            inner = self._resolve_expression(call.args[0])
            if inner is None:
                return None
            kind, tool, receiver, attribute = inner
            if kind == DYNAMIC and len(call.args) > 1:
                pinned = _string_constant(call.args[1])
                if pinned is not None:
                    return LITERAL, pinned, receiver, attribute
            return kind, tool, receiver, attribute

        if isinstance(node, ast.Lambda):
            return self._resolve_call_expression(node.body)

        # Only a bare `X.call_tool` / `X.read` / `X.<known-method>`
        # attribute reference (not a getattr call -- handled above) can
        # reach here.
        matched = _match_dispatch_attribute(
            node,
            known_read_methods=self.known_read_methods,
            known_write_methods=self.known_write_methods,
            bare=True,
        )
        if matched is not None:
            receiver = _receiver_name(matched.receiver)
            if matched.argument_style:
                # A bare (uncalled) argument-style reference: we do not
                # know the tool name without a call site, so this can only
                # be resolved once actually invoked. Report it as
                # unresolved-but-tracked so a later alias-call site can
                # still be classified as DYNAMIC/UNRESOLVED rather than
                # silently vanishing.
                return DYNAMIC, None, receiver, matched.attribute
            return matched.known_kind, matched.attribute, receiver, matched.attribute

        if isinstance(node, ast.Name) and node.id in self.aliases:
            return self.aliases[node.id]

        if isinstance(node, ast.Subscript):
            base = node.value
            if isinstance(base, ast.Name) and base.id in self.registries:
                registry = self.registries[base.id]
                key_literal = _string_constant(node.slice)
                if key_literal is not None and key_literal in registry:
                    return registry[key_literal]
                if self.registry_all_safe.get(base.id, False):
                    return PROVEN_READ_ONLY, None, base.id, "<registry>"
                return UNRESOLVED, None, base.id, "<registry>"

        return None

    def _resolve_call_expression(
        self, call_node: ast.AST
    ) -> tuple[str, str | None, str, str] | None:
        """Resolve an expression that is itself a full call (e.g. a
        lambda's body), reusing the same argument-style resolution used
        for direct call sites."""
        if not isinstance(call_node, ast.Call):
            return None
        matched = _match_dispatch_attribute(
            call_node.func,
            known_read_methods=self.known_read_methods,
            known_write_methods=self.known_write_methods,
        )
        if matched is not None:
            receiver = _receiver_name(matched.receiver)
            if matched.argument_style:
                kind, tool = _resolve_argument_style(
                    call_node, constants=self.constants, validated=self.validated
                )
                return kind, tool, receiver, matched.attribute
            return matched.known_kind, matched.attribute, receiver, matched.attribute
        if _is_dynamic_getattr(call_node.func):
            target = _getattr_target(call_node.func)
            receiver = _receiver_name(target[0]) if target else "<expr>"
            return UNRESOLVED, None, receiver, "<dynamic>"
        if isinstance(call_node.func, ast.Name) and call_node.func.id in self.aliases:
            kind, tool, receiver, attribute = self.aliases[call_node.func.id]
            if kind == DYNAMIC and tool is None and call_node.args:
                # The alias only knew the attribute (argument-style,
                # unresolved until called); resolve it now.
                resolved_kind, resolved_tool = _resolve_argument_style(
                    call_node, constants=self.constants, validated=self.validated
                )
                return resolved_kind, resolved_tool, receiver, attribute
            return kind, tool, receiver, attribute
        return None

    # -- phase 2: aliases --

    def _build_aliases(self) -> None:
        for node in ast.walk(self.tree):
            if not isinstance(node, ast.Assign):
                continue
            if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
                continue
            name = node.targets[0].id
            resolved = self._resolve_expression(node.value)
            if resolved is None:
                continue
            self.aliases[name] = resolved
            self.consumed.add(id(node.value))
            for child in ast.walk(node.value):
                self.consumed.add(id(child))

    # -- phase 3: registries (dict literals with all-literal string keys) --

    def _build_registries(self) -> None:
        for node in ast.walk(self.tree):
            if not isinstance(node, ast.Assign):
                continue
            if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
                continue
            if not isinstance(node.value, ast.Dict):
                continue
            name = node.targets[0].id
            entries: dict[str, tuple[str, str | None, str, str]] = {}
            all_literal_keys = True
            any_unsafe = False
            for key, value in zip(node.value.keys, node.value.values):
                literal_key = _string_constant(key)
                resolved = self._resolve_expression(value)
                if resolved is not None:
                    self.consumed.add(id(value))
                    for child in ast.walk(value):
                        self.consumed.add(id(child))
                    kind = resolved[0]
                    if kind not in _ALWAYS_SAFE_KINDS and kind != LITERAL:
                        any_unsafe = True
                    if literal_key is not None:
                        entries[literal_key] = resolved
                if literal_key is None:
                    all_literal_keys = False
            if not entries:
                continue
            self.registries[name] = entries
            self.registry_all_safe[name] = all_literal_keys and not any_unsafe
            self.consumed.add(id(node.value))

    # -- phase 4: direct calls, getattr calls, alias calls, registry calls --

    def _direct_call_sites(self) -> list[CallSite]:
        sites: list[CallSite] = []

        for node in ast.walk(self.tree):
            if not isinstance(node, ast.Call):
                continue

            func = node.func

            # Shape: X.call_tool(...) / X.read(...) / X.<named-method>(...)
            if isinstance(func, ast.Attribute):
                matched = _match_dispatch_attribute(
                    func,
                    known_read_methods=self.known_read_methods,
                    known_write_methods=self.known_write_methods,
                )
                if matched is None:
                    continue
                receiver = _receiver_name(matched.receiver)
                if matched.argument_style:
                    if matched.attribute == GATEWAY_READ_ATTRIBUTE:
                        first_arg = node.args[0] if node.args else None
                        if not _is_gateway_call(matched.receiver, first_arg):
                            continue
                    kind, tool = _resolve_argument_style(
                        node, constants=self.constants, validated=self.validated
                    )
                else:
                    kind, tool = matched.known_kind, matched.attribute
                self.consumed.add(id(func))
                sites.append(
                    CallSite(self.path, node.lineno, receiver, matched.attribute, kind, tool)
                )
                continue

            # Shape: getattr(X, "call_tool")(...) or getattr(X, expr)(...)
            target = _getattr_target(func)
            if target is not None:
                receiver_node, attr_expr = target
                literal_attr = _string_constant(attr_expr)
                self.consumed.add(id(func))
                if literal_attr is None:
                    sites.append(
                        CallSite(
                            self.path,
                            node.lineno,
                            _receiver_name(receiver_node),
                            "<dynamic>",
                            UNRESOLVED,
                            None,
                            "getattr attribute name is not a string literal",
                        )
                    )
                    continue
                matched = _match_dispatch_attribute(
                    func,
                    known_read_methods=self.known_read_methods,
                    known_write_methods=self.known_write_methods,
                )
                if matched is None:
                    continue  # a getattr call unrelated to any known dispatch shape
                if matched.argument_style:
                    if matched.attribute == GATEWAY_READ_ATTRIBUTE:
                        first_arg = node.args[0] if node.args else None
                        if not _is_gateway_call(receiver_node, first_arg):
                            continue
                    kind, tool = _resolve_argument_style(
                        node, constants=self.constants, validated=self.validated
                    )
                else:
                    kind, tool = matched.known_kind, matched.attribute
                sites.append(
                    CallSite(
                        self.path,
                        node.lineno,
                        _receiver_name(receiver_node),
                        matched.attribute,
                        kind,
                        tool,
                    )
                )
                continue

            # Shape: fn(...) where fn is a recognized alias.
            if isinstance(func, ast.Name) and func.id in self.aliases:
                self.consumed.add(id(func))
                kind, tool, receiver, attribute = self.aliases[func.id]
                if kind == DYNAMIC and tool is None and node.args:
                    kind, tool = _resolve_argument_style(
                        node, constants=self.constants, validated=self.validated
                    )
                sites.append(
                    CallSite(self.path, node.lineno, receiver, attribute, kind, tool)
                )
                continue

            # Shape: REGISTRY[key](...) or REGISTRY.get(key)(...) style
            # subscript dispatch.
            if isinstance(func, ast.Subscript):
                base = func.value
                if isinstance(base, ast.Name) and base.id in self.registries:
                    self.consumed.add(id(func))
                    registry = self.registries[base.id]
                    key_literal = _string_constant(func.slice)
                    if key_literal is not None and key_literal in registry:
                        kind, tool, receiver, attribute = registry[key_literal]
                    elif self.registry_all_safe.get(base.id, False):
                        kind, tool, receiver, attribute = (
                            PROVEN_READ_ONLY,
                            None,
                            base.id,
                            "<registry>",
                        )
                    else:
                        kind, tool, receiver, attribute = (
                            UNRESOLVED,
                            None,
                            base.id,
                            "<registry>",
                        )
                    sites.append(
                        CallSite(self.path, node.lineno, receiver, attribute, kind, tool)
                    )
                    continue

        return sites

    def _leak_sites(self) -> list[CallSite]:
        """Bare broker-dispatch-attribute references not consumed by any
        bounded resolution above: passed as an argument, returned, stored
        in an unrecognized container, or otherwise escaped into a context
        this analysis cannot prove safe."""
        sites: list[CallSite] = []

        for node in ast.walk(self.tree):
            if id(node) in self.consumed:
                continue

            if isinstance(node, ast.Attribute):
                matched = _match_dispatch_attribute(
                    node,
                    known_read_methods=self.known_read_methods,
                    known_write_methods=self.known_write_methods,
                    bare=True,
                )
                if matched is None:
                    continue
                receiver = _receiver_name(matched.receiver)
                sites.append(
                    CallSite(
                        self.path,
                        node.lineno,
                        receiver,
                        matched.attribute,
                        UNRESOLVED,
                        None,
                        "broker-dispatch reference escaped an unproven context",
                    )
                )
                continue

            target = _getattr_target(node)
            if target is not None and isinstance(node, ast.Call):
                receiver_node, attr_expr = target
                literal_attr = _string_constant(attr_expr)
                matched = _match_dispatch_attribute(
                    node,
                    known_read_methods=self.known_read_methods,
                    known_write_methods=self.known_write_methods,
                    bare=True,
                )
                if matched is None:
                    # Either a getattr unrelated to any known dispatch
                    # attribute (literal but ordinary, e.g. "value"), or a
                    # dynamically-named getattr that is never invoked here
                    # (e.g. `getattr(self, name)` field introspection).
                    # Neither exposes dispatch capability by itself; an
                    # *invoked* dynamic getattr is caught separately in
                    # `_direct_call_sites`.
                    continue
                receiver = _receiver_name(receiver_node)
                sites.append(
                    CallSite(
                        self.path,
                        node.lineno,
                        receiver,
                        literal_attr or "<dynamic>",
                        UNRESOLVED,
                        None,
                        "broker-dispatch reference escaped an unproven context",
                    )
                )

        return sites

    def _mark_capability_probes_consumed(self) -> None:
        """`callable(getattr(X, "call_tool", None))` and
        `callable(X.call_tool)` check whether a dispatch attribute exists
        and is callable -- they never invoke or expose it. This is an
        explicit, reviewed, narrow exclusion (EXCLUDED_BY_REVIEWED_POLICY
        in spirit): only the sole/first argument of a call to the builtin
        `callable(...)` is exempted, and only that one argument
        expression -- not the whole file, not the receiver, and not any
        alias built from it elsewhere. Confirmed against the current
        repository: this is exactly the idiom used in
        `read_only_observation_runtime.py`, `robinhood_commissioning_
        adapter.py`, and `robinhood_live_adapter.py` to check session
        capability before ever attempting a call.
        """
        for node in ast.walk(self.tree):
            if not isinstance(node, ast.Call):
                continue
            if not isinstance(node.func, ast.Name) or node.func.id != "callable":
                continue
            if not node.args:
                continue
            probed = node.args[0]
            self.consumed.add(id(probed))
            for child in ast.walk(probed):
                self.consumed.add(id(child))

    def build(self) -> list[CallSite]:
        # Evidence of broker-capable receivers first: dynamic-getattr
        # alias tracking below needs to know which receivers are
        # demonstrated broker-capable in this file.
        self._scan_broker_capable_receivers()
        # Registries next: an alias may reference a registry entry
        # (`operation = OPERATIONS["key"]`), so the registry map must
        # already exist when aliases are resolved.
        self._build_registries()
        self._build_aliases()
        self._mark_capability_probes_consumed()
        direct = self._direct_call_sites()
        leaks = self._leak_sites()
        return sorted(direct + leaks, key=lambda site: site.line)


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


def _validated_names(tree: ast.Module) -> frozenset[str]:
    """Names assigned from the documented allowlist validator."""

    names: set[str] = set()

    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue

        value = node.value

        if not isinstance(value, ast.Call):
            continue

        func = value.func
        called = getattr(func, "id", None) or getattr(func, "attr", None)

        if called != SAFE_WRAPPER_VALIDATOR:
            continue

        for target in node.targets:
            if isinstance(target, ast.Name):
                names.add(target.id)

    return frozenset(names)


def analyze_source(
    source: str,
    *,
    path: str,
    known_read_methods: frozenset[str] = frozenset(),
    known_write_methods: frozenset[str] = frozenset(),
) -> list[CallSite]:
    tree = ast.parse(source)
    builder = _CallSiteBuilder(
        tree,
        path=path,
        known_read_methods=known_read_methods,
        known_write_methods=known_write_methods,
    )
    return builder.build()


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

    A site fails when its tool name is dynamic or unresolvable, when it
    resolved to a known write (argument-style or named-method-style), or
    when a resolved literal is absent from the immutable allowlist.
    """

    offending: list[CallSite] = []

    for site in sites:
        if site.kind in _ALWAYS_SAFE_KINDS:
            continue

        if site.kind in _ALWAYS_UNSAFE_KINDS:
            offending.append(site)
            continue

        # LITERAL: resolved to a specific tool name, judged by allowlist.
        if site.tool not in allowlist:
            offending.append(site)

    return offending


__all__ = [
    "CALL_TOOL_ATTRIBUTE",
    "GATEWAY_READ_ATTRIBUTE",
    "DISPATCH_ATTRIBUTES",
    "SAFE_WRAPPER_VALIDATOR",
    "LITERAL",
    "SAFE_WRAPPER",
    "DYNAMIC",
    "KNOWN_WRITE",
    "PROVEN_READ_ONLY",
    "UNRESOLVED",
    "CallSite",
    "analyze_source",
    "runtime_files",
    "analyze_runtime_tree",
    "violations",
]
