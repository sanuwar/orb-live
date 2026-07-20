from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import yaml

from src.config import PROJECT_ROOT


DEFAULT_LOCAL_CONFIG_PATH = (
    PROJECT_ROOT / "config" / "local.yaml"
)

FINGERPRINT_ENVIRONMENT_VARIABLE = (
    "ORB_EXPECTED_ACCOUNT_FINGERPRINT"
)

FINGERPRINT_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class CommissionedIdentityError(RuntimeError):
    """Raised when the commissioned account identity is unusable."""


def normalize_fingerprint(
    value: Any,
    *,
    source: str,
) -> str:
    """
    Validates one account fingerprint.

    Fingerprints are SHA-256 hex digests produced by
    broker_snapshot.account_fingerprint. A full account number is never
    accepted here and never stored.
    """

    if value is None:
        raise CommissionedIdentityError(
            f"The commissioned account fingerprint from {source} "
            "is missing."
        )

    if not isinstance(value, str):
        raise CommissionedIdentityError(
            f"The commissioned account fingerprint from {source} "
            "must be a string."
        )

    normalized = value.strip().lower()

    if not normalized:
        raise CommissionedIdentityError(
            f"The commissioned account fingerprint from {source} "
            "is blank."
        )

    if FINGERPRINT_PATTERN.fullmatch(normalized) is None:
        raise CommissionedIdentityError(
            f"The commissioned account fingerprint from {source} "
            "is malformed. A 64-character SHA-256 hex digest is "
            "required. Do not supply a raw account number."
        )

    return normalized


def _broker_section(
    config: Mapping[str, Any] | None,
    *,
    source: str,
) -> Mapping[str, Any]:
    if not config:
        return {}

    broker = config.get("broker")

    if broker is None:
        return {}

    if not isinstance(broker, Mapping):
        raise CommissionedIdentityError(
            f"The broker section in {source} must be an object."
        )

    return broker


def load_local_override(
    path: Path = DEFAULT_LOCAL_CONFIG_PATH,
) -> dict[str, Any]:
    """
    Loads the gitignored local configuration override, when present.

    The override supplies operator-commissioned values that must never
    be committed. A missing file is not an error; a malformed one is.
    """

    if not path.exists():
        return {}

    with path.open("r", encoding="utf-8") as file:
        loaded = yaml.safe_load(file)

    if loaded is None:
        return {}

    if not isinstance(loaded, dict):
        raise CommissionedIdentityError(
            f"The local configuration override at {path.name} must "
            "contain a YAML object."
        )

    return loaded


def resolve_commissioned_fingerprint(
    config: Mapping[str, Any] | None = None,
    *,
    local_config: Mapping[str, Any] | None = None,
    environment: Mapping[str, str] | None = None,
) -> str:
    """
    Resolves the commissioned account fingerprint, failing closed.

    Precedence is explicit and never silent:

    - an explicit local override value wins,
    - otherwise the base configuration value is used,
    - the environment variable may supply a value only when no explicit
      commissioned value exists; when both exist and disagree, this
      fails closed rather than letting the environment silently
      override a commissioned value.

    There is no trust-on-first-use. An observed account can never
    establish or update this value.
    """

    env = os.environ if environment is None else environment

    local_value = _broker_section(
        local_config,
        source="the local configuration override",
    ).get("expected_account_fingerprint")

    base_value = _broker_section(
        config,
        source="the strategy configuration",
    ).get("expected_account_fingerprint")

    commissioned_raw = (
        local_value
        if local_value is not None
        else base_value
    )
    commissioned_source = (
        "the local configuration override"
        if local_value is not None
        else "the strategy configuration"
    )

    environment_raw = env.get(
        FINGERPRINT_ENVIRONMENT_VARIABLE
    )

    if commissioned_raw is None:
        if environment_raw is None:
            raise CommissionedIdentityError(
                "broker.expected_account_fingerprint is not "
                "configured. Supply it through the gitignored local "
                "configuration override before reconciling against a "
                "live account."
            )

        return normalize_fingerprint(
            environment_raw,
            source=(
                f"the {FINGERPRINT_ENVIRONMENT_VARIABLE} "
                "environment variable"
            ),
        )

    commissioned = normalize_fingerprint(
        commissioned_raw,
        source=commissioned_source,
    )

    if environment_raw is not None:
        environment_value = normalize_fingerprint(
            environment_raw,
            source=(
                f"the {FINGERPRINT_ENVIRONMENT_VARIABLE} "
                "environment variable"
            ),
        )

        if environment_value != commissioned:
            raise CommissionedIdentityError(
                f"The {FINGERPRINT_ENVIRONMENT_VARIABLE} environment "
                "variable disagrees with the commissioned account "
                "fingerprint. Failing closed rather than silently "
                "overriding a commissioned value."
            )

    return commissioned


@dataclass(frozen=True)
class CommissionedAccount:
    """
    The operator-commissioned account identity.

    Holds a fingerprint only. The full account number is never stored,
    logged, or persisted.
    """

    expected_fingerprint: str

    @classmethod
    def from_config(
        cls,
        config: Mapping[str, Any] | None = None,
        *,
        local_config: Mapping[str, Any] | None = None,
        environment: Mapping[str, str] | None = None,
    ) -> "CommissionedAccount":
        return cls(
            expected_fingerprint=(
                resolve_commissioned_fingerprint(
                    config,
                    local_config=local_config,
                    environment=environment,
                )
            )
        )

    def matches(self, observed_fingerprint: Any) -> bool:
        try:
            observed = normalize_fingerprint(
                observed_fingerprint,
                source="the broker snapshot",
            )
        except CommissionedIdentityError:
            return False

        return observed == self.expected_fingerprint

    def verify(self, observed_fingerprint: Any) -> None:
        """
        Fails closed unless the observed account is the commissioned one.

        The observed fingerprint is never adopted, recorded, or used to
        update the commissioned value.
        """

        observed = normalize_fingerprint(
            observed_fingerprint,
            source="the broker snapshot",
        )

        if observed != self.expected_fingerprint:
            raise CommissionedIdentityError(
                "The observed broker account does not match the "
                "commissioned account. Failing closed without "
                "adopting the observed account."
            )
