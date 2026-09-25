# Plan 3 — Email / Finance Checks (Afternoon + Evening)

**Agent:** assistant · **Lane:** `daily-lane` · **Schedule:** 16:00 and 21:00 daily

## Purpose
Twice-daily scan of the inbox for items needing action, plus financial anomaly
detection (unusual charges, failed payments, price changes on subscriptions),
summarized to ntfy. Evening run is the end-of-day rollup.

## Data flow
```
openclaw cron 16:00 / 21:00
  → assistant agent (daily-lane)
    ├─ IMAP read-only (INBOX, unread since last check)
    ├─ classify: actionable | FYI | finance receipt/notification
    ├─ finance parser: amounts, merchants, recurring-detection
    │   against a rolling baseline stored on the agent workspace
    └─ dedupe against previous run (state file)
  → ntfy publish (only if actionable OR anomalies; else silent "all clear"
    at evening run only)
```

## Connectors
| Source | Method | Notes |
|--------|--------|-------|
| Email | IMAP IDLE-less poll, read-only app password | provider-agnostic; Gmail users: app password + `X-GM-` labels optional |
| Finance | **phase A:** parse receipts/bank notification emails only — no bank API. **phase B (optional):** self-hosted [Maybe Finance](https://github.com/maybefinance/maybe-finance) with Plaid for real account data | start with A; B is its own sub-project |
| State | workspace JSON on the OpenClaw box | processed UIDs, spend baseline |
| Delivery | ntfy | existing |

## Extra software to deploy
- **Nothing for phase A.**
- Phase B optional: deploy **Maybe Finance** as `kubernetes/apps/selfhosted/maybe-finance/`
  (Helm chart via OCI or community chart; needs Postgres — reuse existing
  CNPG/Postgres pattern in `apps/database/`) + Plaid developer app credentials.
  Only do this if email-parsing proves too noisy/limited.

## Secrets
- `agent-imap` — `host`, `port`, `user`, `app-password` (read-only; never an
  account password; IMAP-only scope where the provider supports it).
- Phase B: `maybe-finance` app secret + Plaid keys (lives with the app, not the agent).

## OpenClaw-side config (sketch)
```jsonc
{ "name": "afternoon-email-finance", "schedule": "0 16 * * *", "agent": "assistant",
  "model": "litellm/daily-lane",
  "prompt": "Scan IMAP since last run. Summarize actionable items (reply needed,
    deadlines, meetings). Flag financial anomalies: charges >2x merchant median,
    new recurring charges, payment failures, price changes on known
    subscriptions. Skip state-file seen UIDs. Publish ntfy only if findings.",
  "tools": ["imap_read", "workspace_read", "workspace_write", "ntfy_publish"] }
```
(duplicate at `0 21 * * *` with "end-of-day summary + next-day flags".)

## Failure policy
Silence is acceptable: failed IMAP fetch → single ntfy error line, no retry storm
(max 1 retry, then wait for next slot). Never send partial finance claims —
unparseable → quote the sender/subject only.

## Privacy note
Receipt parsing happens against the agent's LiteLLM lane. If using third-party cloud
models, consider stripping amounts into pseudonyms first, or pin this job to a local
lane once one exists.

## Acceptance criteria
- 1 week without missed known-test receipt anomaly (seed 3 fake anomalies).
- False-positive rate < 1/night by week 2 (tune baseline).

## Effort
S (phase A) / L (phase B with Maybe Finance).
