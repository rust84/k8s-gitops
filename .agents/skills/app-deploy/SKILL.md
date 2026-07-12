---
name: app-deploy
description: Deploy a new application to the rust84/k8s-gitops Flux-managed Talos cluster. Use this skill whenever the user asks to "deploy", "install", "add", or "set up" an app on their home cluster, especially when they reference Flux, HelmRelease, Helm chart names, OCI charts, or specific tools (e.g. "deploy Jellyfin", "install Authentik", "add Prometheus node-exporter", "set up Tailscale on the cluster"). The skill uses Kubesearch to find the canonical chart, then creates the full set of manifests (OCIRepository/HelmRepository, HelmRelease, kustomization.yaml, ks.yaml Kustomization, and an ExternalSecret backed by the cluster's 1Password ClusterSecretStore if the app needs credentials). Networking is auto-wired to the existing `envoy-external` Gateway with a `{{ .Release.Name }}.${SECRET_DOMAIN}` hostname unless the app is internal-only. Even when the user says "I want to add X" without explicitly mentioning Flux or GitOps, trigger this skill if the request implies a new workload on this cluster.
---

# Deploy a new app to rust84/k8s-gitops

This is an opinionated, end-to-end workflow for adding a new application to the Flux-managed Talos cluster defined in `/home/russell/repos/k8s-gitops`. It assumes the conventions already in this repo and is **not** a generic "deploy to Kubernetes" skill.

## When to use

Trigger this skill when the user asks to **add, install, deploy, or set up** any application, service, or workload on the cluster — for example:

- "Deploy Jellyfin"
- "Install Authentik"
- "Add Prometheus node-exporter to observability"
- "Set up Tailscale"
- "Wire up OIDC for Grafana"

Do **not** use this skill for:
- In-place edits to an already-deployed app (use the existing files instead)
- Cluster bootstrap / Talos config changes
- Generic Kubernetes how-to questions
- Apps the user only wants to *research* (use Kubesearch directly, no manifests)

## Cluster facts (read-only)

The repo at `/home/russell/repos/k8s-gitops` has these conventions baked in. Mirror them — do not invent new patterns.

- **Layout**: `kubernetes/apps/<category>/<app>/{app,instance,config}/` with a top-level `ks.yaml`.
- **Categories**: `ai`, `cert-manager`, `database`, `external-secrets`, `flux-system`, `home`, `kube-system`, `media`, `network`, `observability`, `openebs-system`, `rook-ceph`, `selfhosted`, `system-upgrade`, `volsync-system`. Place each new app under the best-matching category. If none fits, ask the user.
- **Chart sources**: `oci://ghcr.io/home-operations/charts/...` (type `oci`), `https://bjw-s.dev/charts`, `oci://ghcr.io/bjw-s-labs/helm/...`, plus the standard bitnami/grafana/jetstack/etc. registries already wired in `kubernetes/flux/meta/repositories/{helm,oci}/`.
- **Secrets**: 1Password via `ClusterSecretStore/onepassword` (`kubernetes/apps/external-secrets/onepassword/store/clustersecretstore.yaml`). Use `ExternalSecret`, **never** SOPS or inline secrets for app credentials. SOPS is reserved for cluster-bootstrap secrets (e.g. `cluster-secrets`).
- **Networking**: `Gateway/envoy-external` in the `network` namespace (defined in `kubernetes/apps/network/envoy-gateway/config/external.yaml`) serves `*.${SECRET_DOMAIN}` on HTTPS with a wildcard listener. Default to attaching `httpRoute` with hostname `{{ .Release.Name }}.${SECRET_DOMAIN}` and parentRef `envoy-external` / `network`. Skip `httpRoute` for non-HTTP workloads (databases, agents, batch jobs).
- **Domain substitution**: `${SECRET_DOMAIN}` is rendered by Flux from the `cluster-secrets` Secret — don't substitute it yourself, leave it literal in HelmRelease values.
- **GitHub repo**: `github://rust84/k8s-gitops` (used by tools like Konflate).
- **Task runner**: `Taskfile.yaml` + `.taskfiles/{k8s,flux,...}/*.yaml`. Tasks like `task k:ks-apply PATH=...` and `task flux:hr-sync` exist.

## Workflow

When the user says "deploy X", follow these steps in order. **Do not skip the research step** — most failures come from guessing chart versions or secret layouts.

### 1. Gather minimum inputs

Ask the user **only** what you cannot infer:

- App name (sometimes implied: "deploy Prometheus" → maybe `prometheus` or `kube-prometheus-stack`)
- Namespace target (default: `flux-system` if it's a flux/charts-system app, otherwise `<category>`)
- If secrets are required: 1Password item name and field names (or "skip — will set up later")
- If external hostname: literal vs `{{ .Release.Name }}` subdomain (default: release-name subdomain)

If the user gives a one-shot request like "deploy Jellyfin", assume defaults (release-name subdomain, app's category namespace, secret layout as needed) and proceed.

### 2. Research the chart with Kubesearch

Use the `kubesearch_*` MCP tools (do NOT use `webfetch` for chart research — Kubesearch returns deployment-ready config).

```
kubesearch_search_deployments(query="<app>", limit=5)        # find best repos and versions
kubesearch_get_chart_index(key="<key from search>")          # discover all values paths
kubesearch_get_chart_details(key="<key>", valuePath="<x>")  # popular values for a section
```

Also pull the chart's README from GitHub if the values aren't documented in Kubesearch — the home-operations chart READMEs explain env-var mappings (`KONFLATE_*`, etc.).

Pick the highest-scored `key` (more stars = better-maintained) and the most recent `version`. If multiple repos have it, prefer `home-operations` > `bjw-s` > `bitnami` > `prometheus-community` for parity with this cluster.

### 3. Decide if the app needs a new chart-source

- If the chart is already covered by an existing HelmRepository/OCIRepository in `kubernetes/flux/meta/repositories/{helm,oci}/`, **reuse it** — no new repo file needed.
- Otherwise, create a new one:
  - OCI chart → `kubernetes/flux/meta/repositories/oci/<name>.yaml` (use `OCIRepository` v1beta2, model on `app-template.yaml` or `envoy-gateway.yaml`)
  - HTTP chart → `kubernetes/flux/meta/repositories/helm/<name>.yaml` (use `HelmRepository`, model on `home-operations.yaml` if OCI-typed, otherwise any standard entry)
  - Add the new file to the parent `kustomization.yaml` resources list

### 4. Create the app directory tree

```
kubernetes/apps/<category>/<app>/
├── app/
│   ├── helmrelease.yaml          # the main HelmRelease
│   ├── kustomization.yaml        # labels: app.kubernetes.io/name: <app>
│   ├── helm-values.yaml          # only if values are large/complex; otherwise inline
│   └── kustomizeconfig.yaml      # only if you need name reference transformations
├── instance/                     # secrets / instance-specific config
│   ├── externalsecret.yaml       # if app needs credentials
│   └── kustomization.yaml        # references externalsecret.yaml (+ optional configmap)
└── ks.yaml                       # two Kustomizations: <app> (app/), <app>-instance (instance/)
```

Reference an existing two-Kustomization app for the layout — `flux-operator/` and the konflate deployment we just did are good templates.

### 5. Write the HelmRelease

This repo uses **two different patterns** for `chartRef` vs `chart.spec`, and choosing the wrong one will fail validation:

**OCI charts** (use `chartRef`, like `konflate`, `envoy-gateway`, `cloudflared`):
```yaml
spec:
  chartRef:
    kind: OCIRepository
    name: <repo name in flux-system>
```

**HTTP/HTTPS HelmRepository charts** (use `chart.spec`, like `authentik`, `kube-prometheus-stack`, every observability app):
```yaml
spec:
  chart:
    spec:
      chart: <chart name>
      version: "<exact pinned version>"   # e.g. "2026.5.3" — never omit, never use 'latest'
      sourceRef:
        kind: HelmRepository
        name: <repo name in flux-system>
        namespace: flux-system
```

Why the split: Flux's `chartRef.kind` enum only accepts `OCIRepository | HelmChart | ExternalArtifact`. `HelmRepository` is NOT a valid `chartRef` kind — it must go in `chart.spec.sourceRef`. Kubeconform with the CRDs catalog schema will reject `chartRef.kind: HelmRepository`. Always check which type of source the chart uses before writing the HelmRelease.

The mandatory structure:

```yaml
---
apiVersion: helm.toolkit.fluxcd.io/v2
kind: HelmRelease
metadata:
  name: <app>
  namespace: <ns>
spec:
  # one of the two above (chartRef OR chart.spec)
  interval: 1h
  install:
    remediation:
      retries: 3            # use -1 for bjw-s app-template apps (matches this repo)
  upgrade:
    cleanupOnFail: true
    remediation:
      strategy: rollback
      retries: 3
  values:
    config: { ... }       # from Kubesearch popular-values
    secret:
      existingSecret: <app>-secret   # if secrets required
    httpRoute: { ... }   # if externally exposed (only for charts that template HTTPRoutes)
    monitoring:
      serviceMonitor:
        enabled: true     # if observability/ is in scope
    resources:
      requests: { cpu: 50m, memory: 256Mi }
      limits:   { memory: 1Gi }
```

Critical rules:

- Pick the right `chartRef` vs `chart.spec` pattern based on whether the chart's repo is OCI-typed or HTTP-typed.
- `namespace:` in metadata is **required** because this repo doesn't put app resources in `flux-system` by default (only the meta-layers do).
- Use `${SECRET_DOMAIN}` directly — Flux substitutes it at render time from `cluster-secrets`.
- For Gateway parentRef (when the chart supports `httpRoute`): `name: envoy-external`, `namespace: network` (always, unless user says internal).
- Default hostname: `{{ .Release.Name }}.${SECRET_DOMAIN}` (matches the wildcard cert).
- Pin chart versions to a specific tag, never `latest` or `*`. Kubesearch returns the latest; use exactly that.

### 6. Write the ExternalSecret (only if credentials required)

Model on `flux-operator/instance/externalsecret.yaml` or `konflate/instance/externalsecret.yaml`:

```yaml
---
apiVersion: external-secrets.io/v1
kind: ExternalSecret
metadata:
  name: &name <app>-secret
  namespace: <ns>
spec:
  refreshInterval: 5m
  secretStoreRef:
    kind: ClusterSecretStore
    name: onepassword
  target:
    name: *name
    creationPolicy: Owner
    template:
      data:
        # map 1Password fields → app's expected env-var / file names
        FOO: '{{ .foo }}'
  dataFrom:
    - extract:
        key: <1password-item-name>
```

If the chart expects env vars named `KONFLATE_*` or similar uppercase prefixes, the Secret key must match exactly (the chart uses `envFrom.secretRef` — keys become env names verbatim).

If you don't know the 1Password item name, **leave `key: <TODO-set-1password-item-name>` as a comment** and tell the user. Do not invent a name.

### 7. Wire into the parent kustomization

Add to `kubernetes/apps/<category>/kustomization.yaml` (create if missing, mirror `flux-system/kustomization.yaml` structure):

```yaml
---
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
namespace: <ns>
components:
  - ../../flux/components/common
resources:
  - ./<app>/ks.yaml
```

Or, if `<category>` doesn't exist yet, scaffold it with the `common` component from `../../flux/components/common` (this brings in `cluster-secrets`, `cluster-settings`, the `flux-system` namespace).

### 8. Validate

Run these in order. They catch ~all mistakes and don't require cluster access.

```bash
# 1. Kubeconform: schema validation for each manifest
kubeconform -strict -summary \
  -schema-location default \
  -schema-location 'https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json' \
  <all new .yaml files>

# 2. Kustomize: parent kustomization renders without errors
kubectl kustomize kubernetes/apps/<category>/

# 3. yamllint with repo's CI config
yamllint -c /home/russell/repos/k8s-gitops/.github/lint/.yamllint.yaml <new files>
```

If any fails, fix before reporting success. Common failures: missing trailing newline (yamllint), wrong indentation, missing namespace in metadata, `chartRef` pointing at a repo that wasn't created in step 3.

### 9. Report

Tell the user:
- All files created/edited (with paths and a one-line summary of each)
- The chart version and source repo chosen (with why)
- Any `TODO` placeholders left (1Password item name, real client ID, real webhook URL, etc.)
- Validation results (kubeconform pass-rate, kustomize rendered without error, yamllint exit 0)
- Whether the commit / push / PR step was done (default: no — only do it if explicitly asked)

## Bundled resources

This skill doesn't ship scripts today — the workflow is mostly read+write. As repeated patterns emerge (e.g., a shared `helm-values.yaml` template, or a `kustomization.yaml` generator), add them to `scripts/` and reference from this file.

- `references/k8s-gitops-conventions.md` — cheatsheet of repo paths, repo files, and conventions; read if you forget a path.
- `assets/helmrelease-template.yaml` — starter `HelmRelease` skeleton you can copy and fill in.

## Validation script (for skill testing)

The grader will run the following checks against generated manifests:

1. `kubeconform -strict` exits 0 on every new file.
2. `kubectl kustomize kubernetes/apps/<category>/` succeeds and includes both `Kustomization`s (`<app>` and `<app>-instance`).
3. `yamllint -c .github/lint/.yamllint.yaml` exits 0 on every new file.
4. The HelmRelease uses the correct source pattern for its repo type: `chartRef.kind: OCIRepository` for OCI repos, or `chart.spec.sourceRef.kind: HelmRepository` for HTTP repos. The referenced repo exists in `kubernetes/flux/meta/repositories/{oci,helm}/`.
5. If an `ExternalSecret` is created, it references `ClusterSecretStore/onepassword`.
6. If `httpRoute` is present, `parentRefs[0]` is `{name: envoy-external, namespace: network}` and `hostnames[0]` uses `${SECRET_DOMAIN}`.
