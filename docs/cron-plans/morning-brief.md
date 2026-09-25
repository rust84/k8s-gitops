# Plan 1 — Unified Morning Brief

**Agent:** assistant · **Lane:** `daily-lane` · **Schedule:** 07:30 daily

## Purpose
One ntfy message assembling: weather today, calendar events, inbox highlights,
news headlines, radon/air quality. The anchor job of the fleet.

## Data flow
```
openclaw cron 07:30
  → assistant agent (daily-lane)
    ├─ Home Assistant REST API   → weather, radon/air sensors
    ├─ Radicale CalDAV           → today's calendar
    ├─ IMAP (via plan 3 connector) → overnight inbox highlights
    └─ SearXNG                   → news query (owner-defined standing queries)
  → ntfy publish (title + markdown body)
```

## Connectors
| Source | Method | Notes |
|--------|--------|-------|
| Weather + AQI/radon | Home Assistant `/api/states` REST | HA already deployed (`apps/home/home-assistant`); create sensors for radon if not present (Z-Wave device or EPA/nearest-monitor via SearXNG fallback) |
| Calendar | CalDAV against `apps/selfhosted/radicale` | use a CalDAV client tool in the agent; app password scoped to the calendar only |
| Inbox | IMAP read-only app password | shared with plan 3 |
| News | SearXNG `http://searxng.ai.svc:8080` | already deployed; needs off-cluster exposure (below) |
| Delivery | ntfy `http://ntfy.selfhosted.svc` | already deployed |

## Extra software to deploy
- **SearXNG external access**: add an Envoy `HTTPRoute` + `ClientTrafficPolicy` in
  `kubernetes/apps/ai/searxng/` (or extend the existing route if one exists) so the
  OpenClaw box can reach it; protect with SearXNG API limiter + a shared token header
  allowlist. No new service needed.
- Nothing else new — HA, Radicale, ntfy all exist.

## Secrets
- `agent-homeassistant` — long-lived HA token (read-only scope).
- `agent-radicale` — CalDAV app password (1Password → ExternalSecret).
- `agent-imap` — reuse plan 3 secret.
- `agent-ntfy` — publish token.

## OpenClaw-side config (sketch)
```jsonc
{
  "name": "unified-morning-brief",
  "schedule": "30 7 * * *",
  "agent": "assistant",
  "model": "litellm/daily-lane",
  "prompt": "Compile today's brief: weather + AQI/radon from HA, calendar from
    Radicale, overnight inbox highlights, top news via SearXNG. Deliver to ntfy
    topic. Under 400 words. Omit empty sections.",
  "tools": ["web_fetch", "ha_api", "caldav", "imap_read", "ntfy_publish"]
}
```

## Failure policy
Per-section degradation: any source that errors gets a one-line "source unavailable"
note; the brief still publishes. Never block the whole brief on one connector.

## Acceptance criteria
- 3 consecutive days of briefs delivered before 07:35 with ≥3 populated sections.
- Killing one connector (bad token) still yields a delivered brief with a warning.

## Effort
S (connectors all exist; mostly agent prompts + 1 route + 4 secrets).
