#!/bin/bash
################################################################
#  Copyright (C) 2016-2026 Fawcett Innovations LLC             #
#                                                              #
#  SPDX-License-Identifier: GPL-2.0-only                       #
#                                                              #
#  This program is free software; you can redistribute it      #
#  and/or modify it under the terms of the GNU General Public  #
#  License as published by the Free Software Foundation;       #
#  version 2 of the License, and no other version.             #
#                                                              #
#  This program is distributed in the hope that it will be     #
#  useful, but WITHOUT ANY WARRANTY; without even the implied  #
#  warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR     #
#  PURPOSE.  See the GNU General Public License for details.   #
#                                                              #
#  See COPYRIGHT and LICENSE at the root of this tree.         #
################################################################
. /usr/local/bin/mapInterfaces

TRANSPORT_CONF="/etc/frognet/transport.conf"

REMOTE_FROGNET_IP=""
RADIO_IFACE=""
RATE_BPS="1200"
DELAY_MS="600"
LOSS_PCT="1.0"
ROUTE_METRIC="20"
QUEUE_NUM="42"
PROBE_TIMEOUT_MS="3000"
GATEWAY_PORT="17777"

die(){ echo "ERROR: $*" >&2; exit 1; }
is_ipv4(){ [[ "${1:-}" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]]; }
is_10net(){ [[ "${1:-}" =~ ^10\. ]]; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --remote) REMOTE_FROGNET_IP="${2:-}"; shift 2 ;;
    --iface)  RADIO_IFACE="${2:-}"; shift 2 ;;
    --rate)   RATE_BPS="${2:-}"; shift 2 ;;
    --delay)  DELAY_MS="${2:-}"; shift 2 ;;
    --loss)   LOSS_PCT="${2:-}"; shift 2 ;;
    --metric) ROUTE_METRIC="${2:-}"; shift 2 ;;
    --queue)  QUEUE_NUM="${2:-}"; shift 2 ;;
    --timeout) PROBE_TIMEOUT_MS="${2:-}"; shift 2 ;;
    --port)   GATEWAY_PORT="${2:-}"; shift 2 ;;
    -h|--help)
      cat >&2 <<EOF
Usage: $0 --remote <remote_frognet_gateway_10.x.y.1> --iface <carrier_iface>
EOF
      exit 0 ;;
    *) die "Unknown argument: $1" ;;
  esac
done

[[ $EUID -eq 0 ]] || die "Must be run as root"
[[ -n "$REMOTE_FROGNET_IP" ]] || die "--remote is required"
is_ipv4 "$REMOTE_FROGNET_IP" || die "Invalid --remote IP"
is_10net "$REMOTE_FROGNET_IP" || die "--remote must be a 10/8 FrogNet address"
[[ -n "$RADIO_IFACE" ]] || die "--iface is required (bench carrier, e.g. eth1)"

[[ -f "$TRANSPORT_CONF" ]] || die "Missing $TRANSPORT_CONF"
# shellcheck disable=SC1090
. "$TRANSPORT_CONF"
: "${TRANSPORT_IPV4_PREFIX:?Missing TRANSPORT_IPV4_PREFIX in transport.conf}"
: "${FROGNET_IPV4_PREFIX:?Missing FROGNET_IPV4_PREFIX in transport.conf}"
: "${TRANSPORT_LINK_MASK:?Missing TRANSPORT_LINK_MASK in transport.conf}"

# Determine local FrogNet identity (10.x.y.1)
LOCAL_FROGNET_IP="$(ip -4 addr show | awk '/inet 10\./{print $2}' | cut -d/ -f1 | awk -F. '$4==1{print; exit}')"
[[ -n "$LOCAL_FROGNET_IP" ]] || die "Cannot determine local FrogNet identity (10.x.y.1)"

# IDs (third octet)
LOCAL_ID="$(echo "$LOCAL_FROGNET_IP" | cut -d. -f3)"
REMOTE_ID="$(echo "$REMOTE_FROGNET_IP" | cut -d. -f3)"

if (( LOCAL_ID < REMOTE_ID )); then
  MIN_ID="$LOCAL_ID"; MAX_ID="$REMOTE_ID"
  LOCAL_TRANSPORT_HOST=1; REMOTE_TRANSPORT_HOST=2
  LOCAL_PHANTOM_HOST=1;  REMOTE_PHANTOM_HOST=2
else
  MIN_ID="$REMOTE_ID"; MAX_ID="$LOCAL_ID"
  LOCAL_TRANSPORT_HOST=2; REMOTE_TRANSPORT_HOST=1
  LOCAL_PHANTOM_HOST=2;  REMOTE_PHANTOM_HOST=1
fi

PHANTOM_NET="10.102.${MIN_ID}.0/24"
LOCAL_PHANTOM_IP="10.102.${MIN_ID}.${LOCAL_PHANTOM_HOST}"
REMOTE_PHANTOM_IP="10.102.${MIN_ID}.${REMOTE_PHANTOM_HOST}"

LOCAL_TRANSPORT_CIDR="${TRANSPORT_IPV4_PREFIX}.${MIN_ID}.${MAX_ID}.${LOCAL_TRANSPORT_HOST}/${TRANSPORT_LINK_MASK}"
REMOTE_TRANSPORT_IP="${TRANSPORT_IPV4_PREFIX}.${MIN_ID}.${MAX_ID}.${REMOTE_TRANSPORT_HOST}"

REMOTE_NET="$(echo "$REMOTE_FROGNET_IP" | cut -d. -f1-3).0/24"

echo "[INIT] local_frognet=$LOCAL_FROGNET_IP remote_frognet=$REMOTE_FROGNET_IP"
echo "[INIT] carrier_iface=$RADIO_IFACE phantom=$PHANTOM_NET local_phantom=$LOCAL_PHANTOM_IP remote_phantom=$REMOTE_PHANTOM_IP"
echo "[INIT] transport_local=$LOCAL_TRANSPORT_CIDR transport_remote=$REMOTE_TRANSPORT_IP"
echo "[INIT] remote_net=$REMOTE_NET shaping=${RATE_BPS}bps ${DELAY_MS}ms ${LOSS_PCT}% metric=$ROUTE_METRIC"

# Preflight: carrier must be up
ip link show "$RADIO_IFACE" >/dev/null 2>&1 || die "Carrier iface $RADIO_IFACE not found"
ip link set "$RADIO_IFACE" up

# Configure phantom underlay
ip addr flush dev "$RADIO_IFACE" || true
ip addr add "${LOCAL_PHANTOM_IP}/24" dev "$RADIO_IFACE"

# Preflight: peer must be reachable on phantom underlay (cable plugged, other side configured)
if ! ping -I "$RADIO_IFACE" -c1 -W1 "$REMOTE_PHANTOM_IP" >/dev/null 2>&1; then
  die "Phantom peer $REMOTE_PHANTOM_IP not reachable on $RADIO_IFACE. Ensure the other side ran the script and the cable is connected."
fi

# Build ham0 overlay
/usr/local/bin/ham_link_down.sh 2>/dev/null || true
/usr/local/bin/ham_link_up.sh "$LOCAL_PHANTOM_IP" "$REMOTE_PHANTOM_IP" "$LOCAL_TRANSPORT_CIDR"
/usr/local/bin/ham_tc.sh "$RATE_BPS" "$DELAY_MS" "$LOSS_PCT" ham0

# Postflight: transport peer must be reachable
if ! ping -I ham0 -c1 -W2 "$REMOTE_TRANSPORT_IP" >/dev/null 2>&1; then
  die "Transport peer $REMOTE_TRANSPORT_IP not reachable on ham0. Underlay OK but overlay failed."
fi

# Route remote FrogNet over ham0 (src forced)
ip route replace "$REMOTE_NET" via "$REMOTE_TRANSPORT_IP" dev ham0 src "$LOCAL_FROGNET_IP" metric "$ROUTE_METRIC"

# rp_filter off (required)
sysctl -w net.ipv4.conf.all.rp_filter=0 >/dev/null || true
sysctl -w net.ipv4.conf.default.rp_filter=0 >/dev/null || true
sysctl -w net.ipv4.conf.ham0.rp_filter=0 >/dev/null || true

# Write ICMP config
mkdir -p /etc/frognet
cat >/etc/frognet/icmp_proxy.json <<EOF
{
  "queue_num": ${QUEUE_NUM},
  "probe_timeout_ms": ${PROBE_TIMEOUT_MS},
  "remote_prefixes": ["${REMOTE_NET}"],
  "gateway_ip": "${REMOTE_TRANSPORT_IP}",
  "gateway_port": ${GATEWAY_PORT},
  "lan_iface": "${eth0Name}",
  "fail_closed": true
}
EOF

# Apply iptables (your updated /etc/setup_iptables reads icmp_proxy.json)
if [[ -x /etc/setup_iptables ]]; then
  /etc/setup_iptables
else
  echo "[WARN] /etc/setup_iptables not executable; skipping"
fi

echo "[OK] carrier:"
ip addr show dev "$RADIO_IFACE" | sed -n '1,12p'
echo "[OK] ham0:"
ip addr show dev ham0 | sed -n '1,12p'
echo "[OK] routes:"
ip route get "$REMOTE_FROGNET_IP" || true
