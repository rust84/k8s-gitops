# Plan 10 — Weekly Prompt Hygiene

**Agent:** ops · **Lane:** `agentic-lane` · **Schedule:** Wed 10:45

## Purpose
Audit the fleet's own prompt/agent config files for bloat, contradictions, and
stale assumptions — the agents' instructions are operational config and rot like
any config (jfroy's 2026-07-29 "prompt-hygiene trim" precedent: his bootstrap files
were halved after this job kept flagging them).

## Data flow
```
openclaw cron Wed 10:45
  → ops agent
    ├─ collect prompt surfaces from Git (single source of truth, see below):
    │    kubernetes/apps/ai/openclaw-config/**  (agent prompts, cron briefs)
    │    docs/runbooks/**                        (sweeper playbooks)
    ├─ checks:
    │    contradictions between prompts; duplicated guidance across files;
    │    references to deleted services/paths; token bloat (per-file budgets);
    │    instructions contradicted by observed behavior in job logs
    ├─ open PR (branch `prompt-hygiene/YYYY-MM-DD`) with the trim diff,
    │    each hunk justified in the PR body
    └─ ntfy summary with PR link
```

## Connectors
| Source | Method | Notes |
|--------|--------|-------|
| Prompt files | Git — `kubernetes/apps/ai/openclaw-config/` (the F2 sync source) | **precondition:** prompts live in this repo, not only on the box |
| Job logs | OpenClaw session logs on the box (export to a PVC or fetch via gateway API) | used for "contradicted by behavior" findings |
| GitHub | PRs via `agent-github` (pull_requests:write) | human approves — the agent never self-merges prompt changes |
| Delivery | ntfy | |

## Extra software to deploy
- **Precondition (F2 hard requirement):** move OpenClaw prompts + cron definitions
  into `kubernetes/apps/ai/openclaw-config/` in this repo. This job only works if
  prompts are Git-tracked reviewable files.
- Nothing else new. Optional: add a CI check (`yamllint`-style, or a 10-line script
  in `hack/`) enforcing per-prompt-file token budgets so hygiene can regress in CI,
  not only in the weekly audit.

## Secrets
- `agent-github` (PR write), `agent-ntfy`.

## OpenClaw-side config (sketch)
```jsonc
{ "name": "weekly-prompt-hygiene", "schedule": "45 10 * * 3", "agent": "ops",
  "model": "litellm/agentic-lane",
  "prompt": "Audit openclaw-config/** and docs/runbooks/**. Find contradictions,
    duplication, stale path/service references, bloat over budgets. Open a PR with
    justified trims. Never change a job's acceptance semantics — size only, or
    flag semantic issues as comments for the owner.",
  "tools": ["repo_read", "github_pr", "ntfy_publish"] }
```

## Failure policy
Read + PR-only, no deploy path — worst case is a bad PR you close. If last week's PR
is still unreviewed, don't stack a second; comment a rebase instead.

## Acceptance criteria
- Each run opens ≤1 PR; hunks individually justified; zero reverts after 4 weeks.

## Effort
S once F2 (Git-tracked prompts) exists.
