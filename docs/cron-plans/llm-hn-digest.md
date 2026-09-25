# Plan 2 — Daily LLM + HN Digest

**Agent:** assistant · **Lane:** `agentic-lane` (or `daily-lane` if cost matters) · **Schedule:** 08:30 daily

## Purpose
Daily digest of AI/LLM community activity: Hacker News top stories plus local-LLM
subreddits (r/LocalLLaMA etc.), deduplicated, ranked, 2-line summaries with links.

## Data flow
```
openclaw cron 08:30
  → assistant agent (agentic-lane)
    ├─ HN Firebase API  https://hacker-news.firebaseio.com/v0/topstories.json
    ├ HN item fetch      https://hacker-news.firebaseio.com/v0/item/<id>.json
    ├─ Reddit            via SearXNG (no Reddit API keys; scrape/search
    │                     reddit.com/r/LocalLLaMA/top?t=day)
    └─ dedupe by URL/title similarity → rank → summarize
  → ntfy publish (or a daily note in immich? no — ntfy + optional Karakeep save)
```

## Connectors
| Source | Method | Notes |
|--------|--------|-------|
| Hacker News | Firebase API, keyless | top 30 → filter to AI/tech-relevant, keep top 10 |
| Reddit | SearXNG query `site:reddit.com/r/LocalLLaMA` daily-top | avoids Reddit app approval; if rate-limited, add public JSON `?limit=25` fetch |
| Delivery | ntfy | existing |
| Optional save | Karakeep API (`apps/selfhosted/karakeep`) | archive the digest link list to a "digests" tag |

## Extra software to deploy
- None mandatory.
- Optional: **Karakeep save** — Karakeep already deployed; needs an API key secret
  and a `web_fetch`-based save call. Adds durability if ntfy messages expire.

## Secrets
- `agent-ntfy` (shared).
- `agent-karakeep` (optional, 1Password → ExternalSecret).

## OpenClaw-side config (sketch)
```jsonc
{
  "name": "daily-llm-hn-digest",
  "schedule": "30 8 * * *",
  "agent": "assistant",
  "model": "litellm/agentic-lane",
  "prompt": "Fetch HN top stories and today's top r/LocalLLaMA (+ r/LocalLLM,
    r/llmc). Dedupe, keep the 10 most interesting to a k8s/homelab/LLM operator,
    2-line summary + link each. Publish to ntfy.",
  "tools": ["web_fetch", "searxng", "ntfy_publish"]
}
```

## Failure policy
HN is keyless and reliable; if Reddit scraping fails, publish HN-only with a note.

## Acceptance criteria
- Digest delivered daily ≤08:40; ≤15 items; zero duplicate URLs across 1 week.

## Effort
S — no new infra, pure agent wiring.
