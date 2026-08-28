# M53 Source Closure and Materialization

## Closure rule and immutable identity

The static Python import closure starts with `scripts/research_momentum_risk_control.py` at historical commit `4c43fe96ab80d739fd10a9bd6bfa4c1319f67865`; it contains 30 tracked Python files.  This is a static source-load closure, not an execution claim.  Every row below has the historical Git blob (SHA-1) and SHA-256 of raw Git-object bytes.  All historical blobs are LF-only.  `raw required` means the isolated checkout must hash to the displayed raw value.  `CRLF provenance` means the expected M53 artifact instead records the specified checked-out CRLF byte hash, so raw-Git equivalence cannot simultaneously be required for that file.

| Path | Git blob | raw SHA-256 | bytes | Materialization / role |
|---|---|---|---:|---|
| scripts/research_momentum_risk_control.py | cf049fa9cae08a4948b59f220f9f97c02621e7f4 | 67091d5abc02f113b73057739d71317f96b9400f0be3da29a9c96ab46ba9e1c1 | 17964 | LF raw required; command wrapper and top-level provenance |
| scripts/research_cross_sectional_momentum.py | 2f2f8b89cec05b520345931f4fb395c69cb90a79 | 411391271c0e8adf5f866233fe9f9ebc9515eddd99adb5dd8bb31cee651dff66 | 33847 | CRLF provenance required: 34521 bytes / `ee5276b67febba75792faf4a7a72d05720145bce25ac9a512017d7f7b724a187`; M52 cache/parity layer |
| src/cross_sectional_momentum_research.py | 54e1d348fac9de6d9c0bb93dfcecff201ea0532f | 7cdecadf409ed5329204c6842dd61db56ee5c181883ea54f002ec1ae1428c969 | 53129 | CRLF provenance required: 54292 bytes / `cde0af38c8c3c91b5a2b33d51449b67a03129d0f8bcf59630056194cbd1ce352`; metrics/simulation |
| src/momentum_risk_control_research.py | a46f00534c9dc654ed6c02d24a5fe466e285dc3e | 038196ee99c9fbcd9fb176a0894e13fc5e4f595c2833f708e46cfe0efc5ac3cf | 7598 | LF raw required; M53 arms/selection/top-level provenance |
| src/orb_capital_target_strategy_family_research.py | 6d7fbc71eea9f39f533add75da59506dedb44a20 | 2653c75f86a12feb6801ca01e71109da204ed8171f5825fcd08667939bd7dba7 | 97201 | LF raw required; reused metrics primitive |
| scripts/research_capital_target_strategy_family.py | 80304ec4a86982dbadb529f683d142afc7431cea | 437328fc2f8b0ea1900ba8ee0e3d388dfc7bf38b208ef0097aa2f3ce7db62127 | 41550 | LF raw required; imported M52 support |
| src/live_shadow_session.py | 76a026a79d8292cfaed3ec9ed0af751dda1d7d78 | 6390a40411fe1a91ef8302d17e4e3ee4a2a4e8049468ded70c516094eb25ed12 | 39950 | LF raw required; import closure |
| src/market_data_provider.py | b8d2fe1c3f395cf8a1a61da2353242835ea40e9e | 4ac2e37c26ce33f7a9fcdd1d223306cffca708d7474a055615cecc5e4a376832 | 10826 | LF raw required; cache transport types |
| src/orb_research_historical_calendar.py | 6e35bde01405ed4e44d471706735db5fb782eab6 | ac2b72ea55cbc1bfeace24c4eb7fe27d4b0dcea9cce053ebfb61c281412dce4a | 22485 | LF raw required; historical calendar |
| src/orb_universe_expansion_research.py | 56c73a7c746493df5d59f81f3a12221eaa3eca2a | 48e310e2d2093c99d098ae490894a92e63c46d84e7d0a4f86febd879473548cd | 68293 | LF raw required; universe/cache helpers |
| src/market_data_canonical.py | 43504c4f1bce8528cb40c9c21336e068abe4c9fe | e8198c964d2a11989987f96c28ae9518548a52795392f5fb9c9c55b30403a89e | 18072 | LF raw required; canonical bars |
| src/exchange_calendar.py | 4692a2f40a86cd9c3c50a0df7c69696996769568 | 1f9ae8966be8e4b74ceffb6aa1902234cc7d2efe024855ed8583bc3b1bda17a9 | 17234 | LF raw required; calendar |
| src/config.py | 98c64f956e025734c9a07555463eae5df0f5923e | 37669854c3e1dae3c282e787c0af22da9c89ce21fb790a482f4edb2e2db7f1c3 | 3242 | LF raw required; imported path definition, no M53 config read |
| src/market_data.py | 244e391c7b5f74c08fe7e2cccb48cc7b78814698 | c91751a1b74a9954b7d3e551780ee91d836345f03af1184da11c845e4acbad0e | 10653 | LF raw required; imported market types |
| src/read_only_authorization_manifest.py | 37a8436fe96a759cc35427c577234c39accc96d5 | 3df52f33b701170910dd35758e5e50c9ed780ad5d315e8c47f468b4f60e929e5 | 30425 | LF raw required; indirect import |
| src/shadow_trading_decision.py | f8ab3a061b2e036860adaa782f83fd11eaf264a0 | f3fb14b95b08734ba90d0c23dd1fc085b0768eb5e1c86ed116987db14e1b3986 | 11993 | LF raw required; indirect import |
| src/signal_engine.py | 420f66b77c05e55a0e828a746319d17c876f2ef6 | 304d61de197a07ead668994e302a24c462970955f45db316f2950437d2efc4b1 | 5950 | LF raw required; indirect import |
| src/protection_observation_model.py | d4fa4984fff2e0799a1e8e6917c84a7ec9c4b8b9 | e8b2b87f14f253da5791b4ac397baccc4d9bb8c747ce07bd1bb1d2c2f7b5574a | 28708 | LF raw required; indirect import |
| src/robinhood_client.py | d10b6375866c1d4b0b6f270b8adaee68c09f3eb4 | 8e00f808dda3e8741a2fccc6ae5edb42deb4a3761af5eae39304c601cfa1f39e | 9684 | LF raw required; indirect import only; no invocation authorized |
| src/canonical_source_hash.py | 104019fb4e8794a589d879e08b88c0c0ebf27db7 | 28b4c9d5154310fcc686f3cfaee6ef3ec2d38a872f5c0da2fea1570fd179168c | 1123 | LF raw required; indirect import |
| src/read_only_observation_runtime.py | 78a67e9332979f76624c6c2a4ad596c06e2204b4 | 1e351f10c2ec78396e30e0643cb37bb9aa51892fdfb5f371a85f3a50ff2f4a7f | 61800 | LF raw required; indirect import |
| src/broker_identity.py | 180c614e6b8192be62088095089c5b87d7be65f0 | d5ffab0c3761385bf1c744ef5c67385c970cdec52584c7fa9f059d0563622812 | 7411 | LF raw required; indirect import |
| src/live_order_state_model.py | edbe93dc2ede0feb837d9cd5773a357902045d09 | 916d0ec0be99756e90d896eb4ccdcaa50568428aad30c367a6cf0f864c5ee084 | 17938 | LF raw required; indirect import |
| src/order_lifecycle_model.py | 171d0aa9273c142c000b939a28ea6b92c2676cb4 | ab329208beabd8986fab1a8a69fc50c34b4f7e952ff3ead1f1551166f663e17c | 26029 | LF raw required; indirect import |
| src/read_only_broker_state.py | 0c0c3f757197f29062c6e16dbf5042131d81b7fa | 660b0d80919b90dd540ec89a898965d367e5a96f2e7c4614faf46ebe25a4d88e | 30293 | LF raw required; indirect import |
| src/live_broker_capabilities.py | 51b80691286c45a308f3153a9e0c359fca109e2a | 8bcda01f8f8af531b6e60c8bee5df1fea1ab327f064f7c37d5abadb9840fde54 | 17372 | LF raw required; indirect import |
| src/live_order_contract.py | 89b10e0e57652fbad09ee70a952d93e70801580f | 6e3c35f39a2867af34cb05cf65a093cc7b39563dc3c28ff41e5d2392cddc6eff | 48043 | LF raw required; indirect import |
| src/broker_action_saga.py | 034d21cc54ece09425f8206b8c7c0e66b2095866 | fb0626a9157288640b077882c71714a774fc2aa8f058e35ded6a87f9e085319a | 103835 | LF raw required; indirect import |
| src/broker_snapshot.py | 63d41e6c8b6b24a4480f7a5d6868b0aca1cf7dee | 0dc401502b81d9df902e01cec9d7c57f221405316b24b848f5d3b69e69c083fd | 7018 | LF raw required; indirect import |
| src/trade_state.py | 1b8138af4d5d96bf06262f00074d8184b74f714c | 3bb5ccfe52c9bd0157a51ddd366b8fce7477a400892cde24bf247b76aaa7680b | 223441 | LF raw required; indirect import |

The expected artifact records the M53 wrapper (`67091dâ€¦`), M53 module (`038196â€¦`), cross-sectional module (`cde0afâ€¦`), and M52 wrapper (`ee5276â€¦`, repeated inside each Stage-0 provenance object).  The last two are exact CRLF materializations of their LF Git blobs; their normalized LF SHA-256 values are respectively `7cdecadfâ€¦1428c969` and `41139127â€¦651dff66`.  Normalization is permitted only for explaining those two Git-object differences; their checked-out raw bytes must match the recorded CRLF provenance values.

## Isolated materialization gate

Use a newly created isolated local clone or equivalent from the local object database; do not modify mainâ€™s Git config or working tree.  Before the historical checkout is materialized, set clone-local `core.autocrlf=false` and clone-local `.git/info/attributes` rules forcing **only** `scripts/research_cross_sectional_momentum.py` and `src/cross_sectional_momentum_research.py` to `text eol=crlf`.  All other closure files remain LF.  This produces the four expected provenance hashes without editing a tracked source file.  Then check out detached `4c43fe96â€¦`, verify every table row, verify the four output-recorded raw values, and require the source files hashed by the wrapper to be clean.

If the stated clone-local rules cannot reproduce the two CRLF provenance hashes while every other closure file matches its raw Git-object hash, execution is blocked as `BLOCKED_SOURCE_PROVENANCE`.  No checkout configuration is claimed to recreate the unknown historical environment; it is an evidence-controlled materialization chosen to satisfy the recorded source-byte provenance.
