"""Producer-bound attestations — evidence a caller cannot mint for itself.

The execution gates were reviewed with one limitation stated plainly: an
`Attestation` carries a source and a timestamp, but nothing stopped the caller
from writing both. A component could claim `OPERATOR_ATTESTED`, stamp it
`now`, and the gate would accept it. The gate was inert regardless, so nothing
was exploitable — but the evidence model was an honour system.

This closes that. An attestation is now **issued** by an authority against a
named producer, and carries a MAC over its own content. Verification fails if
any field is altered, if the signature is replayed onto a different fact, or
if the producer was not permitted to make that claim in the first place.

## What this does and does not guarantee

It guarantees that a caller **without the signing key** cannot mint evidence,
cannot alter issued evidence, and cannot move a valid signature onto a
different fact or a fresher timestamp.

It does **not** guarantee anything against a caller that holds the key. An
in-process component with the secret can issue whatever it likes. The security
boundary is key custody, not this module — the intended deployment keeps
producers in separate processes with separate keys, and this module is what
makes that separation mean something. Saying otherwise would be the same
overclaim the review caught the first time.

## Two independent restrictions per producer

A producer declares what it may claim, and both halves are checked:

* **which facts** it owns — the risk engine cannot attest that the kill switch
  is disengaged, because it is not the thing that knows;
* **which sources** it may cite — a monitoring probe cannot claim
  `OPERATOR_ATTESTED`, because it is not a person.

Either restriction alone would leave a hole. Together they mean a compromised
producer can lie only about its own subject, in its own voice.
"""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

from src.execution_gates import (
    REQUIRED_EVIDENCE,
    Attestation,
    EvidenceSource,
)


class AttestationAuthorityError(RuntimeError):
    """A refused issuance or a failed verification. Carries no secret."""


#: Minimum acceptable signing-key length. Short keys make a brute-force search
#: cheap, and a key that is easy to guess is the same as no key at all.
MINIMUM_KEY_BYTES = 32


@dataclass(frozen=True)
class ProducerIdentity:
    """Who may say what, in which voice.

    Both sets are required and neither may be empty. A producer permitted to
    attest nothing is a configuration mistake that would otherwise sit silently
    in a registry looking legitimate.
    """

    producer_id: str
    permitted_facts: frozenset[str]
    permitted_sources: frozenset[EvidenceSource]

    def __post_init__(self) -> None:
        if not isinstance(self.producer_id, str) or not self.producer_id.strip():
            raise AttestationAuthorityError("a producer identity is required")
        if not isinstance(self.permitted_facts, frozenset) or not self.permitted_facts:
            raise AttestationAuthorityError("a producer must own at least one fact")
        if (
            not isinstance(self.permitted_sources, frozenset)
            or not self.permitted_sources
        ):
            raise AttestationAuthorityError("a producer must have at least one source")
        for fact in self.permitted_facts:
            if fact not in REQUIRED_EVIDENCE:
                raise AttestationAuthorityError("producer owns an unknown fact")
        for source in self.permitted_sources:
            if not isinstance(source, EvidenceSource):
                raise AttestationAuthorityError("producer source is invalid")
        if EvidenceSource.SELF_DECLARED in self.permitted_sources:
            # Nothing accepts SELF_DECLARED, so a producer permitted to emit it
            # could only ever produce evidence that fails. Refusing the
            # registration is clearer than issuing attestations nobody accepts.
            raise AttestationAuthorityError(
                "SELF_DECLARED is accepted nowhere and may not be granted"
            )


@dataclass(frozen=True)
class SignedAttestation:
    """An attestation bound to the fact it describes and the producer that
    made it. The signature covers all of it, so none of it can be swapped."""

    fact_name: str
    attestation: Attestation
    producer_id: str
    signature: str

    def __post_init__(self) -> None:
        if not isinstance(self.fact_name, str) or not self.fact_name.strip():
            raise AttestationAuthorityError("a fact name is required")
        if not isinstance(self.attestation, Attestation):
            raise AttestationAuthorityError("an attestation is required")
        if not isinstance(self.producer_id, str) or not self.producer_id.strip():
            raise AttestationAuthorityError("a producer identity is required")
        if not isinstance(self.signature, str) or not self.signature:
            raise AttestationAuthorityError("a signature is required")


def _canonical_bytes(
    fact_name: str, attestation: Attestation, producer_id: str
) -> bytes:
    """Every signed field, unambiguously delimited.

    `\\x1f` separates fields so that a producer named `"a|b"` attesting `"c"`
    cannot collide with one named `"a"` attesting `"b|c"`. Length-prefixing
    would work too; an ASCII unit separator that cannot appear in an enum
    value or a precondition name is simpler to read in a failure.
    """
    parts = (
        "orb-live.attestation.v1",
        fact_name,
        "true" if attestation.satisfied else "false",
        attestation.source.value,
        repr(float(attestation.recorded_monotonic)),
        producer_id,
    )
    return "\x1f".join(parts).encode("utf-8")


class AttestationAuthority:
    """Issues and verifies producer-bound attestations.

    The key never leaves this object, is never rendered by `repr`, and is
    never included in an error message — a failure that printed the secret
    would defeat the whole exercise.
    """

    __slots__ = ("_key", "_producers")

    def __init__(
        self, signing_key: bytes, producers: Mapping[str, ProducerIdentity]
    ) -> None:
        if not isinstance(signing_key, (bytes, bytearray)):
            raise AttestationAuthorityError("signing key must be bytes")
        if len(signing_key) < MINIMUM_KEY_BYTES:
            raise AttestationAuthorityError("signing key is too short")
        if not isinstance(producers, Mapping) or not producers:
            raise AttestationAuthorityError("at least one producer is required")
        for producer_id, identity in producers.items():
            if not isinstance(identity, ProducerIdentity):
                raise AttestationAuthorityError("producer entry is invalid")
            if producer_id != identity.producer_id:
                raise AttestationAuthorityError("producer key and identity disagree")
        self._key = bytes(signing_key)
        self._producers: Mapping[str, ProducerIdentity] = MappingProxyType(
            dict(producers)
        )

    def __repr__(self) -> str:
        return f"AttestationAuthority(producers={len(self._producers)})"

    @property
    def producer_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._producers))

    def _sign(self, fact_name: str, attestation: Attestation, producer_id: str) -> str:
        return hmac.new(
            self._key,
            _canonical_bytes(fact_name, attestation, producer_id),
            hashlib.sha256,
        ).hexdigest()

    def issue(
        self,
        *,
        producer_id: str,
        fact_name: str,
        satisfied: bool,
        source: EvidenceSource,
        recorded_monotonic: float,
    ) -> SignedAttestation:
        """Mint evidence, or refuse if this producer may not make this claim."""
        identity = self._producers.get(producer_id)
        if identity is None:
            raise AttestationAuthorityError("unknown producer")
        if fact_name not in REQUIRED_EVIDENCE:
            raise AttestationAuthorityError("unknown fact")
        if fact_name not in identity.permitted_facts:
            raise AttestationAuthorityError("producer does not own this fact")
        if not isinstance(source, EvidenceSource):
            raise AttestationAuthorityError("evidence source is invalid")
        if source not in identity.permitted_sources:
            raise AttestationAuthorityError("producer may not cite this source")
        attestation = Attestation(
            satisfied=satisfied, source=source, recorded_monotonic=recorded_monotonic
        )
        return SignedAttestation(
            fact_name=fact_name,
            attestation=attestation,
            producer_id=producer_id,
            signature=self._sign(fact_name, attestation, producer_id),
        )

    def verify(self, signed: SignedAttestation) -> bool:
        """True only if the signature matches and the claim was permitted.

        Re-checks the producer's permissions rather than trusting that they
        held at issue time: a registry that has since narrowed should
        invalidate evidence issued under the wider one.
        """
        if not isinstance(signed, SignedAttestation):
            return False
        identity = self._producers.get(signed.producer_id)
        if identity is None:
            return False
        if signed.fact_name not in identity.permitted_facts:
            return False
        if signed.attestation.source not in identity.permitted_sources:
            return False
        expected = self._sign(
            signed.fact_name, signed.attestation, signed.producer_id
        )
        # Constant time: a timing side channel on a MAC comparison is the
        # classic way a signature check becomes forgeable.
        return hmac.compare_digest(expected, signed.signature)

    def verified_attestation(self, signed: SignedAttestation) -> Attestation:
        """The underlying attestation, or a refusal. Never returns unverified."""
        if not self.verify(signed):
            raise AttestationAuthorityError("attestation failed verification")
        return signed.attestation


__all__ = [
    "MINIMUM_KEY_BYTES",
    "AttestationAuthority",
    "AttestationAuthorityError",
    "ProducerIdentity",
    "SignedAttestation",
]
