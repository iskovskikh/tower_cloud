# Garage S3 object storage on tower

Object storage does **not** need a block device. Garage runs as a Docker
container **on tower**, with its data directory directly on the data array
(`/mnt/raid1/garage`), and clients talk to it over the S3 API. There is no
iSCSI LUN, no PersistentVolume and no PVC involved.

This replaces the earlier design where Garage ran in k8s on a dedicated 50 GiB
tower LUN (`tower-iscsi-garage`, lun 2). That LUN is now **retired** — see
"Reclaiming lun 2" below.

## Architecture

```
tower 192.168.30.200
 └─ docker container "garage" (host network, dxflrs/garage:v2.4.1)
      config /etc/garage/garage.toml   (rendered by Ansible, mode 0600)
      data   /mnt/raid1/garage/{meta,data}   (ext4 on md0)
      :3900 S3 API  ·  :3901 RPC  ·  :3902 web  ·  :3903 admin
            ▲
            │  VLAN120 <-> 192.168.30.0/24 (router forwards 3900)
k8s nodes (and any LAN client) — S3 endpoint http://192.168.30.200:3900
```

Single node with `server --single-node`: Garage configures its cluster layout
itself, so there is no imperative `layout assign`/`layout apply` step.

## Ansible

Enabled by `tower_garage_enabled` in `ansible/roles/tower_storage/defaults/main.yml`.
`garage.toml` is rendered from `templates/garage.toml.j2`; the secrets
(`rpc_secret`, `admin_token`) are **never committed** — they are read from the
environment at play time and should match Vault `secret/garage`:

```bash
export GARAGE_RPC_SECRET="$(vault kv get -field=rpc_secret  secret/garage)"
export GARAGE_ADMIN_TOKEN="$(vault kv get -field=admin_token secret/garage)"
ansible-playbook ansible/playbooks/site.yml --limit tower:router
```

## Managing buckets and keys

Run the CLI inside the container:

```bash
ssh tower sudo docker exec garage /garage status
ssh tower sudo docker exec garage /garage bucket create <bucket>
ssh tower sudo docker exec garage /garage bucket allow --read --write --owner <bucket> --key <access_key>
ssh tower sudo docker exec garage /garage key list
```

## Migration: k8s Garage (block LUN) → Garage on tower

Order matters — the old in-cluster Garage is the migration **source** and must
stay up until the copy is verified.

1. **Bring up the new Garage** (empty) and open the port on the router:

   ```bash
   export GARAGE_RPC_SECRET="$(vault kv get -field=rpc_secret  secret/garage)"
   export GARAGE_ADMIN_TOKEN="$(vault kv get -field=admin_token secret/garage)"
   ansible-playbook ansible/playbooks/site.yml --limit tower:router
   ssh tower sudo docker exec garage /garage status   # node healthy, single node
   ```

2. **Create the bucket and re-import the existing key** so OpenCloud's stored
   credentials keep working (no Vault change needed):

   ```bash
   OLD_AK="$(vault kv get -field=s3_access_key secret/opencloud)"
   OLD_SK="$(vault kv get -field=s3_secret_key secret/opencloud)"
   ssh tower sudo docker exec garage /garage bucket create opencloud
   ssh tower sudo docker exec garage /garage key import "$OLD_AK" "$OLD_SK" -n opencloud
   ssh tower sudo docker exec garage /garage bucket allow --read --write --owner opencloud --key "$OLD_AK"
   ```

3. **Copy the objects** with a temporary rclone pod (it reaches both the old
   ClusterIP and tower's `3900`):

   ```bash
   kubectl -n garage run rclone-migrate --rm -it --restart=Never \
     --image=rclone/rclone:latest -- sh
   # inside the pod — substitute the two Vault values for <OLD_AK>/<OLD_SK>
   # (❓ confirm the provider/force_path_style flags for Garage):
   rclone config create old s3 provider=Other \
     endpoint=http://garage.garage.svc.cluster.local:3900 \
     access_key_id=<OLD_AK> secret_access_key=<OLD_SK> region=garage
   rclone config create new s3 provider=Other \
     endpoint=http://192.168.30.200:3900 \
     access_key_id=<OLD_AK> secret_access_key=<OLD_SK> region=garage
   rclone copy  old:opencloud new:opencloud --progress
   rclone check old:opencloud new:opencloud
   ```

4. **Cut over.** Push the committed k8s changes: Flux prunes
   `k8s/apps/storage/garage/` (removing the old instance) and rolls OpenCloud
   with `STORAGE_USERS_DECOMPOSEDS3_ENDPOINT=http://192.168.30.200:3900`.
   Verify an upload/download round-trip through the OpenCloud UI.

5. **Reclaiming lun 2** is a deliberate follow-up, not part of the cutover:
   `tower_iscsi_volumes` is positional (the LUN index is the list position), so
   deleting the `garage` entry would renumber `postgres`/`jellyfin`/`opencloud`
   and break their PVs. A clean reclaim means recreating all following LUNs and
   re-pointing their PVs/LUN numbers.

## Notes and risks

- **Single node, no replication** (`replication_factor = 1`): tower is a SPOF,
  same as the rest of the PoC. Multi-node Garage is a later option.
- **ext4 inode limit.** Garage's own guidance recommends **XFS** for `data_dir`
  (ext4 can exhaust inodes with very many small objects). Here the data lives on
  the array's ext4 for now; revisit if object counts grow. `metadata_dir` ideally
  belongs on an SSD (here it shares the HDD).
- **Thin by nature**: the backing filesystem only consumes array space as data
  is written, so there is no fixed cap to hit — but if `/mnt/raid1` fills, writes
  fail (`ENOSPC`). Monitor free space (Marmonitor/`df`).
- Secrets come from the environment at play time; they are not stored in git.
