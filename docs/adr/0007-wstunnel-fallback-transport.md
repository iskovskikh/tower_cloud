# ADR-0007: wstunnel (WSS) fallback transport for public ingress

- **Status:** Accepted — applied 2026-11-14
- **Date:** 2026-11-14
- **Related:** ADR-0006 (Xray VLESS Reality + SSH reverse tunnel), ADR-0004/0005 (DNS + TLS)

## Context

Since ADR-0006 the public ingress has exactly one transport:

```
internet -> adminvps Traefik :443 -> Xray VLESS Reality (SNI passthrough)
         -> SSH reverse tunnel -> k8s-node-1 -> ingress-nginx
```

The home provider already DPI-drops WireGuard transport and throttled the Xray
handshake on non-standard ports / the `chrome` ClientHello (CHANGELOG
2026-11-14). If the provider starts dropping VLESS Reality itself, the only
public path dies — there is no second transport, and the home WAN is CGNAT so a
direct inbound path is impossible.

We need a **different protocol signature**, outbound from home, that reuses the
existing VPS/TLS front end and can be switched in without re-certificating or
re-pointing DNS.

## Decision

1. **wstunnel over WebSocket/TLS as the fallback transport.**
   - `k8s-node-1` runs `wstunnel-fallback.service` (`wstunnel client` v11.1.0),
     connecting to `wss://relay.metoda.cc` and keeping two reverse tunnels:
     `127.0.0.1:10444 -> 127.0.0.1:443` (ingress-nginx) and
     `127.0.0.1:12223 -> 127.0.0.1:30222` (GitLab shell) on `adminvps`.
   - `adminvps` runs `wstunnel-server.service` on `127.0.0.1:8080` (plain WS;
     Traefik terminates TLS). Its `--restrict-config` allows **only** those two
     loopback reverse-tunnel binds; the HTTP upgrade-path prefix is a credential
     matched in the restriction file.
   - `relay.metoda.cc` is served by the existing Traefik with a normal
     Let's Encrypt certificate. The `*.metoda.cc` A record already points to
     `adminvps`, so no DNS change is needed. The traffic looks like ordinary
     HTTPS to a subdomain.

2. **Traefik switches backends automatically.**
   - The file provider now watches a directory (`/etc/traefik/conf.d`) instead
     of one file. `routers.yml` is static; `backend.yml` is the live selection.
   - `traefik-ingress-failover.timer` (30 s) probes
     `https://127.0.0.1:10443` (primary) and `https://127.0.0.1:10444`
     (fallback). After two consecutive probes agree, it atomically replaces
     `conf.d/backend.yml` with `backends/primary.yml` or `backends/fallback.yml`;
     Traefik reloads without a restart. The primary wins whenever it answers, so
     the fallback is a standby, not a load-balanced peer.

3. **IaC:** roles `wstunnel_fallback` (server + client) and updates to
   `adminvps_traefik` (directory provider, relay router, failover timer),
   playbook `ansible/playbooks/wstunnel-fallback.yml`. The upgrade-path secret
   is a credential and is passed out-of-band (`~/.wstunnel-vars.yml`), like the
   Xray UUID.

## Consequences

- Public ingress survives a DPI block of VLESS Reality as long as
  `adminvps:443`, `relay.metoda.cc` and the home uplink still work; the switch
  is automatic and the downtime is the probe window plus Traefik's reload
  (tens of seconds).
- The fallback shares `adminvps` (same IP) and the home WAN with the primary, so
  it does **not** cover an IP-level block or a VPS outage. A truly independent
  path needs a second relay/provider (Cloudflare Tunnel, Tailscale Funnel or a
  second VPS); those remain open options.
- Two systemd services now run on `k8s-node-1` (`xray-*` and
  `wstunnel-fallback`), still outside GitOps; the failover timer lives on
  `adminvps`. Alerting on either transport dying is still open.
- The failover timer probes a loopback HTTPS port; a live but broken backend
  (e.g. ingress returning 5xx) is treated as healthy. It detects transport
  death, not application failure.

## Alternatives considered

- **A second Xray inbound / port on the same VPS** — same protocol signature, so
  it does not help if VLESS Reality is fingerprinted.
- **Hysteria/QUIC or a second WireGuard attempt** — UDP transport is throttled
  or DPI-dropped on this uplink; also a second daemon with its own credentials.
- **Cloudflare Tunnel** — genuinely independent vendor/IP, but requires moving
  `metoda.cc` (or a new domain) to Cloudflare and an account; deferred.
- **Tailscale Funnel** — quick and CGNAT-friendly, but a third-party account and
  a `ts.net` hostname or CNAME; deferred.
- **ProxyCommand failover on the primary SSH tunnel** — one tunnel, but it only
  fails over at SSH reconnect time and cannot present a separate backend to
  Traefik; the explicit standby + Traefik switch is observable and reversible.
