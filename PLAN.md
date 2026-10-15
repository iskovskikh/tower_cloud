# PLAN — tower_cloud

Living execution plan. Legend: `[x]` done · `[~]` in progress · `[ ]` todo ·
`[!]` blocked · `[?]` needs decision. Update this file as work proceeds.

**Goal:** applications run in Kubernetes; stateful services (Garage, Postgres)
and all persistent data live on `tower` (ADR-0002); the whole stack is
Infrastructure as Code (see `docs/adr/0001-infrastructure-as-code.md`).

_Updated: 2026-10-15_

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

- [x] firewall/routing VLAN 120 ↔ `192.168.30.0/24` (ports 2049, 3260, 3900, 5432)
      — OpenWrt zone forwarding `vlan120`→`srv` + port rule (role `router_openwrt`)
- [x] NFS export (RWX) — `/mnt/raid1/k8s-nfs` to `10.10.120.0/24` + `192.168.30.0/24`
- [x] iSCSI LIO target (RWO block) — fileio LUN on `/mnt/raid1/k8s-iscsi`,
      configured via **configfs** (targetcli is AUR-only on Arch and the local
      AUR helpers are broken); boot unit `tower-iscsi`
- [ ] MinIO (S3) — deferred to Phase 3 (needs a secret source; `tower_minio_enabled=false`)
      → superseded by **Garage on tower** (Phase 4); the MinIO task was removed
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
- [x] `tower-s3` — **Garage v2.4.1**, now running **on tower** (Docker, host
      network, `data_dir=/mnt/raid1/garage`) — object storage needs no block
      device, so the old k8s Garage pod + 50 GiB block LUN (lun 2) were retired.
      `garage.toml` rendered by Ansible; secrets (`secret/garage`) from env.
      OpenCloud blobs go to it over `http://192.168.30.200:3900`. See
      `docs/storage/garage.md`.
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
- [x] media stack — **Transmission** (ns `media`, `transmission.k8s.lan`, LSIO
      4.1.3) and **Jellyfin 10.11.6** (ns `media`, `jellyfin.k8s.lan`) both in
      k8s, sharing tower's `/mnt/raid1/share/downloads` over NFS (RWX). Jellyfin
      `/config` on a **node-local dynamic PVC** (`tower-local`, local-path-
      provisioner — SQLite is unsafe on NFS);
      browser UI behind SSO, HTTP API + `/socket` left open for native clients.
      **No GPU on the nodes → software transcoding only** — GPU passthrough is a
      follow-up.
- [x] **databases** — **Postgres 18.6 now runs on tower** (`:5432`, Docker,
      `/mnt/raid1/postgres`) and serves the cluster's apps over the network; the
      k8s StatefulSet + LUN-backed PV were retired (ADR-0002). Keycloak connects
      to `192.168.30.200:5432`.
- [x] **SSO / identity — Keycloak 26.8.1** (ns `keycloak`): Postgres backend,
      issuer `auth.metoda.cc` (split-horizon; was `keycloak.k8s.lan`), realm `tower` (clients `vault` +
      `oauth2-proxy`). `oauth2-proxy` (ns `oauth2-proxy`, `auth.k8s.lan`) gives
      forward-auth SSO to any Ingress via the `auth-url`/`auth-signin`
      annotations (demo: `whoami.k8s.lan`). Vault UI logs in via Keycloak OIDC.
      Secrets generated in Vault (`secret/keycloak`) → ESO. See
      `docs/identity/README.md`.
- [x] **OpenCloud** — self-hosted file sync/share/collab (opencloud.eu, the Go
      fork of ownCloud Infinite Scale), ns `opencloud`, `opencloud.k8s.lan`.
      Manifests in `k8s/apps/opencloud/` (Deployment
      `opencloudeu/opencloud:7.2.4`, built-in IDM/IDP — **not** behind
      oauth2-proxy; Service :9200; Ingress with `tower-ca`; ExternalSecret from
      Vault `secret/opencloud`). Storage is **`decomposeds3`**: user **blobs go
      to Garage S3** (bucket `opencloud`); metadata + the embedded IDM stay on a
      tower iSCSI LUN (lun 5, 500 GiB **sparse** — floats) because POSIX+xattrs
      can't be served over this NFS. `/etc/opencloud` on a small `tower-nfs` PVC.
      Verified end-to-end (WebDAV upload → 1 object in Garage, round-trip OK);
      ingress `curl --cacert tower-ca` → 200. Keycloak federation is a follow-up.

## Phase 5 — reliability

- [ ] backups + snapshots (tower array, VMs, k8s volumes, etcd)
- [ ] disk replacement plan (~3.6 y power-on on both NDSs)
- [ ] monitoring/alerting (mdadm MAILADDR already set)

## Phase 6 — external access (public VPS + WireGuard)

Design: **ADR-0003**. The home WAN is CGNAT (no public IPv4) → a public VPS
(`adminvps`) is the relay; the **Keenetic Omni II** (OpenWrt) is the home WG
client and the server-segment router.

- [x] Confirm CGNAT (`100.64.48.0/10`) → port-forwarding impossible
- [x] `adminvps` (`78.17.99.16`) as WireGuard **server** (`10.200.0.1`, udp 51820);
      `arch` NOPASSWD sudo
- [x] Keenetic Omni II → OpenWrt **server router**: LAN `192.168.30.0/24`, VLAN30
      (proxmox) bridged in, VLAN120 (k8s) = `10.10.120.1`; `kmod-wireguard` +
      `wireguard-tools` installed
- [x] Main router switch port 3 `VLAN30 → LAN`; Keenetic WAN = DHCP `192.168.1.216`
- [x] WireGuard **client** on the Keenetic (`10.200.0.2`); home nets routed over
      the tunnel — verified `adminvps` reaches tower / proxmox / k8s nodes /
      ingress / API `:6443`
- [x] `tower` address pinned to `.200` (DHCP reservation on the Keenetic)
- [x] **Traefik** reverse proxy on `adminvps` (`:80`/`:443`) — role
      `adminvps_traefik` (Docker Compose, Traefik v3.7.14), 2026-10-15
- [x] **domain**: `metoda.cc` registered at Timeweb (2026-10-15)
- [x] **DNS**: zone managed **by hand** in the Timeweb hosting panel
      (`hosting.timeweb.ru/domains`) — the Timeweb Cloud API does not see it, so
      not IaC (ADR-0004). A `cloud`/`auth`/`*` (+ apex and `www`) → `78.17.99.16`
      added and verified 2026-10-15; old parking A/AAAA removed.
- [x] **TLS**: Let's Encrypt **HTTP-01** for `cloud.` / `auth.` (Traefik on
      `adminvps:80`; no wildcard certificate) — certs issued 2026-10-15
- [~] publish **OpenCloud** (`cloud.`) and **Keycloak** (`auth.`) k8s Ingresses
      with the public hostnames — added to the Ingress manifests 2026-10-15; not
      yet applied (cluster down)
- [~] **split-horizon DNS** for the Keycloak issuer — `KC_HOSTNAME`, the router
      dnsmasq entry (`auth.metoda.cc` → ingress-nginx node IPs) and the
      oauth2-proxy/Vault issuer URLs are codified 2026-10-15; not yet applied
- [ ] bring the cluster's **Keycloak / Vault** back up (currently not answering)
- [ ] **IaC**: Ansible role/playbook for `adminvps` (WG + Traefik) and for the
      Keenetic Omni II (OpenWrt server router) — both configured by hand 2026-10-14
- [ ] move the k8s node **DHCP reservations** from the main router to the Keenetic
- [ ] **harden `adminvps`** (public, internet-exposed relay)

## Done log

- 2026-10-15 — **Keycloak issuer hostname → `auth.metoda.cc` + split-horizon DNS.**
  `KC_HOSTNAME=https://auth.metoda.cc` (was `keycloak.k8s.lan`); the router role
  (`router_openwrt`) dnsmasq now answers `auth.metoda.cc` with the ingress-nginx
  node IPs so the issuer resolves the same inside (ingress) and outside
  (`adminvps`); `oauth2-proxy --oidc-issuer-url` and Vault's
  `vault_oidc_discovery_url` follow. Manifests/roles edited, **not applied**
  (cluster down). See `docs/identity/README.md`, `CHANGELOG.md`.
- 2026-10-15 — **Traefik front end on `adminvps` (ADR-0003/0004).** New Ansible
  role `adminvps_traefik` + `playbooks/adminvps.yml`: Docker + Compose v2,
  Traefik v3.7.14 (`network_mode: host`), ACME HTTP-01. Let's Encrypt certs
  issued for `cloud.metoda.cc` / `auth.metoda.cc`; `:80`→`:443` redirect and the
  HTTP-01 challenge both live. Routing: `cloud.` / `auth.` → home ingress-nginx
  (`https://10.10.120.101-104:443`, re-encrypt, skip-verify) over WireGuard. k8s
  Ingress manifests now carry the public hostnames (not yet applied — the
  cluster's VLAN120 nodes are DOWN, so backends return 502). Follow-up:
  `KC_HOSTNAME`/split-horizon for Keycloak. See `docs/network/CHANGELOG.md`.
- 2026-10-15 — **DNS + TLS decision for `metoda.cc` (ADR-0004).** Investigated
  codifying the zone: the official Timeweb Cloud Terraform provider exists, but
  the Timeweb Cloud API does **not** see the domain (`GET /api/v1/domains` →
  total 0; `404` for metoda.cc) — the zone lives in the **Timeweb hosting panel**
  (`hosting.timeweb.ru/domains`), for which no lego DNS provider exists. Decision:
  manage the few `A` records (`cloud`/`auth`/`*` → `78.17.99.16`) **by hand** and
  issue TLS via **HTTP-01** on `adminvps`. Records were added + verified
  2026-10-15 (apex and `www` repointed too); a `tofu/dns` module was written and
  then removed. See ADR-0004, inventory `domains:`.
- 2026-10-14 — **External-access plumbing: public VPS + WireGuard (ADR-0003).**
  Home WAN is CGNAT, so external access runs through `adminvps` (`78.17.99.16`):
  a WireGuard **server** (`10.200.0.1:51820`, only peer = the Keenetic). The
  spare **Keenetic Omni II** (reflashed to OpenWrt 23.05.3) became the
  **server-segment router** (`192.168.30.1/24`, WG **client** `10.200.0.2`); its
  port 3 to proxmox is a VLAN30+VLAN120 trunk (VLAN30 bridged into the LAN;
  VLAN120 = `10.10.120.1`, the k8s nodes' gateway). The main router's switch
  port 3 was moved `VLAN30 → LAN` so the Keenetic WAN can take `192.168.1.216`.
  `tower` pinned back to `.200` via a Keenetic DHCP reservation. Verified from
  `adminvps` over the tunnel: tower, proxmox, all k8s nodes, ingress, API
  `:6443`. Next: Traefik + domain + TLS on `adminvps` (Phase 6). See ADR-0003 and
  `docs/network/CHANGELOG.md`.
- 2026-10-13 — **Jellyfin /config → node-local PVC.** Added Rancher
  **local-path-provisioner v0.0.30** + StorageClass `tower-local` (Flux), and
  moved Jellyfin's `/config` (SQLite; unsafe on NFS) off the block LUN 4 onto a
  dynamic `tower-local` PVC. Trade-off: node-local, off the tower array, not
  backed up (`docs/storage/local-path.md`). Retired LUN 4; only Vault (1) and
  OpenCloud (5) LUNs remain.
- 2026-10-13 — **Postgres moved to tower; LUNs trimmed (ADR-0002).** Keycloak's
  database now runs as a Docker Postgres on tower (`data_dir=/mnt/raid1/postgres`,
  `:5432`), reached over the network; the k8s StatefulSet + `tower-iscsi-postgres`
  LUN were retired. `tower_iscsi_volumes` switched to **explicit `lun:`** numbers
  (no more positional indices), letting `vol1` (lun 0) and `garage` (lun 2) be
  dropped alongside `postgres` (lun 3) without touching Vault (1), Jellyfin (4)
  or OpenCloud (5). Router opens 5432. Docs: `docs/storage/postgres.md`,
  `docs/adr/0002-stateful-services-on-tower.md`.
- 2026-10-13 — **S3 moved off the block LUN: Garage now runs on tower.**
  Object storage needs no block device, so Garage was relocated from a k8s pod
  on a 50 GiB tower LUN (lun 2) to a Docker container on tower itself
  (`data_dir=/mnt/raid1/garage`, host network, `server --single-node`). This
  removes the fixed cap (thin on `/mnt/raid1`) and the double network hop.
  Ansible role `tower_storage` (`tasks/garage.yml` + `garage.toml.j2`); router
  rule ports `2049 3260 9000` → `2049 3260 3900`; removed
  `k8s/apps/storage/garage/`; OpenCloud endpoint →
  `http://192.168.30.200:3900` (bucket migrated with rclone, key re-imported so
  Vault was unchanged). Lun 2 kept as a placeholder to avoid renumbering the
  following LUNs. See `docs/storage/garage.md`.
- 2026-10-13 — **OpenCloud live** (ns `opencloud`, `https://opencloud.k8s.lan`,
  `opencloudeu/opencloud:7.2.4`) — self-hosted file sync/share/collab. Ships its
  own IDM/IDP and serves its own login, so it is **not** behind oauth2-proxy
  (`PROXY_TLS=false`; nginx terminates TLS). Admin password in Vault
  `secret/opencloud` → ESO (`IDM_ADMIN_PASSWORD`, read only on first init).
  **Storage gotcha:** the POSIX ("decomposed") storage driver needs **extended
  attributes**; tower's NFS export doesn't provide `user.*` xattrs (the kernel
  NFS client only does so via NFSv4.2, and vers=4.2 OOMs the 4 GiB nodes), so
  the first deploy crash-looped with `unfit storage ... extended attributes not
  supported`. Fixed by moving `/var/lib/opencloud` onto a **dedicated tower
  iSCSI LUN** (`opencloud`, lun 5, ext4; static PV `tower-iscsi-opencloud`).
  The LUN is **sparse** (role flag `sparse: true` → `truncate` not `fallocate`),
  so it reports 500 GiB but only takes array blocks on demand.
  **Storage switched to `decomposeds3`:** user blobs now go to **Garage S3**
  (new bucket `opencloud` + key, creds in Vault `secret/opencloud`), while
  metadata + embedded IDM remain on the block LUN. Verified end-to-end via a
  temporary basic-auth WebDAV PUT → 1 object appeared in Garage, round-trip OK.
  `/etc/opencloud` stays on a small `tower-nfs` PVC.
  LUN added to `ansible/roles/tower_storage`. Verified: pod Ready, Ingress
  serves 200 (root + OIDC discovery), cert valid under `tower-ca`. Keycloak
  federation is a follow-up. Manifests `k8s/apps/opencloud/`.
- 2026-10-13 — **Jellyfin moved from tower to k8s** (ns `media`,
  `https://jellyfin.k8s.lan`, `jellyfin/jellyfin:10.11.6`). `/config`
  (metadata + SQLite) migrated onto a dedicated tower iSCSI LUN (`jellyfin`,
  lun 4, 20 GiB, static PV `tower-iscsi-jellyfin`); `/cache` is `emptyDir`;
  media is the shared tower NFS export at `/media`. Browser UI (`/web`) behind
  **Keycloak SSO**; the HTTP API + `/socket` stay open for native clients
  (two Ingresses on one host). **No `/dev/dri` on the nodes → software
  transcoding only** (tower's Intel VAAPI HW transcode retired). Tower's Docker
  container + nginx Jellyfin site stopped. Manifests
  `k8s/apps/media/jellyfin/`; LUN added to `ansible/roles/tower_storage`.
- 2026-10-13 — **Transmission moved from tower to k8s** (ns `media`,
  `https://transmission.k8s.lan`, `ghcr.io/linuxserver/transmission:4.1.3`).
  Tower's media library `/mnt/raid1/share/downloads` is NFS-exported to the
  cluster (role `tower_storage`, `tower_nfs_media_export_dir`) and mounted RWX in
  the pod — same files Jellyfin reads. Config on a dynamic `tower-nfs` PVC,
  `settings.json` seeded from a ConfigMap. Whole host behind **Keycloak SSO**
  (oauth2-proxy); Transmission RPC auth disabled → single login, no Basic
  prompt. No torrent state to migrate (empty). Outbound-only peers for now.
  Manifests under `k8s/apps/media/transmission/`.
- 2026-10-13 — **SSO / identity live: Keycloak.** Postgres 18.6 (new 20 Gi tower
  LUN, static PV) backs **Keycloak 26.8.1** (ns `keycloak`, `keycloak.k8s.lan`).
  Bootstrap realm `tower` (clients `vault`, `oauth2-proxy`; test user) imported
  from a ConfigMap with secrets injected via `${VAR}` placeholders from Vault.
  **oauth2-proxy 7.15.5** (`auth.k8s.lan`) protects Ingresses via forward-auth
  (demo `whoami.k8s.lan`; verified full login → `X-Auth-Request-Email`). Vault UI
  now uses **OIDC** (`auth/oidc`, role `tower-user`). Secrets auto-generated in
  Vault (`secret/keycloak`) → ESO; nothing secret in git. Ansible role
  `vault_config` seeds secrets and (optionally) configures OIDC.
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
