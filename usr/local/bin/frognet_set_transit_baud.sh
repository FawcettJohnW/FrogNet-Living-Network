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
# /usr/local/bin/frognet_set_transit_baud.sh
#
# Transit "baud" shaper + MTU/buffer tuning
#
# PURPOSE:
#   - Shape a transit interface (e.g., eth1) to a HAM-like baud profile
#   - ALSO set MTU + buffering so the link stays usable at very low rates
#   - Does NOT create ham0 / overlays; this is strictly per-interface shaping
#
# USAGE:
#   frognet_set_transit_baud.sh <iface> <baud>
#
# EXAMPLES:
#   sudo frognet_set_transit_baud.sh eth1 1200
#   sudo frognet_set_transit_baud.sh eth1 9600
#
# DEPENDS ON:
#   /usr/local/bin/frognet_tc_apply.sh
#
# NOTES:
#   - At sub-kilobit rates, default MTU + tiny queues can cause ENOBUFS ("No buffer space")
#   - We keep queues bounded, but not absurdly small.

set -u

TC_APPLY="/usr/local/bin/frognet_tc_apply.sh"
TC="/sbin/tc"
IP="/usr/sbin/ip"
ETHTOOL="/usr/sbin/ethtool"

die(){ echo "ERROR: $*" >&2; exit 1; }
log(){ echo "[frognet_set_transit_baud] $*"; }

# Must be root
if [[ "$(id -u)" -ne 0 ]]; then
  die "must be run as root (use sudo)"
fi

if [[ $# -ne 2 ]]; then
  echo "Usage: $0 <interface> <baud>"
  echo "Supported baud rates: 1200 2400 4800 9600 19200"
  exit 1
fi

DEV="$1"
BAUD="$2"

[[ -x "$TC_APPLY" ]] || die "$TC_APPLY not found or not executable"
$IP link show dev "$DEV" >/dev/null 2>&1 || die "interface not found: $DEV"

# ------------------------------------------------------------
# Profile table
# ------------------------------------------------------------
# MTU choices:
#  - Keep >= 128 at 1200 so IP+ICMP works reliably and TCP isn't constantly fragmented into dust.
# Queue choices:
#  - MUST NOT be tiny (5/10 packets caused ENOBUFS).
#  - Keep bounded but allow brief bursts and ARP/ND/control traffic.
#
# NETEM_LIMIT = packets in netem queue
# TXQLEN      = device tx queue length (packets)

case "$BAUD" in
  1200)
    RATE="900bit";   DELAY="600"; JITTER="120"; LOSS="1.0"
    MTU="128"
    TXQLEN="100"
    NETEM_LIMIT="100"
    ;;
  2400)
    RATE="1800bit";  DELAY="600"; JITTER="120"; LOSS="1.0"
    MTU="256"
    TXQLEN="150"
    NETEM_LIMIT="150"
    ;;
  4800)
    RATE="3600bit";  DELAY="600"; JITTER="100"; LOSS="0.5"
    MTU="384"
    TXQLEN="200"
    NETEM_LIMIT="200"
    ;;
  9600)
    RATE="7200bit";  DELAY="500"; JITTER="80";  LOSS="0.3"
    MTU="576"
    TXQLEN="300"
    NETEM_LIMIT="300"
    ;;
  19200)
    RATE="14400bit"; DELAY="350"; JITTER="50";  LOSS="0.1"
    MTU="1000"
    TXQLEN="500"
    NETEM_LIMIT="500"
    ;;
  *)
    die "Unsupported baud rate: $BAUD (supported: 1200 2400 4800 9600 19200)"
    ;;
esac

log "iface=$DEV baud=$BAUD rate=$RATE delay=${DELAY}ms jitter=${JITTER}ms loss=${LOSS}% mtu=$MTU txqlen=$TXQLEN netem_limit=$NETEM_LIMIT"

# ------------------------------------------------------------
# Apply MTU + tx queue + offloads
# ------------------------------------------------------------
$IP link set dev "$DEV" mtu "$MTU" >/dev/null 2>&1 || die "failed to set MTU on $DEV"

$IP link set dev "$DEV" txqueuelen "$TXQLEN" >/dev/null 2>&1 || log "WARN: could not set txqueuelen on $DEV (non-fatal)"

# Disable offloads that create giant packets on low-speed links (best-effort)
if [[ -x "$ETHTOOL" ]]; then
  $ETHTOOL -K "$DEV" gro off gso off tso off lro off >/dev/null 2>&1 || log "WARN: ethtool offload disable failed on $DEV (non-fatal)"
else
  log "WARN: ethtool not found; skipping offload tuning"
fi

# ------------------------------------------------------------
# Apply shaping via existing tool (as requested)
# ------------------------------------------------------------
"$TC_APPLY" "$DEV" "$RATE" "$DELAY" "$JITTER" "$LOSS"

# ------------------------------------------------------------
# Tighten netem limit (best-effort)
# frognet_tc_apply.sh typically creates:
#   root htb 1:
#   netem 10: parent 1:10 limit 1000 ...
# We replace netem with same impairment but sane limit.
# ------------------------------------------------------------
if [[ -x "$TC" ]]; then
  $TC qdisc replace dev "$DEV" parent 1:10 handle 10: netem \
    limit "$NETEM_LIMIT" delay "${DELAY}ms" "${JITTER}ms" loss "${LOSS}%" >/dev/null 2>&1 \
    && log "netem limit set to $NETEM_LIMIT packets on $DEV" \
    || log "WARN: could not adjust netem limit on $DEV (non-fatal; tc hierarchy may differ)"
fi

log "DONE"
exit 0
