# Execution Environment

## Current rerun environment

| Field | Recorded value |
|---|---|
| Current main / pilot start SHA | a89a47b71c6d1165f2213c669824256f7ec86ed6 |
| M52 worktree commit | 3d345ebca19c7159678aaad801cdb66299f9ba6b |
| M53 worktree commit | 4c43fe96ab80d739fd10a9bd6bfa4c1319f67865 |
| Operating system | Microsoft Windows 10 10.0.19045, x64 |
| Python executable | C:\dev\orb-live\.venv\Scripts\python.exe |
| Python version | Python 3.12.10 |
| requirements.txt SHA-256 | 8c28d644086fa38ba74d3a73a0d7d55275536646fb57747fa579a285023bf564 |
| Root project lockfile | none present |
| Cache mode | --fetch-missing omitted; default false |
| Deliberately cleared for M52 child | ALPACA_API_KEY, ALPACA_SECRET_KEY, ALPACA_API_KEY_ID, ALPACA_API_SECRET_KEY, APCA_API_KEY_ID, APCA_API_SECRET_KEY, ALPACA_BASE_URL, ALPACA_FEED |
| Isolation | detached worktrees under C:\tmp\orb-live-p3b-phase2b1-20260826T000000Z; ordinary recursive copies, no symlinks or junctions |

Relevant installed package versions: pytest 9.1.1; PyYAML 6.0.3; python-dotenv 1.2.2; httpx 0.28.1; pydantic 2.13.4; starlette 1.3.1; uvicorn 0.51.0. No package was installed, removed, or updated.

## Independent-review source-byte finding

The detached M52 worktree had `core.autocrlf=true` and no root `.gitattributes`. Its `scripts/research_cross_sectional_momentum.py` was therefore 34,521 bytes with 674 CRLF endings and SHA-256 `ee5276b67febba75792faf4a7a72d05720145bce25ac9a512017d7f7b724a187`. The historical Git object is 33,847 bytes with 674 LF endings and SHA-256 `411391271c0e8adf5f866233fe9f9ebc9515eddd99adb5dd8bb31cee651dff66`; LF-normalized contents are identical. This explains the changed source-hash provenance fields in the regenerated result without implying an intentional source or frozen-parameter edit.

## Commands and timing

M52 command, executed from its detached historical worktree:

    C:\dev\orb-live\.venv\Scripts\python.exe -m scripts.research_cross_sectional_momentum

No arguments were supplied. Attempt 1 began 2026-08-26T21:17:39.9095773Z and was externally terminated by the command wrapper at 60 seconds while reading cached daily bars; it produced no result file. Cache integrity was rechecked. Unchanged attempt 2 began 2026-08-26T21:20:14.3388630Z, completed 2026-08-26T21:21:07.1395535Z, and exited 0. Its scratch-only log is preserved outside the repository. No M53 command was authorized.

## Provenance limitation

This records the current rerun environment only. It does not claim to recreate the historical OS image, Python build, dependency resolution, clock, locale, filesystem behaviour, or process state. The historical CLIs record execution-time metadata, so mismatch must not be attributed to caches without separately authorized diagnosis.
