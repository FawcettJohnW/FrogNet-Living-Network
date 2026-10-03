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
# /usr/local/bin/ham_restart_link.sh
#
# Rebuild the FrogNet HAM simulator link after reboot:
#   - Recreates ham0 gretap tunnel
#   - Assigns 44/8 /30 transport IP
#   - Applies tc shaping (rate/lat/loss)
#   - Installs 10/8 network-to-network routes over ham0 with explicit src=<10.x.x.1>
#   - Disables rp_filter for ham0 + all/default (prevents asymmetric drops)
#
# Usage (run on EACH gateway):
#   ham_restart_link.sh \
#     --local-underlay 10.101.40.1 \
#     --remote-underlay 10.101.40.66 \
#     --local-ham 44.40.50.1/30 \
#     --remote-ham 44.40.50.2 \
#     --remote-net 10.101.50.0/24 \
#     --src-ip 10.101.40.1 \
#     --rate 1200 --delay 300 --loss 1.0 \
#     --metric 20
#
# Notes:
# - "underlay" is what the gretap tunnel rides on (must be directly reachable).
# - local-ham is the address assigned to ham0 on THIS machine.
# - remote-ham is the peer ham0 address used as the next hop.
# - remote-net is the 10/8 network reachable on the far gateway.
# - src-ip must be THIS FrogNet host identity (.1 on its 10/8 /24).
# - metric can be 20 for "force ham" testing, or 900 for "backup path" mode.
#

LOCAL_UNDERLAY=""
REMOTE_UNDERLAY=""
LOCAL_HAM_CIDR=""
REMOTE_HAM_IP=""
REMOTE_NET=""
SRC_IP=""
RATE="1200"
DELAY="300"
LOSS="1.0"
METRIC="20"

# Parse args
while [[ $# -gt 0 ]]; do
  case "$1" in
    --local-underlay) LOCAL_UNDERLAY="${2:-}"; shift 2 ;;
    --remote-underlay) REMOTE_UNDERLAY="${2:-}"; shift 2 ;;
    --local-ham) LOCAL_HAM_CIDR="${2:-}"; shift 2 ;;
    --remote-ham) REMOTE_HAM_IP="${2:-}"; shift 2 ;;
    --remote-net) REMOTE_NET="${2:-}"; shift 2 ;;
    --src-ip) SRC_IP="${2:-}"; shift 2 ;;
    --rate) RATE="${2:-}"; shift 2 ;;
    --delay) DELAY="${2:-}"; shift 2 ;;
    --loss) LOSS="${2:-}"; shift 2 ;;
    --metric) METRIC="${2:-}"; shift 2 ;;
    -h|--help)
      echo "See header comment for usage." >&2
      exit 0
      ;;
    *)
      echo "Unknown arg: $1" >&2
      exit 2
      ;;
  esac
done

# Validate
if [[ -z "${LOCAL_UNDERLAY}" || -z "${REMOTE_UNDERLAY}" || -z "${LOCAL_HAM_CIDR}" || -z "${REMOTE_HAM_IP}" || -z "${REMOTE_NET}" || -z "${SRC_IP}" ]]; then
  echo "Missing required args. Run with --help." >&2
  exit 2
fi

if [[ ! -x /usr/local/bin/ham_link_up.sh || ! -x /usr/local/bin/ham_tc.sh ]]; then
  echo "Missing required scripts: /usr/local/bin/ham_link_up.sh and/or /usr/local/bin/ham_tc.sh" >&2
  exit 2
fi

echo "[HAM] Bringing down any existing ham0..."
/usr/local/bin/ham_link_down.sh 2>/dev/null || true

echo "[HAM] Bringing up ham0 over underlay ${LOCAL_UNDERLAY} -> ${REMOTE_UNDERLAY} with ${LOCAL_HAM_CIDR} ..."
/usr/local/bin/ham_link_up.sh "${LOCAL_UNDERLAY}" "${REMOTE_UNDERLAY}" "${LOCAL_HAM_CIDR}"

echo "[HAM] Applying shaping to ham0: rate=${RATE}bps delay=${DELAY}ms loss=${LOSS}% ..."
/usr/local/bin/ham_tc.sh "${RATE}" "${DELAY}" "${LOSS}" ham0

echo "[HAM] Disabling rp_filter (fail-open routing acceptance for asymmetric paths) ..."
/sbin/sysctl -w net.ipv4.conf.all.rp_filter=0 >/dev/null || true
/sbin/sysctl -w net.ipv4.conf.default.rp_filter=0 >/dev/null || true
/sbin/sysctl -w net.ipv4.conf.ham0.rp_filter=0 >/dev/null || true

echo "[HAM] Installing route to ${REMOTE_NET} via ${REMOTE_HAM_IP} dev ham0 src ${SRC_IP} metric ${METRIC} ..."
/sbin/ip route replace "${REMOTE_NET}" via "${REMOTE_HAM_IP}" dev ham0 src "${SRC_IP}" metric "${METRIC}"

echo "[HAM] Sanity checks:"
echo "  - ham0 addr:"
/sbin/ip addr show ham0 | sed -n '1,20p'
echo "  - route to remote net:"
/sbin/ip route get "$(echo "${REMOTE_NET}" | cut -d/ -f1)" 2>/dev/null || true
echo "  - tc:"
/sbin/tc qdisc show dev ham0 2>/dev/null || true

echo "[HAM] Done."
