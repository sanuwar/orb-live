"""Per-producer signing keys — making producer separation real.

Every test is local and offline. **Every key here is a fixture.** No real key,
no key generation, no keyring, no network, no credential store.

The property that matters most: **compromising one producer yields that
producer's key and nothing else.** With the shared-key authority, a component
able to sign at all could sign in any producer's name; here it cannot.
"""

from __future__ import annotations

import ast
import dataclasses
import pathlib

import pytest

import src.producer_key_registry as registry_module
from src.attestation_authority import ProducerIdentity
from src.execution_gates import (
    REQUIRED_EVIDENCE,
    REQUIRED_PRECONDITIONS,
    Attestation,
    EvidenceSource,
    GatePreconditions,
    GateVerdict,
    open_gate,
)
from src.producer_key_registry import (
    BoundAttestation,
    ProducerKey,
    ProducerKeyError,
    ProducerKeyRegistry,
    RegisteredProducer,
    VerificationOutcome,
    VerificationResult,
)

MODULE_PATH = pathlib.Path(registry_module.__file__)

# Fixture keys only. Distinct per producer, and obviously not secrets.
RISK_KEY = b"fixture-key-for-the-risk-producer-0001!!!"
RISK_KEY_V2 = b"fixture-key-for-the-risk-producer-0002!!!"
SWITCH_KEY = b"fixture-key-for-the-kill-switch-probe-01!"
OPERATOR_KEY = b"fixture-key-for-the-operator-console-001!"

NOW = 1000.0
FRESH = NOW - 10.0

RISK_FACTS = frozenset({"risk_within_limits"})
SWITCH_FACTS = frozenset({"kill_switch_disengaged"})
OPERATOR_FACTS = frozenset({"strategy_enabled", "operator_present_and_acknowledged",
                            "protection_grounding_complete"})


def producer(producer_id, facts, sources, keys, enabled=True) -> RegisteredProducer:
    return RegisteredProducer(
        identity=ProducerIdentity(producer_id, facts, sources),
        keys=keys,
        enabled=enabled,
    )


def risk_producer(**overrides) -> RegisteredProducer:
    values = {
        "producer_id": "risk",
        "facts": RISK_FACTS,
        "sources": frozenset({EvidenceSource.MEASURED}),
        "keys": (ProducerKey("risk", "v1", RISK_KEY),),
    }
    values.update(overrides)
    return producer(**values)


def switch_producer(**overrides) -> RegisteredProducer:
    values = {
        "producer_id": "switch",
        "facts": SWITCH_FACTS,
        "sources": frozenset({EvidenceSource.MEASURED}),
        "keys": (ProducerKey("switch", "v1", SWITCH_KEY),),
    }
    values.update(overrides)
    return producer(**values)


def operator_producer(**overrides) -> RegisteredProducer:
    values = {
        "producer_id": "operator",
        "facts": OPERATOR_FACTS,
        "sources": frozenset({EvidenceSource.OPERATOR_ATTESTED}),
        "keys": (ProducerKey("operator", "v1", OPERATOR_KEY),),
    }
    values.update(overrides)
    return producer(**values)


def registry(**producers) -> ProducerKeyRegistry:
    entries = producers or {
        "risk": risk_producer(),
        "switch": switch_producer(),
        "operator": operator_producer(),
    }
    return ProducerKeyRegistry(entries)


def issue(reg, producer_id, fact, *, satisfied=True, source=None, at=FRESH,
          key_version=None):
    return reg.issue(
        producer_id=producer_id, fact_name=fact, satisfied=satisfied,
        source=source if source is not None
        else sorted(REQUIRED_EVIDENCE[fact], key=lambda s: s.value)[0],
        recorded_monotonic=at, key_version=key_version,
    )


# --------------------------------------------------------------------------
# 1. Separation is real, not conventional.
# --------------------------------------------------------------------------


def test_one_producers_key_cannot_sign_for_another():
    """The gap the shared-key authority left open.

    Signing as `switch` using the risk producer's key produces a signature
    that does not verify, because the two keys differ.
    """
    reg = registry()
    genuine = issue(reg, "switch", "kill_switch_disengaged")
    assert reg.verify(genuine) is True

    # Forge: same content and producer name, signed with the risk key.
    import hashlib
    import hmac

    from src.producer_key_registry import _canonical_bytes

    forged_signature = hmac.new(
        RISK_KEY,
        _canonical_bytes(
            "kill_switch_disengaged", genuine.attestation, "switch", "v1"
        ),
        hashlib.sha256,
    ).hexdigest()
    forged = BoundAttestation(
        "kill_switch_disengaged", genuine.attestation, "switch", "v1",
        forged_signature,
    )
    assert reg.verify(forged) is False
    assert reg.verify_detailed(forged).outcome is (
        VerificationOutcome.SIGNATURE_MISMATCH
    )


def test_producers_may_not_share_signing_material():
    """Sharing a key would recreate exactly the weakness this removes."""
    with pytest.raises(ProducerKeyError):
        ProducerKeyRegistry({
            "risk": risk_producer(),
            "switch": switch_producer(keys=(ProducerKey("switch", "v1", RISK_KEY),)),
        })


def test_a_producer_cannot_issue_a_fact_it_does_not_own():
    reg = registry()
    with pytest.raises(ProducerKeyError):
        issue(reg, "risk", "kill_switch_disengaged")


def test_a_producer_cannot_cite_a_source_it_may_not_use():
    reg = registry()
    with pytest.raises(ProducerKeyError):
        issue(reg, "risk", "risk_within_limits",
              source=EvidenceSource.OPERATOR_ATTESTED)


def test_an_unknown_producer_cannot_issue_or_verify():
    reg = registry()
    with pytest.raises(ProducerKeyError):
        issue(reg, "nobody", "risk_within_limits")
    genuine = issue(reg, "risk", "risk_within_limits")
    impostor = BoundAttestation(
        genuine.fact_name, genuine.attestation, "nobody", "v1", genuine.signature
    )
    assert reg.verify_detailed(impostor).outcome is (
        VerificationOutcome.UNKNOWN_PRODUCER
    )


def test_narrowing_a_producers_facts_invalidates_evidence_in_flight():
    reg = registry()
    signed = issue(reg, "operator", "strategy_enabled")
    assert reg.verify(signed) is True
    narrowed = ProducerKeyRegistry({
        "operator": operator_producer(facts=frozenset({"protection_grounding_complete"}))
    })
    assert narrowed.verify_detailed(signed).outcome is (
        VerificationOutcome.FACT_NOT_OWNED
    )


def test_narrowing_a_producers_sources_invalidates_evidence_in_flight():
    reg = registry()
    signed = issue(reg, "operator", "strategy_enabled")
    narrowed = ProducerKeyRegistry({
        "operator": operator_producer(sources=frozenset({EvidenceSource.TESTED}))
    })
    assert narrowed.verify_detailed(signed).outcome is (
        VerificationOutcome.SOURCE_NOT_PERMITTED
    )


# --------------------------------------------------------------------------
# 2. Key rotation.
# --------------------------------------------------------------------------


def rotating_registry() -> ProducerKeyRegistry:
    return ProducerKeyRegistry({
        "risk": risk_producer(keys=(
            ProducerKey("risk", "v1", RISK_KEY, retired=True),
            ProducerKey("risk", "v2", RISK_KEY_V2),
        ))
    })


def test_the_key_version_is_inside_the_signature():
    """Otherwise rotation would be cosmetic."""
    reg = registry()
    signed = issue(reg, "risk", "risk_within_limits")
    relabelled = BoundAttestation(
        signed.fact_name, signed.attestation, signed.producer_id, "v2",
        signed.signature,
    )
    assert reg.verify_detailed(relabelled).outcome is (
        VerificationOutcome.UNKNOWN_KEY_VERSION
    )


def test_evidence_under_a_retired_key_stops_verifying():
    """Rotation has to actually invalidate, or it is only paperwork."""
    before = registry()
    signed = issue(before, "risk", "risk_within_limits")
    assert before.verify(signed) is True

    after = rotating_registry()
    result = after.verify_detailed(signed)
    assert result.verified is False
    assert result.outcome is VerificationOutcome.RETIRED_KEY_VERSION


def test_issuing_uses_the_current_key_by_default():
    reg = rotating_registry()
    signed = issue(reg, "risk", "risk_within_limits")
    assert signed.key_version == "v2"
    assert reg.verify(signed) is True


def test_issuing_under_a_retired_version_is_refused():
    reg = rotating_registry()
    with pytest.raises(ProducerKeyError):
        issue(reg, "risk", "risk_within_limits", key_version="v1")


def test_issuing_under_an_unknown_version_is_refused():
    reg = registry()
    with pytest.raises(ProducerKeyError):
        issue(reg, "risk", "risk_within_limits", key_version="v99")


def test_a_producer_with_only_retired_keys_is_refused():
    """It could never issue; accepting it would look legitimate and not work."""
    with pytest.raises(ProducerKeyError):
        risk_producer(keys=(ProducerKey("risk", "v1", RISK_KEY, retired=True),))


def test_duplicate_key_versions_are_refused():
    with pytest.raises(ProducerKeyError):
        risk_producer(keys=(
            ProducerKey("risk", "v1", RISK_KEY),
            ProducerKey("risk", "v1", RISK_KEY_V2),
        ))


def test_a_key_belonging_to_another_producer_is_refused():
    with pytest.raises(ProducerKeyError):
        risk_producer(keys=(ProducerKey("switch", "v1", SWITCH_KEY),))


# --------------------------------------------------------------------------
# 3. Disabled producers.
# --------------------------------------------------------------------------


def test_a_disabled_producer_cannot_issue():
    reg = ProducerKeyRegistry({"risk": risk_producer(enabled=False)})
    with pytest.raises(ProducerKeyError):
        issue(reg, "risk", "risk_within_limits")


def test_disabling_a_producer_invalidates_its_outstanding_evidence():
    """Taken out of service means immediately, not at the next rotation."""
    live = registry()
    signed = issue(live, "risk", "risk_within_limits")
    assert live.verify(signed) is True

    disabled = ProducerKeyRegistry({"risk": risk_producer(enabled=False)})
    result = disabled.verify_detailed(signed)
    assert result.verified is False
    assert result.outcome is VerificationOutcome.PRODUCER_DISABLED


def test_disabling_does_not_require_deleting_the_producer():
    reg = ProducerKeyRegistry({"risk": risk_producer(enabled=False)})
    assert "risk" in reg.producer_ids
    assert reg.is_enabled("risk") is False


def test_enablement_is_reported_for_unknown_producers_as_false():
    assert registry().is_enabled("nobody") is False


@pytest.mark.parametrize("value", [1, 0, "yes", None])
def test_a_non_bool_enabled_flag_is_refused(value):
    with pytest.raises(ProducerKeyError):
        risk_producer(enabled=value)


# --------------------------------------------------------------------------
# 4. Tampering.
# --------------------------------------------------------------------------


def test_altering_any_signed_field_invalidates_the_signature():
    reg = registry()
    signed = issue(reg, "risk", "risk_within_limits", satisfied=True, at=FRESH)
    mutations = [
        BoundAttestation(signed.fact_name,
                         Attestation(False, signed.attestation.source, FRESH),
                         signed.producer_id, signed.key_version, signed.signature),
        BoundAttestation(signed.fact_name,
                         Attestation(True, signed.attestation.source, NOW),
                         signed.producer_id, signed.key_version, signed.signature),
        BoundAttestation(signed.fact_name, signed.attestation,
                         signed.producer_id, signed.key_version, "0" * 64),
    ]
    for mutated in mutations:
        assert reg.verify(mutated) is False


def test_a_signature_cannot_be_moved_to_another_fact():
    reg = ProducerKeyRegistry({
        "operator": operator_producer()
    })
    signed = issue(reg, "operator", "strategy_enabled")
    moved = BoundAttestation(
        "protection_grounding_complete", signed.attestation, signed.producer_id,
        signed.key_version, signed.signature,
    )
    assert reg.verify_detailed(moved).outcome is (
        VerificationOutcome.SIGNATURE_MISMATCH
    )


@pytest.mark.parametrize(
    "value", [None, "signed", 42, object(),
              Attestation(True, EvidenceSource.MEASURED, FRESH)]
)
def test_verifying_a_non_bound_attestation_is_malformed_not_an_error(value):
    result = registry().verify_detailed(value)
    assert result.outcome is VerificationOutcome.MALFORMED
    assert result.verified is False


@pytest.mark.parametrize("field,value", [
    ("fact_name", ""), ("fact_name", None),
    ("producer_id", "  "), ("producer_id", 7),
    ("key_version", ""), ("key_version", None),
    ("signature", ""), ("signature", 3),
])
def test_a_malformed_bound_attestation_is_refused(field, value):
    good = issue(registry(), "risk", "risk_within_limits")
    values = {
        "fact_name": good.fact_name, "attestation": good.attestation,
        "producer_id": good.producer_id, "key_version": good.key_version,
        "signature": good.signature,
    }
    values[field] = value
    with pytest.raises(ProducerKeyError):
        BoundAttestation(**values)


def test_a_non_attestation_payload_is_refused():
    good = issue(registry(), "risk", "risk_within_limits")
    with pytest.raises(ProducerKeyError):
        BoundAttestation(good.fact_name, "yes", good.producer_id,
                         good.key_version, good.signature)


# --------------------------------------------------------------------------
# 5. Key material never escapes.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("key", [b"", b"short", b"x" * 31])
def test_a_short_key_is_refused(key):
    with pytest.raises(ProducerKeyError):
        ProducerKey("risk", "v1", key)


@pytest.mark.parametrize("key", ["a-string-key-long-enough-to-pass-length!!", None, 42])
def test_a_non_bytes_key_is_refused(key):
    with pytest.raises(ProducerKeyError):
        ProducerKey("risk", "v1", key)


def test_a_key_is_never_rendered():
    key = ProducerKey("risk", "v1", RISK_KEY)
    assert RISK_KEY.decode() not in repr(key)
    assert "signing_key" not in repr(key)


def test_the_registry_never_renders_keys_or_producer_names():
    reg = registry()
    rendered = repr(reg)
    assert RISK_KEY.decode() not in rendered
    assert SWITCH_KEY.decode() not in rendered
    assert not hasattr(reg, "__dict__")


def test_no_error_message_carries_key_material():
    reg = registry()
    for call in (
        lambda: issue(reg, "nobody", "risk_within_limits"),
        lambda: issue(reg, "risk", "kill_switch_disengaged"),
        lambda: issue(reg, "risk", "risk_within_limits", key_version="v99"),
    ):
        try:
            call()
        except ProducerKeyError as error:
            for key in (RISK_KEY, SWITCH_KEY, OPERATOR_KEY):
                assert key.decode() not in str(error)


def test_the_module_generates_no_keys_and_reads_no_store():
    """Structural, not textual.

    A substring scan flags the docstring's own "no OS keyring" disclaimer,
    which is how a guard earns a reputation for crying wolf and then gets
    deleted. Imports and call names are what actually matter.
    """
    tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
    imported = _imports(MODULE_PATH)
    assert imported.isdisjoint(
        {"secrets", "keyring", "os", "pathlib", "random", "uuid", "ssl"}
    )
    called = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = (
                func.attr if isinstance(func, ast.Attribute)
                else func.id if isinstance(func, ast.Name)
                else None
            )
            if name:
                called.add(name)
    for forbidden in ("open", "urandom", "token_bytes", "getenv", "urlopen",
                      "read_text", "read_bytes", "connect"):
        assert forbidden not in called, forbidden


# --------------------------------------------------------------------------
# 6. Privacy-safe verification results.
# --------------------------------------------------------------------------

ALLOWED_FACT_VALUES = {
    "verification_outcome": {o.value for o in VerificationOutcome},
    "verified": {"true", "false"},
}


def test_verification_facts_are_allowlisted():
    for outcome in VerificationOutcome:
        facts = VerificationResult(outcome).sanitized_facts()
        assert set(facts) == set(ALLOWED_FACT_VALUES)
        for key, value in facts.items():
            assert value in ALLOWED_FACT_VALUES[key], (key, value)


def test_a_verification_result_names_no_producer_fact_or_key():
    reg = registry()
    signed = issue(reg, "risk", "risk_within_limits")
    rendered = repr(reg.verify_detailed(signed).sanitized_facts())
    for forbidden in ("risk", "v1", "risk_within_limits", RISK_KEY.decode()):
        assert forbidden not in rendered, forbidden


def test_the_result_has_no_field_that_could_carry_one():
    names = {f.name for f in dataclasses.fields(VerificationResult)}
    assert names == {"outcome"}


def test_facts_survive_the_commissioning_privacy_sanitizer():
    from src.one_share_commissioning import validate_sanitized_evidence

    for outcome in VerificationOutcome:
        validate_sanitized_evidence(VerificationResult(outcome).sanitized_facts())


def test_every_failure_mode_has_a_distinct_reason():
    """A bare False tells an operator nothing at three in the morning."""
    assert len(list(VerificationOutcome)) == 9
    assert len({o.value for o in VerificationOutcome}) == 9


# --------------------------------------------------------------------------
# 7. Gate integration.
# --------------------------------------------------------------------------


def full_registry() -> ProducerKeyRegistry:
    """One producer per fact, each with its own key — the intended shape."""
    keys = {}
    entries = {}
    for index, fact in enumerate(sorted(REQUIRED_EVIDENCE)):
        producer_id = f"producer-{index:02d}"
        key = (f"fixture-key-{index:02d}-".encode() + b"x" * 40)[:48]
        keys[fact] = producer_id
        entries[producer_id] = RegisteredProducer(
            identity=ProducerIdentity(
                producer_id, frozenset({fact}), REQUIRED_EVIDENCE[fact]
            ),
            keys=(ProducerKey(producer_id, "v1", key),),
        )
    return ProducerKeyRegistry(entries), keys


def test_the_gate_accepts_bound_attestations_and_stays_closed():
    reg, owner = full_registry()
    values = {
        name: issue(reg, owner[name], name) for name in REQUIRED_PRECONDITIONS
    }
    record = GatePreconditions(**values)
    assert record.unmet(current_monotonic=NOW, verifier=reg) == ()
    decision = open_gate(record, current_monotonic=NOW, verifier=reg)
    assert decision.verdict is GateVerdict.CLOSED_ACTIVATION_NOT_APPROVED
    assert decision.is_open is False
    assert decision.evidence_verified is True


def test_evidence_from_the_wrong_producer_fails_its_precondition():
    reg, owner = full_registry()
    values = {
        name: issue(reg, owner[name], name) for name in REQUIRED_PRECONDITIONS
    }
    good = values["risk_within_limits"]
    impersonated = BoundAttestation(
        "risk_within_limits", good.attestation,
        owner["kill_switch_disengaged"], "v1", good.signature,
    )
    values["risk_within_limits"] = impersonated
    record = GatePreconditions(**values)
    assert record.unmet(current_monotonic=NOW, verifier=reg) == (
        "risk_within_limits",
    )


def test_a_disabled_producer_fails_only_its_own_precondition():
    reg, owner = full_registry()
    values = {
        name: issue(reg, owner[name], name) for name in REQUIRED_PRECONDITIONS
    }
    record = GatePreconditions(**values)
    assert record.unmet(current_monotonic=NOW, verifier=reg) == ()
    # Same registry with one producer disabled.
    entries = {}
    for index, fact in enumerate(sorted(REQUIRED_EVIDENCE)):
        producer_id = f"producer-{index:02d}"
        key = (f"fixture-key-{index:02d}-".encode() + b"x" * 40)[:48]
        entries[producer_id] = RegisteredProducer(
            identity=ProducerIdentity(
                producer_id, frozenset({fact}), REQUIRED_EVIDENCE[fact]
            ),
            keys=(ProducerKey(producer_id, "v1", key),),
            enabled=fact != "risk_within_limits",
        )
    degraded = ProducerKeyRegistry(entries)
    assert record.unmet(current_monotonic=NOW, verifier=degraded) == (
        "risk_within_limits",
    )


def test_stale_bound_evidence_still_fails():
    """Signing proves origin, not freshness."""
    reg, owner = full_registry()
    values = {
        name: issue(reg, owner[name], name) for name in REQUIRED_PRECONDITIONS
    }
    values["strategy_enabled"] = issue(
        reg, owner["strategy_enabled"], "strategy_enabled", at=NOW - 100_000.0
    )
    record = GatePreconditions(**values)
    assert record.unmet(current_monotonic=NOW, verifier=reg) == ("strategy_enabled",)


# --------------------------------------------------------------------------
# 8. Independence.
# --------------------------------------------------------------------------


def _imports(path: pathlib.Path) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def test_the_registry_imports_no_pinned_checkpoint_file():
    for pinned in ("read_only_observation_runtime", "read_only_broker_state",
                   "read_only_authorization_manifest", "live_order_state_model"):
        assert not any(pinned in name for name in _imports(MODULE_PATH)), pinned


def test_the_registry_performs_no_io_and_no_network():
    imported = _imports(MODULE_PATH)
    assert imported.isdisjoint(
        {"socket", "http", "urllib", "requests", "os", "pathlib", "subprocess",
         "secrets", "ssl", "cryptography"}
    )


def test_the_registry_reads_no_wall_clock():
    source = MODULE_PATH.read_text(encoding="utf-8")
    assert "datetime.now(" not in source
    assert "time.time(" not in source


def test_comparison_is_constant_time():
    source = MODULE_PATH.read_text(encoding="utf-8")
    assert "hmac.compare_digest" in source
    assert "== signed.signature" not in source


def test_the_module_states_what_it_still_does_not_do():
    source = MODULE_PATH.read_text(encoding="utf-8")
    assert "does not do" in source.lower()
    assert "key custody remains the boundary" in source.lower()


def test_no_broker_manifest_or_checkpoint_path_exists():
    source = MODULE_PATH.read_text(encoding="utf-8").lower()
    for token in ("robinhood", "call_tool", "place_equity", "cancel_equity",
                  "manifest", "checkpoint"):
        assert token not in source, token
