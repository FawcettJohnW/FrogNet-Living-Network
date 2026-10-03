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
record_walk - the record-driven discovery pass.

Replaces the seed-crawl-plus-vouch walk with the algorithm John specified:

  1. Identity comes from the transient DB: every host's frognet_echo tuple, read
     once, no probes (see host_records). That builds /etc/sentinels/hosts.
  2. Reachability is then asked of EVERY interface, unconditionally. For each
     known host's .2, go down each interface - LAN and tunnel alike - and put the
     one un-foolable question to the kernel: bound to THIS interface, does the .2
     answer :9009? Answer -> candidate, with its measured RTT and the kernel's
     actual next-hop. No answer -> skip THIS interface, never the dest. In
     parallel.

There is no parent_via, no seg_relay, no transit-map gate, no vouch freshness, no
existing-winner fastpath, and no getHosts - none of the inference layer that used
to talk the walk out of probing an interface that actually had the route. An
interface is asked or it isn't; it answers or it doesn't. That deletes the entire
class of "route should be there so I'll skip the probe" bug.

Candidates are written in promote()'s exact format (rtt|via|dev|onlink|src|kind|
host) into disc.CAND, so promote() - with WINNER_HYSTERESIS and the incumbency
hold - selects and installs unchanged.
"""
from __future__ import annotations

import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed

from .discovery import net_dot, dest24_of


def _default_via(dot2: str, dev: str) -> str:
    """The kernel's actual next-hop to `dot2` scoped to `dev`, or "" for on-link
    (tunnel or same-segment). Parsed from `ip route get ... oif <dev>`. This is
    the via promote installs, so the /24 winner rides the same path the probe
    proved. Best-effort: any failure -> "" (on-link), which is correct for tunnels
    and directly-attached segments."""
    try:
        r = subprocess.run(["ip", "-o", "route", "get", dot2, "oif", dev],
                           capture_output=True, text=True, timeout=2)
        toks = (r.stdout or "").split()
        if "via" in toks:
            return toks[toks.index("via") + 1]
    except Exception:
        pass
    return ""


def probe_interfaces(disc, records, interfaces, *, via_resolver=None,
                     max_workers=64, logger=None):
    """Populate disc.CAND by asking every interface about every known host's .2,
    in parallel. Returns the number of candidates produced.

    disc        : Discovery - uses .verify.measure_or_loop, .dev_src, .CAND,
                  .own_subnet, .log
    records     : [{"dot1","name",...}] from host_records.read_all()
    interfaces  : every active dev, tunnels included
    via_resolver: fn(dot2, dev) -> via ("" = on-link). Default: `ip route get`.
    """
    log = logger or disc.log
    viaf = via_resolver or _default_via
    own = getattr(disc, "own_subnet", "") or ""

    work = []   # (dot2, dest24, dev, name)
    for rec in records:
        d1 = rec.get("dot1") or ""
        name = rec.get("name") or ""
        if not d1:
            continue
        dest24 = dest24_of(d1)
        if dest24 == own:                 # our own subnet is directly attached
            continue
        d2 = net_dot(d1, 2)
        for dev in interfaces:
            work.append((d2, dest24, dev, name))

    if not work:
        log("RECORD_WALK cands=0 reason=no_remote_records")
        return 0

    def _probe(item):
        d2, dest24, dev, name = item
        # bound to dev, uses the interface's EXISTING routes (on-link, tunnel
        # subnet, or default via a relay) - SO_BINDTODEVICE means a stale winner
        # route on another dev can't answer for this one.
        v = disc.verify.measure_or_loop(d2, dev)
        return (item, v)

    workers = min(max_workers, len(work))
    log(f"RECORD_WALK start hosts={len(records)} ifaces={len(interfaces)} "
        f"cells={len(work)} workers={workers}")

    results = []
    with ThreadPoolExecutor(max_workers=workers,
                            thread_name_prefix="recordwalk") as ex:
        for fut in as_completed([ex.submit(_probe, w) for w in work]):
            results.append(fut.result())

    n = 0
    for (d2, dest24, dev, name), v in results:
        if v == "LOOP":
            log(f"IFACE_SKIP dest={dest24} dev={dev} target={d2} reason=loop")
            continue
        if not isinstance(v, float):
            log(f"IFACE_SKIP dest={dest24} dev={dev} target={d2} "
                f"reason=no_route_via_iface")
            continue
        via = viaf(d2, dev)
        onlink = 0
        src = disc.dev_src(dev) if hasattr(disc, "dev_src") else ""
        kind = "tunnel" if dev.startswith("wg") else "lan"
        rtt = int(round(v))
        disc.CAND[dest24].append(f"{rtt}|{via}|{dev}|{onlink}|{src}|{kind}|{name}")
        log(f"CANDIDATE dest={dest24} via={via or ''} dev={dev} onlink=0 "
            f"rtt={rtt} kind={kind} form=IFACE host={name}")
        n += 1

    log(f"RECORD_WALK done cells={len(results)} candidates={n}")
    return n
