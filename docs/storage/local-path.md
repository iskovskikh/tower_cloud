# Local-path volumes on the nodes (`tower-local`)

`local-path-provisioner` (Rancher v0.0.30) gives **dynamic** RWO volumes backed
by a directory on whichever node the consuming pod runs on
(`/opt/local-path-provisioner`). It exists for state that must **not** live on
tower — chiefly **Jellyfin `/config`**, whose SQLite databases are unsafe on the
NFS export (see [`../adr/0002-stateful-services-on-tower.md`](../adr/0002-stateful-services-on-tower.md)
and `plan.md`).

Manifests: `k8s/apps/storage/local-path/` (upstream deploy manifest + our
`tower-local` StorageClass). Deployed by Flux.

## Trade-offs (important)

- **Node-local, not on tower.** The data lives on one node's disk, outside the
  `/mnt/raid1` RAID1 array — so it is **not** covered by tower's redundancy or
  backups. `tower` remains the store of record for everything else.
- **Pinned to a node.** The StorageClass is `WaitForFirstConsumer`, so the PV is
  created on the node the pod lands on, and the pod can only ever run there
  afterwards. If that node is lost, the data is gone.
- **Node disk is small** (~20 GiB allocatable). Jellyfin's `/config` is ~160 MiB
  today (metadata grows with the library), so it fits, but it is not the place
  for large data — use `tower-nfs` (media) or the tower block LUNs for that.
- `reclaimPolicy: Retain` — deleting the PVC keeps the on-disk directory.

## Use

```yaml
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: example
  namespace: example
spec:
  accessModes: [ReadWriteOnce]
  storageClassName: tower-local
  resources:
    requests:
      storage: 5Gi
```

There is no `volumeBindingMode: Immediate` here — the PVC stays `Pending` until a
pod uses it, then binds on that pod's node.

## Backup

Not covered by tower backups. If the config matters, snapshot it out
periodically (e.g. `kubectl cp <pod>:/config …`).
