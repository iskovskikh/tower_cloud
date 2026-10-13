# Static RWO block volumes on tower (tower-iscsi)

Dynamic block provisioning has no off-the-shelf CSI driver for this tower
(mdadm RAID1 + in-kernel LIO via configfs, **no ZFS**): democratic-csi's iSCSI
drivers all require ZFS (and `targetcli`, which is AUR-only on Arch), and
`csi-driver-iscsi` only attaches pre-created LUNs. So **RWO block volumes use
static PersistentVolumes**, while RWX filesystems use the dynamic
`tower-nfs` StorageClass (csi-driver-nfs).

This is the same path Vault uses for its Raft data. Per
[ADR-0002](../adr/0002-stateful-services-on-tower.md), block LUNs are limited to
what a k8s pod genuinely needs as local block — **Vault** (Raft) and
**OpenCloud** metadata (POSIX xattrs). Databases and object storage live on
tower, not on LUNs.

## Add a block volume

1. **Create the backing LUN on tower** — append to `tower_iscsi_volumes` in
   `ansible/roles/tower_storage/defaults/main.yml`:

   ```yaml
   tower_iscsi_volumes:
     - { name: vault,    size: 10G, lun: 1 }
     - { name: jellyfin, size: 20G, lun: 4 }
     - { name: mydb,     size: 20G, lun: 6 }   # <-- new (next free LUN number)
   ```

   The **LUN number is explicit** (`lun:`), not the list position, so you can
   free a slot without renumbering the others. Pick an unused number and apply:

   ```bash
   ansible-playbook ansible/playbooks/site.yml --limit tower
   ansible-playbook ansible/playbooks/k8s-storage.yml   # rescan nodes (optional)
   ```

2. **Render the PV + PVC** with the helper (uses the LUN number from step 1):

   ```bash
   scripts/new-iscsi-pv.sh mydb 6 20Gi
   ```

   Commit the output under `k8s/` (Flux applies it) and set
   `spec.volumeName` so the PVC binds to that PV. Reference the PVC from a
   workload with `accessModes: [ReadWriteOnce]`.

## Retire a block volume

1. Move the data off / confirm nothing uses it, then delete the PV + PVC and the
   entry from `tower_iscsi_volumes` in `defaults/main.yml`. Re-run the tower
   play (it only *creates*; it never deletes).
2. On **tower**, drop the LUN and backstore and delete the backing file:

   ```bash
   T=/sys/kernel/config/target/iscsi/iqn.2026-10.lan.tower:k8s/tpgt_1/lun
   C=/sys/kernel/config/target/core/fileio_0
   lun=6  # the number being retired
   sudo rm -f "$T/lun_$lun/mydb"        # unlink the backstore from the LUN
   sudo rmdir "$T/lun_$lun"
   sudo sh -c "echo 0 > $C/mydb/enable" 2>/dev/null || true
   sudo rmdir "$C/mydb"
   sudo rm -f /mnt/raid1/k8s-iscsi/mydb.img   # frees the array space
   ```

   Because LUN numbers are explicit, the surviving volumes keep their indices
   and their PVs keep working.

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
