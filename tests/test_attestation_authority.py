"""Producer-bound attestations — evidence a caller cannot mint for itself.

Every test is local and offline. No key here is a real key.

The property that matters most: **a caller without the signing key cannot
produce evidence a gate will accept**, cannot alter issued evidence, and
cannot move a valid signature onto a different fact or a fresher timestamp.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

import src.attestation_authority as authority_module
from src.attestation_authority import (
    MINIMUM_KEY_BYTES,
    AttestationAuthority,
    AttestationAuthorityError,
    ProducerIdentity,
    SignedAttestation,
)
from src.execution_gates import (
    REQUIRED_EVIDENCE,
    REQUIRED_PRECONDITIONS,
    UNATTENDED_PRECONDITIONS,
    Attestation,
    EvidenceSource,
    GatePreconditions,
    GateVerdict,
    UnattendedPreconditions,
    open_gate,
    open_unattended_gate,
    verified_or_none,
)

MODULE_PATH = pathlib.Path(authority_module.__file__)

KEY = b"a-test-signing-key-that-is-long-enough!!"
OTHER_KEY = b"a-different-key-also-long-enough-here!!!"
NOW = 1000.0
FRESH = NOW - 10.0

ALL_FACTS = frozenset(REQUIRED_EVIDENCE)
ALL_SOURCES = frozenset(
    {EvidenceSource.OPERATOR_ATTESTED, EvidenceSource.MEASURED, EvidenceSource.TESTED}
)


def accepted_source(fact: str) -> EvidenceSource:
    return sorted(REQUIRED_EVIDENCE[fact], key=lambda s: s.value)[0]


def omnipotent() -> AttestationAuthority:
    """One producer allowed everything — used where the restriction is not
    what is under test."""
    return AttestationAuthority(
        KEY, {"all": ProducerIdentity("all", ALL_FACTS, ALL_SOURCES)}
    )


def issue(auth: AttestationAuthority, fact: str, *, satisfied=True,
          source=None, at=FRESH, producer="all") -> SignedAttestation:
    return auth.issue(
        producer_id=producer,
        fact_name=fact,
        satisfied=satisfied,
        source=source if source is not None else accepted_source(fact),
        recorded_monotonic=at,
    )


def signed_supervised(auth, **overrides):
    values = {name: issue(auth, name) for name in REQUIRED_PRECONDITIONS}
    values.update(overrides)
    return GatePreconditions(**values)


def signed_unattended(auth, **overrides):
    values = {name: issue(auth, name) for name in UNATTENDED_PRECONDITIONS}
    values.update(overrides)
    return UnattendedPreconditions(**values)


# --------------------------------------------------------------------------
# 1. Issuance and verification.
# --------------------------------------------------------------------------


def test_an_issued_attestation_verifies():
    auth = omnipotent()
    assert auth.verify(issue(auth, "strategy_enabled")) is True


@pytest.mark.parametrize("fact", sorted(REQUIRED_EVIDENCE))
def test_every_fact_can_be_issued_and_verified(fact):
    auth = omnipotent()
    signed = issue(auth, fact)
    assert auth.verify(signed) is True
    assert signed.fact_name == fact


def test_the_underlying_attestation_is_returned_only_after_verification():
    auth = omnipotent()
    signed = issue(auth, "strategy_enabled")
    assert auth.verified_attestation(signed).satisfied is True
    forged = SignedAttestation(
        signed.fact_name, signed.attestation, signed.producer_id, "0" * 64
    )
    with pytest.raises(AttestationAuthorityError):
        auth.verified_attestation(forged)


def test_a_false_attestation_is_still_signable():
    """Signing says who made the claim, not that the claim is good news."""
    auth = omnipotent()
    signed = issue(auth, "kill_switch_disengaged", satisfied=False)
    assert auth.verify(signed) is True
    assert signed.attestation.satisfied is False


# --------------------------------------------------------------------------
# 2. Forgery and tampering.
# --------------------------------------------------------------------------


def test_a_fabricated_signature_is_refused():
    auth = omnipotent()
    signed = issue(auth, "strategy_enabled")
    for fake in ("0" * 64, "", "deadbeef", signed.signature.upper(),
                 signed.signature[:-1] + "0"):
        if not fake:
            with pytest.raises(AttestationAuthorityError):
                SignedAttestation(signed.fact_name, signed.attestation,
                                  signed.producer_id, fake)
            continue
        forged = SignedAttestation(
            signed.fact_name, signed.attestation, signed.producer_id, fake
        )
        assert auth.verify(forged) is False, fake


def test_altering_the_satisfied_flag_invalidates_the_signature():
    auth = omnipotent()
    signed = issue(auth, "kill_switch_disengaged", satisfied=False)
    flipped = SignedAttestation(
        signed.fact_name,
        Attestation(True, signed.attestation.source,
                    signed.attestation.recorded_monotonic),
        signed.producer_id,
        signed.signature,
    )
    assert auth.verify(flipped) is False


def test_refreshing_the_timestamp_invalidates_the_signature():
    """The exact replay the review was worried about: stamp it `now`."""
    auth = omnipotent()
    signed = issue(auth, "strategy_enabled", at=FRESH)
    refreshed = SignedAttestation(
        signed.fact_name,
        Attestation(True, signed.attestation.source, NOW),
        signed.producer_id,
        signed.signature,
    )
    assert auth.verify(refreshed) is False


def test_upgrading_the_evidence_source_invalidates_the_signature():
    auth = omnipotent()
    signed = issue(auth, "monitoring_healthy", source=EvidenceSource.MEASURED)
    upgraded = SignedAttestation(
        signed.fact_name,
        Attestation(True, EvidenceSource.TESTED,
                    signed.attestation.recorded_monotonic),
        signed.producer_id,
        signed.signature,
    )
    assert auth.verify(upgraded) is False


def test_a_signature_cannot_be_moved_to_another_fact():
    """The fact name is inside the MAC, so evidence is not transferable."""
    auth = omnipotent()
    signed = issue(auth, "strategy_enabled")
    moved = SignedAttestation(
        "kill_switch_disengaged", signed.attestation, signed.producer_id,
        signed.signature,
    )
    assert auth.verify(moved) is False


def test_a_signature_cannot_be_reattributed_to_another_producer():
    auth = AttestationAuthority(KEY, {
        "a": ProducerIdentity("a", ALL_FACTS, ALL_SOURCES),
        "b": ProducerIdentity("b", ALL_FACTS, ALL_SOURCES),
    })
    signed = issue(auth, "strategy_enabled", producer="a")
    moved = SignedAttestation(
        signed.fact_name, signed.attestation, "b", signed.signature
    )
    assert auth.verify(moved) is False


def test_evidence_from_a_different_key_is_refused():
    """Key custody is the boundary; a foreign authority proves nothing."""
    mine = omnipotent()
    theirs = AttestationAuthority(
        OTHER_KEY, {"all": ProducerIdentity("all", ALL_FACTS, ALL_SOURCES)}
    )
    assert mine.verify(issue(theirs, "strategy_enabled")) is False


def test_field_separation_prevents_a_concatenation_collision():
    """A producer named "a|b" attesting "c" must not collide with "a" and "b|c"."""
    facts = frozenset({"strategy_enabled", "kill_switch_disengaged"})
    auth = AttestationAuthority(KEY, {
        "risk": ProducerIdentity("risk", facts, ALL_SOURCES),
        "risk|strategy_enabled": ProducerIdentity(
            "risk|strategy_enabled", facts, ALL_SOURCES
        ),
    })
    first = issue(auth, "strategy_enabled", producer="risk")
    second = issue(auth, "kill_switch_disengaged", producer="risk|strategy_enabled")
    assert first.signature != second.signature


@pytest.mark.parametrize("value", [None, "signed", 42, object(), Attestation(
    True, EvidenceSource.MEASURED, FRESH)])
def test_verifying_a_non_signed_object_is_false_not_an_error(value):
    assert omnipotent().verify(value) is False


# --------------------------------------------------------------------------
# 3. Producer restrictions.
# --------------------------------------------------------------------------


def test_a_producer_cannot_attest_a_fact_it_does_not_own():
    """The risk engine is not the thing that knows about the kill switch."""
    auth = AttestationAuthority(KEY, {
        "risk": ProducerIdentity(
            "risk", frozenset({"risk_within_limits"}), frozenset({EvidenceSource.MEASURED})
        )
    })
    assert auth.issue(
        producer_id="risk", fact_name="risk_within_limits", satisfied=True,
        source=EvidenceSource.MEASURED, recorded_monotonic=FRESH,
    ).fact_name == "risk_within_limits"
    with pytest.raises(AttestationAuthorityError):
        auth.issue(
            producer_id="risk", fact_name="kill_switch_disengaged", satisfied=True,
            source=EvidenceSource.MEASURED, recorded_monotonic=FRESH,
        )


def test_a_producer_cannot_cite_a_source_it_may_not_use():
    """A monitoring probe is not a person."""
    auth = AttestationAuthority(KEY, {
        "probe": ProducerIdentity(
            "probe", frozenset({"monitoring_healthy"}), frozenset({EvidenceSource.MEASURED})
        )
    })
    with pytest.raises(AttestationAuthorityError):
        auth.issue(
            producer_id="probe", fact_name="monitoring_healthy", satisfied=True,
            source=EvidenceSource.OPERATOR_ATTESTED, recorded_monotonic=FRESH,
        )


def test_an_unknown_producer_cannot_issue():
    with pytest.raises(AttestationAuthorityError):
        omnipotent().issue(
            producer_id="nobody", fact_name="strategy_enabled", satisfied=True,
            source=EvidenceSource.OPERATOR_ATTESTED, recorded_monotonic=FRESH,
        )


def test_an_unknown_fact_cannot_be_issued():
    with pytest.raises(AttestationAuthorityError):
        omnipotent().issue(
            producer_id="all", fact_name="not_a_precondition", satisfied=True,
            source=EvidenceSource.MEASURED, recorded_monotonic=FRESH,
        )


def test_narrowing_a_registry_invalidates_previously_issued_evidence():
    """Permissions are re-checked at verification, not trusted from issuance."""
    wide = AttestationAuthority(KEY, {
        "p": ProducerIdentity("p", ALL_FACTS, ALL_SOURCES)
    })
    signed = issue(wide, "kill_switch_disengaged", producer="p")
    assert wide.verify(signed) is True
    narrowed = AttestationAuthority(KEY, {
        "p": ProducerIdentity(
            "p", frozenset({"risk_within_limits"}), frozenset({EvidenceSource.MEASURED})
        )
    })
    assert narrowed.verify(signed) is False


def test_a_producer_may_not_be_granted_self_declared():
    """Nothing accepts it, so granting it would only mint useless evidence."""
    with pytest.raises(AttestationAuthorityError):
        ProducerIdentity(
            "p", frozenset({"strategy_enabled"}),
            frozenset({EvidenceSource.SELF_DECLARED}),
        )


@pytest.mark.parametrize("facts,sources", [
    (frozenset(), ALL_SOURCES),
    (ALL_FACTS, frozenset()),
    (frozenset({"not_a_fact"}), ALL_SOURCES),
])
def test_a_malformed_producer_is_refused(facts, sources):
    with pytest.raises(AttestationAuthorityError):
        ProducerIdentity("p", facts, sources)


@pytest.mark.parametrize("producer_id", ["", "   ", None, 7])
def test_a_producer_needs_an_identity(producer_id):
    with pytest.raises(AttestationAuthorityError):
        ProducerIdentity(producer_id, ALL_FACTS, ALL_SOURCES)


def test_a_registry_key_must_match_its_identity():
    with pytest.raises(AttestationAuthorityError):
        AttestationAuthority(
            KEY, {"a": ProducerIdentity("b", ALL_FACTS, ALL_SOURCES)}
        )


# --------------------------------------------------------------------------
# 4. Key handling.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("key", [b"", b"short", b"x" * (MINIMUM_KEY_BYTES - 1)])
def test_a_short_key_is_refused(key):
    """A key that is easy to guess is the same as no key at all."""
    with pytest.raises(AttestationAuthorityError):
        AttestationAuthority(key, {"a": ProducerIdentity("a", ALL_FACTS, ALL_SOURCES)})


@pytest.mark.parametrize("key", ["a-string-key-that-is-long-enough-here!!!", None, 42])
def test_a_non_bytes_key_is_refused(key):
    with pytest.raises(AttestationAuthorityError):
        AttestationAuthority(key, {"a": ProducerIdentity("a", ALL_FACTS, ALL_SOURCES)})


def test_the_key_is_never_rendered():
    """A failure that printed the secret would defeat the exercise."""
    auth = omnipotent()
    assert "signing" not in repr(auth)
    assert KEY.decode() not in repr(auth)
    assert not hasattr(auth, "signing_key")
    assert not hasattr(auth, "__dict__")
    try:
        auth.issue(producer_id="nobody", fact_name="strategy_enabled",
                   satisfied=True, source=EvidenceSource.MEASURED,
                   recorded_monotonic=FRESH)
    except AttestationAuthorityError as error:
        assert KEY.decode() not in str(error)


def test_the_registry_requires_at_least_one_producer():
    with pytest.raises(AttestationAuthorityError):
        AttestationAuthority(KEY, {})


def test_producer_ids_are_reported_sorted():
    auth = AttestationAuthority(KEY, {
        "z": ProducerIdentity("z", ALL_FACTS, ALL_SOURCES),
        "a": ProducerIdentity("a", ALL_FACTS, ALL_SOURCES),
    })
    assert auth.producer_ids == ("a", "z")


# --------------------------------------------------------------------------
# 5. Gate integration.
# --------------------------------------------------------------------------


def test_signed_evidence_satisfies_the_gate_preconditions():
    auth = omnipotent()
    record = signed_supervised(auth)
    assert record.unmet(current_monotonic=NOW, verifier=auth) == ()


def test_bare_attestations_are_refused_once_a_verifier_is_supplied():
    """Accepting one would restore the honour system the verifier replaces."""
    auth = omnipotent()
    bare = GatePreconditions(**{
        name: Attestation(True, accepted_source(name), FRESH)
        for name in REQUIRED_PRECONDITIONS
    })
    assert len(bare.unmet(current_monotonic=NOW, verifier=auth)) == len(
        REQUIRED_PRECONDITIONS
    )


def test_forged_evidence_fails_every_precondition_it_touches():
    auth = omnipotent()
    good = issue(auth, "strategy_enabled")
    forged = SignedAttestation(
        good.fact_name, good.attestation, good.producer_id, "0" * 64
    )
    record = signed_supervised(auth, strategy_enabled=forged)
    assert record.unmet(current_monotonic=NOW, verifier=auth) == ("strategy_enabled",)


def test_stale_signed_evidence_still_fails():
    """Signing proves origin, not freshness. Both checks still apply."""
    auth = omnipotent()
    stale = issue(auth, "strategy_enabled", at=NOW - 10_000.0)
    assert auth.verify(stale) is True
    record = signed_supervised(auth, strategy_enabled=stale)
    assert record.unmet(current_monotonic=NOW, verifier=auth) == ("strategy_enabled",)


def test_a_wrongly_sourced_signed_attestation_still_fails():
    auth = omnipotent()
    wrong = issue(auth, "strategy_enabled", source=EvidenceSource.MEASURED)
    assert auth.verify(wrong) is True          # genuinely issued
    record = signed_supervised(auth, strategy_enabled=wrong)
    assert record.unmet(current_monotonic=NOW, verifier=auth) == ("strategy_enabled",)


def test_the_gate_stays_closed_with_perfect_signed_evidence():
    auth = omnipotent()
    decision = open_gate(
        signed_supervised(auth), current_monotonic=NOW, verifier=auth
    )
    assert decision.verdict is GateVerdict.CLOSED_ACTIVATION_NOT_APPROVED
    assert decision.is_open is False
    assert decision.permits_automated_action is False


def test_the_unattended_gate_stays_closed_with_perfect_signed_evidence():
    auth = omnipotent()
    decision = open_unattended_gate(
        signed_supervised(auth), signed_unattended(auth),
        current_monotonic=NOW, verifier=auth,
    )
    assert decision.is_open is False
    assert decision.unmet_precondition_count == 0


def test_unsigned_mode_still_works_for_callers_that_have_not_adopted_signing():
    """Backward compatible, and the difference is never invisible."""
    bare = GatePreconditions(**{
        name: Attestation(True, accepted_source(name), FRESH)
        for name in REQUIRED_PRECONDITIONS
    })
    assert bare.unmet(current_monotonic=NOW) == ()
    assert bare.unmet(current_monotonic=NOW, verifier=omnipotent()) != ()


def test_verified_or_none_refuses_a_bare_attestation_under_a_verifier():
    auth = omnipotent()
    bare = Attestation(True, EvidenceSource.MEASURED, FRESH)
    assert verified_or_none(None, bare) is bare
    assert verified_or_none(auth, bare) is None


def test_verified_or_none_refuses_anything_without_an_attestation():
    for value in (None, "signed", 42, object()):
        assert verified_or_none(None, value) is None
        assert verified_or_none(omnipotent(), value) is None


# --------------------------------------------------------------------------
# 6. Malformed signed attestations.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("fact", ["", "   ", None, 7])
def test_a_signed_attestation_needs_a_fact_name(fact):
    with pytest.raises(AttestationAuthorityError):
        SignedAttestation(fact, Attestation(True, EvidenceSource.MEASURED, FRESH),
                          "p", "0" * 64)


def test_a_signed_attestation_needs_a_real_attestation():
    with pytest.raises(AttestationAuthorityError):
        SignedAttestation("strategy_enabled", "yes", "p", "0" * 64)


@pytest.mark.parametrize("producer", ["", "  ", None, 3])
def test_a_signed_attestation_needs_a_producer(producer):
    with pytest.raises(AttestationAuthorityError):
        SignedAttestation("strategy_enabled",
                          Attestation(True, EvidenceSource.MEASURED, FRESH),
                          producer, "0" * 64)


@pytest.mark.parametrize("signature", ["", None, 42])
def test_a_signed_attestation_needs_a_signature(signature):
    with pytest.raises(AttestationAuthorityError):
        SignedAttestation("strategy_enabled",
                          Attestation(True, EvidenceSource.MEASURED, FRESH),
                          "p", signature)


# --------------------------------------------------------------------------
# 7. Independence and honesty about scope.
# --------------------------------------------------------------------------


def _imports(path: pathlib.Path) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def test_the_authority_imports_no_pinned_checkpoint_file():
    for pinned in ("read_only_observation_runtime", "read_only_broker_state",
                   "read_only_authorization_manifest", "live_order_state_model"):
        assert not any(pinned in name for name in _imports(MODULE_PATH)), pinned


def test_the_authority_performs_no_io_and_no_network():
    imported = _imports(MODULE_PATH)
    assert imported.isdisjoint(
        {"socket", "http", "urllib", "requests", "os", "pathlib", "subprocess"}
    )
    for node in ast.walk(ast.parse(MODULE_PATH.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Call):
            func = node.func
            name = (
                func.attr if isinstance(func, ast.Attribute)
                else func.id if isinstance(func, ast.Name)
                else None
            )
            assert name not in {"open", "urlopen", "write_text", "print", "getenv"}


def test_the_authority_reads_no_wall_clock():
    source = MODULE_PATH.read_text(encoding="utf-8")
    assert "datetime.now(" not in source
    assert "time.time(" not in source
    assert "time.monotonic(" not in source


def test_comparison_is_constant_time():
    """A timing side channel on a MAC check is how a signature becomes forgeable."""
    source = MODULE_PATH.read_text(encoding="utf-8")
    assert "hmac.compare_digest" in source
    assert "== signed.signature" not in source


def test_the_module_states_what_it_does_not_guarantee():
    """The review caught an overclaim once; the limit is written down now."""
    source = MODULE_PATH.read_text(encoding="utf-8")
    assert "does **not** guarantee" in source
    assert "key custody" in source


def test_no_broker_manifest_checkpoint_or_credential_path_exists():
    source = MODULE_PATH.read_text(encoding="utf-8").lower()
    for token in ("robinhood", "call_tool", "place_equity", "cancel_equity",
                  "manifest", "local.yaml", ".rh_tokens", "password", "environ"):
        assert token not in source, token


def test_a_decision_records_whether_its_evidence_was_verified():
    """An unverified decision that looks identical to a verified one is how
    the distinction quietly stops mattering."""
    auth = omnipotent()
    unsigned = GatePreconditions(**{
        name: Attestation(True, accepted_source(name), FRESH)
        for name in REQUIRED_PRECONDITIONS
    })
    without = open_gate(unsigned, current_monotonic=NOW)
    with_verifier = open_gate(signed_supervised(auth), current_monotonic=NOW,
                              verifier=auth)
    assert without.evidence_verified is False
    assert with_verifier.evidence_verified is True
    assert without.sanitized_facts()["evidence_verified"] == "false"
    assert with_verifier.sanitized_facts()["evidence_verified"] == "true"


def test_the_unattended_decision_also_records_verification():
    auth = omnipotent()
    decision = open_unattended_gate(
        signed_supervised(auth), signed_unattended(auth),
        current_monotonic=NOW, verifier=auth,
    )
    assert decision.evidence_verified is True


def test_a_malformed_input_decision_still_records_the_mode():
    auth = omnipotent()
    assert open_gate("nope", current_monotonic=NOW, verifier=auth).evidence_verified is True
    assert open_gate("nope", current_monotonic=NOW).evidence_verified is False


def test_recording_verification_never_grants_permission():
    auth = omnipotent()
    decision = open_gate(signed_supervised(auth), current_monotonic=NOW, verifier=auth)
    assert decision.evidence_verified is True
    assert decision.permits_automated_action is False
    assert decision.is_open is False
