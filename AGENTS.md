# AGENTS.md

Guidance for AI agents working in this repository (`tower_cloud` — the home/local
network and Kubernetes infrastructure, managed as **Infrastructure as Code**).

## Read this first

1. [`docs/network/README.md`](docs/network/README.md) — topology, subnets, access.
2. [`docs/network/inventory.yaml`](docs/network/inventory.yaml) — **source of truth**
   (hosts, IPs, services, status). Trust this over assumptions.
3. [`docs/network/CHANGELOG.md`](docs/network/CHANGELOG.md) — history & anomalies.
4. [`docs/adr/`](docs/adr/) — architecture decisions (the "why").
5. [`docs/storage/plan.md`](docs/storage/plan.md) — tower storage plan.

## The environment in one paragraph

One OpenWrt router (`192.168.1.1`) bridges the LAN `192.168.1.0/24` and **routes**
a server subnet `192.168.30.0/24`. Servers: `tower` (`192.168.30.200` — storage
appliance; data array `/mnt/raid1` = native mdadm RAID1 `md0`; will serve
NFS/iSCSI/S3 to the cluster) and `proxmox` (`192.168.30.201` — Proxmox VE,
hosts the k8s VMs). The Kubernetes cluster `k8s-node-1..4`
(`10.10.120.101-104`, VLAN 120, Ubuntu 22.04) is being **rebuilt** from scratch
(kubeadm, currently down). `archasus` (`192.168.1.204`) is the docker host running
these agents. The **Keenetic Omni 2** router is an offline spare.

## Repository layout

| Path | Purpose |
|---|---|
| `docs/` | topology, inventory, changelog, ADRs |
| `tofu/` | provisioning — OpenTofu (`proxmox` VMs, `dns` for metoda.cc) |
| `ansible/` | host config — tower storage, router, k8s install |
| `k8s/` | GitOps (Flux) — storage, secrets (Vault+ESO), apps |
| `scripts/` | helper scripts (`recon.sh`, …) |

## Rules

- **IaC is the source of truth.** Change infrastructure by editing code here and
  applying it (`tofu` / `ansible-playbook` / `flux`), not by hand on hosts.
  Hand-fixes are stopgaps — reflect them back into code.
- **Verify reality before changing it.** Run `scripts/recon.sh`; trust
  `inventory.yaml` over assumptions.
- **Get explicit approval before applying** changes to hosts or the cluster
  (`tofu apply`, `ansible-playbook`, `flux`, `kubectl`) — this is home prod.
- **Keep `inventory.yaml` and `docs/network/CHANGELOG.md` in sync** with reality.
- **Never commit secrets.** Values live in Vault; commit references/paths only.
- **Mark uncertainty.** Use ❓ / "unconfirmed" rather than guessing.
- Prefer stable identifiers (`id: tower`) and CIDRs over ad-hoc IP strings.

## Handy commands

```bash
scripts/recon.sh                              # read-only network report
scripts/recon.sh --host 192.168.30.200        # scan one host
ssh root@192.168.1.1                          # router (OpenWrt)
ssh tower                                     # tower.lan → 192.168.30.200
ssh arch@192.168.30.201                       # proxmox

tofu -chdir=tofu/proxmox plan                 # provisioning (needs API token)
TF_VAR_twc_token="$TWC_TOKEN" tofu -chdir=tofu/dns plan   # metoda.cc DNS (ADR-0005)
ansible-playbook ansible/playbooks/site.yml   # configure hosts
flux reconcile kustomization flux-system      # GitOps (once bootstrapped)
```

Tooling note: `nmap`/`arp-scan` are **not installed** on `archasus`; `recon.sh`
uses bash TCP and ping instead. `curl`, `ssh`, `showmount`, `rpcinfo` are available.
