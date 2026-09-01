#!/usr/bin/env bash
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
# frognet_setup_advertisers.sh — make THIS host a registered candidate for the
# service roles, automatically and at every boot. Idempotent; safe to re-run.
#
# The unit files ship STATICALLY in /etc/systemd/system (frognet-dbhost-advertise.*
# and frognet-mediahost-advertise.*). This script ensures the probe + writer are on
# PATH, then ENABLES the timers (--now), so they fire at boot (OnBootSec=30) and every
# 60s thereafter. If a unit file is somehow absent it writes a fallback copy.
#
# Policy (current): databasehost = ALL machines; mediahost = ALL machines too — the
# election keeps only LAN media candidates (reach_plane split), so a host
# self-advertising mediahost can never win another node's media role. To take a host
# OUT of consideration for a role, just stop+disable its timer:
#     systemctl disable --now frognet-dbhost-advertise.timer       # opt out of DB
#     systemctl disable --now frognet-mediahost-advertise.timer    # opt out of media
set -u
[ "$(id -u)" = "0" ] || { echo "[advertisers] must run as root" >&2; exit 1; }
command -v systemctl >/dev/null 2>&1 || { echo "[advertisers] no systemd; skipping" >&2; exit 0; }
BUNDLE="${FROGNET_BUNDLE:-/etc/frognet_bundles/communicator}"
UNIT_DIR=/etc/systemd/system

# 1. ensure the probe + the generic writer are present on PATH
for f in frognet_capability_probe.sh frognet_register_candidate.sh; do
  if [ ! -x "/usr/local/bin/$f" ]; then
    for src in "$BUNDLE/$f" "$(dirname "$0")/$f" "./$f"; do
      [ -f "$src" ] && { install -m 0755 "$src" "/usr/local/bin/$f"; break; }
    done
  fi
done
[ -x /usr/local/bin/frognet_register_candidate.sh ] || {
  echo "[advertisers] frognet_register_candidate.sh missing; cannot continue" >&2; exit 1; }

# 2. [UNITS_ARE_PART_OF_THE_WORLD_V1] The units SHIP -- they are files in
#    etc/systemd/system, laid down by the world tar. This used to hold a
#    _fallback_unit() that wrote them inline when absent, under a comment saying
#    "the tar normally places these already". The tar did not place them: the
#    release excluded the unit directory entirely, so the inline copy was the only
#    definition that ever existed, and the release could not be told from a good
#    one. Their absence now says so.
for _u in frognet-dbhost-advertise frognet-mediahost-advertise; do
  for _ext in service timer; do
    [ -f "$UNIT_DIR/${_u}.${_ext}" ] || {
      echo "[advertisers] $UNIT_DIR/${_u}.${_ext} missing - it ships in the world tar; this release is incomplete" >&2
      exit 1; }
  done
done

# 3. enable + start the timers (boot-persistent via WantedBy=timers.target)
systemctl daemon-reload
for t in frognet-dbhost-advertise.timer frognet-mediahost-advertise.timer; do
  systemctl enable --now "$t" >/dev/null 2>&1 && echo "[advertisers] enabled $t" \
    || echo "[advertisers] WARN could not enable $t" >&2
done
# fire one of each now so the host is immediately a candidate
/usr/local/bin/frognet_register_candidate.sh databasehost || true
/usr/local/bin/frognet_register_candidate.sh mediahost   || true
echo "[advertisers] DONE — db + media registration enabled (start at boot, every 60s)."
