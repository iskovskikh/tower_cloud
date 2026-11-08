# GitLab CE (`gitlab.metoda.cc`)

Self-hosted GitLab CE 19.5 (Helm chart 10.5.4) in namespace `gitlab`, managed
by the Flux-synced manifests under [`k8s/apps/gitlab/`](../../k8s/apps/gitlab/).

## Architecture

```
browser ──https──> adminvps Traefik (Let's Encrypt)
                     │  wg0, re-encrypt
                     ▼
                 ingress-nginx (tower-ca)
                     │
                     ▼
        gitlab-webservice / sidekiq / gitaly / shell
             │                │              │
             │                │              └─ iSCSI LUN 6 (repositories)
             │                └─ object storage -> Garage S3 on tower
             └─ PostgreSQL 18.6 + Redis 7 on tower
```

State follows [ADR-0002](../adr/0002-stateful-services-on-tower.md): databases,
cache and object storage live on **tower**, not on a k8s block device. Only
Gitaly (repository data) keeps a dedicated RWO iSCSI LUN, pinned by a
`matchLabels` selector (`tower-iscsi-gitlab-gitaly`, lun 6).

Components deliberately **not** deployed: bundled cert-manager,
nginx-ingress, Prometheus, Container Registry, KAS, Pages, GitLab Runner and the
chart's Envoy Gateway. The cluster reuses the existing ingress-nginx,
cert-manager/tower-ca and external services. SSH clone is **not** exposed; use
HTTPS clone URLs.

## Prerequisites (one-time, out of band)

1. **Vault** `secret/gitlab` must contain:

   ```bash
   vault kv put secret/gitlab \
     postgres_password=... \
     redis_password=... \
     initial_root_password=... \
     oidc_client_secret=... \
     s3_access_key=... \
     s3_secret_key=...
   ```

   `s3_access_key`/`s3_secret_key` can be generated with
   `openssl rand -hex 16` / `openssl rand -hex 32`. The same values are passed
   to Ansible below and imported into Garage.

2. **Tower** (Postgres database, Redis, Garage buckets, Gitaly LUN):

   ```bash
   export POSTGRES_PASSWORD="$(vault kv get -field=db_password secret/keycloak)"
   export GITLAB_POSTGRES_PASSWORD="$(vault kv get -field=postgres_password secret/gitlab)"
   export GITLAB_REDIS_PASSWORD="$(vault kv get -field=redis_password secret/gitlab)"
   export GARAGE_RPC_SECRET="$(vault kv get -field=rpc_secret secret/garage)"
   export GARAGE_ADMIN_TOKEN="$(vault kv get -field=admin_token secret/garage)"
   export GITLAB_S3_ACCESS_KEY="$(vault kv get -field=s3_access_key secret/gitlab)"
   export GITLAB_S3_SECRET_KEY="$(vault kv get -field=s3_secret_key secret/gitlab)"

   ansible-playbook ansible/playbooks/site.yml --limit tower
   ```

   This creates the `gitlab` Postgres role/database, the Redis container, the
   four Garage buckets (`gitlab-artifacts`, `git-lfs`, `gitlab-uploads`,
   `gitlab-packages`) and the `gitlab-gitaly` iSCSI LUN (lun 6).

3. **DNS:** `tofu/dns` adds `gitlab.metoda.cc` -> `78.17.99.16`:

   ```bash
   TF_VAR_twc_token="$TWC_TOKEN" tofu -chdir=tofu/dns plan
   TF_VAR_twc_token="$TWC_TOKEN" tofu -chdir=tofu/dns apply
   ```

4. **Public front:** `ansible/roles/adminvps_traefik/defaults/main.yml` lists
   `gitlab.metoda.cc`; apply it:

   ```bash
   ansible-playbook ansible/playbooks/adminvps.yml
   ```

5. **LAN split-horizon:** `ansible/roles/router_openwrt` answers
   `gitlab.metoda.cc` with the four ingress node IPs:

   ```bash
   ansible-playbook ansible/playbooks/site.yml --limit router
   ```

## Deploy order

1. Apply `k8s/apps/gitlab/` (namespace first, then the ExternalSecrets, then
   the HelmRelease). **Flux only owns it after the commit reaches `master`;**
   the initial deployment was applied manually with `kubectl`. The
   ExternalSecrets must sync **before** the HelmRelease can start:

   ```bash
   kubectl -n gitlab get externalsecret
   kubectl -n gitlab get secret gitlab-secrets gitlab-object-storage gitlab-oidc
   ```

2. Reconcile the Keycloak client (reads `keycloak-gitlab-oidc` from the
   keycloak namespace):

   ```bash
   ansible-playbook ansible/playbooks/keycloak.yml
   ```

3. Watch the release. First install runs migrations and takes several minutes:

   ```bash
   flux -n gitlab get helmrelease
   kubectl -n gitlab get pods -w
   ```

## Verify

```bash
dig +short gitlab.metoda.cc @1.1.1.1          # adminvps
dig +short gitlab.metoda.cc @192.168.1.1      # ingress nodes (LAN)
curl -I https://gitlab.metoda.cc               # 200/302, LE cert publicly
kubectl -n gitlab get pvc                      # repo-data-gitlab-gitaly-0 Bound
```

Then open `https://gitlab.metoda.cc`, log in via Keycloak, and create a test
project. Local accounts also work with the `initial-root-password` from Vault.

## Resource notes

The cluster is small (4 VMs, ~13 GiB allocatable, several apps already running).
GitLab is the heaviest workload in it. The HelmRelease asks for roughly:

| Component | request | limit |
|---|---|---|
| webservice | 300m / 1 GiB | 2 GiB |
| sidekiq | 500m / 1 GiB | 1.5 GiB |
| gitaly | 100m / 512 MiB | 1 GiB |
| toolbox | 50m / 350 MiB | 1 GiB |
| migrations | 250m / 200 MiB | 1 GiB |

If pods stay `Pending`, the nodes cannot satisfy the requests: lower sidekiq
`concurrency`, or add memory to the VMs (proxmox) rather than removing the
limits. Watch `kubectl top`/`dmesg` for OOM kills on first boot.

## First-boot gotchas (hit on 2026-11-08)

- **Node disks filled up.** GitLab CNG images are large; on 22 GiB nodes the
  kubelet entered `DiskPressure` and evicted pods (including ingress-nginx).
  The four VMs are now **40 GiB** (proxmox `qm resize <vmid> scsi0 40G`,
  then `growpart /dev/sda 1` + `resize2fs /dev/sda1` in each guest). This was a
  manual stopgap; the disk sizes are recorded in `docs/network/inventory.yaml`.
- **PostgreSQL out of shared memory.** `db:schema:load` needs more lock slots:
  the tower Postgres container now runs
  `postgres -c max_locks_per_transaction=256` (role default).
- **Deployment progress deadline.** The webservice/sidekiq `dependencies` init
  waits for the migrations Job; the default 600 s `progressDeadlineSeconds`
  made Helm treat the release as stalled and uninstall it. The HelmRelease
  post-renderer sets the deadline to 3600 s, and webservice uses
  `strategy: Recreate` so upgrades do not need a second 1 GiB surge pod.

## Follow-ups

- Enable the Container Registry (Garage bucket + `registry.metoda.cc`) if image
  hosting is needed; it is currently disabled.
- Add a GitLab Runner (separate Helm release) when CI is needed.
- Consider offloading GitLab backups to Garage (`gitlab.toolbox.backups`).
- GitLab SSH clone would need a TCP entry point (ingress-nginx TCP config or a
  Traefik TCP router); currently HTTPS-only.
