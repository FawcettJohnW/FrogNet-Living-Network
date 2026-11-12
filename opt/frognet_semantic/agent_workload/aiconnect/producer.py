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
AIConnect producer -- objects written into the receivers' memory, ramped to saturation.

[AICONNECT_IN_THE_REGION_V1 - John 2026-11-11] "Why would I deliberately disadvantage my architecture?"

    1. read (AIConnect, receiver, *) in each FrogTorch region   which machines are receivers, and where their memory is
                                                                 (a session-held cell each: there is a receiver
                                                                 exactly while its service is)
    2. each sender thread writes its object, again and again, as one tensor cell in the receiver's region:
       aic.<me>.<thread>, held by the thread's own session (life="session"), so it goes when the link closes
    3. the region's acknowledgement of the write is delivery: the bytes are in the receiver's memory

No request, no "ready" flag, no port handed around, no poll, no done signal. Before this: MachineReady ->
ConnectionRequest -> BulkReady polled every 0.3 s -> a socket to a sharded receiver -> BulkReady = 0.

The rest8080 and semantic80 arms are kept as they were: HTTP POSTs to Apache and through the FrogNet proxy -- the
stock comparison, not this architecture.

RAMP. Threads are added every --step-seconds, doubling, spread across every receiver, so many transfers run to
different machines at once. It stops on a measured condition and names it: CPU average above the threshold,
interface errors or drops (WireGuard counted separately from physical), disk approaching its limit, memory floor, or
transfers failing. There is no preset thread ceiling beyond --max-threads.
"""

from __future__ import annotations

import argparse
import collections
import json
import os
import http.client
import random
import resource
import socket
import sys
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

_TREE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _TREE not in sys.path:
    sys.path.insert(0, _TREE)

from core.frognet_diag import diag, diag_exc  # noqa: E402
from core.codec import MAX_WIRE_FIELD_BYTES  # noqa: E402
from agent_workload.aiconnect.service import (  # noqa: E402
    BUILD, SERVICE, VAR_RECEIVER, machine_name, parse_region)
from agent_workload.tuplespace.frogtorch_ram_client import _Session, discover_regions  # noqa: E402
from agent_workload.tuplespace.tensor_plane import MAX_SERVER_WAIT_S, _recv_frame, _send_frame  # noqa: E402
from agent_workload.aiconnect.resources import (  # noqa: E402
    cpu_idle_pct, disk_free_mb, loadavg_per_core, mem_available_mb,
    net_counters, net_delta, link_capacity, link_summary, peer_routes, softnet_drops, wire_overhead_ratio, carrier_of, iface_table)


#: Largest object the semantic arm will attempt. Control-plane sized on
#: purpose: this arm exists to compare REST against the semantic path on the
#: kind of request each is meant to carry.
#: [SEMANTIC_CEILING_IS_THE_WIRE_FIELD_V1]
#: This was `64 * 1024` -- 65536 -- and the semantic arm failed 100% of
#: attempts at exactly that size with
#:   ValueError: TYPE_STR over 65535-byte wire field: 65536 bytes
#: The body becomes one TYPE_STR field, whose length prefix is a uint16, so
#: the largest object the semantic path can carry is 65535 bytes. "64 KB" is
#: the number a person writes when they mean this limit and it is one byte
#: past it, which is why the bound is now taken FROM the codec rather than
#: restated here: a literal cannot be checked against the thing it is
#: supposed to match, and this one silently disagreed with it.
SEMANTIC_MAX_OBJECT = MAX_WIRE_FIELD_BYTES

#: Ramp ceiling. [THE_DRIVER_IS_NOT_THE_SUBJECT_V1] -> [CEILING_RAISED_V2]
#:
#: Was 512. The 512 was set on evidence from a producer that opened a fresh
#: TCP connection for every object and started its clock before the threads
#: had connected: at 2048 that produced load 104 per core and 12,917 failures,
#: and the number being measured was this program's scheduler.
#:
#: That producer no longer exists. Links are permanent, every rung starts from
#: a start line with the connects already done, and the receiver is sharded.
#: Measured 2026-09-05 at the old 512 ceiling: 38.34 MB/s, 613 transfers/s,
#: CPU 12.0%, load 0.12 per core, and the run ended on DRIVER-CEILING rather
#: than on any resource. Nothing was near a limit, so 512 had stopped being a
#: safeguard and become an arbitrary stop.
#:
#: Raised to 2048 on that evidence, NOT on the assumption that the old
#: measurement was wrong. What 2048 does newly threaten is descriptors --
#: 2048 threads x N peers permanent links -- so that is now checked before the
#: ramp starts instead of being discovered at rung 1024. See _check_fd_budget.
DEFAULT_MAX_THREADS = 2048


class Receivers:
    """Step 1: which machines are receivers, and where their memory is. All of it read from the regions."""

    def __init__(self, regions: Optional[List[Tuple[str, int]]] = None, timeout_s: float = 30.0,
                 me: Optional[str] = None):
        self.timeout_s = timeout_s
        self.me = me or machine_name()
        self.regions = regions if regions is not None else discover_regions()
        self._seen: Dict[str, Dict[str, Any]] = {}

    def _rows(self, hp, wait_s=0.0, unlike=None) -> List[Dict[str, Any]]:
        return _Session(*hp).call({"op": "cget", "service": SERVICE, "var": VAR_RECEIVER, "min_rows": 0,
                                   "wait_s": wait_s, "unlike": unlike}).get("rows", [])

    def receivers(self, want: int = 0) -> Dict[str, Dict[str, Any]]:
        """{machine: its receiver cell's value}. want > 0: hold a read on every region until that many are there
        (a receiver that starts later is seen when it writes), or the timeout."""
        found: Dict[str, Dict[str, Any]] = {}
        lock = threading.Condition()

        def take(hp, rows):
            for r in rows:
                v = r.get("value") or {}
                if v.get("machine") and v["machine"] != self.me:
                    v = dict(v, region=tuple(parse_region(v["region"])) if isinstance(v.get("region"), str)
                             else tuple(v["region"]))
                    found[v["machine"]] = v
            diag("DIAG-RDV", "receivers", region="%s:%d" % hp, rows=len(rows))

        for hp in self.regions:
            take(hp, self._rows(hp))
        if want and len(found) < want:
            deadline = time.time() + self.timeout_s

            def hold(hp):
                ids = sorted(r["id"] for r in self._rows(hp))
                while time.time() < deadline:
                    rows = self._rows(hp, wait_s=max(0.1, min(MAX_SERVER_WAIT_S, deadline - time.time())),
                                      unlike=ids)
                    ids = sorted(r["id"] for r in rows)
                    with lock:
                        take(hp, rows)
                        lock.notify_all()
                        if len(found) >= want:
                            return
            ts = [threading.Thread(target=hold, args=(hp,), daemon=True) for hp in self.regions]
            for t in ts:
                t.start()
            with lock:
                lock.wait_for(lambda: len(found) >= want, timeout=self.timeout_s)
        self._seen = found
        return found

    def ages(self) -> Dict[str, Any]:
        """{machine: seconds since its receiver started} -- [PRIMED_MEANS_NOTHING_WITHOUT_AN_AGE_V1]."""
        now = time.time()
        return {m: (now - float(v["started"])) if v.get("started") else None for m, v in self._seen.items()}

    @staticmethod
    def probe_rtt(peer: str, port: int, timeout: float = 3.0) -> Optional[float]:
        """Milliseconds to complete a TCP connect. None if it will not open.

        [A_PEER_IS_ALSO_A_PATH_V1] This number was already in the log --
        `probe OK ... ms=6.1` against `ms=2.3` -- and nothing used it. A peer
        at nearly 3x the connect latency of another is not the same
        measurement, and per-peer results were being compared as if it were.
        """
        t0 = time.time()
        try:
            with socket.create_connection((peer, port), timeout=timeout):
                return (time.time() - t0) * 1000.0
        except Exception:
            return None

    @staticmethod
    def is_on_link(peer: str, port: int, timeout: float = 3.0) -> Optional[bool]:
        """True if `peer` is ONE hop away, False if it is relayed.

        [A_RELAYED_PEER_IS_NOT_A_SECOND_PEER_V1]
        Measured 2026-09-05 from Seattle3: 10.250.250.1 is one hop;
        10.170.170.1 is TWO, and the intermediate hop IS 10.250.250.1. So a
        run against both peers put 100% of its bytes through Seattle5 --
        half addressed to it and half passing through it -- while the ramp
        table presented them as two independent targets sharing the load.
        Every TimeoutError in every run today landed on the far side of that
        relay, and the run had no way to say so.

        Done with IP_TTL=1 on a normal connect: at one hop the SYN arrives and
        the connection opens; at two the first router decrements to zero,
        drops it, and the connect fails. Only meaningful straight after a
        successful full-TTL probe, which is how it is called -- otherwise a
        down peer and a distant one look the same. Returns None when the
        platform will not set the option rather than guessing. [NO FALLBACKS]
        """
        sk = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sk.setsockopt(socket.IPPROTO_IP, socket.IP_TTL, 1)
        except (OSError, AttributeError):
            sk.close()
            return None
        try:
            sk.settimeout(timeout)
            sk.connect((peer, port))
            return True
        except Exception:
            return False
        finally:
            try:
                sk.close()
            except OSError:
                pass

    @staticmethod
    def probe(peer: str, port: int, timeout: float = 3.0) -> Optional[str]:
        """Is anything actually listening there? Returns None if the port
        accepts, else a description of the refusal.

        [PROVE_THE_PORT_IS_LIVE_V1]
        The same discipline [PROVE_THE_REQUEST_LANDED_V1] applies one step
        earlier. A run took a port out of a BulkReady row and discovered it
        was dead 198 times a second for eight rungs, and reported the result
        as a capacity limit: with two peers and one of them refusing, the
        failure rate sat at the coin flip -- 49%, 48%, 49%, 52% -- until it
        crossed a 50% stop threshold, and the ramp called that a peak.
        """
        t0 = time.time()
        try:
            with socket.create_connection((peer, port), timeout=timeout):
                diag("DIAG-RDV", "probe OK", peer=peer, port=port,
                     ms=round((time.time() - t0) * 1000, 1))
                return None
        except Exception as e:
            diag_exc("DIAG-RDV", "probe FAILED", e, peer=peer, port=port,
                     ms=round((time.time() - t0) * 1000, 1))
            return "%s: %s" % (type(e).__name__, e)


def send_rest(peer: str, port: int, total_bytes: int, chunk: int = 1 << 16,
              json_body: bool = False, sender: str = "?", thread_id: int = 0) -> int:
    """The REST arm: the same object, POSTed over HTTP instead of a socket.

    [SAME_RAMP_THREE_TRANSPORTS_V1]
    port 8080 goes straight to Apache with the semantic path bypassed; port 80
    enters the FrogNet proxy. Everything else -- object size, thread ramp,
    resource thresholds, the peer set -- is identical, which is the only way
    the three numbers can be put next to each other.

    The body is streamed from a generator, not built in memory, so object size
    is independent of the sender's RAM exactly as it is on the socket arm.
    """
    # [THE_SEMANTIC_ARM_SENDS_WHAT_IT_CARRIES_V1]
    # This posted raw os.urandom() bytes. Apache does not care, so rest8080 was
    # fine; the proxy inspects the body to build a template key, and the
    # semantic arm failed 198 times with RemoteDisconnected -- the handler died
    # without a response.
    #
    # Binary is also not what this path carries. The semantic path is for
    # CONTROL-PLANE requests, which are JSON text, and a template keyed off a
    # body is meaningless for an opaque blob. So the semantic arm sends JSON of
    # the requested size and rest8080 keeps sending bytes: each arm carries
    # what its transport is actually for, which is the comparison worth making.
    if json_body:
        filler = "x" * 1024
        head = '{"sender":"%s","thread":%d,"data":"' % (sender, thread_id)
        tail = '"}'
        n_fill = max(0, total_bytes - len(head) - len(tail))
        payload = (head + (filler * (n_fill // len(filler)))
                   + "x" * (n_fill % len(filler)) + tail).encode()
        total_bytes = len(payload)
        content_type = "application/json"

        def chunks():
            for i in range(0, len(payload), chunk):
                yield payload[i:i + chunk]
    else:
        block = os.urandom(min(chunk, total_bytes))
        content_type = "application/octet-stream"

        def chunks():
            sent = 0
            while sent < total_bytes:
                n = min(len(block), total_bytes - sent)
                sent += n
                yield block[:n]

    conn = http.client.HTTPConnection(peer, port, timeout=120)
    try:
        conn.putrequest("POST", "/bulk.php")
        conn.putheader("Content-Type", content_type)
        conn.putheader("Content-Length", str(total_bytes))
        conn.endheaders()
        for c in chunks():
            conn.send(c)
        r = conn.getresponse()
        body = r.read()
        if r.status >= 300:
            raise RuntimeError("bulk.php on %s:%d returned HTTP %d: %s"
                               % (peer, port, r.status, body[:120]))
        return total_bytes
    finally:
        conn.close()


#: Set from --link-timeout before any worker starts.
#: [THE_TIMEOUT_IS_SHAPING_THE_DATA_V1]
_LINK_TIMEOUT = [30.0]


class RegionLink:
    """One sender thread's PERMANENT session with one receiver's region ([THE_LINK_IS_PERMANENT_V1]).

    send() writes one object as the thread's cell, aic.<sender>.<thread>, into that region, overwriting the last
    one, and returns when the region has it. The cell is held by this session (life="session"): it goes when the
    link closes, so a run leaves nothing in the receiver's memory. The object is sent from one block, repeated, so
    an object of any size needs no buffer of that size here.

    A link that breaks is not re-established inside send(): the exception reaches the worker, which drops the link
    and counts the failure. [NO FALLBACKS]
    """

    def __init__(self, machine: str, region: Tuple[str, int], sender: str, thread_id: int,
                 chunk: int = 1 << 16, connect_timeout: float = 30.0, timeout: float = None):
        self.peer, self.region = machine, region
        self.sender, self.thread_id = sender, thread_id
        self.chunk = chunk
        self.objects = 0
        self.bytes = 0
        self.gen = 0
        self.opened = time.time()
        self.name = "aic.%s.%d" % (sender, thread_id)
        self._block = memoryview(os.urandom(chunk))
        # [THE_TIMEOUT_IS_SHAPING_THE_DATA_V1] the connect timeout, then --link-timeout for every write
        self.timeout = connect_timeout if timeout is None else timeout
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.settimeout(connect_timeout)
        self.sock.connect(region)
        self.sock.settimeout(self.timeout)
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        diag("DIAG-PROD", "link opened", peer=machine, region="%s:%d" % region,
             thread_id=thread_id, timeout_s=self.timeout)

    def send(self, total_bytes: int) -> int:
        self.gen += 1
        _send_frame(self.sock, json.dumps({"op": "tput", "name": self.name, "gen": self.gen, "inc": None,
                                           "life": "session"}).encode())
        full, tail = divmod(int(total_bytes), self.chunk)
        parts = [self._block] * full + ([self._block[:tail]] if tail else [])
        _send_frame(self.sock, *(parts or [b""]))
        reply = bytes(_recv_frame(self.sock))
        if reply[:4] != b"OKAY":
            raise ConnectionError("region %s:%d refused the write: %r" % (self.region[0], self.region[1],
                                                                            reply[:120]))
        self.objects += 1
        self.bytes += total_bytes
        return total_bytes

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass


class RungGate:
    """Holds every worker at the rung boundary and releases them together.

    [A_RUNG_IS_N_SIMULTANEOUS_V1]
    The ramp says 1, then 2, then 4, then 8 simultaneous requests. It was not
    doing that. Threads were created and started running immediately, t0 was
    taken in the next statement, and BulkLink opened its socket lazily on the
    first send -- so a rung's measured window contained the connect storm for
    every thread added at that rung. Measured on the node: at the 256 rung the
    "link opened" lines ran from 41.85s to 44.50s, two and a half seconds of a
    four second window spent connecting rather than transferring, with the
    older threads already at full rate and the new ones not started.

    That is a rolling ramp-up, not N simultaneous anything, and it is why the
    early rungs read low and the curve looked like it was still climbing when
    it was mostly measuring thread startup.

    Now: every worker opens its links first, parks here, and the whole set is
    released in one call once the last one has arrived. No thread waits on
    another inside the measured window because no thread has anything left to
    set up when the window opens.
    """

    def __init__(self):
        self._cond = threading.Condition()
        self._open = False
        self._arrived = 0

    def arrive_and_wait(self, stop: threading.Event) -> None:
        """Worker side: I am warm and ready. Sleep until the rung opens."""
        with self._cond:
            self._arrived += 1
            self._cond.notify_all()
            while not self._open and not stop.is_set():
                self._cond.wait(0.5)

    def is_open(self) -> bool:
        return self._open

    def wait_for(self, n: int, timeout: float,
                 stop: threading.Event) -> int:
        """Main side: block until n workers are parked. Returns how many are."""
        deadline = time.time() + timeout
        with self._cond:
            while self._arrived < n and time.time() < deadline \
                    and not stop.is_set():
                self._cond.wait(0.1)
            return self._arrived

    def release(self) -> None:
        with self._cond:
            self._open = True
            self._cond.notify_all()

    def close_and_reset(self) -> None:
        """End the rung and re-arm. Workers fall out of their inner loop and
        come back to arrive_and_wait for the next one."""
        with self._cond:
            self._open = False
            self._arrived = 0


class Stats:
    def __init__(self):
        self.lock = threading.Lock()
        self.transfers = 0
        self.bytes = 0
        self.failures = 0
        self.inflight = 0
        self.errors: List[str] = []
        # [FAILURE_BREAKDOWN_V1] errors[] kept the FIRST 8 strings and nothing
        # else, and was only written into the JSON after stop_reason was set --
        # so a run that was Ctrl-C'd, or one whose failures changed character
        # partway, recorded nothing usable. 14,678 failures in one rung
        # produced zero diagnosis. Count every failure by (peer, port, kind),
        # forever, and report the breakdown per rung.
        self.by_kind: "collections.Counter" = collections.Counter()
        self.rung_base: "collections.Counter" = collections.Counter()
        #: thread_id -> when its CURRENT attempt began. [OUTSTANDING_MEANS_
        #: STUCK_NOT_BUSY_V1] The quantity the outstanding rule needs.
        self._attempt_started: Dict[int, float] = {}

    def start(self, thread_id: int = -1) -> None:
        with self.lock:
            self.inflight += 1
            if thread_id >= 0:
                self._attempt_started[thread_id] = time.time()

    def end(self, thread_id: int = -1) -> None:
        with self.lock:
            self.inflight -= 1
            if thread_id >= 0:
                self._attempt_started.pop(thread_id, None)

    def stalled(self, older_than: float) -> int:
        """How many attempts have been in flight longer than `older_than`.

        [OUTSTANDING_MEANS_STUCK_NOT_BUSY_V1]
        `inflight` cannot answer this. A worker's loop is start / send / end
        with nothing between, so at any sampling instant essentially every
        thread is inside an attempt: `inflight` has equalled `n_threads`
        exactly on every rung of every run -- 1/1, 2/2, 8/8, 512/512. It is a
        count of threads that EXIST, not of threads that are stuck, so
        `inflight / n_threads` is 1.0 always and the ratio carries no
        information at all.

        Age does carry it. An attempt still running after several step windows
        is genuinely not completing; one running for 20 ms is the workload
        working."""
        cutoff = time.time() - older_than
        with self.lock:
            return sum(1 for t0 in self._attempt_started.values() if t0 < cutoff)

    def oldest_attempt_s(self) -> float:
        with self.lock:
            if not self._attempt_started:
                return 0.0
            return time.time() - min(self._attempt_started.values())

    def ok(self, n: int) -> None:
        with self.lock:
            self.transfers += 1
            self.bytes += n

    def fail(self, msg: str, peer: str = "?", port: int = 0,
             exc: Optional[BaseException] = None) -> None:
        with self.lock:
            self.failures += 1
            if len(self.errors) < 8:
                self.errors.append(msg)
            kind = type(exc).__name__ if exc is not None else "?"
            err = getattr(exc, "errno", None) if exc is not None else None
            # [A_TYPE_NAME_IS_NOT_A_CAUSE_V1] The breakdown printed
            # "10.170.170.1:80 RuntimeError x8" and that is two different
            # faults wearing one name: send_rest raises RuntimeError for ANY
            # HTTP >= 300 from bulk.php, and the proxy raises its own for
            # "per-target limit (32) exceeded". A 503 from an overloaded proxy
            # and a 500 from a dying handler are not the same finding and the
            # count could not tell them apart.
            #
            # Errno already identifies the OSError family, so the message is
            # only carried where there is no errno. The peer and port are
            # already in the key, so they are stripped out of it.
            detail = ""
            if exc is not None and err is None:
                detail = str(exc).replace("%s:%d" % (peer, port), "")
                detail = " ".join(detail.split())[:64]
            self.by_kind[(peer, port, kind, err, detail)] += 1

    def rung_breakdown(self) -> str:
        """What failed in THIS rung, by peer and kind. The share per peer is
        the thing to read: with N peers chosen at random, one dead peer pins
        the failure rate at 1/N and the ramp climbs until that constant
        crosses a threshold and gets reported as a capacity limit."""
        with self.lock:
            delta = self.by_kind - self.rung_base
            self.rung_base = self.by_kind.copy()
        if not delta:
            return ""
        parts = []
        for (peer, port, kind, err, detail), n in delta.most_common(12):
            parts.append("%s:%s %s%s%s x%d"
                         % (peer, port, kind,
                            ("/errno=%s" % err) if err is not None else "",
                            (" [%s]" % detail) if detail else "", n))
        total = sum(delta.values())
        by_peer = collections.Counter()
        for key, n in delta.items():
            by_peer[key[0]] += n
        share = " ".join("%s=%.0f%%" % (pr, 100.0 * n / total)
                         for pr, n in by_peer.most_common())
        return "         failures: " + "; ".join(parts) + "  [share " + share + "]"


def _check_memory_budget(max_threads: int, obj_bytes: int, receivers: Dict[str, Dict[str, Any]],
                         transport: str) -> None:
    """[A_TRANSFER_IS_MEMORY_V1] Will the receivers' memory survive the ramp?

    Each sender thread holds one cell in every receiver it has written to, so at the last rung a receiver can hold
    max_threads objects from this producer. Refused up front with the arithmetic, like the descriptor budget, rather
    than discovered when a receiver runs out. [NO FALLBACKS] -- it does not quietly lower the ceiling.
    """
    if transport != "region":
        return
    need_mb = max_threads * obj_bytes / 1048576.0
    for m, v in sorted(receivers.items()):
        have = float(v.get("mem_available_mb") or 0)
        print("  memory budget  : %s holds up to %.0f MB (%d threads x %.1f MB) of %.0f MB available"
              % (m, need_mb, max_threads, obj_bytes / 1048576.0, have))
        if need_mb > have / 2:
            raise SystemExit(
                "this ramp could put %.0f MB into %s's memory (%d threads x %.1f MB objects), more than half of the "
                "%.0f MB it had available when its receiver started. Lower --max-threads to %d or --object-bytes "
                "to %d, or run on receivers with more memory."
                % (need_mb, m, max_threads, obj_bytes / 1048576.0, have,
                   int(have / 2 * 1048576 // max(1, obj_bytes)), int(have / 2 * 1048576 // max(1, max_threads))))


def _check_fd_budget(max_threads: int, n_peers: int, transport: str) -> None:
    """[CEILING_RAISED_V2] Will the descriptor limit survive the ramp?

    A bulk run holds one PERMANENT link per thread per peer, so the ceiling
    costs `max_threads * n_peers` descriptors all at once at the last rung --
    4096 at 2048 threads across two peers. Raising the thread ceiling without
    checking this trades a clean stop for EMFILE somewhere in the middle of a
    measurement, which is the failure this whole run exists to avoid.

    Refuses up front with the arithmetic rather than failing at rung 1024.
    [NO FALLBACKS] -- it does not quietly lower the ceiling to fit.
    """
    if transport != "region":
        return
    try:
        soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    except (ValueError, OSError):
        return
    need = max_threads * max(1, n_peers)
    have = soft - 64          # stdio, the store's HTTP sockets, results file
    print("  fd budget      : %d links (%d threads x %d peers) against "
          "RLIMIT_NOFILE %d/%d" % (need, max_threads, n_peers, soft, hard))
    if need > have:
        raise SystemExit(
            "this ramp would hold %d permanent links (%d threads x %d peers) "
            "but RLIMIT_NOFILE is %d, leaving room for about %d. It would "
            "fail with EMFILE partway through a rung and the measurement "
            "would be worthless.\n"
            "Raise the limit (frognet-system-tune.sh sets %d for the tuned "
            "services; a login shell gets it from "
            "/etc/security/limits.d/99-frognet.conf), or lower --max-threads "
            "to %d or less."
            % (need, max_threads, n_peers, soft, have, 65536,
               have // max(1, n_peers)))


def _fmt_age(sec) -> str:
    if sec is None:
        return "age?"
    if sec < 120:
        return "%.0fs" % sec
    if sec < 7200:
        return "%.0fm" % (sec / 60.0)
    return "%.1fh" % (sec / 3600.0)


def _near_line_rate(mb_s: float, caps: Dict[str, Any]) -> str:
    """[A_THROUGHPUT_NUMBER_NEEDS_A_LINE_RATE_V1] Say so when the plateau is
    the cable. A ramp reporting "more threads are not buying more bytes" on a
    link that is already full has found the NIC, not the mesh, and the two
    conclusions point at completely different work."""
    close = []
    for name in sorted(caps):
        cap = caps[name].get("max_mb_s")
        if cap and mb_s >= cap * 0.80:
            close.append("%s (%.1f MB/s line rate, %.0f%% used)"
                         % (name, cap, 100.0 * mb_s / cap))
    if not close:
        return ""
    return (" NOTE: this is at or near the physical line rate of "
            + ", ".join(close) + " -- the limit found is the wire, not the mesh.")


def should_extend(d_x: int, d_f: int, inflight: int,
                  now: float, deadline: float) -> bool:
    """Should this rung's window be extended?

    [EXTEND_ONLY_FOR_PENDING_NEVER_FOR_FAILING_V1]
    Extending exists to cover a step window shorter than one transfer cycle:
    threads are working, the rung simply ended before any of them finished.
    The condition tested only `d_x == 0 and inflight > 0`, and `inflight` is
    raised by Stats.start() before the attempt and lowered in `finally`,
    so a thread spinning connect -> fail -> retry reads as outstanding at
    nearly every sampling instant. A pure failure loop therefore looked
    identical to pending work.

    Measured: the 1024-thread rung extended four times while `d_f` went from
    1,591 to 14,678 and `d_x` never left zero. `d_f` was computed three lines
    above the loop and never consulted.

    A rung that is FAILING is a measurement, not an unfinished one. Any
    failure in the window ends the extension and lets the stop rules read it.
    """
    if d_x != 0:
        return False
    if d_f != 0:
        return False
    if inflight <= 0:
        return False
    return now < deadline


def worker(rv: "Receivers", peers: List[str], obj_bytes: int,
           ports: Dict[str, Tuple[str, int]], stop: threading.Event, stats: Stats,
           transport: str = "region", thread_id: int = 0,
           gate: "RungGate" = None) -> None:
    """One transfer thread: pick a primed peer and send. Repeat until stopped.

    [NO_RENDEZVOUS_PER_TRANSFER_V1]
    Each transfer used to request a connection, wait for BulkReady, send, then
    signal done -- a full rendezvous per object. That was complexity the design
    does not call for and it broke under load: several threads sharing a peer
    would each signal done, the receiver closed, and the rest got
    ConnectionRefused as soon as concurrency passed one thread per peer.

    The receiver is a property of the machine, not of a transfer. It is primed
    ONCE, before the ramp starts, and every thread sends to the same port for
    as long as the run lasts. A transfer is a connect and a send. Nothing else.
    """
    links: Dict[str, RegionLink] = {}
    try:
        _worker_loop(rv, peers, obj_bytes, transport, stats, stop, thread_id,
                     ports, links, gate)
    finally:
        for lk in links.values():
            lk.close()


def _prewarm(rv, peers, ports, transport, stats, thread_id, links) -> None:
    """[A_RUNG_IS_N_SIMULTANEOUS_V1] Everything that is not a transfer, done
    before the rung opens. For bulk that is the TCP connect and the identity
    header on a link to EVERY peer this thread may choose, so no measured
    window ever contains a connect. A link that cannot be built is reported and
    left absent; the worker fails against that peer on its first pick, which is
    a real failure and is counted as one."""
    if transport != "region":
        return
    for peer in peers:
        if peer in links:
            continue
        port = ports.get(peer)
        if not port:
            continue
        try:
            links[peer] = RegionLink(peer, port, rv.me, thread_id,
                                     timeout=_LINK_TIMEOUT[0])
        except Exception as e:
            diag_exc("DIAG-PROD", "prewarm failed", e, peer=peer, port=port,
                     thread_id=thread_id)


def _worker_loop(rv, peers, obj_bytes, transport, stats, stop, thread_id,
                 ports, links, gate=None):
    while not stop.is_set():
        if gate is not None:
            _prewarm(rv, peers, ports, transport, stats, thread_id, links)
            gate.arrive_and_wait(stop)
            if stop.is_set():
                return
        while (gate is None or gate.is_open()) and not stop.is_set():
            _one_attempt(rv, peers, obj_bytes, transport, stats, thread_id,
                         ports, links)
        if gate is None:
            return


def _one_attempt(rv, peers, obj_bytes, transport, stats, thread_id,
             ports, links):
    peer = random.choice(peers)
    port = ports.get(peer)
    if not port:
        stats.fail("%s has no region" % peer, peer=peer, port=0)
        diag("DIAG-PROD", "no region for peer", peer=peer,
             known_ports=ports, thread_id=thread_id)
        return
    stats.start(thread_id)
    try:
        if transport == "region":
            lk = links.get(peer)
            if lk is None:
                lk = links[peer] = RegionLink(peer, port, rv.me, thread_id, timeout=_LINK_TIMEOUT[0])
            n = lk.send(obj_bytes)
        else:
            n = send_rest(peer, 8080 if transport == "rest8080" else 80,
                          obj_bytes,
                          json_body=(transport == "semantic80"),
                          sender=rv.me, thread_id=thread_id)
        stats.ok(n)
    except Exception as e:
        # [THE_LINK_IS_PERMANENT_V1] Drop the broken link so the next
        # attempt builds a new one. Not a retry -- this attempt still
        # failed and is still counted.
        lk = links.pop(peer, None)
        if lk is not None:
            diag("DIAG-PROD", "link dropped after failure", peer=peer,
                 thread_id=thread_id, objects_on_link=lk.objects,
                 age_s=round(time.time() - lk.opened, 2),
                 exc_type=type(e).__name__)
            lk.close()
        stats.fail("%s: %s: %s" % (peer, type(e).__name__, e),
                   peer=peer,
                   port=(port[1] if transport == "region"
                         else (8080 if transport == "rest8080" else 80)),
                   exc=e)
    finally:
        stats.end(thread_id)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="AIConnect ramp across the mesh")
    p.add_argument("--receivers", type=int, default=0,
                   help="hold reads on the regions until this many receivers are there (0: those there now)")
    p.add_argument("--regions", default="",
                   help="host:port,... of the FrogTorch RAMs (default: those announced in the SystemRam)")
    p.add_argument("--step-seconds", type=float, default=4.0,
                   help="how long each rung runs before threads double")
    p.add_argument("--start-threads", type=int, default=1)
    p.add_argument("--object-bytes", type=int, default=4 << 20)
    p.add_argument("--transport", default="region",
                   choices=["region", "rest8080", "semantic80"],
                   help="how the object moves. region: written into the receiver's FrogTorch RAM "
                        "([AICONNECT_IN_THE_REGION_V1]). rest8080: HTTP straight to Apache (stock). "
                        "semantic80: HTTP through the FrogNet proxy -- CONTROL-PLANE SIZES ONLY, see "
                        "--object-bytes.")
    p.add_argument("--max-threads", type=int, default=DEFAULT_MAX_THREADS,
                   help="ceiling on the ramp. [THE_DRIVER_IS_NOT_THE_SUBJECT_V1] "
                        "Without one the ramp doubled to 2048 sender threads in "
                        "a single process: load hit 104 per core with 12,917 "
                        "failures, measuring this program's scheduler rather "
                        "than the mesh. Past this the answer is more client "
                        "MACHINES, not more threads on one.")
    p.add_argument("--link-timeout", type=float, default=30.0,
                   help="[THE_TIMEOUT_IS_SHAPING_THE_DATA_V1] socket timeout on "
                        "a bulk link's sends. Every stall duration in a run is "
                        "a multiple of this, so it decides what a 'slow peer' "
                        "looks like in the data. Default %(default)s.")
    p.add_argument("--warmup-timeout", type=float, default=60.0,
                   help="[A_RUNG_IS_N_SIMULTANEOUS_V1] how long to wait for "
                        "every thread to open its links and reach the start "
                        "line before the rung's clock starts. The rung is "
                        "refused rather than measured if they do not.")
    p.add_argument("--stall-seconds", type=float, default=0.0,
                   help="[OUTSTANDING_MEANS_STUCK_NOT_BUSY_V1] an attempt in "
                        "flight longer than this counts as stuck. 0 means "
                        "4x --step-seconds. A busy thread is not a stuck one, "
                        "and the old rule could not tell them apart.")
    p.add_argument("--saturation-rungs", type=int, default=2,
                   help="[SATURATION_IS_ITS_OWN_REASON_V1] stop after this "
                        "many consecutive rungs that fail to beat 90%% of the "
                        "best so far. 0 disables it and lets the ramp run to "
                        "--max-threads. Default %(default)d.")
    p.add_argument("--store-timeout", type=float, default=30.0)
    p.add_argument("--cpu-max-pct", type=float, default=50.0,
                   help="stop after TWO consecutive intervals above this")
    p.add_argument("--load-max-per-core", type=float, default=0.0,
                   help="[QUEUEING_IS_NOT_AN_ERROR_V1] OFF by default. A "
                        "thread blocked in sendall() burns no CPU and still "
                        "counts toward load, so high load means the client is "
                        "queueing -- which costs the client latency and the "
                        "measurement nothing. The failure a caller actually "
                        "sees is a timeout, and that is already covered by "
                        "the errors rule. Set a positive value only if you "
                        "want the ramp to stop on client queue depth.")
    p.add_argument("--mem-floor-mb", type=float, default=256.0)
    p.add_argument("--disk-floor-mb", type=float, default=1024.0)
    p.add_argument("--results-dir",
                   default=os.path.join(_TREE, "agent_workload", "results"))
    p.add_argument("--run-id", default=None)
    a = p.parse_args(argv)

    # [BULK_DOES_NOT_RIDE_THE_SEMANTIC_PATH_V1]
    # A 4 MB object POSTed to port 80 reaches the daemon as a ~15 MB FNW1 frame
    # and is refused: RuntimeError('invalid frame size 15840619'). That is the
    # architecture being right and the test being wrong. The semantic path
    # carries CONTROL-PLANE requests -- ids, status, locations -- and a bulk
    # object put through it is the same error as segmenting bulk into tuples.
    #
    # So the semantic arm is bounded to control-plane sizes. Comparing
    # rest8080 against semantic80 is a real and interesting comparison; it just
    # is not a bulk comparison, and running it at bulk sizes measures nothing
    # except the frame limit.
    if a.transport == "semantic80" and a.object_bytes > SEMANTIC_MAX_OBJECT:
        raise SystemExit(
            "--transport semantic80 with --object-bytes %d: the semantic path "
            "carries control-plane requests, not bulk. The body travels as one "
            "TYPE_STR field whose length prefix is a uint16, so %d bytes is the "
            "largest object it can carry -- a %.1f MB object is refused outright "
            "('invalid frame size'), and even 65536 fails, by one byte. Use "
            "--object-bytes %d or less for the REST-vs-semantic comparison, or "
            "--transport region to move volume."
            % (a.object_bytes, SEMANTIC_MAX_OBJECT,
               a.object_bytes / 1048576.0, SEMANTIC_MAX_OBJECT))

    # The results directory is used as a disk-space probe on every rung and
    # written at the end, so it must exist before the first reading.
    os.makedirs(a.results_dir, exist_ok=True)

    regions = [parse_region(x) for x in a.regions.split(",") if x.strip()] or None
    rv = Receivers(regions, a.store_timeout)
    me = rv.me
    found = rv.receivers(a.receivers)
    if not found or len(found) < a.receivers:
        raise SystemExit(
            "%d AIConnect receiver(s) in %d FrogTorch region(s) (%s), %s. Start the AIConnect service on the "
            "machines that should receive: it writes its receiver cell into its own machine's region."
            % (len(found), len(rv.regions), " ".join("%s:%d" % hp for hp in rv.regions),
               "wanted %d" % a.receivers if a.receivers else "wanted at least one"))
    # machine -> the region its memory is in (host, port)
    ports: Dict[str, Tuple[str, int]] = {m: tuple(v["region"]) for m, v in found.items()}
    targets = sorted(ports)

    # [PROVE_THE_PORT_IS_LIVE_V1] a receiver cell exists only while its service's session does, but the region
    # itself must also take connections from HERE
    if a.transport == "region":
        dead = {}
        for m in targets:
            why = rv.probe(*ports[m])
            if why:
                dead[m] = "%s:%d (%s)" % (ports[m][0], ports[m][1], why)
        if dead:
            raise SystemExit("%d of %d receivers' regions refuse connections from here: %s"
                             % (len(dead), len(targets), ", ".join("%s=%s" % kv for kv in sorted(dead.items()))))

    _LINK_TIMEOUT[0] = a.link_timeout
    if a.stall_seconds <= 0:
        a.stall_seconds = 4.0 * a.step_seconds

    _check_fd_budget(a.max_threads, len(targets), a.transport)
    _check_memory_budget(a.max_threads, a.object_bytes, found, a.transport)

    run_id = a.run_id or "aiconnect-%d" % int(time.time())
    print("AIConnect ramp from %s  (%s)" % (me, BUILD))
    print("  regions        : %s" % " ".join("%s:%d" % hp for hp in rv.regions))
    print("  receivers      : %d  %s" % (len(targets), " ".join(targets)))
    print("  transport      : %s" % a.transport)
    print("  object size    : %.1f MB, doubling threads every %.0fs"
          % (a.object_bytes / 1048576.0, a.step_seconds))
    # [PRIMED_MEANS_NOTHING_WITHOUT_AN_AGE_V1] The port alone hides the one
    # fact that predicted every failure of 2026-09-05: the two peers' receivers
    # were created 30 minutes apart, so they were not running the same code,
    # and 100% of the run's TimeoutErrors landed on the older one. A row's age
    # is how long since that peer last opened a receiver -- a large number
    # means its aiconnect has not restarted since, whatever you have copied
    # onto it.
    _ages = rv.ages()
    # [A_PEER_IS_ALSO_A_PATH_V1] The egress interface per peer, from
    # /proc/net/route. Two peers behaving differently under identical load may
    # simply be two different paths, and the run printed only addresses.
    _routes = peer_routes(sorted(ports))
    print("  receiver memory: %s"
          % " ".join("%s=%s:%d(%s via %s)"
                     % (m, p[0], p[1], _fmt_age(_ages.get(m)), _routes.get(m) or "route?")
                     for m, p in sorted(ports.items())))
    # [A_RELAYED_PEER_IS_NOT_A_SECOND_PEER_V1] Latency and hop distance per
    # peer, measured with sockets only. A peer reached THROUGH another target
    # is not a second target: it doubles the load on the one in between.
    _rtt, _onlink = {}, {}
    for _m in sorted(ports):
        _rtt[_m] = rv.probe_rtt(*ports[_m])
        _onlink[_m] = (rv.is_on_link(*ports[_m])
                       if _rtt[_m] is not None else None)
    print("  path per peer  : %s"
          % " ".join("%s=%s/%s" % (
              _m,
              ("%.1fms" % _rtt[_m]) if _rtt[_m] is not None else "unreachable",
              {True: "1hop", False: "RELAYED", None: "hops?"}[_onlink[_m]])
              for _m in sorted(ports)))
    _relayed = [m for m, v in _onlink.items() if v is False]
    if _relayed and len(ports) > 1:
        print("  WARNING        : %s reached through another node, not "
              "directly. If the relay is also a target, BOTH peers' bytes "
              "cross it and the ramp is loading one machine twice while "
              "reporting two. Per-peer failure shares below are about the "
              "PATH, not the peer."
              % ", ".join(sorted(_relayed)))
    _lat = [v for v in _rtt.values() if v]
    if len(_lat) > 1 and max(_lat) > 2.0 * min(_lat):
        print("  WARNING        : peer connect latency differs by %.1fx "
              "(%s). These are not interchangeable targets."
              % (max(_lat) / min(_lat),
                 " ".join("%s=%.1fms" % (m, _rtt[m])
                          for m in sorted(_rtt) if _rtt[m])))

    _ifaces = {i for i in _routes.values() if i}
    if len(_ifaces) > 1:
        print("  WARNING        : peers leave this box by DIFFERENT interfaces "
              "(%s). Per-peer results are not comparable until you know which "
              "path each took -- read the per-peer failure share below against "
              "this line."
              % ", ".join("%s=%s" % (m, _routes.get(m) or "?")
                          for m in sorted(_routes)))
    _spread = [a for a in (_ages.get(m) for m in ports) if a is not None]
    if len(_spread) > 1 and max(_spread) > 4 * max(1.0, min(_spread)):
        print("  WARNING        : receiver ages differ by %.0fx (%s). The "
              "peers are probably not running the same receiver build; "
              "compare failures per peer before comparing anything else."
              % (max(_spread) / max(1.0, min(_spread)),
                 " ".join("%s=%s" % (m, _fmt_age(_ages.get(m)))
                          for m in sorted(ports))))
    # [A_THROUGHPUT_NUMBER_NEEDS_A_LINE_RATE_V1] What the wire can physically
    # do, printed next to what the run achieved. Without it a plateau cannot
    # be told from a cable.
    _caps = link_capacity()
    print("  link capacity  : %s" % link_summary(_caps))
    print()
    # [A_RUNG_MEASURES_THE_BUFFER_NOT_THE_WIRE_V1] Two rates per rung.
    # sendall() returns when the kernel ACCEPTS the bytes, not when the NIC
    # transmits them. With 2048 sockets and autotuned send buffers a four
    # second rung can bank hundreds of megabytes that are still queued, so the
    # logical column is an OFFER rate. Measured on the Pi 5: the 2048 rung
    # claimed 112.95 MB/s while eth0 averaged 46.43 MB/s across the whole run
    # and never exceeded 41% of its payload capacity -- the "101% of the wire"
    # was a buffer, not a cable.
    print("%-8s %10s %10s %10s %9s %8s %9s %8s %8s %9s"
          % ("threads", "MB/s", "wire MB/s", "xfers/s", "failed", "cpu %",
             "load/core", "wg bad", "phys bad", "outstndg"))

    net0 = net_counters()
    _softnet0 = softnet_drops()
    _ramp_began_at = time.time()
    levels: List[Dict[str, Any]] = []
    threads: List[threading.Thread] = []
    stop = threading.Event()
    stats = Stats()
    gate = RungGate()
    n_threads = 0
    consec_outstanding = 0
    consec_flat = 0
    consec_cpu = 0
    consec_load = 0
    consec_fail = 0
    stop_reason: Optional[Tuple[str, str]] = None

    try:
        target_threads = a.start_threads
        while stop_reason is None:
            while n_threads < target_threads:
                t = threading.Thread(target=worker,
                                     args=(rv, targets, a.object_bytes, ports,
                                           stop, stats, a.transport, n_threads,
                                           gate),
                                     daemon=True, name="xfer-%d" % n_threads)
                t.start()
                threads.append(t)
                n_threads += 1

            # [A_RUNG_IS_N_SIMULTANEOUS_V1] Wait for every worker -- the ones
            # just created AND the ones from earlier rungs -- to be parked with
            # its links already open. Only then does the clock start, and the
            # whole set is released in one call.
            _warm = time.time()
            # [THE_FAILED_COLUMN_MISSED_THE_WARM_UP_V1]
            # `failed` counts only the measured window, but the rung breakdown
            # is diffed from the end of the previous rung, so it also covers
            # the warm-up -- and the two are printed on adjacent lines.
            # Measured on the Pi 5: the 4096 rung showed `failed 0` while the
            # line beneath it read "TimeoutError x62; TimeoutError x35". All
            # 97 timeouts happened during a 27-second start-line wait, which
            # is where they WOULD happen: the gate waits for stuck threads to
            # hit their socket timeout. The column a person reads said the
            # rung was clean.
            _f_warm0 = stats.failures
            parked = gate.wait_for(n_threads, a.warmup_timeout, stop)
            _warm_fails = stats.failures - _f_warm0
            if parked < n_threads:
                raise SystemExit(
                    "only %d of %d threads reached the start line within "
                    "%.0fs (%.1fs spent warming). A rung is %d SIMULTANEOUS "
                    "requests; starting it with %d of them ready would measure "
                    "the ramp-up instead. Threads that cannot open a link to a "
                    "peer are the usual cause -- check the [DIAG-PROD] prewarm "
                    "lines above."
                    % (parked, n_threads, a.warmup_timeout,
                       time.time() - _warm, n_threads, parked))
            diag("DIAG-PROD", "rung start line", threads=n_threads,
                 warm_s=round(time.time() - _warm, 2),
                 warm_failures=_warm_fails)
            if _warm_fails:
                print("         (%d failure(s) while %d threads reached the "
                      "start line over %.0fs -- these are real and are NOT in "
                      "the failed column below, which covers the measured "
                      "window only)"
                      % (_warm_fails, n_threads, time.time() - _warm),
                      flush=True)

            _warm_elapsed = time.time() - _warm
            lvl_retries = 0
            _rung_net0 = net_counters()
            b0, x0, f0 = stats.bytes, stats.transfers, stats.failures
            t0 = time.time()
            gate.release()
            time.sleep(a.step_seconds)
            dt = time.time() - t0
            d_bytes = stats.bytes - b0
            d_x = stats.transfers - x0
            d_f = stats.failures - f0

            idle = cpu_idle_pct()
            cpu_used = 100.0 - idle
            load = loadavg_per_core()
            mem = mem_available_mb()
            disk = disk_free_mb(a.results_dir)
            net = net_delta(net0)
            # This rung's own wire bytes, transmit side, physical only.
            _rung_tx = 0
            _rung_now = net_counters()
            for _n, _c in _rung_now.items():
                _b = _rung_net0.get(_n)
                if _b is None or _c["is_wg"]:
                    continue
                _rung_tx += _c["tx_bytes"] - _b["tx_bytes"]
            _wire_rate = _rung_tx / 1048576.0 / dt

            lvl = {"threads": n_threads,
                   "mb_per_s": d_bytes / 1048576.0 / dt,
                   "wire_mb_per_s": _wire_rate,
                   "warm_failures": _warm_fails,
                   "warm_seconds": round(_warm_elapsed, 2),
                   "xfers_per_s": d_x / dt, "failed": d_f,
                   "cpu_used_pct": cpu_used, "load_per_core": load,
                   "mem_available_mb": mem, "disk_free_mb": disk,
                   "wg_errors": net["wg_bad"], "phys_errors": net["phys_bad"],
                   "wg_mb": net["wg_bytes"] / 1048576.0,
                   "phys_mb": net["phys_bytes"] / 1048576.0,
                   "ambient_drops": net.get("ambient", []),
                   "hw_errors": net.get("hw_errors", 0),
                   "queue_drops": net.get("queue_drops", 0),
                   "counter_breakdown": net.get("counter_breakdown", {}),
                   "softnet_backlog_drops": softnet_drops() - _softnet0,
                   "link_capacity": _caps,
                   "peer_routes": _routes,
                   "peer_rtt_ms": _rtt, "peer_on_link": _onlink}
            levels.append(lvl)
            print("%-8d %10.2f %10.2f %10.2f %9d %8.1f %9.2f %8d %8d %5d/%-4d"
                  % (n_threads, lvl["mb_per_s"], _wire_rate,
                     lvl["xfers_per_s"], d_f, cpu_used, load,
                     net["wg_bad"], net["phys_bad"],
                     stats.inflight, n_threads))
            _bd = stats.rung_breakdown()
            if _bd:
                print(_bd, flush=True)
            lvl["failure_breakdown"] = _bd
            diag("DIAG-PROD", "rung", threads=n_threads,
                 mb_s=round(lvl["mb_per_s"], 2), xfers=d_x, failed=d_f,
                 inflight=stats.inflight, cpu=cpu_used, load=load,
                 breakdown=_bd)

            # [A_RUNG_THAT_MOVED_NOTHING_IS_NOT_A_MEASUREMENT_V1]
            # The first rung can complete zero transfers simply because a
            # rendezvous plus one object does not fit in the step window, and a
            # 0.00 MB/s rung tells you nothing about capacity. Rather than
            # stopping on resource readings taken while nothing was happening,
            # the rung is retried once with the window doubled; if it still
            # moves nothing, that is a real failure to transfer and it says so.
            # [IN_FLIGHT_IS_NOT_FAILURE_V1]
            # A rung showing 0 transfers and 0 failures usually means the step
            # window is shorter than one rendezvous-plus-send cycle, not that
            # the system moved nothing. Threads still working are counted and
            # the window is extended while any remain, rather than reporting
            # 0.00 MB/s as if that were a capacity reading.
            # Extending is for a window shorter than one transfer cycle. If
            # attempts are FAILING rather than pending, extending just counts
            # more failures -- measured as a rung sitting on one in-flight
            # thread while a thousand others failed, and the ramp never
            # returning.
            # Bounded by WALL TIME, not by a failure count a fast ramp blows
            # through in one pass. Extending exists to cover a window shorter
            # than one transfer cycle; past a few multiples of the step it is
            # not waiting for a slow transfer, it is hanging.
            _extend_deadline = time.time() + 4 * a.step_seconds
            while should_extend(d_x, d_f, stats.inflight,
                                time.time(), _extend_deadline):
                print("         (%d transfer(s) still in flight after %.0fs -- "
                      "extending)" % (stats.inflight, dt))
                lvl_retries += 1
                levels.pop()
                time.sleep(a.step_seconds)
                d_bytes = stats.bytes - b0
                d_x = stats.transfers - x0
                d_f = stats.failures - f0
                dt = time.time() - t0
                lvl["mb_per_s"] = d_bytes / 1048576.0 / dt
                lvl["xfers_per_s"] = d_x / dt
                levels.append(lvl)
                print("%-8d %10.2f %10.2f %10.2f %9d %8.1f %9.2f %8d %8d "
                      "%5d/%-4d"
                      % (n_threads, lvl["mb_per_s"],
                         lvl.get("wire_mb_per_s", 0.0), lvl["xfers_per_s"],
                         d_f, cpu_used, load, net["wg_bad"], net["phys_bad"],
                         stats.inflight, n_threads))

            # [A_RUNG_IS_N_SIMULTANEOUS_V1] The window -- including any
            # extension -- is over. Park every worker so the next rung can
            # start with all of them, old and new, on the same start line.
            # Closed HERE and not before the extend loop: extending is more of
            # the same window, and parking the workers for it would extend a
            # rung in which nothing was allowed to happen.
            gate.close_and_reset()

            # [FOUR_STOP_RULES_V1] Each is a state of the SYSTEM under load,
            # and each needs to persist or be unambiguous before it counts.
            #
            #   outstanding : more than half the launched threads still have a
            #                 transfer in flight at the end of a rung. Once is
            #                 a slow rung; TWICE is the system no longer
            #                 keeping up with what it was given.
            #   cpu         : above the threshold for two consecutive rungs. A
            #                 single sample catches a scheduling spike, not a
            #                 saturated machine.
            #   disk        : out of space. Immediate -- there is no second
            #                 reading that makes a full disk acceptable.
            #   errors      : a transfer failed. A system that has stopped
            #                 serving is saturated regardless of the counters.
            # [OUTSTANDING_MEANS_STUCK_NOT_BUSY_V1]
            # This was stats.inflight / n_threads. That ratio is 1.0 on every
            # rung of every run ever recorded here -- 1/1, 2/2, 8/8, 512/512 --
            # because a worker is inside an attempt except for the few
            # microseconds between one ending and the next beginning. So the
            # rule below never turned on outstanding work at all; it was
            # `not improving` wearing an outstanding-shaped name, and it
            # reported a throughput plateau as "the system is not keeping up".
            # Measured: semantic80 saturating at 4.62 MB/s by 8 threads stopped
            # the ramp at 32 with zero failures and 252 transfers in the final
            # 4s window -- nothing was stuck, and the reason printed said it
            # was.
            #
            # Count attempts by AGE instead. An attempt still running after
            # a.stall_seconds is not completing; the rest are just working.
            stalled = stats.stalled(a.stall_seconds)
            outstanding_frac = (stalled / float(n_threads)) if n_threads else 0.0
            lvl["outstanding"] = stalled
            lvl["outstanding_frac"] = outstanding_frac
            lvl["inflight"] = stats.inflight
            lvl["oldest_attempt_s"] = round(stats.oldest_attempt_s(), 2)

            # [OUTSTANDING_ONLY_COUNTS_UNDER_LOAD_V1]
            # Half the threads in flight at an interval boundary is normal when
            # transfers are short: at 128 KB objects the ramp stopped at TWO
            # threads doing 33 transfers/s with 6% CPU, having caught both
            # threads mid-connect. That is not a system failing to keep up.
            #
            # It only counts as falling behind if throughput has stopped
            # improving. While each rung is still moving more than the last,
            # outstanding threads are just work in progress.
            # [IMPROVING_NEEDS_A_REAL_MARGIN_V1] 5% is inside the run-to-run
            # noise of this workload: a rung that went 9.36 -> 9.77 MB/s, a
            # 4.4% gain, was counted as "not improving" and stopped the ramp at
            # 32 threads while it was plainly still climbing -- the same ramp
            # reached 512 on the next run. Compare against the best rung so far
            # rather than the immediately previous one, so a single soft rung
            # in a rising series cannot end it, and require nothing more than
            # "not clearly falling".
            best = max((l["mb_per_s"] for l in levels[:-1]), default=0.0)
            improving = (len(levels) < 3 or lvl["mb_per_s"] >= best * 0.90)
            # [ONE_FAILURE_IS_NOISE_V1] This stopped on the FIRST failed
            # transfer. On a mesh with occasional transient stalls that ends
            # the ramp at the first hiccup and produces no measurement at all
            # -- semantic80 stopped at one thread having moved nothing, while a
            # curl loop of 200 sequential requests through the same path
            # returned 200 every time and simply paused in the middle.
            #
            # A failed transfer is not a capacity limit. Failures becoming the
            # MAJORITY of what is attempted, and staying there across two
            # intervals, is. Same rule as CPU and outstanding: one sample of
            # anything is noise, and every stop condition has to persist.
            attempted = d_f + d_x
            fail_frac = (d_f / float(attempted)) if attempted else 0.0
            lvl["fail_frac"] = fail_frac
            if fail_frac > 0.5:
                consec_fail += 1
            else:
                consec_fail = 0

            if outstanding_frac > 0.5:
                consec_outstanding += 1
            else:
                consec_outstanding = 0

            # [SATURATION_IS_ITS_OWN_REASON_V1] Throughput ceasing to climb is
            # a real thing to notice and it is NOT threads being stuck. It gets
            # its own counter and its own printed reason, so the stop line says
            # what actually happened. --saturation-rungs 0 turns it off, for
            # when the point of the run is to reach the thread ceiling.
            if not improving:
                consec_flat += 1
            else:
                consec_flat = 0
            if cpu_used > a.cpu_max_pct:
                consec_cpu += 1
            else:
                consec_cpu = 0

            # [LOAD_IS_THE_ONE_CPU_CANNOT_SEE_V1]
            # CPU% does not detect this failure and never could. A thread
            # blocked in sendall() on a full socket buffer is in
            # uninterruptible sleep: it consumes no CPU and it counts toward
            # load average. Measured on the Pi 5 at 8192 threads: load 46.35
            # per core with cpu_used reading 11.4%, and the ramp called that
            # "without hitting a resource limit".
            #
            # Thousands of threads parked in the kernel's send path is not
            # offered load, it is queue. The rung above it went DOWN --
            # 153.11 MB/s at 4096, 138.75 at 8192 -- while the run-average
            # wire rate sat at 36% of the link.
            # [QUEUEING_IS_NOT_AN_ERROR_V1] Load is REPORTED, never a stop.
            # A client parking threads in the kernel's send path costs the
            # client latency and costs the measurement nothing; the error the
            # caller actually sees is a timeout, and that is already a stop
            # condition. Stopping on load would have ended the Pi 5 ramp at
            # 4096 -- the rung that produced the highest throughput of the
            # run and the first client-visible failures, which is exactly the
            # rung worth reaching.
            if a.load_max_per_core > 0 and load > a.load_max_per_core:
                consec_load += 1
            else:
                consec_load = 0

            if disk < a.disk_floor_mb:
                stop_reason = ("disk", "%.0f MB free, below %.0f MB"
                               % (disk, a.disk_floor_mb))
            elif fail_frac > 0.5 and consec_fail >= 2:
                stop_reason = ("errors",
                               "%.0f%% of attempts failed for two consecutive "
                               "intervals (%d of %d this interval): %s"
                               % (fail_frac * 100.0, d_f, d_f + d_x,
                                  "; ".join(stats.errors[:3])))
            elif consec_outstanding >= 2:
                stop_reason = ("outstanding",
                               "%d of %d threads (%.0f%%) had an attempt older "
                               "than %.1fs at the end of two consecutive "
                               "intervals (oldest %.1fs) -- transfers are "
                               "starting and not finishing"
                               % (stalled, n_threads, outstanding_frac * 100.0,
                                  a.stall_seconds, stats.oldest_attempt_s()))
            elif a.saturation_rungs and consec_flat >= a.saturation_rungs:
                stop_reason = ("saturated",
                               "throughput stopped improving for %d consecutive "
                               "rungs (%.2f MB/s against a best of %.2f) -- "
                               "more threads are not buying more bytes. Nothing "
                               "is stuck and nothing failed; pass "
                               "--saturation-rungs 0 to ramp to the thread "
                               "ceiling anyway.%s"
                               % (consec_flat, lvl["mb_per_s"], best,
                                  _near_line_rate(best, _caps)))
            elif a.load_max_per_core > 0 and consec_load >= 2:
                stop_reason = ("load",
                               "load %.2f per core for two consecutive "
                               "intervals, above %.1f, at %.1f%% CPU. Threads "
                               "blocked in the kernel's send path count "
                               "toward load and burn no CPU, so this is queue "
                               "depth, not work: the client is parking "
                               "threads, not driving the mesh harder."
                               % (load, a.load_max_per_core, cpu_used))
            elif consec_cpu >= 2:
                stop_reason = ("cpu",
                               "%.1f%% CPU for two consecutive intervals, above "
                               "%.0f%%" % (cpu_used, a.cpu_max_pct))
            elif net["wg_bad"] > 0:
                stop_reason = ("network-overlay",
                               "%d errors/drops on WireGuard: %s -- the tunnel "
                               "is backing up, not the wire"
                               % (net["wg_bad"], ", ".join(net["culprits"])))
            elif net["phys_bad"] > 0:
                # [A_DROP_IS_NOT_A_CARD_ERROR_V1] Say which counter moved.
                # rx_errs/tx_errs are the wire. rx_drop/tx_drop are queues --
                # the card delivered or accepted the packet and something
                # above it could not keep up. They lead to opposite work and
                # this line used to merge them.
                _hw, _q = net.get("hw_errors", 0), net.get("queue_drops", 0)
                if _hw and not _q:
                    _kind = ("HARDWARE: rx_errs/tx_errs only -- the wire or "
                             "the adapter")
                elif _q and not _hw:
                    _kind = ("QUEUES, NOT HARDWARE: rx_drop/tx_drop only -- "
                             "the card is fine and something above it could "
                             "not keep up. Compare against packet RATE, not "
                             "bytes: this run's softnet backlog drops were %d"
                             % (softnet_drops() - _softnet0))
                else:
                    _kind = ("MIXED: %d hardware (rx_errs/tx_errs), %d queue "
                             "(rx_drop/tx_drop)" % (_hw, _q))
                stop_reason = ("network-physical",
                               "%d errors/drops on physical interfaces: %s -- %s"
                               % (net["phys_bad"], ", ".join(net["culprits"]),
                                  _kind))
            elif mem < a.mem_floor_mb:
                stop_reason = ("memory", "%.0f MB available, below %.0f MB"
                               % (mem, a.mem_floor_mb))
            elif n_threads >= a.max_threads:
                # [THE_CEILING_IS_ONLY_CLEAN_IF_THE_CLIENT_WAS_V1] This said
                # "the mesh absorbed everything this client could offer"
                # unconditionally. At 8192 threads on the Pi 5 the client was
                # at load 46 per core with the last rung SLOWER than the one
                # before it; the mesh had not absorbed anything, the client
                # had queued it.
                if load > 8.0:
                    stop_reason = ("driver-ceiling",
                                   "reached --max-threads %d, but at load "
                                   "%.2f per core. That load is the client "
                                   "queueing in the send path, not the mesh "
                                   "refusing work -- it is only a problem if "
                                   "the failure column above shows timeouts. "
                                   "Check that before adding client "
                                   "MACHINES." % (a.max_threads, load))
                else:
                    stop_reason = ("driver-ceiling",
                                   "reached --max-threads %d without hitting "
                                   "a resource limit (load %.2f per core). "
                                   "The mesh absorbed everything this client "
                                   "could offer; add client MACHINES to push "
                                   "further." % (a.max_threads, load))
            else:
                target_threads = min(n_threads * 2, a.max_threads)
    finally:
        stop.set()
        for t in threads:
            t.join(timeout=2.0)
        # The receiver belongs to the machine and stays open for the next run.
        # Nothing here tears it down.

    kind, detail = stop_reason
    peak = max(levels, key=lambda x: x["mb_per_s"])
    _run_seconds = max(1e-9, time.time() - _ramp_began_at)
    print()
    print("STOPPED: %s -- %s" % (kind.upper(), detail))
    print("PEAK   : %.2f MB/s, %.2f transfers/s at %d threads"
          % (peak["mb_per_s"], peak["xfers_per_s"], peak["threads"]))
    # [THE_PEAK_IS_A_FRACTION_OF_SOMETHING_V1] The peak was printed as a bare
    # number and the line rate was printed 30 lines earlier in the header, so
    # reading one against the other was left to the person. Measured
    # 2026-09-05: bulk peaked at 114.18 MB/s on a 1000Mbit link -- 95.8% of the
    # wire, i.e. the run had found the cable -- and the output said only
    # "114.18 MB/s". The comparison was previously made ONLY on a `saturated`
    # stop, which is the one stop reason that cannot happen when the ramp runs
    # to its thread ceiling, so the case that most needed it never got it.
    # [PAYLOAD_IS_NOT_LINE_RATE_V1] Compare payload against the payload
    # maximum, not against the signalling rate. At a 1500 MTU those differ by
    # about 6%, which is the whole reason 114.18 MB/s read as "95.8% of the
    # wire" -- a figure above what 1500-byte Ethernet can actually carry.
    _share, _impossible = [], []
    for _n in sorted(_caps):
        _pay = _caps[_n].get("max_payload_mb_s")
        _raw = _caps[_n].get("max_mb_s")
        if not _pay:
            continue
        _pct = 100.0 * peak["mb_per_s"] / _pay
        _share.append("%.0f%% of what %s can carry as TCP payload "
                      "(%.1f MB/s at MTU %s; %.1f MB/s raw signalling)"
                      % (_pct, _n, _pay, _caps[_n].get("mtu"), _raw))
        if _pct > 100.0:
            _impossible.append((_n, _pct, _pay))
    if _share:
        print("         %s" % "; ".join(_share))
    for _n, _pct, _pay in _impossible:
        print("         IMPOSSIBLE: %.0f%% of the payload capacity of %s. "
              "The usual cause is that this is a LOGICAL rate: sendall() "
              "returns when the kernel accepts the bytes, not when the NIC "
              "sends them, so a short rung with thousands of sockets banks "
              "buffered data it has not transmitted. Compare the wire MB/s "
              "column for that rung; if it is well below this figure, the "
              "excess was queued, not carried. Otherwise the MTU or the link "
              "speed is not what sysfs reports."
              % (_pct, _n))
    if _share and not _impossible:
        _hot = [c for c in _share if float(c.split("%")[0]) >= 90.0]
        if _hot:
            print("         The limit found is the WIRE. More threads, more "
                  "client machines and a faster receiver all move nothing "
                  "from here; a faster link does.")

    # [LOGICAL_IS_NOT_WIRE_V1]
    # `stats.bytes` is what the WORKLOAD handed to the transport. It is not
    # what the wire moved, and on the semantic path it is not supposed to be:
    # the whole proposition is that only SAME/DIFF crosses the link, so a run
    # that presents 171.2 MB of objects while the interfaces move 1.9 MB has
    # not failed to move 171 MB -- it has moved 171 MB of objects for 1.9 MB
    # of wire.
    #
    # Reporting one MB/s figure for both arms compared a wire rate against a
    # logical rate and called the second one ten times slower. It is not the
    # same measurement. Bulk sends os.urandom over a socket, so its logical
    # and wire figures should sit within framing overhead of each other --
    # measured 1.157 -- and any large gap there means something else is on the
    # interface. The semantic arm SHOULD show a large gap; that gap is the
    # result.
    #
    # So both numbers are printed, and the ratio between them is named for
    # what it is.
    # [SAY_WHICH_INTERFACE_CARRIED_IT_V1] The witness, per interface, TX only.
    #
    # The first version summed rx+tx across PHYSICAL interfaces only and
    # reported one ratio. It could not answer either question it was built
    # for. Bulk came back 1.157 -- far above the ~1.06 that 1500-byte framing
    # costs -- because the sum folded in ACKs and every unrelated flow on the
    # box. Semantic came back 0.011, 171.2 MB sent against 1.9 MB counted,
    # because the overlay interfaces were EXCLUDED from the witness, so
    # traffic riding wg was subtracted rather than attributed.
    #
    # A sender is witnessed by TX on the interface that actually carried it.
    # Overlays are shown too, and named as overlays: payload crossing wg0 is
    # counted again, encapsulated, on whatever carries wg0, so the two are not
    # additive and the table exists so nobody has to guess which is which.
    _fin = net_delta(net0)
    _per = _fin.get("per_iface", {})
    _app_bytes = stats.bytes
    _carrier = carrier_of(_per)
    if _per:
        print("         carried by     : %s" % iface_table(_per))
    if _carrier and _app_bytes > 0:
        _tx = _per[_carrier]["tx"]
        _ratio = wire_overhead_ratio(_app_bytes, _tx)
        print("         wire/payload   : %.3f on %s  (%.1f MB sent by the "
              "workload, %.1f MB transmitted by %s)"
              % (_ratio, _carrier, _app_bytes / 1048576.0,
                 _tx / 1048576.0, _carrier))
        # [TWO_EFFICIENCIES_V1] Both ratios, over the SAME window.
        #
        # Run averages, not the peak rung: the peak is one 4-second slice and
        # the interface counters cover the whole run, so dividing one by the
        # other would compare different periods and quietly inflate whichever
        # arm had the spikier curve.
        #
        #   wire efficiency  -- of the link's payload capacity, how much did
        #                       the bytes ACTUALLY on the wire use. This is the
        #                       "am I filling the pipe" number and it cannot
        #                       exceed 100% on a correctly attributed link.
        #   REST-equivalent  -- the same objects if every byte had to cross,
        #                       which is what a transport without SAME/DIFF
        #                       would have needed. It CAN exceed 100%, and
        #                       when it does it means the link could not have
        #                       carried this workload at all in that form.
        #
        # The gap between them is the reduction; the reduction alone does not
        # say whether the link is busy, and the wire figure alone does not say
        # what was delivered. Neither is the headline on its own.
        _logical_mb_s = _app_bytes / 1048576.0 / max(1e-9, _run_seconds)
        _wire_mb_s = (_tx / 1048576.0) / max(1e-9, _run_seconds)
        print("         logical %.2f MB/s of objects, wire %.2f MB/s on %s "
              "(run averages over %.1fs)"
              % (_logical_mb_s, _wire_mb_s, _carrier, _run_seconds))
        _pay_cap = _caps.get(_carrier, {}).get("max_payload_mb_s")
        if _pay_cap:
            _eff_wire = 100.0 * _wire_mb_s / _pay_cap
            _eff_rest = 100.0 * _logical_mb_s / _pay_cap
            print("         wire efficiency: %6.2f%%  of %s payload capacity "
                  "(%.1f MB/s) -- what the link actually carried"
                  % (_eff_wire, _carrier, _pay_cap))
            print("         REST-equivalent: %6.2f%%  of the same capacity -- "
                  "what these objects would have needed with every byte on "
                  "the wire" % _eff_rest)
            if _eff_rest > 100.0:
                print("         A transport without SAME/DIFF could not have "
                      "delivered this on this link: it would need one %.2fx "
                      "faster." % (_eff_rest / 100.0))
            _peak_wire = peak.get("wire_mb_per_s")
            if _peak_wire is not None:
                print("         peak rung wire rate: %.2f MB/s against its "
                      "%.2f MB/s logical -- the gap is bytes accepted by the "
                      "kernel but not yet transmitted."
                      % (_peak_wire, peak["mb_per_s"]))
            if _eff_wire > 100.0:
                # Not a result -- a contradiction, and it points at the
                # denominator or the attribution rather than at the link.
                print("         WIRE EFFICIENCY ABOVE 100%% IS IMPOSSIBLE. "
                      "Either %s is not carrying only this workload, or its "
                      "MTU is larger than the %s sysfs reports (at MTU 9000 "
                      "the payload capacity is %.1f MB/s, not %.1f), or the "
                      "link is faster than it claims. The table above says "
                      "which interfaces moved bytes; that is the place to "
                      "look."
                      % (_carrier, _caps.get(_carrier, {}).get("mtu"),
                         (_caps[_carrier]["max_mb_s"] * (9000 - 52.0)
                          / (9000 + 38.0)) if _caps.get(_carrier, {}).get("max_mb_s")
                         else float("nan"), _pay_cap))
        elif _carrier:
            print("         (%s reports no link speed, so neither efficiency "
                  "can be computed -- wifi and virtual interfaces do not "
                  "publish one in sysfs)" % _carrier)
        if _ratio < 0.9:
            if _per.get(_carrier, {}).get("is_wg"):
                print("         (%s is an OVERLAY: this payload is counted "
                      "again, encapsulated, on whatever carries %s. The "
                      "figure belongs to that overlay, not to the NIC under "
                      "it.)" % (_carrier, _carrier))
            print("         SEMANTIC REDUCTION %.1fx -- %.1f MB of objects "
                  "moved for %.1f MB on the wire. This is not a shortfall. "
                  "The MB/s column above is LOGICAL throughput; comparing it "
                  "against a transport that puts every byte on the wire "
                  "compares two different quantities."
                  % (1.0 / max(1e-9, _ratio), _app_bytes / 1048576.0,
                     _tx / 1048576.0))
            for _n in sorted(_caps):
                _pay = _caps[_n].get("max_payload_mb_s")
                if _pay and _n == _carrier:
                    print("         at this reduction %s could carry %.0f MB/s "
                          "of objects before the wire is the limit; the "
                          "%.2f MB/s reached is bounded by something else "
                          "(CPU, the proxy, the daemon), not by the link."
                          % (_n, _pay / max(1e-9, _ratio),
                             _app_bytes / 1048576.0 / max(1e-9, _run_seconds)))
        elif _ratio > 1.15:
            print("         %.0f%% more on the wire than in the payload. "
                  "1500-byte framing costs about 6%%, so the rest is other "
                  "traffic sharing this interface -- the figure above is not "
                  "this workload alone. NOTE: this arm shows no semantic "
                  "reduction at all, which for a transport sending "
                  "os.urandom is expected: random bytes have no SAME to find."
                  % (100.0 * (_ratio - 1.0)))
        else:
            print("         no semantic reduction (wire/payload %.3f): every "
                  "object byte crossed the link. Expected for an arm that "
                  "sends incompressible bytes; NOT expected for one that is "
                  "meant to send SAME/DIFF." % _ratio)

    out = os.path.join(a.results_dir, run_id + ".aiconnect.json")
    with open(out, "w") as f:
        json.dump({"run_id": run_id, "from": me, "targets": targets,
                   "transport": a.transport,
                   "object_bytes": a.object_bytes,
                   "step_seconds": a.step_seconds,
                   "stop_reason": kind, "stop_detail": detail,
                   "peak_mb_per_s": peak["mb_per_s"],
                   "peak_threads": peak["threads"],
                   "levels": levels, "errors": stats.errors}, f, indent=2)
    print("  -> %s" % out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
