# Offline shadow rehearsal

`scripts/run_offline_shadow_rehearsal.py` is a finite, deterministic integration
rehearsal for the existing offline stack. It creates fresh SQLite files beneath
one caller-supplied temporary directory, injects fixed timestamps, exercises
public component contracts, prints one compact JSON report, and exits.

Run it only with a new path outside the repository:

```text
python scripts/run_offline_shadow_rehearsal.py --root <new-temporary-directory>
```

The ten scenarios cover bounded completion, operator review, active suspension,
ambiguous runner evidence, missed heartbeat, unconfirmed alert delivery,
operator awareness without clearance, readiness remaining blocked, stale
evidence, and restart recovery. The completion, review, suspension, and restart
paths use the real runner, driver, orchestrator, scheduler, controller, and CLI
persistence. Monitoring, alert, and acknowledgment scenarios use their real
durable stores. The readiness scenario consumes bound public projections.

The report contains only scenario names, fixed states, relative durable
filenames, booleans, and the global false safety flags. It never reports live or
unattended readiness, never enables automated action, and never accesses a
broker, credential, manifest, account, network client, or repository `data/`.

The rehearsal is not a daemon, scheduler, service, recovery tool, or deployment
entrypoint. A passing report is integration evidence for offline behavior only;
it is not authorization or operational readiness evidence.
