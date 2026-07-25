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
