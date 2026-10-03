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
# /usr/local/bin/ham_tc.sh
#
# Radio-like shaper for ham0:
#   - netem for delay/jitter/loss (bounded queue)
#   - tbf for strict rate limiting
#   - offloads disabled to make tc behavior realistic
#
# Usage:
#   ham_tc.sh <RATE_BPS> <BASE_DELAY_MS> <JITTER_MS> <LOSS_PCT> <DEV>
#
# Example:
#   ham_tc.sh 9600 600 80 1.0 ham0
#

RATE_BPS="${1:?RATE_BPS required}"
BASE_DELAY_MS="${2:?BASE_DELAY_MS required}"
JITTER_MS="${3:?JITTER_MS required}"
LOSS_PCT="${4:?LOSS_PCT required}"
DEV="${5:?DEV required}"

tc_bin="/usr/sbin/tc"
ethtool_bin="/usr/sbin/ethtool"
ip_bin="/usr/sbin/ip"

echo "[ham_tc] dev=$DEV rate=${RATE_BPS}bps delay=${BASE_DELAY_MS}ms jitter=${JITTER_MS}ms loss=${LOSS_PCT}%"

# Safety: dev must exist
$ip_bin link show "$DEV" >/dev/null 2>&1 || { echo "[ham_tc] missing dev $DEV" >&2; exit 1; }

# Disable offloads (critical for realistic shaping)
if command -v "$ethtool_bin" >/dev/null 2>&1; then
  $ethtool_bin -K "$DEV" gro off gso off tso off 2>/dev/null || true
fi

# Clear any existing qdiscs
$tc_bin qdisc del dev "$DEV" root 2>/dev/null || true

# -----------------------------
# Queue sizing
# -----------------------------
# Rule of thumb:
#   netem limit should hold ~ (rate_bytes_per_s * (delay+3*jitter)/1000) bytes
# but netem limit is in packets. We'll approximate with MTU=1500.
#
# Keep it bounded to avoid "everything blocks" bufferbloat.
#
rate_Bps=$(( RATE_BPS / 8 ))
# window_ms = delay + 3*jitter (conservative)
window_ms=$(( BASE_DELAY_MS + 3*JITTER_MS ))
# bytes_in_flight ~= rate_Bps * window_ms/1000
bytes_in_flight=$(( rate_Bps * window_ms / 1000 ))
# packets ~= bytes/1500, clamp 50..2000
pkts=$(( (bytes_in_flight + 1499) / 1500 ))
if (( pkts < 50 )); then pkts=50; fi
if (( pkts > 2000 )); then pkts=2000; fi

# -----------------------------
# Root: netem (delay/jitter/loss)
# Use distribution normal to avoid extreme outliers.
# Keep jitter modest relative to base delay to reduce reordering.
# -----------------------------
$tc_bin qdisc add dev "$DEV" root handle 1: netem \
  delay "${BASE_DELAY_MS}ms" "${JITTER_MS}ms" distribution normal \
  loss "${LOSS_PCT}%" \
  limit "$pkts"

# -----------------------------
# Child: tbf (rate)
# burst: allow some aggregation (64k is safe for HTTP bursts)
# latency: cap internal queueing
# -----------------------------
$tc_bin qdisc add dev "$DEV" parent 1:1 handle 10: tbf \
  rate "${RATE_BPS}bit" \
  burst 64k \
  latency 2s

# Optional: increase tx queue length a bit (helps absorb microbursts)
$ip_bin link set dev "$DEV" txqueuelen 2000 2>/dev/null || true

echo "[ham_tc] netem.limit_pkts=$pkts"
$tc_bin -s qdisc show dev "$DEV" || true
