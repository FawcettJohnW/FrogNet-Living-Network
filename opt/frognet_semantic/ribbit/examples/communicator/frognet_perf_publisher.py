#!/usr/bin/env python3
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
"""
frognet_perf_publisher.py - publish THIS node's capability/perf tuple to BOTH databases.

WHAT IT WRITES
  The <role>/capability tuple (SensorType=<role>, SensorName=SD:capability.<role_scope>)
  carrying the self-contained capability blob from frognet_capability_probe.sh - static
  caps (mysql_running, ram, cores, disk, ffmpeg/libvpx, lan_ip, av_port) PLUS live perf
  (loadavg, temps_c) INSIDE the same blob, exactly where gather_candidates() and
  _capability_index() read it. This is the tuple the election scores over - NOT the
  System/Perf sensor metric_upsert.sh writes (the election never reads that).

WHERE IT WRITES - BOTH, every publish
  * databasehost_control.frognet - the deterministic highest-.1 CONTROL host. AUTHORITATIVE
    election input: it is the only DB whose identity is known early in a merge, so every
    election method reads candidates here.
  * databasehost.frognet - the elected, floating DATA host. A MIRROR only, so normal
    post-discovery services can read capability without touching _control.

WHEN IT WRITES - at boot, and IMMEDIATELY on every host change (not a timer)
  The data/control host floats on merge, and the transient holds no durable state, so on a
  switch the NEW host has none of this node's capability until it is re-published. So this
  module re-publishes on the ONE trigger-agnostic reconcile path (frognet_host_reset:
  host_reset_all / HostResetWatcher), and is primed once at boot. Rationale (the merge
  convergence substrate): on each merge every node re-publishes its perf, the control holder
  runs SELECT * over the capability tuples and elects; a host change itself triggers a
  confirmation merge, so an interim-wrong pick self-heals and the mesh settles on the
  authoritative answer in two or three merges. A publisher that only re-asserted on a slow
  timer would leave the new host blind for up to one interval and stall that convergence.

NOTE: capability is a FRESH PROBE, not resumable perm state, so hostReset() RE-PROBES and
re-publishes rather than refaulting perm -> transient like a WorkingMemory codex does.
"""
from __future__ import annotations

import json
import subprocess
from typing import Any, Dict, Optional

CONTROL_DBHOST = "databasehost_control.frognet"   # authoritative election input
DATA_DBHOST = "databasehost.frognet"              # mirror for normal consumers
DEFAULT_ROLES = ("databasehost", "mediahost")     # every host registers both; the blob's
                                                  # gate (mysql_running / ffmpeg+libvpx) makes
                                                  # it inert in scoring until the server exists
PROBE = "/usr/local/bin/frognet_capability_probe.sh"


class PerfPublisher:
    """Dual-writes this node's <role>/capability tuple to control AND data, for every role
    it can serve. hostReset() re-publishes; prime once at boot via publish()."""

    def __init__(self, roles=DEFAULT_ROLES, probe: str = PROBE,
                 control: str = CONTROL_DBHOST, data: str = DATA_DBHOST,
                 logger=lambda s: None):
        self.roles = tuple(roles)
        self.probe = probe
        self.control = control
        self.data = data
        self._log = logger

    # -- probe ----------------------------------------------------------------
    def _probe_blob(self) -> Optional[Dict[str, Any]]:
        """Run the capability probe once; return the self-contained blob (perf inside)."""
        try:
            out = subprocess.run([self.probe], capture_output=True, text=True, timeout=10)
            if out.returncode != 0 or not out.stdout.strip():
                self._log(f"PERF_PUB probe_fail rc={out.returncode}")
                return None
            return json.loads(out.stdout)
        except Exception as e:  # never raise into the reconcile
            self._log(f"PERF_PUB probe_exc={e}")
            return None

    # -- publish --------------------------------------------------------------
    def publish(self) -> Dict[str, Any]:
        """Probe once, then write the capability tuple for each role to BOTH databases.
        own=False: a refresh write that must OUTLIVE this call and age out by ts (matches
        the candidate advertiser). Returns a hostReset-style report."""
        import frognet_tuples as T  # late import so the box can swap the module
        rep: Dict[str, Any] = {"module": "perf_publisher", "reset": [], "written": [],
                               "consistency": "ok"}
        blob = self._probe_blob()
        if blob is None:
            rep["consistency"] = "probe_unavailable"
            return rep
        for role in self.roles:
            scope = T.role_scope(role)
            for dbhost in (self.control, self.data):   # BOTH: control authoritative, data mirror
                try:
                    ok = T.put(role, "capability", scope, blob, dbhost=dbhost, own=False)
                except Exception as e:
                    self._log(f"PERF_PUB put_exc role={role} dbhost={dbhost} err={e}")
                    continue
                if ok:
                    rep["written"].append(f"{role}@{dbhost}")
                    try:                               # collapse deprecated rows on that DB too
                        T.prune_self_stale_capability(role, "capability", dbhost=dbhost)
                    except Exception:
                        pass
                else:
                    self._log(f"PERF_PUB put_false role={role} dbhost={dbhost}")
        return rep

    # -- the UnREST reconcile hook -------------------------------------------
    def hostReset(self) -> Dict[str, Any]:
        """Trigger-agnostic reconcile (boot / merge / float): re-probe and re-publish to
        BOTH databases so the (possibly new) host carries our current capability for the
        next election round."""
        return self.publish()


def make_publisher(roles=DEFAULT_ROLES, logger=lambda s: None) -> PerfPublisher:
    """Construct a publisher; caller primes it (publish()) at boot and adds it to the
    HostResetWatcher module list so it re-publishes on every float."""
    return PerfPublisher(roles=roles, logger=logger)
