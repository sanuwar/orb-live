"""Small registered-route layer used by the read-only operator WSGI app.

The operator application is deliberately dependency-free.  This module provides
the application-owned route objects and table that are constructed before the
WSGI callable is used; it is not a second dispatcher or a policy registry.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, replace


_PARAMETER = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")


def normalize_route_path(path: str) -> str:
    """Return the canonical route-template spelling used by the app table."""
    if not isinstance(path, str) or not path.startswith("/") or path.startswith("//"):
        raise ValueError("route path must be absolute")
    if path != "/" and path.endswith("/"):
        raise ValueError("route path must not have a trailing slash")
    normalized = _PARAMETER.sub(lambda match: "{" + match.group(1) + "}", path)
    if normalized != "/":
        for segment in normalized.strip("/").split("/"):
            if not segment or "?" in segment or "{" in segment.replace("{", "", 1) or "}" in segment.replace("}", "", 1):
                raise ValueError("route path syntax is invalid")
            remainder = _PARAMETER.sub("", segment)
            if "{" in remainder or "}" in remainder or (remainder and not remainder.replace(".", "").replace("-", "").replace("_", "").isalnum()):
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
        actual = path if path == "/" else path.rstrip("/")
        expected_parts = self.path.strip("/").split("/") if self.path != "/" else []
        actual_parts = actual.strip("/").split("/") if actual != "/" else []
        if len(expected_parts) != len(actual_parts):
            return False
        return all(
            expected == actual or (_PARAMETER.fullmatch(expected) is not None and bool(actual))
            for expected, actual in zip(expected_parts, actual_parts)
        )


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
        for route in self.routes:
            if route.name in names:
                raise ValueError("duplicate operator route name")
            names.add(route.name)
            for method in route.methods:
                key = (route.path, method)
                if key in path_methods:
                    raise ValueError("duplicate operator route path/method")
                path_methods.add(key)
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
