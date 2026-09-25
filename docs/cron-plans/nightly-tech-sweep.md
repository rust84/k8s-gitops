# Plan 7 — Nightly Tech Sweep

**Agent:** ops · **Lane:** `agentic-lane` · **Schedule:** 06:20 daily

## Purpose
Overnight cluster health check with **low-risk fixes only**: failing pods, crash
loops, un-ready HelmReleases, pending CSRs, expiring certs, disk pressure, failed
Flux Kustomizations. The agent investigates, applies only reversible/boring fixes,
and opens PRs (never direct pushes) for anything else.

## Data flow
```
openclaw cron 06:20
  → ops agent (agentic-lane)
    ├─ kubectl (scoped kubeconfig):
    │    pods NotReady/CrashLoopBackOff, HelmRelease Failed, KS stalled,
    │    cert-manager Certificate expiring<14d, PersistentVolumeClaim Pending,
    │    nodes NotReady, kubelet disk pressure
    ├─ triage playbook (repo-owned runbooks fetched from Git!)
    ├─ low-risk actions whitelist:
    │    delete crashed pod (lets controller recreate), resume an
    │    accidentally-suspended KS (with comment), rollout restart single HR
    └─ everything else → open GitHub issue with the diagnostic bundle
  → ntfy publish (what was found / what was fixed / what awaits human)
```

## Connectors
| Source | Method | Notes |
|--------|--------|-------|
| Kubernetes API | dedicated **scoped kubeconfig** | see RBAC below |
| Playbooks/runbooks | this repo, `docs/runbooks/` | agent fetches via GitHub — fixes stay repo-reviewable |
| GitHub | `agent-github` (needs issues:write here — separate finer token `agent-github-issues`) | |
| Delivery | ntfy | |

## Extra software to deploy
- **RBAC**: `kubernetes/apps/ai/agent-rbac/`
  - ServiceAccount `agent-ops`, bound to:
    - `view` ClusterRole (read everything),
    - a narrow Role allowing `delete pods` (core), `patch helmreleases/kustomizations`
      (flux/toolkit) **in app namespaces only**, `get certificates` (cert-manager).
    - **Never** `delete deployments`, `patch rbac`, `patch network`, cluster-scope write.
  - long-lived ServiceAccount token Secret → exported for the OpenClaw box
    (via external-secrets push or the config-sync mechanism).
- **Runbooks**: seed `docs/runbooks/` with 3–5 one-pagers (crashloop triage, HR
  failed triage, cert renewal stuck, PVC pending). These become the sweep's playbooks
  and the audits' review target.

## Secrets
- `agent-kubeconfig` (scoped, from RBAC above), `agent-github-issues`, `agent-ntfy`.

## OpenClaw-side config (sketch)
```jsonc
{ "name": "nightly-tech-sweep", "schedule": "20 6 * * *", "agent": "ops",
  "model": "litellm/agentic-lane",
  "prompt": "Run sweep checklist. Apply ONLY whitelisted low-risk fixes; follow
    docs/runbooks/. Anything beyond whitelist → GitHub issue with diagnostics.
    Log every action with before/after state.",
  "tools": ["kubectl", "github_issues", "workspace_write", "ntfy_publish"],
  "guardrails": { "max_actions_per_night": 3, "never": ["delete pvc","patch rbac","patch network","direct push"] } }
```

## Failure policy
Any fix that doesn't converge in 10 min → revert it (own the rollback), file issue,
continue checklist. Cap 3 actions/night so a bad model night can't churn the cluster.

## Acceptance criteria
- Seed a CrashLoopBackOff pod → next sweep fixes it and reports before/after.
- Seed a "needs human" condition (PVC Pending) → issue opened, no cluster mutation.

## Effort
M–L (RBAC + runbooks + guardrail tuning).
