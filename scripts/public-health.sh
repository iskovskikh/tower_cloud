#!/usr/bin/env bash
# Probe the public entry points and the Xray reverse tunnel.
#
# Run from archasus (it has routes to both the LAN and the cluster). DNS is
# split-horizon, so the public hosts are forced to the VPS IP with --resolve:
# otherwise cloud.metoda.cc would resolve to ingress-nginx and the adminvps ->
# Xray/SSH tunnel -> ingress path would not be tested at all.
#
# Since 2026-11-13 the public path is: internet -> adminvps Traefik ->
# 127.0.0.1:10443 (SSH reverse) -> Xray VLESS Reality -> k8s-node-1 ingress.
# (WireGuard transport is dropped by the home provider's DPI.)
#
# Exit: 0 = healthy, 1 = at least one check failed. Safe for cron/systemd.
#
# Optional env (e.g. /etc/public-health.env):
#   HEALTHCHECKS_PING_URL  healthchecks.io ping URL; "/fail" is appended on
#                          error. Preferred over Telegram: Telegram egress in
#                          this setup goes through a proxy, so it cannot report
#                          the tunnel itself being down. If the whole WAN dies,
#                          the ping is simply not sent and healthchecks.io
#                          alerts after its grace period.
#   PUBLIC_IP              default 78.17.99.16
#   PUBLIC_HOSTS           space-separated, defaults to all public vhosts
#   TUNNEL_SSH_HOST        default arch@10.10.120.101 (k8s-node-1)
#
# Install with: ansible-playbook ansible/playbooks/monitoring.yml

set -uo pipefail

PUBLIC_IP="${PUBLIC_IP:-78.17.99.16}"
PUBLIC_HOSTS="${PUBLIC_HOSTS:-cloud.metoda.cc auth.metoda.cc hermes.metoda.cc gitlab.metoda.cc}"
TUNNEL_SSH_HOST="${TUNNEL_SSH_HOST:-arch@10.10.120.101}"

fail=0

report_fail() {
    printf 'FAIL %s\n' "$1" >&2
    fail=1
}

for host in $PUBLIC_HOSTS; do
    code="$(curl -skS -o /dev/null -w '%{http_code}' --max-time 10 \
        --resolve "${host}:443:${PUBLIC_IP}" "https://${host}/" 2>/dev/null)"
    # Any 2xx/3xx/4xx proves Traefik reached a backend; 5xx (502/504) and
    # connection failures/timeouts mean the tunnel or ingress is broken.
    case "$code" in
        2??|3??|4??) printf 'ok   %-22s %s\n' "$host" "$code" ;;
        *)           report_fail "$host -> ${code:-timeout}" ;;
    esac
done

if ssh -o BatchMode=yes -o ConnectTimeout=8 "$TUNNEL_SSH_HOST" \
    'systemctl is-active --quiet xray-client xray-reverse-tunnel' 2>/dev/null; then
    printf 'ok   %-22s %s\n' "xray tunnel" "active"
else
    report_fail "xray tunnel services inactive on ${TUNNEL_SSH_HOST#*@}"
fi

if [ -n "${HEALTHCHECKS_PING_URL:-}" ]; then
    if [ "$fail" -eq 0 ]; then
        curl -fsS -m 10 -o /dev/null "$HEALTHCHECKS_PING_URL" || true
    else
        curl -fsS -m 10 -o /dev/null "${HEALTHCHECKS_PING_URL%/}/fail" || true
    fi
fi

exit "$fail"
