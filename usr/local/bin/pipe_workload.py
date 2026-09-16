#!/usr/bin/env python3
"""
FrogNet Pipeline Workload - demonstrates semantic compression across three phases.

Usage:
    python3 pipe_workload.py <target_ip> <payload> [requests]

Payload types:
    echo    - frognet_echo.php             (~50B, RESP_SAME after bootstrap)
    kb      - semantic_test.php            (~500KB HTML with 6 dynamic fields)
    mb      - semantic_test_json.php       (~500KB JSON with 6 dynamic fields)
    json-kb - semantic_test_json_500k.php  (~500KB JSON with 5 dynamic fields)
    chat    - semantic_test_chat.php       (50-msg rolling chat window, ~20% new-msg rate)
    telem   - semantic_test_telemetry.php  (200-sensor telemetry array, 5-10 updates/call)

Examples:
    python3 pipe_workload.py 10.101.20.1 echo 100
    python3 pipe_workload.py 10.101.40.1 mb 50
    python3 pipe_workload.py 10.101.20.1 telem 100
    python3 pipe_workload.py 10.101.20.1 chat 100
"""

import sys
import time
import threading
import urllib.request
import urllib.error
import statistics
# [A_SWALLOWED_NAMEERROR_LOOKS_LIKE_A_DEAD_SENSOR_V1] json was never imported,
# so _read_endpoint_wire raised NameError on EVERY call and the bare `except
# Exception` turned that into "metrics unavailable". The wire accounting had
# never run on any invocation of this script, and the message pointed at the
# engine rather than at these six lines. Found 2026-08-15.
import json

# [ASCII_SAFE_OUTPUT_V1] Force UTF-8 stdout/stderr. A fresh box boots in the C/POSIX
# locale, where Python sets stdout encoding to ASCII and any non-ASCII byte printed
# (a response body, a header, a symbol) raises UnicodeEncodeError and kills the run.
# errors="replace" means it degrades a stray char instead of crashing.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# -- Args ----------------------------------------------------------------------
if len(sys.argv) < 3:
    print(__doc__)
    sys.exit(1)

TARGET_IP   = sys.argv[1]
PAYLOAD     = sys.argv[2].lower()
N           = int(sys.argv[3]) if len(sys.argv) > 3 else 100
TIMEOUT     = 60

ENDPOINTS = {
    "echo":    "/frognet_echo.php",
    "kb":      "/semantic_test.php",
    "mb":      "/semantic_test_json.php",
    "json-kb": "/semantic_test_json_500k.php",
    "chat":    "/semantic_test_chat.php",
    "telem":   "/semantic_test_telemetry.php",
}

DESCRIPTIONS = {
    "echo":    "~50B static text response",
    "kb":      "~500KB HTML with 6 dynamic fields",
    "mb":      "~500KB JSON with 6 dynamic fields",
    "json-kb": "~500KB JSON with 5 dynamic fields",
    "chat":    "Realistic HTML: 50-msg chat window, ~20% new-msg rate",
    "telem":   "Realistic JSON: 200-sensor telemetry, 5-10 updates/call",
}

if PAYLOAD not in ENDPOINTS:
    print(f"Unknown payload '{PAYLOAD}'. Choose: echo, kb, mb, json-kb, chat, telem")
    sys.exit(1)

PATH        = ENDPOINTS[PAYLOAD]
DESC        = DESCRIPTIONS[PAYLOAD]
URL_DIRECT  = f"http://{TARGET_IP}:8080{PATH}"   # bypass FrogNet, direct to Apache
URL_FROGNET = f"http://{TARGET_IP}{PATH}"         # full FrogNet semantic pipeline

# -- Fetch ---------------------------------------------------------------------
def fetch(url):
    t0 = time.time()
    try:
        req = urllib.request.Request(url, headers={"Accept": "*/*"})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            body = r.read()
            cache = r.getheader("X-FrogNet-Cache", "-")
        ms = (time.time() - t0) * 1000
        return ms, True, len(body), cache
    except Exception as e:
        ms = (time.time() - t0) * 1000
        return ms, False, 0, str(e)


# -- Wire accounting ---------------------------------------------------------
# The client CANNOT see wire bytes: the proxy reconstructs the full body locally,
# so every fetch() above returns the whole payload regardless of what crossed the
# wire. The real would/actual lives in proxy_metrics.py, published as the sensor
# {domain}.SemanticCache.Endpoints (per-endpoint bytes_would / bytes_actual),
# flushed ~every 30s - the same source frognet_monitor reads. So we snapshot that
# sensor before and after the run and diff the endpoint we hit. Everything here is
# best-effort: any failure returns None and the summary falls back to the old form.
def _read_endpoint_wire(path):
    """(bytes_would, bytes_actual) for `path`, from the local proxy, right now.

    [COUNTERS_ARE_READABLE_WHEN_ASKED_V1] This used to ask the published sensor,
    which the metrics thread writes about every thirty seconds. A run of a few
    seconds sits inside one interval and has nothing to diff, so every phase
    reported "no flush landed" and the poll waiting for one read as a hang.

    The proxy now answers /_frognet/wire on loopback with the same counters the
    flusher would publish, at the instant they are asked for. No flush interval,
    no polling, and the numbers are THIS box's -- which is what was wanted: the
    sensor aggregates a mesh, and the question was about this node.

    It also removes the domain lookup entirely. That read /etc/frognet_domain to
    build a sensor name, fell back to the literal string "frognet" when the file
    was absent -- which it is on a correctly built node, since nothing writes it
    -- and asked for a sensor nobody publishes. Loopback needs no name.
    """
    try:
        url = "http://127.0.0.1/_frognet/wire?path=" + urllib.parse.quote(path)
        with urllib.request.urlopen(url, timeout=5) as r:
            data = json.loads(r.read().decode())
        ep = (data.get("endpoints") or {}).get(path)
        if ep is None:
            # No traffic on this endpoint yet. Zero is the honest starting
            # point, and distinct from a read that failed.
            return (0, 0)
        return int(ep.get("bytes_would", 0)), int(ep.get("bytes_actual", 0))
    except Exception as e:
        print(f"    [wire] cannot read local proxy counters: "
              f"{type(e).__name__}: {e}")
        print(f"    [wire] the proxy answers /_frognet/wire on loopback; "
              f"an older proxy will not have it")
        return None


# -- Wire accounting for the run ------------------------------------------------
#
# [ONE_BRACKET_PER_FLUSH_INTERVAL_V1] One measurement, around the whole run.
#
# This was briefly per-phase, which cannot work: the engine flushes its counters
# about every thirty seconds, so bracketing four phases that each take a few
# seconds splits one flush interval four ways and produces four "no flush
# landed" reports instead of one number. Worse, each bracket waited up to forty
# seconds for a flush that was never going to arrive inside it, which read as
# the run hanging after the :8080 phase. Measured 2026-08-15.
#
# The granularity the counters can resolve is the run. That is what is reported.
class WireRun:
    """Bracket anything -- a phase or a whole run -- and report the difference.

    Per-phase is viable again now that a read is instant. It was not when the
    numbers came from a thirty-second flush: four short phases split one
    interval four ways and reported nothing four times.
    """

    def __init__(self, path):
        self.path = path
        self.before = _read_endpoint_wire(path)
        self.would = self.actual = 0
        self.measured = False

    def close(self):
        # No wait: the counters are read on demand now, so closing the bracket
        # is a second read and a subtraction.
        if self.before is None:
            return self
        after = _read_endpoint_wire(self.path)
        if after is None:
            return self
        self.would = after[0] - self.before[0]
        self.actual = after[1] - self.before[1]
        self.measured = self.would > 0
        return self

    def report(self, indent="    "):
        if self.before is None:
            print(f"{indent}Wire: engine counters were not readable at the start "
                  f"of the run")
            return
        if not self.measured:
            print(f"{indent}Wire: the counters did not move during this run "
                  f"(no traffic reached the engine on {self.path})")
            return
        saved = self.would - self.actual
        pct = saved / self.would * 100
        ratio = self.would / self.actual if self.actual > 0 else float("inf")
        rs = f"{ratio:.1f}x" if self.actual > 0 else "inf"
        print(f"{indent}Bytes sent:          {self.actual:>13,}   "
              f"({self.actual/1024:>10.1f} KB)")
        print(f"{indent}Bytes without engine:{self.would:>13,}   "
              f"({self.would/1024:>10.1f} KB)")
        print(f"{indent}Saved:               {saved:>13,}   "
              f"({pct:>5.1f}%, {rs})")


# -- Run N serial requests ------------------------------------------------------
def run_serial(url, n, label, quiet=False):
    times, body_sizes, cache_types = [], [], []
    ok = fail = 0
    n = int(n)
    for i in range(25):
        ms, success, sz, cache = fetch(url)
        if success:
            ok += 1
            times.append(ms)
            body_sizes.append(sz)
            cache_types.append(cache)
        else:
            fail += 1
        if not quiet and (i+1) % max(1, n//2) == 0:
            avg = statistics.mean(times) if times else 0
            print(f"  [{i+1:4d}/{n}] avg={avg:.0f}ms  ok={ok}  fail={fail}")
    return times, ok, fail, body_sizes, cache_types

def print_serial_results(label, times, ok, fail, body_sizes, cache_types):
    total_s = sum(times) / 1000 if times else 0.001
    print(f"\n  {label} RESULTS:")
    print(f"    Requests: {ok} OK  {fail} FAIL")
    if times:
        print(f"    Total:    {total_s:.1f}s")
        print(f"    Avg:      {statistics.mean(times):.0f}ms")
        print(f"    P50:      {statistics.median(times):.0f}ms")
        print(f"    P95:      {sorted(times)[int(len(times)*0.95)]:.0f}ms")
        print(f"    Min:      {min(times):.0f}ms")
        print(f"    Throughput: {ok/total_s:.1f} req/s")
    if body_sizes:
        print(f"    Avg body: {statistics.mean(body_sizes)/1024:.1f} KB")
    if cache_types:
        from collections import Counter
        counts = Counter(cache_types)
        print(f"    Cache:    {dict(counts)}")

# -- Run N concurrent requests --------------------------------------------------
def run_concurrent(url, n, label):
    results = [None] * n

    def worker(idx):
        results[idx] = fetch(url)

    t_start = time.time()
    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    for t in threads: t.start()
    for t in threads: t.join()
    elapsed = time.time() - t_start

    times    = [r[0] for r in results if r[1]]
    fails    = [r for r in results if not r[1]]
    caches   = [r[3] for r in results if r[1]]

    print(f"\n  {label} CONCURRENT ({n} simultaneous):")
    print(f"    Elapsed:    {elapsed:.1f}s")
    print(f"    OK:         {len(times)}  FAIL: {len(fails)}")
    if times:
        print(f"    Avg:        {statistics.mean(times):.0f}ms")
        print(f"    P50:        {statistics.median(times):.0f}ms")
        print(f"    P95:        {sorted(times)[int(len(times)*0.95)]:.0f}ms")
        print(f"    Throughput: {len(times)/elapsed:.1f} req/s")
    if caches:
        from collections import Counter
        print(f"    Cache:      {dict(Counter(caches))}")

# -- Ramp phase -----------------------------------------------------------------
def run_ramp(url, label):
    print(f"\n  RAMP ({label}) - 10 requests each at increasing concurrency")
    print(f"  What this shows: how throughput scales as more requests fly in parallel.")
    print(f"  With semantic compression, concurrent requests coalesce - the daemon")
    print(f"  executes once and fans out the result to all waiting callers.")
    print(f"  Higher concurrency = better wire efficiency. Watch req/s climb.")
    print()
    for conc in [1, 5, 10, 25, 50]:
        results = [None] * conc

        def worker(idx):
            results[idx] = fetch(url)

        t0 = time.time()
        threads = [threading.Thread(target=worker, args=(i,)) for i in range(conc)]
        for t in threads: t.start()
        for t in threads: t.join()
        elapsed = time.time() - t0
        times = [r[0] for r in results if r[1]]
        ok = len(times)
        p50 = f"{statistics.median(times):.0f}ms" if times else "-"
        rps = f"{ok/elapsed:.1f}" if elapsed > 0 else "-"
        print(f"    conc={conc:3d}  ok={ok:3d}  p50={p50:>8}  elapsed={elapsed:.1f}s  rps={rps}")

# -- Main -----------------------------------------------------------------------
def main():
    print(f"\nFrogNet Pipeline Workload")
    print(f"Target:   {TARGET_IP}")
    print(f"Payload:  {PAYLOAD.upper()} - {DESC}")
    print(f"Requests: {N} per phase")

    # -- PHASE 1: Direct to Apache, no FrogNet ----------------------------------
    print(f"\n{'='*60}")
    print(f"  PHASE 1: Direct to Apache ({TARGET_IP}:8080) - NO FrogNet")
    print(f"  Baseline: raw Apache performance, no proxy, no compression.")
    print(f"  This is what every request costs without FrogNet on the wire.")
    print(f"{'='*60}")
    times1, ok1, fail1, sizes1, cache1 = run_serial(URL_DIRECT, N // 4, "DIRECT")
    print_serial_results("DIRECT", times1, ok1, fail1, sizes1, cache1)

    time.sleep(2)

    # -- Bootstrap: one request through FrogNet to learn the template -----------
    #
    # [THE_BOOTSTRAP_IS_NOT_STEADY_STATE_V1] Measured, reported, and kept out of
    # the steady-state total.
    #
    # The bootstrap is one full uncompressed body -- it is the request that
    # TEACHES the template, so by definition nothing is cached yet. Counting it
    # in the run total means the headline figure includes the one request that
    # could not possibly be compressed, and the total then disagrees with the
    # sum of its phases by exactly that request. Measured 2026-08-15 on a 1 MB
    # payload: phases summed to 141,909 bytes and the total said 1,245,480 --
    # the 1.1 MB difference being the bootstrap, sitting inside the run bracket
    # and outside every phase bracket.
    #
    # It is real cost and it is not hidden: it is bracketed on its own and
    # printed. What it is not is part of a steady-state average, because a
    # template is learned once and used for the life of the call.
    print(f"\n  Bootstrapping FrogNet template (one request through :80)...")
    boot_wire = WireRun(PATH)
    ms_boot, ok_boot, sz_boot, cache_boot = fetch(URL_FROGNET)
    boot_wire.close()
    wire = WireRun(PATH)
    print(f"  Bootstrap: {ms_boot:.0f}ms  body={sz_boot/1024:.0f}KB  cache={cache_boot}")
    if boot_wire.measured:
        print(f"    (bootstrap on the wire: {boot_wire.actual:,} bytes -- one full "
              f"body, since this is the request that teaches the template.\n"
              f"     Reported here and excluded from the totals below, which are "
              f"steady state.)")
    time.sleep(1)

    # -- PHASE 2: FrogNet serial - template hot, SAME/DIFF firing --------------
    print(f"\n{'='*60}")
    print(f"  PHASE 2: FrogNet HOT - serial requests, SAME/DIFF active")
    print(f"  Templates are cached on both ends. The daemon sends only the")
    print(f"  changed fields over the wire; the proxy reconstructs the full")
    print(f"  response locally. Serial performance is similar to Phase 1 -")
    print(f"  one request in flight at a time, so wire savings don't increase")
    print(f"  throughput here. The gain shows up in Phase 3 (concurrent).")
    print(f"{'='*60}")
    w2 = WireRun(PATH)
    times2, ok2, fail2, sizes2, cache2 = run_serial(URL_FROGNET, N // 4, "FROGNET HOT")
    print_serial_results("FROGNET HOT", times2, ok2, fail2, sizes2, cache2)
    w2.close().report()

    time.sleep(2)

    # -- RAMP phase -------------------------------------------------------------
    print(f"\n{'='*60}")
    print(f"  PHASE 3: RAMP - concurrent scalability (FrogNet HOT)")
    print(f"{'='*60}")
    w3 = WireRun(PATH)
    run_ramp(URL_FROGNET, "FrogNet HOT")
    print(f"\n  RAMP wire:")
    w3.close().report()

    # -- CONCURRENT burst -------------------------------------------------------
    print(f"\n{'='*60}")
    print(f"  PHASE 4: BURST - {N} simultaneous requests (FrogNet HOT)")
    print(f"  This is where FrogNet shines. Concurrent requests coalesce on")
    print(f"  the daemon - it executes once and serves all callers. The wire")
    print(f"  carries tiny diff frames regardless of how many clients are waiting.")
    print(f"{'='*60}")
    w4 = WireRun(PATH)
    run_concurrent(URL_FROGNET, N, "FrogNet HOT")
    w4.close().report()

    # -- Wire accounting -------------------------------------------------------
    # The phases above each closed their own bracket, so the run total is their
    # sum rather than a fresh snapshot. Two independent measurements of the same
    # traffic would disagree at the edges -- a flush landing between the last
    # phase and the summary belongs to neither -- and a total that does not
    # equal its parts is the kind of discrepancy that costs an afternoon.
    wire.close()

    # -- Summary ----------------------------------------------------------------
    print(f"\n{'='*60}")
    print(f"  SUMMARY")
    print(f"{'='*60}")
    if times1 and times2:
        avg1 = statistics.mean(times1)
        avg2 = statistics.mean(times2)
        improvement = (avg1 - avg2) / avg1 * 100
        rps1 = ok1 / (sum(times1) / 1000)
        rps2 = ok2 / (sum(times2) / 1000)
        avg_body = statistics.mean(sizes1) if sizes1 else 0
        print(f"    Payload size:        {avg_body/1024:.0f} KB")
        print(f"    Direct Apache avg:   {avg1:.0f}ms  ({rps1:.1f} req/s)")
        print(f"    FrogNet HOT avg:     {avg2:.0f}ms  ({rps2:.1f} req/s)")
        if improvement > 0:
            print(f"    Latency improvement: {improvement:.0f}%")
        else:
            print(f"    Overhead:            {-improvement:.0f}% (expected - WG hop adds RTT)")
        if cache2:
            from collections import Counter
            c = Counter(cache2)
            same_diff = c.get("SAME", 0) + c.get("DIFF", 0)
            total = sum(c.values())
            if total:
                print(f"    Cache hit rate:      {same_diff/total*100:.0f}% SAME/DIFF")

    # -- Wire savings, measured from the engine's own counters (not the client) --
    # One reporter, one bracket. WireRun.report() already distinguishes the
    # three outcomes -- measured, no flush landed, counters unreadable -- so
    # branching here to call it three ways only made it possible for them to
    # drift apart.
    if wire.measured:
        print(f"    -- on the wire, everything through :80 "
              f"(from SemanticCache.Endpoints) --")
    wire.report()
    print(f"{'='*60}\n")

if __name__ == "__main__":
    main()
