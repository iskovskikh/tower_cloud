# Network change log / observations

One entry per recon session or infrastructure change. Newest first.

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
