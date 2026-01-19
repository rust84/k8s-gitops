  # Migration to Volsync Component Pattern

  ## Current State
  - **Volsync component**: Commented out in `ks.yaml`
  - **PVC**: Manual definition in `app/pvc.yaml` (1Gi, ceph-block)
  - **Backup**: None (no ReplicationSource or restic secret)
  - **UID/GID**: 1000

  ## Files to Modify

  ### 1. `kubernetes/apps/selfhosted/appname/ks.yaml`
  - Uncomment the volsync component
  - Add `external-secrets` dependency
  - Add `VOLSYNC_PUID` and `VOLSYNC_PGID` substitutes (1000)

  ### 2. `kubernetes/apps/selfhosted/appname/app/kustomization.yaml`
  - Remove `pvc.yaml` from resources

  ### 3. Delete `kubernetes/apps/selfhosted/appname/app/pvc.yaml`

  ## Execution Steps

  1. **Make file changes** (listed above)
  2. **User commits and pushes**
  3. **Create initial backup**:
  - Apply ExternalSecret for `appname-restic`
  - Apply ReplicationSource to backup existing PVC
  - Trigger manual backup
  - Wait for backup to complete
  4. **Run bootstrap-restore**:
  - Suspend HelmRelease, scale to 0
  - Delete existing PVC
  - Create bootstrap ReplicationDestination named `appname`
  - Wait for VolumeSnapshot creation
  - Reconcile Flux (creates PVC with dataSourceRef, binds from snapshot)
  - Resume app
  - Cleanup ReplicationDestination
  5. **Cleanup manual resources** (Flux will recreate them)

  ## Verification
  - `kubectl -n selfhosted get pods -l app.kubernetes.io/name=appname` shows Running
  - `kubectl -n selfhosted get pvc appname` shows Bound with dataSourceRef
  - `kubectl -n selfhosted get replicationsource appname` exists
  - `kubectl -n selfhosted exec deploy/appname -- ls /home/node/.appname` shows data