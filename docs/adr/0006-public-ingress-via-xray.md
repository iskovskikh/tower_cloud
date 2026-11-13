# ADR-0006: Public ingress via Xray VLESS Reality + SSH reverse tunnel (WireGuard retired)

- **Status:** Accepted — applied 2026-11-13; WireGuard fully removed 2026-11-14
- **Date:** 2026-11-14
- **Supersedes:** ADR-0003 (public VPS + WireGuard)
- **Related:** ADR-0004 (DNS + TLS for `metoda.cc`)
- **Deciders:** owner + agent

## Context

ADR-0003 built external access as a WireGuard tunnel from the home Keenetic
Omni II to the public VPS `adminvps`, with Traefik on the VPS proxying public
TLS traffic to ingress-nginx over the tunnel. On 2026-11-13 the home provider
began **DPI-dropping WireGuard transport packets**: the handshake still
succeeded, but no data crossed the tunnel (verified from both ends — the VPS
sent ICMP/TCP that never arrived, and the Keenetic's receive counter stayed
flat while its send counter grew). Restarting the interface did not help, and
a watchdog that bounced `wg0` only churned the interface.

The WAN is CGNAT, so port-forwarding remains impossible; external access still
needs an outbound-only path from home.

## Decision

1. **Retire WireGuard.** `wg0` and its keys were removed from the Keenetic and
   `adminvps`, the `wireguard` packages were purged, and the `51820/udp` plus
   WireGuard-only UFW exceptions were deleted. The `10.200.0.0/24` subnet no
   longer exists.

2. **Public ingress runs over Xray VLESS Reality + an SSH reverse tunnel.**
   - `k8s-node-1` runs `xray-client` (VLESS Reality client, SOCKS on
     `10.10.120.101:1081`) towards the existing x-ui inbound, reached on
     `adminvps:443` through Traefik (see item 5).
   - `xray-reverse-tunnel` keeps an SSH connection to `adminvps` **through that
     SOCKS proxy** and binds `127.0.0.1:10443 → 127.0.0.1:443` (ingress-nginx)
     and `127.0.0.1:12222 → 127.0.0.1:30222` (GitLab shell) on the VPS.
   - Traefik on `adminvps` keeps terminating public TLS (Let's Encrypt HTTP-01)
     and proxies to those loopback ports.
   - The path is outbound from home, so CGNAT is still a non-issue and no home
     port is exposed.

3. **Admin/control ports stay closed.** `danted` (:1080), the x-ui panel
   (:12267) and its subscription endpoint (:2096) no longer have a public or
   WireGuard-network allow rule; they are reachable only through an SSH tunnel
   (`ssh -L`).

4. **The transport rides public :443.** The home provider stalls TLS on
   non-standard ports and stalls the large post-quantum ClientHello sent by the
   `chrome` fingerprint. The client therefore uses the `firefox` fingerprint
   (single-segment ClientHello), and Traefik peeks the TLS SNI: the camouflage
   name (`www.oracle.com`) is passed through untouched to the x-ui Reality
   inbound on `127.0.0.1:36243`, while every other SNI is terminated normally.
   Direct `:36243` remains as a fallback.
5. **IaC:** `ansible/roles/xray_tunnel` + `playbooks/xray-tunnel.yml` deploy the
   two node-1 services; `adminvps_traefik` backends are the loopback ports and
   hold the SNI-passthrough router; `adminvps_hardening` deletes the retired
   WireGuard rules.

## Consequences

- Public ingress survives CGNAT and provider IP changes, without exposing home
  ports.
- `adminvps` and `k8s-node-1` are critical: Traefik depends on the SSH
  reverse forwards, and the VLESS UUID/key material is a credential (kept out
  of git, passed out-of-band).
- **Hermes' Telegram egress moved.** It used `socks5://10.200.0.1:1080`
  (danted over WireGuard). That path is gone; `TELEGRAM_PROXY` now points at
  the Xray SOCKS client on `k8s-node-1` (`10.10.120.101:1081`, VLAN120), which
  binds the node address for in-cluster egress. Verified stable (12/12) after
  the fingerprint change.
- The Xray/SSH services are hand-installed systemd units on node-1, not GitOps
  yet; moving them into the cluster and alerting on their death remain open.
- The `10.200.0.0/24` WireGuard subnet is gone; `danted` no longer listens on
  a tunnel address.

## Alternatives considered

- **Keep WireGuard and fight the DPI** (obfuscation, port changes, watchdog) —
  rejected: the drop is on the transport, not a stale NAT/conntrack entry, so
  interface tricks cannot fix it.
- **Cloudflare Tunnel / Tailscale Funnel** — viable CGNAT-friendly options,
  but the self-owned VPS and its existing Xray/x-ui stack were already in
  place.
- **SSH reverse tunnel directly to the VPS** — rejected on its own because the
  provider also disrupts long-lived SSH-like flows; tunneling SSH through the
  VLESS Reality proxy keeps one consistent, working transport.
