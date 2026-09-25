# Agent Cron Fleet — Overview & Shared Foundations

Plans for porting jfroy's OpenClaw cron fleet (see joryirving/home-ops
`docs/src/notes/llm-strategy.md`) to this cluster. OpenClaw runs on a separate box
and is the agent/scheduler runtime; this repo is the source of truth for the
infrastructure each job depends on: secrets, model lanes, data connectors, helper
services, and delivery.

## One plan per project

| # | Plan | Agent | Schedule |
|---|------|-------|----------|
| 1 | [morning-brief.md](morning-brief.md) | assistant | 07:30 daily |
| 2 | [llm-hn-digest.md](llm-hn-digest.md) | assistant | 08:30 daily |
| 3 | [email-finance-check.md](email-finance-check.md) | assistant | 16:00 + 21:00 daily |
| 4 | [alertmanager-health-digest.md](alertmanager-health-digest.md) | ops | 09:30 daily |
| 5 | [homelab-commit-watch.md](homelab-commit-watch.md) | ops | 09:00 daily |
| 6 | [nightly-tech-sweep.md](nightly-tech-sweep.md) | ops | 06:20 daily |
| 7 | [nightly-audit-decomposer.md](nightly-audit-decomposer.md) | ops | 02:00 daily |
| 8 | [weekly-repo-audit.md](weekly-repo-audit.md) | ops | Wed 01:00 |
| 9 | [prompt-hygiene.md](prompt-hygiene.md) | ops | Wed 10:45 |


## Shared foundations (do first — every plan assumes these)

### F1. LiteLLM model lanes

Add three model groups to `kubernetes/apps/ai/litellm/` config (extend the existing
deployment's model config; this repo already runs litellm):

| Lane | Purpose | jfroy equivalent | Sizing |
|------|---------|------------------|--------|
| `daily-lane` | routine summaries over personal data | MiniMax-M2.7 | cheap flat-rate or ≤$0.50/M output model, ≥128k ctx |
| `agentic-lane` | multi-step tool use, image pipelines | qwen3.8-flash-next (local Strix) | long-context (≥256k if local, else cloud); vision |
| `escalation-lane` | audits, second opinions, MC-Escalated | gpt-5.6-sol | premium pay-per-token |

Give each group `order`-priority members and a `cooldown_time: 30`; use
`routing_strategy: simple-shuffle` (matches jfroy's rationale for burst fan-out).
Per-job lane assignment lives in each plan; the OpenClaw agent config references
`litellm/<lane>`.

### F2. Cron carrier

OpenClaw's internal scheduler executes the jobs (jfroy pattern). To keep it
declarative, store the OpenClaw cron/agent config in this repo under
`kubernetes/apps/ai/openclaw-config/` as a plain file, and sync it to the box with a
tiny cluster CronJob using `kubectl`-style exec is NOT possible cross-host — instead
pick one:

- **preferred:** a GitHub Action or `task` target (`task openclaw:sync`) that
  `rsync`/`scp`-renders `openclaw-config/` to the box and calls OpenClaw's reload;
- alternative: a CronJob in `apps/ai/agent-cron/` that POSTs each brief to the
  OpenClaw gateway HTTP API on schedule (cron lives in-cluster, OpenClaw stays stateless w.r.t. schedules).

Secrets referenced from OpenClaw config use `$${VAR}` expansion from an env file
pushed alongside, backed by the ExternalSecrets below.

### F3. Delivery

Reuse `kubernetes/apps/selfhosted/ntfy` for all push notifications (briefs, digests,
alerts, dispatch confirmations). Optionally a Telegram/Discord bot token later; ntfy
first keeps phase 1 dependency-free.

### F4. Secrets inventory (ExternalSecret / SOPS)

| Secret | Used by | 1Password field |
|--------|---------|-----------------|
| `agent-imap` | 3 | host/user/app-password |
| `agent-github` | 6, 7, 8, 9, 10 | fine-grained PAT |
| `agent-homeassistant` | 1, 4 | long-lived token |
| `agent-solar` | 4 | vendor API key |
| `agent-instagram` | 11–14 | page access token, IG user id |
| `agent-ntfy` | all | topic or access token |
| `agent-openclaw-token` | F2 (API carrier option) | gateway token |

Manifest pattern: `kubernetes/apps/ai/<consumer>/externalsecret.yaml` matching the
existing `apps/ai/litellm` external-secrets setup.

### F5. Search

Already deployed: `kubernetes/apps/ai/searxng`. All research jobs (1, 2, 14) use
`http://searxng.ai.svc:8080` — expose via Invress/Envoy so the off-cluster OpenClaw
box can reach it, with an API key or network allowlist.

## Rollout order

1. F1–F5 foundations, `flux get all -n ai` green.
2. Plans 1–4 (assistant, daily-lane/agentic): run 3 days, verify ntfy deliveries.
3. Plans 5–10 (ops agent): watch LiteLLM quota for a week.
4. Plans 11–14 (media pipeline) only if the IG use case is real; requires ComfyUI
   (plan 11) first.

Stagger the 06:20–10:45 block exactly as scheduled so the shared agentic lane never
double-books (jfroy's staggering rationale).
