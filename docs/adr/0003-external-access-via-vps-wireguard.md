# ADR-0003: External access via a public VPS + WireGuard (no port-forwarding)

- **Status:** Accepted — networking done; Traefik front end **live** (2026-10-15)
- **Date:** 2026-10-14
- **Deciders:** owner + agent

## Context

The home WAN is behind the ISP's **carrier-grade NAT** (main router `eth0.2` =
`100.64.48.134/20`, i.e. `100.64.0.0/10`). There is **no public IPv4** and no
usable inbound port-forward, so "reach my services by hostname from the
internet" cannot be done by simply DNAT-ing `:443` on the home router. The
services to expose (`OpenCloud`, `Keycloak`) run in the k8s cluster, which lives
in an **isolated VLAN 120** (`10.10.120.0/24`) behind ingress-nginx; admin
endpoints (Vault, Proxmox `:8006`, SSH) should stay private.

A second constraint appeared while solving this: the segment switch ran out of
ports, so the server gear (`tower`, `proxmox`) had to be re-homed onto the spare
**Keenetic Omni II** — which turned out to be reflashed to **OpenWrt**, i.e. a
fully programmable router, not a stock KeeneticOS box.

## Decision

1. **Relay external access through a public VPS (`adminvps`, `78.17.99.16`)**
   instead of port-forwarding. The VPS is a **WireGuard server**; the home side
   is the WG **client**. The tunnel is outbound-only from home, so CGNAT is a
   non-issue and no home ports are exposed.

2. **The Keenetic Omni II (OpenWrt 23.05.3) is the router for the server
   segment.** Its LAN is `192.168.30.0/24`; `tower` and `proxmox` hang off it.
   proxmox's **VLAN30**-tagged management is bridged into that segment, and its
   **VLAN120** stays a separate routed network (`10.10.120.1`) so k8s keeps its
   own L2. The device is configured in place (no IaC role yet).

3. **The tunnel carries the home routes.** On `adminvps`, the peer's `AllowedIPs`
   route `192.168.30.0/24` and `10.10.120.0/24` through the tunnel, so `adminvps`
   can reach `tower`, `proxmox`, the k8s nodes, ingress and the API directly.

4. **A reverse proxy on the VPS terminates public TLS** (planned: **Traefik** on
   `:80`/`:443` with Let's Encrypt), proxying named subdomains
   (`cloud.`, `auth.`, …) to the home ingress over the tunnel. Only the intended
   apps are published; Vault/Proxmox/SSH remain VPN-only.

5. **The main router's switch port 3 is repurposed** from VLAN30 (Servers) to
   VLAN1 (LAN) so the Keenetic's WAN gets a subnet (`192.168.1.0/24`) that does
   not collide with the server segment; the Keenetic then does NAT/routing for
   the servers through it. The main router's `br-srv` (VLAN30) is left orphaned.

## Consequences

- **No home ports opened; no public IP needed** — the design survives CGNAT and
  IP changes (the client re-registers with keepalive).
- **`adminvps` becomes a critical dependency** for external access (it is public
  and internet-exposed). It already ran a personal proxy stack, so it must be
  hardened and backed up (its WG config/keys).
- **The Keenetic Omni II is now load-bearing** for the server segment: it is the
  LAN router for `tower`/`proxmox` and the sole tunnel endpoint. It is a weak,
  low-RAM device (~64 MB, tiny overlay) and is **not yet managed by IaC** — a
  gap to close.
- **Double NAT** for server internet traffic (Keenetic → main router → ISP).
  Fine for the current workloads.
- **Two DHCP boundaries changed:** the server segment and VLAN120 are now served
  by the Keenetic; `tower` gets its address from a Keenetic DHCP reservation
  (`.200`). Node reservations may need to move too ❓.
- The main router's `br-srv`/VLAN30 and its port-5 trunk are now unused.
- **Documented split horizon** is needed so the Keycloak issuer hostname resolves
  consistently inside (ingress) and outside (VPS) — a follow-up for the Traefik
  phase.

## Alternatives considered

- **Port-forwarding on the home router** — impossible: CGNAT, no public IPv4.
- **A Cloudflare Tunnel / Tailscale Funnel** — viable (also CGNAT-friendly), but
  rejected in favour of a self-owned public VPS with a WireGuard tunnel, which
  also reuses the existing `adminvps` box.
- **Keep the servers on the main router's VLAN30** — the original plan; dropped
  because the switch ran out of ports and the Keenetic (once online as OpenWrt)
  can be both the extra switch/ports and the server router.
- **Stock KeeneticOS** — rejected: no VLANs, no WireGuard routing, closed; the
  OpenWrt reflash makes the device programmable and VLAN-capable.
