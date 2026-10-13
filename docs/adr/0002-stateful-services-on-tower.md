# ADR-0002: Stateful services on tower, k8s for applications

- **Status:** Accepted
- **Date:** 2026-10-13
- **Deciders:** owner + agent

## Context

ADR-0001 set the direction "**all services run in Kubernetes, all persistent
data lives on `tower`**". Running *stateful* services that way turned out to be
the awkward part of this particular cluster:

- There is **no dynamic block CSI** for this tower (mdadm RAID1 + in-kernel LIO,
  no ZFS — see [`../storage/static-iscsi.md`](../storage/static-iscsi.md)), so
  every database needs a hand-made static PV.
- Every block volume travels **tower → iSCSI → node → pod**, a double network
  hop, even though the data is already on tower.
- LUN indices were **positional**, so retiring one volume shifted the others and
  broke their PVs.
- `/mnt/raid1` is a single fakeraid-turned-RAID1 array with **no snapshots**;
  where the data lives matters more than which orchestrator mounts it.

Meanwhile the network path is simple: tower `192.168.30.200` is reachable from
VLAN 120 through the router. Garage (S3) was already moved from a k8s pod on a
LUN to a **Docker container on tower** (see
[`../storage/garage.md`](../storage/garage.md)) with no loss of function.

## Decision

1. **`tower` hosts the shared stateful services**, run as Docker containers next
   to the disks, managed by the Ansible role `tower_storage`:
   - **Garage** (S3 object storage, `:3900`),
   - **Postgres** (`:5432`) — one instance, apps connect over the network.

2. **k8s runs applications** (stateless or per-pod), reached by their Ingress;
   they consume tower services over the network (S3 API, SQL).

3. **Block LUNs stay only where a k8s pod genuinely needs local block storage**:
   - **Vault** Raft data (fsync/locking semantics),
   - **OpenCloud** metadata (needs POSIX extended attributes; the NFS export
     can't provide them at the pinned NFSv4.1).

4. **LUN numbers are explicit** (`lun:` per volume), never positional, so a
   volume can be retired without renumbering the others.

## Consequences

- **Deviation from "everything in k8s":** stateful services are now split
  between k8s (Vault, OpenCloud) and tower (Garage, Postgres). k8s is for
  applications.
- **tower is a bigger SPOF** (already the storage host): losing it takes down
  media, S3 and the databases together. Acceptable for the PoC; the durability
  plan (Phase 5) must cover tower, and backups become more important.
- These services are **Ansible/Docker-managed, not Flux-managed** — two control
  planes for one stack. Documented per service under `docs/storage/`.
- Databases are reachable from VLAN 120 over the router on their ports
  (S3 `3900`, Postgres `5432`); the router rule (`router_openwrt`) is the
  network boundary.
- **Backups** (Phase 5) should prefer logical dumps (`pg_dump`, Garage bucket
  export) plus the array, and keep the Vault/OpenCloud block volumes in scope.

## Alternatives considered

- **Run Postgres in k8s on a static LUN** (the previous setup): rejected — the
  LUN/CSI/hop complexity buys nothing when the data is already on tower and the
  instance is a singleton anyway.
- **NFS for the database/object directories**: rejected — SQLite/Postgres
  locking and Garage/POSIX xattrs are unsafe or unsupported on this NFS export.
