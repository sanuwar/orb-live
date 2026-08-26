# M52 Regeneration Result

| Field | Value |
|---|---|
| Historical source commit | 3d345ebca19c7159678aaad801cdb66299f9ba6b |
| Exact command | C:\dev\orb-live\.venv\Scripts\python.exe -m scripts.research_cross_sectional_momentum |
| Fetch state | --fetch-missing omitted; false by default |
| Expected SHA-256 | 590672f029a519b665b856106d0b35d701b464aa3f12b5a0b1978f18b18ac8f9 |
| Observed SHA-256 | a5c604ab63e145ef2e8878677aabcc2e4cb8694082bd8edf5de1c4e6360f9efd |
| Historical CLI exit | 0 |
| Result | FAIL — exact hash mismatch |
| M53 handoff authorized | No |

The observed hash is for scratch-only regenerated m52_result.json, which is not committed. A successful CLI exit does not pass the predeclared byte-equality gate.

## Warnings and limitations

- The first identical invocation was externally terminated at 60 seconds while walking cache-only daily bars, before producing a result. Integrity checks then passed and the unchanged command completed once.
- The CLI printed a generic “Fetching” status while entering cache helpers. --fetch-missing remained omitted; every required cache entry was present; no credential was requested and no cache-miss/fetch error occurred. This does not reinterpret the status string as a market-data fetch.
- The mismatch is a reproducibility finding, not a performance finding and not proof of a cache defect. Environment provenance, execution-time metadata, filesystem behaviour, and dependencies remain possible causes.
- No M52 result or manifest was copied into M53.
