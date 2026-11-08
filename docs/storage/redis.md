# Redis on tower

A single Redis instance runs **on tower** (Docker, data on `/mnt/raid1/redis`)
and serves the cluster's cache/queue needs over the network. GitLab is the
first consumer (sidekiq queues + Rails cache); Keycloak and other apps can
share it with separate logical databases.

Rationale: [`../adr/0002-stateful-services-on-tower.md`](../adr/0002-stateful-services-on-tower.md).
The GitLab Helm chart 10 no longer ships a bundled Redis, so this is a required
external service.

## Architecture

```
tower 192.168.30.200
 └─ docker container "redis" (redis:7.4-alpine)
      config /etc/redis/redis.conf   (requirepass; rendered by Ansible, 0640 root:1000)
      data   /mnt/raid1/redis        (AOF, everysec; uid/gid 999/1000)
      :6379
            ▲
            │  VLAN120 <-> 192.168.30.0/24 (router forwards 6379)
k8s — GitLab (ns gitlab) connects to redis://:PASSWORD@192.168.30.200:6379/0
```

## Ansible

Enabled by `tower_redis_enabled` in
`ansible/roles/tower_storage/defaults/main.yml`. The password is **never
committed** — it is read from the environment at play time and must match Vault
`secret/gitlab` (property `redis_password`):

```bash
export GITLAB_REDIS_PASSWORD="$(vault kv get -field=redis_password secret/gitlab)"
ansible-playbook ansible/playbooks/site.yml --limit tower
```

## Verify

```bash
ssh tower 'docker exec redis redis-cli -a "$GITLAB_REDIS_PASSWORD" ping'
# from the cluster
kubectl -n gitlab exec deploy/gitlab-sidekiq -- \
  sh -c 'redis-cli -h 192.168.30.200 -a "$GITLAB_REDIS_PASSWORD" ping'  # ❓ cli may be absent
```

## Notes and risks

- **Single instance, no HA** — tower is a SPOF, same as the rest of the PoC.
- **AOF is enabled** so sidekiq jobs survive a Redis restart. This is still a
  cache/queue tier, not a system of record.
- **One shared instance:** use separate Redis logical databases (`/0`, `/1`, …)
  or key prefixes if more apps are added.
- The password is in the container's rendered config (`/etc/redis/redis.conf`,
  mode 0640 root:redis gid 1000), not in `docker inspect` arguments. The
  Ansible role restarts Redis when the rendered config changes.
