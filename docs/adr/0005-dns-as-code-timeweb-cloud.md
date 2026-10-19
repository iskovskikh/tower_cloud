# ADR-0005: DNS as code for `metoda.cc` on Timeweb Cloud

- **Status:** Accepted
- **Date:** 2026-10-19
- **Supersedes:** ADR-0004 (manual zone; the Cloud API did not see the domain)
- **Deciders:** owner + agent

## Context

`metoda.cc` was moved into **Timeweb Cloud**. The Cloud API now sees the domain:
`GET /api/v1/domains` returns it (zone id `25051187-metoda.cc`, `provider: PDR`,
`paid` until `2027-10-15`, auto-renew enabled), and
`GET /api/v1/domains/metoda.cc/dns-records` works.

The transfer was incomplete: the old hosting-panel `A` records were gone. Only
the default Timeweb records remained (two MX, one SPF TXT, plus two empty
leftovers). Public resolvers returned non-existent answers for the apex, `www`,
`cloud`, `auth` and the wildcard, so external access was broken even though the
`adminvps` front end and its certificates were healthy. The router's
split-horizon DNS (`cloud`/`auth` → ingress node IPs) masked the problem on the
LAN.

The official Terraform provider (`tf.timeweb.cloud/timeweb-cloud/timeweb-cloud`)
supports the zone through `data.twc_dns_zone` and `twc_dns_rr`, so the previous
IaC exception can be removed.

## Decision

1. Manage the `metoda.cc` zone **as code** in [`tofu/dns`](../../tofu/dns):
   five `A` records (`@`, `www`, `cloud`, `auth`, `*`) →
   `78.17.99.16` (`adminvps`).
2. Leave the Timeweb default **MX** (`mx1/mx2.timeweb.ru`) and **SPF** records
   unmanaged; they are not part of external access.
3. Keep TLS on **HTTP-01** for now. DNS-01 + a wildcard certificate became
   possible, but is a separate change (Traefik configuration and cert policy).
4. The API token is supplied via `TF_VAR_twc_token` (or `TWC_TOKEN`), never
   committed. State is local and git-ignored, like the other `tofu/` stacks.

## Consequences

- `tofu -chdir=tofu/dns plan` is the drift check; applied records are verified
  publicly with `dig @1.1.1.1`/`@8.8.8.8`.
- The previous `hosting.timeweb.ru/domains` panel is no longer the source of
  truth. `docs/network/inventory.yaml` records the intended state.
- Timeweb propagates changes across its four nameservers over a few minutes; a
  serial bump is not immediate.
- API subdomain creation auto-adds a default SPF TXT for the subdomain. Those
  records were removed as noise; if they reappear, decide whether to manage or
  ignore them explicitly.
- The account status endpoint still reports `is_blocked: true`, yet domain/DNS
  writes succeed. Re-check this if API calls start failing.

## Alternatives considered

- **Keep managing the zone by hand** — rejected: the API now supports it and the
  transfer proved manual state drifts silently.
- **Import the exchange into another DNS provider** — unnecessary; the
  delegation is already at Timeweb and the provider is supported.
- **Switch to DNS-01 immediately** — deferred: not needed to restore service;
  HTTP-01 certificates are valid until 2027-01-13 and renew automatically.
