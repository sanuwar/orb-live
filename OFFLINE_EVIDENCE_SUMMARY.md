# Offline Evidence Summary

This projection aggregates already-decided bounded shadow-session receipts.
It uses no persistence, network, broker, credentials, manifests, or wall clock.
All timestamps are supplied by the caller. Count buckets and fixed blocker
reasons make the output compact while preserving fail-closed readiness semantics.

The summary is offline evidence only: live and unattended readiness, protection
grounding, activation approval, and automated action permission are always false.
