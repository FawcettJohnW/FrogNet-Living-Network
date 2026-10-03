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
# /usr/local/bin/ham_sysctl_apply.sh
#
# Idempotent sysctl tuning for long RTT low-speed links.
# Safe: does not change LAN routing, only kernel TCP/buffer behavior.
#

DEV="ham0"
RATE_BPS=""
DELAY_MS=""
JITTER_MS=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dev) DEV="${2:?}"; shift 2 ;;
    --rate-bps) RATE_BPS="${2:?}"; shift 2 ;;
    --delay-ms) DELAY_MS="${2:?}"; shift 2 ;;
    --jitter-ms) JITTER_MS="${2:?}"; shift 2 ;;
    *) echo "Unknown arg: $1" >&2; exit 2 ;;
  esac
done

# rp_filter must be off for GRE/gretap overlays
sysctl -w net.ipv4.conf.all.rp_filter=0 >/dev/null || true
sysctl -w net.ipv4.conf.default.rp_filter=0 >/dev/null || true
sysctl -w "net.ipv4.conf.${DEV}.rp_filter=0" >/dev/null || true

# Core socket buffers: keep larger than defaults for long RTT.
# These do not harm LAN; they only raise ceilings.
sysctl -w net.core.rmem_max=33554432   >/dev/null || true
sysctl -w net.core.wmem_max=33554432   >/dev/null || true
sysctl -w net.core.rmem_default=262144 >/dev/null || true
sysctl -w net.core.wmem_default=262144 >/dev/null || true

# TCP autotuning ranges
sysctl -w 'net.ipv4.tcp_rmem=4096 262144 33554432' >/dev/null || true
sysctl -w 'net.ipv4.tcp_wmem=4096 262144 33554432' >/dev/null || true

# Backlogs
sysctl -w net.core.netdev_max_backlog=250000 >/dev/null || true
sysctl -w net.core.somaxconn=4096            >/dev/null || true
sysctl -w net.ipv4.tcp_max_syn_backlog=8192  >/dev/null || true

# Optional stability knobs (safe; keep conservative)
# Long RTT links tend to suffer from spurious retransmits; leave kernel defaults unless needed.

exit 0
