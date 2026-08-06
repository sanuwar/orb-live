# Operator application read model

`operator_app.sqlite` is a disposable, rebuildable SQLite query model.
Runtime stores remain authoritative. It contains only existing privacy-safe
snapshot, timeline, and incident projections and cannot change runtime safety.

Version 1 has five operational tables: `import_batches`, `operator_snapshots`,
`operator_events`, `operator_incidents`, and `incident_evidence_links`, plus
`schema_metadata`. Imports are explicit and transactional. Web reads are
read-only and never initialize or import.

The database path must be an absolute `.sqlite` file outside source/test and
repository-data directories. The store rejects incompatible release identity,
unsafe snapshots, unknown schemas, prohibited safety flags, raw payloads, and
account/trading or credential data.
