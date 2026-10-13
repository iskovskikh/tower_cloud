# Network change log / observations

One entry per recon session or infrastructure change. Newest first.

---

## 2026-10-13 — Vault config codified (Ansible), demo ExternalSecret removed

Executed from `archasus`.

- Added Ansible role **`vault_config`** + playbook **`playbooks/vault.yml`** that
  reconciles the Vault bootstrap config idempotently: KV-v2 mount `secret/`, the
  `kubernetes` auth method + its config, policy `eso-read` and role `eso`.
  It runs the `vault` CLI **inside the Vault pod via `kubectl exec`**, so the
  Vault API is not exposed outside the cluster; the admin token comes from
  `$VAULT_TOKEN` or the off-repo `~/.config/tower-cloud/vault-init.json`.
  Verified idempotent (two runs, no changes).
- Removed the demo `ExternalSecret` (`k8s/apps/secrets/external-secrets/`) and the
  backing `secret/demo` from Vault, so no secret *values* live in the repo. ESO +
  `ClusterSecretStore` remain ready for real workloads.

Sources: `ansible-playbook`, `kubectl`. No secret values recorded.

---

## 2026-10-13 — ingress-nginx (hostNetwork DaemonSet) via Flux

Executed from `archasus` (Flux).

- Deployed **ingress-nginx v1.15.1** (chart 4.15.1) via Flux (HelmRepository
  `kubernetes.github.io/ingress-nginx` + HelmRelease in `ingress-nginx`).
- Runs as a **hostNetwork DaemonSet** (no cloud LoadBalancer/MetalLB), so every
  node listens on `:80` (and `:443`); any node IP is an entry point. Class
  `nginx` is set as the default IngressClass.
- Controller runs on the 3 workers (`k8s-node-2/3/4`); the control-plane is
  tainted `NoSchedule` so it is intentionally skipped.
- Verified: a temporary whoami Deployment/Service/Ingress answered from
  `archasus` at `http://10.10.120.102/` with `Host: whoami.local`
  (`X-Forwarded-For: 10.10.120.1` — via the router). Test objects removed.
- Not yet: TLS / cert-manager, DNS names.

Sources: `kubectl`, `flux`. No credentials involved.

---

## 2026-10-13 — Dynamic RWX storage (csi-driver-nfs) + firewall fix

Executed from `archasus` (Flux).

- Deployed **csi-driver-nfs v4.13.5** via Flux (HelmRepository from the project's
  raw charts URL + HelmRelease in `kube-system`). Controller `5/5`, a
  `csi-nfs-node` DaemonSet on all 4 nodes.
- Added StorageClass **`tower-nfs`** (RWX, `nfs.csi.k8s.io`, server
  `192.168.30.200`, share `/mnt/raid1/k8s-nfs`). A test PVC + pod wrote/read
  `hello-nfs`; the driver created `pvc-<uid>/` on tower under the export. Test
  objects removed afterwards.
- Renamed role `k8s_iscsi_initiator` → **`k8s_storage_client`** and it now also
  installs **`nfs-common`** (needed for the node-side NFS mounts).
- **Fix 1 — NFS version:** default NFSv4.2 failed with `page allocation
  failure: order:5` on the 4 GB nodes (`mount.nfs: Cannot allocate memory`).
  Pinned `vers=4.1`, which mounts fine.
- **Fix 2 — firewall scope:** the OpenWrt zone-pair *forwarding* we added earlier
  actually allowed **all** ports vlan120→srv (111/20048 were reachable). Replaced
  it with port-scoped rules, so only **2049/3260/9000** pass; verified 111/20048
  are now closed and 2049/3260 still open.

Sources: `kubectl`, `flux`, `csi` logs, `mount`/`dmesg`, `uci` on the router.

---

## 2026-10-13 — Vault auto-unseal + iSCSI multi-initiator incident

- Added an **auto-unseal sidecar** to the Vault StatefulSet, reading unseal keys
  from the `vault-unseal` Secret (created out-of-band; **not** in git).
- **Incident:** adding the sidecar forced the Vault pod to reschedule from
  `k8s-node-2` to `k8s-node-3`. Because the `k8s_iscsi_initiator` role had
  pre-logged **every** node into the target, node-3 held a **stale (pre-format)
  view** of the LUN. When node-3's kubelet attached it, `blkid` returned nothing
  → it **reformatted** the volume, wiping the first Vault init (demo data only;
  the unseal keys/root token were lost with it).
- **Recovery:** `vault operator init` again (new 5/3 keys, saved off-repo),
  re-created the KV engine, kubernetes auth, policy `eso-read`, role `eso` and
  the `secret/demo` value; updated the `vault-unseal` Secret; restarted the pod.
- **Fix:** removed pre-login from `k8s_iscsi_initiator` — the kubelet in-tree
  iscsi plugin logs in on attach and out on detach, so only the node actually
  using a volume holds a session (no cross-node stale views). Verified: pod
  `2/2`, `Sealed=false`; `vault operator seal` → auto-unsealed in ~25 s;
  `ClusterSecretStore` Ready and `ExternalSecret` `SecretSynced` again.
- Standing risk documented in `PLAN.md` (multi-initiator); the real fix is a CSI
  driver for dynamic provisioning.

Sources: `kubectl`, `vault`, `iscsiadm`, `flux`. Keys off-repo.

---

## 2026-10-13 — External Secrets Operator + Vault k8s-auth (secrets chain live)

Executed from `archasus` (Flux + `kubectl`/`vault`).

- Deployed **External Secrets Operator v2.12.0** via Flux (HelmRepository
  `charts.external-secrets.io` + HelmRelease; namespace `external-secrets`).
- Granted the Vault server SA `system:auth-delegator` (ClusterRoleBinding
  `vault-auth-delegator`) so it can call TokenReview.
- Configured Vault out-of-band (root token): KV-v2 at `secret/`, enabled the
  **kubernetes** auth method, wrote policy `eso-read`
  (`secret/data/*`, `secret/metadata/*`) and role `eso` bound to SA
  `external-secrets` in namespace `external-secrets`; wrote test secret
  `secret/demo`.
- `ClusterSecretStore` `vault` → **Ready** ("store validated"); demo
  `ExternalSecret` `default/vault-demo` → **SecretSynced**; Secret created with
  keys `username`/`password` (values verified: `admin` / `please-rotate-me`).
- Caveat: the Vault auth/policy/role setup is imperative (one-off commands), not
  yet in Git; manual unseal still required after restarts.

Sources: `kubectl`, `flux`, `vault` inside `vault-0`. Root token/unseal keys
kept off-repo (`~/.config/tower-cloud/vault-init.json`).

---

## 2026-10-13 — Vault (Raft) deployed on tower iSCSI block via Flux

Executed from `archasus` (Flux GitOps).

- Added a second iSCSI backstore **`vault`** on tower (`/mnt/raid1/k8s-iscsi/vault.img`,
  10 GiB, **lun 1**); nodes rescanned and see it as `sdc` (role change:
  `k8s_iscsi_initiator` now runs `iscsiadm -m session --rescan`).
- Deployed **Vault v1.21.4** as plain manifests under `k8s/apps/secrets/vault/`
  (Flux-reconciled): namespace, static PV `tower-iscsi-vault` (lun 1) +
  `tower-iscsi-static` StorageClass (no-provisioner) + a `vault` StatefulSet
  (Raft, single node, `tls_disable`). **Not** Helm: `helm.releases.hashicorp.com`
  returns **403** from this network (both host and cluster), so the chart repo is
  unusable.
- Fixed a startup crash: the image entrypoint already appends `-config=/vault/config`,
  so passing `-config=…/vault.hcl` loaded the listener twice (`bind: address
  already in use`). The pod now just runs `server`.
- `vault operator init` (5 shares / 3 threshold) + unseal → **Initialized=true,
  Sealed=false, HA active**, pod `1/1`. Unseal keys + root token written to
  `~/.config/tower-cloud/vault-init.json` (0600) — **not** in the repo.
- Caveat: no auto-unseal → Vault is sealed after each restart (manual unseal).

Sources: `kubectl -n vault`, `flux`, `vault operator`. Secrets not recorded here.

---

## 2026-10-13 — Flux GitOps bootstrapped

Executed from `archasus`.

- Added git remote **`origin = git@github.com:iskovskikh/tower_cloud.git`** (SSH)
  and pushed the IaC repo (empty GitHub repo → 5 commits).
- **Flux v2.9.6** bootstrapped with `flux bootstrap git --branch=master
  --path=k8s --private-key-file=~/.ssh/id_ed25519`. Components installed in
  `flux-system` and healthy (source/kustomize/helm/notification controllers);
  `GitRepository` + root `Kustomization` reconciled to `master@sha1:5d22c320`.
- The root Kustomization reconciles the whole `k8s/` tree, so the static iSCSI
  smoke test (PV/PVC/Pod) is now **GitOps-managed** (previously applied by hand).
- Flux committed `k8s/flux-system/{gotk-components,gotk-sync,kustomization}.yaml`.

Sources: `flux bootstrap`, `flux get`, `kubectl -n flux-system`, `git`.
The in-cluster source secret holds an SSH key — **not** recorded here.

---

## 2026-10-13 — tower block storage validated from a pod (Phase 1)

Executed from `archasus` (Ansible + `kubectl`).

- Installed **open-iscsi** on `k8s-node-1..4` (role `k8s_iscsi_initiator`,
  playbook `playbooks/k8s-storage.yml`): `iscsid` enabled, discovered
  `192.168.30.200:3260`, logged in to `iqn.2026-10.lan.tower:k8s`. The 10 GiB LUN
  now shows as `sdb` on each node.
- Static smoke test (`k8s/apps/storage/iscsi-test-*.yaml`): in-tree `iscsi` PV
  (`targetPortal 192.168.30.200:3260`, lun 0, ext4) + PVC + busybox pod scheduled
  on `k8s-node-2`. `AttachVolume` succeeded; `cat /data/test.txt` →
  `hello-from-pod`. **Phase-1 exit criteria met** (pod reads/writes a block
  volume that lives on tower); in-tree iSCSI plugin is present in v1.37.1.
- Perf (through the router): 256 MiB write ≈ **624 MB/s cached**, ~**14 MB/s
  durable**; 4 KiB fsync ≈ 0.8 ms. The durable rate is a bottleneck to revisit
  (HDD fileio + routed iSCSI).
- Live test objects were deleted afterwards; the manifests remain in the repo.

Sources: Ansible run reports, `kubectl get/describe/exec`, `iscsiadm`, `lsblk`.
No credentials recorded.

---

## 2026-10-13 — tower storage PoC (NFS + iSCSI), router forwarding

Executed from `archasus` via Ansible (`ansible-playbook playbooks/site.yml`).

- **Router (`192.168.1.1`)** — the k8s VLAN (zone `vlan120`) could not reach the
  server segment (zone `srv`, `192.168.30.0/24`): both networks are directly
  connected but the firewall `REJECT`ed inter-zone forwarding (workers got ICMP
  `Destination Port Unreachable` from `10.10.120.1`). Added, via uci, an explicit
  forwarding `vlan120 → srv` plus an allow rule for TCP `2049 3260 9000`.
  Role `router_openwrt` (uses `raw` — OpenWrt has no Python). Idempotent.
- **tower (`192.168.30.200`)** — storage appliance role `tower_storage`:
  - **NFSv4**: `nfs-utils` installed; `/mnt/raid1/k8s-nfs` exported rw to
    `10.10.120.0/24` and `192.168.30.0/24`; `nfs-server` enabled.
  - **iSCSI (LIO)**: target `iqn.2026-10.lan.tower:k8s`, one 10 GiB `fileio`
    LUN backed by `/mnt/raid1/k8s-iscsi/vol1.img`, portal `0.0.0.0:3260`,
    dynamic ACLs. Configured **directly through configfs** — `targetcli` /
    `python-rtslib` are AUR-only on Arch and `yay`/`pikaur` are broken
    (`libalpm.so.13` / Python version mismatch). A boot unit `tower-iscsi.service`
    replays the bring-up script at every boot.
- **Verified** from `k8s-node-2`: `ping tower` OK, TCP `2049` and `3260` open
  through the router (`9000` closed — MinIO deferred).
- Roles `router_openwrt` and `tower_storage` are idempotent (second run: 0 changed).
- Not done: MinIO (no secret source yet → `tower_minio_enabled=false`);
  `open-iscsi` initiator on the nodes + a static PV/PVC smoke test.

Sources: live SSH read-only inspection (router `uci show firewall/network`,
`ip route`; tower kernel modules/configfs, `ss`, `exportfs`), then approved
Ansible applies. No credentials recorded.

---

## 2026-10-13 — k8s cluster rebuilt (kubeadm v1.37.1 + Calico)

- Ran `ansible-playbook playbooks/k8s.yml` (role `k8s_install`, three stages:
  prereq → control plane → workers) against `k8s-node-1..4`.
- Prereq: wiped old kubeadm state, upgraded `containerd.io` 1.6.24 → **2.4.1**,
  enabled CRI + systemd cgroup, replaced the **expired** k8s apt repo key
  (v1.28 → v1.37), installed and held `kubelet`/`kubeadm`/`kubectl` **v1.37.1**.
- Control plane: `kubeadm init` (endpoint `10.10.120.101:6443`, pod CIDR
  `10.244.0.0/16`, containerd cri-socket) + **Calico v3.33** (operator).
- Workers joined. Result: **4/4 nodes `Ready`**; Calico and CoreDNS running.
- `kubeconfig` copied to `archasus:~/.kube/config`; `kubectl` works from there.
- Fixes en route: duplicate CRI sockets (containerd + cri-dockerd) needed
  `--cri-socket`; a stale apiserver held `:6443` until leftover containers were
  removed; Calico CRD-registration race (retry loop added).
- The role is **not yet idempotent** — it resets the cluster on every run.

---

## 2026-10-13 — direction: Infrastructure as Code; k8s cluster discovered (down)

- **Adopted IaC.** Provision with **OpenTofu** (`bpg/proxmox`), configure hosts
  with **Ansible**, deliver workloads with **Flux** (GitOps), secrets with
  **HashiCorp Vault + External Secrets Operator**. Recorded in
  [`../adr/0001-infrastructure-as-code.md`](../adr/0001-infrastructure-as-code.md).
  Added repo layout `tofu/`, `ansible/`, `k8s/`; updated `AGENTS.md` (repo is no
  longer recon-only).
- **Inspected the k8s cluster** from `archasus` via the proxmox guest-agent
  (`qm guest exec`): `k8s-node-1..4` = Ubuntu 22.04.3, 2 vCPU / 4 GB / 22 GB,
  VLAN 120, `10.10.120.101-104`, kubeadm **v1.28.3** + **Calico** + containerd.
- **Cluster is DOWN** since ~2025-08: `kubelet` in a restart loop (3479 restarts)
  because the bootstrap client cert in `/etc/kubernetes/kubelet.conf` expired
  **2025-08-05**; `etcd`/`kube-apiserver` static pods not running; `:6443`
  connection refused. No ingress controller, no Helm, no StorageClass. Left as-is;
  to be rebuilt via kubeadm + Ansible.

Sources: `qm config`, `qm guest exec`/`get-osinfo` on proxmox; in-guest
`systemctl`, `journalctl -u kubelet`, `crictl ps -a`, `ls /etc/kubernetes`.

---

## 2026-10-13 — tower: Intel IMSM fakeraid → native mdadm RAID1

Executed from `archasus` over SSH (`tower` as `arch`, `sudo -n`).

- Old array: Intel IMSM (fakeraid) `1.3`, RAID1 `md126` over `sdb`+`sdc`
  (Toshiba N300 HDWG440 4 TB, ext4). Data at **sector offset 0**; IMSM
  metadata in the last ~1 MiB. Container `md127` (imsm).
- Converted **in place, without data loss**:
  stopped Docker `jellyfin` container + `transmission-daemon`/`minidlna`,
  unmounted `/mnt/raid1`, `mdadm --stop md126 md127`,
  `mdadm --zero-superblock` both disks, then recreated as
  `md0 --level=1 --metadata=1.0 --bitmap=internal --assume-clean`.
  **`v1.0` deliberately chosen** — its superblock lives at the *end* of the
  device, matching the offset-0 data layout; `v1.2` (superblock 4 KiB from the
  start) would have overwritten the ext4 superblock.
- Verified: `e2fsck` **clean** (22962 files, 638M/976M blocks); ext4 superblock
  byte-identical except the `s_wtime` timestamp; `mdadm --assemble --scan`
  re-assembles `md0`; `mount -a` mounts by UUID; all top-level dirs present.
- `/etc/mdadm.conf` rewritten: IMSM/container entries removed →
  `ARRAY /dev/md0 metadata=1.0 UUID=f09bfb3e:b5e8c4d7:0853fea4:3146658e`.
- `/etc/fstab`: `/dev/md/data` → `UUID=4d4ef0b1-fd11-4f60-9ab5-d9951dca4e26`.
- initramfs regenerated (`mkinitcpio -P`; the `mdadm_udev` hook embeds
  `/etc/mdadm.conf`).
- Rollback material (metadata regions of both disks + old configs):
  `/home/arch/raid-migrate-20261013-075052/`.
- Array healthy: SMART PASSED both disks, 0 reallocated/pending, ~2.4 TB used.
  Data intentionally kept (user may wipe later).
- `transmission-daemon` and `minidlna` **stopped and disabled** (`systemctl
  disable --now`) — no longer auto-start. `jellyfin` container left stopped.

Sources: live SSH inspection (`mdstat`, `mdadm --examine/--detail/--create`,
`smartctl`, `e2fsck`, `blkid`), plus a loop-device sandbox test of the
`v1.0 + internal bitmap` layout before touching the real disks.

---

## 2026-10-12 — audit cleanup & upgrades (tower + proxmox)

Executed from `archasus` over SSH (`tower` as `arch`; `proxmox` as `root`, then
`arch`). Read-only audit first, then approved changes.

**tower (`192.168.30.200`)**
- Full system upgrade (Arch was last updated 2026-03-11): kernel `6.19.6` →
  **`7.2.9-arch1-1`** (rebooted), 0 pending updates.
- **MinIO removed** — container, image, compose dir `/home/arch/docker/minio`,
  data `/mnt/raid1/minio`. Ports 9000/9001 closed.
- **NFS removed** — nfs-server/rpcbind stopped+disabled, `/etc/exports` cleared,
  bind mount (`/mnt/raid1/datasets → /srv/nfs/datasets`) removed from
  `/etc/fstab`; `nfs-utils` and `rpcbind` uninstalled.
- **SMB removed** — smb/nmb stopped+disabled, `samba` uninstalled; config backed
  up to `/etc/samba/smb.conf.bak-<ts>`.
- **`/mnt/raid1/datasets` deleted** (15 GB: `commoncrawl` 6.1 GB +
  `webdataset` 8.5 GB), as requested.
- **Transmission fixed** — the upgrade renamed the packaged unit
  `transmission.service` → `transmission-daemon.service` (old symlink dangled,
  daemon didn't start). Added drop-in `transmission-daemon.service.d/override.conf`
  (`User=arch`, `HOME=/home/arch`, `TRANSMISSION_WEB_HOME=/var/lib/flood-for-transmission`)
  and enabled the new unit.
- Stale failed `netctl@*` units disabled/cleared (0 failed units now).
- Config backups kept: `/etc/exports.bak-<ts>`, `/etc/fstab.bak-<ts>`,
  `/etc/samba/smb.conf.bak-<ts>`.
- ⚠️ `.pacnew` files await review (`ssh/sshd_config`, `sudoers`, `mkinitcpio.conf`,
  `pacman.conf`, `mdadm.conf`, `passwd`, `hosts`, `locale.gen`, …).
- ⚠️ Temporary `/etc/sudoers.d/arch-nopasswd` added to run this work (easy to remove).

**proxmox (`192.168.30.201`)**
- Destroyed VMs **110 `xminer`**, **203 `win10`**, **9000 `ubuntu-cloud`**; only
  `k8s-node-1..4` remain (all `onboot`, running).
- Added `pve-no-subscription` repo; **PVE 8.0.3 → 8.4.21**; default kernel
  installed; rebooted into **`6.8.12-43-pve`** (was `6.2.16-3-pve`).
- Configured user **`arch`** — added `archasus` SSH key + `NOPASSWD` sudo
  (`/etc/sudoers.d/arch`).
- Not addressed (from audit): PVE node SSL cert still expired (2025-08-17) →
  daily `pveupdate: SSL certificate failed`; no `vzdump` backups; NVMe
  media/data-integrity errors (20); SSH `PermitRootLogin yes` +
  `PasswordAuthentication yes`; PVE firewall has no rules.

Sources: live SSH inspection of both hosts (root/privileged), `pacman`, `apt`,
`systemctl`, `qm`, `smartctl`. No credentials are recorded here.

---

## 2026-10-12 — initial reconnaissance

Observed from `archasus` (`192.168.1.204`).

**Topology**
- Router `192.168.1.1` (and `192.168.30.1`) is **OpenWrt + LuCI on TP-Link
  hardware** (OUI `c0:4a:00` → TP-Link; cert O=`OpenWrt…`; SSH = dropbear).
  Handles DHCP/DNS for the LAN and **routes** the `192.168.30.0/24` server
  segment. Advertises IPv6 ULA `fde7:823d:5636::/64`.
- Two servers found on `192.168.30.0/24`:
  - `192.168.30.200` = **tower** (`tower.lan`) — NAS/media: nginx :80 → Jellyfin,
    Jellyfin 10.11.6 :8096, MinIO :9000/:9001, Transmission :9091, MiniDLNA :8200,
    SMB 139/445, NFS 2049 (export `/srv/nfs/datasets` → `192.168.1.0/24`).
  - `192.168.30.201` = **proxmox** — Proxmox VE, web :8006, SPICE proxy :3128.
- LAN clients seen: `archasus` `.204`, Dreame vacuum `.207`
  (`dreame_vacuum_p2114o.lan`), Pixel-10 `.214`.

**Anomalies / things to investigate**
- `archasus` holds **two** IPv4 leases on one NIC: `.204` **and** `.205`
  (two default routes, metrics 100 / 1002). Possible duplicate/stale DHCP lease.
- Server addresses `192.168.1.200–.203` (and `.161`) appear in shell history and
  `~/.ssh/known_hosts` but are **not reachable** — servers were moved to the
  `.30` subnet. Legacy entries retained in `inventory.yaml:legacy`.
- `biqu@192.168.1.161/.202/.203` logins hint at 3D-printer/SBC hosts
  (BIGTREETECH image) — currently offline, unconfirmed.
- WiFi `wlo1` cycles its MAC every ~7 min (NetworkManager scan randomization) —
  benign, `NO-CARRIER`.

**Sources used**
- Local: `ip addr/route`, `ip neigh`, `ss`, `journalctl -u NetworkManager`,
  `~/.ssh/known_hosts`, shell history, `/etc/fstab`, `nmcli`, `docker ps`.
- Network (read-only): ping sweeps of both /24s, TCP port scans, HTTP/HTTPS
  banners, `showmount`, `rpcinfo`.
- No authentication was used anywhere; no credentials were needed or stored.
