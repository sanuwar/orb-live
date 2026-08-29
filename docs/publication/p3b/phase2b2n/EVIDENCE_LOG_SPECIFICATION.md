# Evidence Log Specification

## File and durability

The guard creates a fresh exclusive (`O_CREAT|O_EXCL`, mode `0600`) UTF-8 JSON Lines file at the explicit absolute path and never appends to an unknown prior record. The path must be absolute, named `m53-network-guard.jsonl`, must not already exist, must not be a symlink, and its parent must be an existing non-symlink absolute directory. Each security-relevant record is written, `flush`ed, and `os.fsync`ed before the operation that produced it is allowed to continue or is refused; a failed `fsync` or write raises `GuardConfigurationError` and fails the run closed. Creation, append, configuration, activation, and logging failures all fail closed. The file must be retained by the future launcher.

## Record format

Every record contains `event`, `guard_version`, `guard_sha256`, `sitecustomize_sha256`, `timestamp_utc`, `pid`, `ppid`, `previous_hash`, and `event_hash`. `event_hash` is SHA-256 of the canonical UTF-8 JSON serialization of the record excluding `event_hash`, with `sort_keys=true`, separators `(',', ':')`, and `ensure_ascii=true`; `previous_hash` begins as 64 zeroes and thereafter carries the preceding record's `event_hash`. Both guard-file digests appear on **every** record, so a launcher can confirm the whole run was produced by the externally verified guard pair.

## Events

- `activation` — configuration and bootstrap verification accepted. Reports `socket_rules_loaded=false` and `subprocess_rules_loaded=false`, because no control is installed yet.
- `guard_ready` — **the affirmative readiness record.** Written only after the audit hook, the `subprocess.Popen` wrapper, all socket monkeypatches, and the `multiprocessing` block are installed. Reports `audit_hook_installed`, `socket_rules_loaded`, `subprocess_rules_loaded`, and `multiprocessing_rules_loaded` as `true`.
- `allowed_subprocess`, `denied_subprocess`, `denied_network`, `denied_environment`, `guard_failure`, `normal_interpreter_exit`.

`activation` records executable basename, executable content and path digests, the pinned Git content digest, a working-directory scope plus digest, the rule-loaded booleans, and cleared sensitive environment variable **names only**. Exact approved Git argv are recorded verbatim; any denied argv is represented only by argument count and SHA-256. Values of credentials, tokens, proxy settings, cache content, market data, usernames, absolute paths, and unrelated environment variables are never recorded.

## Verification

`verify_evidence_log()` requires a final newline, parses each JSONL line, rejects an unparsable or truncated record, recalculates the canonical SHA-256, and validates the chain link by link. An empty file is a failure.

`verify_guard_run()` is the launcher-grade check and is the one a future M53 run must use. It fails closed when the file is **absent**, the chain is broken or **truncated**, the first record is not `activation`, no `guard_ready` record is present, any `guard_failure` record is present, **any** `denied_*` record is present, or the final record is not `normal_interpreter_exit`. An `os._exit(78)` activation failure deliberately skips `atexit`, so a failed launch can never produce a terminal record.

## What the hash chain can and cannot prove

It **can** prove internal consistency and self-contained tamper evidence: any edit, reordering, deletion, or truncation of a record invalidates the chain unless every following hash is recomputed. Combined with the required terminal record, a partial deletion is detectable.

It **cannot** prove authenticity. The guard holds no private key and performs no external attestation, so an actor with write access to the finished log can rewrite it end to end into a self-consistent chain — including one that omits denied events. The chain is therefore evidence against accidental loss, truncation, and casual edits, not a defence against an actor who controls the evidence directory. Anything stronger requires an external append-only sink or a signing key, neither of which is in this scope.
