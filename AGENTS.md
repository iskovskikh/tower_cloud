# AGENTS.md

Guidance for AI agents working in this repository (`tower_cloud` — development of
the home/local network infrastructure).

## Read this first

Before doing anything network-related, load the current picture:

1. [`docs/network/README.md`](docs/network/README.md) — topology, subnets, access.
2. [`docs/network/inventory.yaml`](docs/network/inventory.yaml) — **source of truth**
   (hosts, IPs, services, status). Trust this over assumptions.
3. [`docs/network/CHANGELOG.md`](docs/network/CHANGELOG.md) — history & anomalies.

## The environment in one paragraph

There is one OpenWrt router (`192.168.1.1`) that bridges the LAN subnet
`192.168.1.0/24` and **routes** a separate server subnet `192.168.30.0/24`.
The two servers are `tower` (`192.168.30.200`: NAS/media/object-storage:
Jellyfin, MinIO, SMB/NFS, Transmission) and `proxmox` (`192.168.30.201`:
Proxmox VE hypervisor). The workstation `archasus` (`192.168.1.204`) is the
docker host running these agents. A second router (**Keenetic Omni 2**) exists
but is offline.

## Rules

- **Verify reality before changing it.** Run `scripts/recon.sh` to refresh facts.
- **Read-only by default.** This repo currently does reconnaissance only. Do not
  change router/server configuration without explicit user approval.
- **Keep `inventory.yaml` in sync** whenever the network changes, and add an
  entry to `docs/network/CHANGELOG.md`. Record the date and how it was observed.
- **Never commit secrets** (passwords, keys, tokens, client configs). Reference
  credentials by name and keep the real values in a password manager.
- **Mark uncertainty.** Use ❓ / "unconfirmed" rather than guessing. Open questions
  live under `open_questions:` in `inventory.yaml`.
- Prefer stable identifiers (`id: tower`) and CIDRs over ad-hoc IP strings.

## Handy commands

```bash
scripts/recon.sh                              # read-only network report
scripts/recon.sh --host 192.168.30.200        # scan one host
ssh root@192.168.1.1                          # router (OpenWrt)
ssh tower                                     # tower.lan → 192.168.30.200
ssh arch@192.168.30.201                       # proxmox
```

Tooling note: `nmap`/`arp-scan` are **not installed** on `archasus`; `recon.sh`
uses bash TCP and ping instead. `curl`, `ssh`, `showmount`, `rpcinfo` are available.
