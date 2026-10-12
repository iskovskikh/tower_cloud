#!/usr/bin/env bash
# recon.sh — read-only reconnaissance of the tower_cloud home network.
#
# Safe to run any time: performs no writes, needs no root, changes nothing.
# It only pings, reads local tables, and opens TCP connections to check ports.
#
# Usage:
#   scripts/recon.sh                 # full report
#   scripts/recon.sh --host 192.168.30.200   # port-scan a single host too
#   scripts/recon.sh --quick         # skip the full /24 sweeps
#
# Output is plain text, suitable for feeding to an AI agent or diffing.
set -u

LAN_SWEEP="${LAN_SWEEP:-192.168.1.0/24}"
SRV_SWEEP="${SRV_SWEEP:-192.168.30.0/24}"
KEY_PORTS="22 80 111 139 443 445 2049 3128 3389 5900 8006 8096 8200 9000 9001 9091"
HOSTS_TO_SCAN=("192.168.1.1" "192.168.30.1" "192.168.30.200" "192.168.30.201")

quick=0
single_host=""
while [ $# -gt 0 ]; do
  case "$1" in
    --quick) quick=1 ;;
    --host) shift; single_host="${1:-}" ;;
    -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
  shift
done

section() { printf '\n=== %s ===\n' "$1"; }
have() { command -v "$1" >/dev/null 2>&1; }

# ping sweep without nmap: fire parallel pings, collect responders.
sweep() {
  local cidr="$1" base="${1%.0/24}" i
  for i in $(seq 1 254); do
    ( ping -c1 -W1 "${base}.${i}" >/dev/null 2>&1 && echo "${base}.${i}" ) &
  done
  wait
}

portopen() { timeout 1 bash -c "echo > /dev/tcp/$1/$2" 2>/dev/null; }

scan_host() {
  local host="$1" p
  local open=""
  for p in $KEY_PORTS; do
    portopen "$host" "$p" && open="${open}${p} "
  done
  printf '  %-18s open: %s\n' "$host" "${open:-(none/blocked)}"
}

now() { date '+%Y-%m-%d %H:%M:%S %z'; }

section "recon report"
printf 'generated: %s\n' "$(now)"
printf 'from:      %s (%s)\n' "$(hostnamectl --static 2>/dev/null || echo host)" "$(uname -srm)"

section "local interfaces (IPv4)"
ip -4 -o addr show scope global 2>/dev/null | awk '{printf "  %-10s %-18s %s\n", $2, $4, $9}'

section "default routes"
ip route show default 2>/dev/null | sed 's/^/  /'

section "ARP/neighbour table (local subnets)"
ip neigh show 2>/dev/null | grep -vE 'FAILED|INCOMPLETE' | sed 's/^/  /'

if [ "$quick" -eq 0 ]; then
  section "live hosts in $LAN_SWEEP"
  sweep "$LAN_SWEEP" | sort -t. -k4 -n | sed 's/^/  /'
  section "live hosts in $SRV_SWEEP"
  sweep "$SRV_SWEEP" | sort -t. -k4 -n | sed 's/^/  /'
fi

section "open key ports on core hosts"
if [ -n "$single_host" ]; then
  scan_host "$single_host"
else
  for h in "${HOSTS_TO_SCAN[@]}"; do scan_host "$h"; done
fi

if have getent; then
  section "resolved names (*.lan / mDNS)"
  for ip in 192.168.1.207 192.168.1.214 192.168.30.200 192.168.30.201; do
    n="$(getent hosts "$ip" 2>/dev/null | awk '{print $2}')"
    printf '  %-18s %s\n' "$ip" "${n:-?}"
  done
fi

section "service probes"
printf '  tower   nginx :80      %s\n' "$(curl -s -m4 -o /dev/null -w '%{http_code}' http://192.168.30.200/ 2>/dev/null)"
printf '  tower   jellyfin       %s\n' "$(curl -s -m4 -o /dev/null -w '%{http_code}' http://192.168.30.200:8096/health 2>/dev/null)"
printf '  tower   minio console  %s\n' "$(curl -s -m4 -o /dev/null -w '%{http_code}' http://192.168.30.200:9001/ 2>/dev/null)"
printf '  proxmox web :8006      %s\n' "$(curl -sk -m4 -o /dev/null -w '%{http_code}' https://192.168.30.201:8006/ 2>/dev/null)"
info="$(curl -s -m4 http://192.168.30.200:8096/System/Info/Public 2>/dev/null)"
[ -n "$info" ] && printf '  jellyfin info: %s\n' "$info"

if have showmount; then
  section "NFS exports (tower)"
  showmount -e 192.168.30.200 2>/dev/null | sed 's/^/  /'
fi

section "notes"
echo "  Update docs/network/inventory.yaml if anything here changed."
echo "  Values are read-only observations; no credentials are printed."
