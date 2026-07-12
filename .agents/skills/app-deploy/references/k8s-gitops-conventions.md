# k8s-gitops repo cheatsheet

Quick reference for paths and conventions used by `rust84/k8s-gitops`. If you're not sure where a file goes, check here first.

## Top-level layout

```
kubernetes/
├── apps/                   # per-application manifests
├── bootstrap/              # cluster bootstrap (Talos, initial Flux, secrets)
├── flux/
│   ├── cluster/            # root Kustomization that pulls in apps/...
│   ├── components/common/  # namespace + cluster-secrets + cluster-settings + sops-age
│   └── meta/
│       └── repositories/   # chart sources (helm/, oci/)
│           ├── helm/
│           └── oci/
└── docs/
```

## App directory layout

```
kubernetes/apps/<category>/<app>/
├── app/
│   ├── helmrelease.yaml
│   ├── kustomization.yaml
│   ├── helm-values.yaml          (optional, if values are large)
│   └── kustomizeconfig.yaml      (optional)
├── instance/                     (optional, for per-instance secrets/config)
│   ├── externalsecret.yaml
│   └── kustomization.yaml
└── ks.yaml                       (two Kustomizations: <app> + <app>-instance)
```

## Categories (existing)

`ai`, `cert-manager`, `database`, `default`, `external-secrets`, `flux-system`, `home`, `kube-system`, `media`, `network`, `observability`, `openebs-system`, `rook-ceph`, `selfhosted`, `system-upgrade`, `volsync-system`.

If the new app doesn't fit any category, pick the closest match and tell the user. Add a new category only if the user agrees.

## Chart sources (already wired)

`kubernetes/flux/meta/repositories/helm/` (HelmRepository) and `kubernetes/flux/meta/repositories/oci/` (OCIRepository).

Already registered: `altinity`, `authentik`, `backube`, `bitnami`, `bjw-s`, `cilium`, `cloudnative-pg`, `controlplaneio`, `coredns`, `democratic-csi`, `descheduler`, `emqx`, `external-dns`, `external-secrets`, `grafana`, `home-operations`, `intel`, `jetstack`, `metrics-server`, `node-feature-discovery`, `openebs`, `piraeus`, `prometheus-community`, `rook-ceph`, `spegel`, `stakater`, `stevehipwell`.

OCI only: `app-template`, `envoy-gateway`.

If the chart you need isn't on this list, create a new `<name>.yaml` in the right directory and add it to the parent `kustomization.yaml`.

## Cluster-wide references

- `ClusterSecretStore/onepassword` — defined in `kubernetes/apps/external-secrets/onepassword/store/clustersecretstore.yaml`. Use for app credentials.
- `Gateway/envoy-external` (ns `network`) — wildcard HTTPS on `*.${SECRET_DOMAIN}`. Used by every public-facing app's `HTTPRoute`.
- `Gateway/envoy-internal` (ns `network`) — internal-only traffic.
- `Secret/cluster-secrets` (ns `flux-system`) — holds `SECRET_DOMAIN`, `SECRET_CLOUDFLARE_*`. Flux substitutes `${SECRET_DOMAIN}` in HelmRelease values at render time. Don't hardcode the domain.
- `Secret/sops-age` (ns `flux-system`) — for SOPS-encrypted cluster-bootstrap secrets. **Do not** use SOPS for per-app secrets — use ExternalSecret instead.

## CI lint config

`/home/russell/repos/k8s-gitops/.github/lint/.yamllint.yaml` — use this with `yamllint -c` for the same rules CI uses. Repo-level `.yamllint.yml` is stricter and produces warnings; CI is the source of truth.

## Useful commands

```bash
# validate a new app's manifests
kubeconform -strict -summary \
  -schema-location default \
  -schema-location 'https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json' \
  <new files>

kubectl kustomize kubernetes/apps/<category>/

yamllint -c /home/russell/repos/k8s-gitops/.github/lint/.yamllint.yaml <new files>

# once on cluster, force a Flux reconcile
task flux:hr-sync         # all HelmReleases
task flux:ks-sync         # all Kustomizations
task k:ks-apply PATH=flux-system/konflate   # build + apply a single Kustomization
```
