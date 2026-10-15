# dm-cache: Optane SSD cache for the data array

An **Intel Optane Memory M10 (16 GB, `MEMPEK1J016GAL`)** sits in tower
(`/dev/nvme0n1`, ~13.4 GiB usable) and caches the data array `/dev/md0`
(mdadm RAID1, 3.6 TB — `/mnt/raid1`) through the kernel **device-mapper
`cache`** target. The array itself is never modified: the same ext4 is only
reached through the dm device, so `/mnt/raid1` now mounts
`/dev/mapper/raid1cache` instead of `/dev/md0`.

The cache is in **writethrough** mode: reads can be served from the Optane, but
every write still lands on the array, so if the Optane dies the ext4 is intact
and `/mnt/raid1` can be mounted straight from `/dev/md0`.

## Why not bcache

`bcache` writes its superblock at an 8 KiB offset and requires the backing
device to be (re)formatted around it — on a live 2.4 TB ext4 with no backup that
is not an option. `dm-cache` maps the existing device in place and keeps its
metadata on the Optane, so the origin's data is untouched.

## Architecture

```
tower 192.168.30.200
 /dev/nvme0n1 (Intel Optane, ~13.4 GiB)
   ├─ nvme0n1p1  1 GiB    dm-cache metadata
   └─ nvme0n1p2  ~12 GiB  dm-cache data (cache blocks, 64 KiB)
        │
        ▼
 /dev/mapper/raid1cache ── dm-cache (writethrough) ── /dev/md0 (RAID1, sdb+sdc)
        │                                                     │
        └────────── /mnt/raid1 (ext4, unchanged) ◄────────────┘  (origin)
```

- Cache block size **128 sectors = 64 KiB** (`tower_cache_block_sectors`).
- Policy **`smq`**, feature **`writethrough`**.
- The dm-cache metadata device is auto-formatted on first activation.

## Ansible

`tower_cache_enabled` in `ansible/roles/tower_storage/defaults/main.yml`
(default **false**). The tasks live in `tasks/dmcache.yml`; the bring-up script
(`templates/tower-cache-start.sh.j2`) and the boot unit
(`templates/tower-cache.service.j2`) are installed to
`/usr/local/sbin/tower-cache-start.sh` and
`/etc/systemd/system/tower-cache.service`.

```bash
cd ansible
ansible-playbook playbooks/site.yml --limit tower -e tower_cache_enabled=true
```

That partitions the (empty) Optane, installs the unit and repoints the
`/mnt/raid1` fstab entry at `/dev/mapper/raid1cache`. It does **not** remount the
live array — the cutover is a reboot (see below).

## Cutover (reboot)

`tower-cache.service` (ordered `Before=local-fs-pre.target`, so it runs before
any local mount) creates the dm device before `local-fs.target`, so on reboot
`/mnt/raid1` mounts through the cache automatically.

```bash
ssh tower sudo reboot
# after reboot:
ssh tower 'dmsetup status raid1cache; findmnt -no SOURCE /mnt/raid1'   # -> /dev/mapper/raid1cache
ssh tower 'dmsetup table raid1cache'
```

Expect `dmsetup status` to show the mode `writethrough` and growing
`promotions`. If the Optane were missing, `/mnt/raid1` would fail to mount
(the box still boots; tower services that need it start after a manual mount).

## Revert / recovery

- **Mount the array directly** (safe in writethrough): stop the services that
  use `/mnt/raid1`, `umount /mnt/raid1`,
  `systemctl stop tower-cache`, `mount /dev/md0 /mnt/raid1`.
- **Disable for good:** `systemctl disable --now tower-cache`, point the fstab
  entry back at `/dev/md0` (or its UUID), and `wipefs -a /dev/nvme0n1`.
- **Repair metadata** (needs_check): stop the unit and run
  `cache_check /dev/nvme0n1p1` / `cache_repair` (from `thin-provisioning-tools`).

## Notes and risks

- **Single, non-redundant cache device.** Writethrough keeps the origin
  authoritative, so a dead Optane costs only cache warmth, not data.
- **Limited benefit for the media workload.** The array is mostly large
  sequential I/O (torrent writes, Jellyfin reads); `dm-cache`/`smq` has no
  sequential bypass, so those streams both gain little and evict cache. The real
  win is random I/O: filesystem metadata and the latency-sensitive services
  (Postgres, Garage metadata, the `vault`/`opencloud` LUNs).
- **`tower-iscsi.service`, NFS, Garage and Postgres all use `/mnt/raid1`**, so
  they transparently follow the cache device — no change needed on their side.
- **LVM cache** would be the friendlier interface, but it needs the origin to be
  an LVM LV; converting the existing ext4 on `md0` is destructive, hence raw
  `dmsetup`.
