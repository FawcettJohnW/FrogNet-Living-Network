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
def _frognet_domain():
    try:
        with open("/etc/frognet_domain") as f:
            d = f.read().strip()
            return d or "frognet"
    except Exception:
        return "frognet"

_METRICS_DBHOST = "databasehost.frognet"   # metrics are observed data, not SD: coordination
_ENDPOINT_SENSOR = f"{_frognet_domain()}.SemanticCache.Endpoints"

def _read_endpoint_wire(path):
    """Return (bytes_would, bytes_actual) for endpoint `path` from the published
    SemanticCache.Endpoints sensor, or None on any failure."""
    try:
        url = (f"http://{_METRICS_DBHOST}/api.php?entity=sensors&action=values"
               f"&parse=1&SensorName={urllib.parse.quote(_ENDPOINT_SENSOR)}")
        req = urllib.request.Request(url, headers={"X-FrogNet-Origin-Local": "1"})
        with urllib.request.urlopen(req, timeout=5) as r:
            rows = json.loads(r.read().decode()).get("rows", [])
        for row in rows:
            data = row.get("data")
            if isinstance(data, str):
                data = json.loads(data)
            if not isinstance(data, dict):
                continue
            for ep in data.get("top_endpoints", []):
                if ep.get("path", "").split("?")[0] == path:
                    return int(ep.get("bytes_would", 0)), int(ep.get("bytes_actual", 0))
        return (0, 0)        # sensor present, endpoint not yet listed
    except Exception:
        return None

def _wire_snapshot_after(path, before, flush_wait=40, poll=5):
    """Poll the endpoint sensor until a post-run flush lands (would advances past
    `before`), up to flush_wait seconds. Returns (would, actual) or None."""
    if before is None:
        return None
    deadline = time.time() + flush_wait
    last = None
    while time.time() < deadline:
        snap = _read_endpoint_wire(path)
        if snap is not None:
            last = snap
            if snap[0] > before[0]:      # new bytes_would since 'before' => flush landed
                return snap
        time.sleep(poll)
    return last

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
    wire_before = _read_endpoint_wire(PATH)        # wire accounting baseline
    print(f"\n  Bootstrapping FrogNet template (one request through :80)...")
    ms_boot, ok_boot, sz_boot, cache_boot = fetch(URL_FROGNET)
    print(f"  Bootstrap: {ms_boot:.0f}ms  body={sz_boot/1024:.0f}KB  cache={cache_boot}")
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
    times2, ok2, fail2, sizes2, cache2 = run_serial(URL_FROGNET, N // 4, "FROGNET HOT")
    print_serial_results("FROGNET HOT", times2, ok2, fail2, sizes2, cache2)

    time.sleep(2)

    # -- RAMP phase -------------------------------------------------------------
    print(f"\n{'='*60}")
    print(f"  PHASE 3: RAMP - concurrent scalability (FrogNet HOT)")
    print(f"{'='*60}")
    run_ramp(URL_FROGNET, "FrogNet HOT")

    # -- CONCURRENT burst -------------------------------------------------------
    print(f"\n{'='*60}")
    print(f"  PHASE 4: BURST - {N} simultaneous requests (FrogNet HOT)")
    print(f"  This is where FrogNet shines. Concurrent requests coalesce on")
    print(f"  the daemon - it executes once and serves all callers. The wire")
    print(f"  carries tiny diff frames regardless of how many clients are waiting.")
    print(f"{'='*60}")
    run_concurrent(URL_FROGNET, N, "FrogNet HOT")

    # -- Wire accounting: wait for a post-run flush, then diff the endpoint ------
    wire_after = _wire_snapshot_after(PATH, wire_before)

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
    if wire_before is not None and wire_after is not None:
        would = wire_after[0] - wire_before[0]
        actual = wire_after[1] - wire_before[1]
        saved = would - actual
        if would > 0:
            pct = saved / would * 100
            ratio = would / actual if actual > 0 else float("inf")
            ratio_s = f"{ratio:.1f}x" if actual > 0 else "?"
            print(f"    -- on the wire (this run, from SemanticCache.Endpoints) --")
            print(f"    Would have crossed:  {would/1024:.1f} KB")
            print(f"    Actually crossed:    {actual/1024:.1f} KB")
            print(f"    Saved:               {saved/1024:.1f} KB  ({pct:.1f}%, {ratio_s})")
        else:
            print(f"    Wire savings: no flushed delta yet (run shorter than the ~30s metrics flush)")
    elif wire_before is not None:
        print(f"    Wire savings: metrics snapshot unavailable after the run (skipped)")
    print(f"{'='*60}\n")

if __name__ == "__main__":
    main()
