# Plan 8 — Nightly Audit Decomposer

**Agent:** ops · **Lane:** `agentic-lane` · **Schedule:** 02:00 daily

## Purpose
Take "umbrella" audit issues (broad goals like "audit the observability stack") filed
by humans or by the weekly audit (plan 9), and decompose each into concrete,
independently-verifiable child issues with acceptance checks — so humans (and the
issue-worker pipeline) pick up small bounded work, not vague mandates.

## Data flow
```
openclaw cron 02:00
  → ops agent
    ├─ GitHub: issues labeled `audit/umbrella`, state open, not yet decomposed
    │    (marker label `audit/decomposed` + comment watermark)
    ├─ read repo tree for context (contents API, shallow)
    ├─ decompose → 3–8 child issues: title, scope, owned paths, observable
    │    done-check, effort estimate, cross-references
    ├─ create children with `audit/child`, parent link; label parent
    │    `audit/decomposed`; comment the reasoning
    └─ flag (don't decompose) umbrellas that are too vague — comment asking
      for one clarifying decision from the owner
  → ntfy only on flags; silent otherwise
```

## Connectors
| Source | Method | Notes |
|--------|--------|-------|
| GitHub issues | REST, `agent-github` PAT (issues:write on watched repos) | shared token with plan 7's issues scope |
| Repo context | Contents API read-only | avoid full clones at 2am; shallow read of relevant dirs |
| Delivery | ntfy (exception-only) | |

## Extra software to deploy
- **Nothing new.** Labels to create in-repo:
  `audit/umbrella`, `audit/child`, `audit/decomposed` — add via a
  `.github/labels.yaml` + small GitHub Action (or one-time `gh label create`
  documented in `docs/cron-plans/labels.md`).
- Optional (later): wire `audit/child` into an automated issue-worker like jfroy's
  foreman dispatch (MC Normal). Out of scope here — note as future plan 15.

## Secrets
- `agent-github` (issues:write), `agent-ntfy`.

## OpenClaw-side config (sketch)
```jsonc
{ "name": "nightly-audit-decomposer", "schedule": "0 2 * * *", "agent": "ops",
  "model": "litellm/agentic-lane",
  "prompt": "Find open issues labeled audit/umbrella lacking audit/decomposed.
    Decompose each into 3-8 child issues (scope, owned paths, observable
    done-check, estimate). Never decompose into more than 8; if it needs more,
    split into two umbrellas and ask the owner. Watermark parents.",
  "tools": ["github_issues", "repo_read", "ntfy_publish"] }
```

## Failure policy
Idempotency is the contract: watermark via label+comment SHA; a crash mid-run must
not double-create children (check existing children count before creating).

## Acceptance criteria
- Seed one umbrella → next run yields 3–8 well-scoped children, correct labels, no
  duplicates on forced re-run.

## Effort
S.
