#!/usr/bin/env python3
"""
Copyright (C) 2016-2026 Fawcett Innovations LLC
SPDX-License-Identifier: GPL-2.0-only

This program is free software; you can redistribute it and/or modify it under
the terms of the GNU General Public License as published by the Free Software
Foundation; version 2 of the License, and no other version.

This program is distributed in the hope that it will be useful, but WITHOUT ANY
WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
PARTICULAR PURPOSE.  See the GNU General Public License for details.

See COPYRIGHT and LICENSE at the root of this tree.
------------------------------------------------------------------------------
AIConnect -- the receiver role. One per machine.

[AICONNECT_IN_THE_REGION_V1 - John 2026-11-11] "make it all memory and role based ... Why would I deliberately
disadvantage my architecture?"

A machine is an AIConnect receiver because its service says so, in memory: one cell in this machine's FrogTorch
RAM (frogtorch_ram.py, the region every machine runs),

    (AIConnect, receiver, <machine>) = {machine, region, build, cores, mem_available_mb, started}

written once with life="session". It lives exactly as long as this service's session: when the service stops, crashes
or its machine stops answering, the region removes it and every read held on it is told. A producer finds the
receivers by reading that cell in each region; nobody is asked, nobody answers.

A transfer is a WRITE into the receiver's memory: the producer writes each object as a tensor cell in this machine's
region, one cell per producer thread, overwritten object by object, and the region's acknowledgement is delivery.
Those cells are held by the producer's own sessions (life="session"), so they go when its links close.

What went: MachineReady re-asserted every interval, ConnectionRequest -> BulkReady -> "BulkReady = 0", the request
poll, the sharded socket receiver (SO_REUSEPORT processes, drain budgets, framing on a private stream). The region is
the receiver.
"""

from __future__ import annotations

import argparse
import os
import struct
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

_TREE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _TREE not in sys.path:
    sys.path.insert(0, _TREE)

BUILD = "aiconnect 20261112-r2 [AICONNECT_IN_THE_REGION_V1] [A_WRITE_IS_NOT_A_CONVERSATION_V1] [DELIVERY_IS_COUNTED_WHERE_IT_LANDS_V1]"
#: how long one held read on the receiver family is held; it is re-held at once, so this is not a poll interval
HOLD_S = 3600.0
SERVICE = "AIConnect"
VAR_RECEIVER = "receiver"

#: [PERSISTENT_LINK_NEEDS_FRAMING_V1] The length prefix every FrogTorch frame carries (tensor_plane, frogtorch_ram).
BULK_LEN_FMT = "!Q"
BULK_LEN_LEN = struct.calcsize(BULK_LEN_FMT)


def bulk_frame(n: int) -> bytes:
    """Length prefix for one frame of n bytes."""
    return struct.pack(BULK_LEN_FMT, int(n))


def machine_name() -> str:
    """This host's identity: what /usr/local/bin/getEth0Address says ([IDENTITY_COMES_FROM_getEth0Address_V1]).

    Every other component in the tree uses it, so it IS the node's identity. There is no fallback: a machine that
    cannot say what it is must not register as a receiver under a name nothing recognises.
    """
    r = subprocess.run(["/usr/local/bin/getEth0Address"], capture_output=True, text=True, timeout=10)
    ip = (r.stdout or "").strip()
    if r.returncode != 0 or not ip:
        raise RuntimeError("getEth0Address gave no address (rc=%s, stderr=%r). This machine cannot assert an "
                           "identity and must not start." % (r.returncode, (r.stderr or "").strip()[:200]))
    return ip


def parse_region(text: str) -> Tuple[str, int]:
    h, p = text.rsplit(":", 1)
    return h, int(p)


def this_machines_region(machine: str, regions: List[Tuple[str, int]]) -> Tuple[str, int]:
    """The FrogTorch RAM on this machine. A receiver's memory is its own machine's; there is no other choice."""
    mine = [hp for hp in regions if hp[0] == machine]
    if not mine:
        raise RuntimeError("no FrogTorch RAM on this machine (%s) among the announced regions %s -- is "
                           "frogtorch-ram running here? (systemctl status frogtorch-ram)"
                           % (machine, " ".join("%s:%d" % hp for hp in regions) or "(none)"))
    return mine[0]


class AIConnectService:
    def __init__(self, region: Optional[Tuple[str, int]] = None, machine: Optional[str] = None):
        from agent_workload.tuplespace.frogtorch_ram_client import discover_regions
        self.machine = machine or machine_name()
        self.region = region or this_machines_region(self.machine, discover_regions())
        self.cell_id = 0

    def value(self) -> Dict[str, Any]:
        from agent_workload.aiconnect.resources import mem_available_mb
        return {"machine": self.machine, "region": "%s:%d" % self.region, "build": BUILD,
                "cores": os.cpu_count(), "mem_available_mb": mem_available_mb(), "started": time.time()}

    def run(self) -> int:
        from agent_workload.tuplespace.frogtorch_ram_client import _Session
        held = _Session(*self.region)            # holds the receiver cell: it goes with this session
        mine = self.value()
        # [A_WRITE_IS_NOT_A_CONVERSATION_V1] written, not answered; the cell is found again by what it says
        held.send({"op": "cput", "service": SERVICE, "var": VAR_RECEIVER, "scope": self.machine,
                   "value": mine, "life": "session", "ack": False})

        def ours(rows):
            return [r for r in rows if r.get("scope") == self.machine
                    and (r.get("value") or {}).get("started") == mine["started"]]
        # A held read on the receiver family: it returns when the cells there change. If ours is not among them, it
        # was removed out from under us and this machine is no longer a receiver -- say so and stop. The first read
        # holds until the write above has landed; each later one holds until the family changes.
        watch = _Session(*self.region)
        rows = watch.call({"op": "cget", "service": SERVICE, "var": VAR_RECEIVER, "min_rows": 0,
                           "wait_s": 0.0}).get("rows", [])
        while not ours(rows):
            rows = watch.call({"op": "cget", "service": SERVICE, "var": VAR_RECEIVER, "min_rows": 0,
                               "wait_s": HOLD_S, "unlike": sorted(r["id"] for r in rows)}).get("rows", [])
        self.cell_id = int(ours(rows)[0]["id"])
        print("[AIConnect] %s %s: receiver in region %s:%d (cell %d)"
              % (BUILD, self.machine, self.region[0], self.region[1], self.cell_id), flush=True)
        while True:
            rows = watch.call({"op": "cget", "service": SERVICE, "var": VAR_RECEIVER, "min_rows": 0,
                               "wait_s": HOLD_S, "unlike": sorted(r["id"] for r in rows)}).get("rows", [])
            if not any(r.get("id") == self.cell_id for r in rows):
                raise RuntimeError("this machine's receiver cell %d is gone from region %s:%d"
                                   % (self.cell_id, self.region[0], self.region[1]))


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="AIConnect receiver: a session-held cell in this machine's FrogTorch RAM")
    p.add_argument("--region", default=None, help="host:port of this machine's FrogTorch RAM (default: the one "
                                                  "announced for this machine in the SystemRam)")
    p.add_argument("--machine", default=None, help="this machine's identity (default: getEth0Address)")
    a = p.parse_args(argv)
    svc = AIConnectService(region=parse_region(a.region) if a.region else None, machine=a.machine)
    try:
        return svc.run()
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
