# Plan 9 — Weekly Repo Audit

**Agent:** ops (spawner) · **Lanes:** `escalation-lane` / `agentic-lane` for auditors · **Schedule:** Wed 01:00

## Purpose
Weekly fleet of per-repo audit sub-agents: each audits one repo (this one plus any
labeled watched repos) for drift, rot, and risk — stale pins, duplicated config,
missing alerts/backup coverage, RBAC sprawl, deprecated APIs, secrets hygiene —
filing findings as `audit/umbrella` issues for plan 8 to decompose.

## Data flow
```
openclaw cron Wed 01:00
  → ops agent (spawner)
    ├─ repo list: `watched` topic/label on rust84/* org repos
    ├─ spawn one audit sub-agent per repo (max 4 concurrent), each:
    │    ├─ GitHub contents API walk (no full clones)
    │    ├─ cross-check against cluster reality (plan 7 kubeconfig, read-only):
    │    │    deployed-vs-declared drift, missing PodMonitor/alert coverage,
    │    │    PVCs without volsync, HRs without health checks
    │    └─ output findings → GitHub issues `audit/umbrella` (+ severity label)
    └─ collect verdicts → ntfy weekly summary
```
jfroy's council rule applies: auditors from a **different model family** than the
code's usual author — use `escalation-lane` for infra-critical repos,
`agentic-lane` for the rest; never let the same lane audit its own fixes.

## Connectors
| Source | Method | Notes |
|--------|--------|-------|
| GitHub org repo list | REST `/orgs/rust84/repos` or `users/rust84/repos` | filter by topic `watched` |
| Repo contents | Contents API | read-only |
| Cluster reality | plan 7 scoped kubeconfig (read-only view role) | drift detection: `kubectl get helmrelease,ks -A -o json` vs Git |
| Delivery | ntfy | |

## Extra software to deploy
- **Nothing new** beyond shared foundations (labels from plan 8; add severity
  labels `audit/critical|major|minor`).
- Optional: `flux diff`-equivalence checking via **Konflate** (jfroy's PR-evidence
  tool) added to CI so audits can cite rendered diffs; GitHub Action, no cluster
  deployment.

## Secrets
- `agent-github` (issues:write + contents:read), `agent-kubeconfig`, `agent-ntfy`.

## OpenClaw-side config (sketch)
```jsonc
{ "name": "weekly-repo-audit", "schedule": "0 1 * * 3", "agent": "ops",
  "model": "litellm/escalation-lane",
  "prompt": "For each watched repo spawn an audit subagent (≤4 concurrent,
    agentic-lane; escalation-lane for repos tagged infra-critical). Audit scope:
    pin staleness, config duplication, alert/backup coverage gaps, RBAC sprawl,
    deprecated APIs, secret hygiene. File findings as audit/umbrella issues with
    severity. Summarize.",
  "tools": ["github_api", "github_issues", "kubectl_ro", "ntfy_publish"] }
```

## Failure policy
One repo failing doesn't cancel the run; report per-repo status. Auditors are
read-only + issue-creating only; no cluster mutation, ever.

## Acceptance criteria
- First run produces ≥1 credible finding per repo with file:line references;
  re-runs don't duplicate open findings (dedupe by title/fingerprint comment).

## Effort
M.
