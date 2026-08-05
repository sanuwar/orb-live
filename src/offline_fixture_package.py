"""Typed, deterministic metadata packages for synthetic offline rehearsals.

This module contains no provider results, persistence, scheduler, or execution
logic.  It packages only constrained scenario identity and injected timestamps
so callers can construct the already-reviewed public offline components without
copying fixture metadata or accepting arbitrary dictionaries.
"""

from __future__ import annotations

import json
import hashlib
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from enum import Enum
from typing import Any


SCHEMA_VERSION = 1
MAX_TICKS = 100
DEFAULT_SESSION_DATE = date(2026, 8, 3)
DEFAULT_TICK_TIMESTAMPS = (
    datetime(2026, 8, 3, 14, 0, tzinfo=timezone.utc),
)


class OfflineFixtureError(ValueError):
    """A fixture package is malformed, stale, private, or untrusted."""


class FixtureScenario(str, Enum):
    SAFE_OFFLINE_COMPLETION = "SAFE_OFFLINE_COMPLETION"
    OPERATOR_REVIEW_REQUIRED = "OPERATOR_REVIEW_REQUIRED"
    ACTIVE_SUSPENSION = "ACTIVE_SUSPENSION"
    AMBIGUOUS_RESTART = "AMBIGUOUS_RESTART"
    MISSED_HEARTBEAT = "MISSED_HEARTBEAT"
    ALERT_DELIVERY_UNCONFIRMED = "ALERT_DELIVERY_UNCONFIRMED"
    ACKNOWLEDGED_BUT_STILL_SUSPENDED = "ACKNOWLEDGED_BUT_STILL_SUSPENDED"
    READINESS_BLOCKED = "READINESS_BLOCKED"
    STALE_EVIDENCE = "STALE_EVIDENCE"
    DURABLE_RESTART = "DURABLE_RESTART"


_SCENARIO_SLUGS = {
    FixtureScenario.SAFE_OFFLINE_COMPLETION: "safe-offline-completion",
    FixtureScenario.OPERATOR_REVIEW_REQUIRED: "operator-review-required",
    FixtureScenario.ACTIVE_SUSPENSION: "active-suspension",
    FixtureScenario.AMBIGUOUS_RESTART: "ambiguous-restart",
    FixtureScenario.MISSED_HEARTBEAT: "missed-heartbeat",
    FixtureScenario.ALERT_DELIVERY_UNCONFIRMED: "alert-delivery-unconfirmed",
    FixtureScenario.ACKNOWLEDGED_BUT_STILL_SUSPENDED: "acknowledged-still-suspended",
    FixtureScenario.READINESS_BLOCKED: "readiness-blocked",
    FixtureScenario.STALE_EVIDENCE: "stale-evidence",
    FixtureScenario.DURABLE_RESTART: "durable-restart",
}

_MANIFEST_KEYS = frozenset({
    "schema_version", "fixture_id", "scenario", "runner_id", "correlation_id",
    "session_date", "tick_timestamps", "permits_automated_action",
    "manifest_digest",
})
_FORBIDDEN_TEXT = (
    "account", "balance", "broker", "credential", "endpoint", "manifest",
    "order", "password", "price", "quantity", "secret", "symbol", "token",
    "username", "webhook",
)


def _identifier(value: object, prefix: str, label: str) -> str:
    if (
        not isinstance(value, str)
        or not value.startswith(prefix)
        or value != value.strip()
        or not value.replace("-", "").replace("_", "").isalnum()
        or not 1 <= len(value) <= 96
        or any(fragment in value.casefold() for fragment in _FORBIDDEN_TEXT)
    ):
        raise OfflineFixtureError(f"{label} is invalid")
    return value


def _moment(value: object, label: str) -> datetime:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.tzinfo.utcoffset(value) is None
    ):
        raise OfflineFixtureError(f"{label} is invalid")
    return value


def _parse_moment(value: object, label: str) -> datetime:
    if not isinstance(value, str):
        raise OfflineFixtureError(f"{label} is invalid")
    try:
        return _moment(datetime.fromisoformat(value), label)
    except (TypeError, ValueError):
        raise OfflineFixtureError(f"{label} is invalid") from None


def _scenario(value: object) -> FixtureScenario:
    if not isinstance(value, str):
        raise OfflineFixtureError("scenario is invalid")
    try:
        return FixtureScenario(value)
    except ValueError:
        raise OfflineFixtureError("scenario is invalid") from None


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise OfflineFixtureError("fixture manifest contains duplicate keys")
        result[key] = value
    return result


def available_scenarios() -> tuple[FixtureScenario, ...]:
    """Return the fixed catalog in its stable serialization order."""
    return tuple(FixtureScenario)


@dataclass(frozen=True)
class OfflineFixturePackage:
    """One privacy-safe fixture manifest and its injected time set."""

    fixture_id: str
    scenario: FixtureScenario
    runner_id: str
    correlation_id: str
    session_date: date
    tick_timestamps: tuple[datetime, ...]
    permits_automated_action: bool = False
    manifest_digest: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.scenario, FixtureScenario):
            raise OfflineFixtureError("scenario must be a FixtureScenario")
        selected = _scenario(self.scenario)
        slug = _SCENARIO_SLUGS[selected]
        _identifier(self.fixture_id, "fixture-", "fixture identity")
        _identifier(self.runner_id, "runner-", "runner identity")
        _identifier(self.correlation_id, "correlation-", "correlation identity")
        if self.fixture_id != f"fixture-{slug}":
            raise OfflineFixtureError("fixture identity is not bound to scenario")
        if self.runner_id != f"runner-fixture-{slug}":
            raise OfflineFixtureError("runner identity is not bound to scenario")
        if self.correlation_id != f"correlation-fixture-{slug}":
            raise OfflineFixtureError("correlation identity is not bound to scenario")
        if not isinstance(self.session_date, date) or isinstance(self.session_date, datetime):
            raise OfflineFixtureError("session date is invalid")
        if not isinstance(self.tick_timestamps, tuple) or not self.tick_timestamps:
            raise OfflineFixtureError("tick timestamps are invalid")
        if len(self.tick_timestamps) > MAX_TICKS:
            raise OfflineFixtureError("tick timestamp count exceeds bound")
        previous: datetime | None = None
        for stamp in self.tick_timestamps:
            checked = _moment(stamp, "tick timestamp")
            if checked.date() != self.session_date:
                raise OfflineFixtureError("tick timestamp is outside session date")
            if previous is not None and checked <= previous:
                raise OfflineFixtureError("tick timestamps must be unique and ordered")
            previous = checked
        if self.permits_automated_action is not False:
            raise OfflineFixtureError("offline fixtures never authorize action")
        expected_digest = _manifest_digest(self)
        if self.manifest_digest is None:
            object.__setattr__(self, "manifest_digest", expected_digest)
        elif self.manifest_digest != expected_digest:
            raise OfflineFixtureError("fixture manifest digest does not match content")

    def validate(self, *, evaluation_time: datetime, maximum_age: timedelta) -> None:
        """Reject future or stale evidence relative to caller-injected time."""
        evaluated = _moment(evaluation_time, "evaluation time")
        if not isinstance(maximum_age, timedelta) or maximum_age <= timedelta(0):
            raise OfflineFixtureError("maximum evidence age is invalid")
        if any(stamp > evaluated for stamp in self.tick_timestamps):
            raise OfflineFixtureError("fixture timestamp is in the future")
        if evaluated - self.tick_timestamps[-1] > maximum_age:
            raise OfflineFixtureError("fixture evidence is stale")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": SCHEMA_VERSION,
            "fixture_id": self.fixture_id,
            "scenario": self.scenario.value,
            "runner_id": self.runner_id,
            "correlation_id": self.correlation_id,
            "session_date": self.session_date.isoformat(),
            "tick_timestamps": [stamp.isoformat() for stamp in self.tick_timestamps],
            "permits_automated_action": False,
            "manifest_digest": self.manifest_digest,
        }

    def serialize_manifest(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))


def build_fixture(
    scenario: FixtureScenario,
    *,
    tick_timestamps: tuple[datetime, ...] = DEFAULT_TICK_TIMESTAMPS,
    session_date: date = DEFAULT_SESSION_DATE,
) -> OfflineFixturePackage:
    """Construct one catalog-bound package from injected values only."""
    if not isinstance(scenario, FixtureScenario):
        raise OfflineFixtureError("scenario must be a FixtureScenario")
    selected = _scenario(scenario)
    slug = _SCENARIO_SLUGS[selected]
    return OfflineFixturePackage(
        fixture_id=f"fixture-{slug}", scenario=selected,
        runner_id=f"runner-fixture-{slug}",
        correlation_id=f"correlation-fixture-{slug}",
        session_date=session_date, tick_timestamps=tick_timestamps,
    )


def _manifest_digest(package: OfflineFixturePackage) -> str:
    unsigned = {
        "schema_version": SCHEMA_VERSION,
        "fixture_id": package.fixture_id,
        "scenario": package.scenario.value,
        "runner_id": package.runner_id,
        "correlation_id": package.correlation_id,
        "session_date": package.session_date.isoformat(),
        "tick_timestamps": [stamp.isoformat() for stamp in package.tick_timestamps],
        "permits_automated_action": False,
    }
    encoded = json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def load_fixture_manifest(payload: str) -> OfflineFixturePackage:
    """Load only the exact, JSON-safe manifest schema."""
    if not isinstance(payload, str):
        raise OfflineFixtureError("fixture manifest must be JSON text")
    try:
        raw: Any = json.loads(payload, object_pairs_hook=_reject_duplicate_keys)
    except OfflineFixtureError:
        raise
    except (TypeError, ValueError, json.JSONDecodeError):
        raise OfflineFixtureError("fixture manifest is malformed") from None
    if not isinstance(raw, dict) or set(raw) != _MANIFEST_KEYS:
        raise OfflineFixtureError("fixture manifest keys are not exact")
    if type(raw.get("schema_version")) is not int or raw["schema_version"] != SCHEMA_VERSION:
        raise OfflineFixtureError("fixture schema version is unsupported")
    if raw.get("permits_automated_action") is not False:
        raise OfflineFixtureError("fixture permission is invalid")
    scenario = _scenario(raw.get("scenario"))
    if any(
        not isinstance(value, str)
        or any(fragment in value.casefold() for fragment in _FORBIDDEN_TEXT)
        for value in raw.values()
        if isinstance(value, str)
    ):
        raise OfflineFixtureError("fixture manifest contains prohibited text")
    timestamps = raw.get("tick_timestamps")
    if not isinstance(timestamps, list):
        raise OfflineFixtureError("tick timestamps are invalid")
    try:
        session_date = date.fromisoformat(raw["session_date"])
    except (TypeError, ValueError):
        raise OfflineFixtureError("session date is invalid") from None
    return OfflineFixturePackage(
        fixture_id=raw["fixture_id"], scenario=scenario,
        runner_id=raw["runner_id"], correlation_id=raw["correlation_id"],
        session_date=session_date,
        tick_timestamps=tuple(_parse_moment(value, "tick timestamp") for value in timestamps),
        manifest_digest=raw["manifest_digest"],
    )


__all__ = [
    "DEFAULT_SESSION_DATE", "DEFAULT_TICK_TIMESTAMPS", "FixtureScenario",
    "OfflineFixtureError", "OfflineFixturePackage", "available_scenarios",
    "build_fixture", "load_fixture_manifest",
]
