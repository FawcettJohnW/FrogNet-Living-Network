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
import argparse, os, time, json, random, string
import http.client
from urllib.parse import urlsplit

def gen_text(n: int) -> str:
    return "".join(random.choice(string.ascii_letters + string.digits + " ,.-") for _ in range(n))

def gen_json(n: int) -> str:
    # pad inside a field
    obj = {"type":"demo","ts":time.time(),"payload":gen_text(n)}
    return json.dumps(obj, separators=(",",":"))

def gen_xml(n: int) -> str:
    return f"<root><type>demo</type><ts>{time.time()}</ts><payload>{gen_text(n)}</payload></root>"

def gen_csv(n: int) -> str:
    # approximate: repeat rows until size ~ n
    rows = ["a,b,c,d,e"]
    while sum(len(r)+1 for r in rows) < n:
        rows.append(",".join([gen_text(8) for _ in range(5)]))
    return "\n".join(rows)

def do_req(url: str, method: str, body: bytes, headers: dict) -> tuple[int, bytes, dict, float]:
    u = urlsplit(url)
    host = u.hostname
    port = u.port or (80 if u.scheme == "http" else 443)
    path = u.path or "/"
    if u.query:
        path = f"{path}?{u.query}"
    conn = http.client.HTTPConnection(host, port, timeout=15)
    t0 = time.time()
    conn.request(method, path, body=body, headers=headers)
    resp = conn.getresponse()
    data = resp.read()
    dt = (time.time() - t0) * 1000.0
    rh = dict(resp.headers)
    conn.close()
    return resp.status, data, rh, dt

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True, help="Target URL (http://host/path?... )")
    ap.add_argument("--run-id", default=f"run-{int(time.time())}", help="Run identifier tag")
    ap.add_argument("--seconds", type=int, default=120, help="Duration per phase")
    ap.add_argument("--qps", type=float, default=2.0, help="Approx requests per second")
    ap.add_argument("--phase", choices=["real","semantic"], required=True, help="Label only; you enforce semantic via semantic_hosts")
    args = ap.parse_args()

    interval = 1.0 / max(0.1, args.qps)
    sizes = [256, 2048, 16384, 65536]  # adjust as needed
    types = ["json","xml","csv","text"]

    print(f"[demo] phase={args.phase} run_id={args.run_id} seconds={args.seconds} qps={args.qps} url={args.url}", flush=True)

    t_end = time.time() + args.seconds
    i = 0
    while time.time() < t_end:
        typ = random.choice(types)
        sz = random.choice(sizes)

        if typ == "json":
            payload = gen_json(sz).encode("utf-8","replace")
            ctype = "application/json"
        elif typ == "xml":
            payload = gen_xml(sz).encode("utf-8","replace")
            ctype = "application/xml"
        elif typ == "csv":
            payload = gen_csv(sz).encode("utf-8","replace")
            ctype = "text/csv"
        else:
            payload = gen_text(sz).encode("utf-8","replace")
            ctype = "text/plain"

        headers = {
            "Host": urlsplit(args.url).hostname or "",
            "Content-Type": ctype,
            "Content-Length": str(len(payload)),
            "X-FrogNet-RunID": args.run_id,
            "X-FrogNet-Phase": args.phase,
            "Accept-Encoding": "identity",
        }

        try:
            st, data, rh, dt = do_req(args.url, "POST", payload, headers)
            print(f"[demo] {i} {typ} {sz}B status={st} t={dt:.1f}ms resp_len={len(data)}", flush=True)
        except Exception as e:
            print(f"[demo] {i} ERROR {typ} {sz}B {repr(e)}", flush=True)

        i += 1
        time.sleep(interval)

if __name__ == "__main__":
    main()
