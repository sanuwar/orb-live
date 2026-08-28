# M53 Execution Isolation and Historical M52 Handoff Plan

## Historical-chain M52 input

The only permitted later input is the original historical M52 artifact at `artifacts/research-cross-sectional-momentum/results/m52_result.json`, consumed by the M53 wrapper's fixed path (`scripts/research_momentum_risk_control.py:62,122-129`).  Read-only verification in this workstream found SHA-256 `590672f029a519b665b856106d0b35d701b464aa3f12b5a0b1978f18b18ac8f9` and `34124` bytes.  Before a later handoff, require valid UTF-8 JSON, the same pre-copy hash and byte count, a binary copy (`Copy-Item -LiteralPath` without transform) to `<scratch-m53>/artifacts/research-cross-sectional-momentum/results/m52_result.json`, and the same post-copy and post-placement hash/size.  Record the source and destination paths, copy command, UTC times, and both hashes in the evidence package.

This is a historical-chain control: it preserves the exact artifact M53 historically consumed and keeps copied `/baseline_parity/m52_generated_at` stable.  The preserved regenerated M52 artifact is deliberately not selected because it has a different generated-at value and M52 remains `EXACT_BYTE_REPRODUCTION_FAIL` plus `RETROSPECTIVE_SEMANTIC_PAYLOAD_MATCH`; choosing the historical artifact neither erases nor reclassifies that failure.

## Cache, network, and environment plan

Only scratch copies of the three required cache trees may be exposed to M53.  The original trees must never be passed, linked, or otherwise exposed directly to the command.

| Cache tree | Files | Bytes | Aggregate SHA-256 |
|---|---:|---:|---|
| `daily-bar-cache` | 942 | 177025917 | `0698d51e8f3c1d599e5d85884fa192b2abf8bfde1cd8495574404681626ebf8d` |
| `sec-nport-cache` | 29 | 25130891 | `889fb5dc0a8b5d5f6ecf6693cd48bb0beae62cd38524fa73c308f44cf546c4fc` |
| `openfigi-cache` | 5243 | 786008 | `b2ad68c940fe3854bca92918290a94211a056cee06887e1e79a0a9020dd5ef86` |

The aggregate algorithm is SHA-256 over lexicographically sorted UTF-8 lines `SHA256(relative_path_utf8):byte_size:SHA256(file_bytes)`, joined with LF.  Hash original read-only baseline before copy, scratch after copy and immediately before the command, then scratch and original immediately after.  Any changed count, total bytes, digest, cache miss, credential request, network attempt, or mutation is a stop without retry.

The static source trace confirms that the authorized M53 path passes the one `fetch_missing` value from the M53 CLI to all M52 helper fetch paths: SEC-list, SEC-document, OpenFIGI, and daily-bar requests.  Every cache miss is rejected before its `do_fetch` callback when false (`research_cross_sectional_momentum.py:101-110,158-187,314-359,387-399,469-473`; M53 forwarding at `research_momentum_risk_control.py:192-206,260-263`).  The indirect `research_capital_target_strategy_family.py` import is in the static closure but is not called by this M53 path.

The later child environment must be constructed as an explicit allowlist, not inherited wholesale.  At minimum it must remove `ALPACA_API_KEY`, `ALPACA_SECRET_KEY`, `ALPACA_API_KEY_ID`, `ALPACA_API_SECRET_KEY`, `APCA_API_KEY_ID`, `APCA_API_SECRET_KEY`, `ALPACA_BASE_URL`, `ALPACA_FEED`, `HTTP_PROXY`, `HTTPS_PROXY`, `ALL_PROXY`, `NO_PROXY`, `http_proxy`, `https_proxy`, `all_proxy`, and `no_proxy`; record the resulting name-only allowlist and pass that environment explicitly to the launcher and every child it starts.  The source directly reads only `ALPACA_API_KEY_ID` and `ALPACA_API_SECRET_KEY` on its fetch branches, but all listed conventional aliases are cleared defensively.

**Blocking limitation: no concrete authorized network-denial/monitoring mechanism is frozen.**  The requirement that a network monitor/log be active is not executable because it does not name a tool, invocation, process coverage, evidence output, pass/fail condition, or unavailable-tool behavior.  Therefore no future M53 run is authorized under this package.  A separately reviewed correction must name an available mechanism that is started before the launcher, covers the launcher and descendants, records a durable process/connection log, treats any non-loopback connection attempt as failure, and blocks before invocation if the mechanism cannot start or its log cannot be retained.  It must not be selected after an output is observed.

Predeclare later evidence: OS/build; Python executable/version; requirements.txt hash (historical Git-object SHA-256 `42ca9151dd474cfd4fb1ebb6e7d029df05e148f331e9659a6191442b133059e5`); relevant installed package versions; locale/timezone; clone-local Git EOL settings; source closure hashes; parameter digest; cache digests; exact command; parent/child environment allowlist; start/end times; output/log paths; M52 copy hashes; and network-monitor result.  This records a later environment; it does not claim to recreate the unknown historical environment.
