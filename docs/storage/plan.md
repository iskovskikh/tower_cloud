# Storage plan — tower as a storage appliance for k8s

**Status:** draft / PoC plan. **Not implemented.** Read-only repo — nothing here
has been applied to any host yet.
**Drafted:** 2026-10-13 · observed from `archasus`.

Goal: turn **tower** (`192.168.30.200`) into the cluster's storage appliance
(СХД) and hand out space to pods **on demand**:

- **block, RWO** volumes for databases (iSCSI),
- **object, S3** buckets for artifacts/backups/ML.

Priority for now is a **PoC on what already exists**; durability comes later.

## Context (from `../network/inventory.yaml`)

- **tower** = Arch, data array `/mnt/raid1` — Intel IMSM **fakeraid** (`md126`,
  `sdb+sdc`). No snapshots, no hardware redundancy. Holds Jellyfin/Transmission
  media — must **not** be repartitioned for the PoC.
- **k8s** = `k8s-node-1..4`, 4 VMs on **proxmox** (`192.168.30.201`, VLAN 120,
  4 GB each). Pods run here, storage must arrive over the network from tower.
- **Networks:** VLAN 120 and `192.168.30.0/24` are separate segments; the
  route/firewall between them is **unconfirmed**.
- MinIO, NFS and SMB were **removed from tower on 2026-10-12** (see CHANGELOG).

## Target architecture (PoC)

```
tower 192.168.30.200  (storage appliance)
 ├─ iSCSI target :3260   LIO/targetcli, fileio backstore on /mnt/raid1/k8s-iscsi
 └─ MinIO S3     :9000   buckets for object workloads
        │   VLAN120 ↔ 192.168.30.0/24  (verify route + firewal!)
        ▼
k8s (k8s-node-1..4, VMs on proxmox)
 ├─ StorageClass tower-iscsi (RWO, xfs/ext4) → PVCs on demand
 └─ StorageClass tower-s3    (csi-s3)         → optional
```

## Phases

### Phase 0 — static LUN (validate the path)  ← start here

Lowest risk, everything **in-kernel, no ZFS, no DKMS**. Proves routing,
initiator setup and that a pod can mount block storage from tower.

On **tower**:

1. Install the LIO target tooling (`targetcli` / `python-rtslib`).
2. Create a **file-backed LUN** (sparse file, e.g.
   `/mnt/raid1/k8s-iscsi/vol1.img`). No repartitioning of the media array.
3. Create an IQN target, one LUN, ACLs per worker initiator, portal
   `192.168.30.200:3260`.

On each **k8s worker** (VM):

4. Install `open-iscsi` (`iscsid`, `iscsi_tcp`), `iscsiadm -m discovery/login`,
   confirm the device appears in `lsblk`.

In **k8s**:

5. Create a static `PersistentVolume` (`iscsi` volume source) + `PVC`, bind it,
   mount in a test pod.

Exit criteria: a pod reads/writes a block volume that lives on tower; latency
measured.

### Phase 1 — dynamic provisioning (block, RWO)

Pick one driver (trade-offs below) and replace the static PV with a
`StorageClass`.

| Route | Pros | Cons for this setup |
|---|---|---|
| **democratic-csi `zfs-generic-iscsi`** | mature; snapshots / clones / resize "out of the box"; "works with any ZoL installation" | requires **ZFS on tower** (Arch + kernel `7.2.9` → DKMS pain; ZFS on top of fakeraid `md126` is wrong) |
| **LINSTOR / Piraeus CSI** | no ZFS; LVM-thin pool; later replication/HA | controller + satellites, DRBD/DKMS modules, more moving parts |

Both add kernel modules (ZFS/DKMS or DRBD) on a bleeding-edge Arch kernel —
the main hidden risk.

### Phase 2 — object storage (S3)

- Run **MinIO** on tower (container) on `:9000`; apps use the **S3 API** with
  credentials — no CSI needed.
- Only if a pod needs a *filesystem view* of a bucket, add a CSI-S3 driver
  (e.g. `csi-s3` / geesefs) as an optional `StorageClass`.

### Phase 3 — durability (out of PoC scope)

- Replace fakeraid `md126` with **mdadm RAID1 or ZFS on raw disks**.
- Backups + snapshots for the block volumes and `/mnt/raid1`.
- See open questions in `../network/inventory.yaml`.

## Risks

- **Fakeraid as the only array** under iSCSI for databases = SPOF. Fine for a
  PoC, wrong for production data.
- **Block traffic through the router** (VLAN 120 ↔ `.30`) adds latency; workers
  on proxmox's NVMe are faster locally.
- Workers need `open-iscsi` (`iscsid` + `iscsi_tcp`).
- `targetcli`/LIO config is not persistent across rebuilds unless exported.

## Open questions

1. Where should block backing live: file on `/mnt/raid1` (PoC) vs dedicated
   disks / LVM-thin later?
2. Is VLAN 120 ↔ `192.168.30.0/24` routed and firewalled? Need `3260` (iSCSI)
   and `9000` (S3) reachable from workers.
3. Which Phase-1 driver: democratic-csi + ZFS, or LINSTOR + LVM-thin?
4. Worker VM OS and whether `open-iscsi` can be installed there.
5. How to snapshot / back up block volumes?
6. Will tower stay on Arch with a bleeding-edge kernel (ZFS/DRBD DKMS
   feasibility)?

## References

- democratic-csi — <https://github.com/democratic-csi/democratic-csi>
  (drivers include `zfs-generic-iscsi` for any ZoL host).
- LINSTOR / Piraeus CSI — <https://github.com/piraeusdatastore/linstor-csi>
- csi-s3 (optional S3-as-filesystem) — to confirm.
