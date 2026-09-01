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
"""sim_pond_node.py — stand up the client-facing surface of a FrogNet node so a
monitor implementation can be exercised end to end without a live pond.

Why this exists
---------------
frognet_monitor_cs and frognet_monitor_cpp have oracles covering their pure
parsers and their dashboard models. Neither had ever completed an HTTP request:
the transport, the envelope unwrapping, the two-step sensor read, the jsonData
string-inside-JSON, and the echo identity path were all unproven. Those are
exactly the parts that fail silently against a real node -- a client that
mis-unwraps the envelope shows an empty dashboard, not an error.

This serves the four endpoints a client actually uses:

    GET /frognet_echo.php                     -> fqdn,eth0,wlan0,wlan1
    GET /getHosts.php                         -> [{"ip":...,"name":...}]
    GET /api.php?entity=sensors&action=list   -> {"ok":true,"rows":[...]}
    GET /api.php?entity=sensor_data&action=get-> {"ok":true,"row":{...}}

Node names and sensor types come from sim_cache_soak.py so this is the same
modelled pond, not a second invention of one.

The interesting part is the DEGRADED nodes. A healthy fixture proves almost
nothing: every implementation renders a happy pond. These are the states that
make a monitor lie --

    Seattle2      echo fails            -> must read DOWN, not "no data"
    BAMacBook     no System.Perf row    -> must read "no perf", not 0%
    BABox         System.Perf 400s old  -> must read stale, not draw a live meter
    New-York-1    92% cpu, 18% iowait   -> the degraded machine from the recording

Run:
    python3 sim_pond_node.py --port 8899
    python3 sim_pond_node.py --port 8899 --identity-fails   # [IDENTITY_FAILS_LOUD_V1]
"""
import argparse
import json
import re
import sys
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs

# Same pond as sim_cache_soak.py.
SELF_DOMAIN = "Seattle5"
SELF = {"eth0": "", "wlan0": "10.250.250.1", "wlan1": "192.168.0.42"}

NODES = [
    # (ip, name, echo_ok)
    ("10.250.250.1", "Seattle5",   True),
    ("10.160.160.1", "Seattle6",   True),
    ("10.102.60.1",  "New-York-1", True),
    ("10.199.199.1", "BAMacBook",  True),
    ("10.155.155.1", "BABox",      True),
    ("10.120.120.1", "Seattle2",   False),   # unreachable: echo must fail
]
DBHOST_IP = "10.199.199.1"

_now = int(time.time())

# System.Perf payloads, shaped exactly as sysperf_reporter.bash emits them.
PERF = {
    "Seattle5": dict(busy=12.4, iow=0.6, load=0.31, mem=41.0, swap=0.0,
                     rx=22000, tx=8100, dr=0, dw=12000, temp=47.2, age=4),
    "Seattle6": dict(busy=63.8, iow=4.2, load=1.94, mem=72.5, swap=0.0,
                     rx=180000, tx=240000, dr=90000, dw=410000, temp=68.1, age=7),
    # The degraded machine encountered during the Monitor recording. The tell is
    # iowait: it looks busy, but it is waiting on disk. No link metric sees that.
    "New-York-1": dict(busy=91.7, iow=18.3, load=6.02, mem=93.1, swap=22.0,
                       rx=900000, tx=1200000, dr=2400000, dw=3100000, temp=79.4, age=5),
    # BABox publishes, but the reading has gone stale.
    "BABox": dict(busy=3.1, iow=0.0, load=0.08, mem=22.0, swap=0.0,
                  rx=900, tx=400, dr=0, dw=0, temp=38.0, age=400),
    # BAMacBook: no System.Perf sensor at all. Deliberately absent below.
}

PEER_CACHE = {
    "10.160.160.1": dict(avg=8.1,  p50=6.4,  p95=21.4, hit=0.48, saved=980000,
                         eff=1900000, act=1400000),
    "10.102.60.1":  dict(avg=71.2, p50=57.0, p95=240.0, hit=0.31, saved=320000,
                         eff=900000,  act=780000),
    "10.199.199.1": dict(avg=64.0, p50=51.0, p95=180.0, hit=0.22, saved=120000,
                         eff=400000,  act=360000),
    "10.155.155.1": dict(avg=66.5, p50=53.0, p95=190.0, hit=0.19, saved=90000,
                         eff=300000,  act=280000),
}


def _perf_payload(name):
    p = PERF.get(name)
    if not p:
        return None
    return {
        "timestamp": _now - int(p["age"]),
        "host": "FrogNetHost",
        "sample_sec": 1.0,
        "cpu_pct": {"user": p["busy"] * 0.6, "system": p["busy"] * 0.3,
                    "iowait": p["iow"], "idle": 100.0 - p["busy"],
                    "total_busy": p["busy"]},
        "loadavg": {"1": p["load"], "5": p["load"] * 0.8, "15": p["load"] * 0.6},
        "mem_kb": {"total": 4000000, "used": int(40000 * p["mem"]),
                   "free": 100000, "available": 200000,
                   "buffers": 10000, "cached": 50000, "pct_used": p["mem"]},
        "swap_kb": {"total": 1000000, "used": int(10000 * p["swap"]),
                    "free": 900000, "pct_used": p["swap"]},
        "disk_io": {"total": {"read_ios_per_s": 3.0, "write_ios_per_s": 9.0,
                              "read_bytes_per_s": p["dr"],
                              "write_bytes_per_s": p["dw"]},
                    "devices": {}},
        "net_io": {"total": {"rx_bytes_per_s": p["rx"], "tx_bytes_per_s": p["tx"],
                             "rx_packets_per_s": 100.0, "tx_packets_per_s": 90.0},
                   "interfaces": {}},
        "temps_c": [{"name": "cpu_thermal", "temp_c": p["temp"]},
                    {"name": "soc", "temp_c": p["temp"] - 3.0}],
    }


def _build_sensors():
    """Return {SensorID: (SensorName, SensorType, jsonData_dict)}."""
    out = {}
    sid = 1684400

    for _ip, name, _ok in NODES:
        payload = _perf_payload(name)
        if payload is not None:          # BAMacBook is absent on purpose
            out[sid] = (f"{name}.System.Perf", "System", payload)
            sid += 1

    out[sid] = (f"{SELF_DOMAIN}.SemanticProxy.Engine", "SemanticProxy.Engine",
                {"requests_total": 41822, "bytes_in": 91_000_000,
                 "bytes_out": 22_400_000, "same": 30112, "diff": 9110,
                 "full": 2600, "uptime_s": 88231})
    sid += 1

    peers = {}
    for ip, c in PEER_CACHE.items():
        peers[ip] = {"sample_count": 240, "success_rate": 0.99,
                     "rtt_ok_p95_ms": c["p95"],
                     "saturation_ratio_p50": 0.12,
                     "saturation_ratio_p95": 0.94 if ip == "10.102.60.1" else 0.21}
    out[sid] = (f"{SELF_DOMAIN}.SemanticProxy.LinkQuality",
                "SemanticProxy.LinkQuality",
                {"timestamp": _now, "window_sec": 30.0,
                 "min_window_sec_floor": 30.0, "peers": peers})
    sid += 1

    for ip, c in PEER_CACHE.items():
        out[sid] = (f"{SELF_DOMAIN}.SemanticCache.Peer.{ip}", "SemanticCache.Peer",
                    {"rtt": {"avg_ms": c["avg"], "p50_ms": c["p50"], "p95_ms": c["p95"]},
                     "cache_hit_rate": c["hit"],
                     "bytes_saved": c["saved"],
                     "effective_throughput": {"effective_bps": c["eff"],
                                              "actual_bps": c["act"]}})
        sid += 1

    return out


SENSORS = _build_sensors()


class Handler(BaseHTTPRequestHandler):
    identity_fails = False
    hits = {}

    def log_message(self, *a):
        pass                                   # the harness prints its own summary

    def _send(self, body, ctype="application/json", code=200):
        raw = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        Handler.hits[u.path] = Handler.hits.get(u.path, 0) + 1

        if u.path == "/frognet_echo.php":
            return self._echo()
        if u.path == "/getHosts.php":
            return self._send(json.dumps(
                [{"ip": ip, "name": nm} for ip, nm, _ in NODES]))
        if u.path == "/api.php":
            return self._api(q)
        self._send('{"ok":false,"error":"not found"}', code=404)

    def _echo(self):
        # Which node is being asked matters: the client connects straight to a
        # .1, so the Host header is absent and the ADDRESS is the question.
        host = self.headers.get("Host", "")
        addr = self.server.server_address[0]
        want = None
        for ip, name, ok in NODES:
            if ip in host or ip == addr:
                want = (name, ok)
                break

        # [IDENTITY_FAILS_LOUD_V1] getFrogNet.bash exits non-zero and prints
        # nothing, so the echo body is EMPTY. Not a fallback name, not a 500 --
        # an empty 200. A client must treat that as failure.
        if Handler.identity_fails or (want and not want[1]):
            return self._send("", ctype="text/plain")

        name = want[0] if want else SELF_DOMAIN
        # eth0 is genuinely empty on this node: no carrier is DATA.
        line = "%s,%s,%s,%s\n" % (name, SELF["eth0"], SELF["wlan0"], SELF["wlan1"])
        self._send(line, ctype="text/plain")

    def _api(self, q):
        entity = (q.get("entity") or [""])[0]
        action = (q.get("action") or [""])[0]

        if entity == "sensors" and action == "list":
            name = (q.get("SensorName") or [None])[0]
            like = (q.get("SensorName__like") or [None])[0]
            rows = []
            for sid, (sname, stype, _jd) in sorted(SENSORS.items()):
                if name is not None:
                    if sname != name:
                        continue
                elif like is not None:
                    # api.php builds a SQL LIKE; % is the wildcard.
                    #
                    # Split on % FIRST, then escape each literal piece. Escaping
                    # the whole string and substituting afterwards does not work:
                    # since Python 3.7 re.escape leaves % alone, so there is no
                    # r"\%" to replace and the pattern keeps a literal % that
                    # matches nothing. That failure looked exactly like a broken
                    # client -- both ports returned "0 sensors" against a server
                    # that had the rows.
                    pat = "^" + ".*".join(re.escape(part)
                                          for part in like.split("%")) + "$"
                    if not re.match(pat, sname):
                        continue
                rows.append({"SensorID": sid, "SensorName": sname,
                             "SensorType": stype,
                             "SensorAddress": SELF["wlan0"],
                             "SensorNetwork": "10.250.250.0/24"})
            limit = (q.get("limit") or [None])[0]
            if limit:
                try:
                    rows = rows[:int(limit)]
                except ValueError:
                    pass
            return self._send(json.dumps({"ok": True, "rows": rows}))

        if entity == "sensor_data" and action == "get":
            try:
                sid = int((q.get("SensorID") or ["0"])[0])
            except ValueError:
                sid = 0
            if sid not in SENSORS:
                return self._send(json.dumps({"ok": True, "row": None}))
            sname, _stype, jd = SENSORS[sid]
            # jsonData is a STRING holding JSON, exactly as the real table stores
            # it. A client that forgets to parse it twice shows raw text.
            return self._send(json.dumps(
                {"ok": True, "row": {"SensorID": sid, "SensorName": sname,
                                     "jsonData": json.dumps(jd)}}))

        self._send(json.dumps({"ok": False, "error": "unknown entity/action"}))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8899)
    ap.add_argument("--bind", default="127.0.0.1")
    ap.add_argument("--identity-fails", action="store_true",
                    help="echo returns an empty body, as a node that cannot "
                         "state its identity does")
    ap.add_argument("--seconds", type=float, default=0,
                    help="exit after N seconds (0 = run until killed)")
    a = ap.parse_args()

    Handler.identity_fails = a.identity_fails
    srv = HTTPServer((a.bind, a.port), Handler)
    print("sim pond node on http://%s:%d  (%d sensors, %d nodes)%s"
          % (a.bind, a.port, len(SENSORS), len(NODES),
             "  IDENTITY FAILS" if a.identity_fails else ""),
          flush=True)
    if a.seconds:
        import threading
        threading.Timer(a.seconds, srv.shutdown).start()
        srv.serve_forever()
        srv.server_close()
    else:
        try:
            srv.serve_forever()
        except KeyboardInterrupt:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
