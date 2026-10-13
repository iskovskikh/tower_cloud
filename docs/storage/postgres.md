# Postgres on tower

A single Postgres instance runs **on tower** (Docker, data on `/mnt/raid1/postgres`)
and serves the cluster's applications over the network. There is no iSCSI LUN,
PV or PVC involved. Keycloak is the first consumer; other apps can share it.

Rationale: [`../adr/0002-stateful-services-on-tower.md`](../adr/0002-stateful-services-on-tower.md).

## Architecture

```
tower 192.168.30.200
 └─ docker container "postgres" (postgres:18.6)
      data  /mnt/raid1/postgres   (ext4 on md0, uid/gid 999)
      :5432                        db "keycloak", user "keycloak"
            ▲
            │  VLAN120 <-> 192.168.30.0/24 (router forwards 5432)
k8s — Keycloak (ns keycloak) connects to jdbc:postgresql://192.168.30.200:5432/keycloak
```

## Ansible

Enabled by `tower_postgres_enabled` in
`ansible/roles/tower_storage/defaults/main.yml`. The password is **never
committed** — it is read from the environment at play time and must match Vault
`secret/keycloak` (property `db_password`, the same value Keycloak already uses):

```bash
export POSTGRES_PASSWORD="$(vault kv get -field=db_password secret/keycloak)"
ansible-playbook ansible/playbooks/site.yml --limit tower:router
```

## Backup

The database is a plain directory on the array, but prefer logical dumps:

```bash
ssh tower 'docker exec postgres pg_dump -U keycloak -d keycloak -Fc' > keycloak-$(date +%F).dump
```

## Migration: k8s Postgres (block LUN) → Postgres on tower

Order matters — the k8s instance is the source and must stay up until the copy
is verified. Keycloak loses its database for the duration, so it is a short
downtime window.

1. **Bring up Postgres on tower** and open the port on the router:

   ```bash
   export POSTGRES_PASSWORD="$(vault kv get -field=db_password secret/keycloak)"
   ansible-playbook ansible/playbooks/site.yml --limit tower:router
   ssh tower 'docker exec postgres pg_isready -U keycloak'
   ```

2. **Stop Keycloak** (freeze writes) and dump the k8s database:

   ```bash
   kubectl -n keycloak scale deploy/keycloak --replicas=0
   kubectl -n keycloak exec statefulset/postgres -- \
     pg_dump -U keycloak -d keycloak -Fc > /tmp/keycloak.dump
   ```

3. **Restore into the tower instance** (pipe the dump over ssh; the image
   already created an empty `keycloak` database):

   ```bash
   ssh tower 'docker exec -i postgres pg_restore -U keycloak -d keycloak \
     --clean --if-exists --no-owner' < /tmp/keycloak.dump
   # sanity: table count / realm rows
   ssh tower "docker exec postgres psql -U keycloak -d keycloak -c '\dt'"
   ```

4. **Cut over.** Push the committed k8s changes: Flux prunes the k8s
   `postgres` StatefulSet and its LUN-backed PV, and rolls Keycloak with
   `KC_DB_URL=jdbc:postgresql://192.168.30.200:5432/keycloak`. Then scale
   Keycloak back if needed and verify a full login through the UI.

5. **Prune the retired LUN** (`postgres`, lun 3) on tower — see
   `static-iscsi.md` → "Retire a block volume" (vol1 and garage can go in the
   same pass).

## Notes and risks

- **Single instance, no HA** — tower is a SPOF, same as the rest of the PoC.
- **Network database:** the router is in the path for every query
  (`vlan120` → `srv`, port 5432). Fine on a LAN; keep an eye on latency.
- **One shared instance:** schemas/roles are not isolated per app — use separate
  databases and least-privilege roles if more apps are added.
