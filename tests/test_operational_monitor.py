"""Privacy-safe monitoring and alerts.

Every test is local and offline.

The property that matters most: **an alert cannot carry free-form text.**
Alerts leave the system, so a leak here reaches a phone or an inbox and
nobody notices.
"""

from __future__ import annotations

import ast
import dataclasses
import pathlib

import pytest

import src.operational_monitor as monitor
from src.operational_monitor import (
    ALLOWED_ALERT_FACTS,
    ALWAYS_ESCALATING,
    Alert,
    AlertCategory,
    AlertPolicy,
    AlertSeverity,
    HeartbeatStatus,
    MonitoringError,
    assess_heartbeat,
    count_bucket,
    validate_alert_facts,
)

MODULE_PATH = pathlib.Path(monitor.__file__)

PINNED_MODULES = (
    "read_only_observation_runtime",
    "read_only_broker_state",
    "read_only_authorization_manifest",
    "live_order_state_model",
)

GOOD_FACTS = {"component": "risk", "condition": "breached"}


# --------------------------------------------------------------------------
# 1. Free text is unrepresentable.
# --------------------------------------------------------------------------


def test_an_alert_has_no_field_that_could_hold_free_text():
    """Structural: there is nothing to fill in, not merely nothing filled in."""
    names = {f.name for f in dataclasses.fields(Alert)}
    assert names == {"category", "severity", "facts"}
    for forbidden in ("message", "detail", "text", "body", "description", "note"):
        assert not any(forbidden in name for name in names), forbidden


def test_an_unallowlisted_fact_key_is_refused():
    with pytest.raises(MonitoringError):
        Alert(AlertCategory.BROKER_DATA_STALE, AlertSeverity.WARNING,
              {"message": "account 12345 is stale"})


def test_an_unallowlisted_fact_value_is_refused():
    with pytest.raises(MonitoringError):
        Alert(AlertCategory.BROKER_DATA_STALE, AlertSeverity.WARNING,
              {"component": "ACCT-12345"})


@pytest.mark.parametrize("value", [None, 7, 1.5, True, ["stale"], {"a": 1}])
def test_a_non_string_fact_value_is_refused(value):
    with pytest.raises(MonitoringError):
        validate_alert_facts({"condition": value})


def test_non_mapping_facts_are_refused():
    with pytest.raises(MonitoringError):
        validate_alert_facts([("component", "risk")])


def test_every_allowlisted_key_and_value_is_accepted():
    for key, values in ALLOWED_ALERT_FACTS.items():
        for value in values:
            validate_alert_facts({key: value})


def test_the_allowlist_carries_no_identifier_shaped_key():
    for key in ALLOWED_ALERT_FACTS:
        for forbidden in ("id", "account", "symbol", "price", "quantity",
                          "amount", "order", "token"):
            assert forbidden not in key.lower(), key


def test_rendering_never_interpolates_unvalidated_input():
    alert = Alert(AlertCategory.RISK_LIMIT_BREACHED, AlertSeverity.CRITICAL, GOOD_FACTS)
    rendered = alert.render()
    assert rendered == "[CRITICAL] RISK_LIMIT_BREACHED component=risk condition=breached"
    for token in rendered.replace("[", " ").replace("]", " ").split():
        assert token != ""


def test_a_rendered_alert_contains_only_enum_and_allowlisted_tokens():
    permitted = (
        {c.value for c in AlertCategory}
        | {s.value for s in AlertSeverity}
        | {v for values in ALLOWED_ALERT_FACTS.values() for v in values}
        | set(ALLOWED_ALERT_FACTS)
    )
    for category in AlertCategory:
        severity = (
            AlertSeverity.CRITICAL if category in ALWAYS_ESCALATING
            else AlertSeverity.INFO
        )
        rendered = Alert(category, severity, GOOD_FACTS).render()
        tokens = rendered.replace("[", "").replace("]", "").replace("=", " ").split()
        for token in tokens:
            assert token in permitted, token


def test_facts_are_frozen_after_construction():
    facts = dict(GOOD_FACTS)
    alert = Alert(AlertCategory.RISK_LIMIT_BREACHED, AlertSeverity.WARNING, facts)
    facts["component"] = "runtime"          # mutate the caller's dict
    assert alert.facts["component"] == "risk"
    with pytest.raises(TypeError):
        alert.facts["component"] = "runtime"


# --------------------------------------------------------------------------
# 2. Severity discipline.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("category", sorted(ALWAYS_ESCALATING, key=str))
def test_an_escalating_category_may_not_be_raised_as_info(category):
    """Downgrading is how a critical condition gets filtered out."""
    with pytest.raises(MonitoringError):
        Alert(category, AlertSeverity.INFO, GOOD_FACTS)


@pytest.mark.parametrize("category", sorted(ALWAYS_ESCALATING, key=str))
def test_an_escalating_category_always_requires_an_operator(category):
    alert = Alert(category, AlertSeverity.WARNING, GOOD_FACTS)
    assert alert.requires_operator is True


def test_critical_always_requires_an_operator():
    alert = Alert(AlertCategory.BROKER_DATA_STALE, AlertSeverity.CRITICAL, GOOD_FACTS)
    assert alert.requires_operator is True


def test_an_ordinary_info_alert_does_not_require_an_operator():
    alert = Alert(AlertCategory.HEARTBEAT_RECOVERED, AlertSeverity.INFO, GOOD_FACTS)
    assert alert.requires_operator is False


@pytest.mark.parametrize("bad", ["CRITICAL", None, 2])
def test_an_invalid_severity_or_category_is_refused(bad):
    with pytest.raises(MonitoringError):
        Alert(AlertCategory.BROKER_DATA_STALE, bad, GOOD_FACTS)
    with pytest.raises(MonitoringError):
        Alert(bad, AlertSeverity.INFO, GOOD_FACTS)


def test_the_category_set_is_closed():
    assert len(list(AlertCategory)) == 8
    assert len(ALWAYS_ESCALATING) == 3


# --------------------------------------------------------------------------
# 3. Heartbeats.
# --------------------------------------------------------------------------


def test_a_recent_beat_is_healthy():
    result = assess_heartbeat(100.0, current_monotonic=105.0, interval_seconds=10.0)
    assert result.status is HeartbeatStatus.HEALTHY
    assert result.is_healthy is True


def test_no_beat_ever_recorded_is_not_healthy():
    """An unstarted monitor must not read as fine."""
    result = assess_heartbeat(None, current_monotonic=100.0, interval_seconds=10.0)
    assert result.status is HeartbeatStatus.NEVER_STARTED
    assert result.is_healthy is False


def test_enough_missed_beats_is_missing():
    result = assess_heartbeat(100.0, current_monotonic=125.0, interval_seconds=10.0)
    assert result.status is HeartbeatStatus.MISSING
    assert result.consecutive_misses == 2


def test_one_missed_beat_is_tolerated_by_default():
    result = assess_heartbeat(100.0, current_monotonic=115.0, interval_seconds=10.0)
    assert result.status is HeartbeatStatus.HEALTHY
    assert result.consecutive_misses == 1


def test_the_tolerance_is_configurable():
    result = assess_heartbeat(
        100.0, current_monotonic=115.0, interval_seconds=10.0,
        missed_beats_before_alert=1,
    )
    assert result.status is HeartbeatStatus.MISSING


def test_a_beat_from_the_future_is_missing_not_healthy():
    """A clock or a record is wrong; neither justifies calling it healthy."""
    result = assess_heartbeat(500.0, current_monotonic=100.0, interval_seconds=10.0)
    assert result.status is HeartbeatStatus.MISSING


@pytest.mark.parametrize("interval", [0, -1])
def test_a_nonsensical_interval_is_refused(interval):
    with pytest.raises(MonitoringError):
        assess_heartbeat(100.0, current_monotonic=101.0, interval_seconds=interval)


def test_alerting_on_zero_missed_beats_is_refused():
    with pytest.raises(MonitoringError):
        assess_heartbeat(100.0, current_monotonic=101.0, interval_seconds=10.0,
                         missed_beats_before_alert=0)


@pytest.mark.parametrize("value", ["100", True, [100]])
def test_a_non_numeric_beat_time_is_refused(value):
    with pytest.raises(MonitoringError):
        assess_heartbeat(value, current_monotonic=101.0, interval_seconds=10.0)


# --------------------------------------------------------------------------
# 4. Flap suppression.
# --------------------------------------------------------------------------


def warning(category=AlertCategory.BROKER_DATA_STALE):
    return Alert(category, AlertSeverity.WARNING, GOOD_FACTS)


def critical(category=AlertCategory.BROKER_DATA_STALE):
    return Alert(category, AlertSeverity.CRITICAL, GOOD_FACTS)


def test_the_first_alert_of_a_category_is_sent():
    assert AlertPolicy().should_send(warning(), current_monotonic=0.0) is True


def test_a_repeat_within_the_cooldown_is_suppressed():
    policy = AlertPolicy(cooldown_seconds=300.0)
    assert policy.should_send(warning(), current_monotonic=0.0) is True
    policy.confirm_delivery(AlertCategory.BROKER_DATA_STALE)
    assert policy.should_send(warning(), current_monotonic=10.0) is False
    assert policy.should_send(warning(), current_monotonic=299.9) is False


def test_a_repeat_after_the_cooldown_is_sent():
    policy = AlertPolicy(cooldown_seconds=300.0)
    policy.should_send(warning(), current_monotonic=0.0)
    policy.confirm_delivery(AlertCategory.BROKER_DATA_STALE)
    assert policy.should_send(warning(), current_monotonic=300.0) is True


def test_an_escalation_is_never_suppressed():
    """A warning becoming critical is new information, however recent."""
    policy = AlertPolicy(cooldown_seconds=3600.0)
    assert policy.should_send(warning(), current_monotonic=0.0) is True
    policy.confirm_delivery(AlertCategory.BROKER_DATA_STALE)
    assert policy.should_send(critical(), current_monotonic=1.0) is True


def test_a_de_escalation_is_suppressed_like_any_repeat():
    policy = AlertPolicy(cooldown_seconds=3600.0)
    policy.should_send(critical(), current_monotonic=0.0)
    policy.confirm_delivery(AlertCategory.BROKER_DATA_STALE)
    assert policy.should_send(warning(), current_monotonic=1.0) is False


def test_a_second_escalation_to_the_same_severity_is_suppressed():
    policy = AlertPolicy(cooldown_seconds=3600.0)
    policy.should_send(warning(), current_monotonic=0.0)
    policy.confirm_delivery(AlertCategory.BROKER_DATA_STALE)
    assert policy.should_send(critical(), current_monotonic=1.0) is True
    policy.confirm_delivery(AlertCategory.BROKER_DATA_STALE)
    assert policy.should_send(critical(), current_monotonic=2.0) is False


def test_categories_are_suppressed_independently():
    policy = AlertPolicy(cooldown_seconds=3600.0)
    assert policy.should_send(warning(AlertCategory.BROKER_DATA_STALE), current_monotonic=0.0) is True
    assert policy.should_send(warning(AlertCategory.RECOVERY_INCONSISTENT), current_monotonic=1.0) is True
    assert policy.tracked_categories == 2


def test_suppression_is_keyed_by_category_not_by_facts():
    """Flapping facts must not defeat suppression."""
    policy = AlertPolicy(cooldown_seconds=3600.0)
    first = Alert(AlertCategory.BROKER_DATA_STALE, AlertSeverity.WARNING,
                  {"component": "risk", "count_bucket": "one"})
    second = Alert(AlertCategory.BROKER_DATA_STALE, AlertSeverity.WARNING,
                   {"component": "risk", "count_bucket": "many"})
    assert policy.should_send(first, current_monotonic=0.0) is True
    policy.confirm_delivery(AlertCategory.BROKER_DATA_STALE)
    assert policy.should_send(second, current_monotonic=1.0) is False


def test_a_negative_cooldown_is_refused():
    with pytest.raises(MonitoringError):
        AlertPolicy(cooldown_seconds=-1.0)


def test_a_zero_cooldown_sends_everything():
    policy = AlertPolicy(cooldown_seconds=0.0)
    assert policy.should_send(warning(), current_monotonic=0.0) is True
    assert policy.should_send(warning(), current_monotonic=0.0) is True


def test_the_policy_rejects_a_non_alert():
    with pytest.raises(MonitoringError):
        AlertPolicy().should_send("stale", current_monotonic=0.0)


# --------------------------------------------------------------------------
# 5. Count buckets.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value,expected", [(-5, "none"), (0, "none"), (1, "one"), (2, "many"), (99, "many")]
)
def test_count_buckets(value, expected):
    assert count_bucket(value) == expected


@pytest.mark.parametrize("value", [1.5, True, "1", None])
def test_a_non_integer_count_is_refused(value):
    with pytest.raises(MonitoringError):
        count_bucket(value)


def test_bucket_values_are_all_allowlisted():
    for value in (0, 1, 5):
        assert count_bucket(value) in ALLOWED_ALERT_FACTS["count_bucket"]


# --------------------------------------------------------------------------
# 6. Independence.
# --------------------------------------------------------------------------


def _imports(path: pathlib.Path) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def test_the_monitor_imports_no_pinned_checkpoint_file():
    imported = _imports(MODULE_PATH)
    for pinned in PINNED_MODULES:
        assert not any(pinned in name for name in imported), pinned


def test_the_monitor_imports_nothing_broker_shaped():
    forbidden = ("adapter", "client", "session", "mcp", "orchestrator",
                 "commissioning", "manifest", "checkpoint", "strategy", "broker")
    for name in _imports(MODULE_PATH):
        assert not any(word in name.lower() for word in forbidden), name


def test_the_monitor_sends_nothing_itself():
    """It decides what an alert says. Delivery is somebody else's module.

    A module that both formats and transmits is one convenience function away
    from posting an unvalidated string to a webhook.
    """
    imported = _imports(MODULE_PATH)
    assert imported.isdisjoint(
        {"socket", "http", "urllib", "requests", "httpx", "aiohttp",
         "smtplib", "subprocess", "os", "pathlib", "logging"}
    )
    source = MODULE_PATH.read_text(encoding="utf-8")
    for token in ("urlopen", "http://", "https://", "webhook", "sendmail"):
        assert token not in source


def test_the_monitor_reads_no_wall_clock():
    source = MODULE_PATH.read_text(encoding="utf-8")
    assert "datetime.now(" not in source
    assert "time.time(" not in source
    assert "time.monotonic(" not in source


def test_the_monitor_performs_no_io():
    for node in ast.walk(ast.parse(MODULE_PATH.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Call):
            func = node.func
            name = (
                func.attr if isinstance(func, ast.Attribute)
                else func.id if isinstance(func, ast.Name)
                else None
            )
            assert name not in {"open", "print", "urlopen", "write_text"}


def test_alert_facts_survive_the_commissioning_privacy_sanitizer():
    from src.one_share_commissioning import validate_sanitized_evidence

    for key, values in ALLOWED_ALERT_FACTS.items():
        for value in values:
            validate_sanitized_evidence({key: value})


# --------------------------------------------------------------------------
# 7. Delivery confirmation (review finding).
# --------------------------------------------------------------------------


def test_an_unconfirmed_send_is_always_resent():
    """Review finding: a critical whose delivery failed used to go silent.

    Suppression is only safe if the thing being suppressed actually arrived.
    """
    policy = AlertPolicy(cooldown_seconds=3600.0)
    alert = Alert(AlertCategory.KILL_SWITCH_ENGAGED, AlertSeverity.CRITICAL, GOOD_FACTS)
    assert policy.should_send(alert, current_monotonic=0.0) is True
    # Delivery failed; nobody confirms.
    assert policy.should_send(alert, current_monotonic=1.0) is True
    assert policy.should_send(alert, current_monotonic=2.0) is True


def test_an_explicit_delivery_failure_also_forces_a_resend():
    policy = AlertPolicy(cooldown_seconds=3600.0)
    policy.should_send(warning(), current_monotonic=0.0)
    policy.confirm_delivery(AlertCategory.BROKER_DATA_STALE)
    assert policy.should_send(warning(), current_monotonic=1.0) is False
    policy.record_delivery_failure(AlertCategory.BROKER_DATA_STALE)
    assert policy.should_send(warning(), current_monotonic=2.0) is True


def test_silence_is_never_evidence_of_health():
    """A fresh send is undelivered until confirmed, and says so."""
    policy = AlertPolicy(cooldown_seconds=3600.0)
    assert policy.all_delivered is True          # nothing sent yet
    policy.should_send(warning(), current_monotonic=0.0)
    assert policy.all_delivered is False
    assert policy.undelivered_categories == (AlertCategory.BROKER_DATA_STALE,)
    policy.confirm_delivery(AlertCategory.BROKER_DATA_STALE)
    assert policy.all_delivered is True
    assert policy.undelivered_categories == ()


def test_undelivered_categories_are_reported_sorted_and_complete():
    policy = AlertPolicy(cooldown_seconds=3600.0)
    policy.should_send(warning(AlertCategory.RECOVERY_INCONSISTENT), current_monotonic=0.0)
    policy.should_send(warning(AlertCategory.BROKER_DATA_STALE), current_monotonic=0.0)
    assert policy.undelivered_categories == (
        AlertCategory.BROKER_DATA_STALE,
        AlertCategory.RECOVERY_INCONSISTENT,
    )


def test_a_resend_after_failure_is_itself_unconfirmed():
    """The retry must not be assumed to have worked either."""
    policy = AlertPolicy(cooldown_seconds=3600.0)
    policy.should_send(warning(), current_monotonic=0.0)
    policy.should_send(warning(), current_monotonic=1.0)
    assert policy.all_delivered is False


def test_confirming_a_category_that_was_never_sent_is_refused():
    policy = AlertPolicy()
    with pytest.raises(MonitoringError):
        policy.confirm_delivery(AlertCategory.BROKER_DATA_STALE)
    with pytest.raises(MonitoringError):
        policy.record_delivery_failure(AlertCategory.BROKER_DATA_STALE)


@pytest.mark.parametrize("bad", ["BROKER_DATA_STALE", None, 3])
def test_delivery_bookkeeping_refuses_a_non_category(bad):
    policy = AlertPolicy()
    with pytest.raises(MonitoringError):
        policy.confirm_delivery(bad)
    with pytest.raises(MonitoringError):
        policy.record_delivery_failure(bad)


def test_confirmation_is_per_category():
    policy = AlertPolicy(cooldown_seconds=3600.0)
    policy.should_send(warning(AlertCategory.BROKER_DATA_STALE), current_monotonic=0.0)
    policy.should_send(warning(AlertCategory.RECOVERY_INCONSISTENT), current_monotonic=0.0)
    policy.confirm_delivery(AlertCategory.BROKER_DATA_STALE)
    assert policy.should_send(
        warning(AlertCategory.BROKER_DATA_STALE), current_monotonic=1.0
    ) is False
    assert policy.should_send(
        warning(AlertCategory.RECOVERY_INCONSISTENT), current_monotonic=1.0
    ) is True


def test_no_alert_carries_a_permission_field():
    """Severity conveys urgency, never authority."""
    names = {f.name for f in dataclasses.fields(Alert)}
    assert not any("permit" in n or "author" in n or "allow" in n for n in names)
    for category in AlertCategory:
        severity = (
            AlertSeverity.CRITICAL if category in ALWAYS_ESCALATING
            else AlertSeverity.INFO
        )
        alert = Alert(category, severity, GOOD_FACTS)
        assert not hasattr(alert, "permits_automated_action")
        assert not hasattr(alert, "authorizes")


def test_monitoring_never_reads_credentials_or_production_data():
    source = MODULE_PATH.read_text(encoding="utf-8")
    for token in ("local.yaml", "data/", ".rh_tokens", "token", "credential",
                  "password", "secret", "environ", "getenv"):
        assert token not in source, token
