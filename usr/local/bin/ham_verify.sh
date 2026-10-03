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
# /usr/local/bin/ham_verify.sh

DEV="ham0"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --dev) DEV="${2:?}"; shift 2 ;;
    *) echo "Unknown arg: $1" >&2; exit 2 ;;
  esac
done

echo "=== ham_verify dev=$DEV ==="
echo "--- ip link"
ip link show "$DEV" || true
echo
echo "--- ip addr"
ip -4 addr show dev "$DEV" || true
echo
echo "--- tc qdisc"
tc -s qdisc show dev "$DEV" || true
echo
echo "--- route sanity (examples)"
for ipt in 10.101.50.1 10.101.30.1; do
  echo "ip route get $ipt"
  ip route get "$ipt" || true
done
echo "=== end ==="
