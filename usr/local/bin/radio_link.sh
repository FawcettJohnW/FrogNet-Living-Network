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
# radio_link.sh — bidirectional "radio" shaper using tc + ifb
# Usage:
#   sudo ./radio_link.sh set  wlan1 1200        [delay_ms] [loss_pct]
#   sudo ./radio_link.sh step wlan1             (cycles preset rates)
#   sudo ./radio_link.sh clear wlan1
#
# Notes:
# - "1200 baud" ≈ 1200 bit/s. tc uses bit/s, so rate=1200bit.
# - Use on the *actual* interface carrying the FrogNet traffic (e.g., wlan0/wlan1).


CMD="${1:-}"
DEV="${2:-}"

IFB="ifb0"

# Presets (bit/s) from "radio" to fast
PRESETS=(1200 2400 4800 9600 19200 38400 57600 115200 256000 512000 1000000 5000000 10000000)

die(){ echo "ERROR: $*" >&2; exit 2; }

need_root(){
  [[ "$(id -u)" == "0" ]] || die "run as root (sudo)"
}

ensure_ifb(){
  modprobe ifb numifbs=1 2>/dev/null || true
  ip link add "$IFB" type ifb 2>/dev/null || true
  ip link set dev "$IFB" up
}

clear_tc(){
  tc qdisc del dev "$DEV" root    2>/dev/null || true
  tc qdisc del dev "$DEV" ingress 2>/dev/null || true
  tc qdisc del dev "$IFB" root    2>/dev/null || true
  tc filter del dev "$DEV" parent ffff: 2>/dev/null || true
}

set_tc(){
  local rate_bps="$1"
  local delay_ms="${2:-0}"
  local loss_pct="${3:-0}"

  [[ -n "$DEV" ]] || die "missing DEV"
  ip link show "$DEV" >/dev/null 2>&1 || die "no such dev: $DEV"

  ensure_ifb
  clear_tc

  # -------------------------
  # EGRESS on DEV
  # -------------------------
  # TBF: rate-limit. Keep burst small (radio-like), but not too small to stall.
  # latency is queue bound for the shaper (ms)
  tc qdisc add dev "$DEV" root handle 1: tbf rate "${rate_bps}bit" burst 1500 latency 400ms

  # Optional: add netem AFTER tbf (delay/loss/jitter). Use a child qdisc.
  if [[ "$delay_ms" != "0" || "$loss_pct" != "0" ]]; then
    tc qdisc add dev "$DEV" parent 1:1 handle 10: netem \
      $( [[ "$delay_ms" != "0" ]] && echo "delay ${delay_ms}ms" ) \
      $( [[ "$loss_pct" != "0"  ]] && echo "loss ${loss_pct}%" )
  fi

  # -------------------------
  # INGRESS shaping via IFB
  # -------------------------
  tc qdisc add dev "$DEV" handle ffff: ingress

  tc filter add dev "$DEV" parent ffff: protocol ip u32 match u32 0 0 \
    action mirred egress redirect dev "$IFB"

  # Shape the redirected ingress on IFB
  tc qdisc add dev "$IFB" root handle 1: tbf rate "${rate_bps}bit" burst 1500 latency 400ms

  if [[ "$delay_ms" != "0" || "$loss_pct" != "0" ]]; then
    tc qdisc add dev "$IFB" parent 1:1 handle 10: netem \
      $( [[ "$delay_ms" != "0" ]] && echo "delay ${delay_ms}ms" ) \
      $( [[ "$loss_pct" != "0"  ]] && echo "loss ${loss_pct}%" )
  fi

  echo "OK: ${DEV} shaped bidirectionally at ${rate_bps} bit/s (delay=${delay_ms}ms loss=${loss_pct}%)"
}

step_tc(){
  [[ -n "$DEV" ]] || die "missing DEV"
  local state="/run/frognet_radio_rate_${DEV}.idx"
  local idx=0
  [[ -f "$state" ]] && idx="$(cat "$state" 2>/dev/null || echo 0)"
  [[ "$idx" =~ ^[0-9]+$ ]] || idx=0

  local rate="${PRESETS[$idx]}"
  idx=$(( (idx + 1) % ${#PRESETS[@]} ))
  echo "$idx" > "$state"

  set_tc "$rate" "${3:-0}" "${4:-0}"
}

need_root

case "$CMD" in
  set)
    [[ -n "${DEV:-}" ]] || die "usage: $0 set <dev> <bps> [delay_ms] [loss_pct]"
    set_tc "${3:-}" "${4:-0}" "${5:-0}"
    ;;
  step)
    [[ -n "${DEV:-}" ]] || die "usage: $0 step <dev> [delay_ms] [loss_pct]"
    step_tc
    ;;
  clear)
    [[ -n "${DEV:-}" ]] || die "usage: $0 clear <dev>"
    ensure_ifb
    clear_tc
    echo "OK: cleared shaping on ${DEV}"
    ;;
  *)
    die "usage:
  sudo $0 set   <dev> <bps> [delay_ms] [loss_pct]
  sudo $0 step  <dev> [delay_ms] [loss_pct]
  sudo $0 clear <dev>"
    ;;
esac
