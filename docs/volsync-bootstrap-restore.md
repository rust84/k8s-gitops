# Volsync Bootstrap Restore Guide

This document explains how to restore a PVC from a volsync backup when migrating to the standard volsync component pattern, where the PVC uses a `dataSourceRef` to clone from a `ReplicationDestination`.

## Quick Start

Use the automated task:

```bash
task volsync:bootstrap-restore rsrc=<app> namespace=<namespace> capacity=<size>

# Example:
task volsync:bootstrap-restore rsrc=seerr namespace=media capacity=1Gi
```

Optional parameters:
- `storageclass` - Storage class (default: `ceph-block`)
- `snapshotclass` - VolumeSnapshot class (default: `csi-ceph-blockpool`)
- `puid` - User ID for mover (default: `568`)
- `pgid` - Group ID for mover (default: `568`)
- `previous` - Number of snapshots to go back (default: `1`, most recent)

The task includes a safety check that verifies backups exist before proceeding.

The rest of this document explains the manual process for reference.

## Background

The standard volsync component (`kubernetes/flux/components/volsync`) creates PVCs with a `dataSourceRef` pointing to a `ReplicationDestination` of the same name:

```yaml
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: "${APP}"
spec:
  dataSourceRef:
    kind: ReplicationDestination
    apiGroup: volsync.backube
    name: "${APP}"
  # ...
```

This pattern uses Kubernetes volume populators - the PVC remains in `Pending` state until the referenced `ReplicationDestination` exists and has a `latestImage` (VolumeSnapshot) available.

## The Problem

The standard `task volsync:restore` command creates a `ReplicationDestination` with a timestamped name (e.g., `seerr-seerr-221015`) and uses `copyMethod: Direct`, which expects to write directly to an existing, bound PVC.

This creates a chicken-and-egg problem:
1. The PVC expects a `ReplicationDestination` named `${APP}` (e.g., `seerr`)
2. The restore task creates one named `${APP}-${APP}-${TIMESTAMP}`
3. The PVC stays `Pending` because the expected `ReplicationDestination` doesn't exist
4. The restore job can't run because the PVC isn't bound

## Solution: Bootstrap Restore

To bootstrap a new app into this pattern, you need to manually create a `ReplicationDestination` with the correct name and `copyMethod: Snapshot`.

### Step-by-Step Process

#### 1. Suspend the Application

```bash
# Suspend the HelmRelease
flux -n <namespace> suspend helmrelease <app>

# Scale down the deployment (if it exists)
kubectl -n <namespace> scale deployment <app> --replicas 0

# Wait for pods to terminate
kubectl -n <namespace> wait pod --for delete --selector="app.kubernetes.io/name=<app>" --timeout=2m
```

#### 2. Delete the Pending PVC

If the PVC exists and is stuck in `Pending` state:

```bash
kubectl -n <namespace> delete pvc <app>
```

#### 3. Create the Bootstrap ReplicationDestination

Create a `ReplicationDestination` with the **exact name** that the PVC expects (matching `${APP}`), using `copyMethod: Snapshot`:

```yaml
apiVersion: volsync.backube/v1alpha1
kind: ReplicationDestination
metadata:
  name: <app>  # Must match the PVC's dataSourceRef.name
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
    capacity: <size>  # Match VOLSYNC_CAPACITY from ks.yaml
    moverSecurityContext:
      runAsUser: 568
      runAsGroup: 568
      fsGroup: 568
    previous: 1  # Restore from most recent snapshot (use 2 to skip potentially corrupt latest)
    enableFileDeletion: true
    cleanupCachePVC: true
    cleanupTempPVC: true
```

Apply it:

```bash
kubectl apply -f replicationdestination.yaml
```

#### 4. Wait for Restore to Complete

Monitor the restore progress:

```bash
# Watch the ReplicationDestination status
kubectl -n <namespace> get replicationdestination <app> -w

# Check for completion
kubectl -n <namespace> get replicationdestination <app> -o jsonpath='{.status.latestMoverStatus}'
```

The restore is complete when:
- `status.latestMoverStatus.result` is `Successful`
- `status.latestImage` contains a VolumeSnapshot reference

#### 5. Reconcile the Flux Kustomization

Trigger the Kustomization to create the PVC:

```bash
flux reconcile kustomization <app> -n <namespace>
```

The PVC will now be created and will clone from the VolumeSnapshot via the volume populator.

#### 6. Verify PVC is Bound

```bash
kubectl -n <namespace> get pvc <app>
```

The PVC should show `STATUS: Bound`.

#### 7. Resume the Application

```bash
# Resume the HelmRelease
flux -n <namespace> resume helmrelease <app>

# If needed, scale the deployment back up
kubectl -n <namespace> scale deployment <app> --replicas 1
```

#### 8. Clean Up

Delete the bootstrap ReplicationDestination (optional, but recommended for cleanliness):

```bash
kubectl -n <namespace> delete replicationdestination <app>
```

The VolumeSnapshot will be cleaned up automatically.

## Common Issues

### Missing Cache PVC

Some applications have separate cache PVCs (e.g., `<app>-cache`) that are not backed by volsync. If the pod fails to schedule with an error like:

```
persistentvolumeclaim "<app>-cache" not found
```

Create the cache PVC manually:

```yaml
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: <app>-cache
  namespace: <namespace>
spec:
  accessModes: ["ReadWriteOnce"]
  resources:
    requests:
      storage: 1Gi
  storageClassName: ceph-block
```

Then add this PVC definition to your git repository to make it GitOps-managed.

### Kustomization Not Found in flux-system

The Flux Kustomization for apps is typically in the app's namespace (e.g., `media`), not `flux-system`. Use:

```bash
flux reconcile kustomization <app> -n <namespace>
```

### Restore Job Not Starting

If the volsync mover pod isn't created, check:
1. The `seerr-restic` secret exists and has valid credentials
2. The volsync controller is running: `kubectl -n volsync-system get pods`
3. Events on the ReplicationDestination: `kubectl -n <namespace> describe replicationdestination <app>`

## Quick Reference

```bash
# Automated task (recommended)
task volsync:bootstrap-restore rsrc=seerr namespace=media capacity=1Gi

# Or with all options:
task volsync:bootstrap-restore \
  rsrc=seerr \
  namespace=media \
  capacity=1Gi \
  storageclass=ceph-block \
  snapshotclass=csi-ceph-blockpool \
  puid=568 \
  pgid=568 \
  previous=1
```

### Manual Process

```bash
# Full bootstrap restore sequence (manual)
APP=seerr
NS=media
CAPACITY=1Gi

# 1. Suspend
flux -n $NS suspend helmrelease $APP
kubectl -n $NS scale deployment $APP --replicas 0 2>/dev/null || true

# 2. Delete pending PVC
kubectl -n $NS delete pvc $APP --ignore-not-found

# 3. Create bootstrap ReplicationDestination
cat <<EOF | kubectl apply -f -
apiVersion: volsync.backube/v1alpha1
kind: ReplicationDestination
metadata:
  name: $APP
  namespace: $NS
spec:
  trigger:
    manual: restore-once
  restic:
    repository: ${APP}-restic
    copyMethod: Snapshot
    volumeSnapshotClassName: csi-ceph-blockpool
    storageClassName: ceph-block
    accessModes: ["ReadWriteOnce"]
    capacity: $CAPACITY
    cacheStorageClassName: ceph-block
    cacheAccessModes: ["ReadWriteOnce"]
    cacheCapacity: 2Gi
    moverSecurityContext:
      runAsUser: 568
      runAsGroup: 568
      fsGroup: 568
    previous: 1
EOF

# 4. Wait for restore
kubectl -n $NS wait replicationdestination $APP --for=jsonpath='{.status.latestMoverStatus.result}'=Successful --timeout=30m

# 5. Reconcile to create PVC
flux reconcile kustomization $APP -n $NS

# 6. Verify PVC bound
kubectl -n $NS get pvc $APP

# 7. Resume
flux -n $NS resume helmrelease $APP
kubectl -n $NS scale deployment $APP --replicas 1 2>/dev/null || true

# 8. Clean up
kubectl -n $NS delete replicationdestination $APP
```
