"""Privacy-safe operational monitoring and alerts.

Alerts are the one thing in this system that deliberately **leaves it** — to a
phone, an inbox, a chat channel, a log aggregator someone else operates. That
makes them the most likely place for a leak, and the least likely place for
anyone to notice one.

So this module makes free-form text **unrepresentable**. An `Alert` carries a
category, a severity, and a mapping of allowlisted fact keys to allowlisted
fact values. There is no `message` field, no `detail` field, and no way to
attach one: a string that nobody validated is exactly how an account number
ends up in a push notification.

Rendering an alert for a human produces a fixed sentence assembled from those
enums. It reads a little stiffly. That is the trade, and it is the right way
round — a stiff alert that leaks nothing beats a fluent one that leaks once.

## Flap suppression is a safety feature, not a convenience

A condition that alternates every few seconds will send hundreds of alerts, an
operator will mute the channel, and the one alert that mattered will arrive
into silence. `AlertPolicy` suppresses repeats of the same category within a
cooldown — but **never suppresses an escalation**, because a warning becoming
critical is new information no matter how recently the warning fired.

## Heartbeats

A process that dies silently looks identical to one that is idle. The heartbeat
monitor turns that into a detectable difference, and treats *no heartbeat ever
recorded* as missing rather than fine — an unstarted monitor must not read as
healthy.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Mapping


class MonitoringError(RuntimeError):
    """A malformed alert or signal. Never carries broker text."""


class AlertSeverity(str, Enum):
    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


_SEVERITY_RANK = {
    AlertSeverity.INFO: 0,
    AlertSeverity.WARNING: 1,
    AlertSeverity.CRITICAL: 2,
}


class AlertCategory(str, Enum):
    """Every alert this system may raise. A category not listed cannot be
    sent, which is what keeps the alert surface reviewable."""

    HEARTBEAT_MISSING = "HEARTBEAT_MISSING"
    HEARTBEAT_RECOVERED = "HEARTBEAT_RECOVERED"
    BROKER_DATA_STALE = "BROKER_DATA_STALE"
    RISK_LIMIT_BREACHED = "RISK_LIMIT_BREACHED"
    KILL_SWITCH_ENGAGED = "KILL_SWITCH_ENGAGED"
    OPERATOR_ACTION_REQUIRED = "OPERATOR_ACTION_REQUIRED"
    RECOVERY_INCONSISTENT = "RECOVERY_INCONSISTENT"
    INTERNAL_INVARIANT_FAILURE = "INTERNAL_INVARIANT_FAILURE"


#: Categories that always mean a human must look, whatever severity is
#: claimed. Declaring an escalation as INFO is refused rather than honoured.
ALWAYS_ESCALATING: frozenset[AlertCategory] = frozenset({
    AlertCategory.KILL_SWITCH_ENGAGED,
    AlertCategory.OPERATOR_ACTION_REQUIRED,
    AlertCategory.INTERNAL_INVARIANT_FAILURE,
})


#: The complete vocabulary an alert may carry. Keys and values are both fixed:
#: an unknown key is as dangerous as an unknown value, because a key is also a
#: string somebody chose.
ALLOWED_ALERT_FACTS: Mapping[str, frozenset[str]] = MappingProxyType({
    "component": frozenset({"scheduler", "broker_reader", "risk", "recovery", "runtime"}),
    "condition": frozenset({"missing", "recovered", "stale", "breached", "engaged", "failed"}),
    "count_bucket": frozenset({"none", "one", "many"}),
    "consecutive_bucket": frozenset({"none", "one", "many"}),
    "requires_operator": frozenset({"true", "false"}),
})


def validate_alert_facts(facts: Mapping[str, str]) -> None:
    """Reject any key or value outside the allowlist, and any non-string."""
    if not isinstance(facts, Mapping):
        raise MonitoringError("alert facts must be a mapping")
    for key, value in facts.items():
        allowed = ALLOWED_ALERT_FACTS.get(key)
        if allowed is None:
            raise MonitoringError("alert fact key is not allowlisted")
        if not isinstance(value, str) or value not in allowed:
            raise MonitoringError("alert fact value is not allowlisted")


def count_bucket(value: int) -> str:
    """Coarse. An exact count in an outbound alert is a detail nobody needs
    and one more thing that can identify a run."""
    if not isinstance(value, int) or isinstance(value, bool):
        raise MonitoringError("count must be an int")
    if value <= 0:
        return "none"
    if value == 1:
        return "one"
    return "many"


@dataclass(frozen=True)
class Alert:
    """A message that leaves the system. Everything in it is an enum or an
    allowlisted string — there is no field that can carry free text."""

    category: AlertCategory
    severity: AlertSeverity
    facts: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.category, AlertCategory):
            raise MonitoringError("alert category is invalid")
        if not isinstance(self.severity, AlertSeverity):
            raise MonitoringError("alert severity is invalid")
        validate_alert_facts(self.facts)
        if (
            self.category in ALWAYS_ESCALATING
            and self.severity is AlertSeverity.INFO
        ):
            # Downgrading an escalation to INFO is how a critical condition
            # ends up filtered out of a notification channel.
            raise MonitoringError("this category may not be raised as INFO")
        object.__setattr__(self, "facts", MappingProxyType(dict(self.facts)))

    @property
    def requires_operator(self) -> bool:
        return (
            self.severity is AlertSeverity.CRITICAL
            or self.category in ALWAYS_ESCALATING
        )

    def render(self) -> str:
        """A fixed sentence assembled from enums. Never interpolates input."""
        parts = [f"[{self.severity.value}] {self.category.value}"]
        for key in sorted(self.facts):
            parts.append(f"{key}={self.facts[key]}")
        return " ".join(parts)


# --------------------------------------------------------------------------
# Heartbeats.
# --------------------------------------------------------------------------


class HeartbeatStatus(str, Enum):
    HEALTHY = "HEALTHY"
    MISSING = "MISSING"
    NEVER_STARTED = "NEVER_STARTED"


@dataclass(frozen=True)
class HeartbeatAssessment:
    status: HeartbeatStatus
    consecutive_misses: int = 0

    @property
    def is_healthy(self) -> bool:
        return self.status is HeartbeatStatus.HEALTHY


def assess_heartbeat(
    last_beat_monotonic: float | None,
    *,
    current_monotonic: float,
    interval_seconds: float,
    missed_beats_before_alert: int = 2,
) -> HeartbeatAssessment:
    """A process that died silently looks identical to one that is idle.

    `None` means no beat was ever recorded, which is `NEVER_STARTED` rather
    than healthy: a monitor that has not started must not read as fine.
    """
    if interval_seconds <= 0:
        raise MonitoringError("heartbeat interval must be positive")
    if missed_beats_before_alert < 1:
        raise MonitoringError("at least one missed beat is required to alert")
    if last_beat_monotonic is None:
        return HeartbeatAssessment(HeartbeatStatus.NEVER_STARTED)
    if not isinstance(last_beat_monotonic, (int, float)) or isinstance(
        last_beat_monotonic, bool
    ):
        raise MonitoringError("heartbeat time must be a number")
    if current_monotonic < last_beat_monotonic:
        # A beat from the future means the clock or the record is wrong.
        return HeartbeatAssessment(HeartbeatStatus.MISSING)
    elapsed = current_monotonic - last_beat_monotonic
    missed = int(elapsed // interval_seconds)
    if missed >= missed_beats_before_alert:
        return HeartbeatAssessment(HeartbeatStatus.MISSING, missed)
    return HeartbeatAssessment(HeartbeatStatus.HEALTHY, missed)


# --------------------------------------------------------------------------
# Flap suppression.
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class _SendRecord:
    sent_monotonic: float
    severity: AlertSeverity
    delivered: bool


@dataclass
class AlertPolicy:
    """Suppresses repeats, never escalations, and never unconfirmed sends.

    Keyed by category rather than by rendered text, so a condition that
    reports slightly different facts each time still counts as the same
    condition — which is precisely the flapping case that floods a channel.

    Suppression is only safe if the thing being suppressed actually arrived.
    An earlier version assumed it had, which meant a critical alert whose
    delivery failed was suppressed for the whole cooldown and the operator
    never learned. **A send is treated as undelivered until
    `confirm_delivery` says otherwise**, and an undelivered category is
    always resent. Silence is not evidence of health.

    That trades some duplicate alerts for never losing one, which is the
    correct direction: a duplicate is an annoyance, a lost critical is the
    failure this module exists to prevent.
    """

    cooldown_seconds: float = 300.0
    _last_sent: dict[AlertCategory, _SendRecord] = field(
        default_factory=dict, init=False
    )

    def __post_init__(self) -> None:
        if self.cooldown_seconds < 0:
            raise MonitoringError("cooldown must not be negative")

    def _remember(self, alert: Alert, moment: float) -> None:
        self._last_sent[alert.category] = _SendRecord(moment, alert.severity, False)

    def should_send(self, alert: Alert, *, current_monotonic: float) -> bool:
        if not isinstance(alert, Alert):
            raise MonitoringError("an alert is required")
        previous = self._last_sent.get(alert.category)
        if previous is None:
            self._remember(alert, current_monotonic)
            return True
        if not previous.delivered:
            # The last one may never have arrived. Suppressing on top of an
            # unconfirmed send is how a condition goes quiet without being
            # resolved.
            self._remember(alert, current_monotonic)
            return True
        if _SEVERITY_RANK[alert.severity] > _SEVERITY_RANK[previous.severity]:
            # New information regardless of how recently the lower severity
            # fired. Suppressing this is how a warning hides a critical.
            self._remember(alert, current_monotonic)
            return True
        if current_monotonic - previous.sent_monotonic >= self.cooldown_seconds:
            self._remember(alert, current_monotonic)
            return True
        return False

    def confirm_delivery(self, category: AlertCategory) -> None:
        """Record that an alert actually reached its destination."""
        if not isinstance(category, AlertCategory):
            raise MonitoringError("alert category is invalid")
        previous = self._last_sent.get(category)
        if previous is None:
            raise MonitoringError("no send is outstanding for this category")
        self._last_sent[category] = _SendRecord(
            previous.sent_monotonic, previous.severity, True
        )

    def record_delivery_failure(self, category: AlertCategory) -> None:
        """Explicitly mark a send as failed. Equivalent to never confirming
        it, and available so a caller can say so rather than stay silent."""
        if not isinstance(category, AlertCategory):
            raise MonitoringError("alert category is invalid")
        if category not in self._last_sent:
            raise MonitoringError("no send is outstanding for this category")
        previous = self._last_sent[category]
        self._last_sent[category] = _SendRecord(
            previous.sent_monotonic, previous.severity, False
        )

    @property
    def undelivered_categories(self) -> tuple[AlertCategory, ...]:
        """Categories with an unconfirmed send. Never an empty-means-healthy
        signal: a caller must treat a non-empty result as unresolved."""
        return tuple(
            sorted(
                (
                    category
                    for category, record in self._last_sent.items()
                    if not record.delivered
                ),
                key=lambda item: item.value,
            )
        )

    @property
    def all_delivered(self) -> bool:
        return not self.undelivered_categories

    @property
    def tracked_categories(self) -> int:
        return len(self._last_sent)


__all__ = [
    "ALLOWED_ALERT_FACTS",
    "ALWAYS_ESCALATING",
    "Alert",
    "AlertCategory",
    "AlertPolicy",
    "AlertSeverity",
    "HeartbeatAssessment",
    "HeartbeatStatus",
    "MonitoringError",
    "assess_heartbeat",
    "count_bucket",
    "validate_alert_facts",
]
