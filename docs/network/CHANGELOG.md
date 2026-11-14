# Network change log / observations

One entry per recon session or infrastructure change. Newest first.

---

## 2026-11-14 — wstunnel (WSS) fallback transport + automatic Traefik switch

- **Why:** public ingress had a single transport (Xray VLESS Reality + SSH
  reverse tunnel). The provider already DPI-drops WireGuard and throttles
  non-standard-port TLS and the `chrome` ClientHello, so a VLESS Reality block
  would take the public sites down with no backup (ADR-0007).
- **Fallback:** `k8s-node-1` now runs `wstunnel-fallback.service` (wstunnel
  11.1.0 client) to `wss://relay.metoda.cc` with reverse tunnels
  `127.0.0.1:10444 -> ingress-nginx` and `127.0.0.1:12223 -> GitLab shell` on
  `adminvps`. `adminvps` runs `wstunnel-server.service` on `127.0.0.1:8080`
  behind Traefik, which terminates a normal Let's Encrypt certificate for
  `relay.metoda.cc` (wildcard DNS already covers it). The upgrade-path prefix
  is a credential (out-of-band, not in git); the server restriction file allows
  only the two loopback reverse binds.
- **Switching:** `adminvps_traefik` now uses the file-provider directory
  `/etc/traefik/conf.d` (`routers.yml` + `backend.yml`). A 30 s timer probes
  `127.0.0.1:10443` (primary) and `:10444` (fallback) and, after two agreeing
  probes, atomically swaps `backend.yml` between `backends/primary.yml` and
  `backends/fallback.yml`; Traefik reloads without a restart. Primary wins when
  healthy, so wstunnel is standby.
- **IaC:** role `wstunnel_fallback` (server/client) + playbook
  `ansible/playbooks/wstunnel-fallback.yml`; `adminvps_traefik` gained the relay
  router, directory provider, `traefik-ingress-failover` script/service/timer,
  and `curl`. ADR-0007.
- **Verified:** fallback backend `127.0.0.1:10444` returns 200/302 for
  `cloud.`/`auth.`; with the primary reverse tunnel stopped, the switch moved
  Traefik to the fallback and public sites stayed 200/302; the primary
  reconnected within ~10 s and the switch moved back. Public ingress was 200/302
  on the primary before and after.
- **Limitation:** the fallback shares `adminvps` (same IP) and the home uplink,
  so it does not survive an IP-level block or a VPS outage.

---

## 2026-11-14 — mwan3 conntrack flush covers tracking-only WAN failover

- **Symptom/gap:** a provider outage usually leaves the link to the nearest ISP
  switch up, so netifd emits no `ifdown`/`ifup`; mwan3track alone detects the
  upstream loss. `flush_conntrack` was limited to `ifup`/`ifdown`, so the
  tracking-only transition left stale NAT/mark entries (confirmed in
  `/etc/hotplug.d/iface/15-mwan3`: `connected`/`disconnected` are accepted and
  `mwan3track` raises `disconnected` on tracking loss).
- **Change:** `router_openwrt` now sets `mwan3.<wan|wan2>.flush_conntrack =
  [ifup, ifdown, connected, disconnected]`. Applied to the main router
  2026-11-14; verified with `uci get` on both interfaces.
- **Caveat:** the flush clears stale entries, it does not migrate an
  established flow — the Xray/SSH tunnel still reconnects via SSH keepalive
  (`ServerAliveInterval=15`, `ServerAliveCountMax=3`).

---

## 2026-11-14 — Xray handshakes stalled by DPI: firefox fingerprint + :443

- **Symptom:** once Hermes used the Xray SOCKS, most *new* VLESS connections
  stalled (~1/10 succeeded) while the established SSH reverse tunnel kept
  working. Captures showed the client's TLS ClientHello split over two TCP
  segments and the second segment never acknowledged; TCP retransmitted the
  first 1448-byte segment without success.
- **Root cause:** the `chrome` uTLS fingerprint sends a ~1.8 KB ClientHello
  (post-quantum key share) that spans two segments; the home ISP's DPI stalls
  that handshake. The same handshake is fine from the VPS to itself, so the
  server and Reality keys are correct.
- **Fix 1 — fingerprint:** the client uses `firefox` (single-segment
  ClientHello). Measured 8/8 for both `firefox` and `safari` vs 1–2/10 for
  `chrome`; set 12/12 after rollout.
- **Fix 2 — public :443:** the ISP also throttles TLS on non-standard ports, so
  Reality now rides `78.17.99.16:443`. Traefik peeks the TLS SNI and passes
  `www.oracle.com` through to `127.0.0.1:36243` (`tls.passthrough`); all other
  SNI is terminated normally. Direct `:36243` stays as a fallback.
- **IaC:** `xray_tunnel_fingerprint: firefox`, `xray_tunnel_server_port: 443`;
  `adminvps_traefik_xray_reality_sni/backend` + a TCP router in `dynamic.yml`.
- **Verified:** SOCKS 12/12, public ingress 10/10, Hermes
  `/api/status` → telegram `state: connected`.

---

## 2026-11-14 — Hermes Telegram egress moved to the Xray SOCKS

- **Why:** WireGuard was retired (entry below), and Hermes' `TELEGRAM_PROXY`
  pointed at the now-dead `socks5://10.200.0.1:1080` (danted over WG). Telegram
  is blocked from the home ISP, so Hermes needs a working proxy.
- **Change:** the Xray VLESS Reality client on `k8s-node-1` now listens on the
  node's VLAN120 address (`10.10.120.101:1081`) instead of loopback only
  (`xray_tunnel_socks_host`), so in-cluster workloads can use it. Hermes'
  `TELEGRAM_PROXY` is now `socks5://10.10.120.101:1081`.
- **IaC:** `ansible/roles/xray_tunnel` defaults/templating unchanged apart from
  the listen address; the role now flushes handlers before its checks (the
  earlier "connection refused" was the verify task running against the old
  process before the restart handler fired).
- **Verified:** from the Hermes pod, SOCKS → `ifconfig.me` = `78.17.99.16` and
  `api.telegram.org` answers 302; public vhosts still 200/302.
- **Note:** the SOCKS listener has no auth and is reachable from VLAN120/LAN
  (never the internet). It replaced a host-level WG path, so the cluster now
  depends on one node's systemd service — a GitOps/k8s-native proxy remains an
  open question.

---

## 2026-11-14 — WireGuard retired (transport is DPI-blocked)

- **Why:** the home provider DPI-drops WireGuard *transport* packets —
  handshakes pass, data does not (entries of 2026-11-13). The tunnel had been
  useless since then; public ingress already runs over the Xray VLESS Reality +
  SSH reverse tunnel from k8s-node-1 (ADR-0006).
- **Keenetic:** removed the `wg0` interface and peer from `/etc/config/network`,
  dropped `wg0` from the `lan` firewall zone, and removed `kmod-wireguard` +
  `wireguard-tools`. No WG key remains on the box.
- **adminvps:** disabled and removed `wg-quick@wg0` + `/etc/wireguard/wg0.conf`,
  purged the wireguard packages, deleted the public `51820/udp` UFW rule and
  the `10.200.0.0/24` admin-port exceptions. `danted` no longer listens on
  `10.200.0.1`; its WireGuard pass rule is gone.
- **Consequence:** `1080` (danted), `2096`/`12267` (x-ui) are now closed —
  reach them with `ssh -L`. **Hermes' Telegram egress** used
  `socks5://10.200.0.1:1080`; it was repointed to the Xray SOCKS client on
  k8s-node-1 (entry above).
- **IaC/docs:** the hardening role now deletes the retired UFW rules; the
  danted role drops the WG listener/allowance; Traefik comments, README and
  inventory are updated; the `10.200.0.0/24` subnet is removed. ADR-0003 is
  superseded by ADR-0006.

---

## 2026-11-14 — WAN provider labels recorded (ZenCom / DomOnline)

- **Why:** LuCI shows only the UCI section names (`wan`, `wan2`), so there was
  no reminder which link belongs to which ISP.
- **Change:** `router_openwrt` now writes a legend comment at the top of
  `/etc/config/network` (`wan` = `eth0.2` = ZenCom primary, `wan2` = `eth0.3`
  = DomOnline failover). `uci commit network` drops comments, so the role
  re-inserts the block after its own commits; the names themselves are
  unchanged (renaming would touch firewall, mwan3 and the conntrack flush
  list). The mapping is also recorded in `inventory.yaml`.
- **Note:** this is a comment in the config file, not a label in the LuCI
  interface list — stock LuCI has no description field per interface.

---

## 2026-11-14 — Keenetic WG watchdog removed (WG transport is DPI-blocked)

- **Why:** public ingress now runs over the Xray tunnel; WireGuard transport
  packets are dropped by the home provider, so bouncing `wg0` cannot restore
  anything and only churns the interface every ~5 min.
- **Change:** removed the `/etc/crontabs/root` entry and `/usr/bin/wg-watchdog`
  (plus the bounce stamp) on the Keenetic, and deleted
  `ansible/roles/keenetic_wireguard`; the `keenetic` play was dropped from
  `site.yml`. The WG **interface itself remains** (keys/routes unchanged) for
  any non-public use.
- **Kept:** `router_openwrt` mwan3 `flush_conntrack` (WAN hygiene) and the
  `wan_wanb` policy. `scripts/public-health.sh` already checks the Xray
  tunnel instead.

---

## 2026-11-13 — Public ingress moved to Xray VLESS Reality + SSH reverse tunnel

- **Why:** the home provider drops WireGuard **transport** packets (handshake
  passes, data does not; proven with a bidirectional UDP echo 10/10 — see the
  entry below), so the public vhosts could not reach ingress-nginx over WG.
- **Architecture (applied):**
  - `k8s-node-1` runs the **Xray VLESS Reality client**
    (`xray-client.service`, SOCKS on `127.0.0.1:1081`) towards the existing
    x-ui inbound `78.17.99.16:36243` (serverName `www.oracle.com`, shortId
    `2f3f8321`; the UUID is passed out-of-band, not in git).
  - `xray-reverse-tunnel.service` keeps an SSH connection to adminvps
    **through that SOCKS proxy** and binds
    `127.0.0.1:10443 -> 127.0.0.1:443` (ingress-nginx hostNetwork) and
    `127.0.0.1:12222 -> 127.0.0.1:30222` (GitLab shell NodePort) on adminvps.
    The SSH key is dedicated (`~arch/.ssh/id_ed25519_xray` on k8s-node-1) and
    authorized on adminvps.
  - `adminvps_traefik` backends changed from the four WG node IPs to
    `https://127.0.0.1:10443` (GitLab SSH to `127.0.0.1:12222`); Traefik still
    terminates public TLS and skips tower-ca verification on the backend.
  - mwan3 default policy (v4/v6 + `https`) switched from `balanced` to
    `wan_wanb`: the 4G `wan2` path stalls TCP data after the handshake, so
    per-flow balancing hung ~half of the new SSH/Xray connections. Primary
    `wan` is preferred; `wan2` remains as failover.
- **IaC:** `ansible/roles/xray_tunnel`, `ansible/playbooks/xray-tunnel.yml`
  (fetches the x-ui Xray binary so client/server versions match), updated
  `adminvps_traefik` defaults and the `router_openwrt` mwan3 policy.
- **Verification:** local ingress on node-1 (each Host header) 200/302; SSH
  from archasus 10/10; public `cloud.metoda.cc` 10/10 200; GitLab SSH on
  :30222 reachable. Startup was flaky until the mwan3 policy change
  (balanced/4G).
- **Monitoring:** `scripts/public-health.sh` now probes the public vhosts and
  the two tunnel services on k8s-node-1 instead of the WG handshake. Install
  with `ansible-playbook ansible/playbooks/monitoring.yml -K`.
- **WG status:** remains configured for other uses, but it is **not** the
  public ingress path; its transport is still DPI-dropped. Its Keenetic
  watchdog was removed on 2026-11-14 (entry above).

---

## 2026-11-13 — Public entry points down: WireGuard lost across WAN failover

- **Symptom:** `https://cloud.metoda.cc` (and auth/hermes/gitlab) returned
  **504** from Traefik while OpenCloud itself was healthy (`status.php` 200,
  pod 1/1, PVC bound, ESO synced, LAN split-horizon access fine).
- **Root cause:** the main router's primary WAN (`eth0.2`, DHCP
  `100.64.48.134/20`) lost its upstream ~15:50 before the recon: link up and
  lease valid, but even its gateway `100.64.48.1` was unreachable (as were the
  ISP DNS servers). mwan3 failed over to `wan2` (`eth0.3` → 4G modem
  `192.168.1.119`, `10.108.221.4`). The **established WireGuard UDP flow did
  not survive the switch**: the Keenetic kept retrying
  `192.168.1.216:50827 → 78.17.99.16:51820` (handshake stale 15h42m), but those
  packets never reached adminvps (tcpdump: 0 in 65 s) while a fresh UDP flow
  from the same host did. adminvps also kept probing the stale endpoint
  `89.188.180.2:50827`.
- **`wan2` is not healthy either:** mwan3track logged repeated 100% loss to
  `1.0.0.1` (19:00, 19:09, 19:39) although the interface stayed `online`.
- **Prevention (applied 2026-11-13, `site.yml --limit router,keenetic`):**
  - `ansible/roles/router_openwrt`: `mwan3.<wan|wan2>.flush_conntrack =
    [ifup, ifdown]` — clears stale NAT/mark entries on netifd WAN events.
    Caveat: this incident was an mwan3 *tracking* failover, so netifd stayed
    up and the flush would not have fired; it is hygiene, not the guarantee.
  - New `ansible/roles/keenetic_wireguard` + `keenetic` inventory host:
    `/usr/bin/wg-watchdog` from cron (`*/2`) bounces `wg0` when the last
    handshake is older than 180 s (300 s cooldown). Re-creating the interface
    picks a new ephemeral UDP port → fresh conntrack/NAT at every hop. The WG
    keys/interface itself stays hand-configured (not in git). Confirmed running
    and it did bounce `wg0` (new listen port, handshake restored).
    **Removed 2026-11-14** — WG transport turned out to be DPI-dropped, so the
    bounce cannot help (see the entry above).
  - `scripts/public-health.sh` + `ansible/playbooks/monitoring.yml`: probes
    every public vhost through `78.17.99.16` (forced `--resolve`) and the `wg0`
    handshake age; optional healthchecks.io ping URL. Telegram cannot report a
    WG outage here because its egress goes through the same tunnel.
    **Not installed yet** — the playbook needs sudo on archasus; run it with
    `-K` (or install it manually). (The probe was later switched to the Xray
    tunnel services; see the entry above.)
- **Recovery attempt:** `ifdown/ifup wan` on the main router **restored the
  primary WAN** (the DHCP/upstream session was stale, not an ISP outage: the
  gateway `100.64.48.1` and `1.1.1.1` came back). It did **not** restore the
  tunnel: the WG handshake succeeds, but adminvps's transport packets (ICMP/TCP
  to `10.200.0.2` / `10.10.120.x`) leave the VPS and **never reach the main
  router** (tcpdump on the router: 0 inbound on `udp src port 51820` while
  adminvps sends 128-byte data), and the Keenetic's WG receive counter stays
  flat. **Decisive test (2026-11-13):** a bidirectional UDP echo from archasus
  through the same WAN (the VPS saw source `89.188.180.2`) got **10/10 requests
  and 10/10 replies**, while WireGuard transport still fails. So this is
  **WireGuard-specific filtering (DPI)** on the path, not generic UDP/CGNAT
  breakage: the filter lets the handshake (types 1/2) through and drops data
  (type 4). Both WANs show the same behaviour.
- **Still down:** public vhosts return 504 (after the primary WAN returned,
  Traefik is reachable again but has no backend over the WG tunnel).
- **Next:** watchdog or port changes cannot fix protocol-level DPI. Options:
  a TCP-based tunnel (Cloudflare Tunnel / frp / the existing Xray), an
  obfuscated WG transport (AmneziaWG / udp2raw) if UDP is preferred, or a
  report to the ISP. Both WANs are online again (`wan`, `wan2`).
  **Resolved the same day** by the Xray tunnel entry above;
  `ansible-playbook ansible/playbooks/monitoring.yml -K` remains to be run.

---

## 2026-11-12 — OpenCloud public links no longer require a password

- **Why:** OpenCloud 7.2.4 defaults `public_sharing_share_must_have_password`
  to `true`, so every public link had to be created with a password.
- **Change:** `k8s/apps/opencloud/deployment.yaml` sets
  `OC_SHARING_PUBLIC_SHARE_MUST_HAVE_PASSWORD=false` and
  `OC_SHARING_PUBLIC_WRITEABLE_SHARE_MUST_HAVE_PASSWORD=false`. The `OC_`
  prefix reaches both the `sharing` and `frontend` (OCS) services.
- **Scope:** removes the requirement for **new** links only; existing
  password-protected links keep their passwords.
- **Deploy:** committed `8df9488` and pushed to `master`; Flux reconciles
  `k8s/` from git. `https://cloud.metoda.cc/status.php` stayed 200 (OpenCloud
  7.2.4) while polling after the push.
- **Verification:** confirmed on 2026-11-13 from `archasus` — the running pod
  has `OC_SHARING_PUBLIC_SHARE_MUST_HAVE_PASSWORD=false` and
  `OC_SHARING_PUBLIC_WRITEABLE_SHARE_MUST_HAVE_PASSWORD=false` in its env.

---

## 2026-11-08 — GitLab blob page 502 fixed (webservice limit 2 GiB)

- **Symptom:** `https://gitlab.metoda.cc/special_tools/sign_skill/-/blob/master/README.md?ref_type=heads`
  returned 502 while other pages worked.
- **Cause:** the webservice container (1536 MiB cap, one Puma worker) hit a
  **memory cgroup OOM** serving the blob request; `dmesg` shows `ruby`/
  `puma srv tp` killed with ~1.1 GiB anon-rss, `OOMKilled` exit 137, then the
  readiness probe failed during restart.
- **Fix:** webservice limit raised to **2 GiB** (request stays 1 GiB, one Puma
  worker). Node3 has 4 GiB and keeps ~1 GiB free.
- **Verified:** webservice 2/2 with 0 restarts, `/-/readiness` 200, blob URL
  302 (redirect to sign-in when unauthenticated), no new OOM.

---

## 2026-11-08 — GitLab SSH enabled on port 30222

- **Why:** `git@gitlab.metoda.cc` hit the node/admin sshd on :22 and returned
  `Permission denied (publickey)`; GitLab Shell was not reachable.
- **Change:** `gitlab-gitlab-shell` is now a **NodePort 30222** service;
  `global.shell.port: 30222` makes GitLab advertise
  `ssh://git@gitlab.metoda.cc:30222/...`. adminvps Traefik gained a TCP
  entrypoint on :30222 forwarding to the four nodes over WireGuard, and UFW
  allows public 30222/tcp. A `~/.ssh/config` entry on archasus maps
  `gitlab.metoda.cc` to port 30222, so `git@gitlab.metoda.cc` works.
- **Verified:** `ssh -T git@gitlab.metoda.cc` and
  `ssh -p 30222 git@78.17.99.16` both return
  `Welcome to GitLab, @aiskovskikh!`; `ssh-keyscan` shows the GitLab Shell host
  key. Port 22 stays with the node/admin sshd.
- **Note:** an intermediate ingress-nginx TCP approach was abandoned because
  Flux reverted the uncommitted HelmRelease change; NodePort 30222 lives in the
  manually applied GitLab HelmRelease and does not depend on a push.

---

## 2026-11-08 — GitLab webservice cgroup OOM fixed (502 on `/‑/profile/preferences`)

- **Symptom:** a second 502, this time on
  `https://gitlab.metoda.cc/-/profile/preferences`, while the rest of GitLab
  answered.
- **Cause:** the webservice container (cap 1536 MiB) ran **two Puma workers**;
  `dmesg` showed a memory-cgroup OOM killing two `ruby` processes
  (~0.9 GiB anon each). The kubelet restarted the container, so ingress had no
  ready endpoint for the duration.
- **Fix:** `gitlab.webservice.workerProcesses: 1` in the HelmRelease. One Puma
  worker plus workhorse/logger stays under the 1536 MiB cap on the 4 GiB node.
- **Verified:** HelmRelease `Ready` (revision v5), webservice 2/2 with 0
  restarts, readiness 200, `/‑/profile/preferences` redirects (302) instead of
  502, node3 ~1 GiB free, no new cgroup OOM.

---

## 2026-11-08 — GitLab worker OOM fixed (502 on `gitlab.metoda.cc`)

- **Symptom:** `https://gitlab.metoda.cc/aiskovskikh` returned 502 (LAN and
  public). `k8s-node-3` was `NotReady` (`Kubelet stopped posting node status`)
  and the `gitlab-webservice-default` Endpoints list was empty.
- **Cause:** node3 had only 3 GiB and hosted webservice + toolbox + exporter +
  shell + ingress-nginx + Calico/Flux. A global OOM killed Calico/logger
  processes, the kubelet began thrashing (load >100) and could not post status.
- **Fix:** node3 raised to 4 GiB on proxmox (`qm set 103 -memory 4096
  -balloon 4096`; `qm reboot` failed, so `qm reset`), stale `Unknown` pods
  deleted. Webservice container capped at 1536 MiB (was 2 GiB), toolbox at
  512 MiB. Node3 now has ~1.5 GiB free.
- **Verified:** webservice 2/2, Endpoints populated, HelmRelease `Ready`,
  `/-/readiness` ok, `/aiskovskikh` returns 200.
- **Note:** node3's 4 GiB is a hand change; inventory now records
  `mem_mb: 4096` for node3. The proxmox tofu stack is still a placeholder.

---

## 2026-11-08 — GitLab CE deployed at `gitlab.metoda.cc`

- **Deployed:** `k8s/apps/gitlab/` — GitLab CE 19.5.3 (Helm chart 10.5.4) in
  namespace `gitlab`, canonical host `https://gitlab.metoda.cc`.
  - State per ADR-0002: PostgreSQL 18.6 (db/user `gitlab`) and Redis 7 on
    **tower**; object storage is Garage S3 (buckets `gitlab-artifacts`,
    `git-lfs`, `gitlab-uploads`, `gitlab-packages`). Only Gitaly uses a block
    volume: tower iSCSI LUN 6 (`gitlab-gitaly`, 50 GiB sparse), bound by
    `matchLabels`.
  - Disabled components to fit the small cluster: bundled cert-manager,
    nginx-ingress, Prometheus, Container Registry, KAS, Pages, GitLab Runner,
    Envoy Gateway. Reuses ingress-nginx + tower-ca. Webservice uses a 1 GiB
    request / 2 GiB limit and `strategy: Recreate`; a post-renderer raises
    Deployment `progressDeadlineSeconds` to 3600 so first-boot migrations do
    not trip Helm's stalled-resource detection.
  - SSO: Keycloak realm `tower`, confidential client `gitlab`; the client
    secret is mirrored into ns `keycloak` by a separate ExternalSecret so a
    missing Vault `secret/gitlab` cannot break Keycloak's own credentials.
    Verified: `POST /users/auth/openid_connect` 302s to
    `https://auth.metoda.cc/realms/tower/...`.
- **Tower infra:** `tower_storage` now creates the GitLab Postgres role/db
  (`GITLAB_POSTGRES_PASSWORD`), a Redis 7 container on `/mnt/raid1/redis`
  (`GITLAB_REDIS_PASSWORD`, AOF, gid 1000), the four Garage buckets for the
  GitLab key (`GITLAB_S3_ACCESS_KEY`/`GITLAB_S3_SECRET_KEY`) and the Gitaly
  LUN. Postgres runs with `max_locks_per_transaction=256` (default 64 made
  `db:schema:load` fail with "out of shared memory").
- **Disk expansion:** node disks were 22.2 GiB; GitLab image pulls filled them
  and kubelet evicted pods (ingress-nginx included) on DiskPressure. All four
  VMs were resized to 40 GiB on proxmox (`qm resize ... 40G`), then
  `growpart`/`resize2fs` inside the guests (root `sda1`, ~21-29 GiB free).
  This matches the aspirational `disk_gb = 40` in `tofu/proxmox/variables.tf`;
  the tofu stack is still a placeholder (no managed state).
- **Edge/DNS:** `gitlab` A record in `tofu/dns`, `gitlab.metoda.cc` in
  `adminvps_traefik_hosts`, and split-horizon `gitlab.metoda.cc` in
  `router_openwrt`. Public path verified with a Let's Encrypt cert
  (`CN=gitlab.metoda.cc`, expires 2027-02-06) and `/-/readiness` 200.
- **Runbook:** `docs/gitlab/README.md` (Vault fields, apply order, verify,
  resource caveats).
- **Status:** applied via `kubectl apply` (the manifests are not committed to
  `master` yet, so Flux does not own them; commit/push is still needed).
  Verified: HelmRelease Ready, all pods Running/Completed, PVC `Bound` to
  `tower-iscsi-gitlab-gitaly`, `/users/sign_in` 200, GitLab readiness `ok`.
- **Not in git:** Vault `secret/gitlab` values (`postgres_password`,
  `redis_password`, `initial_root_password`, `oidc_client_secret`,
  `s3_access_key`, `s3_secret_key`).

---

## 2026-11-07 — OpenCloud space «Общее» quota raised to 500 GB

- **Change:** the project space «Общее» (drive
  `18e7c45f-…$d8fc1c47-…`) quota was raised from 1 GB to 500 GB via
  `PATCH /graph/v1.0/drives/{id}` with `quota.total=500000000000` bytes.
  Used at the time: ~973 MB (0.19%), state `normal`.
- **How:** short-lived `opencloud auth-app create` token + Basic auth (the
  OIDC password grant failed with stale stored credentials). This is runtime
  app data, not an IaC resource.

---

## 2026-11-07 — OpenCloud config declarative (ConfigMap + Vault, no init)

- **Changed:** `k8s/apps/opencloud/` no longer runs `opencloud init` and no
  longer has the `opencloud-config` PVC. The previously generated
  `/etc/opencloud/opencloud.yaml` is now the `opencloud-yaml` ConfigMap with
  `${OCCFG_*}` placeholders; the values come from Vault `secret/opencloud`
  through the `opencloud-secrets` ExternalSecret (`envFrom`). Non-secret values
  and the installation IDs (system/admin user id, `graph.application.id`,
  mount ids, `service_account_id`) are pinned in git.
- **Trade-off:** the pinned IDs and IDM bind passwords belong to this
  installation's embedded IDM and the `opencloud-data` LUN. Do not reuse this
  config after wiping/rebuilding that LUN without re-running `opencloud init`.
- **Migration order:** load the 12 `OCCFG_*` secrets into Vault **before**
  applying (they were read from the old config PVC); otherwise the placeholders
  expand empty and the pod fails.

---

## 2026-10-23 — Hermes published at `hermes.metoda.cc`

- **DNS:** added `hermes` A → `78.17.99.16` in `tofu/dns` (record
  `95011463`); public resolver returns it. Router split-horizon
  (`router_openwrt`) now answers `hermes.metoda.cc` with the four
  ingress-nginx node IPs so LAN clients hit the cluster directly.
- **Public TLS/routing:** `adminvps_traefik_hosts` includes
  `hermes.metoda.cc`; Traefik issued a Let's Encrypt cert and proxies to
  ingress-nginx over WireGuard. UFW already allows 80/443.
- **Kubernetes:** Ingress `hermes` lists both `hermes.k8s.lan` and
  `hermes.metoda.cc`; the `hermes-tls` cert has both SANs (tower-ca on the
  back end, LE on the public edge).
- **OIDC:** Hermes `dashboard.public_url` is now
  `https://hermes.metoda.cc`; the Keycloak client `hermes` allows callbacks on
  both names. The authorization redirect carries
  `redirect_uri=https://hermes.metoda.cc/auth/callback`, and Keycloak serves
  the login page (no invalid-redirect error).
- **Verified:** public `dig` → adminvps; `dig` via router → 10.10.120.x;
  `https://hermes.metoda.cc` → 302 `/auth/login` (LE cert `CN=hermes.metoda.cc`
  to 2027-01-21); `/api/status` `overall: ok`, Telegram `connected`.
- **Note:** `hermes.k8s.lan` still serves the UI, but OAuth always returns to
  the canonical `hermes.metoda.cc` callback.
- **Follow-ups:** commit/push so Flux owns the manifests; rotate the Telegram
  bot token (printed during setup).

---

## 2026-10-23 — Hermes Agent deployed; adminvps SOCKS egress for Telegram

- **Added:** `k8s/apps/hermes/` — namespace, `hermes-data` PVC (`tower-local`,
  10 Gi), ExternalSecret from Vault `secret/hermes`, one Deployment running
  `gateway run` (Telegram) with the image's s6 tree also supervising the
  dashboard in the same container, Service, Ingress `https://hermes.k8s.lan`,
  and a throwaway `Certificate` whose `ca.crt` extends the container trust
  bundle with `tower-ca`.
- **Applied:** `kubectl apply -f k8s/apps/hermes/`; Keycloak client
  reconciled with `ansible-playbook playbooks/keycloak.yml` (changed once).
  Pod is 1/1 Running; `/api/status` reports `overall: ok`,
  `gateway_running: true`, Telegram `connected`.
- **Auth:** dashboard binds `0.0.0.0`, so Hermes' gate is mandatory. It uses
  the built-in self-hosted OIDC provider against Keycloak realm `tower`
  (public PKCE client `hermes`, callback `/auth/callback`) instead of
  oauth2-proxy forward-auth. `dashboard.trusted_proxies` lists the four node
  IPs so `X-Forwarded-Proto` yields Secure cookies.
- **Keycloak:** added the `hermes` client to both the bootstrap realm ConfigMap
  and `ansible/roles/keycloak_config` (admin-API reconciliation).
- **Secrets:** Vault `secret/hermes` has `deepseek_api_key`,
  `telegram_bot_token`, `telegram_allowed_users` (currently only
  `2043981105` / @aiskovskikh). Values are not in git.
- **Model:** `deepseek-chat` (provider `deepseek`) in `configmap.yaml`;
  `_config_version: 51` pinned so the read-only ConfigMap mount is not
  migrated/written on boot.
- **Telegram egress:** the home ISP blocks every Telegram API IP. Added
  `ansible/roles/adminvps_danted` (wired into `playbooks/adminvps.yml`, tag
  `danted`): danted also listens on `10.200.0.1:1080`, UFW allows
  `10.10.120.0/24`, and SOCKS without auth is allowed only for the WireGuard
  and k8s node networks; the catch-all rule keeps password auth. Gateway uses
  `TELEGRAM_PROXY=socks5://10.200.0.1:1080`.
- **Verify:** `danted -f /etc/danted.conf -V` passes; SOCKS5 CONNECT from the
  pod to `api.telegram.org:443` completes TLSv1.3; dashboard
  `/auth/login?provider=self-hosted` redirects to the Keycloak authorization
  endpoint with PKCE and the registered callback; `kubectl apply
  --dry-run=client` and Ansible syntax checks pass.
- **Follow-ups:** add @iskovskikh's numeric ID (he must message
  @metodacc_bot), commit/push the manifests so Flux owns them, and **rotate
  the Telegram bot token** (it was printed during setup) + update Vault.

---

## 2026-10-23 — external-exposure audit; `adminvps_hardening` applied

- **Scope:** read-only audit of the only internet-facing host (`adminvps`,
  `78.17.99.16`) from `archasus`, plus Shodan InternetDB and check-host.net.
  The home network is behind CGNAT and not directly reachable.
- **Public surface found:** TCP `22` (SSH), `80`/`443` (Traefik LE, only
  `cloud.`/`auth.` routed; unknown hosts 404 with the default Traefik cert),
  `2096`/`12267` (x-ui subscription/panel), `36243` (Xray VLESS Reality);
  UDP `443` (Hysteria) and `51820` (WireGuard). `danted :1080` listens on
  `0.0.0.0` but was filtered from three external vantage points.
- **Risks:** SSH still allows passwords and root (`PermitRootLogin yes`); `arch`
  has `NOPASSWD: ALL`; fail2ban is absent; ~6.3k failed SSH logins/day (~56k in
  7 days, ~18k for root). UFW is inactive and `iptables INPUT` is `ACCEPT`, so
  any new `0.0.0.0` listener would be public. x-ui panel is plain HTTP (random
  path/user, bcrypt password). Keycloak admin console is public and the realm
  config has no brute-force protection. `apt` reports 171 upgradable packages
  (no security tag) and the host needs a reboot.
- **Change (code + applied):** added `ansible/roles/adminvps_hardening` and
  wired it into `ansible/playbooks/adminvps.yml` (tags `hardening`). Applied on
  2026-10-23: key-only SSH (drop-in `00-adminvps-hardening.conf`, effective
  `sshd -T` assertions), default-deny UFW (`22/80/443/36243` tcp, `443/51820`
  udp public; `1080/2096/12267` only from WireGuard `10.200.0.0/24`), and
  fail2ban for sshd banning via UFW.
- **Verified after apply:** UFW active with default deny incoming; external scan
  from `archasus` shows `22/80/443/36243` open and `1080/2096/12267` blocked;
  `cloud.metoda.cc` 200, `auth.metoda.cc` 302, HTTP→HTTPS 301; WireGuard
  handshake fresh; password-auth probe returns `Permission denied (publickey)`;
  fail2ban sshd active and had already banned 3 IPs. A second role run reported
  `changed=0` (idempotent).
- **Follow-ups:** decide whether x-ui subscription `2096` must remain public
  (static client configs are unaffected); enable Keycloak brute-force detection
  + MFA or restrict `/admin`; patch/reboot (171 packages, reboot required);
  consider closing `NOPASSWD: ALL` on `arch`; consider removing the unused
  wildcard DNS record. Rotate secrets if the audit transcript was retained.
- **Verify:** `ansible-playbook playbooks/adminvps.yml --syntax-check` passes;
  `--tags hardening` re-run is idempotent.

---

## 2026-10-19 — `metoda.cc` moved to Timeweb Cloud; DNS restored and codified (ADR-0005)

- **Transfer check:** `GET /api/v1/domains` now returns `metoda.cc` (zone id
  `25051187-metoda.cc`, `provider: PDR`, `paid` until **2027-10-15**, auto-renew
  on) — previously `total: 0`/`404`. API reads and writes work; an empty
  `POST .../dns-records` returns `400 validation`, not `403`.
- **Gap found:** the old hosting-panel `A` records did **not** transfer. The
  zone held only the Timeweb default MX×2 + SPF TXT (plus an empty wildcard TXT
  and an empty wildcard MX). Public resolvers (`1.1.1.1`, `8.8.8.8`,
  authoritative Timeweb NS) returned nothing for apex, `www`, `cloud`, `auth`
  and `*`, so external access was broken. Locally the router's split-horizon
  answers (`10.10.120.x`) masked it. `adminvps` itself was healthy: `cloud` 200,
  `auth` 302, LE cert `cloud.metoda.cc` valid to **2027-01-13**.
- **Change:** added **`tofu/dns`** (provider
  `tf.timeweb.cloud/timeweb-cloud/timeweb-cloud` v1.8.3; `data.twc_dns_zone` +
  `twc_dns_rr`) with five `A` records — `@`, `www`, `cloud`, `auth`, `*` →
  `78.17.99.16`. `tofu apply` created all five; a follow-up `tofu plan` reports
  no drift. Default MX/SPF are intentionally left unmanaged.
- **Cleanup:** deleted the transfer leftovers (empty wildcard TXT, empty
  wildcard MX) and the per-subdomain SPF TXT records that the API auto-created
  when the subdomains were added.
- **Verified:** after Timeweb's multi-NS propagation, `1.1.1.1`, `8.8.8.8` and
  `9.9.9.9` resolve all five names to `78.17.99.16`;
  `https://cloud.metoda.cc` → `200`, `https://auth.metoda.cc` → `302` to
  `/admin/` through `adminvps` with the Let's Encrypt certificate.
- **Docs:** ADR-0004 marked superseded; new ADR-0005; `inventory.yaml` and
  `docs/network/README.md` updated.
- **Observation:** `GET /api/v1/account/status` still says `is_blocked: true`,
  but domain/DNS writes succeed — revisit if API calls begin to fail.
- **Follow-ups:** LAN clients still get the internal `tower-cloud-ca`
  certificate via split-horizon (expected). Consider DNS-01 + wildcard now that
  the zone is API-managed.

---

## 2026-10-16 — tower: stale nginx transmission vhost removed; k8s + services verified healthy

- **Change (tower nginx):** the legacy vhost `/etc/nginx/sites-enabled/transmission.conf`
  was removed (symlink deleted; `/etc/nginx/sites-available/transmission.conf` kept
  as backup) and nginx reloaded. The vhost proxied to `127.0.0.1:9091`, which was
  removed when Transmission migrated to k8s on 2026-10-13 — any request to
  `transmission.tower.local` returned `502`. Requests now fall through to the
  intentional catch-all `tower.conf` (`server_name _; return 444;`), which also
  explains the recon probe `tower nginx :80 -> 000` (empty reply is by design, not
  a fault). Legacy nginx sites on tower are **not** managed by Ansible (the
  `tower_storage` role covers only nfs/iscsi/garage/postgres/dmcache).
- **Recon (read-only):** all services up. k8s cluster `k8s-node-1..4` (10.10.120.101-104)
  all `Ready` (v1.37.1, control-plane on node-1) — contradicting the older
  "cluster currently down" note in AGENTS.md; system pods (calico, coredns,
  etcd/apiserver, csi-nfs, cert-manager, external-secrets, ingress-nginx, flux)
  all `Running`; apps jellyfin (node-4), transmission (node-2), keycloak `Running`;
  Flux `flux-system` reconciled `master@8e6d5e3`. tower storage `md0` RAID1 `[UU]`
  healthy behind dm-cache (writethrough), `/mnt/raid1` 2.4/3.6 TB (71%);
  docker `postgres`+`garage` up.
- **Follow-up:** tower's nginx sites are unmanaged hand-config — consider adding
  them to IaC (or dropping nginx there entirely) so they cannot drift.

---

## 2026-10-15 — tower: Intel Optane SSD added as a dm-cache for /mnt/raid1

- **Change:** an **Intel Optane Memory M10 (16 GB, `MEMPEK1J016GAL`)** was
  installed in tower (`/dev/nvme0n1`, ~13.4 GiB usable). It now caches the mdadm
  array `/dev/md0` via the kernel **device-mapper `cache`** target in
  **writethrough** mode: GPT on the Optane (`p1` = 1 GiB metadata, `p2` = ~12 GiB
  cache data, 64 KiB blocks, `smq` policy), exposed as `/dev/mapper/raid1cache`.
  `/mnt/raid1` now mounts that device instead of `/dev/md0` — the ext4 and the
  array data are unchanged (dm-cache never rewrites the origin in-place).
- **Why not bcache:** bcache's superblock sits at an 8 KiB offset and needs the
  backing device reformatted around it — unacceptable for a live 2.4 TB ext4 with
  no backup. dm-cache maps the existing device in place.
- **Cutover:** codified as `tower_cache_enabled` in the `tower_storage` role
  (`tasks/dmcache.yml`, tag `tower_cache`, `templates/tower-cache.*`);
  `/etc/fstab` now points `/mnt/raid1` at `/dev/mapper/raid1cache` and the
  `tower-cache` boot unit creates the device before `local-fs-pre.target`. Procedure
  validated first on loop devices (table syntax, metadata auto-format, clean
  teardown), then applied and cut over by **rebooting tower** (2026-10-16).
  Applied with `ansible-playbook playbooks/site.yml --limit tower
  -e tower_cache_enabled=true --tags tower_cache` (tagged so garage/postgres
  config, which needs env secrets, is untouched).
- **Verified after reboot:** `/mnt/raid1` mounts `/dev/mapper/raid1cache` (ext4,
  same UUID, 2.4 TB intact); `dmsetup status` shows `writethrough`, `smq`, 64 KiB
  blocks, promotions growing; NFS/iSCSI/Garage/Postgres services and all k8s app
  pods back up. Kernel note: `Origin device (md0) discard unsupported: Disabling
  discard passdown` — benign.
- **Status:** applied and live (`ansible/roles/tower_storage`,
  `docs/storage/dmcache.md`). Writethrough = a dead Optane costs cache warmth,
  not data. Benefit expected on random I/O (fs metadata, Postgres, Garage
  metadata, the LUNs); large sequential media I/O gains little (no sequential
  bypass in dm-cache/smq).

---


- **Symptom:** `https://cloud.metoda.cc` login failed in the browser —
  `oidc-client-ts` "Connecting to
  'https://auth.metoda.cc/realms/tower/.well-known/openid-configuration'
  violates the following Content Security Policy directive: connect-src 'self'
  blob: …", so the OIDC discovery fetch was refused and sign-in never started.
- **Root cause:** OpenCloud serves a **built-in default CSP** that only allows
  `connect-src 'self'` (plus the app-store/update hosts). An external IdP is a
  different origin, so its discovery/token endpoints must be explicitly allowed.
  The upstream compose deployment ships `config/opencloud/csp.yaml` and sets
  `PROXY_CSP_CONFIG_FILE_LOCATION`; our Deployment set neither, so the default
  was served and `auth.metoda.cc` was not in `connect-src`/`frame-src`.
- **Fix:** new `k8s/apps/opencloud/configmap-csp.yaml` (mirrors the upstream
  `csp.yaml`, trimmed to the services we run, IdP origin from
  `${IDP_DOMAIN|auth.metoda.cc}`); `deployment.yaml` gains
  `PROXY_CSP_CONFIG_FILE_LOCATION=/etc/opencloud/csp.yaml` and mounts the
  ConfigMap key `csp.yaml` over that path.
- **Status:** codified in git. `k8s/` is Flux-synced from `master`, so the
  ConfigMap + Deployment apply on push (a `flux reconcile` / rollout restart
  picks it up). Files: `k8s/apps/opencloud/{configmap-csp,deployment}.yaml`.

## 2026-10-15 — Power-loss recovery: Keenetic WAN moved off .216, blackholing the server segment

- **Symptom:** after a facility power loss everything behind the Keenetic was
  unreachable (tower `.200`, proxmox `.201`, k8s `10.10.120.101-104`), while the
  main router was up — `kubectl` → `no route to host` to the API.
- **Root cause:** the Keenetic server-router WAN is DHCP with **no reservation**,
  so after reboot it came back on `192.168.1.101` (lease hostname `OpenWrt`)
  instead of `192.168.1.216`. The main router's static routes `network.rt_srv` /
  `network.rt_k8s` both point at `.216` → the server segment and VLAN120 were
  blackholed. The Keenetic itself was healthy (`br-lan` = `192.168.30.1`,
  `eth0.120` = `10.10.120.1`, wg0 up); only its WAN address had drifted.
- **Fix:** new `router_openwrt_dhcp_hosts` in the `router_openwrt` role pins the
  Keenetic WAN MAC `e4:18:6b:5a:e1:39` → `192.168.1.216` (dnsmasq `config host`,
  uci section `kcwan`). Applied to the main router (`uci commit dhcp` +
  `dnsmasq reload`); the Keenetic's WAN renewed onto `.216` and the routes
  recovered. Files: `ansible/roles/router_openwrt/{defaults,tasks}/main.yml`.
- **Follow-up:** the Keenetic is still hand-configured (no Ansible role) — either
  keep this reservation or give its WAN a static IP.

## 2026-10-15 — OpenCloud SSO: federate to Keycloak (external OIDC IdP)

- **Change:** OpenCloud (ns `opencloud`) now authenticates against **Keycloak**
  (realm `tower`, issuer `https://auth.metoda.cc`) instead of its built-in IDP.
  `k8s/apps/opencloud/deployment.yaml` gains `OC_EXCLUDE_RUN_SERVICES=idp`,
  `OC_OIDC_ISSUER`/`IDP_DOMAIN`/`IDP_ISSUER_URL`, `PROXY_OIDC_REWRITE_WELLKNOWN`,
  `PROXY_OIDC_ACCESS_TOKEN_VERIFY_METHOD=jwt`, autoprovisioning
  (`PROXY_AUTOPROVISION_ACCOUNTS=true`, `preferred_username` mapping,
  `PROXY_USER_CS3_CLAIM=username`), role assignment via the flat `roles` claim
  (`PROXY_ROLE_ASSIGNMENT_DRIVER=oidc`) and `WEB_OPTION_ACCOUNT_EDIT_LINK_HREF`.
  The built-in **IDM/LDAP is kept** (only `idp` excluded) and stores the
  autoprovisioned users.
- **Keycloak side:** realm roles `opencloudAdmin`/`opencloudSpaceAdmin`/
  `opencloudUser`/`opencloudGuest`; public PKCE clients `web`,
  `OpenCloudDesktop`, `OpenCloudAndroid`, `OpenCloudIOS`; client scope
  `opencloud-roles` emitting a flat `roles` claim. Codified in the
  `keycloak_config` Ansible role (roles/scopes/clients reconciled via the Admin
  API) and in `keycloak/configmap-realm.yaml` for fresh installs. The demo user
  is deliberately **not** given an `opencloud*` role, so it cannot
  autoprovision into OpenCloud.
  `keycloak/deployment.yaml` also gains
  `--spi-connections-http-client-default-disable-trust-manager=true` so the
  backchannel-logout callback to `https://cloud.metoda.cc` (tower-ca) works.
- **Status:** codified in git, **not applied**. `k8s/` is Flux-synced from
  `master` (`git@github.com:iskovskikh/tower_cloud.git`) — the manifests apply on
  push; the realm objects are applied by `ansible-playbook playbooks/keycloak.yml`.
  Files: `k8s/apps/opencloud/deployment.yaml`, `k8s/apps/opencloud/ingress.yaml`
  (comment), `k8s/apps/keycloak/configmap-realm.yaml`,
  `k8s/apps/keycloak/deployment.yaml`, `ansible/roles/keycloak_config/`. See
  `docs/identity/README.md`.

## 2026-10-15 — OpenCloud canonical host `cloud.metoda.cc` (fix public web UI)

- **Symptom:** `https://cloud.metoda.cc` served OpenCloud's web app but the UI
  died on the generic **"Missing or invalid config / config.json"** screen.
- **Root cause:** the deployment's `OC_URL` was `https://opencloud.k8s.lan`, so
  the server baked that *internal* host into `config.json` (`server`),
  `.well-known/openid-configuration` (`issuer`) and `.well-known/webfinger`
  (`href`). The web runtime bootstraps by fetching
  `<server>/.well-known/webfinger`; a **public** browser cannot resolve
  `opencloud.k8s.lan`, so `loadWebfingerDiscoveryData` threw and bootstrap
  rejected → the generic error screen. (The main bundle's error view is a
  catch-all, so the message is not literal.)
- **Change:** `opencloud/deployment.yaml` `OC_URL` → `https://cloud.metoda.cc`
  (init + main container). Split-horizon added to `router_openwrt` dnsmasq
  (`/cloud.metoda.cc/10.10.120.101-104`) so LAN clients resolve the canonical
  name straight to ingress-nginx instead of hairpinning via `adminvps`.
- **Status:** codified in git, **not applied** (needs `rollout restart` of the
  OpenCloud Deployment + router dnsmasq push). Files:
  `k8s/apps/opencloud/deployment.yaml`,
  `ansible/roles/router_openwrt/defaults/main.yml`.
- **Follow-up:** the internal name `opencloud.k8s.lan` still serves the same
  Ingress but now cross-origin bootstraps against `cloud.metoda.cc` — use the
  canonical host (public or LAN split-horizon) going forward.

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
