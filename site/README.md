# ORB-LIVE documentation dashboard

A static, internal explainer for the ORB-LIVE architecture, strategy,
safety model, and development journey.

This React application is intentionally isolated from the Python trading
runtime. It has no backend connection, broker integration, live data, or
trading controls. All content is sanitized and bundled locally.

## Run locally

```powershell
npm install
npm run dev -- --host 127.0.0.1
```

## Quality gates

```powershell
npm run lint
npm run test -- --run
npm run build
```

The dashboard is documentation only, remains under controlled
commissioning, and is not financial advice.

## Trade journal

The dashboard now includes a browser-local, append-oriented trade journal for sanitized agent evidence, manual trades, and manual recovery records. Records are stored under the browser local-storage key `orb-live.trade-journal.v1`; no journal database or imported evidence file belongs in Git. The `site/.gitignore` also excludes local journal exports.

The evidence importer accepts only the documented sanitized JSON envelope (`schemaVersion`, `environment`, `runId`, and normalized `events`). It hashes the selected file locally, rejects private or broker-shaped fields, and deduplicates by evidence SHA-256 plus sanitized event key. Re-importing the same file adds no duplicate records. Missing values and unproven attribution remain explicit.

Manual records use the same normalized model and decimal strings for quantities and money. Form submission adds a journal entry only; it cannot place, review, cancel, reconcile, or flatten an order. The application has no backend endpoint, so server CSRF is not applicable. A per-form submission key prevents duplicate local submission, and the shared domain validator is applied before persistence.

The trade journal is not a broker execution interface.

Current limitations: storage is local to one browser profile, evidence schemas are intentionally narrow, journal exports are not yet provided, and realized P&L appears only when complete sanitized evidence supplies it. A future main-branch merge should occur only after frontend review, local quality gates, and the existing runtime safety suite pass.
