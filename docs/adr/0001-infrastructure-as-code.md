# ADR-0001: Infrastructure as Code for tower_cloud

- **Status:** Accepted
- **Date:** 2026-10-13
- **Deciders:** owner + agent

## Context

`tower_cloud` began as read-only reconnaissance of the home network. The direction
is now: **all services run in Kubernetes, all persistent data lives on `tower`**,
and the whole stack is delivered as **Infrastructure as Code**.

Verified reality (2026-10-13):

- `tower` (`192.168.30.200`) — Arch; data array `/mnt/raid1` = native mdadm RAID1
  `md0` (converted from Intel IMSM fakeraid on 2026-10-13, internal bitmap).
- `proxmox` (`192.168.30.201`) — Proxmox VE; hosts the cluster VMs.
- k8s cluster `k8s-node-1..4` (`10.10.120.101-104`, VLAN 120, Ubuntu 22.04,
  2 vCPU / 4 GB / 22 GB each) — **kubeadm v1.28.3 + Calico, currently DOWN**:
  kubelet client certs expired 2025-08-05, control-plane static pods are not
  running. No ingress controller, no Helm, no StorageClass.

## Decisions

1. **Manage infrastructure as code.** The repo is no longer recon-only. Changes
   are made by editing code and applying it, not by hand on hosts.

2. **Provisioning = OpenTofu + Ansible.** `OpenTofu` (provider `bpg/proxmox`)
   declares proxmox VMs/disks; `Ansible` configures host state idempotently.
   Rationale: declarative VM lifecycle in TF, config management in Ansible.

3. **Kubernetes = rebuild with kubeadm, driven by Ansible**, CNI = **Calico**.
   Rationale: the existing cluster is dead (expired certs) and unused for ~1 year;
   a fresh reproducible build is cheaper than resurrecting an outdated v1.28.3.

4. **GitOps = Flux.** Flux reconciles `k8s/` from git. Rationale: lightweight,
   git-native, right-sized for a single home cluster.

5. **Secrets = HashiCorp Vault + External Secrets Operator (ESO).** Workloads
   consume `ExternalSecret` objects; values live in Vault. Rationale: dynamic DB
   credentials, rotation, audit, multi-workload — beyond Sealed Secrets.
   Vault storage backend (Raft vs S3-on-MinIO) and auto-unseal: **TBD**.

6. **Storage on tower = three access patterns → three StorageClasses:**
   - `tower-nfs` (RWX) — shared media library (Jellyfin, Transmission, *arr),
     via `nfs-server` + `csi-driver-nfs`.
   - `tower-iscsi` (RWO block) — databases, via LIO target + a CSI driver.
   - `tower-s3` — object storage, MinIO.
   See `docs/storage/plan.md`.

## Consequences

- Cluster access (kubeconfig) and stable node addressing are prerequisites.
- Vault becomes a critical service with unseal/backup duties (new SPOF) — must be
  backed up; auto-unseal approach must be decided.
- Host firewall/routing must allow VLAN 120 ↔ `192.168.30.0/24` for the storage
  ports (2049, 3260, 9000).

## Open questions

- Recreate the 4 VMs in OpenTofu, or `tofu import` the existing ones?
- Static IPs for k8s nodes (they use DHCP today) — move to static? ❓ gateway
  `10.10.120.1` is assumed.
- Vault storage backend (Raft vs S3-on-MinIO) and auto-unseal mechanism.
- Ingress controller choice (none exists today).
- Node sizing: 4 GB is tight for media transcoding.
