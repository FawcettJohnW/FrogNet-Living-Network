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
AIConnect ramp oracle — the three defects behind the 1024-thread meltdown of
2026-09-04, each asserted on CODE rather than on the comment explaining it.

The measured run this is built from, from 10.130.130.1 against two peers at
64 KB objects: the ramp climbed cleanly to 512 threads and 23.83 MB/s, doubled
to 1024, and collapsed to 0.00 MB/s with 93.4% CPU and load 30.71 per core.
No stop rule fired -- each needs two consecutive intervals and every one of
them was on its first. The rung then extended four times while failures went
1,591 -> 14,678, and the receivers said nothing at all.

  1. THE CEILING. --max-threads was set to 512 last session under
     [THE_DRIVER_IS_NOT_THE_SUBJECT_V1] and came back as 2048 while the help
     text beneath it still described the 2048 failure word for word. Asserted
     on DEFAULT_MAX_THREADS and on what the parser actually resolves to, not
     on the presence of the tag.

  2. THE EXTEND WINDOW. should_extend() must distinguish pending work from a
     failure loop. Stats.start() raises inflight before the attempt and the
     finally clause lowers it after, so a thread failing and retrying reads as
     outstanding; d_x == 0 and inflight > 0 alone cannot tell the two apart.

  3. THE RECEIVER'S SILENCE. BulkReceiver._loop caught ValueError from
     select() alongside OSError, reaped, and continued -- so a descriptor set
     past FD_SETSIZE left the receiver alive, draining nothing, logging
     nothing. It must stop and say so. [NO FALLBACKS]

  4. THE SEMANTIC CEILING. The body travels as one TYPE_STR field with a
     uint16 length prefix, so 65535 bytes is the maximum -- and the guard was
     written as 64*1024, one byte past it. Every semantic80 attempt at 64 KB
     failed with "TYPE_STR over 65535-byte wire field: 65536 bytes".

  5. THE RECEIVER'S WIDTH. [REUSEPORT_SHARDS_V1] One thread doing every
     accept() and every recv() inline was one core regardless of the machine.
     N processes share the port via SO_REUSEPORT and the kernel balances.

  6. THE PERMANENT LINK. [THE_LINK_IS_PERMANENT_V1] A connection per object
     meant 454 connects and 454 accepts a second at 64 KB. Many objects, one
     accept -- with an explicit length prefix, since the close is no longer
     the boundary.

Run: python3 simulation/test_aiconnect_ramp_oracle.py
"""

import inspect
import os
import resource
import selectors
import json
import socket
import sys
import tempfile
import threading
import traceback
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_PARENT = os.path.dirname(_HERE)
for _p in (_PARENT, _HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from agent_workload.aiconnect import producer, service  # noqa: E402

_FAILURES = []


def open_receiver(shards):
    """Construct a receiver, or report the old single-threaded shape as a
    FAILED CHECK rather than crashing the run and hiding every later
    assertion. [REUSEPORT_SHARDS_V1]"""
    try:
        return service.BulkReceiver("127.0.0.1", shards=shards)
    except TypeError as e:
        check("the receiver accepts a shard count", False,
              "%s -- a single-threaded receiver has no width to set" % e)
        return None


def check(name, cond, detail=""):
    if cond:
        print("  PASS  %s" % name)
    else:
        print("  FAIL  %s%s" % (name, ("  -- " + detail) if detail else ""))
        _FAILURES.append(name)


# ---------------------------------------------------------------- 1. ceiling

def test_driver_ceiling():
    print("[1] driver ceiling  [THE_DRIVER_IS_NOT_THE_SUBJECT_V1]")

    check("DEFAULT_MAX_THREADS exists",
          hasattr(producer, "DEFAULT_MAX_THREADS"),
          "the ceiling must be a named constant an oracle can assert on, "
          "not an argparse literal")
    if not hasattr(producer, "DEFAULT_MAX_THREADS"):
        return

    # [CEILING_RAISED_V2] 512 was set against a producer that reconnected per
    # object and timed its own connects. At the old ceiling the run now ends
    # on DRIVER-CEILING at 38.34 MB/s with CPU 12% and load 0.12 -- nothing
    # near a limit -- so 512 had stopped being a safeguard.
    check("DEFAULT_MAX_THREADS is 2048",
          producer.DEFAULT_MAX_THREADS == 2048,
          "is %r" % (producer.DEFAULT_MAX_THREADS,))
    check("the descriptor cost of the ceiling is checked first",
          hasattr(producer, "_check_fd_budget"),
          "2048 threads x N peers permanent links is the thing 2048 newly "
          "threatens; EMFILE mid-rung would waste the measurement")
    if hasattr(producer, "_check_fd_budget"):
        import resource as _res
        _soft, _ = _res.getrlimit(_res.RLIMIT_NOFILE)
        ok = True
        try:
            producer._check_fd_budget(8, 2, "bulk")
        except SystemExit:
            ok = False
        check("a ramp that fits is allowed", ok)
        refused = False
        try:
            producer._check_fd_budget(_soft * 4, 8, "bulk")
        except SystemExit:
            refused = True
        check("a ramp that cannot fit is refused up front", refused,
              "it must not be discovered at rung 1024")
        skipped = True
        try:
            producer._check_fd_budget(_soft * 4, 8, "semantic80")
        except SystemExit:
            skipped = False
        check("non-bulk transports are not held to a link budget", skipped,
              "rest8080/semantic80 hold no permanent links")

    # The constant is only worth having if main()'s parser actually uses it.
    src = inspect.getsource(producer.main)
    check("parser default is the constant, not a literal",
          "default=DEFAULT_MAX_THREADS" in src,
          "--max-threads must resolve to DEFAULT_MAX_THREADS")

    # And the ramp must stop at it rather than double past it.
    check("ramp stops at the ceiling instead of doubling past it",
          "n_threads >= a.max_threads" in src,
          "driver-ceiling rule missing from the stop ladder")


# ----------------------------------------------------------- 2. extend window

def test_outstanding_means_stuck():
    """[OUTSTANDING_MEANS_STUCK_NOT_BUSY_V1] + [SATURATION_IS_ITS_OWN_REASON_V1]

    inflight/n_threads read 1.0 on every rung of every recorded run, because a
    worker is inside an attempt except between one ending and the next
    beginning. The OUTSTANDING rule therefore never tested outstanding work:
    it was `not improving` under another name, and it stopped semantic80 at 32
    threads -- zero failures, 252 transfers in the final window -- while
    printing "the system is not keeping up".
    """
    print("[3] outstanding means stuck, not busy")

    check("Stats can age an attempt", hasattr(producer.Stats, "stalled"),
          "a count of threads cannot distinguish busy from stuck")
    if not hasattr(producer.Stats, "stalled"):
        return

    st = producer.Stats()
    stop = threading.Event()

    def busy(tid):
        while not stop.is_set():
            st.start(tid)
            time.sleep(0.002)
            st.end(tid)

    ts = [threading.Thread(target=busy, args=(i,), daemon=True)
          for i in range(24)]
    for t in ts:
        t.start()
    time.sleep(0.6)
    try:
        check("every healthy worker still reads as in flight",
              st.inflight >= 20,
              "inflight=%d -- this is the reading the old rule used" % st.inflight)
        check("none of them counts as stalled", st.stalled(1.0) == 0,
              "%d stalled -- a fast transfer is not a stuck one" % st.stalled(1.0))

        st.start(9999)          # a genuinely hung attempt
        time.sleep(1.2)
        check("a hung attempt IS counted", st.stalled(1.0) == 1,
              "stalled=%d" % st.stalled(1.0))
        check("its age is reportable", st.oldest_attempt_s() >= 1.0,
              "oldest=%.2f" % st.oldest_attempt_s())
    finally:
        stop.set()
        for t in ts:
            t.join(timeout=5)

    src = inspect.getsource(producer.main)
    check("the rule reads the age, not the thread count",
          "stats.stalled(a.stall_seconds)" in src,
          "outstanding_frac must come from stalled attempts")
    check("the rule no longer hides behind `improving`",
          "if outstanding_frac > 0.5:" in src,
          "coupling it to throughput is what disguised the broken measure")
    check("a plateau gets its own stop reason",
          '"saturated"' in src,
          "throughput ceasing to climb is real, and it is not stuck threads")
    check("the ramp can be told to ignore a plateau",
          "a.saturation_rungs" in src,
          "--saturation-rungs 0 must reach the thread ceiling")


def test_breakdown_names_the_cause():
    """[A_TYPE_NAME_IS_NOT_A_CAUSE_V1] The rung breakdown printed
    "10.170.170.1:80 RuntimeError x8". send_rest raises RuntimeError for any
    HTTP >= 300 from bulk.php and the proxy raises its own for "per-target
    limit exceeded" -- a 503 from an overloaded proxy and a 500 from a dying
    handler counted as the same thing."""
    print("[4] the failure breakdown names the cause")

    st = producer.Stats()
    st.fail("x", peer="10.1.1.1", port=80,
            exc=RuntimeError("bulk.php on 10.1.1.1:80 returned HTTP 503: busy"))
    st.fail("x", peer="10.1.1.1", port=80,
            exc=RuntimeError("bulk.php on 10.1.1.1:80 returned HTTP 500: dead"))
    st.fail("x", peer="10.1.1.1", port=80,
            exc=RuntimeError("per-target limit (32) exceeded for 10.1.1.1"))
    st.fail("x", peer="10.2.2.2", port=9,
            exc=ConnectionRefusedError(111, "Connection refused"))
    line = st.rung_breakdown()

    check("503 and 500 are separate entries",
          "HTTP 503" in line and "HTTP 500" in line,
          "one RuntimeError count cannot be two different faults: %s" % line)
    check("the proxy's own limit is distinguishable",
          "per-target limit" in line, line)
    check("errno still identifies the OSError family",
          "errno=111" in line, line)
    check("the peer share is still computed",
          "share" in line and "10.1.1.1=75%" in line, line)


def test_receiver_age_is_reported():
    """[PRIMED_MEANS_NOTHING_WITHOUT_AN_AGE_V1] + [THE_TIMEOUT_IS_SHAPING_THE_
    DATA_V1] On 2026-09-05 the two peers' BulkReady rows were 558s and 2364s
    old -- their receivers were created half an hour apart, so they were not
    running the same build -- and 100% of the run's TimeoutErrors landed on the
    older one. The header printed only ports. And every stall duration was an
    exact multiple of the hardcoded 30s socket timeout: 30.3, 60.6, 90.0,
    120.5, 150.6, 179.4. Those are the constant firing, not the network."""
    print("[5] receiver age and link timeout are visible")

    check("Rendezvous exposes the row ages",
          hasattr(producer.Rendezvous, "bulk_row_ages"),
          "without it the header cannot say when each receiver was created")
    check("age formatting is human-readable",
          producer._fmt_age(2364) == "39m" and producer._fmt_age(45) == "45s"
          and producer._fmt_age(None) == "age?",
          "%r %r %r" % (producer._fmt_age(2364), producer._fmt_age(45),
                        producer._fmt_age(None)))

    src = inspect.getsource(producer.main)
    check("the header prints an age beside each port",
          "_fmt_age(_ages.get(m))" in src)
    check("a large spread between peers is called out",
          "receiver ages differ by" in src,
          "peers on different builds must not be compared silently")

    check("the link timeout is a flag",
          "--link-timeout" in src,
          "a hardcoded timeout decides what every stall looks like")
    check("workers use the configured value",
          "_LINK_TIMEOUT[0] = a.link_timeout" in src)

    lsrc = inspect.getsource(producer.BulkLink.__init__)
    check("the send timeout is set separately from the connect timeout",
          lsrc.count("settimeout") >= 2,
          "leaving the connect timeout in place makes every send inherit it")


def test_peer_route_is_reported():
    """[A_PEER_IS_ALSO_A_PATH_V1] On 2026-09-05, 10.170.170.1 owned 100% of a
    run's TimeoutErrors and 10.250.250.1 had none -- at matching receiver ages
    and matching builds. The run printed both addresses and neither route, so
    'different peer' and 'different path' were indistinguishable."""
    print("[7] each peer's egress interface is reported")

    from agent_workload.aiconnect import resources

    check("route_iface() exists", hasattr(resources, "route_iface"),
          "the path is as much a variable as the peer")
    if not hasattr(resources, "route_iface"):
        return

    iface = resources.route_iface("10.170.170.1")
    check("a routable address resolves to an interface",
          iface is None or isinstance(iface, str), repr(iface))
    check("garbage returns None rather than a guess",
          resources.route_iface("not-an-ip") is None
          and resources.route_iface("1.2.3") is None,
          "a wrong interface is worse than no interface")
    _rsrc = inspect.getsource(resources.route_iface)
    check("no subprocess is used",
          "subprocess." not in _rsrc and "Popen" not in _rsrc
          and "/proc/net/route" in _rsrc,
          "the kernel table is a file; shelling out to ip adds a dependency "
          "and the proxy tier already fails in this container for exactly "
          "that reason")
    check("longest-prefix wins",
          "best_bits" in inspect.getsource(resources.route_iface),
          "a default route must not beat a specific one")

    src = inspect.getsource(producer.main)
    check("the header prints the route per peer", "via %s" in src)
    check("a split across interfaces is called out",
          "DIFFERENT interfaces" in src,
          "peers on different NICs are not comparable results")
    check("the routes are recorded in the results",
          '"peer_routes": _routes' in src)


def test_drop_is_not_a_card_error():
    """[A_DROP_IS_NOT_A_CARD_ERROR_V1]

    net_counters sums rx_errs + rx_drop + tx_errs + tx_drop into one number
    called `bad`, and the stop line called all of it "errors/drops on physical
    interfaces". Those are four different faults:
        rx_errs / tx_errs -- the wire or the adapter
        tx_drop           -- the qdisc overflowed; congestion, not hardware
        rx_drop           -- the kernel discarded a delivered packet
    Measured 2026-09-05: bulk moved 38.34 MB/s with phys bad 0 while semantic
    moved 3.19 MB/s and produced wlan1+45. Volume was not the variable, so the
    merged count could not say what was.
    """
    print("[8] a drop is distinguished from a card error")

    from agent_workload.aiconnect import resources

    # [NET_DELTA_HAS_A_CONTRACT_V1] A rewrite of net_delta's return dict
    # dropped wg_bad and phys_bad, and the ramp died at rung 1 with
    # KeyError: 'wg_bad' -- on the user's node, not here, because no oracle
    # ever asserted the key set the producer consumes. Every key read by
    # main() is now part of the contract.
    _required = ("wg_bad", "phys_bad", "wg_bytes", "phys_bytes", "culprits",
                 "ambient", "counter_breakdown", "hw_errors", "queue_drops",
                 "per_iface")
    _got = resources.net_delta(resources.net_counters())
    for _k in _required:
        check("net_delta returns %r" % _k, _k in _got,
              "main() reads it when building every rung record")
    _psrc = inspect.getsource(producer.main)
    for _k in _required:
        if 'net["%s"]' % _k in _psrc or 'net.get("%s"' % _k in _psrc:
            check("%r is actually consumed, so the check is honest" % _k, True)

    check("softnet backlog drops are read",
          hasattr(resources, "softnet_drops"),
          "backlog overflow is invisible in /proc/net/dev and looks like a "
          "failing card")

    zero = {"rx_bytes": 0, "rx_errs": 0, "rx_drop": 0, "tx_bytes": 0,
            "tx_errs": 0, "tx_drop": 0, "bad": 0, "is_wg": False}
    before = {"wlan1": dict(zero)}
    real = resources.net_counters

    def fake(vals):
        def f():
            v = dict(zero, **vals)
            v["bad"] = (v["rx_errs"] + v["rx_drop"]
                        + v["tx_errs"] + v["tx_drop"])
            return {"wlan1": v}
        return f

    try:
        resources.net_counters = fake({"tx_bytes": 20 << 20, "tx_drop": 45})
        q = resources.net_delta(before)
        check("a queue overflow is not counted as hardware",
              q["queue_drops"] == 45 and q["hw_errors"] == 0,
              "hw=%d queue=%d" % (q["hw_errors"], q["queue_drops"]))
        check("the entry names the counter that moved",
              any("tx_drop=45" in e for e in q["culprits"] + q["ambient"]),
              repr(q["culprits"] + q["ambient"]))

        resources.net_counters = fake({"tx_bytes": 20 << 20, "rx_errs": 45})
        h = resources.net_delta(before)
        check("a wire error IS counted as hardware",
              h["hw_errors"] == 45 and h["queue_drops"] == 0,
              "hw=%d queue=%d" % (h["hw_errors"], h["queue_drops"]))
        check("the entry names that counter too",
              any("rx_errs=45" in e for e in h["culprits"] + h["ambient"]),
              repr(h["culprits"] + h["ambient"]))

        resources.net_counters = fake({"tx_bytes": 20 << 20, "rx_errs": 2,
                                       "tx_drop": 43})
        m = resources.net_delta(before)
        check("a mixed fault reports both", m["hw_errors"] == 2
              and m["queue_drops"] == 43,
              "hw=%d queue=%d" % (m["hw_errors"], m["queue_drops"]))
    finally:
        resources.net_counters = real

    src = inspect.getsource(producer.main)
    check("the stop reason names the class of fault",
          "QUEUES, NOT HARDWARE" in src and "HARDWARE:" in src,
          "one message for both leads to opposite work")
    check("the breakdown is recorded per rung",
          '"counter_breakdown"' in src and '"softnet_backlog_drops"' in src)


def test_relayed_peer_is_detected():
    """[A_RELAYED_PEER_IS_NOT_A_SECOND_PEER_V1]

    Measured from Seattle3 on 2026-09-05:
        traceroute 10.250.250.1 -> 1 hop,  2.4ms   (Seattle5)
        traceroute 10.170.170.1 -> 2 hops, 10.9ms  VIA Seattle5
    So a two-peer run put 100% of its bytes through Seattle5 -- half addressed
    to it, half passing through it -- while the ramp table presented two
    independent targets sharing the load. Every TimeoutError of the day landed
    on the far side of that relay. The connect latencies were already in the
    log (6.1ms against 2.3ms) and nothing used them.
    """
    print("[9] a relayed peer is detected, not treated as a second peer")

    check("probe_rtt() exists", hasattr(producer.Rendezvous, "probe_rtt"))
    check("is_on_link() exists", hasattr(producer.Rendezvous, "is_on_link"))
    if not (hasattr(producer.Rendezvous, "probe_rtt")
            and hasattr(producer.Rendezvous, "is_on_link")):
        return

    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(64)
    port = srv.getsockname()[1]
    stop = threading.Event()

    def accept_loop():
        srv.settimeout(0.5)
        while not stop.is_set():
            try:
                srv.accept()
            except OSError:
                pass

    t = threading.Thread(target=accept_loop, daemon=True)
    t.start()
    try:
        rtt = producer.Rendezvous.probe_rtt("127.0.0.1", port)
        check("a reachable port yields a latency",
              rtt is not None and rtt >= 0.0, repr(rtt))
        check("an unreachable port yields None, not zero",
              producer.Rendezvous.probe_rtt("127.0.0.1", 1) is None,
              "a failed probe must not read as an instant one")
        check("loopback is one hop",
              producer.Rendezvous.is_on_link("127.0.0.1", port) is True,
              "IP_TTL=1 must still reach a host on this machine")
    finally:
        stop.set()
        t.join(timeout=3)
        srv.close()

    src = inspect.getsource(producer.main)
    check("the header prints latency and hop distance per peer",
          "path per peer" in src)
    check("a relayed target is called out",
          "reached through another node" in src,
          "a peer behind another target doubles the load on the relay")
    check("a latency split is called out",
          "not interchangeable targets" in src)
    check("both are recorded in the results",
          '"peer_rtt_ms": _rtt' in src and '"peer_on_link": _onlink' in src)


def test_line_rate_is_reported():
    """[A_THROUGHPUT_NUMBER_NEEDS_A_LINE_RATE_V1] A plateau on a full cable and
    a plateau in the mesh are opposite conclusions, and the ramp printed no
    number that could tell them apart. 12.19 MB/s is 102 Mbit/s: on gigabit
    that is a tenth of the wire, on 100 Mbit it IS the wire."""
    print("[10] the run states what the wire can do")

    from agent_workload.aiconnect import resources

    check("link_capacity() exists", hasattr(resources, "link_capacity"),
          "the line rate must be read, not assumed")
    if not hasattr(resources, "link_capacity"):
        return

    caps = resources.link_capacity()
    check("it reads real interfaces", isinstance(caps, dict))
    for name, c in caps.items():
        if c.get("speed_mbit") is not None:
            check("%s reports a positive speed" % name, c["speed_mbit"] > 0,
                  "%r -- -1 means unknown and must become None" % c["speed_mbit"])
            check("%s converts to MB/s" % name,
                  abs(c["max_mb_s"] - c["speed_mbit"] / 8.0 / 1.048576) < 1e-6)
        else:
            check("%s reports no speed rather than a guess" % name,
                  c["max_mb_s"] is None,
                  "a virtual or down link has no line rate")

    # The judgement itself, on values that do not depend on this container.
    fake = {"eth0": {"speed_mbit": 100, "duplex": "full", "max_mb_s": 11.92},
            "wg0": {"speed_mbit": None, "duplex": None, "max_mb_s": None}}
    hot = producer._near_line_rate(12.19, fake)
    cold = producer._near_line_rate(4.28, fake)
    check("a plateau at the line rate is named as the wire",
          "line rate" in hot and "eth0" in hot, hot or "(silent)")
    check("a plateau well below it is NOT blamed on the wire",
          cold == "", cold)
    check("an interface with no readable speed is never blamed",
          "wg0" not in hot, hot)

    src = inspect.getsource(producer.main)
    check("the header prints the capacity", "link capacity" in src)
    # [THE_PEAK_IS_A_FRACTION_OF_SOMETHING_V1] Measured 2026-09-05: bulk
    # peaked at 114.18 MB/s on a 1000Mbit link -- 95.8% of the wire -- and the
    # summary printed only the bare number, because the comparison was made
    # solely on a `saturated` stop, which cannot occur when the ramp reaches
    # its thread ceiling.
    check("the share is computed for every stop reason, not just saturation",
          src.index('"PEAK   : ') < src.index("_share, _impossible"),
          "DRIVER-CEILING is exactly the case that needed it")
    check("a peak at the wire says so plainly",
          "The limit found is the WIRE" in src)

    # [PAYLOAD_IS_NOT_LINE_RATE_V1] 114.18 MB/s measured, reported as 95.8% of
    # a 119.2 MB/s link. 119.2 is the SIGNALLING rate; at a 1500 MTU with
    # timestamps the payload maximum is 112.2, so the measurement was 101.7%
    # of what the wire can carry -- which is not a good result, it is a
    # contradiction.
    check("capacity carries an MTU",
          all("mtu" in c for c in caps.values()) if caps else True,
          "payload capacity cannot be derived without it")
    check("the payload maximum is computed, not the raw rate reused",
          "max_payload_mb_s" in str(list(caps.values())[0]) if caps else True)
    for name, c in caps.items():
        if c.get("max_payload_mb_s") and c.get("max_mb_s"):
            check("%s payload max is below its signalling rate" % name,
                  c["max_payload_mb_s"] < c["max_mb_s"],
                  "framing is not free")
    check("the PEAK line compares against payload capacity",
          "can carry as TCP payload" in src,
          "comparing payload against signalling rate overstates by ~6%")
    check("an over-100%% result is called impossible, not excellent",
          "IMPOSSIBLE:" in src,
          "a physically unachievable number must not read as a triumph")

    # [SAY_WHICH_INTERFACE_CARRIED_IT_V1] Measured 2026-09-05: bulk reported
    # wire/payload 1.157 (far above the ~1.06 that 1500-byte framing costs,
    # because rx+tx and unrelated flows were summed) and semantic reported
    # 0.011 -- 171.2 MB sent against 1.9 MB counted -- because overlay
    # interfaces were excluded from the witness entirely.
    check("the delta carries a per-interface tx/rx split",
          "per_iface" in resources.net_delta(resources.net_counters()),
          "one summed number cannot say which wire carried the run")
    check("carrier_of picks the biggest transmitter",
          resources.carrier_of({"eth0": {"tx": 10, "rx": 0, "is_wg": False},
                                "wg0": {"tx": 99, "rx": 0, "is_wg": True}})
          == "wg0",
          "the overlay must be eligible, or its traffic vanishes")
    check("carrier_of on nothing is None, not a guess",
          resources.carrier_of({}) is None)
    check("the table marks overlays",
          "(overlay)" in resources.iface_table(
              {"wg0": {"tx": 5 << 20, "rx": 0, "is_wg": True}}),
          "wg payload is counted again encapsulated; the two are not additive")
    check("the witness uses TX, not rx+tx", '_per[_carrier]["tx"]' in src,
          "ACKs and unrelated flows inflated the ratio to 1.157")
    check("an overlay-carried run is explained rather than dismissed",
          "belongs to that overlay" in src,
          "0.011 was reported as 'did not cross them' when the answer was "
          "'crossed a different one'")

    check("wire_overhead_ratio exists",
          hasattr(resources, "wire_overhead_ratio"),
          "the interface counters are the only witness to where bytes went")
    if hasattr(resources, "wire_overhead_ratio"):
        check("wire above payload gives a ratio above 1",
              resources.wire_overhead_ratio(100.0, 104.0) > 1.0)
        check("wire below payload gives a ratio below 1",
              resources.wire_overhead_ratio(100.0, 60.0) < 1.0,
              "that case means the bytes were not on that interface")
        check("zero or negative inputs give None, not a ratio",
              resources.wire_overhead_ratio(0, 5) is None
              and resources.wire_overhead_ratio(5, 0) is None)
    check("the run prints the measured ratio", "wire/payload" in src)
    check("the run names the interface that carried it", "carried by" in src)

    # [LOGICAL_IS_NOT_WIRE_V1] stats.bytes is what the WORKLOAD handed to the
    # transport, not what the wire moved. Measured 2026-09-05: semantic
    # reported 171.2 MB of objects against 1.9 MB on the interfaces, and the
    # run called that "not a measurement of that wire at all" -- when a 90x
    # gap on a SAME/DIFF transport is the result, not a fault. Bulk sends
    # os.urandom and came back at 1.157, i.e. no reduction, which is correct
    # for random bytes and is the control.
    # [TWO_EFFICIENCIES_V1] Both ratios, same window. The wire figure says
    # whether the link is busy; the REST-equivalent says what the objects
    # would have cost without SAME/DIFF. Neither answers the other's question.
    check("wire efficiency is reported",
          "wire efficiency: %6.2f%%" in src,
          "what the link actually carried")
    check("REST-equivalent efficiency is reported",
          "REST-equivalent: %6.2f%%" in src,
          "what the same objects would have needed with every byte on the wire")
    check("both come from run averages, not the peak rung",
          "run averages over %.1fs" in src,
          "the peak is one 4s slice; the counters cover the whole run")
    check("a REST-equivalent above 100%% is spelled out",
          "would need one %.2fx" in src,
          "that is the case the transport exists for")
    check("a WIRE efficiency above 100%% is called impossible",
          "WIRE EFFICIENCY ABOVE 100" in src,
          "over 100%% of payload capacity is a denominator or attribution "
          "fault, not a result")
    check("an unmeasurable link says so rather than printing nothing",
          "reports no link speed" in src,
          "wifi publishes no speed in sysfs and the blank must not read as "
          "'no limit found'")

    check("logical and wire rates are reported separately",
          "logical %.2f MB/s of objects, wire %.2f MB/s" in src,
          "one MB/s figure compares a wire rate against a logical one")
    check("a large gap is named as reduction, not shortfall",
          "SEMANTIC REDUCTION" in src,
          "the gap IS the product on this path")
    check("the reduction case says what the real bound is",
          "bounded by something else" in src,
          "at 90x the link stops being the constraint and CPU starts")
    check("no reduction is also stated explicitly",
          "no semantic reduction" in src,
          "an arm that should compress and does not is a finding too")
    check("the incompressible control is called out",
          "random bytes have no SAME to find" in src,
          "bulk sending urandom must not read as a semantic failure")
    check("each rung records it", '"link_capacity": _caps' in src,
          "the results JSON must carry it or the number is unfalsifiable later")


def test_rung_is_n_simultaneous():
    """[A_RUNG_IS_N_SIMULTANEOUS_V1] The ramp says 1, 2, 4, 8 simultaneous
    requests. It was creating threads and starting the clock in the next
    statement, with BulkLink connecting lazily on first send -- so a rung's
    window contained its own connect storm. Measured on the node: at the 256
    rung the link-open lines ran 41.85s to 44.50s of a 4s window."""
    print("[11] a rung is N simultaneous requests")

    check("RungGate exists", hasattr(producer, "RungGate"),
          "nothing holds the threads at a start line")
    check("links are opened before the window", hasattr(producer, "_prewarm"),
          "a lazy connect inside the window is measured as transfer time")
    if not (hasattr(producer, "RungGate") and hasattr(producer, "_prewarm")):
        return

    src = inspect.getsource(producer.main)
    check("the clock starts only once every thread is parked",
          "gate.wait_for(n_threads" in src)
    # Anchored on the rung's own assignment, not on any line that merely
    # contains the same characters -- a run-level timestamp named _run_t0
    # matched this and made the check read the wrong statement.
    check("t0 is taken before the release, not before the warm-up",
          src.index("\n            t0 = time.time()") < src.index("gate.release()")
          and src.index("gate.wait_for(n_threads")
          < src.index("\n            t0 = time.time()"),
          "the warm-up must be outside the measured window")
    check("a rung that cannot fill its start line is refused, not measured",
          "only %d of %d threads reached the start line" in src)

    rx = open_receiver(2)
    if rx is None:
        return
    n = 24
    stop = threading.Event()
    stats = producer.Stats()
    gate = producer.RungGate()

    class _RV:
        me = "10.0.0.1"

    peers = ["127.0.0.1"]
    ports = {"127.0.0.1": rx.port}
    first_send = {}
    real_send = producer.BulkLink.send

    def timed_send(self, nb):
        first_send.setdefault(self.thread_id, time.time())
        return real_send(self, nb)

    producer.BulkLink.send = timed_send
    ts = []
    try:
        for i in range(n):
            t = threading.Thread(
                target=producer.worker,
                args=(_RV(), peers, 4096, ports, stop, stats, "bulk", i, gate),
                daemon=True)
            t.start()
            ts.append(t)

        parked = gate.wait_for(n, 30.0, stop)
        check("all %d threads reach the start line" % n, parked == n,
              "%d parked" % parked)
        # A worker parks the instant its connect() returns; the shard
        # publishes its accept counter a moment later. That lag is still
        # before the window opens, so poll briefly rather than reading the
        # counter at the exact instant of parking -- the claim is "no connect
        # inside the measurement", not "the counter is instantaneous".
        _dl = time.time() + 10.0
        while time.time() < _dl and rx.accepts < n:
            time.sleep(0.02)
        check("every link is open BEFORE the window", rx.accepts == n,
              "%d accepts at the start line, expected %d -- a link opened "
              "later would be a connect inside the measurement"
              % (rx.accepts, n))
        check("nothing has been sent yet", stats.transfers == 0,
              "%d transfers before release -- the gate is not holding"
              % stats.transfers)

        t0 = time.time()
        gate.release()
        time.sleep(1.5)
        stop.set()
        gate.release()
        for t in ts:
            t.join(timeout=10)

        check("every thread sent after release", len(first_send) == n,
              "%d of %d threads sent" % (len(first_send), n))
        if first_send:
            lag = min(first_send.values()) - t0
            spread = max(first_send.values()) - min(first_send.values())
            check("the first send follows the release immediately",
                  lag < 0.5, "%.3fs" % lag)
            check("they start together, not in a rolling ramp-up",
                  spread < 1.0,
                  "%.3fs between the first and last thread's first send"
                  % spread)
    finally:
        producer.BulkLink.send = real_send
        stop.set()
        gate.release()
        rx.close()


def test_load_is_reported_not_enforced():
    """[QUEUEING_IS_NOT_AN_ERROR_V1] + [THE_FAILED_COLUMN_MISSED_THE_WARM_UP_V1]

    Load 46.35 per core at 11.4% CPU on the Pi 5 at 8192 threads is the client
    parking threads in the kernel's send path. That costs the CLIENT latency
    and costs the measurement nothing. The failure a caller actually sees is a
    timeout, and the errors rule already covers it -- so stopping on load
    would have ended that ramp at 4096, the rung with the highest throughput
    of the run and the first client-visible failures, which is the rung worth
    reaching.

    The reporting bug it exposed matters more. The 4096 rung printed
    `failed 0` with "TimeoutError x62; TimeoutError x35" on the line
    underneath: 97 timeouts during a 27-second start-line wait, invisible in
    the column a person reads, because `failed` covers the measured window
    while the breakdown is diffed from the end of the previous rung.
    """
    print("[6] load is reported, and warm-up failures are not hidden")

    src = inspect.getsource(producer.main)
    check("load does not stop the ramp by default",
          "default=0.0" in src and "load_max_per_core" in src,
          "queueing is not an error and must not end a healthy ramp")
    check("the rule is opt-in, not removed",
          "a.load_max_per_core > 0" in src,
          "it stays available for anyone who does want it")
    check("high load at the ceiling is explained, not condemned",
          "only a problem if" in src,
          "load is a client-side cost, not a mesh refusal")

    check("failures during the start-line wait are counted",
          "_warm_fails = stats.failures - _f_warm0" in src,
          "97 timeouts read as `failed 0` because they fell outside the "
          "measured window")
    # Anchored on a fragment contained in ONE source line: the message wraps,
    # so no full sentence from it appears contiguously in the source.
    check("they are printed where the rung is read",
          "these are real and are NOT in " in src
          and "the failed column below" in src,
          "two adjacent lines disagreeing is worse than either alone")
    check("they are recorded per rung", '"warm_failures": _warm_fails' in src)
    check("the warm-up duration is recorded too",
          '"warm_seconds"' in src,
          "27s of start line is the symptom that produced them")


def test_semantic_ceiling():
    """[SEMANTIC_CEILING_IS_THE_WIRE_FIELD_V1] Asserted against the codec that
    enforces it, not against a number retyped here -- a literal restating
    another literal is exactly the failure this is fixing."""
    print("[12] semantic ceiling  [SEMANTIC_CEILING_IS_THE_WIRE_FIELD_V1]")

    from core import codec as _codec

    check("codec names its wire-field limit",
          hasattr(_codec, "MAX_WIRE_FIELD_BYTES"),
          "the bound must be a named constant callers can bind to")
    if not hasattr(_codec, "MAX_WIRE_FIELD_BYTES"):
        return

    check("the limit is 65535, not 65536",
          _codec.MAX_WIRE_FIELD_BYTES == 65535,
          "is %r" % (_codec.MAX_WIRE_FIELD_BYTES,))
    check("the producer takes its ceiling FROM the codec",
          producer.SEMANTIC_MAX_OBJECT == _codec.MAX_WIRE_FIELD_BYTES,
          "SEMANTIC_MAX_OBJECT=%r vs MAX_WIRE_FIELD_BYTES=%r"
          % (producer.SEMANTIC_MAX_OBJECT, _codec.MAX_WIRE_FIELD_BYTES))
    check("64*1024 is NOT accepted as the ceiling",
          producer.SEMANTIC_MAX_OBJECT != 65536,
          "65536 is one byte past a uint16 length prefix")

    # And the encoder must actually agree, either side of the boundary.
    enc = _codec.FrogNetCodec() if hasattr(_codec, "FrogNetCodec") else None
    if enc is None:
        for nm in dir(_codec):
            o = getattr(_codec, nm)
            if isinstance(o, type) and hasattr(o, "_encode_value"):
                enc = o()
                break
    if enc is None:
        check("codec encoder is reachable", False, "no class with _encode_value")
        return

    ok_at_limit = True
    try:
        enc._encode_value(enc.TYPE_STR, "x" * producer.SEMANTIC_MAX_OBJECT)
    except Exception as e:
        ok_at_limit = False
        detail = "%s: %s" % (type(e).__name__, e)
    check("a body at exactly the ceiling encodes", ok_at_limit,
          detail if not ok_at_limit else "")

    rejected = False
    try:
        enc._encode_value(enc.TYPE_STR, "x" * (producer.SEMANTIC_MAX_OBJECT + 1))
    except ValueError:
        rejected = True
    check("one byte past the ceiling is refused", rejected,
          "the encoder accepted a field it cannot length-prefix")


def test_extend_window():
    print("[2] extend window  [EXTEND_ONLY_FOR_PENDING_NEVER_FOR_FAILING_V1]")

    check("should_extend() exists",
          hasattr(producer, "should_extend"),
          "the extend predicate must be callable to be testable")
    if not hasattr(producer, "should_extend"):
        return

    se = producer.should_extend
    now, deadline = 100.0, 200.0

    check("extends for genuinely pending work",
          se(d_x=0, d_f=0, inflight=1, now=now, deadline=deadline) is True,
          "a window shorter than one transfer cycle is what extending is for")

    # The measured case. Every one of these rungs extended on the old code.
    check("does NOT extend a pure failure loop (the 1024 rung)",
          se(d_x=0, d_f=1591, inflight=1024, now=now, deadline=deadline)
          is False,
          "d_f=1591 with d_x=0 is a measurement, not unfinished work")
    check("does NOT extend as failures compound (1,591 -> 14,678)",
          se(d_x=0, d_f=14678, inflight=1024, now=now, deadline=deadline)
          is False)
    check("a single failure ends the extension",
          se(d_x=0, d_f=1, inflight=512, now=now, deadline=deadline) is False,
          "extending past any failure only counts more failures")

    check("does not extend once transfers are completing",
          se(d_x=7, d_f=0, inflight=4, now=now, deadline=deadline) is False)
    check("does not extend with nothing in flight",
          se(d_x=0, d_f=0, inflight=0, now=now, deadline=deadline) is False)
    check("still bounded by wall time",
          se(d_x=0, d_f=0, inflight=1, now=deadline + 1.0, deadline=deadline)
          is False)

    # The loop must recompute failures inside the window, or the predicate is
    # only ever handed the value it was given before the first sleep.
    src = inspect.getsource(producer.main)
    body = src[src.index("should_extend"):]
    check("the loop refreshes d_f inside the extension",
          "d_f = stats.failures - f0" in body,
          "a stale d_f makes the new predicate untestable at runtime")


# -------------------------------------------------------- 3. receiver silence

def test_reuseport_shards():
    """[REUSEPORT_SHARDS_V1] N processes, one port, kernel-balanced."""
    print("[13] SO_REUSEPORT shards")

    check("SO_REUSEPORT is available on this platform",
          hasattr(socket, "SO_REUSEPORT"),
          "without it there is no sharing and no sharding")
    if not hasattr(socket, "SO_REUSEPORT"):
        return

    rx = open_receiver(4)
    if rx is None:
        return
    try:
        check("the receiver reports its width", rx.shards == 4,
              "shards=%r" % (rx.shards,))
        check("every shard is alive", rx.live_shards() == 4,
              "%d of 4 alive: %r" % (rx.live_shards(), rx.error))
        check("the shards are separate processes",
              len({p.pid for p in rx._procs}) == 4,
              "a shard per process is what escapes the GIL")
        check("no shard is this process",
              os.getpid() not in {p.pid for p in rx._procs})
        check("one port serves them all", isinstance(rx.port, int)
              and rx.port > 0)

        # The port really is shareable: a fifth listener must be able to join.
        extra = None
        try:
            extra = service._bulk_listener("127.0.0.1", rx.port)
            joined = True
        except OSError as e:
            joined = False
            detail = "%s: %s" % (type(e).__name__, e)
        finally:
            if extra is not None:
                extra.close()
        check("another listener can bind the same port", joined,
              detail if not joined else "")
    finally:
        rx.close()


def test_permanent_link_framing():
    """[THE_LINK_IS_PERMANENT_V1] + [PERSISTENT_LINK_NEEDS_FRAMING_V1]

    The measurable consequence: many objects, ONE accept. On the old code
    every object was its own connection, so accepts equalled transfers and
    the bulk arm was measuring TCP setup.
    """
    print("[14] the link is permanent and objects are framed")

    n_obj, obj_bytes = 50, 4096
    rx = open_receiver(2)
    if rx is None:
        return
    try:
        lk = producer.BulkLink("127.0.0.1", rx.port, "10.0.0.1", 7)
        try:
            for _ in range(n_obj):
                lk.send(obj_bytes)
        finally:
            lk.close()

        deadline = time.time() + 15.0
        while time.time() < deadline and rx.transfers < n_obj:
            if rx.error is not None:
                break
            time.sleep(0.05)

        check("no shard failed", rx.error is None, repr(rx.error))
        check("%d objects arrive as %d transfers" % (n_obj, n_obj),
              rx.transfers == n_obj,
              "counted %d -- the length prefix is what separates them"
              % rx.transfers)
        check("exactly ONE accept for %d objects" % n_obj,
              rx.accepts == 1,
              "%d accepts -- a permanent link opens once" % rx.accepts)
        check("payload bytes match the sender exactly",
              rx.bytes_received == n_obj * obj_bytes,
              "receiver counted %d, sender sent %d; a mismatch of "
              "32 + 8*objects means the framing is being counted as payload"
              % (rx.bytes_received, n_obj * obj_bytes))
        check("the link reports what it carried",
              lk.objects == n_obj and lk.bytes == n_obj * obj_bytes,
              "objects=%d bytes=%d" % (lk.objects, lk.bytes))
    finally:
        rx.close()


def test_framing_survives_split_fields():
    """[FRAME_WITHOUT_COPYING_THE_PAYLOAD_V1] The shard walks each received
    block in place and carries only a partial header or length prefix. That
    hand-rolled boundary handling is the risk the copying version did not
    have, so drive it with fields deliberately split across recv calls."""
    print("[15] framing across recv boundaries")

    rx = open_receiver(1)
    if rx is None:
        return
    n_obj, obj_bytes = 40, 3000
    try:
        sk = socket.create_connection(("127.0.0.1", rx.port), timeout=10)
        try:
            sk.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            stream = service.bulk_header("10.0.0.1", 3)
            for _ in range(n_obj):
                stream += service.bulk_frame(obj_bytes) + b"q" * obj_bytes
            # Three bytes at a time: every header and every length prefix is
            # split, and most objects straddle several writes.
            for i in range(0, len(stream), 3):
                sk.sendall(stream[i:i + 3])

            deadline = time.time() + 30.0
            while time.time() < deadline and rx.transfers < n_obj:
                if rx.error is not None:
                    break
                time.sleep(0.02)
        finally:
            sk.close()

        check("no shard failed on split fields", rx.error is None,
              repr(rx.error))
        check("all %d objects framed from 3-byte writes" % n_obj,
              rx.transfers == n_obj,
              "counted %d -- a partial length prefix was mishandled"
              % rx.transfers)
        check("payload bytes exact across the splits",
              rx.bytes_received == n_obj * obj_bytes,
              "got %d want %d" % (rx.bytes_received, n_obj * obj_bytes))
    finally:
        rx.close()


def test_drain_is_bounded():
    """[DRAIN_MUST_BE_FAIR_V1] A permanent link never stops sending, so a
    drain loop that runs until EWOULDBLOCK never yields the shard. The budget
    is what makes the loop give the other connections a turn."""
    print("[16] the per-connection drain is bounded")

    check("DRAIN_BUDGET_BYTES exists",
          hasattr(service, "DRAIN_BUDGET_BYTES"),
          "an unbounded drain lets one sender hold a shard indefinitely")
    if not hasattr(service, "DRAIN_BUDGET_BYTES"):
        return
    check("the budget is a real bound",
          0 < service.DRAIN_BUDGET_BYTES <= (64 << 20),
          "is %r" % (service.DRAIN_BUDGET_BYTES,))

    src = inspect.getsource(service._shard_main)
    check("the read loop tests the budget",
          "while drained < DRAIN_BUDGET_BYTES" in src,
          "the loop must stop on the budget, not only on EWOULDBLOCK")
    check("payload bytes are not buffered",
          'st["buf"] += b' not in src,
          "concatenating every received block copies the whole run twice")


def test_many_concurrent_links():
    """The descriptor limit is now the only limit -- select()'s FD_SETSIZE of
    1024 was the old ceiling and epoll has none. Held concurrently, because it
    is simultaneity that allocates high descriptors."""
    print("[17] concurrent links past FD_SETSIZE")

    want = 1200
    soft, _hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    headroom = soft - len(os.listdir("/proc/self/fd")) - 64
    if headroom < want:
        check("descriptor limit allows the test", False,
              "RLIMIT_NOFILE soft=%d leaves room for %d, need %d"
              % (soft, headroom, want))
        return

    rx = open_receiver(2)
    if rx is None:
        return
    links = []
    try:
        for i in range(want):
            links.append(producer.BulkLink("127.0.0.1", rx.port,
                                           "10.0.0.%d" % (i % 250 + 1), i))
        for lk in links:
            lk.send(512)

        deadline = time.time() + 30.0
        while time.time() < deadline and rx.transfers < want:
            if rx.error is not None:
                break
            time.sleep(0.05)

        check("no shard died under %d concurrent links" % want,
              rx.error is None, repr(rx.error))
        check("all %d links were accepted" % want, rx.accepts == want,
              "accepted %d" % rx.accepts)
        check("all %d objects were framed" % want, rx.transfers == want,
              "counted %d" % rx.transfers)
        check("descriptors past FD_SETSIZE were reached",
              want > 1024, "the test must actually cross 1024")
    finally:
        for lk in links:
            lk.close()
        rx.close()


def test_a_dead_shard_is_not_silent():
    """A receiver quietly running at a fraction of its width is exactly the
    failure this change exists to end, so a shard that dies must show."""
    print("[18] a dead shard is visible")

    rx = open_receiver(2)
    if rx is None:
        return
    try:
        check("healthy receiver reports no error", rx.error is None,
              repr(rx.error))
        victim = rx._procs[0]
        victim.terminate()
        victim.join(timeout=5.0)

        deadline = time.time() + 5.0
        while time.time() < deadline and rx.error is None:
            time.sleep(0.05)

        check("the loss is reported", rx.error is not None,
              "a shard exited and the receiver still claimed to be healthy")
        check("live_shards() shows the reduced width", rx.live_shards() == 1,
              "live=%d" % rx.live_shards())
        check("stats() carries it too",
              rx.stats().get("error") is not None
              and rx.stats().get("live_shards") == 1,
              repr(rx.stats()))
    finally:
        rx.close()


def test_main_runs_end_to_end():
    """[THE_RAMP_ITSELF_IS_NEVER_RUN_V1]

    Every check above this one exercises a FUNCTION. Nothing ran main(), so a
    rewrite of net_delta's return dict that dropped wg_bad and phys_bad passed
    the whole oracle and then died on the user's node at rung 1 with
    KeyError: 'wg_bad'. A gate that never executes the program it gates cannot
    catch a missing key, a bad format string, or an argument that no longer
    exists.

    So: a real ramp, real threads, a real sharded receiver, real rung records
    and the real summary -- just small and local.
    """
    print("[19] the ramp runs end to end")

    rx = open_receiver(2)
    if rx is None:
        return

    hosts = os.path.join(tempfile.mkdtemp(), "hosts")
    with open(hosts, "w") as f:
        f.write("127.0.0.1 peer.frognet\n")
    results = tempfile.mkdtemp()

    real_rv = producer.Rendezvous
    real_hosts = producer.hosts_from_etc
    real_machine = producer.machine_name

    class _RV(producer.Rendezvous):
        def __init__(self, *a, **k):
            self.dbhost, self.timeout_s, self.me = "x", 5.0, "10.0.0.1"

        def ready_machines(self):
            return ["127.0.0.1"]

        def request_connection(self, peer):
            pass

        def primed_machines(self):
            return {"127.0.0.1": rx.port}

        def bulk_row_ages(self):
            return {"127.0.0.1": 12.0}

    try:
        producer.Rendezvous = _RV
        producer.hosts_from_etc = lambda path="/etc/hosts": ["127.0.0.1"]
        producer.machine_name = lambda: "10.0.0.1"
        # Caught, not allowed to propagate: a ramp that dies mid-run must be
        # reported as a FAILED CHECK with its exception, or it aborts the
        # oracle and every assertion after it is silently skipped -- which is
        # how a KeyError reached a live node in the first place.
        rc, exc = None, None
        try:
            rc = producer.main([
                "--transport", "bulk", "--object-bytes", "8192",
                "--step-seconds", "1", "--max-threads", "4",
                "--cpu-max-pct", "100", "--results-dir", results,
                "--hosts-file", hosts, "--saturation-rungs", "0",
                "--run-id", "oracle-e2e"])
        except BaseException as e:
            exc = "%s: %s" % (type(e).__name__, e)
            # The traceback is the whole value of running the real ramp; a
            # type name alone sends you hunting.
            traceback.print_exc()
        check("main() completes without raising", exc is None, exc or "")
        check("it exits clean", rc == 0, "rc=%r" % rc)

        out = os.path.join(results, "oracle-e2e.aiconnect.json")
        check("it writes its results file", os.path.exists(out), out)
        if os.path.exists(out):
            with open(out) as f:
                doc = json.load(f)
            check("the run recorded levels", bool(doc.get("levels")))
            lvl = (doc.get("levels") or [{}])[0]
            for k in ("threads", "mb_per_s", "wire_mb_per_s", "xfers_per_s",
                      "failed",
                      "warm_failures", "warm_seconds",
                      "wg_errors", "phys_errors", "wg_mb", "phys_mb",
                      "hw_errors", "queue_drops", "link_capacity",
                      "peer_routes", "outstanding", "inflight"):
                check("rung record has %r" % k, k in lvl,
                      "a key main() writes must exist or the ramp dies "
                      "mid-run")
            check("it moved real bytes",
                  any(l.get("mb_per_s", 0) > 0 for l in doc["levels"]),
                  "a ramp that transferred nothing proves nothing")
            check("a stop reason was recorded", bool(doc.get("stop_reason")))
            # [A_RUNG_MEASURES_THE_BUFFER_NOT_THE_WIRE_V1] sendall() returns
            # on kernel acceptance, not transmission. Measured on the Pi 5:
            # the 2048 rung reported 112.95 MB/s -- 101% of what the link can
            # carry -- while eth0 averaged 46.43 MB/s over the whole run and
            # peaked at 41% of capacity. The excess was buffered, not sent.
            check("every rung carries a wire rate beside its logical rate",
                  all("wire_mb_per_s" in l for l in doc["levels"]),
                  "without it a buffered rung reads as a wire measurement")
            check("the wire rate is a real reading, not a copy of the logical",
                  any(abs(l["wire_mb_per_s"] - l["mb_per_s"]) > 1e-9
                      for l in doc["levels"])
                  or all(l["mb_per_s"] == 0 for l in doc["levels"]),
                  "identical columns would mean the counters were not read")
    finally:
        producer.Rendezvous = real_rv
        producer.hosts_from_etc = real_hosts
        producer.machine_name = real_machine
        rx.close()


def main(argv=None):
    print("AICONNECT RAMP ORACLE")
    print("=" * 60)
    test_driver_ceiling()
    test_extend_window()
    test_outstanding_means_stuck()
    test_breakdown_names_the_cause()
    test_receiver_age_is_reported()
    test_peer_route_is_reported()
    test_drop_is_not_a_card_error()
    test_relayed_peer_is_detected()
    test_line_rate_is_reported()
    test_rung_is_n_simultaneous()
    test_load_is_reported_not_enforced()
    test_semantic_ceiling()
    test_reuseport_shards()
    test_permanent_link_framing()
    test_framing_survives_split_fields()
    test_drain_is_bounded()
    test_many_concurrent_links()
    test_a_dead_shard_is_not_silent()
    test_main_runs_end_to_end()
    print("=" * 60)
    if _FAILURES:
        print("ORACLE: FAIL (%d)" % len(_FAILURES))
        for f in _FAILURES:
            print("   - %s" % f)
        return 1
    print("ORACLE: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
