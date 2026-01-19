# Migration to Volsync Component Pattern

This guide covers migrating an app from a manual PVC to the volsync component pattern with automated backups.

## Prerequisites

- App has a commented-out volsync component in `ks.yaml`
- App has a manual PVC definition in `app/pvc.yaml`
- Know the UID/GID the app runs as (check the HelmRelease or container spec)

## Files to Modify

### 1. `kubernetes/apps/<namespace>/<app>/ks.yaml`
- Uncomment the volsync component
- Add `external-secrets` dependency
- Add `VOLSYNC_PUID` and `VOLSYNC_PGID` substitutes

### 2. `kubernetes/apps/<namespace>/<app>/app/kustomization.yaml`
- Remove `pvc.yaml` from resources

### 3. Delete `kubernetes/apps/<namespace>/<app>/app/pvc.yaml`

## Execution Steps

### Phase 1: Protect Existing Data

**Before pushing any changes**, protect the existing PV:

```bash
# Get the PV name from the existing PVC
PV_NAME=$(kubectl -n <namespace> get pvc <app> -o jsonpath='{.spec.volumeName}')

# Change reclaim policy to Retain (prevents data loss if PVC is deleted)
kubectl patch pv $PV_NAME -p '{"spec":{"persistentVolumeReclaimPolicy":"Retain"}}'
```

### Phase 2: Suspend Flux

Suspend the Kustomization to prevent Flux from deleting the PVC when we push changes:

```bash
flux suspend kustomization <app> -n <namespace>
```

### Phase 3: Push File Changes

Make the file modifications listed above, then commit and push.

### Phase 4: Create Initial Backup

Manually apply the ExternalSecret and ReplicationSource to backup the existing data:

```bash
# Apply ClusterSecretStore and ExternalSecret
cat <<EOF | kubectl apply -f -
---
apiVersion: external-secrets.io/v1
kind: ClusterSecretStore
metadata:
  name: <app>-restic
spec:
  provider:
    doppler:
      project: restic-template
      config: prd
      auth:
        secretRef:
          dopplerToken:
            name: doppler-token-auth-api
            key: dopplerToken
            namespace: flux-system
---
apiVersion: external-secrets.io/v1
kind: ExternalSecret
metadata:
  name: <app>-restic
  namespace: <namespace>
spec:
  secretStoreRef:
    kind: ClusterSecretStore
    name: <app>-restic
  target:
    name: <app>-restic
    template:
      data:
        RESTIC_REPOSITORY: "{{ .REPOSITORY_TEMPLATE }}/<app>"
        RESTIC_PASSWORD: "{{ .RESTIC_PASSWORD }}"
        AWS_ACCESS_KEY_ID: "{{ .AWS_ACCESS_KEY_ID }}"
        AWS_SECRET_ACCESS_KEY: "{{ .AWS_SECRET_ACCESS_KEY }}"
  dataFrom:
    - find:
        name:
          regexp: .*
EOF

# Wait for secret
kubectl -n <namespace> wait --for=condition=Ready externalsecret/<app>-restic --timeout=60s

# Apply ReplicationSource with manual trigger
cat <<EOF | kubectl apply -f -
---
apiVersion: volsync.backube/v1alpha1
kind: ReplicationSource
metadata:
  name: <app>-backup
  namespace: <namespace>
spec:
  sourcePVC: <app>
  trigger:
    manual: initial-backup
  restic:
    copyMethod: Snapshot
    repository: <app>-restic
    volumeSnapshotClassName: csi-ceph-blockpool
    cacheCapacity: 2Gi
    cacheStorageClassName: ceph-block
    cacheAccessModes: ["ReadWriteOnce"]
    storageClassName: ceph-block
    accessModes: ["ReadWriteOnce"]
    moverSecurityContext:
      runAsUser: <PUID>
      runAsGroup: <PGID>
      fsGroup: <PGID>
    retain:
      daily: 7
      within: 3d
EOF

# Wait for backup to complete
kubectl -n <namespace> wait --for=jsonpath='{.status.lastSyncTime}' replicationsource/<app>-backup --timeout=300s
```

### Phase 5: Delete Old PVC and Prepare for Restore

```bash
# Suspend HelmRelease
flux suspend helmrelease <app> -n <namespace>

# Scale down the deployment
kubectl -n <namespace> scale deployment <app> --replicas=0

# Wait for pod to terminate
kubectl -n <namespace> wait --for=delete pod -l app.kubernetes.io/name=<app> --timeout=60s

# Delete the backup ReplicationSource
kubectl -n <namespace> delete replicationsource <app>-backup

# The PVC should now delete (was pending deletion due to prune)
# If not, delete it manually:
kubectl -n <namespace> delete pvc <app> --ignore-not-found

# Delete the old PV (data is safe in restic backup)
kubectl delete pv $PV_NAME
```

### Phase 6: Bootstrap Restore

Create the ReplicationDestination that the new PVC will reference:

```bash
cat <<EOF | kubectl apply -f -
---
apiVersion: volsync.backube/v1alpha1
kind: ReplicationDestination
metadata:
  name: <app>
  namespace: <namespace>
spec:
  trigger:
    manual: restore-once
  restic:
    repository: <app>-restic
    copyMethod: Snapshot
    volumeSnapshotClassName: csi-ceph-blockpool
    cacheStorageClassName: ceph-block
    cacheAccessModes: ["ReadWriteOnce"]
    cacheCapacity: 2Gi
    storageClassName: ceph-block
    accessModes: ["ReadWriteOnce"]
    capacity: <VOLSYNC_CAPACITY>
    moverSecurityContext:
      runAsUser: <PUID>
      runAsGroup: <PGID>
      fsGroup: <PGID>
    enableFileDeletion: true
    cleanupCachePVC: true
    cleanupTempPVC: true
EOF

# Wait for restore to complete
kubectl -n <namespace> wait --for=jsonpath='{.status.latestImage.name}' replicationdestination/<app> --timeout=300s
```

### Phase 7: Resume Flux

```bash
# Resume the Kustomization - this creates the new PVC with dataSourceRef
flux resume kustomization <app> -n <namespace>

# Wait for reconciliation
flux reconcile kustomization <app> -n <namespace>

# Verify PVC is bound
kubectl -n <namespace> get pvc <app>

# Resume HelmRelease
flux resume helmrelease <app> -n <namespace>

# Wait for pod to be ready
kubectl -n <namespace> wait --for=condition=Ready pod -l app.kubernetes.io/name=<app> --timeout=120s
```

### Phase 8: Cleanup

```bash
# Delete the bootstrap ReplicationDestination (Flux manages the ongoing backups)
kubectl -n <namespace> delete replicationdestination <app>

# Delete manually created ClusterSecretStore (Flux will recreate it)
kubectl delete clustersecretstore <app>-restic

# Reconcile to let Flux recreate resources
flux reconcile kustomization <app> -n <namespace>
```

## Verification

```bash
# Pod is running
kubectl -n <namespace> get pods -l app.kubernetes.io/name=<app>

# PVC is bound with dataSourceRef
kubectl -n <namespace> get pvc <app>
kubectl -n <namespace> get pvc <app> -o jsonpath='{.spec.dataSourceRef}'

# ReplicationSource exists and has synced
kubectl -n <namespace> get replicationsource <app>

# Data is intact
kubectl -n <namespace> exec deploy/<app> -- ls -la <data-path>
```

## Key Lessons

1. **Always patch PV to Retain first** - This is your safety net if anything goes wrong.

2. **Suspend Kustomization before pushing** - Removing `pvc.yaml` from resources with `prune: true` causes immediate PVC deletion.

3. **PVC spec is immutable** - You cannot add `dataSourceRef` to an existing bound PVC. The PVC must be deleted and recreated.

4. **Bootstrap ReplicationDestination required** - The volsync component's PVC references a ReplicationDestination by name. This must exist before the PVC can provision from the snapshot.

5. **Order matters** - The ReplicationDestination must complete its restore and create a VolumeSnapshot before Flux can successfully create the new PVC.
