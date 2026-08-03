"""Per-producer signing keys — making producer separation real.

`attestation_authority` binds a producer name into the signature, which stops
an attacker *reattributing* one producer's evidence to another. It does not
stop a component that holds the signing key from issuing evidence **as** any
producer it likes, because there is only one key and every producer shares it.

That is a meaningful gap. The whole argument for producer restrictions — the
risk engine cannot attest the kill switch, a monitoring probe cannot claim to
be a person — rests on components being unable to sign in each other's names.
With a shared key that restriction is a convention, enforced only by everyone
choosing to write their own name in.

This layer gives each producer **its own key**, so the restriction becomes a
property of key custody rather than of good manners. Compromising the risk
engine yields the risk engine's key and nothing else.

## What it adds beyond the shared-key authority

* **Per-producer signing material.** No single secret can sign for everyone.
* **Key versions.** Rotation is explicit: a signature names the version that
  made it, and evidence signed under a retired version stops verifying rather
  than silently continuing to work.
* **Disabled producers.** A component can be taken out of service without
  deleting its history, and its outstanding evidence stops verifying
  immediately.
* **Reasoned verification results.** A bare `False` tells an operator nothing.
  A `VerificationOutcome` says *why* — unknown producer, disabled, retired
  key, wrong fact, wrong source, bad signature — without naming which producer
  or leaking any key material.

## What it still does not do

There is no PKI, no network, no credential store, no OS keyring, and no key
generation. Keys are supplied explicitly by whoever constructs the registry.
Key custody remains the boundary: a caller holding a producer's key can issue
as that producer. This layer narrows the blast radius from *everything* to
*one component*; it does not eliminate it, and saying otherwise would be the
kind of overclaim this codebase has already had to correct once.
"""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Mapping

from src.attestation_authority import MINIMUM_KEY_BYTES, ProducerIdentity
from src.execution_gates import REQUIRED_EVIDENCE, Attestation, EvidenceSource


class ProducerKeyError(RuntimeError):
    """A refused registration or issuance. Never carries key material."""


class VerificationOutcome(str, Enum):
    """Why verification concluded what it did.

    Named reasons rather than a bare boolean, because an operator reading
    "verification failed" at three in the morning needs to know whether a
    producer was disabled on purpose or somebody is forging signatures.
    """

    VERIFIED = "VERIFIED"
    UNKNOWN_PRODUCER = "UNKNOWN_PRODUCER"
    PRODUCER_DISABLED = "PRODUCER_DISABLED"
    UNKNOWN_KEY_VERSION = "UNKNOWN_KEY_VERSION"
    RETIRED_KEY_VERSION = "RETIRED_KEY_VERSION"
    FACT_NOT_OWNED = "FACT_NOT_OWNED"
    SOURCE_NOT_PERMITTED = "SOURCE_NOT_PERMITTED"
    SIGNATURE_MISMATCH = "SIGNATURE_MISMATCH"
    MALFORMED = "MALFORMED"


@dataclass(frozen=True)
class VerificationResult:
    """A verdict a caller can log without redacting anything.

    Deliberately carries no producer identity, key version, fact name, or
    signature. Those are all fine inside the process and none of them belongs
    in whatever a caller decides to write down.
    """

    outcome: VerificationOutcome

    @property
    def verified(self) -> bool:
        return self.outcome is VerificationOutcome.VERIFIED

    def sanitized_facts(self) -> dict[str, str]:
        return {
            "verification_outcome": self.outcome.value,
            "verified": "true" if self.verified else "false",
        }


@dataclass(frozen=True)
class ProducerKey:
    """One version of one producer's signing material."""

    producer_id: str
    key_version: str
    signing_key: bytes
    retired: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.producer_id, str) or not self.producer_id.strip():
            raise ProducerKeyError("a producer identity is required")
        if not isinstance(self.key_version, str) or not self.key_version.strip():
            raise ProducerKeyError("a key version is required")
        if not isinstance(self.signing_key, (bytes, bytearray)):
            raise ProducerKeyError("signing key must be bytes")
        if len(self.signing_key) < MINIMUM_KEY_BYTES:
            raise ProducerKeyError("signing key is too short")
        if not isinstance(self.retired, bool):
            raise ProducerKeyError("retired must be a bool")
        object.__setattr__(self, "signing_key", bytes(self.signing_key))

    def __repr__(self) -> str:
        # Never renders the key. A repr lands in logs and tracebacks.
        return (
            f"ProducerKey(producer_id={self.producer_id!r}, "
            f"key_version={self.key_version!r}, retired={self.retired})"
        )


@dataclass(frozen=True)
class RegisteredProducer:
    """A producer, what it may claim, its keys, and whether it is in service."""

    identity: ProducerIdentity
    keys: tuple[ProducerKey, ...]
    enabled: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.identity, ProducerIdentity):
            raise ProducerKeyError("a producer identity is required")
        if not isinstance(self.keys, tuple) or not self.keys:
            raise ProducerKeyError("a producer needs at least one key")
        if not isinstance(self.enabled, bool):
            raise ProducerKeyError("enabled must be a bool")
        versions = [key.key_version for key in self.keys]
        if len(versions) != len(set(versions)):
            raise ProducerKeyError("duplicate key version for a producer")
        for key in self.keys:
            if not isinstance(key, ProducerKey):
                raise ProducerKeyError("producer key entry is invalid")
            if key.producer_id != self.identity.producer_id:
                raise ProducerKeyError("key belongs to a different producer")
        if all(key.retired for key in self.keys):
            # A producer whose every key is retired can never issue. Refusing
            # the registration is clearer than accepting one that silently
            # cannot work.
            raise ProducerKeyError("a producer needs at least one live key")

    def key(self, key_version: str) -> ProducerKey | None:
        for candidate in self.keys:
            if candidate.key_version == key_version:
                return candidate
        return None

    @property
    def current_key(self) -> ProducerKey:
        for candidate in self.keys:
            if not candidate.retired:
                return candidate
        raise ProducerKeyError("no live key")   # unreachable; guarded above


@dataclass(frozen=True)
class BoundAttestation:
    """A signed attestation that names the key version that made it."""

    fact_name: str
    attestation: Attestation
    producer_id: str
    key_version: str
    signature: str

    def __post_init__(self) -> None:
        for name in ("fact_name", "producer_id", "key_version", "signature"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ProducerKeyError(f"{name} is required")
        if not isinstance(self.attestation, Attestation):
            raise ProducerKeyError("an attestation is required")


def _canonical_bytes(
    fact_name: str, attestation: Attestation, producer_id: str, key_version: str
) -> bytes:
    """Every signed field, unambiguously delimited.

    The key version is inside the MAC so a signature cannot be replayed as if
    a different version had produced it — otherwise rotation would be
    cosmetic.
    """
    parts = (
        "orb-live.bound-attestation.v1",
        fact_name,
        "true" if attestation.satisfied else "false",
        attestation.source.value,
        repr(float(attestation.recorded_monotonic)),
        producer_id,
        key_version,
    )
    return "\x1f".join(parts).encode("utf-8")


class ProducerKeyRegistry:
    """Issues and verifies with per-producer keys.

    Satisfies the gate's `AttestationVerifier` protocol via `verify`, and
    offers `verify_detailed` where a reason is wanted. Keys never leave this
    object, are never rendered, and never appear in an error message.
    """

    __slots__ = ("_producers",)

    def __init__(self, producers: Mapping[str, RegisteredProducer]) -> None:
        if not isinstance(producers, Mapping) or not producers:
            raise ProducerKeyError("at least one producer is required")
        seen_keys: set[bytes] = set()
        for producer_id, registered in producers.items():
            if not isinstance(registered, RegisteredProducer):
                raise ProducerKeyError("producer entry is invalid")
            if producer_id != registered.identity.producer_id:
                raise ProducerKeyError("registry key and identity disagree")
            for key in registered.keys:
                if key.signing_key in seen_keys:
                    # Two producers sharing a key would recreate exactly the
                    # weakness this module exists to remove.
                    raise ProducerKeyError("producers may not share signing material")
                seen_keys.add(key.signing_key)
        self._producers: Mapping[str, RegisteredProducer] = MappingProxyType(
            dict(producers)
        )

    def __repr__(self) -> str:
        return f"ProducerKeyRegistry(producers={len(self._producers)})"

    @property
    def producer_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._producers))

    def is_enabled(self, producer_id: str) -> bool:
        registered = self._producers.get(producer_id)
        return registered is not None and registered.enabled

    def _sign(self, key: ProducerKey, fact_name: str, attestation: Attestation) -> str:
        return hmac.new(
            key.signing_key,
            _canonical_bytes(fact_name, attestation, key.producer_id, key.key_version),
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
        key_version: str | None = None,
    ) -> BoundAttestation:
        """Mint evidence under a producer's own key, or refuse."""
        registered = self._producers.get(producer_id)
        if registered is None:
            raise ProducerKeyError("unknown producer")
        if not registered.enabled:
            raise ProducerKeyError("producer is disabled")
        if fact_name not in REQUIRED_EVIDENCE:
            raise ProducerKeyError("unknown fact")
        if fact_name not in registered.identity.permitted_facts:
            raise ProducerKeyError("producer does not own this fact")
        if not isinstance(source, EvidenceSource):
            raise ProducerKeyError("evidence source is invalid")
        if source not in registered.identity.permitted_sources:
            raise ProducerKeyError("producer may not cite this source")

        if key_version is None:
            key = registered.current_key
        else:
            key = registered.key(key_version)
            if key is None:
                raise ProducerKeyError("unknown key version")
            if key.retired:
                raise ProducerKeyError("key version is retired")

        attestation = Attestation(
            satisfied=satisfied, source=source, recorded_monotonic=recorded_monotonic
        )
        return BoundAttestation(
            fact_name=fact_name,
            attestation=attestation,
            producer_id=producer_id,
            key_version=key.key_version,
            signature=self._sign(key, fact_name, attestation),
        )

    def verify_detailed(self, signed: object) -> VerificationResult:
        """Verify, and say why when it fails.

        Permissions and enablement are re-checked here rather than trusted
        from issuance, so disabling a producer or narrowing its facts
        invalidates evidence that is already in flight.
        """
        if not isinstance(signed, BoundAttestation):
            return VerificationResult(VerificationOutcome.MALFORMED)
        registered = self._producers.get(signed.producer_id)
        if registered is None:
            return VerificationResult(VerificationOutcome.UNKNOWN_PRODUCER)
        if not registered.enabled:
            return VerificationResult(VerificationOutcome.PRODUCER_DISABLED)
        if signed.fact_name not in registered.identity.permitted_facts:
            return VerificationResult(VerificationOutcome.FACT_NOT_OWNED)
        if signed.attestation.source not in registered.identity.permitted_sources:
            return VerificationResult(VerificationOutcome.SOURCE_NOT_PERMITTED)
        key = registered.key(signed.key_version)
        if key is None:
            return VerificationResult(VerificationOutcome.UNKNOWN_KEY_VERSION)
        if key.retired:
            # Rotation has to actually invalidate, or it is only paperwork.
            return VerificationResult(VerificationOutcome.RETIRED_KEY_VERSION)
        expected = self._sign(key, signed.fact_name, signed.attestation)
        if not hmac.compare_digest(expected, signed.signature):
            # Constant time: a timing side channel on a MAC comparison is the
            # classic way a signature check becomes forgeable.
            return VerificationResult(VerificationOutcome.SIGNATURE_MISMATCH)
        return VerificationResult(VerificationOutcome.VERIFIED)

    def verify(self, signed: object) -> bool:
        """The gate's `AttestationVerifier` protocol."""
        return self.verify_detailed(signed).verified


__all__ = [
    "BoundAttestation",
    "ProducerKey",
    "ProducerKeyError",
    "ProducerKeyRegistry",
    "RegisteredProducer",
    "VerificationOutcome",
    "VerificationResult",
]
