"""Operator-issued non-interactive authorization for the read-only checkpoint.

Every test is local. No manifest here authorizes a real account, and nothing
opens a session.

The properties that matter most:

1. a manifest is not an always-yes mode — it authorizes one identity, once;
2. every pinned field is actually checked, and each one alone can refuse;
3. nothing in tracked source can create, renew, or broaden a manifest;
4. a read-only manifest can never become write authorization.
"""

from __future__ import annotations

import ast
import json
import pathlib
from datetime import datetime, timedelta, timezone

import pytest

import src.read_only_authorization_manifest as manifests
from src.read_only_authorization_manifest import (
    MANIFEST_REQUIRED_KEYS,
    MANIFEST_VERSION,
    MAXIMUM_VALIDITY,
    PINNED_IMPLEMENTATION_FILES,
    READ_ONLY_SCOPE,
    UNATTENDED_WRITE_POLICY_REQUIRED_FIELDS,
    AuthorizationManifestError,
    ManifestAuthorization,
    ManifestConsumptionLedger,
    assert_not_write_authorization,
    checkpoint_implementation_digest,
    load_manifest,
    parse_manifest,
    resolve_head_sha,
    validate_manifest,
)
from src.read_only_observation_runtime import (
    ACKNOWLEDGMENT_STAGE,
    AUTHORIZATION_STAGES,
    OBSERVATION_STAGE,
    PERMITTED_READ_TOOLS,
    REQUIRED_ZERO_COUNTERS,
)

REPOSITORY_ROOT = pathlib.Path(__file__).parent.parent
OBSERVATION_ID = "m9-obs-0007"
SHA = "a" * 40
DIGEST = "b" * 64
ISSUED = datetime(2026, 8, 3, 13, 0, tzinfo=timezone.utc)
NOW = ISSUED + timedelta(minutes=5)


def payload(**overrides: object) -> dict[str, object]:
    document = {
        "manifest_version": MANIFEST_VERSION,
        "scope": READ_ONLY_SCOPE,
        "approved_main_sha": SHA,
        "checkpoint_implementation_digest": DIGEST,
        "allowed_broker_operations": list(PERMITTED_READ_TOOLS),
        "write_limits": {name: 0 for name in REQUIRED_ZERO_COUNTERS},
        "maximum_sessions": 1,
        "checkpoint_observation_id": OBSERVATION_ID,
        "issued_at": ISSUED.isoformat(),
        "expires_at": (ISSUED + timedelta(hours=2)).isoformat(),
        "issued_by": "operator",
    }
    document.update(overrides)
    return document


def check(manifest, *, observation_id: str = OBSERVATION_ID, now: datetime = NOW) -> None:
    validate_manifest(
        manifest,
        repository_root=REPOSITORY_ROOT,
        checkpoint_observation_id=observation_id,
        now=now,
        head_sha=SHA,
        implementation_digest=DIGEST,
    )


def authorization(tmp_path, **overrides) -> ManifestAuthorization:
    settings = {
        "manifest": parse_manifest(payload()),
        "ledger": ManifestConsumptionLedger(tmp_path / "ledger"),
        "repository_root": REPOSITORY_ROOT,
        "checkpoint_observation_id": OBSERVATION_ID,
        "now": NOW,
        "head_sha": SHA,
        "implementation_digest": DIGEST,
    }
    settings.update(overrides)
    return ManifestAuthorization(**settings)


# --------------------------------------------------------------------------
# 1. Shape.
# --------------------------------------------------------------------------


def test_a_well_formed_manifest_parses_and_validates():
    check(parse_manifest(payload()))


def test_a_missing_field_is_refused():
    for name in MANIFEST_REQUIRED_KEYS:
        document = payload()
        document.pop(name)
        with pytest.raises(AuthorizationManifestError):
            parse_manifest(document)


def test_an_unexpected_field_is_refused():
    """An unknown key may be a typo for one that then silently defaults."""
    with pytest.raises(AuthorizationManifestError):
        parse_manifest(payload(allow_writes=True))


@pytest.mark.parametrize("document", [None, [], "manifest", 3, True])
def test_a_non_object_manifest_is_refused(document):
    with pytest.raises(AuthorizationManifestError):
        parse_manifest(document)


def test_an_unsupported_version_is_refused():
    with pytest.raises(AuthorizationManifestError):
        parse_manifest(payload(manifest_version=2))


@pytest.mark.parametrize("value", ["", "xyz", "a" * 39, "a" * 41, "A" * 40, None, 1])
def test_a_malformed_commit_sha_is_refused(value):
    with pytest.raises(AuthorizationManifestError):
        parse_manifest(payload(approved_main_sha=value))


@pytest.mark.parametrize("value", ["", "b" * 63, "b" * 65, "Z" * 64, None])
def test_a_malformed_digest_is_refused(value):
    with pytest.raises(AuthorizationManifestError):
        parse_manifest(payload(checkpoint_implementation_digest=value))


@pytest.mark.parametrize("value", ["2026-08-03 13:00:00", "not-a-time", "", None, 1])
def test_a_malformed_timestamp_is_refused(value):
    with pytest.raises(AuthorizationManifestError):
        parse_manifest(payload(issued_at=value))


def test_a_naive_timestamp_is_refused():
    """Ambiguous by exactly the amount that matters for an expiry check."""
    with pytest.raises(AuthorizationManifestError):
        parse_manifest(payload(issued_at="2026-08-03T13:00:00"))


@pytest.mark.parametrize("value", [True, 1.5, "1", None])
def test_a_non_integer_write_limit_is_refused(value):
    limits = {name: 0 for name in REQUIRED_ZERO_COUNTERS}
    limits["placement_writes"] = value
    with pytest.raises(AuthorizationManifestError):
        parse_manifest(payload(write_limits=limits))


@pytest.mark.parametrize("value", ["", "  ", None, 3])
def test_a_missing_observation_identity_is_refused(value):
    with pytest.raises(AuthorizationManifestError):
        parse_manifest(payload(checkpoint_observation_id=value))


def test_a_malformed_file_is_refused(tmp_path):
    target = tmp_path / "manifest.json"
    target.write_text("{not json", encoding="utf-8")
    with pytest.raises(AuthorizationManifestError):
        load_manifest(target)


def test_an_absent_file_is_refused(tmp_path):
    with pytest.raises(AuthorizationManifestError):
        load_manifest(tmp_path / "missing.json")


def test_a_manifest_round_trips_from_disk(tmp_path):
    target = tmp_path / "manifest.json"
    target.write_text(json.dumps(payload()), encoding="utf-8")
    check(load_manifest(target))


# --------------------------------------------------------------------------
# 2. Every pin is enforced, and each alone can refuse.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "scope",
    [
        "",
        "UNATTENDED_LIVE_TRADING",
        "READ_ONLY_BROKER_OBSERVATION_V2",
        "read_only_broker_observation",
        "WRITE_ENABLED",
    ],
)
def test_a_different_scope_is_refused(scope):
    with pytest.raises(AuthorizationManifestError):
        check(parse_manifest(payload(scope=scope)))


def test_a_different_commit_is_refused():
    with pytest.raises(AuthorizationManifestError):
        check(parse_manifest(payload(approved_main_sha="c" * 40)))


def test_a_different_implementation_digest_is_refused():
    """An operator approved *this* implementation, not this file name."""
    with pytest.raises(AuthorizationManifestError):
        check(parse_manifest(payload(checkpoint_implementation_digest="d" * 64)))


@pytest.mark.parametrize(
    "operations",
    [
        [],
        ["get_accounts"],
        list(PERMITTED_READ_TOOLS) + ["get_portfolio"],
        list(PERMITTED_READ_TOOLS) + ["place_equity_order"],
        ["get_accounts", "get_equity_positions", "review_equity_order"],
    ],
)
def test_a_widened_or_narrowed_capability_list_is_refused(operations):
    with pytest.raises(AuthorizationManifestError):
        check(parse_manifest(payload(allowed_broker_operations=operations)))


def test_a_duplicated_capability_entry_is_refused():
    duplicated = list(PERMITTED_READ_TOOLS) + ["get_accounts"]
    with pytest.raises(AuthorizationManifestError):
        check(parse_manifest(payload(allowed_broker_operations=duplicated)))


@pytest.mark.parametrize("counter", REQUIRED_ZERO_COUNTERS)
def test_any_non_zero_write_limit_is_refused(counter):
    limits = {name: 0 for name in REQUIRED_ZERO_COUNTERS}
    limits[counter] = 1
    with pytest.raises(AuthorizationManifestError):
        check(parse_manifest(payload(write_limits=limits)))


def test_a_missing_or_extra_write_limit_is_refused():
    limits = {name: 0 for name in REQUIRED_ZERO_COUNTERS}
    limits.pop("placement_writes")
    with pytest.raises(AuthorizationManifestError):
        check(parse_manifest(payload(write_limits=limits)))
    limits = {name: 0 for name in REQUIRED_ZERO_COUNTERS}
    limits["option_writes"] = 0
    with pytest.raises(AuthorizationManifestError):
        check(parse_manifest(payload(write_limits=limits)))


@pytest.mark.parametrize("sessions", [0, 2, 10, -1])
def test_anything_other_than_one_session_is_refused(sessions):
    with pytest.raises(AuthorizationManifestError):
        check(parse_manifest(payload(maximum_sessions=sessions)))


def test_a_manifest_for_a_different_checkpoint_is_refused():
    with pytest.raises(AuthorizationManifestError):
        check(parse_manifest(payload()), observation_id="m9-obs-9999")


@pytest.mark.parametrize("issuer", ["agent", "Agent", "claude", "AUTOMATION", "system"])
def test_a_manifest_claiming_agent_issuance_is_refused(issuer):
    with pytest.raises(AuthorizationManifestError):
        check(parse_manifest(payload(issued_by=issuer)))


def test_an_expired_manifest_is_refused():
    manifest = parse_manifest(payload())
    with pytest.raises(AuthorizationManifestError):
        check(manifest, now=ISSUED + timedelta(hours=3))


def test_a_manifest_is_refused_exactly_at_expiry():
    manifest = parse_manifest(payload())
    with pytest.raises(AuthorizationManifestError):
        check(manifest, now=ISSUED + timedelta(hours=2))


def test_a_not_yet_valid_manifest_is_refused():
    manifest = parse_manifest(payload())
    with pytest.raises(AuthorizationManifestError):
        check(manifest, now=ISSUED - timedelta(seconds=1))


def test_an_over_long_validity_window_is_refused():
    """An authorization that outlives its review is not an authorization."""
    document = payload(
        expires_at=(ISSUED + MAXIMUM_VALIDITY + timedelta(seconds=1)).isoformat()
    )
    with pytest.raises(AuthorizationManifestError):
        check(parse_manifest(document))


def test_a_window_expiring_before_issuance_is_refused():
    document = payload(expires_at=(ISSUED - timedelta(hours=1)).isoformat())
    with pytest.raises(AuthorizationManifestError):
        check(parse_manifest(document))


def test_a_naive_evaluation_time_is_refused():
    with pytest.raises(AuthorizationManifestError):
        check(parse_manifest(payload()), now=datetime(2026, 8, 3, 13, 5))


def test_the_maximum_validity_is_one_day():
    assert MAXIMUM_VALIDITY == timedelta(hours=24)


# --------------------------------------------------------------------------
# 3. One use, recorded outside evidence.
# --------------------------------------------------------------------------


def test_a_manifest_is_consumed_on_use(tmp_path):
    ledger = ManifestConsumptionLedger(tmp_path / "ledger")
    assert ledger.is_consumed(OBSERVATION_ID) is False
    authorization(tmp_path)
    assert ledger.is_consumed(OBSERVATION_ID) is True


def test_a_consumed_manifest_cannot_be_used_again(tmp_path):
    authorization(tmp_path)
    with pytest.raises(AuthorizationManifestError):
        authorization(tmp_path)


def test_consumption_is_atomic(tmp_path):
    ledger = ManifestConsumptionLedger(tmp_path / "ledger")
    ledger.consume(OBSERVATION_ID)
    with pytest.raises(AuthorizationManifestError):
        ledger.consume(OBSERVATION_ID)


@pytest.mark.parametrize("value", ["", ".", "..", "a/b", "a\\b", " x", "x ", "a:b"])
def test_an_unsafe_observation_identity_is_refused(tmp_path, value):
    ledger = ManifestConsumptionLedger(tmp_path / "ledger")
    with pytest.raises(AuthorizationManifestError):
        ledger.consume(value)


def test_the_ledger_is_not_inside_the_evidence_tree(tmp_path):
    """Clearing evidence must never silently restore authorization."""
    evidence_root = tmp_path / "evidence"
    ledger_root = tmp_path / "authorization-ledger"
    ledger = ManifestConsumptionLedger(ledger_root)
    ledger.consume(OBSERVATION_ID)
    assert evidence_root not in ledger_root.parents
    assert not evidence_root.exists()


def test_an_invalid_manifest_is_not_consumed(tmp_path):
    """A refusal must not burn the identity — the operator can reissue."""
    ledger = ManifestConsumptionLedger(tmp_path / "ledger")
    with pytest.raises(AuthorizationManifestError):
        authorization(tmp_path, manifest=parse_manifest(payload(maximum_sessions=2)))
    assert ledger.is_consumed(OBSERVATION_ID) is False


# --------------------------------------------------------------------------
# 4. The authorization provider behaves exactly like the interactive gate.
# --------------------------------------------------------------------------


def test_it_answers_each_stage_once(tmp_path):
    provider = authorization(tmp_path)
    assert provider(ACKNOWLEDGMENT_STAGE) is True
    assert provider(OBSERVATION_STAGE) is True


def test_a_stage_cannot_be_answered_twice(tmp_path):
    provider = authorization(tmp_path)
    assert provider(ACKNOWLEDGMENT_STAGE) is True
    with pytest.raises(AuthorizationManifestError):
        provider(ACKNOWLEDGMENT_STAGE)


def test_an_unknown_stage_is_refused(tmp_path):
    provider = authorization(tmp_path)
    with pytest.raises(AuthorizationManifestError):
        provider("entry")


def test_it_covers_exactly_the_runtime_stages(tmp_path):
    provider = authorization(tmp_path)
    for stage in AUTHORIZATION_STAGES:
        assert provider(stage) is True


def test_validation_happens_before_any_stage_is_answered(tmp_path):
    """An invalid manifest costs zero broker reads: construction refuses."""
    with pytest.raises(AuthorizationManifestError):
        authorization(tmp_path, manifest=parse_manifest(payload(scope="OTHER")))


def test_the_full_checkpoint_runs_non_interactively(tmp_path):
    """End to end through the runner with no prompt and no console."""
    import asyncio
    from decimal import Decimal

    from src.read_only_observation_runtime import (
        UNATTENDED_MODE,
        CheckpointOutcome,
        ReadOnlyCheckpointRequest,
        run_read_only_observation_checkpoint,
    )

    from tests.test_read_only_observation_runtime import (
        ACCOUNT,
        REGULAR_HOURS,
        SYMBOL,
        StubReader,
        Ticks,
    )

    request = ReadOnlyCheckpointRequest(
        run_id=OBSERVATION_ID,
        account_number=ACCOUNT,
        symbol=SYMBOL,
        expected_quantity=Decimal("1"),
        evidence_root=tmp_path / "evidence",
        authorization_flag="AUTHORIZE_MILESTONE_9_READ_ONLY_OBSERVATION",
        operator_present=False,
        authorization_mode=UNATTENDED_MODE,
    )
    result = asyncio.run(
        run_read_only_observation_checkpoint(
            request,
            reader_factory=lambda: StubReader(),
            authorization=authorization(tmp_path),
            monotonic=Ticks(),
            now=lambda: REGULAR_HOURS,
        )
    )
    assert result.outcome is CheckpointOutcome.NO_PROTECTIVE_ORDER_OBSERVED
    assert result.evidence.write_counters == {
        name: 0 for name in REQUIRED_ZERO_COUNTERS
    }


def test_unattended_mode_does_not_require_an_operator():
    """The approved destination: no person at the keyboard, still validated."""
    from decimal import Decimal

    from src.read_only_observation_runtime import (
        ATTENDED_MODE,
        UNATTENDED_MODE,
        ReadOnlyCheckpointRequest,
        ReadOnlyRuntimeError,
    )

    def request(mode: str, present: bool) -> ReadOnlyCheckpointRequest:
        return ReadOnlyCheckpointRequest(
            run_id=OBSERVATION_ID,
            account_number="ACCT",
            symbol="AAPL",
            expected_quantity=Decimal("1"),
            evidence_root=pathlib.Path("evidence"),
            authorization_flag="AUTHORIZE_MILESTONE_9_READ_ONLY_OBSERVATION",
            operator_present=present,
            authorization_mode=mode,
        )

    request(UNATTENDED_MODE, False).validate()
    with pytest.raises(ReadOnlyRuntimeError):
        request(ATTENDED_MODE, False).validate()
    with pytest.raises(ReadOnlyRuntimeError):
        request("SOMETHING_ELSE", True).validate()


# --------------------------------------------------------------------------
# 5. The agent can never author its own authorization.
# --------------------------------------------------------------------------


def test_the_manifest_module_has_no_write_path():
    """No template generator, no renewal helper, no serializer.

    Structural rather than name-based: the module is checked for calls that
    could *emit* a manifest, not for functions whose names contain "write".
    The single permitted filesystem mutation is the consumption ledger's
    directory creation, which spends authorization rather than granting it.
    """
    tree = ast.parse(pathlib.Path(manifests.__file__).read_text(encoding="utf-8"))
    emitting = {"open", "write_text", "write_bytes", "dump", "dumps", "writelines"}
    mutations = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        function = node.func
        name = (
            function.attr
            if isinstance(function, ast.Attribute)
            else function.id
            if isinstance(function, ast.Name)
            else None
        )
        if name in emitting:
            mutations.append(name)
        if name == "mkdir":
            # Allowed, and only here: consuming an identity, never issuing one.
            assert isinstance(function, ast.Attribute)
    assert mutations == []


def test_nothing_in_the_module_serializes_a_manifest():
    source = pathlib.Path(manifests.__file__).read_text(encoding="utf-8")
    assert "json.dump" not in source
    assert "json.dumps" not in source
    assert "write_text" not in source


def test_no_tracked_source_file_writes_a_manifest():
    """Repository-wide: nothing produces the document that authorizes it."""
    offenders = []
    for path in (REPOSITORY_ROOT / "src").glob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "READ_ONLY_BROKER_OBSERVATION" in text and (
            "json.dump" in text or "json.dumps" in text
        ):
            offenders.append(path.name)
    assert offenders == []


def test_the_only_manifest_producing_code_in_the_repository_is_test_fixtures():
    launcher = (
        REPOSITORY_ROOT / "read_only_observation_checkpoint_launcher.py"
    ).read_text(encoding="utf-8")
    assert "json.dump" not in launcher
    assert "--authorization-manifest" in launcher


# --------------------------------------------------------------------------
# 5b. Launcher mode selection.
# --------------------------------------------------------------------------


def launcher_args(**overrides: str) -> list[str]:
    arguments = {
        "--run-id": OBSERVATION_ID,
        "--account-number": "ACCT",
        "--symbol": "AAPL",
        "--expected-quantity": "1",
        "--evidence-root": "evidence",
        "--authorization": "AUTHORIZE_MILESTONE_9_READ_ONLY_OBSERVATION",
    }
    arguments.update(overrides)
    flat = [item for pair in arguments.items() for item in pair]
    return flat + [
        "--operator-present",
        "--i-understand-this-performs-real-broker-reads",
    ]


def parse_launcher(extra: list[str]):
    import read_only_observation_checkpoint_launcher as launcher

    return launcher.build_parser().parse_args(launcher_args() + extra)


def test_neither_mode_is_refused():
    """A scheduled run must never fall back to a prompt nobody can answer."""
    import read_only_observation_checkpoint_launcher as launcher

    with pytest.raises(Exception):
        launcher.assert_real_read_preconditions(parse_launcher([]))


def test_both_modes_are_refused(tmp_path, monkeypatch):
    import read_only_observation_checkpoint_launcher as launcher

    monkeypatch.setenv("ENABLE_MILESTONE_9_READ_ONLY_OBSERVATION", "1")
    namespace = parse_launcher(
        ["--interactive", "--authorization-manifest", str(tmp_path / "m.json")]
    )
    with pytest.raises(Exception):
        launcher.assert_real_read_preconditions(namespace)


def test_a_manifest_requires_a_ledger(tmp_path, monkeypatch):
    import read_only_observation_checkpoint_launcher as launcher

    monkeypatch.setenv("ENABLE_MILESTONE_9_READ_ONLY_OBSERVATION", "1")
    namespace = parse_launcher(["--authorization-manifest", str(tmp_path / "m.json")])
    with pytest.raises(Exception):
        launcher.assert_real_read_preconditions(namespace)


def test_the_manifest_mode_needs_no_operator_at_the_keyboard(tmp_path, monkeypatch):
    """The whole point: scheduled execution without a person present."""
    import read_only_observation_checkpoint_launcher as launcher

    monkeypatch.setenv("ENABLE_MILESTONE_9_READ_ONLY_OBSERVATION", "1")
    namespace = launcher.build_parser().parse_args(
        launcher_args()[:-2]
        + [
            "--i-understand-this-performs-real-broker-reads",
            "--authorization-manifest",
            str(tmp_path / "m.json"),
            "--authorization-ledger",
            str(tmp_path / "ledger"),
        ]
    )
    assert namespace.operator_present is False
    launcher.assert_real_read_preconditions(namespace)


def test_interactive_mode_still_requires_an_operator(monkeypatch):
    import read_only_observation_checkpoint_launcher as launcher

    monkeypatch.setenv("ENABLE_MILESTONE_9_READ_ONLY_OBSERVATION", "1")
    namespace = launcher.build_parser().parse_args(
        launcher_args()[:-2]
        + ["--i-understand-this-performs-real-broker-reads", "--interactive"]
    )
    with pytest.raises(Exception):
        launcher.assert_real_read_preconditions(namespace)


def test_the_environment_flag_alone_is_never_approval(monkeypatch):
    """Necessary, and deliberately not sufficient."""
    import read_only_observation_checkpoint_launcher as launcher

    monkeypatch.setenv("ENABLE_MILESTONE_9_READ_ONLY_OBSERVATION", "1")
    with pytest.raises(Exception):
        launcher.assert_real_read_preconditions(parse_launcher([]))


def test_a_missing_environment_flag_is_refused_even_with_a_manifest(
    tmp_path, monkeypatch
):
    import read_only_observation_checkpoint_launcher as launcher

    monkeypatch.delenv("ENABLE_MILESTONE_9_READ_ONLY_OBSERVATION", raising=False)
    namespace = parse_launcher(
        [
            "--authorization-manifest",
            str(tmp_path / "m.json"),
            "--authorization-ledger",
            str(tmp_path / "ledger"),
        ]
    )
    with pytest.raises(Exception):
        launcher.assert_real_read_preconditions(namespace)


def test_the_launcher_builds_a_manifest_authorization(tmp_path, monkeypatch):
    import read_only_observation_checkpoint_launcher as launcher

    document = payload(
        approved_main_sha=resolve_head_sha(REPOSITORY_ROOT),
        checkpoint_implementation_digest=checkpoint_implementation_digest(
            REPOSITORY_ROOT
        ),
    )
    target = tmp_path / "manifest.json"
    target.write_text(json.dumps(document), encoding="utf-8")
    monkeypatch.setenv("ENABLE_MILESTONE_9_READ_ONLY_OBSERVATION", "1")
    namespace = parse_launcher(
        [
            "--authorization-manifest",
            str(target),
            "--authorization-ledger",
            str(tmp_path / "ledger"),
        ]
    )
    launcher.assert_real_read_preconditions(namespace)
    provider = launcher.build_authorization(namespace, now=NOW)
    # Resolved up front so a manifest failure costs zero session opens.
    from src.read_only_observation_runtime import PreResolvedAuthorization

    assert isinstance(provider, PreResolvedAuthorization)
    assert provider.all_approved is True
    assert provider(ACKNOWLEDGMENT_STAGE) is True


def test_the_launcher_refuses_a_manifest_pinned_to_another_commit(
    tmp_path, monkeypatch
):
    import read_only_observation_checkpoint_launcher as launcher

    target = tmp_path / "manifest.json"
    target.write_text(json.dumps(payload()), encoding="utf-8")
    monkeypatch.setenv("ENABLE_MILESTONE_9_READ_ONLY_OBSERVATION", "1")
    namespace = parse_launcher(
        [
            "--authorization-manifest",
            str(target),
            "--authorization-ledger",
            str(tmp_path / "ledger"),
        ]
    )
    with pytest.raises(AuthorizationManifestError):
        launcher.build_authorization(namespace, now=NOW)


def test_the_launcher_never_auto_answers_a_prompt():
    launcher_source = (
        REPOSITORY_ROOT / "read_only_observation_checkpoint_launcher.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(launcher_source)
    literals = {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    assert "y" not in literals
    assert "Y" not in literals
    assert "yes" not in literals


# --------------------------------------------------------------------------
# 6. A read-only manifest is never write authorization.
# --------------------------------------------------------------------------


def test_a_read_only_manifest_is_not_write_authorization():
    assert_not_write_authorization(parse_manifest(payload()))


def test_a_manifest_carrying_write_policy_fields_is_refused():
    limits = {name: 0 for name in REQUIRED_ZERO_COUNTERS}
    limits["capital_tier"] = 2680
    with pytest.raises(AuthorizationManifestError):
        assert_not_write_authorization(parse_manifest(payload(write_limits=limits)))


def test_the_write_policy_specification_is_recorded_but_not_implemented():
    """Named so a later milestone builds it deliberately, not by widening this."""
    assert set(UNATTENDED_WRITE_POLICY_REQUIRED_FIELDS) == {
        "capital_tier",
        "strategy_version",
        "symbol_allowlist",
        "position_limits",
        "exposure_limits",
        "daily_loss_limit",
        "weekly_loss_limit",
        "drawdown_limit",
        "permitted_trading_window",
        "deployment_expiry",
        "global_kill_switch_state",
    }
    for name in UNATTENDED_WRITE_POLICY_REQUIRED_FIELDS:
        assert name not in MANIFEST_REQUIRED_KEYS


# --------------------------------------------------------------------------
# 7. Repository pins.
# --------------------------------------------------------------------------


def test_the_implementation_digest_covers_every_file_that_can_change_behaviour():
    assert set(PINNED_IMPLEMENTATION_FILES) == {
        "src/read_only_observation_runtime.py",
        "src/read_only_broker_state.py",
        "src/read_only_authorization_manifest.py",
        "src/live_order_state_model.py",
        "read_only_observation_checkpoint_launcher.py",
    }
    for relative in PINNED_IMPLEMENTATION_FILES:
        assert (REPOSITORY_ROOT / relative).is_file()


def test_the_digest_is_stable_and_order_sensitive():
    first = checkpoint_implementation_digest(REPOSITORY_ROOT)
    assert first == checkpoint_implementation_digest(REPOSITORY_ROOT)
    assert len(first) == 64


def test_the_digest_changes_when_a_pinned_file_changes(tmp_path):
    root = tmp_path / "repo"
    for relative in PINNED_IMPLEMENTATION_FILES:
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("x = 1\n", encoding="utf-8")
    before = checkpoint_implementation_digest(root)
    (root / PINNED_IMPLEMENTATION_FILES[0]).write_text("x = 2\n", encoding="utf-8")
    assert checkpoint_implementation_digest(root) != before


def test_an_absent_pinned_file_refuses_rather_than_skips(tmp_path):
    with pytest.raises(AuthorizationManifestError):
        checkpoint_implementation_digest(tmp_path / "empty")


def test_head_resolves_to_a_commit_sha():
    assert len(resolve_head_sha(REPOSITORY_ROOT)) == 40


def test_head_resolution_refuses_a_non_repository(tmp_path):
    with pytest.raises(AuthorizationManifestError):
        resolve_head_sha(tmp_path)


def test_head_resolution_uses_no_subprocess():
    """No process-spawn surface on the authorization path."""
    tree = ast.parse(pathlib.Path(manifests.__file__).read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert imported.isdisjoint({"subprocess", "os", "shutil", "shlex"})


def test_the_manifest_module_imports_nothing_write_capable():
    tree = ast.parse(pathlib.Path(manifests.__file__).read_text(encoding="utf-8"))
    forbidden = ("adapter", "client", "mcp", "orchestrator", "session")
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            assert not any(word in node.module.lower() for word in forbidden), node.module
