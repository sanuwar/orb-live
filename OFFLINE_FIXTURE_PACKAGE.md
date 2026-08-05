# Offline fixture package

`src/offline_fixture_package.py` provides a small typed manifest for the fixed
synthetic offline scenario catalog. It packages only scenario identity, runner
and correlation IDs, an injected session date, and ordered injected timestamps.

It does not contain provider results, account data, symbols, quantities, prices,
balances, credentials, endpoints, signing keys, database paths, or exception
text. It does not persist anything and cannot authorize automated action.

The package is intended to remove repeated fixture metadata from offline CLI,
runner, monitoring, alert, acknowledgment, and readiness callers. Those callers
still own their reviewed public state machines and durable stores.

```python
from src.offline_fixture_package import FixtureScenario, build_fixture

fixture = build_fixture(FixtureScenario.SAFE_OFFLINE_COMPLETION)
manifest = fixture.serialize_manifest()
```

Loading accepts only the exact allowlisted JSON schema. Callers must validate a
fixture against their own injected evaluation time and freshness policy before
using it as evidence. The manifest digest detects content tampering; it is an
integrity binding, not authentication or authorization.
