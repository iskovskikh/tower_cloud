# ADR-0004: DNS + TLS for `metoda.cc` (manual zone, HTTP-01)

- **Status:** Superseded by ADR-0005 (2026-10-19) — the domain moved to Timeweb Cloud, the API now sees the zone, and records are codified.
- **Date:** 2026-10-15
- **Deciders:** owner + agent

## Context

External access runs through the public relay `adminvps` (`78.17.99.16`) over a
WireGuard tunnel (ADR-0003). The domain **`metoda.cc`** is registered at
**Timeweb** and its zone is delegated to Timeweb's nameservers
(`ns1/2.timeweb.ru`, `ns3/4.timeweb.org`). We need `cloud.metoda.cc` (OpenCloud)
and `auth.metoda.cc` (Keycloak) to resolve to `adminvps`, plus publicly-trusted
TLS for them.

The IaC-preferred path was to manage the zone with the official **Timeweb Cloud**
Terraform provider and to use the lego provider `timewebcloud` for DNS-01.
Investigation showed this does **not** work for this domain:

- The zone is managed in the **hosting panel** (`hosting.timeweb.ru/domains`),
  not in Timeweb Cloud.
- The Timeweb Cloud API does not see it: `GET /api/v1/domains` returns
  `{"total":0}` and `GET /api/v1/domains/metoda.cc` → `404 not_found`.
- lego has no provider for the Timeweb hosting API, so DNS-01 cannot be
  automated against this zone as-is. (The Timeweb Cloud account also reports
  `is_blocked: true` ❓.)

## Decision

1. **Manage the `metoda.cc` zone manually** in the hosting panel
   (`hosting.timeweb.ru/domains`). Add `A` records `cloud`, `auth` and `*` →
   `78.17.99.16` (`adminvps`); the apex `@` and `www` were moved there too,
   replacing the registrar parking `A` (`92.53.96.201`).

2. **Issue TLS via the Let's Encrypt HTTP-01 challenge** (Traefik on `adminvps`,
   port `:80`). No DNS provider/API is needed.

This is a deliberate, documented **exception to the "everything as code" rule
(ADR-0001)**: the zone changes very rarely (a handful of `A` records),
`docs/network/inventory.yaml` remains the record of truth, and no suitable API
exists for the panel that hosts the zone.

## Consequences

- **Records are set by hand**, so drift is possible; keep `inventory.yaml`
  (`domains:`) in sync after any change. There is no `tofu`/`flux` resource for
  DNS (an earlier `tofu/dns` module was written and then removed).
- **`adminvps` must keep `:80` open** for HTTP-01 challenges (and redirect to
  `:443` afterwards). Renewals depend on it.
- **No wildcard certificate.** Traefik will obtain individual certificates per
  host (`cloud.`, `auth.`, …). The `*.metoda.cc` `A` record is kept only for
  routing convenience; an unknown subdomain over HTTPS falls back to Traefik's
  default (untrusted) certificate.
- If the zone is later **moved to Timeweb Cloud** (or another DNS host with a
  Terraform + lego provider), this ADR should be revisited: records could then
  be codified and DNS-01 enabled (wildcard certs).

## Alternatives considered

- **Timeweb Cloud provider + DNS-01** — rejected *for now*: the API does not see
  the zone (`total:0`/`404`); would require moving the domain into a Timeweb
  Cloud project first.
- **acme-dns** (delegate `_acme-challenge.metoda.cc` via CNAME to a small
  service on `adminvps`) — keeps DNS-01 without the Timeweb API, but adds a
  service to run and secure; unnecessary since `adminvps:80` is already public.
- **Self-hosted authoritative DNS** — rejected: a second public SPOF for a zone
  that changes rarely.
- **Do nothing / parking** — rejected: the front end (Traefik) needs working
  names and certificates.

## Superseded (2026-10-19)

The "if the zone is later moved to Timeweb Cloud" condition happened: the domain
was transferred to Timeweb Cloud and `GET /api/v1/domains` now returns it. The
manual-zone exception no longer applies; DNS is managed as code in `tofu/dns`.
TLS remains HTTP-01 for now. See **ADR-0005**.
