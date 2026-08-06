"""Small registered-route layer used by the read-only operator WSGI app.

The operator application is deliberately dependency-free.  This module provides
the application-owned route objects and table that are constructed before the
WSGI callable is used; it is not a second dispatcher or a policy registry.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, replace


_PARAMETER = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _route_segment_parts(segment: str) -> list[tuple[str, str]]:
    """Return literal/parameter pieces for one registered path segment."""
    pieces: list[tuple[str, str]] = []
    cursor = 0
    for match in _PARAMETER.finditer(segment):
        literal = segment[cursor:match.start()]
        if "{" in literal or "}" in literal:
            raise ValueError("route path syntax is invalid")
        if literal:
            pieces.append(("literal", literal))
        pieces.append(("parameter", match.group(1)))
        cursor = match.end()
    literal = segment[cursor:]
    if "{" in literal or "}" in literal:
        raise ValueError("route path syntax is invalid")
    if literal:
        pieces.append(("literal", literal))
    if not pieces:
        raise ValueError("route path syntax is invalid")
    return pieces


def _compile_route_pattern(path: str) -> tuple[re.Pattern[str], tuple[str, ...]]:
    """Compile a registered route template without accepting arbitrary regex."""
    normalized = normalize_route_path(path)
    if normalized == "/":
        return re.compile(r"^/$"), ()

    parameter_names: list[str] = []
    compiled_segments: list[str] = []
    for segment in normalized.strip("/").split("/"):
        segment_pattern: list[str] = []
        pieces = _route_segment_parts(segment)
        for index, (kind, value) in enumerate(pieces):
            if kind == "literal":
                segment_pattern.append(re.escape(value))
                continue
            if value in parameter_names:
                raise ValueError("duplicate route parameter name")
            parameter_names.append(value)
            has_literal_after = any(piece_kind == "literal" for piece_kind, _ in pieces[index + 1 :])
            quantifier = "+?" if has_literal_after else "+"
            segment_pattern.append(f"(?P<{value}>[^/]{quantifier})")
        compiled_segments.append("".join(segment_pattern))

    return re.compile(r"^/" + "/".join(compiled_segments) + r"/?$"), tuple(parameter_names)


def normalize_route_path(path: str) -> str:
    """Return the canonical route-template spelling used by the app table."""
    if not isinstance(path, str) or not path.startswith("/") or path.startswith("//"):
        raise ValueError("route path must be absolute")
    if path != "/" and path.endswith("/"):
        raise ValueError("route path must not have a trailing slash")
    normalized = _PARAMETER.sub(lambda match: "{" + match.group(1) + "}", path)
    if normalized != "/":
        parameter_names: set[str] = set()
        for segment in normalized.strip("/").split("/"):
            for kind, value in _route_segment_parts(segment):
                if kind == "parameter":
                    if value in parameter_names:
                        raise ValueError("duplicate route parameter name")
                    parameter_names.add(value)
                elif value == "?" or not value.replace(".", "").replace("-", "").replace("_", "").isalnum():
                    raise ValueError("route path syntax is invalid")
    return normalized


@dataclass(frozen=True)
class ApplicationRoute:
    path: str
    methods: tuple[str, ...]
    name: str
    endpoint: str
    route_type: str
    classification: str
    privacy_classification: str
    safety_authority: str
    permits_database_write: bool
    permits_runtime_write: bool
    permits_outbound_network: bool
    exposes_private_data: bool
    response_kind: str
    requires_store: bool
    supports_file_fixture: bool
    supports_sqlite: bool
    auto_refresh_eligible: bool

    @property
    def route(self) -> str:
        return self.path

    def normalized(self) -> "ApplicationRoute":
        methods = tuple(sorted({method.upper() for method in self.methods}))
        if not methods:
            raise ValueError("route must declare at least one method")
        return replace(self, path=normalize_route_path(self.path), methods=methods)

    def to_dict(self) -> dict[str, object]:
        return {
            "route": self.path,
            "path": self.path,
            "methods": list(self.methods),
            "name": self.name,
            "endpoint": self.endpoint,
            "route_type": self.route_type,
            "classification": self.classification,
            "privacy_classification": self.privacy_classification,
            "safety_authority": self.safety_authority,
            "permits_database_write": self.permits_database_write,
            "permits_runtime_write": self.permits_runtime_write,
            "permits_outbound_network": self.permits_outbound_network,
            "exposes_private_data": self.exposes_private_data,
            "response_kind": self.response_kind,
            "requires_store": self.requires_store,
            "supports_file_fixture": self.supports_file_fixture,
            "supports_sqlite": self.supports_sqlite,
            "auto_refresh_eligible": self.auto_refresh_eligible,
        }

    def matches(self, path: str) -> bool:
        return self.match_params(path) is not None

    def match_params(self, path: str) -> dict[str, str] | None:
        pattern, parameter_names = _compile_route_pattern(self.path)
        match = pattern.fullmatch(path)
        if match is None:
            return None
        return {name: match.group(name) for name in parameter_names}


class ApplicationRouteTable:
    """Ordered route table registered while constructing ``OperatorWebApp``."""

    def __init__(self) -> None:
        self._routes: list[ApplicationRoute] = []

    @property
    def routes(self) -> tuple[ApplicationRoute, ...]:
        return tuple(self._routes)

    def add(self, route: ApplicationRoute) -> ApplicationRoute:
        normalized = route.normalized()
        self._routes.append(normalized)
        return normalized

    def mount(self, prefix: str, child: "ApplicationRouteTable", *, name_prefix: str, endpoint_prefix: str, classification: str | None = None) -> None:
        prefix = normalize_route_path(prefix)
        for route in child.routes:
            child_path = "" if route.path == "/" else route.path
            full_path = prefix if not child_path else prefix.rstrip("/") + child_path
            self.add(replace(route, path=full_path, name=f"{name_prefix}.{route.name}", endpoint=f"{endpoint_prefix}:{route.endpoint}", route_type="MOUNTED", classification=classification or route.classification))

    def validate(self) -> None:
        names: set[str] = set()
        path_methods: set[tuple[str, str]] = set()
        route_shapes: set[tuple[str, str]] = set()
        for route in self.routes:
            if route.name in names:
                raise ValueError("duplicate operator route name")
            names.add(route.name)
            for method in route.methods:
                key = (route.path, method)
                if key in path_methods:
                    raise ValueError("duplicate operator route path/method")
                path_methods.add(key)
                shape = _PARAMETER.sub("{}", route.path)
                shape_key = (shape, method)
                if shape_key in route_shapes:
                    raise ValueError("ambiguous operator route path/method")
                route_shapes.add(shape_key)
            if route.route_type not in {"HTML", "API", "HEALTH", "EXPORT", "MOUNTED"}:
                raise ValueError("unknown operator route type")
            if route.safety_authority != "NONE" or route.permits_database_write or route.permits_runtime_write or route.permits_outbound_network or route.exposes_private_data:
                raise ValueError("unsafe operator route declaration")

    def match(self, path: str, method: str = "GET") -> ApplicationRoute | None:
        method = method.upper()
        for route in self.routes:
            if method in route.methods and route.matches(path):
                return route
        return None
