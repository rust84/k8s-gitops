# Plan 5 — Alertmanager Health Digest

**Agent:** ops · **Lane:** `daily-lane` (analysis) with `agentic-lane` for investigation · **Schedule:** 09:30 daily

## Purpose
Morning review of Prometheus/Alertmanager state: what fired in the last 24h, what is
firing now, what was silenced, plus a short root-cause investigation of anything
unresolved — not just a paste of the alert list.

## Data flow
```
openclaw cron 09:30
  → ops agent
    ├─ Alertmanager API  http://alertmanager.observability.svc:9093/api/v2
    │    GET /alerts, /silences  (read-only)
    ├─ Prometheus API    http://prometheus...observability.svc:9090/api/v1
    │    (context queries: node memory, disk, cert expiry, Flux failures)
    └─ investigate anomalies (agentic-lane sub-step: query series, correlate)
  → ntfy publish (red digest = action needed / green one-liner)
```

## Connectors
| Source | Method | Notes |
|--------|--------|-------|
| Alertmanager | API v2, read-only | already deployed (`apps/observability/alertmanager`) |
| Prometheus | HTTP API | part of kube-prometheus-stack |
| Delivery | ntfy | |

## Extra software to deploy
The OpenClaw box is off-cluster, so the observability APIs need controlled access:

1. **Read-only egress route** — add to `kubernetes/apps/observability/`:
   - an `Ingress`/`HTTPRoute` (Envoy Gateway, per repo convention) exposing
     `alertmanager:9093` and `prometheus:9090` on an internal hostname
     (e.g. `alert-lookup.<internal-domain>`),
   - a `SecurityPolicy` requiring a bearer token (or mTLS/client cert), scoped to
     `GET /api/v2/alerts|silences` and `GET/POST /api/v1/query*` only,
   - a `NetworkPolicy` allowing only the OpenClaw box IP.
   - token stored as `agent-observability-token` ExternalSecret and pushed to the box.
2. **Optional — Flux health sidecar**: nothing new; the route above plus
   `flux get all` is done by plan 6/7's kubeconfig path instead.

Alternative if you prefer zero exposure: a tiny CronJob-rendered snapshot —
`apps/observability/alert-snapshot/` CronJob dumps `/alerts` JSON to a PVC that the
agent fetches over SFTP. More moving parts; route + token is simpler.

## Secrets
- `agent-observability-token` (new, as above), `agent-ntfy`.

## OpenClaw-side config (sketch)
```jsonc
{ "name": "alertmanager-health-digest", "schedule": "30 9 * * *", "agent": "ops",
  "model": "litellm/daily-lane",
  "prompt": "Pull firing+resolved alerts (24h) and active silences from
    alert-lookup. For each unresolved firing alert, run 1-3 PromQL context queries
    and state the likely cause and suggested fix. Green day = one line.",
  "tools": ["web_fetch", "ntfy_publish"] }
```

## Failure policy
API unreachable → publish the auth/connectivity error itself as the alert (this job
monitoring itself failing is news). Cap investigations at 3 alerts/day; defer the
rest.

## Acceptance criteria
- Fires during a seeded test alert (deliberate threshold breach) with a correct
  cause line; green days ≤1 line.

## Effort
M (route + policy + token is the bulk).
