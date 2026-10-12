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
# frognet_register_candidate.sh <role>
#
# Register THIS host as a candidate for a service role by writing <role>/capability
# into the transient, the SAME way the merge-end election reads it. The one role is
#   databasehost   — every FrogNet host registers (election gates on mysql_running)
#
# It is just: run the capability probe, write ONE tuple. A host without the role's
# server still registers; the blob's gate (mysql_running) makes it ineligible in
# scoring, so it is an inert candidate until the server is present.
#
# IMPORTANT: the write goes through frognet_tuples.put (SensorType=<role>,
# SensorName=SD:capability.<scope>) — NOT metric_upsert.sh, whose <fqdn>.<type>.<name>
# SensorName the election's frognet_tuples.get() does not parse as the "capability"
# variable (it would be invisible to scoring).
set -u
ROLE="${1:?role required: databasehost}"
# [ONE_ROLE_V1] databasehost is the one role. A leftover frognet-mediahost-advertise.timer on a node (the unit is
# retired) lands here and is refused, in its journal, instead of writing a capability row nothing reads.
[ "$ROLE" = "databasehost" ] || { echo "frognet_register_candidate: no role '$ROLE' (the one role is databasehost)" >&2; exit 2; }
PROBE="${FROGNET_PROBE:-/usr/local/bin/frognet_capability_probe.sh}"

[ -x "$PROBE" ] || { echo "frognet_register_candidate: missing probe $PROBE" >&2; exit 0; }
BLOB="$("$PROBE" 2>/dev/null)"
[ -n "$BLOB" ] || exit 0
echo "$BLOB" | python3 -c 'import sys,json; json.load(sys.stdin)' >/dev/null 2>&1 \
  || { echo "frognet_register_candidate: probe emitted non-JSON" >&2; exit 0; }

# [CORE_TUPLES_V1] core.frognet_tuples from /opt/frognet_semantic, the way the shipped code imports it (the old
# Communicator bundle dir is retired).
FROGNET_BLOB="$BLOB" FROGNET_ROLE="$ROLE" \
PYTHONPATH="/opt/frognet_semantic${PYTHONPATH:+:$PYTHONPATH}" /opt/frognet_semantic/venv/bin/python3 - <<'PY'
import os, sys, json
role = os.environ["FROGNET_ROLE"]
blob = json.loads(os.environ["FROGNET_BLOB"])
from core import frognet_tuples as T
# role_scope (host:<ip>:<role>) + own=False: a periodic oneshot refresh whose tuple
# must OUTLIVE the script, and whose SensorName must be role-distinct — a bare per-host
# scope would collide between roles (SensorName is the DB's unique key and omits
# SensorType), so two roles would overwrite each other to a single flip-flopping row.
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
        # Collapse deprecated bare/pid-keyed rows now (there is no reaper).
        try:
            T.prune_self_stale_capability(role, "capability", dbhost=dbhost)
        except Exception:
            pass
sys.exit(0 if ok else 1)
PY
