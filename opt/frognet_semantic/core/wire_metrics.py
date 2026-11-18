# Copyright (C) 2016-2026 Fawcett Innovations LLC
# SPDX-License-Identifier: GPL-2.0-only
"""[WHERE_THE_TIME_GOES_V1] A clock at every boundary a request crosses inside a transport driver.

John, 2026-11-17: "If you are updating to fix the driver, add metrics so we can find the bottlenecks. And not just
where you think they are."

So nothing here chooses a stage to watch. A driver marks EVERY boundary a request crosses on its side (handed to the
driver, queued, taken by the wire manager, sent, reply arrived, handed up, waiter woke; on the daemon: arrived, to the
engine, engine replied, writer took it, given to the wire manager, sent), and at each mark it may say any fact it has
in hand (queue depths, how many are outstanding, how many threads the process has). This module keeps every interval
between two marks that followed one another in time, by the size of the frame, and says them in the process's own
log:

  [WIRE-METRICS]      one table per kind of trail and size class, when 1, 2, 5, 10, 20, 50 ... of them have finished,
                      then at every EVERY, and when the driver says flush(): for each interval its count, median, 95th percentile, largest, and its
                      share of all the time the trails took. The largest share is where the time goes.
  [WIRE-METRICS-ONE]  each trail of a frame of BIG bytes or more, mark by mark, so one slow large frame can be read.

An interval is named by the two marks at its ends, in the order they happened for that request. Nothing is assumed
about the order: a reply's first piece can arrive before the request's last piece is sent, and then that is what the
table says. Nothing here runs on a timer: a table is written when a count is reached or when it is asked for.

Turned off with FROGNET_WIRE_METRICS=0 (then start() returns None and every other call returns at once)."""
import os
import threading
import time
from typing import Dict, List, Optional

BUILD = "wire-metrics-v1-20261118a"
ON = os.environ.get("FROGNET_WIRE_METRICS", "1") != "0"
EVERY = int(os.environ.get("FROGNET_WIRE_METRICS_EVERY", "500"))
BIG = int(os.environ.get("FROGNET_WIRE_METRICS_BIG", "100000"))
KEPT = 4000                                    # values kept per interval for the percentiles; counts and sums are of all
SIZES = (1000, 10000, 100000, 1000000, 10 ** 12)

_lock = threading.Lock()
_tables: Dict[tuple, "_Table"] = {}


class Trail:
    __slots__ = ("who", "kind", "marks", "facts", "bytes_out", "bytes_in", "closed")

    def __init__(self, who: str, kind: str):
        self.who, self.kind = who, kind
        self.marks: Dict[str, int] = {}
        self.facts: Dict[str, float] = {}
        self.bytes_out = self.bytes_in = 0
        self.closed = False


class _Table:
    def __init__(self):
        self.n = 0
        self.total_ns = 0
        self.spans: Dict[str, List] = {}       # name -> [count, sum_ns, max_ns, [values]]
        self.facts: Dict[str, List] = {}       # name -> [count, sum, max]
        self.since = 0


def start(who: str, kind: str, **facts) -> Optional[Trail]:
    if not ON:
        return None
    t = Trail(who, kind)
    mark(t, "start", **facts)
    return t


def mark(t: Optional[Trail], stage: str, first: bool = False, **facts) -> None:
    """One boundary crossed. first=True keeps the earliest time for a stage crossed many times (a frame's pieces);
    otherwise the latest. Each fact is a number the caller has in hand at that moment."""
    if t is None or t.closed:
        return
    now = time.perf_counter_ns()
    if not (first and stage in t.marks):
        t.marks[stage] = now
    for k, v in facts.items():
        t.facts[stage + "." + k] = v


def done(t: Optional[Trail], bytes_out: int = 0, bytes_in: int = 0) -> None:
    if t is None or t.closed:
        return
    t.closed = True
    t.bytes_out, t.bytes_in = bytes_out or t.bytes_out, bytes_in or t.bytes_in
    order = sorted(t.marks.items(), key=lambda kv: kv[1])
    size = max(t.bytes_out, t.bytes_in)
    cls = next(s for s in SIZES if size <= s)
    say = None
    with _lock:
        tb = _tables.setdefault((t.who, t.kind, cls), _Table())
        tb.n += 1
        tb.since += 1
        tb.total_ns += order[-1][1] - order[0][1]
        for (a, ta), (b, tb_ns) in zip(order, order[1:]):
            s = tb.spans.setdefault(a + ">" + b, [0, 0, 0, []])
            d = tb_ns - ta
            s[0] += 1; s[1] += d; s[2] = max(s[2], d)
            if len(s[3]) < KEPT:
                s[3].append(d)
        for k, v in t.facts.items():
            f = tb.facts.setdefault(k, [0, 0.0, 0.0])
            f[0] += 1; f[1] += v; f[2] = max(f[2], v)
        if _say_at(tb.n):
            say = _text(t.who, t.kind, cls, tb, "at this count")
    if size >= BIG:
        t0 = order[0][1]
        print("[WIRE-METRICS-ONE] %s %s out=%dB in=%dB total=%.2fms: %s | %s" % (
            t.who, t.kind, t.bytes_out, t.bytes_in, (order[-1][1] - t0) / 1e6,
            " ".join("%s@%.2f" % (n, (ns - t0) / 1e6) for n, ns in order),
            " ".join("%s=%g" % kv for kv in sorted(t.facts.items()))), flush=True)
    if say:
        print(say, flush=True)


def _say_at(n: int) -> bool:
    """At 1, 2, 5, 10, 20, 50 ... and then at every EVERY: a count, not a clock, so a process that is ended has still
    said where its time went up to its last such count."""
    if n >= EVERY:
        return n % EVERY == 0
    d = 1
    while d * 10 <= n:
        d *= 10
    return n in (d, 2 * d, 5 * d)


def _pct(v: List[int], p: float) -> float:
    s = sorted(v)
    return s[min(len(s) - 1, int(len(s) * p))] / 1e6 if s else 0.0


def _text(who: str, kind: str, cls: int, tb: _Table, why: str) -> str:
    total = max(1, tb.total_ns)
    rows = sorted(tb.spans.items(), key=lambda kv: -kv[1][1])
    out = ["[WIRE-METRICS] %s %s %s frames<=%s n=%d mean total %.2fms (%s). Each interval: share of all time, n, "
           "p50/p95/max ms:" % (BUILD, who, kind, "%d" % cls if cls < 10 ** 12 else "any", tb.n, tb.total_ns / tb.n / 1e6, why)]
    for name, (n, s, mx, vals) in rows:
        out.append("[WIRE-METRICS]   %5.1f%% %-44s n=%-6d %.3f / %.3f / %.3f" % (100.0 * s / total, name, n, _pct(vals, 0.5), _pct(vals, 0.95), mx / 1e6))
    if tb.facts:
        out.append("[WIRE-METRICS]   facts, mean / max: " + "; ".join("%s %.1f / %g" % (k, f[1] / f[0], f[2]) for k, f in sorted(tb.facts.items())))
    return "\n".join(out)


def flush(why: str) -> None:
    """Every table this process has, said now (the driver calls this when it stops, and a test when it asks)."""
    if not ON:
        return
    with _lock:
        texts = [_text(who, kind, cls, tb, why) for (who, kind, cls), tb in sorted(_tables.items()) if tb.n]
    for t in texts:
        print(t, flush=True)
