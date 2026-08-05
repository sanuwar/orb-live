# Supervised read-only deployment checklist

This checklist is an external human review aid. Checking a box cannot change
runtime state, create an approval, issue a manifest, or authorize any operation.

## Before packaging

- [ ] Repository SHA recorded: `____________________________`
- [ ] Implementation digest recorded: `____________________________`
- [ ] Pinned-file count verified: `________`
- [ ] Full suite and guards passed.
- [ ] Strategy disabled; approvals empty.

## Before host deployment

- [ ] Host identified and filesystem layout defined.
- [ ] Evidence database path and backup location defined.
- [ ] Log destination and alert recipient defined.
- [ ] Shutdown and restart procedures defined.
- [ ] Host clock and timezone verified.

## Before read-only connection

- [ ] Read-only credential scope verified externally; no write permission present.
- [ ] Identity scope privately verified without recording private values here.
- [ ] Network path approved; rate-limit and outage behavior understood.
- [ ] Manifest/checkpoint gate respected; no unapproved checkpoint executed.

## Before supervised operation

- [ ] Operator assigned and runbook reviewed.
- [ ] Alert channel tested and recovery exercise completed.
- [ ] Campaign acceptance package reviewed.
- [ ] Deployment approval recorded outside runtime.
- [ ] Live writes remain structurally impossible.
- [ ] Unattended operation remains structurally impossible.

I acknowledge that this checklist supports planning for supervised read-only
operation only. It does not authorize broker access, strategy activation, live
trading, or unattended operation.
