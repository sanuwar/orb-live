# M25.6 Remote Read-Only Monitoring

This is a separate authenticated monitoring boundary. It does not move ORB-LIVE trading to the cloud and it does not expose the local control service.

## Boundary

```text
local watcher + runtime journal
        |
        | fixed GET-only sanitized collection
        v
127.0.0.1:8777  authenticated remote-monitoring service
        |
        | Cloudflare Tunnel outbound connection
        v
https://<operator-configured-hostname>
```

- Local watcher control remains `127.0.0.1:8766` and is never a tunnel target.
- Remote monitoring binds only to `127.0.0.1:8777`.
- The monitoring service performs one fixed local GET to `/api/local-control/status`; it accepts no URL, method, command, argument, or PID from a client.
- There is no local-control proxy. No readiness, Start, Stop, commissioning, activation, suspension, authorization, or broker endpoint exists remotely.
- The Cloudflare hostname and credentials are deployment configuration, never application constants.

## Remote endpoint inventory

| Method | Route | Purpose |
|---|---|---|
| `GET` | `/api/remote/session` | Validate the server-side session and return its expiry plus the remote-session logout CSRF value. |
| `GET` | `/api/remote/projection` | Return the authenticated allowlisted monitoring projection. |
| `POST` | `/api/auth/login` | Verify the scrypt password hash and create an expiring server-side session. |
| `POST` | `/api/auth/logout` | Revoke the session; exact Origin and remote-session CSRF are required. |

All other `/api/*` routes return `404`. In particular, `/api/local-control/*` is never proxied. Static files contain the login/read-only UI only and reveal no monitoring data before API authentication.

## Authentication

- Password hash: scrypt, configured as `ORB_REMOTE_PASSWORD_HASH` outside source control.
- Session: random opaque cookie token with state stored only in server memory.
- Production cookie: `HttpOnly`, `Secure`, `SameSite=Strict`, one-hour expiry.
- Login protection: exact configured HTTPS Origin and five failed attempts per five-minute window.
- Logout protection: exact Origin, authenticated session, and per-session CSRF value.
- No password, password hash, session token, or CSRF value is stored in localStorage or bundled into React.

Sessions are intentionally lost when the monitoring process restarts; the operator signs in again.

## Outbound projection allowlist

The remote payload permits only:

- projection schema/type and UTC generation time;
- explicit capabilities (`monitoringRead=true`; control, broker write, and authorization all false);
- current watcher state, observation time, and sanitized last-run result/terminal state/start/finish/stop reason;
- sanitized sessions, evaluation timestamps, evaluator-owned filter state/rejection facts;
- sanitized candidate journal facts (symbol, state, reason, timestamp);
- sanitized runtime events (event identity/type/time/severity/component/detail code);
- server-computed `CURRENT`, `STALE`, or `NO_EVIDENCE` freshness.

It excludes credentials, environment variables, account identifiers, broker payloads, authorization envelopes, local filesystem paths, PIDs, subprocess commands/logs, raw requests/responses, and the local-control CSRF value. A stale or unavailable projection sets `candidateActionable=false`, and the UI does not display a stale candidate as active.

## Local software setup

Build both the existing local UI and the separate remote UI:

```powershell
cd C:\dev\orb-live\site
npm ci
npm run build
cd ..
```

Generate a password hash offline. The command does not echo the password:

```powershell
python scripts/hash_remote_monitoring_password.py
```

Store the generated hash and the final HTTPS origin in the process environment or a Windows secret/service manager. Do not commit either value:

```powershell
$env:ORB_REMOTE_PASSWORD_HASH='<generated-scrypt-hash>'
$env:ORB_REMOTE_ORIGIN='https://orb-monitor.<configured-domain>'
python scripts/run_remote_monitoring.py
```

For loopback-only development without HTTPS:

```powershell
$env:ORB_REMOTE_PASSWORD_HASH='<generated-scrypt-hash>'
python scripts/run_remote_monitoring.py --dev-insecure-cookie
```

Then open `http://127.0.0.1:8777`. The insecure-cookie option refuses any non-loopback Origin and must not be used for public access.

## Cloudflare Tunnel HTTPS publication

Cloudflare account, domain, DNS, and tunnel credentials are external operator prerequisites. They cannot be created or committed by this repository. Install `cloudflared` from Cloudflare's official distribution, then authenticate on the Windows trading PC:

```powershell
cloudflared tunnel login
cloudflared tunnel create orb-live-monitor
```

Create `%USERPROFILE%\.cloudflared\config.yml` outside this repository, substituting the tunnel UUID, credential path, and hostname supplied/configured by the operator:

```yaml
tunnel: <tunnel-uuid>
credentials-file: C:\Users\<operator>\.cloudflared\<tunnel-uuid>.json
ingress:
  - hostname: orb-monitor.<configured-domain>
    service: http://127.0.0.1:8777
  - service: http_status:404
```

The ingress service must be exactly port `8777`. Never use `8766`. Create DNS and run the outbound tunnel:

```powershell
cloudflared tunnel route dns orb-live-monitor orb-monitor.<configured-domain>
cloudflared tunnel run orb-live-monitor
```

From a phone or another computer, open `https://orb-monitor.<configured-domain>`. The expected flow is login, then `REMOTE MONITORING - READ ONLY`. No router port forwarding is required or permitted.

Before relying on the public URL, verify unauthenticated `/api/remote/projection` returns `401`, authenticated monitoring renders current freshness, `/api/local-control/start` returns `404`, and no tunnel ingress targets `127.0.0.1:8766`.

## Deployment stop

Repository software can prepare and validate the boundary, but cannot create the operator's Cloudflare account, domain, DNS record, or tunnel credential. Production publication therefore stops until the operator completes those external steps and reviews the resulting HTTPS hostname.
