# Static RWO block volumes on tower (tower-iscsi)

Dynamic block provisioning has no off-the-shelf CSI driver for this tower
(mdadm RAID1 + in-kernel LIO via configfs, **no ZFS**): democratic-csi's iSCSI
drivers all require ZFS (and `targetcli`, which is AUR-only on Arch), and
`csi-driver-iscsi` only attaches pre-created LUNs. So **RWO block volumes use
static PersistentVolumes**, while RWX filesystems use the dynamic
`tower-nfs` StorageClass (csi-driver-nfs).

This is the same path Vault uses for its Raft data.

## Add a block volume

1. **Create the backing LUN on tower** — append to `tower_iscsi_volumes` in
   `ansible/roles/tower_storage/defaults/main.yml`:

   ```yaml
   tower_iscsi_volumes:
     - { name: vol1,  size: 10G }   # lun 0
     - { name: vault, size: 10G }   # lun 1
     - { name: mydb,  size: 20G }   # lun 2  <-- new
   ```

   The **LUN index is the position in this list** (0-based). Then apply:

   ```bash
   ansible-playbook ansible/playbooks/site.yml --limit tower
   ansible-playbook ansible/playbooks/k8s-storage.yml   # rescan nodes (optional)
   ```

2. **Render the PV + PVC** with the helper (uses the LUN index from step 1):

   ```bash
   scripts/new-iscsi-pv.sh mydb 2 20Gi
   ```

   Commit the output under `k8s/` (Flux applies it) and set
   `spec.volumeName` so the PVC binds to that PV. Reference the PVC from a
   workload with `accessModes: [ReadWriteOnce]`.

## Notes

- Backing files live in `/mnt/raid1/k8s-iscsi/<name>.img`; the target IQN is
  `iqn.2026-10.lan.tower:k8s`, portal `192.168.30.200:3260`.
- **Thin / "floating" volumes:** add `sparse: true` to a volume (e.g.
  `{ name: opencloud, size: 500G, sparse: true }`) to back it with a **sparse**
  file (`truncate -s`) instead of a preallocated one (`fallocate -l`). The LUN
  still reports its full size but only consumes array blocks as the guest writes.
  Caveat: thin provisioning — if `/mnt/raid1` fills up, guest writes fail
  (ENOSPC); monitor free space. Changing this on an existing volume requires
  recreating its backing file (drop the LUN + backstore, delete the file, rerun
  the play) and reformatting the filesystem.
- **Single writer:** a LUN is RWO; never mount it on two nodes. The initiator
  role intentionally does **not** pre-login nodes (the kubelet plugin manages
  login/logout) to avoid stale views and accidental reformat.
- `reclaimPolicy: Retain` — deleting the PVC keeps the LUN/data on tower.
- `tower-iscsi-static` is a `no-provisioner` StorageClass; only PVs that carry
  that `storageClassName` bind to PVCs using it.
