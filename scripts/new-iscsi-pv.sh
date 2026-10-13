#!/usr/bin/env bash
# Print a static iSCSI PersistentVolume + PersistentVolumeClaim pair for a
# tower block volume (StorageClass tower-iscsi-static, RWO).
#
# Usage: scripts/new-iscsi-pv.sh <name> <lun> [size]
#   name  short volume name (e.g. db-postgres)
#   lun   LUN index on the tower target — matches the order of
#         tower_iscsi_volumes in ansible/roles/tower_storage/defaults/main.yml
#         (vol1 -> 0, vault -> 1, next -> 2, ...)
#   size  PV capacity (default 10Gi)
#
# NOTE: this only renders the Kubernetes objects. Create the backing LUN first
# by adding the volume to tower_iscsi_volumes and running the tower play:
#   ansible-playbook ansible/playbooks/site.yml --limit tower
set -euo pipefail

name=${1:?usage: new-iscsi-pv.sh <name> <lun> [size]}
lun=${2:?usage: new-iscsi-pv.sh <name> <lun> [size]}
size=${3:-10Gi}

target_portal=192.168.30.200:3260
iqn=iqn.2026-10.lan.tower:k8s

cat <<EOF
apiVersion: v1
kind: PersistentVolume
metadata:
  name: tower-iscsi-${name}
spec:
  capacity:
    storage: ${size}
  accessModes:
    - ReadWriteOnce
  persistentVolumeReclaimPolicy: Retain
  storageClassName: tower-iscsi-static
  iscsi:
    targetPortal: ${target_portal}
    iqn: ${iqn}
    lun: ${lun}
    fsType: ext4
---
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: ${name}
spec:
  accessModes:
    - ReadWriteOnce
  storageClassName: tower-iscsi-static
  volumeName: tower-iscsi-${name}
  resources:
    requests:
      storage: ${size}
EOF
