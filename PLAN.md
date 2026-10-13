# PLAN — tower_cloud

Living execution plan. Legend: `[x]` done · `[~]` in progress · `[ ]` todo ·
`[!]` blocked · `[?]` needs decision. Update this file as work proceeds.

**Goal:** all services run in Kubernetes; all persistent data lives on `tower`;
the whole stack is Infrastructure as Code (see `docs/adr/0001-infrastructure-as-code.md`).

_Updated: 2026-10-13_

## Phase 0 — foundations

- [x] tower array: Intel IMSM fakeraid → native mdadm RAID1 `md0` (2026-10-13)
- [x] Adopt IaC; scaffold `tofu/`, `ansible/`, `k8s/`; ADR-0001; update `AGENTS.md`
- [x] CLI tooling on `archasus` — **no passwordless sudo**, so user-local
      binaries in `~/.local/bin` (tofu 1.13.1, kubectl 1.37.1, helm 4.3.0,
      flux 2.9.6; ansible-core 2.21.5 in a venv)
- [x] SSH access `archasus → k8s nodes` (pubkey injected via `qm guest exec`;
      user `arch`, passwordless sudo)
- [ ] local git remote (repo is local-only today)

## Phase 1 — tower storage (СХД)

- [x] firewall/routing VLAN 120 ↔ `192.168.30.0/24` (ports 2049, 3260, 9000)
      — OpenWrt zone forwarding `vlan120`→`srv` + port rule (role `router_openwrt`)
- [x] NFS export (RWX) — `/mnt/raid1/k8s-nfs` to `10.10.120.0/24` + `192.168.30.0/24`
- [x] iSCSI LIO target (RWO block) — fileio LUN on `/mnt/raid1/k8s-iscsi`,
      configured via **configfs** (targetcli is AUR-only on Arch and the local
      AUR helpers are broken); boot unit `tower-iscsi`
- [ ] MinIO (S3) — deferred to Phase 3 (needs a secret source; `tower_minio_enabled=false`)
- [x] `ansible` role `tower_storage` implemented + idempotent
- [x] validated from a test pod — `open-iscsi` initiator on all nodes (role
      `k8s_iscsi_initiator`, playbook `k8s-storage.yml`); static `iscsi` PV/PVC +
      busybox pod (`k8s/apps/storage/iscsi-test-*.yaml`) wrote and read back
      `hello-from-pod`. Latency: ~0.8 ms/4 KiB fsync; 256 MiB write ≈ 624 MB/s
      (cached) / ~14 MB/s durable (HDD + router path — a bottleneck to revisit)

## Phase 2 — cluster rebuild

- [x] `ansible` role `k8s_install`: containerd 2.4.1 + kubeadm **v1.37.1** +
      Calico v3.33 (operator, pod CIDR 10.244.0.0/16) — 4/4 nodes `Ready`,
      kubeconfig fetched to `archasus` (~/.kube/config)
- [x] role made idempotent — the destructive `kubeadm reset` is now opt-in via
      `-e k8s_reset=true` (`kubeadm init`/join were already guarded by their
      output files)
- [?] import VMs 101–104 into OpenTofu state, or recreate from a template
- [?] static IPs for k8s nodes (currently DHCP)

## Phase 3 — GitOps + secrets

- [ ] Flux bootstrap (points at `k8s/`)
- [?] Vault storage backend (Raft vs S3-on-MinIO) + auto-unseal
- [ ] Vault + External Secrets Operator installed via Flux
- [ ] first `ExternalSecret` verified

## Phase 4 — workloads

- [ ] StorageClasses `tower-nfs` / `tower-iscsi` / `tower-s3`
- [ ] ingress controller
- [ ] media stack (Jellyfin, Transmission) consumed from tower
- [ ] databases (Postgres) on RWO block

## Phase 5 — reliability

- [ ] backups + snapshots (tower array, VMs, k8s volumes, etcd)
- [ ] disk replacement plan (~3.6 y power-on on both NDSs)
- [ ] monitoring/alerting (mdadm MAILADDR already set)

## Done log

- 2026-10-13 — **Phase 1 validated end to end.** `open-iscsi` initiator on all 4
  nodes (role `k8s_iscsi_initiator`, separate playbook `k8s-storage.yml` — not in
  `site.yml`, which still pulls the non-idempotent `k8s_install`). Static iSCSI
  PV/PVC + busybox pod on `k8s-node-2` wrote/read `hello-from-pod`. Perf: 256 MiB
  write 624 MB/s cached / ~14 MB/s durable via the router. Live test objects
  deleted; manifests kept under `k8s/apps/storage/`.
- 2026-10-13 — tower storage PoC (Phase 1): OpenWrt forwarding `vlan120`→`srv`
  on ports 2049/3260/9000; NFS export `/mnt/raid1/k8s-nfs`; LIO iSCSI target
  `iqn.2026-10.lan.tower:k8s` via **configfs** (10 GiB fileio LUN, boot unit
  `tower-iscsi`). Roles `router_openwrt` + `tower_storage` idempotent. Verified
  from `k8s-node-2`: 2049/3260 open. MinIO deferred (no secret source).
  Notes: `targetcli`/`python-rtslib` are AUR-only on Arch and `yay`/`pikaur` are
  broken (libalpm/Python mismatch) → configured LIO through configfs directly;
  the OpenWrt role uses `raw` (no Python on the router).

- 2026-10-13 — k8s cluster rebuilt: kubeadm **v1.37.1** + containerd 2.4.1 +
  Calico v3.33, 4/4 nodes Ready; kubeconfig on `archasus`. (Ansible role
  `k8s_install`; several fixes along the way: expired k8s apt key, duplicate
  CRI sockets, stale control-plane port, Calico CRD race.)
- 2026-10-13 — tower RAID converted IMSM → native mdadm (data preserved);
  `transmission-daemon`/`minidlna` disabled; docs updated.
- 2026-10-13 — IaC direction adopted; repo scaffolded; cluster discovered (down,
  expired kubelet certs since 2025-08).
