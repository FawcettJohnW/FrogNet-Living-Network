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
# /usr/local/bin/ham_apply_profile.sh
#
# Idempotent "HAM radio simulation" profile applier.
#
# This script supports two roles:
#
#   1) LEGACY (dev == ham0):
#        Apply everything (MTU/offloads/txq + sysctls + qdisc) to ham0.
#
#   2) RADIO (dev != ham0)  [recommended for gretap setups]:
#        - Apply "radio framing" (MTU/offloads/txq) to ham0 ONLY
#        - Apply "RF channel" (rate + delay/jitter/loss, bounded queue) to the
#          specified UNDERLAY dev (typically eth1)
#        - Apply rp_filter sysctls to BOTH devices (overlay + underlay)
#
# Rationale:
#   With gretap/gre overlays, L3 routing egresses the UNDERLAY (eth1), not ham0.
#   If you shrink the UNDERLAY MTU, you can blackhole PMTU and stall TCP/ICMP.
#   Therefore, we NEVER change MTU/offloads on the underlay in RADIO mode.
#
# Usage:
#   ham_apply_profile.sh 1200|2400|4800|9600|19200 [--dev <dev>]
#
# Examples:
#   # Legacy (shape ham0 only; mostly useful if you route through ham0 directly)
#   ham_apply_profile.sh 1200
#
#   # Radio simulation for gretap: shape UNDERLAY eth1, frame-size on ham0
#   ham_apply_profile.sh 1200 --dev eth1
#

RATE="${1:-}"
shift || true

DEV="ham0"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --dev) DEV="${2:?}"; shift 2 ;;
    *) echo "Unknown arg: $1" >&2; exit 2 ;;
  esac
done

[[ -n "$RATE" ]] || { echo "usage: $0 1200|2400|4800|9600|19200 [--dev <dev>]" >&2; exit 2; }
[[ "$RATE" =~ ^[0-9]+$ ]] || { echo "rate must be integer bps" >&2; exit 2; }

# -------------------------------
# Profile defaults (tune here)
# -------------------------------
case "$RATE" in
  1200)
    BASE_DELAY_MS=600
    JITTER_MS=120
    LOSS_PCT=1.0
    MTU=296
    ;;
  2400)
    BASE_DELAY_MS=550
    JITTER_MS=110
    LOSS_PCT=0.8
    MTU=296
    ;;
  4800)
    BASE_DELAY_MS=550
    JITTER_MS=100
    LOSS_PCT=0.6
    MTU=576
    ;;
  9600)
    BASE_DELAY_MS=500
    JITTER_MS=80
    LOSS_PCT=0.5
    MTU=1000
    ;;
  19200)
    BASE_DELAY_MS=450
    JITTER_MS=60
    LOSS_PCT=0.3
    MTU=1400
    ;;
  *)
    echo "Unsupported rate: $RATE (use 1200/2400/4800/9600/19200)" >&2
    exit 2
    ;;
esac

IP="/usr/sbin/ip"

# Underlay MTU for qdisc sizing (do NOT change it in RADIO mode)
get_mtu() {
  local d="$1"
  $IP link show dev "$d" 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="mtu"){print $(i+1); exit}}'
}

if [[ "$DEV" == "ham0" ]]; then
  echo "[HAM PROFILE][LEGACY] rate=${RATE}bps dev=${DEV} delay=${BASE_DELAY_MS}ms jitter=${JITTER_MS}ms loss=${LOSS_PCT}% mtu=${MTU}"

  # 1) Interface-level settings (MTU, offloads, txqueue)
  /usr/local/bin/ham_iface_apply.sh --dev "$DEV" --mtu "$MTU"

  # 2) Sysctls (runtime) for long RTT + low-speed stability
  /usr/local/bin/ham_sysctl_apply.sh --dev "$DEV" --rate-bps "$RATE" --delay-ms "$BASE_DELAY_MS" --jitter-ms "$JITTER_MS"

  # 3) Traffic control shaping (HTB + netem)
  /usr/local/bin/ham_qdisc_apply.sh --dev "$DEV" --rate-bps "$RATE" --delay-ms "$BASE_DELAY_MS" --jitter-ms "$JITTER_MS" --loss-pct "$LOSS_PCT" --mtu "$MTU"

  # 4) Flush route cache so kernel uses new path immediately
  $IP route flush cache 2>/dev/null || true

  # 5) Quick verification summary
  /usr/local/bin/ham_verify.sh --dev "$DEV" || true

  exit 0
fi

# RADIO mode: dev != ham0
UNDERLAY_DEV="$DEV"
HAM_DEV="ham0"

UNDERLAY_MTU="$(get_mtu "$UNDERLAY_DEV")"
[[ -n "$UNDERLAY_MTU" ]] || UNDERLAY_MTU="1500"

echo "[HAM PROFILE][RADIO] rate=${RATE}bps underlay=${UNDERLAY_DEV} (mtu=${UNDERLAY_MTU}) ham=${HAM_DEV} (mtu=${MTU}) delay=${BASE_DELAY_MS}ms jitter=${JITTER_MS}ms loss=${LOSS_PCT}%"

# 1) Radio framing on ham0 ONLY (MTU/offloads/txq)
#    (Do NOT touch underlay MTU/offloads; that can blackhole PMTU for GRE/TCP.)
/usr/local/bin/ham_iface_apply.sh --dev "$HAM_DEV" --mtu "$MTU"

# 2) Sysctls (rp_filter) on BOTH overlay and underlay
/usr/local/bin/ham_sysctl_apply.sh --dev "$HAM_DEV" --rate-bps "$RATE" --delay-ms "$BASE_DELAY_MS" --jitter-ms "$JITTER_MS" || true
/usr/local/bin/ham_sysctl_apply.sh --dev "$UNDERLAY_DEV" --rate-bps "$RATE" --delay-ms "$BASE_DELAY_MS" --jitter-ms "$JITTER_MS" || true

# 3) RF channel shaping on UNDERLAY (rate/delay/jitter/loss + bounded queue)
#    Use the *current underlay MTU* for queue sizing math.
#    This does NOT change the interface MTU.
/usr/local/bin/ham_qdisc_apply.sh --dev "$UNDERLAY_DEV" --rate-bps "$RATE" --delay-ms "$BASE_DELAY_MS" --jitter-ms "$JITTER_MS" --loss-pct "$LOSS_PCT" --mtu "$UNDERLAY_MTU"

# 4) Flush route cache so kernel uses new path immediately
$IP route flush cache 2>/dev/null || true

# 5) Quick verification summary (show both)
echo "=== ham_verify underlay=${UNDERLAY_DEV} ==="
/usr/local/bin/ham_verify.sh --dev "$UNDERLAY_DEV" || true
echo "=== ham_verify ham=${HAM_DEV} ==="
/usr/local/bin/ham_verify.sh --dev "$HAM_DEV" || true

exit 0
