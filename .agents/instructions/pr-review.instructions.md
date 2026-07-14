# k8s-gitops PR review conventions

This file is the `system_prompt_file` for the AI PR Review workflow
(`.github/workflows/ai-pr-review.yaml`), used with `system_prompt_mode: append`:
the action keeps its bundled default system prompt and appends this file as a
repo-specific addendum.

## Repo conventions

The conventions in `CLAUDE.md` are authoritative for this project.

### Documented conventions to honour without flagging

- **`metadata.namespace` is intentionally absent on `HelmRelease` and `Kustomization` resources.** The namespace is injected at build time by kustomize's `namespace:` directive in the per-app `kustomization.yaml`. For Flux `Kustomization` resources, `spec.targetNamespace` propagates automatically. Do not flag the absence of `metadata.namespace` on these resources.

- **OCI artifacts are pinned by tag/version, not by SHA digest.** The "Prefer `@sha256:` digests" policy applies to container images only. OCI artifacts pulled via `OCIRepository` (Helm charts in OCI registries) are pinned by tag or version. Do not flag the absence of `@sha256:` on OCI artifact references.

### Compact Renovate digest-only reviews

For Renovate digest-only container image updates where the repository and tag are unchanged and the diff only changes `@sha256:` values, keep `review_markdown` compact. Prefer a short recommendation, changed files summary, and non-blocking caveats if any. Do not include separate sections unless they contain an actual warning or blocker.

### Konflate rendered-diff tools

A Konflate MCP server is configured. Konflate renders Helm charts and Kustomizations into their final Kubernetes manifests, so its rendered diff shows the actual cluster impact of a PR. A rendered-diff summary is usually already injected into the corpus by the konflate evidence provider; use the MCP tools when you need more than the summary provides.

- `mcp__konflate__get_pr_summary` — pass the current PR `number`. Blast radius, caution lint, image changes, render failures.
- `mcp__konflate__get_pr_diff` — pass the current PR `number`. The full rendered manifest diff.

Konflate signals in the review: surface cautions as caveats or blockers by severity; treat render failures as blockers. For Renovate digest-only bumps where konflate shows only `@sha256:` changes, keep the review compact.

Check upstream for breaking changes.
