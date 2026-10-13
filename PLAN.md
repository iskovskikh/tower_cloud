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
- [x] git remote `origin` (GitHub — see Phase 3)

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
      `k8s_storage_client`, playbook `k8s-storage.yml`); static `iscsi` PV/PVC +
      busybox pod (`k8s/apps/storage/iscsi-test-*.yaml`) wrote and read back
      `hello-from-pod`. Latency: ~0.8 ms/4 KiB fsync; 256 MiB write ≈ 624 MB/s
      (cached) / ~14 MB/s durable (HDD + router path — a bottleneck to revisit)
- [!] **iSCSI multi-initiator hazard.** Pre-logging every node to the target left
      stale (pre-format) device views elsewhere; a reschedule made another node's
      kubelet reformat the LUN (wiped the first Vault init, 2026-10-13).
      Mitigation: the initiator role no longer pre-logs-in — the kubelet plugin
      manages login/logout per attach. Proper fix: a real CSI driver (Phase 1 dynamic).

## Phase 2 — cluster rebuild

- [x] `ansible` role `k8s_install`: containerd 2.4.1 + kubeadm **v1.37.1** +
      Calico v3.33 (operator, pod CIDR 10.244.0.0/16) — 4/4 nodes `Ready`,
      kubeconfig fetched to `archasus` (~/.kube/config)
- [x] role made idempotent — the destructive `kubeadm reset` is now opt-in via
      `-e k8s_reset=true` (`kubeadm init`/join were already guarded by their
      output files)
- [?] import VMs 101–104 into OpenTofu state, or recreate from a template
- [~] static IPs for k8s nodes — effectively static via router **DHCP
      reservations** (`dhcp.@host[4..7]`, MAC → 10.10.120.101-104); not yet
      codified in IaC (reservations were added by hand)

## Phase 3 — GitOps + secrets

- [x] Flux **v2.9.6** bootstrapped (`flux bootstrap git`, SSH, path `k8s/`,
      branch `master`); `flux-system` healthy, GitRepository + Kustomization
      reconciled. Repo now has remote `origin` (GitHub).
- [x] Git remote `origin = git@github.com:iskovskikh/tower_cloud.git` (Phase 0 item)
- [x] Vault storage backend decided: **Raft on tower iSCSI block** (LUN `vault`)
- [x] **Vault deployed via Flux** (`k8s/apps/secrets/vault/`, plain manifests —
      the Helm repo `helm.releases.hashicorp.com` is 403 from this network).
      Single node, Raft, tls off. Initialized + unsealed; keys/root token kept
      outside the repo (`~/.config/tower-cloud/vault-init.json`, 0600).
- [x] auto-unseal via a **sidecar** in the Vault StatefulSet reading the unseal
      keys from the out-of-band `vault-unseal` Secret (never committed).
      Verified: `vault operator seal` → auto-unsealed within ~25 s
- [x] **External Secrets Operator v2.12.0** via Flux (Helm, namespace
      `external-secrets`); Vault kubernetes-auth role `eso`; `ClusterSecretStore`
      `vault` validated (demo `ExternalSecret` verified, then removed — no secret
      values in the repo)
- [x] Vault config **codified** in Ansible role `vault_config`
      (`playbooks/vault.yml`): KV-v2 mount, kubernetes auth + config, ESO policy
      `eso-read`, role `eso`. Idempotent, runs the CLI in the pod via
      `kubectl exec`. Admin token from `$VAULT_TOKEN` or the off-repo init file.

## Phase 4 — workloads

- [x] StorageClass `tower-nfs` (RWX) via **csi-driver-nfs v4.13.5** (Flux);
      dynamic PVC validated (pod wrote/read `hello-nfs`; subdir created on tower).
      Pinned `vers=4.1` (4.2 OOMs the 4 GB nodes). Role `k8s_storage_client` also
      installs `nfs-common` on the nodes.
- [x] `tower-iscsi` (RWO block) as **static PVs** — reusable pattern +
      `scripts/new-iscsi-pv.sh`; StorageClass `tower-iscsi-static`. **No** dynamic
      block CSI fits this tower (democratic-csi needs ZFS+targetcli;
      csi-driver-iscsi is attach-only) — dynamic block deliberately deferred.
      See `docs/storage/static-iscsi.md`.
- [x] `tower-s3` — **Garage v2.4.1** (MinIO images are now gated — `unauthorized`):
      single node on a tower block LUN (lun 2); `garage.toml` rendered by ESO
      from Vault (`secret/garage` → rpc_secret/admin_token; S3 keys in
      `secret/garage-s3`). Validated with `aws-cli` (put/list in bucket
      `artifacts`).
- [x] ingress controller — **ingress-nginx v1.15.1** via Flux, run as a
      hostNetwork DaemonSet (no cloud LB): any node IP `:80` (`:443` free) is an
      entry point, reachable from the LAN through the router. Class `nginx`
      is the default. Validated with a whoami Ingress from `archasus`.
- [x] **TLS via cert-manager v1.21.2** (Flux) + internal **CA ClusterIssuer**
      (`tower-ca`, self-signed bootstrap). Works for LAN-only names (no public
      ACME). Vault UI exposed at `https://vault.local` (`vault-tls`). Import the
      `tower-ca` cert to trust it.
- [x] **DNS** — wildcard `*.k8s.lan` on the router (dnsmasq `--address`, role
      `router_openwrt`) → all 4 node IPs; ingress-nginx runs on every node
      (control-plane toleration). New Ingresses just use `<name>.k8s.lan`.
      Vault moved to `vault.k8s.lan`; `/etc/hosts` hand-fix removed.
- [ ] media stack (Jellyfin, Transmission) consumed from tower
- [ ] databases (Postgres) on RWO block

## Phase 5 — reliability

- [ ] backups + snapshots (tower array, VMs, k8s volumes, etcd)
- [ ] disk replacement plan (~3.6 y power-on on both NDSs)
- [ ] monitoring/alerting (mdadm MAILADDR already set)

## Done log

- 2026-10-13 — **DNS live (`*.k8s.lan`).** Router dnsmasq now serves a wildcard
  for cluster services: role `router_openwrt` writes
  `dhcp.@dnsmasq[0].address='/k8s.lan/<ip>'` for all four nodes (idempotent,
  `--address` matches subdomains). Added a control-plane toleration so
  ingress-nginx (hostNetwork DaemonSet) also runs on `k8s-node-1` — all four node
  IPs are entry points. Vault Ingress moved `vault.local` → **`vault.k8s.lan`**
  (cert re-issued); the manual `/etc/hosts` entry on `archasus` removed. Node IPs
  stay stable via router DHCP reservations (`dhcp.@host[4..7]`, not yet IaC).
- 2026-10-13 — **Fixed control-plane OOM (VM memory over-commit).** proxmox has
  16 GB but the 4 VMs were `4096/balloon=1024` ⇒ Proxmox ballooned guests to
  ~1.2 GB and oom-killed kube-apiserver/controller-manager/scheduler (API
  `TLS handshake timeout`). Applied variant B on proxmox: `101 -balloon 4096`,
  `102-104 -memory 3072 -balloon 3072` + VM reboots. Now node-1 4 GB fixed,
  workers 3 GB, host keeps ~2.5 GB; apiserver stable, 0 OOM. Inventory updated.
- 2026-10-13 — **TLS live (cert-manager).** Deployed **cert-manager v1.21.2** via
  Flux; internal CA chain (`selfsigned` → `Certificate tower-ca` → ClusterIssuer
  `tower-ca`). Vault UI now served at **`https://vault.local`** (Ingress +
  cert-manager annotation); verified with `curl --cacert tower-ca`. No public
  ACME (services are LAN-only). TODO: DNS (router or hosts).
- 2026-10-13 — **Object storage live: Garage (replaces MinIO).** MinIO container
  images are now gated (`unauthorized` in docker.io/quay.io), so deployed
  **Garage v2.4.1** instead: single node on a dedicated tower block LUN (lun 2),
  `garage.toml` rendered by **ESO** from Vault (`secret/garage` + `secret/garage-s3`),
  bucket `artifacts` + key `apps`. Validated with `aws-cli` (put/list). See
  `k8s/apps/storage/garage/`.
- 2026-10-13 — **Block storage: static RWO PVs (decision).** No dynamic block
  CSI fits tower (democratic-csi needs ZFS+targetcli; csi-driver-iscsi is
  attach-only). Formalized the static pattern: shared StorageClass
  `tower-iscsi-static`, helper `scripts/new-iscsi-pv.sh`, docs
  `docs/storage/static-iscsi.md`. Dynamic block deferred.
- 2026-10-13 — **Vault config codified (Phase 3 tail).** New Ansible role
  `vault_config` (`playbooks/vault.yml`) reconciles KV-v2 (`secret/`), the
  kubernetes auth method + config, policy `eso-read` and role `eso`, idempotently
  via `kubectl exec` into the Vault pod (no external API exposure). Verified
  idempotent (run twice: no changes). The demo `ExternalSecret`/Vault secret were
  removed so no secret values live in the repo.
- 2026-10-13 — **Ingress live.** Deployed **ingress-nginx v1.15.1** via Flux
  (Helm) as a `hostNetwork` DaemonSet (no MetalLB/cloud LB) — every node's `:80`
  (`:443`) is an entry point; the class `nginx` is default. Verified end to end:
  a whoami Ingress answered from `archasus` over the LAN→VLAN120 route. No TLS
  yet (cert-manager later).
- 2026-10-13 — **Dynamic RWX storage live (Phase 4 start).** Deployed
  **csi-driver-nfs v4.13.5** via Flux (Helm) + StorageClass **`tower-nfs`**
  (provisioner `nfs.csi.k8s.io`, server tower, share `/mnt/raid1/k8s-nfs`).
  `k8s_storage_client` (renamed from `k8s_iscsi_initiator`) now also installs
  `nfs-common`. Test PVC/pod verified end-to-end. Fixes along the way: the NFS
  export needed `nfs-common` on nodes; NFSv4.2 fails with an order-5 page
  allocation failure on the 4 GB nodes → pinned `vers=4.1`; the earlier
  zone-pair forwarding had silently allowed **all** ports vlan120→srv → replaced
  with port-scoped rules (only 2049/3260/9000).
- 2026-10-13 — **Vault auto-unseal + iSCSI incident.** Adding the auto-unseal
  sidecar rescheduled the Vault pod; `k8s-node-3` held a stale empty view of the
  LUN (from the initiator role's pre-login) and its kubelet reformatted the
  volume, wiping the first Vault init (demo data only). Recovered by re-init +
  reconfiguring Vault. Root cause fixed by removing pre-login from
  `k8s_storage_client` (kubelet manages login/logout). Auto-unseal sidecar using
  the `vault-unseal` Secret verified (seal → unsealed ~25 s). ESO store + demo
  ExternalSecret healthy again.
- 2026-10-13 — **Secrets chain live (Phase 3): Vault → ESO → Secret.**
  External Secrets Operator v2.12.0 deployed via Flux (Helm). Vault configured
  out-of-band: KV-v2 at `secret/`, kubernetes auth method, policy `eso-read`,
  role `eso` (SA `external-secrets/external-secrets`). `ClusterSecretStore`
  `vault` = Ready; demo `ExternalSecret` synced `secret/demo` → Secret
  `default/vault-demo` (verified values). Caveat: Vault auth/policy/role are set
  imperatively (not yet codified); demo ExternalSecret kept as a living example.
- 2026-10-13 — **Vault live (Phase 3).** Deployed via Flux as plain manifests
  (Helm repo blocked, 403): single-node Raft on a dedicated tower iSCSI LUN
  (`vault`, lun 1), static PV + `tower-iscsi-static` StorageClass. Initialized
  (5/3 Shamir) and unsealed; pods `1/1`. Keys/root token off-repo. Caveats:
  manual unseal on restart; cluster_addr defaults to https:8201 (single node).
  Next: ESO + Vault k8s auth.
- 2026-10-13 — **Flux GitOps live.** Pushed the repo to
  `github.com:iskovskikh/tower_cloud` (SSH) and bootstrapped Flux v2.9.6
  (`flux bootstrap git --branch=master --path=k8s`, SSH deploy secret).
  `flux-system` healthy; the root Kustomization reconciles `k8s/` — the static
  iSCSI smoke test is now GitOps-managed. Next: Vault+ESO (backend/auto-unseal TBD).
- 2026-10-13 — **Phase 1 validated end to end.** `open-iscsi` initiator on all 4
  nodes (role `k8s_storage_client`, separate playbook `k8s-storage.yml` — not in
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
