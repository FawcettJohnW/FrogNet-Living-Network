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
# /usr/local/bin/ham_qdisc_apply.sh
#
# Idempotent qdisc setup for ham simulation.
# Pattern:
#   root HTB (strict rate)
#   child netem (delay/jitter/loss with bounded limit)
#

DEV="ham0"
RATE_BPS=""
DELAY_MS=""
JITTER_MS=""
LOSS_PCT=""
MTU="576"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dev) DEV="${2:?}"; shift 2 ;;
    --rate-bps) RATE_BPS="${2:?}"; shift 2 ;;
    --delay-ms) DELAY_MS="${2:?}"; shift 2 ;;
    --jitter-ms) JITTER_MS="${2:?}"; shift 2 ;;
    --loss-pct) LOSS_PCT="${2:?}"; shift 2 ;;
    --mtu) MTU="${2:?}"; shift 2 ;;
    *) echo "Unknown arg: $1" >&2; exit 2 ;;
  esac
done

[[ -n "$RATE_BPS" && -n "$DELAY_MS" && -n "$JITTER_MS" && -n "$LOSS_PCT" ]] || {
  echo "usage: $0 --dev ham0 --rate-bps N --delay-ms N --jitter-ms N --loss-pct X --mtu N" >&2
  exit 2
}

IP="/usr/sbin/ip"
TC="/usr/sbin/tc"

$IP link show "$DEV" >/dev/null 2>&1 || { echo "[ham_qdisc] missing dev $DEV" >&2; exit 1; }

echo "[ham_qdisc] dev=$DEV rate=${RATE_BPS}bps delay=${DELAY_MS}ms jitter=${JITTER_MS}ms loss=${LOSS_PCT}% mtu=$MTU"

# Clear existing qdisc (idempotent)
$TC qdisc del dev "$DEV" root 2>/dev/null || true

# Bounded netem queue sizing (packets)
rate_Bps=$(( RATE_BPS / 8 ))
window_ms=$(( DELAY_MS + 3*JITTER_MS ))
bytes_in_flight=$(( rate_Bps * window_ms / 1000 ))
pkts=$(( (bytes_in_flight + (MTU-1)) / MTU ))
# At very low rates, large queues create absurd latency (bufferbloat).
# Clamp aggressively so interactive traffic (ping/control) stays responsive.
if (( RATE_BPS <= 2400 )); then
  # ~1–3 seconds max buffering
  if (( pkts < 2 )); then pkts=2; fi
  if (( pkts > 10 )); then pkts=10; fi
elif (( RATE_BPS <= 9600 )); then
  if (( pkts < 5 )); then pkts=5; fi
  if (( pkts > 30 )); then pkts=30; fi
else
  if (( pkts < 20 )); then pkts=20; fi
  if (( pkts > 200 )); then pkts=200; fi
fi


# HTB root class
$TC qdisc add dev "$DEV" root handle 1: htb default 10
$TC class add dev "$DEV" parent 1: classid 1:10 htb \
  rate "${RATE_BPS}bit" ceil "${RATE_BPS}bit" burst 64k

# netem child on the class
$TC qdisc add dev "$DEV" parent 1:10 handle 10: netem \
  delay "${DELAY_MS}ms" "${JITTER_MS}ms" distribution normal \
  loss "${LOSS_PCT}%" \
  limit "$pkts"

echo "[ham_qdisc] netem.limit_pkts=$pkts"
$TC -s qdisc show dev "$DEV" || true

exit 0
