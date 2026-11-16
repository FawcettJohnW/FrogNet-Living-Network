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
lisp_slow_tier.py - [LISP_SLOW_TIER_V1] Ribbit-LISP against lispers.net in the simulator, over slow links.

Both systems run here, unmodified: lispers.net 0.643 (its own RUN-LISP) and Ribbit-LISP (lisper-ram, lisp-boundary),
configured from the acceptance test's one fixture (tools/acceptance.py SITES). What is simulated is the network
between the parts (slowlink.py): the xTRs' path to each map-server, and the Ribbit front's session to its RAM host.

  deployments
    central     every xTR reaches the map-server over the WAN (wan-100ms: the 100 ms round trip of NY2 <-> Seattle7);
                Ribbit's front sits next to its RAM host. The two systems are the same distance from the xTRs.
    edge        lispers.net is central (xTRs over wan-100ms); Ribbit's front is on the xTRs' LAN and its RAM host is
                across the WAN (front <-> RAM host over wan-100ms). Ribbit's shape: the state is distributed, the
                answers are local.
    slow-edge   as edge, but the xTRs' own bearer is 4G (40 ms each way, 10 Mb/s) to either map-server's front door;
                Ribbit's front <-> RAM host over wan-100ms.
  workloads     closed-loop clients (acceptance.py's own Worker) at 1, 16 and 64 clients: authenticated
                Map-Registers (each answered by a Map-Notify) and ECM Map-Requests, against 1000 registrations
                preloaded without acks; and a causality probe: a register through one client, then at once a request
                through another -- resolved or not, and how long.
  UDP shaping   a map-server answers a Map-Request to the ITR address inside the ECM, not through the relay, so the
                round trip of each UDP bearer is applied on the way in (2 x one-way); the bandwidth on both ways.

usage (from opt/frognet_semantic):
  python3 simulation/lisp_slow_tier.py --pkg DIR --lispers DIR [--deploy central edge slow-edge] [--clients 1 16 64]
  DIR (--pkg): a Ribbit-LISP package (ribbit/examples/lispers.net, built: make front ram)
"""
import argparse
import json
import os
import random
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import replace

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
import slowlink as SL                         # noqa: E402

DEPLOY = {
    #             xTR -> lispers.net   xTR -> Ribbit front   Ribbit front -> RAM host
    "central":   ("wan-100ms",         "wan-100ms",          "lan"),
    "edge":      ("wan-100ms",         "lan",                "wan-100ms"),
    "slow-edge": ("4g",                "4g",                 "wan-100ms"),
}


def udp_link(name):
    """A UDP bearer: its round trip on the way in (the answer comes back directly), its rate both ways."""
    L = SL.profile(name)
    return replace(L, latency_ms=2 * L.latency_ms, jitter_ms=2 * L.jitter_ms), replace(L, latency_ms=0.0, jitter_ms=0.0)


class Args:
    pass


def resolved(rep):
    return bool(rep) and any(r.get("locs") for r in rep.get("records") or [])


# [LISP_TIER_SAYS_WHAT_IS_MISSING_V1] The tier needs things this tree cannot carry. It checks each one first and names
# every one that is missing, instead of failing deep inside lispers.net's startup.
LISP_MODULES = ("bottle", "cheroot", "netifaces", "OpenSSL", "Crypto", "curve25519", "requests", "geopy", "ecdsa",
                "future", "distro")


def lisp_python_bin():
    """The bin directory of the Python 3.12 that runs lispers.net (its .pyc files are 3.12's): LISP_PYTHON_BIN, or the
    venv setup.sh --lisp makes."""
    return os.environ.get("LISP_PYTHON_BIN", os.path.expanduser("~/.frogsim/lisp-py312/bin"))


def prerequisites(pkg, lispers, pybin):
    miss = []
    for b in ("ribbit_cpp/lisper-ram", "ribbit_cpp/ribbit-lisp", "tools/lisp-boundary", "tools/tuple-write"):
        if not os.access(os.path.join(pkg, b), os.X_OK):
            miss.append(f"the Ribbit-LISP package, built: {os.path.join(pkg, b)} (make front ram in {pkg})")
            break
    if not os.path.isfile(os.path.join(lispers, "lisp-core.pyc")):
        miss.append(f"lispers.net 0.643, unpacked: {lispers}/lisp-core.pyc (LISPERS_DIR names another place)")
    if not shutil.which("tcsh"):
        miss.append("tcsh (lispers.net's scripts are tcsh)")
    py = os.path.join(pybin, "python")
    if not os.access(py, os.X_OK):
        miss.append(f"a Python 3.12 for lispers.net: {py} (LISP_PYTHON_BIN names another bin directory)")
    else:
        chk = subprocess.run([py, "-c", "import sys,importlib.util as u; m=[x for x in sys.argv[1:] if not u.find_spec(x)];"
                              "print('%d.%d' % sys.version_info[:2], ' '.join(m))", *LISP_MODULES],
                             capture_output=True, text=True)
        ver, _, mods = chk.stdout.strip().partition(" ")
        if ver != "3.12":
            miss.append(f"{py} is Python {ver or '?'}; lispers.net 0.643's .pyc files need 3.12")
        if mods:
            miss.append(f"{py} lacks lispers.net's modules: {mods}")
        v6 = subprocess.run([py, "-c", "import socket; socket.socket(socket.AF_INET6, socket.SOCK_DGRAM).close()"],
                            capture_output=True, text=True)
        if v6.returncode:
            miss.append("IPv6 (lispers.net listens on ::); this machine's kernel has it disabled")
    return miss


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pkg", required=True)
    ap.add_argument("--lispers", required=True)
    ap.add_argument("--deploy", nargs="+", default=list(DEPLOY))
    ap.add_argument("--clients", nargs="+", type=int, default=[1, 16, 64, 256, 1024, 2048])
    ap.add_argument("--duration", type=float, default=10.0)
    ap.add_argument("--warmup", type=float, default=3.0)
    ap.add_argument("--out", default=".")
    ap.add_argument("--ram-port", type=int, default=17991)
    a0 = ap.parse_args()
    pkg = os.path.abspath(a0.pkg)
    pybin = lisp_python_bin()
    missing = prerequisites(pkg, a0.lispers, pybin)
    if missing:
        print("LISP SLOW TIER - FAIL before any run. Missing:")
        for m in missing:
            print("  - " + m)
        print("  bash opt/frognet_semantic/simulation/setup.sh --lisp sets up what it can; lispers.net itself is "
              "Dino Farinacci's and is not in this tree.")
        sys.exit(1)
    sys.path.insert(0, os.path.join(pkg, "tools"))
    os.chdir(pkg)                              # STOP-LISP kills "lisp-": the harness runs from the package, by a relative path
    import acceptance as A
    A.LOCAL_IP = "127.0.0.1"                  # the fixture's LOCAL_IP (the harness sets it from --local-ip)
    os.makedirs(a0.out, exist_ok=True)
    os.environ["PATH"] = pybin + os.pathsep + os.environ.get("PATH", "")   # lispers.net runs the `python` it finds
    rows, probes, ok = [], [], True
    print(f"LISP SLOW TIER - lispers.net {open(os.path.join(a0.lispers, 'lisp-version.txt')).read().strip()} and Ribbit-LISP "
          f"{A.Ribbit.__init__.__code__ and open(os.path.join(pkg, 'ribbit_cpp', 'version.hpp')).read().split('RIBBIT_LISP_VERSION')[1].split()[0]}"
          f" over simulated links")
    for dep in a0.deploy:
        x_l, x_r, f_r = DEPLOY[dep]
        print(f"== {dep}: xTR -> lispers.net {SL.profile(x_l).label()}; xTR -> Ribbit front {SL.profile(x_r).label()}; "
              f"Ribbit front -> RAM host {SL.profile(f_r).label()}", flush=True)
        ram = subprocess.Popen([os.path.join(pkg, "ribbit_cpp", "lisper-ram"), "--listen", f"127.0.0.1:{a0.ram_port}"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(0.5)
        fr = SL.Relay(("127.0.0.1", a0.ram_port), SL.profile(f_r), name="front-ram") if f_r != "lan" else None
        a = Args()
        a.lispers_dir, a.lispers_api_port, a.lispers_env, a.local_ip = a0.lispers, 0, [], "127.0.0.1"
        a.ribbit_udp, a.ribbit_front, a.ram_port, a.ram_server = 14342, None, a0.ram_port, None
        a.ram_external = fr.where if fr else f"127.0.0.1:{a0.ram_port}"
        a.out = tempfile.mkdtemp(prefix=f"lisp-slow-{dep}-", dir=a0.out)
        L, R = A.Lispers(a), A.Ribbit(a)
        relays = {}
        try:
            L.start()                          # STOP-LISP first, then Ribbit (STOP-LISP would kill lisp-boundary)
            R.start()
            for sysm, prof in ((L, x_l), (R, x_r)):
                up, down = udp_link(prof)
                relays[sysm.name] = SL.UdpRelay(sysm.target, up, down, name=sysm.name)
            for sysm in (L, R):
                tgt = ("127.0.0.1", relays[sysm.name].port)
                base = A.host_eids(1000)
                # preload without acks, as the perf harness does: 25 registers of 40 records, then sample-resolve
                c = A.Client("127.0.0.1", tgt)
                for i in range(0, len(base), 40):
                    c.s.sendto(A.register([(e, m, 1440, ["127.0.0.1"]) for e, m in base[i:i + 40]], c.next(), want_notify=False), tgt)
                    time.sleep(0.02)
                t0 = time.time()
                sample = [base[i] for i in range(0, len(base), 100)]
                left = list(sample)
                while left and time.time() - t0 < 60:
                    left = [e for e in left if not resolved(c.ask(e[0], timeout=3.0)[0])]
                    if left:
                        time.sleep(0.5)
                print(f"  {sysm.name:11s} preload: 1000 registrations, {len(sample) - len(left)}/{len(sample)} sampled resolve"
                      f" {time.time() - t0:.1f} s after the last register left", flush=True)
                pick = lambda b=base: random.choice(b)[0]
                for kind in ("reg", "req"):
                    for n in a0.clients:
                        now = time.time()
                        stop_at, measure_from = now + a0.warmup + a0.duration, now + a0.warmup
                        # the perf harness's own load processes (acceptance.py _load_child): threads up to 32 clients a
                        # process, one socket and one loop per process past that ([MANY_IN_FLIGHT_V1])
                        import array, multiprocessing
                        ctx = multiprocessing.get_context("fork")
                        nproc = min(n, max(1, min(4, os.cpu_count() or 1))) if n > 32 else 1
                        kids, base_n = [], random.getrandbits(16) << 48
                        for k in range(nproc):
                            rd, wr = ctx.Pipe(duplex=False)
                            pr = ctx.Process(target=A._load_child, args=(wr, list(range(k, n, nproc)), "127.0.0.1", tgt, kind, pick,
                                                                         stop_at, measure_from, base_n), daemon=True)
                            pr.start(); wr.close(); kids.append((pr, rd))
                        lat_a, unans = array.array("d"), 0
                        for pr, rd in kids:
                            b, e, u, c_, err, _m = rd.recv(); pr.join()
                            lat_a.frombytes(b); unans += u
                            if err:
                                raise RuntimeError("load process: " + err)
                        lat = sorted(lat_a)
                        r = {"deploy": dep, "system": sysm.name, "workload": kind, "clients": n, "ops_s": len(lat) / a0.duration,
                             "p50_ms": (A.pct(lat, 50) or 0) * 1e3, "p99_ms": (A.pct(lat, 99) or 0) * 1e3, "unanswered": unans}
                        rows.append(r)
                        print(f"  {sysm.name:11s} {kind:3s} {n:3d} cl  {r['ops_s']:8.0f} ops/s  p50 {r['p50_ms']:8.2f} ms  p99 {r['p99_ms']:8.2f} ms  unans {unans}", flush=True)
                # causality: register through one client, then at once a request through another
                c1, c2 = A.Client("127.0.0.1", tgt), A.Client("127.0.0.1", tgt)
                got, lat = 0, []
                for k in range(20):
                    eid = f"198.18.250.{k + 1}"
                    t0 = time.perf_counter()
                    d, _ = c1.reg([(eid, 32, 3, ["127.0.0.1"])], timeout=5.0)
                    if d is None:
                        continue
                    rep = c2.ask(eid, timeout=5.0)[0]
                    if resolved(rep):
                        got += 1
                        lat.append((time.perf_counter() - t0) * 1e3)
                pr = {"deploy": dep, "system": sysm.name, "resolved_at_once": got, "of": 20,
                      "register_then_resolve_p50_ms": sorted(lat)[len(lat) // 2] if lat else None}
                probes.append(pr)
                print(f"  {sysm.name:11s} causality: {got}/20 resolved at once through a second client"
                      + (f", register + resolve p50 {pr['register_then_resolve_p50_ms']:.1f} ms" if lat else ""), flush=True)
        except Exception as x:
            ok = False
            print(f"  [FAIL] {dep}: {type(x).__name__}: {x}", flush=True)
        finally:
            try:
                R.stop()
            except Exception:
                pass
            try:
                L.stop()
            except Exception:
                pass
            ram.terminate()
            ram.wait()
            if fr:
                fr.close()
    json.dump({"rows": rows, "causality": probes}, open(os.path.join(a0.out, "lisp-slow.json"), "w"), indent=2)
    print(f"LISP SLOW: {'PASS' if ok else 'FAIL'} ({len(rows)} measurements)")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
