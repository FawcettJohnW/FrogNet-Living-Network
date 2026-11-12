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
Resource readings, straight out of /proc. Nothing here is estimated.

[THE_DEPLOYED_PACKAGE_STANDS_ALONE_V1]
These live here rather than being imported from agent_workload/benchmark/ramp.py.
The AIConnect tarball installs one directory onto machines that may carry
nothing else of the workload, and an import reaching outside that directory
turns a deploy into a dependency hunt -- which is exactly what happened: the
producer died on ModuleNotFoundError for a module that was never shipped.

A package that is deployed on its own must be complete on its own.
"""

from __future__ import annotations

import os
import time
from typing import Any, Dict, List, Optional


def mem_available_mb() -> float:
    with open("/proc/meminfo") as f:
        for line in f:
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) / 1024.0
    raise RuntimeError("MemAvailable not in /proc/meminfo")


def loadavg_per_core() -> float:
    with open("/proc/loadavg") as f:
        one = float(f.read().split()[0])
    return one / max(1, os.cpu_count() or 1)


def cpu_idle_pct(sample_s: float = 0.5) -> float:
    """Idle percentage over a real sample window, not an instantaneous guess."""
    def snap():
        with open("/proc/stat") as f:
            parts = [int(x) for x in f.readline().split()[1:]]
        return sum(parts), parts[3]          # total, idle
    t0, i0 = snap()
    time.sleep(sample_s)
    t1, i1 = snap()
    dt = t1 - t0
    return 100.0 * (i1 - i0) / dt if dt else 100.0


def disk_free_mb(path: str) -> float:
    st = os.statvfs(path)
    return st.f_bavail * st.f_frsize / (1024.0 * 1024.0)


def net_counters() -> Dict[str, Dict[str, int]]:
    """Per-interface errors, drops and bytes.

    Every interface is read -- a node carries traffic on all of them, and
    naming one misses the drop when it happens elsewhere.

    WireGuard is flagged separately. A wg drop is the OVERLAY: tunnel
    congestion, rekeying, an unreachable peer. An eth or wlan drop is the
    underlying link out of capacity. Summed together a tunnel problem looks
    like a NIC problem and the other way round, and the point of a ramp is to
    say which resource bent the curve.

    lo is excluded; loopback drops mean something else again.
    """
    out: Dict[str, Dict[str, int]] = {}
    with open("/proc/net/dev") as f:
        f.readline(); f.readline()
        for line in f:
            name, _, rest = line.partition(":")
            name = name.strip()
            if not name or name == "lo":
                continue
            v = [int(x) for x in rest.split()]
            out[name] = {
                "rx_bytes": v[0], "rx_errs": v[2], "rx_drop": v[3],
                "tx_bytes": v[8], "tx_errs": v[10], "tx_drop": v[11],
                "bad": v[2] + v[3] + v[10] + v[11],
                "is_wg": name.startswith("wg"),
            }
    return out


def softnet_drops() -> int:
    """Packets the kernel dropped from the per-CPU receive backlog.

    [A_DROP_IS_NOT_A_CARD_ERROR_V1]
    /proc/net/softnet_stat column 2 is the backlog overflow count: the NIC
    delivered the packet and softirq processing could not keep up. It is a CPU
    and packet-RATE symptom, not a wire symptom, and it is invisible in
    /proc/net/dev -- so a workload that produces many small packets can look
    like a failing card while the card is fine.

    Relevant here because bulk moved 38.34 MB/s with zero physical counts
    while semantic moved 3.19 MB/s and produced 45 on wlan1. Volume was not
    the variable; packet rate and packet size were.
    """
    total = 0
    try:
        with open("/proc/net/softnet_stat") as f:
            for line in f:
                cols = line.split()
                if len(cols) > 1:
                    total += int(cols[1], 16)
    except (OSError, ValueError):
        return 0
    return total


def link_capacity() -> Dict[str, Dict[str, Any]]:
    """Negotiated link speed and duplex per physical interface, from sysfs.

    [A_THROUGHPUT_NUMBER_NEEDS_A_LINE_RATE_V1]
    A ramp that reports 12.19 MB/s and stops improving cannot say whether the
    mesh ran out of capacity or the cable did, and those are opposite
    conclusions. 12.19 MB/s is 102 Mbit/s; on a gigabit link that is a tenth of
    the wire and the ceiling is software, on a 100 Mbit link it IS the wire and
    no amount of tuning moves it. The number was never printed, so every
    plateau in this workload has been ambiguous.

    Speed is unreadable for virtual interfaces (wg, bridges, veth) and for a
    down link; those report None rather than a guess. [NO FALLBACKS]
    """
    out: Dict[str, Dict[str, Any]] = {}
    try:
        names = sorted(os.listdir("/sys/class/net"))
    except OSError:
        return out
    for name in names:
        if name == "lo":
            continue
        info: Dict[str, Any] = {"speed_mbit": None, "duplex": None,
                                "operstate": None}
        info["mtu"] = None
        for key, fn in (("speed_mbit", "speed"), ("duplex", "duplex"),
                        ("operstate", "operstate"), ("mtu", "mtu")):
            try:
                with open("/sys/class/net/%s/%s" % (name, fn)) as f:
                    raw = f.read().strip()
                info[key] = int(raw) if fn in ("speed", "mtu") else raw
            except (OSError, ValueError):
                pass
        if info["speed_mbit"] is not None and info["speed_mbit"] < 0:
            info["speed_mbit"] = None      # -1 means "not known", not a speed
        # Raw signalling rate. NOT what an application can move.
        info["max_mb_s"] = (info["speed_mbit"] / 8.0 / 1.048576
                            if info["speed_mbit"] else None)
        # [PAYLOAD_IS_NOT_LINE_RATE_V1] The achievable TCP payload rate.
        #
        # Comparing application bytes against the signalling rate overstates
        # efficiency by about 5% at a 1500 MTU, and that is exactly the size
        # of the gap that made a measured 114.18 MB/s read as "95.8% of the
        # wire" -- a number above what 1500-byte Ethernet can carry.
        #
        # Per frame at MTU m: the wire carries m + 18 (Ethernet header and
        # FCS) + 8 (preamble/SFD) + 12 (inter-frame gap), and of that only
        # m - 40 is TCP payload (20 IP + 20 TCP), or m - 52 once timestamps
        # are on, which they are by default on Linux. Use 52: a ratio that
        # flatters the result is the failure being fixed.
        m = info.get("mtu")
        if info["max_mb_s"] and m and m > 60:
            info["max_payload_mb_s"] = info["max_mb_s"] * (m - 52.0) / (m + 38.0)
        else:
            info["max_payload_mb_s"] = None
        out[name] = info
    return out


def carrier_of(per_iface: Dict[str, Dict[str, int]]) -> Optional[str]:
    """The interface that transmitted the most during the run.

    [SAY_WHICH_INTERFACE_CARRIED_IT_V1] The workload's own answer to "which
    wire was this measured on", rather than whichever one the header happened
    to name from the routing table.
    """
    if not per_iface:
        return None
    return max(per_iface, key=lambda n: per_iface[n]["tx"])


def iface_table(per_iface: Dict[str, Dict[str, int]], top: int = 6) -> str:
    rows = sorted(per_iface.items(), key=lambda kv: -kv[1]["tx"])[:top]
    return "  ".join("%s%s tx=%.1fMB rx=%.1fMB"
                     % (n, "(overlay)" if v["is_wg"] else "",
                        v["tx"] / 1048576.0, v["rx"] / 1048576.0)
                     for n, v in rows)


def wire_overhead_ratio(payload_bytes: int, wire_bytes: int) -> Optional[float]:
    """wire / payload for a run. The one self-consistency check available.

    [THE_INTERFACE_COUNTERS_ARE_THE_WITNESS_V1]
    The application counts what it handed to sendall; /proc/net/dev counts
    what the interface actually moved. The second must EXCEED the first --
    every payload byte carries headers -- and by roughly 3-4% at a 1500 MTU.

    A ratio at or below 1.0 means the bytes did not all cross the interface
    being credited: another path carried them, or the interface being
    compared against is the wrong one. That is the check that turns "95% of
    the wire, which is impossible" into an answer.
    """
    if payload_bytes <= 0 or wire_bytes <= 0:
        return None
    return float(wire_bytes) / float(payload_bytes)


def route_iface(dest_ip: str) -> Optional[str]:
    """Which interface the kernel would send `dest_ip` out of.

    [A_PEER_IS_ALSO_A_PATH_V1]
    Two peers that behave completely differently under the same load are not
    necessarily two different peers -- they may be two different paths. On
    2026-09-05, 10.170.170.1 owned 100% of a run's TimeoutErrors while
    10.250.250.1 had none, at matching receiver ages and matching builds, and
    nothing in the run said whether the two were even leaving the box by the
    same NIC. The peer address was printed; the route never was.

    Read from /proc/net/route by longest-prefix match. No `ip` binary, no
    subprocess -- the file is the kernel's own table. Returns None rather than
    a guess when nothing matches. [NO FALLBACKS]
    """
    try:
        packed = [int(x) for x in dest_ip.split(".")]
        if len(packed) != 4:
            return None
        # /proc/net/route holds little-endian hex, so build the same way.
        dest = (packed[3] << 24) | (packed[2] << 16) | (packed[1] << 8) | packed[0]
    except (ValueError, AttributeError):
        return None

    best_iface, best_bits = None, -1
    try:
        with open("/proc/net/route") as f:
            f.readline()
            for line in f:
                cols = line.split()
                if len(cols) < 8:
                    continue
                try:
                    net = int(cols[1], 16)
                    mask = int(cols[7], 16)
                except ValueError:
                    continue
                if (dest & mask) != net:
                    continue
                bits = bin(mask).count("1")
                if bits > best_bits:
                    best_iface, best_bits = cols[0], bits
    except OSError:
        return None
    return best_iface


def peer_routes(peers) -> Dict[str, Optional[str]]:
    return {p: route_iface(p) for p in peers}


def link_summary(caps: Dict[str, Dict[str, Any]]) -> str:
    """One line for the run header: what each real link can physically do."""
    parts = []
    for name in sorted(caps):
        c = caps[name]
        if c.get("speed_mbit") is None:
            continue
        parts.append("%s %dMbit/%s=%.1f MB/s"
                     % (name, c["speed_mbit"], c.get("duplex") or "?",
                        c["max_mb_s"]))
    return "  ".join(parts) if parts else "(no interface reports a link speed)"


#: Share of the run's total bytes an interface must carry before its drops are
#: attributed to the run.
_CARRYING_SHARE = 0.10

#: Absolute floor as well, so a tiny run cannot promote a bystander.
_CARRYING_BYTES = 8 << 20          # 8 MB

#: Drops per MB carried, above which a CARRYING interface is backing up.
_DROPS_PER_MB = 1.0


def net_delta(before: Dict[str, Dict[str, int]]) -> Dict[str, Any]:
    """Errors/drops and bytes since `before`, overlay split from physical.

    [DROPS_MUST_BE_ATTRIBUTABLE_V1]
    The first version stopped the ramp on ANY nonzero drop on ANY interface. On
    a real node that fires immediately and means nothing: a measured run
    stopped at 4 drops on wlan1 having transferred ZERO bytes -- ambient
    wireless noise on an interface the workload never touched, reported as
    network saturation.

    Two conditions now, and both must hold before an interface is blamed:

      it carried this run's traffic -- at least _CARRYING_BYTES, so an idle or
      unrelated interface cannot trip the ramp; and

      its drops are material RELATIVE TO WHAT IT CARRIED -- more than
      _DROPS_PER_MB per megabyte. A handful of drops across a gigabyte is a
      working link; the same count across a hundred kilobytes is not.

    Counts on non-carrying interfaces are still reported, as `ambient`, because
    they are worth seeing. They just do not stop anything.
    """
    now = net_counters()

    # Total first: an interface is judged against what the RUN moved, not
    # against a fixed number. Without this a bystander carrying a trickle
    # always exceeds a per-MB rate -- measured as the ramp stopping on
    # "wlan1+4/1.0MB" while 20 MB/s of real traffic crossed another interface
    # cleanly, reported as a network limit when wlan1 was carrying background
    # wifi noise and none of the workload.
    deltas = {}
    total_bytes = 0
    for name, c in now.items():
        b = before.get(name)
        if b is None:
            continue
        d_bytes = (c["rx_bytes"] - b["rx_bytes"]) + (c["tx_bytes"] - b["tx_bytes"])
        # [A_DROP_IS_NOT_A_CARD_ERROR_V1] Keep the four counters apart. `bad`
        # sums rx_errs + rx_drop + tx_errs + tx_drop, and those are four
        # different faults:
        #   rx_errs  -- the wire: CRC, framing, a genuinely bad receive
        #   tx_errs  -- the transmitter: carrier loss, aborted send
        #   tx_drop  -- the QDISC overflowed. Congestion, not hardware.
        #   rx_drop  -- the kernel discarded a delivered packet: backlog full,
        #               no socket, wrong protocol. Also not hardware.
        # Reporting the sum as "errors/drops on physical interfaces" makes a
        # saturated queue and a failing card the same message, and they lead
        # to opposite work.
        parts = {k: c[k] - b[k] for k in
                 ("rx_errs", "rx_drop", "tx_errs", "tx_drop")}
        deltas[name] = (c["bad"] - b["bad"], d_bytes, c["is_wg"], parts)
        total_bytes += d_bytes

    wg_bad = phys_bad = 0
    wg_bytes = phys_bytes = 0
    culprits: List[str] = []
    ambient: List[str] = []
    breakdown: Dict[str, Dict[str, int]] = {}
    for name, (d_bad, d_bytes, is_wg, parts) in deltas.items():
        # Carrying means BOTH a real share of this run and a real volume.
        carrying = (d_bytes >= _CARRYING_BYTES
                    and d_bytes >= _CARRYING_SHARE * total_bytes)
        material = carrying and d_bad > _DROPS_PER_MB * (d_bytes / 1048576.0)
        c = {"is_wg": is_wg}

        if is_wg:
            wg_bytes += d_bytes
            if material:
                wg_bad += d_bad
        else:
            phys_bytes += d_bytes
            if material:
                phys_bad += d_bad

        if d_bad > 0:
            breakdown[name] = dict(parts, bytes=d_bytes)
            # Name WHICH counter moved, in the entry itself.
            which = " ".join("%s=%d" % (k, v) for k, v in sorted(parts.items())
                             if v)
            entry = "%s+%d/%.1fMB [%s]" % (name, d_bad,
                                           d_bytes / 1048576.0, which)
            (culprits if material else ambient).append(entry)

    hw = sum(p.get("rx_errs", 0) + p.get("tx_errs", 0)
             for p in breakdown.values())
    queue = sum(p.get("rx_drop", 0) + p.get("tx_drop", 0)
                for p in breakdown.values())
    # [SAY_WHICH_INTERFACE_CARRIED_IT_V1] The per-interface tx/rx split for
    # EVERY interface, overlay included. One summed number could not answer
    # the two questions it was built for:
    #   bulk    reported wire/payload 1.157 -- far above the ~1.06 that 1500-
    #           byte framing costs, because the sum mixes tx with rx (ACKs)
    #           and every other flow on the box.
    #   semantic reported 0.011 -- 171.2 MB sent, 1.9 MB counted -- because
    #           phys_bytes EXCLUDES wg*, so traffic riding the overlay was
    #           subtracted from the witness rather than attributed to it.
    # A sender is witnessed by TX, per interface, named.
    per_iface = {}
    for name, c in now.items():
        b = before.get(name)
        if b is None:
            continue
        tx = c["tx_bytes"] - b["tx_bytes"]
        rx = c["rx_bytes"] - b["rx_bytes"]
        if tx or rx:
            per_iface[name] = {"tx": tx, "rx": rx, "is_wg": c["is_wg"]}

    return {"per_iface": per_iface,
            "wg_bad": wg_bad, "phys_bad": phys_bad,
            "wg_bytes": wg_bytes, "phys_bytes": phys_bytes,
            "culprits": culprits, "ambient": ambient,
            "counter_breakdown": breakdown,
            "hw_errors": hw, "queue_drops": queue}
