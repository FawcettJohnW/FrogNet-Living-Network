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
# frognet_register_candidate.sh <role> [av_port]
#
# Register THIS host as a candidate for a service role by writing <role>/capability
# into the transient, the SAME way the merge-end election reads it. Role is one of:
#   databasehost   — every FrogNet host registers (election gates on mysql_running)
#   mediahost      — every FrogNet host registers; the election keeps only LAN
#                    candidates (reach_plane split), so a WAN host self-advertising
#                    is harmless — it never wins another node's media role.
#
# It is just: run the capability probe, write ONE tuple. A host without the role's
# server still registers; the blob's gate (mysql_running / ffmpeg+libvpx) makes it
# ineligible in scoring, so it is an inert candidate until the server is present.
#
# IMPORTANT: the write goes through frognet_tuples.put (SensorType=<role>,
# SensorName=SD:capability.<scope>) — NOT metric_upsert.sh, whose <fqdn>.<type>.<name>
# SensorName the election's frognet_tuples.get() does not parse as the "capability"
# variable (it would be invisible to scoring).
set -u
ROLE="${1:?role required: databasehost|mediahost}"
AV_PORT="${2:-${FROGNET_AV_PORT:-}}"
PROBE="${FROGNET_PROBE:-/usr/local/bin/frognet_capability_probe.sh}"
BUNDLE="${FROGNET_BUNDLE:-/etc/frognet_bundles/communicator}"

[ -x "$PROBE" ] || { echo "frognet_register_candidate: missing probe $PROBE" >&2; exit 0; }
BLOB="$("$PROBE" 2>/dev/null)"
[ -n "$BLOB" ] || exit 0
echo "$BLOB" | python3 -c 'import sys,json; json.load(sys.stdin)' >/dev/null 2>&1 \
  || { echo "frognet_register_candidate: probe emitted non-JSON" >&2; exit 0; }

FROGNET_BLOB="$BLOB" FROGNET_ROLE="$ROLE" FROGNET_AVPORT="$AV_PORT" \
PYTHONPATH="$BUNDLE${PYTHONPATH:+:$PYTHONPATH}" python3 - <<'PY'
import os, sys, json
role = os.environ["FROGNET_ROLE"]
blob = json.loads(os.environ["FROGNET_BLOB"])
ap = os.environ.get("FROGNET_AVPORT", "")
if role == "mediahost" and ap:           # stamp the A/V port this host actually serves
    try: blob["av_port"] = int(ap)
    except ValueError: blob["av_port"] = ap
import frognet_tuples as T
# role_scope (host:<ip>:<role>) + own=False: a periodic oneshot refresh whose tuple
# must OUTLIVE the script, and whose SensorName must be role-distinct — a bare per-host
# scope collides mediahost vs databasehost (SensorName is the DB's unique key and omits
# SensorType), so the two roles overwrite each other to a single flip-flopping row.
#
# [CAPABILITY_DUAL_WRITE_V1] Write to BOTH the deterministic control DB (the AUTHORITATIVE
# election input — every election method reads here) AND the elected data DB (a MIRROR so
# normal post-discovery services can read capability without touching _control). The data
# host floats, so this refresh keeps the mirror current on it too.
scope = T.role_scope(role)
ok = False
for dbhost in ("databasehost_control.frognet", "databasehost.frognet"):
    if T.put(role, "capability", scope, blob, dbhost=dbhost, own=False):
        ok = True
        # Collapse deprecated bare/pid-keyed rows now instead of at the 30-min reaper.
        try:
            T.prune_self_stale_capability(role, "capability", dbhost=dbhost)
        except Exception:
            pass
sys.exit(0 if ok else 1)
PY
