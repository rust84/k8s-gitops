# Plan 6 — Daily Home-Ops Updates (Commit Watch)

**Agent:** ops · **Lane:** `daily-lane` · **Schedule:** 09:00 daily

## Purpose
Watch the homelab repos (this one first) for commits since yesterday: what changed,
what it means operationally (image bumps, config drift, new apps, reverted PRs), and
flag anything that looks risky (secrets touched, RBAC widened, `suspend` fields).

## Data flow
```
openclaw cron 09:00
  → ops agent
    ├─ GitHub REST: GET /repos/rust84/k8s-gitops/commits?since=<last-run>
    ├─ per-commit: parse diff summary; classify
    │   (renovate-bump | feature | config | revert | security-relevant)
    ├─ Flux health correlation: did HRs reconcile after the change?
    │   (via plan 5 route or kubeconfig from plan 7)
    └─ state file: last-run SHA per repo
  → ntfy publish (grouped summary, risk flags first)
```

## Connectors
| Source | Method | Notes |
|--------|--------|-------|
| GitHub | REST API with fine-grained PAT (contents:read, metadata:read) | no webhooks needed — polling is fine at 1/day; avoids exposing a receiver |
| Flux state | kubeconfig (shared with plan 7) or skip until plan 7 | |
| Delivery | ntfy | |

## Extra software to deploy
- **Nothing new.** GitHub polling from the agent; existing secrets pattern.
- Optional (later): `flux get all` output can be captured by the nightly sweep
  (plan 7) instead of duplicating cluster access here.

## Secrets
- `agent-github` — fine-grained PAT: **read-only contents + metadata** on
  `rust84/*`; stored 1Password → ExternalSecret for the config sync target.
- `agent-ntfy`.

## OpenClaw-side config (sketch)
```jsonc
{ "name": "homelab-commit-watch", "schedule": "0 9 * * *", "agent": "ops",
  "model": "litellm/daily-lane",
  "prompt": "For each watched repo, list commits since state-file SHA. Classify
    each; flag: secret/RBAC/network-policy changes, Flux suspensions, major
    version bumps, deleted manifests. Publish grouped summary, flags first.",
  "tools": ["github_api", "workspace_read", "workspace_write", "ntfy_publish"],
  "state": "repos.json" }
```

## Failure policy
API errors → retry once, then report which repos were skipped (never silent partial).

## Acceptance criteria
- Correctly flags a seeded risky PR (e.g. `hostNetwork: true` added) within a day.

## Effort
S.
