# Network change log / observations

One entry per recon session or infrastructure change. Newest first.

---

## 2026-10-15 — Cluster outage + recovery (VLAN120 DHCP, Vault LUN, server routing)

- **Symptom:** the whole cluster was unreachable — no `10.10.120.0/24` from
  `archasus` or `adminvps`, k8s nodes "down", `vault-0` CrashLoop. `tower` and
  `proxmox` were actually **up** (reachable from `adminvps` over WireGuard); the
  four k8s VMs were **running** on proxmox.
- **Root cause 1 — VLAN120 had no DHCP server.** The Keenetic (the real VLAN120
  gateway/`10.10.120.1`) had no DHCP pool for `eth0.120`; the nodes logged
  `eth0: DHCP lease lost` at ~01:38 and could not renew, so kubelet had no IP /
  default route. (The main router's old `dhcp.Lan120` pool lived on the dead
  trunk, which is why it stopped working.) **Fix:** added `dhcp.k8s`
  (10.10.120.100-.249) + static reservations for the four node MACs on the
  Keenetic → nodes back on `.101-.104`.
- **Root cause 2 — Vault iSCSI LUN went read-only.** During the ~13 h VLAN120
  outage the nodes lost their iSCSI session to tower (lun-1, the Vault LUN); the
  ext4 journal aborted → remounted RO → `vault-0` could not open the raft bolt
  file. **Fix:** scaled vault to 0, unmounted, `e2fsck -f -y /dev/sdb` (journal
  recovered, fs clean), recreated the pod → `vault-0` 2/2, unsealed. (Phase 5
  backups don't exist yet — this was recovered, not restored.)
- **Recovery:** restarted kube-proxy/calico/flux/cert-manager/ingress/vault to
  clear stale pod-network state after the interfaces returned. Result: 4/4 nodes
  Ready, all pods Running, Flux applied `master@5682e67`.
- **Home-LAN path to the server segment fixed (role `router_openwrt`).** The main
  router still had stale directly-connected `br-srv` (192.168.30.1) / `eth0.120`
  (10.10.120.1) on its dead trunk — it blackholed the server/cluster subnets for
  LAN clients and duplicated the Keenetic's 192.168.30.1. Now: those interfaces
  are `proto=none`, `rt_srv`/`rt_k8s` route `192.168.30.0/24` + `10.10.120.0/24`
  via the Keenetic (`192.168.1.216`), and a `hairpin_lan` rule allows LAN clients
  through. `archasus` can now reach `kubectl`, `tower` and `proxmox` directly.
- **Split-horizon DNS:** the main-router dnsmasq now also answers
  `/auth.metoda.cc/<node IP>` (role `router_openwrt`), so in-cluster clients
  resolve the OIDC issuer to ingress-nginx instead of hairpinning to
  `78.17.99.16`.
- **Keenetic:** `wan_to_lan` forwarding added (main LAN → server/cluster) and my
  SSH key added to `/etc/dropbear/authorized_keys`. Still hand-configured (no
  Ansible role — see `open_questions`).
- **Keenetic DNS rebind + WG (follow-ups):** dnsmasq's rebind protection was
  dropping the private-IP answers for `auth.metoda.cc`/`*.k8s.lan` forwarded from
  the main router (`possible DNS-rebind attack detected`) → added `rebind_domain`
  for `k8s.lan` and `metoda.cc`. The WireGuard tunnel to `adminvps` dropped when
  the main router's network was restarted; a `wg0` bounce on the Keenetic
  restored the handshake.

## 2026-10-15 — Keycloak issuer hostname `auth.metoda.cc` + split-horizon DNS

- **Why:** the OIDC issuer must be the same name inside and outside, or login on
  the public host fails (Keycloak advertises `KC_HOSTNAME` as `iss`; clients
  validate it and follow its endpoints). `keycloak.k8s.lan` is LAN-only, so it
  can't be the public issuer; `auth.metoda.cc` publicly points at `adminvps`.
- **Change:** `keycloak/deployment.yaml` `KC_HOSTNAME` → `https://auth.metoda.cc`.
  `oauth2-proxy --oidc-issuer-url` and Vault's `vault_oidc_discovery_url` move to
  `https://auth.metoda.cc/realms/tower`; the `keycloak_config` admin URL too.
- **Split-horizon:** `router_openwrt` now pushes dnsmasq `address=` entries
  `/auth.metoda.cc/10.10.120.101-104`, so in-cluster clients (oauth2-proxy,
  Vault) resolve the issuer straight to ingress-nginx instead of hairpinning via
  the public VPS. Outside, Timeweb answers `auth.metoda.cc` → `78.17.99.16`.
- **Ingress:** `keycloak/ingress.yaml` already carries both `keycloak.k8s.lan`
  and `auth.metoda.cc` (tower-ca cert); Keycloak redirects the old name to the
  canonical one.
- **Status:** codified in git, **not applied** (cluster down). Files:
  `k8s/apps/keycloak/deployment.yaml`, `k8s/apps/oauth2-proxy/deployment.yaml`,
  `ansible/roles/router_openwrt/defaults/main.yml`,
  `ansible/roles/vault_config/defaults/main.yml`,
  `ansible/roles/keycloak_config/defaults/main.yml`; docs updated.

## 2026-10-15 — Traefik front end on `adminvps` (Let's Encrypt HTTP-01, ADR-0003/0004)

- **New Ansible role `adminvps_traefik`** (`playbooks/adminvps.yml`, imported by
  `site.yml`; host `adminvps` added to `inventory/hosts.yml`). Installs Docker +
  the Compose v2 plugin (Ubuntu packages) and runs **Traefik v3.7.14** as a
  Compose service with `network_mode: host`. Config: `/etc/traefik/traefik.yml`
  (static), `dynamic.yml` (file provider), ACME store `acme/acme.json` (0600).
- **TLS (Let's Encrypt HTTP-01).** `:80` redirects to `:443` but still answers
  the `http-01` challenge. Certs obtained for **`cloud.metoda.cc`** and
  **`auth.metoda.cc`** (per-host, no wildcard); http→https `301` and the served
  cert (SNI) verified. hysteria keeps `udp/443`; Traefik takes `tcp/80`+`tcp/443`.
- **Routing:** both hostnames proxy to the home **ingress-nginx** over the
  WireGuard tunnel — `https://10.10.120.101-104:443`, re-encrypt with
  `insecureSkipVerify` (internal `tower-ca`), Host header preserved. Backend is
  a **502** today because the cluster is down (VLAN120 nodes unreachable).
- **k8s (GitOps manifests, not yet applied):** `opencloud/ingress.yaml` gained
  `cloud.metoda.cc`, `keycloak/ingress.yaml` gained `auth.metoda.cc`
  (`tls.hosts` + rules) alongside the internal `*.k8s.lan` names.
- **Pending:** bring the cluster up (nodes DOWN), then Flux applies the Ingresses;
  Keycloak needs `KC_HOSTNAME=https://auth.metoda.cc` + split-horizon DNS for OIDC.

## 2026-10-15 — DNS + TLS for `metoda.cc`: manual zone at Timeweb, HTTP-01 (ADR-0004)

- **Zone state (verified via `dig`):** `metoda.cc` is **already delegated** to
  Timeweb's nameservers — `ns1.timeweb.ru`, `ns2.timeweb.ru`, `ns3.timeweb.org`,
  `ns4.timeweb.org` (inventory previously said "not yet delegated" — corrected).
  At investigation time the apex `@` and `www` returned the registrar parking `A`
  `92.53.96.201` (since replaced), plus a default `TXT`
  `v=spf1 include:_spf.timeweb.ru ~all`. `cloud.` / `auth.` / wildcard did **not**
  exist yet.
- **Tried to codify the zone — not possible.** Authoring a `tofu/dns` module with
  the official Timeweb Cloud provider (`tf.timeweb.cloud/timeweb-cloud/timeweb-cloud`)
  failed at plan: the Cloud API does **not** know the domain —
  `GET /api/v1/domains` → `{"total":0}`, `GET /api/v1/domains/metoda.cc` → `404`.
  The zone is managed in the **hosting panel** (`hosting.timeweb.ru/domains`), and
  lego has **no provider** for the Timeweb hosting API (so DNS-01 can't be
  automated against it either). The `tofu/dns` module was removed.
- **Decision (ADR-0004):** manage the handful of `A` records **by hand** in the
  Timeweb hosting panel — `cloud` / `auth` / `*` → `78.17.99.16` (`adminvps`, the
  Traefik relay from ADR-0003). A documented exception to IaC (ADR-0001); keep
  `inventory.yaml` (`domains:`) in sync by hand. The apex and `www` were also
  repointed at `adminvps`, replacing the parking `A`.
- **TLS:** Let's Encrypt **HTTP-01** on `adminvps:80` (Traefik). No DNS API → no
  wildcard certificate; per-host certs for `cloud.` / `auth.`.
- **Status:** records **added by hand and verified** — `cloud`/`auth`/`*` + apex +
  `www` all resolve to `78.17.99.16` (`dig @1.1.1.1`, `@ns1.timeweb.ru`). The old
  registrar parking A (`92.53.96.201`) and AAAA (`2a03:6f00:1::5c35:60c9`) on
  apex/`www` were removed (propagating across Timeweb's NS). Docs updated:
  ADR-0004, `inventory.yaml` (`domains:`), `README.md`, `PLAN.md` Phase 6.

## 2026-10-15 — External domain `metoda.cc` registered at Timeweb

- **`metoda.cc` acquired** at **Timeweb** (`.cc`): registration `1 930 ₽`,
  renewal `2 030 ₽/yr`. Replaces the earlier `towercloud.dev` / `towercloud.ru`
  candidates for the external-access domain.
- ~~**Not yet delegated**~~ — **corrected 2026-10-15:** the zone was already
  delegated to Timeweb's nameservers on registration (see the entry above). Still
  to do: point `cloud.` / `auth.` at `adminvps` (`78.17.99.16`) and front it with
  the planned **Traefik + Let's Encrypt**, then wire the hosts.
- Recorded in `inventory.yaml` under a new `domains:` section; the related
  `open_questions` entry was updated.

## 2026-10-14 — External-access plumbing: Keenetic Omni II as OpenWrt server router + WireGuard to adminvps

Executed from `archasus`. The home WAN is **CGNAT** (main router `eth0.2` =
`100.64.48.134/20`, carrier-grade NAT), so inbound port-forwarding is impossible —
external access must go through a public relay. Chosen design: a public VPS
(`adminvps`) runs a **WireGuard server**, and the **Keenetic Omni II** (reflashed
to OpenWrt) runs the **WG client** and doubles as the **router for the server
segment**.

- **New host `adminvps` = `78.17.99.16`** (Ubuntu 24.04, 1 vCPU / 2 GB, user
  `arch` with NOPASSWD sudo). It was already a personal proxy box (x-ui/Xray
  3.2.6, hysteria udp:`443`, danted SOCKS tcp:`1080`); now also the WG server.
- **WireGuard tunnel.** Server `adminvps` `wg0` `10.200.0.1/24`, udp `51820`;
  client `Keenetic` `wg0` `10.200.0.2/32`. The server's `AllowedIPs` route the
  home nets (`192.168.30.0/24`, `10.10.120.0/24`) to the Keenetic peer. Verified
  from `adminvps`: tower `192.168.30.200:22`, proxmox `192.168.30.201:8006`,
  all k8s nodes `10.10.120.101-104`, ingress, and k8s API `:6443` (`403`).
- **`Keenetic Omni II` brought online, running OpenWrt 23.05.3**
  (`ramips/mt7620`, MACs `e4:18:6b:5a:e1:38/:39`). Role: **router of the server
  segment** (`br-lan` `192.168.30.1/24`), Wi-Fi off. `kmod-wireguard` +
  `wireguard-tools` installed — overlay is tiny (~1.7 MB free), so `tcpdump` /
  `iputils-arping` did not fit.
- **Server ports move onto the Keenetic.** Physical switch: `0` = WAN, `1-4` =
  LAN. `tower` → LAN port 2, `proxmox` → LAN port 3. Port 3 is a **trunk**
  (`VLAN30` + `VLAN120` tagged): `VLAN30` (`eth0.30`) is **bridged into
  `br-lan`** so proxmox's tagged management rejoins `192.168.30.0/24`; `VLAN120`
  (`eth0.120`) is a routed interface **`10.10.120.1/24`** — the k8s nodes' new
  gateway (keeps k8s in its own L2, as intended).
- **Main router port 3 moved `VLAN30 → LAN`.** To give the Keenetic's WAN a
  subnet that does not collide with the server network, the main router's switch
  port 3 (`@switch_vlan` vid 30 → vid 1) is now an untagged **LAN** access port;
  the Keenetic WAN takes DHCP `192.168.1.216`. The main router's `br-srv`
  (`192.168.30.1`, VLAN30) is now **orphaned** (no physical ports).
- **`tower` keeps `192.168.30.200`.** It briefly took DHCP `.139` off the
  Keenetic, so a **DHCP reservation** (MAC `00:01:29:6a:b5:d6` → `.200`) was
  added on the Keenetic to restore the address the rest of the IaC hardcodes
  (k8s manifests, Ansible inventory, scripts). `proxmox` stays static
  `192.168.30.201` (VLAN30 tagged `vmbr0.30`).
- **Keenetic firewall:** rule `Allow-Mgmt-WAN` (tcp `22 80 443` in from
  `192.168.0.0/16`) added so it stays manageable on its WAN side; `wg0` and the
  k8s interface are in the `lan` zone.
- **Pending:** Traefik + public domain + Let's Encrypt on `adminvps` (the
  reverse-proxy front for `cloud.`/`auth.`), then update the k8s Ingress hosts
  and the router's split DNS.

## 2026-10-13 — Jellyfin /config on a node-local PVC (`tower-local`)

Executed from `archasus` (Ansible + Flux/kubectl).

- **New StorageClass `tower-local`.** Added Rancher
  **local-path-provisioner v0.0.30** via Flux (`k8s/apps/storage/local-path/`):
  dynamic RWO volumes on the node's local disk (`/opt/local-path-provisioner`),
  `WaitForFirstConsumer`, `Retain`.
- **Jellyfin `/config` moved off the block LUN.** Its SQLite DBs are unsafe on
  NFS, so the dedicated iSCSI LUN (lun 4) was replaced with a dynamic
  `tower-local` PVC (5 Gi; used ~160 MiB). Removed the static PV
  `tower-iscsi-jellyfin`; `jellyfin-config` is now a plain dynamic PVC.
- **Trade-off (accepted):** `/config` is now **node-local** — tied to the node
  Jellyfin runs on, off the `/mnt/raid1` array, not covered by tower backups.
  See `docs/storage/local-path.md`.
- **Retired LUN 4** (`jellyfin`, 20 Gi) on tower; backing file pruned. Only
  **vault (lun 1)** and **opencloud (lun 5)** LUNs remain.
- **Data migrated** by tarring `/config` out of the running pod and restoring it
  into the new PVC.

## 2026-10-13 — Postgres moved off k8s LUN onto tower; LUN indices made explicit

Executed from `archasus` (Ansible + Flux/kubectl).

- **Decision (ADR-0002).** Stateful services live on **tower**; k8s runs
  applications. Block LUNs are limited to what a pod genuinely needs as local
  block — **Vault** (Raft) and **OpenCloud** metadata (POSIX xattrs). A single
  **Postgres** container on tower now serves the cluster's apps over the network.
- **Ansible — `tower_storage` role.** New `tasks/postgres.yml` (Docker,
  `postgres:18.6`, `data_dir=/mnt/raid1/postgres`, `:5432`, password from env,
  matching Vault `secret/keycloak` → `db_password`).
- **Explicit LUN numbers.** `tower_iscsi_volumes` now carries an explicit `lun:`
  per volume instead of relying on list position, so retiring a volume no longer
  renumbers the others. Bring-up script template updated accordingly.
- **Retired LUNs:** `vol1` (lun 0, PoC), `garage` (lun 2, S3 → tower), and
  `postgres` (lun 3, → tower). Vault stays on lun 1, Jellyfin on lun 4, OpenCloud
  on lun 5 — indices unchanged, their PVs unaffected. Backing files pruned on
  tower.
- **Router.** Storage ports now `2049 3260 3900 5432` (`vlan120` → `srv`).
- **k8s.** Removed `k8s/apps/db/postgres/` (StatefulSet + LUN-backed PV);
  Keycloak `KC_DB_URL` → `jdbc:postgresql://192.168.30.200:5432/keycloak`.
  Realm/users migrated with `pg_dump`/`pg_restore`.
- **Docs.** New `docs/storage/postgres.md`, `docs/adr/0002-…`; updated
  `static-iscsi.md` (explicit LUN + retire procedure) and `plan.md`.

## 2026-10-13 — S3: Garage moved off the k8s block LUN onto tower

Executed from `archasus` (Ansible + Flux/kubectl).

- **Rationale.** Object storage does not need a block device. **Garage** now runs
  **on tower** next to the disks (`data_dir=/mnt/raid1/garage`), so there is no
  iSCSI LUN / PV / PVC and no double network hop — and the fixed **50 GiB** cap
  is gone (space is thin on `/mnt/raid1`).
- **Ansible — `tower_storage` role.** New `tasks/garage.yml` +
  `templates/garage.toml.j2`: Docker container `garage` (
  `dxflrs/garage:v2.4.1`, host network, `server --single-node`, data on
  `/mnt/raid1/garage`). `rpc_secret`/`admin_token` come from the environment at
  play time (`GARAGE_RPC_SECRET`/`GARAGE_ADMIN_TOKEN`), matching Vault
  `secret/garage` — never committed. The disabled MinIO task was removed
  (`tasks/minio.yml`, `tower_minio_*`).
- **Router.** `router_openwrt` storage rule ports `2049 3260 9000` →
  `2049 3260 3900` (MinIO's 9000 dropped; Garage S3 is 3900).
- **k8s.** Deleted `k8s/apps/storage/garage/` (namespace, deployment, service,
  externalsecret, pv-pvc); Flux prunes them. OpenCloud
  `STORAGE_USERS_DECOMPOSEDS3_ENDPOINT` → `http://192.168.30.200:3900`.
- **Retired LUN.** `garage` (lun 2, 50 GiB) is kept in `tower_iscsi_volumes` as a
  placeholder: the LUN index is the list position, so removing it would renumber
  `postgres`/`jellyfin`/`opencloud` and break their PVs. Reclaiming it is a
  follow-up (requires renumbering).
- **Migration.** The `opencloud` bucket was copied with rclone and the existing
  key re-imported, so Vault `secret/opencloud` was unchanged. Runbook:
  `docs/storage/garage.md`.

## 2026-10-13 — OpenCloud (file sync/share) deployed to k8s

Executed from `archasus` (Ansible + Flux/kubectl).

- **New app.** Added `k8s/apps/opencloud/` — **OpenCloud** (opencloud.eu, the Go
  fork of ownCloud Infinite Scale) in ns `opencloud`, `https://opencloud.k8s.lan`.
  `Deployment` `opencloudeu/opencloud:7.2.4` (an `initContainer` runs
  `opencloud init || true`, then `opencloud server`; uid/gid 1000, `Recreate`),
  `Service` :9200, `Ingress` (issuer `tower-ca`; nginx annotations for large
  bodies, no buffering and long timeouts — needed by Tus uploads and SSE), and an
  `ExternalSecret` (Vault `secret/opencloud` → `IDM_ADMIN_PASSWORD`, only read on
  first init; plus the Garage S3 access/secret keys). Admin password seeded with
  `vault kv put secret/opencloud admin_password=…`.
- **Storage gotcha — NFS can't host the data.** First deploy crash-looped:
  ```
  tree: unfit storage '/var/lib/opencloud/storage/users':
  extended attributes not supported: xattr.Set ... user.posixfs.test: operation not supported
  ```
  OpenCloud's POSIX ("decomposed") storage driver **requires extended
  attributes**. Tower's NFS export doesn't provide `user.*` xattrs — the Linux
  NFS client only supports them via **NFSv4.2**, and vers=4.2 OOMs the 4 GiB
  nodes (see the `tower-nfs` StorageClass, pinned to 4.1). So the data volume was
  moved off NFS onto a **dedicated tower iSCSI LUN**: backstore `opencloud`
  (**lun 5**, 500 GiB, ext4 which supports xattrs), static PV
  `tower-iscsi-opencloud` (RWO). Append to `tower_iscsi_volumes` in
  `ansible/roles/tower_storage` and run the tower play. `/etc/opencloud` (plain
  config YAML, no xattrs needed) stays on a small dynamic `tower-nfs` PVC.
  This is the same block pattern as Jellyfin/Vault/Postgres.
- **Floating (thin) space.** The LUN is **sparse** — added a `sparse: true` flag
  to `tower_iscsi_volumes`, which makes `tower-iscsi-up.sh` create the backing
  file with `truncate -s` instead of `fallocate -l`. So it reports 500 GiB but
  only allocates array blocks as data is written. Verified: file apparent 500 G,
  on-disk ~1 GiB after init, `/mnt/raid1` back to ~990 G free. Caveat: thin
  provisioning — if the array fills, guest writes fail (ENOSPC); monitor free.
- **Storage switched to `decomposeds3` (blobs in Garage).** OpenCloud's
  decomposeds3 driver keeps config/metadata on POSIX but stores file **blobs in
  S3-compatible storage**. Created Garage bucket `opencloud` + key
  (`garage bucket create opencloud`, `garage key create opencloud`,
  `garage bucket allow --read --write --owner opencloud --key opencloud`);
  access/secret stored in Vault `secret/opencloud`. Deployment env:
  `STORAGE_USERS_DRIVER=decomposeds3`, `STORAGE_SYSTEM_DRIVER=decomposed`,
  `STORAGE_USERS_DECOMPOSEDS3_{ENDPOINT,REGION,BUCKET}` and
  `..._ACCESS_KEY/SECRET_KEY` from ESO. So the block LUN now holds only metadata
  + the embedded IDM (grows with file *count*, not size) — still can't be NFS
  (xattr). Note: Garage itself is a tower LUN, so capacity ultimately still
  lands on the array.
- **Not behind oauth2-proxy.** OpenCloud ships its own IDM/IDP and serves its own
  login (OIDC issuer derived from `OC_URL`), so forward-auth would force a second
  login; instead `PROXY_TLS=false` and nginx terminates TLS. Keycloak federation
  (external IdP / shared LDAP) is a follow-up.
- **Verified:** pod `1/1 Ready` (0 restarts), Ingress serves **HTTP 200** on `/`
  and `/.well-known/openid-configuration`, cert valid under `tower-ca`
  (`curl --cacert`). Initial admin user is `admin`; password in Vault.
  End-to-end S3 test: with basic-auth temporarily enabled, a WebDAV PUT created
  region `opencloud` object `Objects: 1` in Garage and round-tripped the content
  — confirming blobs really land in S3.

Sources: `k8s/apps/opencloud/`, `ansible/roles/tower_storage`.

---

## 2026-10-13 — Jellyfin migrated from tower to k8s (media)

Executed from `archasus` (Ansible + Flux).

- **Moved service.** Jellyfin 10.11.6 now runs in k8s (ns `media`,
  `https://jellyfin.k8s.lan`) instead of the tower Docker container
  (`jellyfin/jellyfin:10.11.6`, stopped 2026-10-13; tower's nginx
  `jellyfin.conf` site disabled). Same image version as tower.
- **Data stays on tower.** The library export `/mnt/raid1/share/downloads` is
  mounted RWX at `/media` (new PV `tower-nfs-media-jellyfin` over the same
  export Transmission uses). Jellyfin's `/config` (metadata + SQLite DBs, 164 MB
  on tower) is migrated onto a **dedicated tower iSCSI LUN** (`jellyfin`, lun 4,
  20 GiB; PV `tower-iscsi-jellyfin`) — SQLite needs real locking, so block not
  NFS. `/cache` (transcodes) is an `emptyDir` and regenerates.
- **Config migration.** Seed pod mounts the `jellyfin-config` PVC; the existing
  `/home/arch/docker/jellifin/config` tree is streamed in with
  `ssh tower 'tar -C … -cf - .' | kubectl exec -i … -- tar -xpf -` (numeric
  owners preserved, then `chown -R 1000:1000`). No library rescan needed.
- **Access.** Browser UI (`/web`) behind **Keycloak SSO** (oauth2-proxy
  forward-auth), TLS `tower-ca`. Every other path — the Jellyfin HTTP API
  (`/System`, `/Users`, `/Items`, `/Videos`, …) and the `/socket` websocket — is
  deliberately **left open** (two Ingresses on one host) so native clients
  (Android TV, iOS/tvOS, Kodi) keep using Jellyfin's own auth. Caveat: the
  browser then also logs into Jellyfin itself (double login).
- **Transcoding.** The k8s nodes have **no `/dev/dri`** (Proxmox VMs, no GPU
  passthrough), so the Intel VAAPI hardware transcoding tower used is gone —
  **software transcoding only** (direct play unaffected). GPU passthrough is a
  follow-up.

Sources: `k8s/apps/media/jellyfin/`, `ansible/roles/tower_storage`.

---

## 2026-10-13 — Fix: 502 on oauth2-proxy OIDC callback (nginx header buffer)

- **Symptom.** Logging into an SSO-protected app failed with **502 Bad Gateway**
  on `https://auth.k8s.lan/oauth2/callback` (browser showed a 502 after the
  Keycloak login). oauth2-proxy logged `[AuthSuccess]` and returned 302, so the
  login itself worked.
- **Root cause.** ingress-nginx error log:
  `upstream sent too big header while reading response header from upstream`
  — oauth2-proxy's `Set-Cookie` (session + id_token) does not fit the default
  4 KiB response-header buffer.
- **Fix.** Raised the controller buffers in the ingress-nginx HelmRelease
  (`controller.config`): `proxy-buffer-size: 16k`, `proxy-buffers-number: 8`,
  `proxy-busy-buffers-size: 32k`. Verified by replaying the full SSO flow
  (transmission → auth start → Keycloak login → callback → app): no 502.

---

## 2026-10-13 — Keycloak: realm-as-code user management + user `aiskovskikh`

- Added Ansible role **`keycloak_config`** (`playbooks/keycloak.yml`):
  reconciles realm `tower` users idempotently via the Keycloak **Admin REST
  API** (runs on the control host; stdlib-only Python). Declared users live in
  the role defaults; passwords live in Vault (`secret/keycloak`) → ESO
  (`keycloak-secrets`). A password is only set when the user is **created**
  (`temporary: true`, forced change on first login) — never reset afterwards.
- Created user **`aiskovskikh`** (realm `tower`): role `user`, group `/users`.
  Password generated in Vault key `aiskovskikh_password`; also added to the
  bootstrap realm ConfigMap for fresh installs.
- (Realm clients are still console-managed; only users are codified so far.)

---

## 2026-10-13 — Transmission migrated from tower to k8s (media)

Executed from `archasus` (Ansible + Flux). Applied 2026-10-13.

- **Moved service.** Transmission 4.1.3 now runs in k8s (ns `media`,
  `https://transmission.k8s.lan`) instead of the tower system unit
  (`transmission-daemon`, stopped+disabled since the RAID migration). No torrent
  state to migrate: tower's `torrents/` and `resume/` were empty.
- **Data stays on tower.** The existing library `/mnt/raid1/share/downloads`
  (read by Jellyfin as `/media`) is now also **NFS-exported** to the cluster
  (`tower_storage`: `tower_nfs_media_export_dir`), mounted RWX in the pod at
  `/downloads`. Jellyfin and Transmission therefore see the same files.
  Config/state (`/config`) is a small dynamic `tower-nfs` PVC.
- **Image.** `ghcr.io/linuxserver/transmission:4.1.3` (matches tower's version;
  the LSIO init only chowns `/downloads` non-recursively). Runs as
  `PUID=1000`/`PGID=1006` (arch:sambashare) so new files stay group-writable.
  `settings.json` is seeded from a ConfigMap on first start (download straight
  into `/downloads`, no `complete`/`incomplete` subdirs, no watch folder).
- **Access.** Whole host behind **Keycloak SSO** (oauth2-proxy forward-auth) at
  `transmission.k8s.lan`, TLS via `tower-ca`. Transmission's own RPC auth is
  **disabled** (`rpc-authentication-required: false`, no USER/PASS), so the
  browser gets a **single** login (Keycloak) and no Basic prompt; every path
  (incl. `/transmission/rpc`) sits behind SSO. Outbound-only for now (no inbound
  peer port 51413 forward). Verified: no cookie → 302 to Keycloak; backend
  `/transmission/web/` → 200 (no `www-authenticate`).
  _Earlier iteration kept Transmission basic auth for API clients (with SSO
  exempted on `/transmission/rpc`); replaced — it forced a double login._

Sources: `k8s/apps/media/transmission/`, `ansible/roles/tower_storage`.

---

## 2026-10-13 — SSO / identity: Keycloak + Postgres + oauth2-proxy

Executed from `archasus` (Ansible + Flux + ESO + Vault).

- **Decision & constraints.** Wanted SSO for all future services. Helm chart
  repos for Keycloak are blocked from this network (`codecentric/keycloakx` →
  404, Bitnami → 403), so — like Vault — deployed as **plain manifests via Flux**
  (Flux applies all YAML under `k8s/` recursively). Images reachable from the
  cluster: `quay.io/keycloak/keycloak:26.8.1`, `docker.io/library/postgres:18.6`,
  `quay.io/oauth2-proxy/oauth2-proxy:v7.15.5`, `traefik/whoami:v1.12.0`.
- **Storage.** New tower iSCSI LUN `postgres` (lun 3, 20 GiB) via
  `tower_iscsi_volumes`; static PV `tower-iscsi-postgres` + PVC `postgres-data`
  (ns `keycloak`, StorageClass `tower-iscsi-static`).
- **Secrets.** Ansible role `vault_config` now generates `secret/keycloak`
  (`admin_username`, `admin_password`, `db_password`,
  `oauth2_proxy_client_secret`, `oauth2_proxy_cookie_secret`,
  `vault_client_secret`, `demo_user`, `demo_user_password`) idempotently
  (`vault kv patch` — existing values never rotated). ESO renders them into
  Secrets `keycloak/keycloak-secrets` and `oauth2-proxy/oauth2-proxy-secrets`.
- **Keycloak 26.8.1** (ns `keycloak`): production `start`, DB=Postgres, hostname
  `https://keycloak.k8s.lan`, proxy-headers, management port 9000 for health.
  Realm **`tower`** bootstrapped from a ConfigMap (`--import-realm`) with clients
  **`vault`** and **`oauth2-proxy`** (each with an audience mapper) and a test
  user. Client secrets/passwords are injected via Keycloak's `${VAR}`
  placeholder substitution from the container env — **no secrets in git**.
  (Note: syntax is `${VAR}`, *not* `${env.VAR}`; and `--import-realm` only
  imports realms that don't exist yet.)
- **oauth2-proxy 7.15.5** (ns `oauth2-proxy`, `auth.k8s.lan`): forward-auth for
  ingress-nginx. Protect any Ingress with `auth-url` →
  `http://oauth2-proxy.oauth2-proxy.svc.cluster.local:4180/oauth2/auth` and
  `auth-signin` → `https://auth.k8s.lan/oauth2/start?rd=...`; needs
  `--cookie-domain=.k8s.lan` so the session cookie is sent to all protected
  hosts. Demo Ingress `whoami.k8s.lan` verified: unauth → Keycloak login →
  back-end returns `X-Auth-Request-Email: <user>@k8s.lan`.
- **Vault OIDC.** `auth/oidc` mounted with `oidc_discovery_url =
  https://keycloak.k8s.lan/realms/tower`, `oidc_client_id=vault`,
  `oidc_discovery_ca_pem` = `tower-ca`, role `tower-user`
  (`bound_audiences=vault`, `user_claim=preferred_username`). Enabled via
  `ansible-playbook playbooks/vault.yml -e vault_oidc_enable=true`.

Sources: `kubectl`, `flux`, `vault`, `curl`, `ansible`. No secret values here.

---

## 2026-10-13 — DNS: wildcard `*.k8s.lan` + ingress on all nodes

Executed from `archasus` (Ansible + Flux).

- **Router DNS (codified).** New task in role `router_openwrt` writes a dnsmasq
  wildcard via uci — `dhcp.@dnsmasq[0].address='/k8s.lan/<node-ip>'` for all four
  nodes (dnsmasq `--address=/k8s.lan/IP`: matches the domain and every subdomain,
  never forwards it). Idempotent; reloads dnsmasq only on change. Any Ingress host
  `<name>.k8s.lan` now resolves from the whole LAN without per-service DNS edits;
  dnsmasq returns all four A records → simple round-robin/failover.
- **ingress-nginx on every node.** Added a `control-plane` `NoSchedule` toleration
  to the HelmRelease so the hostNetwork controller also runs on `k8s-node-1`
  (`:443` there was closed before). All four node IPs are now entry points.
- **Vault Ingress renamed** `vault.local` → **`vault.k8s.lan`** (cert-manager
  re-issued `vault-tls` from `tower-ca`). Removed the manual
  `10.10.120.102 vault.local` line from `/etc/hosts` on `archasus` — the hand-fix
  is now folded into IaC.
- Node IPs are stable via **pre-existing DHCP reservations** on the router
  (`dhcp.@host[4..7]`, MAC → 10.10.120.101-104); not yet codified in IaC.
- Verified from `archasus`: `nslookup vault.k8s.lan 192.168.1.1` → all four node
  IPs; `getent hosts`; `curl --cacert tower-ca https://vault.k8s.lan`.

Sources: `uci`/`nslookup`/`getent`, `kubectl`, `flux`.

---

## 2026-10-13 — control-plane OOM: VM memory over-commit on proxmox

- **Symptom:** `kubectl` started failing with `TLS handshake timeout` / connection
  refused.
- **Cause:** the 4 k8s VMs were configured `memory=4096, balloon=1024` on a
  proxmox host with only **16 GB** (4×4 = 16 GB + host ⇒ over-committed). Proxmox
  ballooned the guests down to ~1.2 GB each; the control-plane VM could not fit
  etcd + kube-apiserver (~500 MB) + controller-manager + Calico, so the **kernel
  oom-killer repeatedly killed kube-apiserver / kube-controller-manager /
  kube-scheduler / calico-kube-controllers** (no swap in the guests).
- **Fix (variant B):** give the control-plane its full memory and trim the
  workers — on proxmox: `qm set 101 -balloon 4096`, `qm set {102,103,104}
  -memory 3072 -balloon 3072`, then reboot the VMs (Proxmox does not re-evaluate
  the balloon target live). Result: node-1 **3939 MB**, workers **~3000 MB**;
  host keeps ~2.5 GB free (16 GB total). apiserver stable, **0 OOM** since.
- Reality vs inventory: the nodes are not all 4 GB — node-1 4 GB (fixed),
  workers 3 GB. `docs/network/inventory.yaml` updated.

Sources: `kubectl`, `free`, `dmesg`, `qm config`/`qm monitor`/`qm set` on proxmox.

---

## 2026-10-13 — TLS: cert-manager + internal CA

Executed from `archasus` (Flux).

- Deployed **cert-manager v1.21.2** (chart `v1.21.2`) via Flux in namespace
  `cert-manager` (HelmRepository `charts.jetstack.io`); CRDs via `installCRDs`.
- Internal **CA chain** (`k8s/apps/ingress/cert-manager/clusterissuers.yaml`):
  ClusterIssuer `selfsigned` → `Certificate/tower-ca` (CN `tower-cloud-ca`,
  ECDSA P-256, 10 y) → ClusterIssuer `tower-ca`. No public ACME — the services
  are LAN-only.
- Exposed **Vault UI over TLS**: Ingress `vault/vault` (`vault.local` → vault:8200)
  annotated `cert-manager.io/cluster-issuer: tower-ca`; cert in Secret
  `vault/vault-tls`. Verified from `archasus`:
  `curl --resolve vault.local:443:<node> --cacert tower-ca https://vault.local/v1/sys/health`
  → OK.
- To trust: import the `tower-ca` `ca.crt` into clients
  (`kubectl -n cert-manager get secret tower-ca -o jsonpath='{.data.ca\.crt}' | base64 -d`).
  To resolve names, add `<node-ip> vault.local` to hosts / router DNS.

Sources: `kubectl`, `flux`, `openssl`, `curl`.

---

## 2026-10-13 — Object storage: Garage (MinIO substituted)

Executed from `archasus` (Flux + ESO + Vault).

- **MinIO is no longer usable:** `docker.io/minio/minio` is gone ("object not
  found") and `quay.io/minio/minio` returns `unauthorized` (MinIO moved to a
  gated/subscription model). Substituted **Garage v2.4.1** (`dxflrs/garage`).
- Added a dedicated tower iSCSI LUN **`garage`** (50 GiB, **lun 2**); static PV/
  PVC (StorageClass `tower-iscsi-static`).
- Deployed Garage (`k8s/apps/storage/garage/`) via Flux. Its `garage.toml` is
  rendered by an **ExternalSecret** from Vault: `secret/garage`
  (`rpc_secret`, `admin_token`, created out-of-band). No secret values in git.
- Bootstrap (one-off): `garage layout assign -z dc1 -c 45G <node>` + `layout
  apply --version 1`; created bucket `artifacts` and key `apps`; stored the S3
  key in Vault `secret/garage-s3` (`access_key`/`secret_key`/`endpoint`/region/
  bucket) for workloads via ESO.
- Validated: `aws-cli` pod uploaded and listed `s3://artifacts/test.txt`.
- Services: S3 `:3900`, web `:3902`, admin `:3903` (ClusterIP `garage.garage`).

Sources: `kubectl`, `garage`, `aws-cli`, `vault`. Key values not recorded here.

---

## 2026-10-13 — block CSI decision: static RWO PVs (no dynamic driver)

- Evaluated dynamic **block** CSI for tower. None fits: democratic-csi's iSCSI
  drivers require **ZFS** *and* `targetcli` (AUR-only on Arch, helpers broken),
  and `csi-driver-iscsi` only attaches pre-created LUNs. Tower is mdadm RAID1 +
  configfs LIO, no ZFS.
- **Decision:** RWO block stays **static** (StorageClass `tower-iscsi-static`).
  Revisit only if ZFS/DRBD on the running kernel proves viable.
- Moved the `tower-iscsi-static` StorageClass out of the Vault dir to
  `k8s/apps/storage/static-iscsi/`; added `scripts/new-iscsi-pv.sh` (renders a
  PV+PVC for a new LUN) and `docs/storage/static-iscsi.md`. Vault keeps its
  static PV (lun 1). `docs/storage/plan.md` updated with the decision.

Sources: democratic-csi / csi-driver-iscsi docs; `kubectl`.

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
