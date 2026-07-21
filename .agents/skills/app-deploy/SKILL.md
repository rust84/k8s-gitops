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
- **Storage**: default block storage class is `ceph-block` (RWO); for RWX/NFS use the `duriel.internal` NAS at `/tank/Apps/<app>/`. PVCs are either static (`app/pvc.yaml`, brand-new apps with no existing backup) or volsync-managed (component `kubernetes/flux/components/volsync/`, requires an existing backup snapshot — see step 5a).
- **Validation tooling**: `flate` at `/home/linuxbrew/.linuxbrew/bin/flate` renders manifests through the same helm/kustomize SDKs Flux uses. Prefer `flate build ks <app>` and `flate build hr <app>` over `kubeconform` — flate catches chart-template and Flux-substitution errors that schema-only validation misses. See step 8.

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

The **default layout** (used by ~97% of apps in this repo — karakeep, immich, recyclarr, audiobookshelf, bazarr, etc.) is a single Kustomization per app, with the ExternalSecret colocated in `app/`:

```
kubernetes/apps/<category>/<app>/
├── app/
│   ├── helmrelease.yaml          # the main HelmRelease
│   ├── kustomization.yaml        # labels: app.kubernetes.io/name: <app>; resources: pvc + externalsecret + helmrelease
│   ├── externalsecret.yaml       # if app needs credentials (colocated, not in a separate instance/ dir)
│   ├── pvc.yaml                  # if a static PVC is needed (see step 5a, Pattern A)
│   ├── helm-values.yaml          # only if values are large/complex; otherwise inline
│   └── kustomizeconfig.yaml      # only if you need name reference transformations
└── ks.yaml                       # ONE Kustomization: <app> pointing at app/
```

Reference implementations to mirror:
- `kubernetes/apps/selfhosted/karakeep/` — clean single-Kustomization app with colocated ExternalSecret
- `kubernetes/apps/selfhosted/immich/` — app with NFS PVC + multiple HelmReleases in one Kustomization
- `kubernetes/apps/media/recyclarr/` — app with static ceph-block PVC and volsync component commented out

#### When to split into two Kustomizations (`app/` + `instance/`)

**Rarely.** Only ~3% of apps in this repo use the split. Use the split only when at least one of these applies:

- **Structural separation of concerns** — `app/` and `instance/` hold meaningfully different *kinds* of resources, not just an ExternalSecret. Example: `kubernetes/apps/flux-system/flux-operator/` — `app/` is the cluster-scoped Flux operator (CRDs), `instance/` is a *separate* HelmRelease for your GitHub App instance + webhook config tree (`instance/github/webhooks/`).
- **Strict startup ordering** — the operator must read its secret at process startup, before reconciling anything else, and you want Flux to guarantee the Secret materializes first. Example: `kubernetes/apps/flux-system/konflate/` — `instance/externalsecret.yaml` holds the GitHub App credentials the operator needs read at boot; the split enforces that `instance/` materializes before `app/` starts.

If neither condition applies, **use the single-Kustomization layout**. A simple ExternalSecret does not justify splitting — `dependsOn: external-secrets` on the app Kustomization already ensures the external-secrets operator is running before the ExternalSecret is reconciled, which is all the ordering you need for a typical app.

If you find yourself creating an `instance/` folder that contains only an ExternalSecret and its `kustomization.yaml`, stop and use the single-Kustomization layout instead.

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

### 5a. Plan persistence and PVCs

If the app needs persistent data (most do — databases, document stores, config files), pick one of four patterns. The **volsync ReplicationDestination requires an existing backup snapshot** to restore from — meaning **brand-new apps without a backup cannot use it on first deploy**. This is a hard constraint, not a workaround.

#### Pattern A — static PVC (first deploy of any new app)

Use this when there's no existing backup/snapshot of the data to restore from. This is the correct default for new apps.

1. Create `app/pvc.yaml`:

```yaml
---
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: <app>
  namespace: <ns>
  labels:
    app.kubernetes.io/name: &name <app>
    app.kubernetes.io/instance: *name
spec:
  accessModes:
    - ReadWriteOnce
  resources:
    requests:
      storage: 5Gi             # adjust per app — 1Gi for tiny configs, 5-10Gi for databases
  storageClassName: ceph-block
```

2. Add `./pvc.yaml` to `app/kustomization.yaml` resources (before `./helmrelease.yaml`).

3. In the HelmRelease, reference the PVC by its **YAML anchor `*app`**, NOT a Flux substitute variable like `${APP}`:

```yaml
metadata:
  name: &app <app>           # this anchor is what you reference below
...
values:
  persistence:
    config:
      existingClaim: *app    # ← correct; resolves at Helm template time
      globalMounts:
        - path: /app/data
```

Why not `${APP}` (Flux postBuild substitute): `${APP}` only resolves when the ks.yaml has `postBuild.substitute.APP: *app`, which the volsync component attaches. Without volsync, `${APP}` stays literal and the chart sees an invalid `existingClaim: ${APP}` — which makes the chart think it has to create a new PVC itself and fails with `accessMode is required for PVC <app>`. The YAML anchor is independent of Flux substitution and always works. (Most apps in this repo use `*app`; the volsync ones are the exception because they want the substitute var shared across both the ks.yaml and the HelmRelease.)

4. **Do NOT attach the volsync component** in `ks.yaml`. Don't add `postBuild.substitute` either. The static PVC in `app/pvc.yaml` is the only source of truth.

5. Leave a `NOTE:` comment in `ks.yaml` (above `dependsOn:`) so the next person knows to migrate:

```yaml
  # NOTE: volsync component is intentionally not attached yet. The PVC is
  # static (see app/pvc.yaml) because there's no existing backup to restore
  # from. Once the first volsync backup has completed, swap the static PVC
  # for the volsync component + ReplicationDestination and delete app/pvc.yaml.
```

Reference implementations: `kubernetes/apps/media/recyclarr/` (transitional — ks.yaml has volsync commented out + `app/pvc.yaml`), `kubernetes/apps/selfhosted/karakeep/` (fully static), `kubernetes/apps/media/radarr-archive/`.

#### Pattern B — volsync-managed PVC (existing backup exists)

Use this when an app already has at least one volsync backup snapshot in object storage, AND you're redeploying it (e.g. migrating, restructuring, restoring after a wipe).

1. Attach the volsync component to the app's ks.yaml:

```yaml
spec:
  commonMetadata: ...
  components:
    - ../../../../flux/components/volsync
  postBuild:
    substitute:
      APP: *app
      VOLSYNC_CAPACITY: 5Gi
      VOLSYNC_PUID: "1000"
      VOLSYNC_PGID: "1000"
  dependsOn:
    - name: volsync
      namespace: volsync-system
    # ...other deps
```

2. The volsync component (`kubernetes/flux/components/volsync/`) generates:
   - A `PersistentVolumeClaim` with `dataSourceRef.kind: ReplicationDestination` (so the PVC is populated from the last backup snapshot on first create)
   - A `ReplicationDestination` (for restore) and a `ReplicationSource` (for ongoing backups), both on a schedule

3. Do NOT create `app/pvc.yaml` — the component already provides it. The PVC's `claimName` matches the value of `${APP}`.

4. In the HelmRelease, reference the PVC via the Flux substitute var:

```yaml
persistence:
  config:
    existingClaim: ${APP}    # ← matches the claim name the volsync component creates
    globalMounts:
      - path: /app/data
```

Reference implementations: any app whose `ks.yaml` attaches `../../../../flux/components/volsync` (e.g. `kubernetes/apps/ai/open-webui/app/helmrelease.yaml` uses `${APP}` substitution successfully because volsync is in scope).

#### Pattern C — NFS-backed PVC (RWX shared storage)

Use this when the app needs ReadWriteMany storage across multiple pods or nodes, OR when the data already lives on the NAS at `duriel.internal:/tank/Apps/<app>/`. Model on `kubernetes/apps/selfhosted/immich/app/pvc.yaml` — a paired `PersistentVolume` (NFS) + `PersistentVolumeClaim` with a custom `storageClassName` to prevent collisions.

Do NOT attach the volsync component for NFS apps — volsync only knows how to snapshot block/Ceph volumes.

#### Pattern D — `emptyDir` for cache / scratch / tmp

For ephemeral mounts (PM2 runtime, log dirs, tmp) that don't need to survive pod restarts:

```yaml
persistence:
  tmp:
    type: emptyDir
    globalMounts:
      - path: /tmp
```

No PVC needed. Always `readOnlyRootFilesystem: true` on the container when using `emptyDir` for write paths — otherwise the app crashes on its first write attempt to the root fs.

#### Migration path: static PVC → volsync-managed

Once an app deployed with Pattern A has been running long enough for a volsync backup to complete (manually triggered — Pattern A doesn't set up scheduled backups, so you'll need to either temporarily enable volsync in a one-off way OR just leave it on Pattern A indefinitely if the data is reproducible):

1. Ensure there's a `ReplicationDestination` snapshot in object storage for this app.
2. Delete the static `app/pvc.yaml`.
3. Attach the volsync component to `ks.yaml` and add the `postBuild.substitute` block.
4. Change `existingClaim: *app` in the HelmRelease to `existingClaim: ${APP}`.
5. The next Flux reconcile will replace the static PVC with a ReplicationDestination-populated one. The existing data on disk survives because volsync's `copyMethod: Snapshot` creates the new PVC from the snapshot (which has the same content).

#### Known chart issues to watch for

- **app-template v5.0.1**: When `persistence.<name>.existingClaim` references a non-existent or literal-invalid claim name (e.g. `${APP}` when postBuild isn't wired), the chart silently falls back to creating its own PVC and errors with `accessMode is required for PVC <name>`. The fix is always to ensure the claim name resolves at Helm template time — either via YAML anchor (`*app`, always works) or via postBuild substitute (only when volsync is attached).
- **`readOnlyRootFilesystem: true`** in the container securityContext means any path the app writes to must be covered by a `persistence` entry. A 4-hour debugging session of "app crashes on startup with no error" can be caused by the app trying to write `/tmp/.cache` with no `emptyDir` mount there.

### 6. Write the ExternalSecret (only if credentials required)

In the **default single-Kustomization layout** (step 4), the ExternalSecret lives at `app/externalsecret.yaml` alongside the HelmRelease. Model on `kubernetes/apps/selfhosted/karakeep/app/externalsecret.yaml`:

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

Use `flate` (installed at `/home/linuxbrew/.linuxbrew/bin/flate`) — it renders Flux manifests using the upstream helm, kustomize, and source SDKs, so it catches errors that pure-schema validators miss (chart template failures, missing ConfigMaps referenced by `valuesFrom`, broken `chartRef` references, PVC `accessMode` issues, etc.).

```bash
# 1. flate build ks: render the Kustomization (catches volsync component errors,
#    missing PVCs, broken kustomize refs in the ks.yaml tree). The output is
#    the full set of resources Flux would apply for this ks.
flate build ks <app> --no-progress

# 2. flate build hr: render the HelmRelease through the actual chart (catches
#    chart template errors, invalid values, missing existingClaim refs, env
#    var schema mistakes). The output is the full set of resources Helm would
#    produce.
flate build hr <app> --no-progress

# 3. flate get ks: confirm both Kustomizations (<app> and <app>-instance)
#    appear in the target namespace.
flate get ks --no-progress | grep <app>

# 4. yamllint with repo's CI config (style + trailing newline checks)
yamllint -c /home/russell/repos/k8s-gitops/.github/lint/.yamllint.yaml <new files>
```

Why `flate` over `kubeconform`/`kubectl kustomize`:

- **`kubeconform` only validates schemas** — it does not run Helm. A HelmRelease that passes kubeconform (schema-valid YAML) can still blow up when Helm renders the chart (e.g. `chartRef` pointing at a non-existent OCIRepository, `persistence.config.existingClaim: ${APP}` where `${APP}` is never substituted by Flux postBuild, `envFrom.secretRef.name` referencing a Secret that doesn't exist in the rendered tree). `flate build hr` does the real Helm render and surfaces these.
- **`kubectl kustomize` does not understand Flux** — it can't resolve `kustomization.yaml` files that live in a different `path` than the Kustomization's target, can't follow OCIRepository/HelmRepository sources, and can't substitute `postBuild.substitute` variables. `flate build ks` does all three.
- **`flate` is the same engine the cluster uses** — it's the SDK Flux's source controller and helm-controller are built on, so a render that succeeds locally will succeed in-cluster (modulo the live Secret values, which `--allow-missing-secrets` skips conservatively if a Secret is declared by an in-repo `ExternalSecret`).

Common failures `flate` surfaces (and `kubeconform` does not):

- `accessMode is required for PVC <name>` — the app-template chart v5.0.1 wants `accessMode` even when `existingClaim` is set; usually means the `existingClaim` value is a literal `${VAR}` that Flux postBuild never substituted (only the volsync component's ks.yaml sets postBuild). Fix: use a YAML anchor `*app` instead of a Flux substitute var, or attach the volsync component / add a postBuild block.
- `execution error at ... chart-content.v1.tar+gzip ...` — the OCIRepository in `kubernetes/flux/meta/repositories/oci/` is wrong or missing.
- `could not find Secret <name> in namespace <ns>` in HelmRelease `valuesFrom` — `ExternalSecret` for that Secret lives in a different `instance/` folder that hasn't been wired into `ks.yaml` yet.
- `HelmRelease <ns>/<app> failed: no chart matching version "<x>"` — pinned chart version doesn't exist in the referenced repo.

### 9. Report

Tell the user:
- All files created/edited (with paths and a one-line summary of each)
- The chart version and source repo chosen (with why)
- Any `TODO` placeholders left (1Password item name, real client ID, real webhook URL, etc.)
- Validation results (`flate build ks` and `flate build hr` exit 0, `yamllint` exit 0)
- Whether the commit / push / PR step was done (default: no — only do it if explicitly asked)

## Bundled resources

This skill doesn't ship scripts today — the workflow is mostly read+write. As repeated patterns emerge (e.g., a shared `helm-values.yaml` template, or a `kustomization.yaml` generator), add them to `scripts/` and reference from this file.

- `references/k8s-gitops-conventions.md` — cheatsheet of repo paths, repo files, and conventions; read if you forget a path.
- `assets/helmrelease-template.yaml` — starter `HelmRelease` skeleton you can copy and fill in.

## Validation script (for skill testing)

The grader will run the following checks against generated manifests:

1. `flate build ks <app> --no-progress` exits 0 and emits the expected resources (PVC, ReplicationDestination if volsync-attached, etc.).
2. `flate build hr <app> --no-progress` exits 0 and emits the Deployment + Service + HTTPRoute + ServiceAccount.
3. `flate get ks --no-progress` lists the `<app>` Kustomization in the target namespace. (Only `<app>` by default — `<app>-instance` appears only if the two-Kustomization split from step 4's "When to split" branch is in use.)
4. `yamllint -c .github/lint/.yamllint.yaml` exits 0 on every new file.
5. The HelmRelease uses the correct source pattern for its repo type: `chartRef.kind: OCIRepository` for OCI repos, or `chart.spec.sourceRef.kind: HelmRepository` for HTTP repos. The referenced repo exists in `kubernetes/flux/meta/repositories/{oci,helm}/`.
6. If an `ExternalSecret` is created, it references `ClusterSecretStore/onepassword`.
7. If `httpRoute` is present, `parentRefs[0]` is `{name: envoy-external, namespace: network}` and `hostnames[0]` uses `${SECRET_DOMAIN}`.
8. If a static PVC is used (Pattern A in step 5a), the HelmRelease references it via YAML anchor (`*app`), NOT via `${APP}` Flux substitute var (unless the volsync component is also attached with `postBuild.substitute.APP`).
