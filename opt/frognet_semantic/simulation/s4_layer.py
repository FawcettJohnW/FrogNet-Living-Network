#!/usr/bin/env python3
################################################################
#  Copyright (C) 2016-2026 Fawcett Innovations LLC             #
#                                                              #
#  SPDX-License-Identifier: GPL-2.0-only                       #
#                                                              #
#  See COPYRIGHT and LICENSE at the root of this tree.         #
################################################################
"""
s4_layer.py - [S4_LAYERED_V1] TIER 4, one machine: shared RAM ABOVE a working routing substrate (John 2026-10-25:
"IS-IS supplies connectivity; shared RAM supplies the programming and coordination model above it").

  routes   real IS-IS (s4_isis: FRR isisd + zebra per node) builds and repairs every route; the hub's ram0
           (198.51.100.1/32, the RAM manager's address) is a passive circuit, so it is routed like any /24
  region   rina-host on the hub at 198.51.100.1:8840, reached over the IS-IS routes; nothing else is configured
  members  every node: fdf-facts --watch (its rows from its own kernel), fdf-client (announce, re-announce on change,
           ends its session when the far end stops answering: its 5 s keepalive / user timeout), s4_reader.py (the
           application: one held read and nothing else -- fdf-events pulls each snapshot over that same session, no
           second channel). Each restarts as its unit does, reconnect paced by the path (immediate after a stable
           session, backing off to the keepalive while the region stays unreachable), not on a blind timer
  ready    a node's application is ready when its view holds every member alive, each with its facts rows

Phases (each timed from the moment it starts; bytes are tx counters over every node's interfaces):
  isis_cold     IS-IS from start to every walk ok (every node to every served /24)
  isis_steady   STEADY s of IS-IS alone
  ram_cold      the region and its members from start to every application ready
  steady        STEADY s of both
  reroute       (--ring) e1's hub link silenced at both ends (it stays up, carries nothing): IS-IS repair time (every
                walk ok again, now through the ring), whether any session ended (the supervisors' restarts: each is
                an fdf-client or reader that lost its session), and when every application is ready again
  restore       the link back; settle
  partition     every link of e1 silenced (hub link and ring links): e1 and its children are cut off. Walks across
                the cut must end in NO ROUTE; every application on the hub's side must drop the cut-off members, and
                every one on e1's side must lose its view (its session ends: the region is unreachable)
  heal          every link back: IS-IS ok again and every application ready again

usage: s4_layer.py [--profile tuned|default] --S 20 --E 2 [--ring] [--steady 30] [--settle 15] [--out DIR]
                   [--frr-bin DIR] [--ram-bin DIR] [--fdf DIR]
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile
import threading
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
import s4_bed as B          # noqa: E402
import s4_isis              # noqa: E402

RAM_IP, RAM_PORT = "198.51.100.1", 8840
API = "/FawcettInnovations.RINA.ram_interface.php"


class Region:
    # [S4_RECONNECT_PACED_BY_THE_PATH_V1] reconnect backoff: a unit that held a session for at least STABLE_S and lost
    # it is retried at once (RETRY_MIN == 0: the path may already be back); one that dies as fast as it starts -- the
    # region is still unreachable, a partition -- backs off, doubling to RETRY_CAP (the session keepalive), so it is
    # not spun. No blind per-unit timer: gone stays gone until the path returns, and the first attempt after it does
    # succeeds, instead of a fixed 2 s that could fire mid-reconvergence and then wait the full 2 s again.
    RETRY_MIN, RETRY_CAP, STABLE_S = 0.25, 5.0, 3.0

    def __init__(self, bed, work, bins, fdf):
        self.bed, self.work, self.bins, self.fdf = bed, work, bins, fdf
        for b in ("rina-host", "fdf-client", "fdf-events"):
            if not os.access(os.path.join(bins, b), os.X_OK):
                raise RuntimeError(f"{bins}/{b} is missing: the layered run needs it")
        for f in ("bin/fdf-facts",):
            if not os.path.exists(os.path.join(fdf, f)):
                raise RuntimeError(f"{fdf}/{f} is missing: the layered run needs it")
        self.procs, self.stopping, self.restarts = [], False, []     # restarts: (t, node, name, rc)
        self.cv = threading.Condition()
        self.view = {}                                                # node -> its reader's last line
        self.lines = []                                               # (node, line) every reader line, in order

    def env(self, n, **more):
        d = os.path.join(self.work, n)
        e = {k: v for k, v in os.environ.items() if not k.startswith(("RINA_", "FDF_"))}
        # [S4_RAM_IN_THIS_NETNS_V1] the region client (rina_region_client) hops to RINA_RAM_NETNS -- else /proc/1/ns/net,
        # the machine ROOT netns -- to open its session, for the real deployment where the RAM lives in the tunnel netns.
        # In the bed the RAM (198.51.100.1) is reachable ONLY inside this node's namespace; root routes it out the host's
        # wlan to the home gateway and the connect times out. The process already runs in the node ns (ip netns exec), so
        # we point RINA_RAM_NETNS at its own ns: the hop becomes a no-op and the client dials from where the RAM lives.
        # (fdf-client uses the non-hopping daf_client and was unaffected either way; fdf-events/region.py hop, and flapped.)
        e.update(RINA_RAM=f"{RAM_IP}:{RAM_PORT}", RINA_RAM_NETNS="/proc/self/ns/net",
                 FDF_BIN=self.bins, FDF_FACTS=f"{d}/facts.json", FDF_VAR=d,
                 FDF_NAME=n, FDF_LEASES=f"{d}/leases", PYTHONUNBUFFERED="1")
        e.update(more)
        return e

    def spawn(self, n, name, argv, reader=False, **more):
        d = os.path.join(self.work, n)
        os.makedirs(d, exist_ok=True)
        log = open(os.path.join(d, f"{name}.log"), "a")
        env = self.env(n, **more)

        def run():
            p = subprocess.Popen(["ip", "netns", "exec", B.NS + n] + argv, env=env, stdin=subprocess.DEVNULL,
                                 stdout=subprocess.PIPE if reader else log, stderr=log, text=True, bufsize=1)
            self.procs.append(p)
            if reader:
                threading.Thread(target=self._read, args=(n, p, log), daemon=True).start()
            return p

        def supervise():
            delay = 0.0
            p = run(); started = time.time()
            while True:
                rc = p.wait()
                if self.stopping:
                    return
                up = time.time() - started
                with self.cv:
                    self.restarts.append((time.time(), n, name, rc))
                # a loss after a stable session restarts at once (the path may already be back); a unit that dies as
                # fast as it starts is still cut off, so back off (doubling, capped) rather than spin or wait blindly.
                delay = 0.0 if up >= self.STABLE_S else min(self.RETRY_CAP, delay * 2 if delay else self.RETRY_MIN)
                log.write(f"[S4-SUPERVISOR] {time.strftime('%H:%M:%S')} {name} ended rc={rc} after {up:.1f}s; "
                          f"restart in {delay:.2f} s\n"); log.flush()
                if delay:
                    time.sleep(delay)
                if self.stopping:
                    return
                p = run(); started = time.time()
        threading.Thread(target=supervise, daemon=True).start()

    def _read(self, n, p, log):
        for line in p.stdout:
            log.write(line); log.flush()
            try:
                d = json.loads(line)
            except ValueError:
                continue
            with self.cv:
                self.view[n] = d
                self.lines.append((n, d))
                self.cv.notify_all()

    def start(self):
        if "hub" in self.bed.mine:
            self.start_host()
        self.start_members()

    def start_host(self):
        h = os.path.join(self.work, "hub")
        os.makedirs(h, exist_ok=True)
        self.spawn("hub", "rina-host", [f"{self.bins}/rina-host", "--listen", f"{RAM_IP}:{RAM_PORT}", "--api", API],
                   RINA_REGION_FILE=f"{h}/region.perm", RINA_NET_CONFIG=f"{h}/net.json")
        for _ in range(250):
            if B.sh("bash", "-c", f"exec 3<>/dev/tcp/{RAM_IP}/{RAM_PORT}", ns="hub", check=False).returncode == 0:
                break
            time.sleep(0.02)
        else:
            raise RuntimeError(f"rina-host did not listen in 5 s: see {h}/rina-host.log")

    def start_members(self):
        for n in self.bed.mine:
            d = os.path.join(self.work, n)
            os.makedirs(d, exist_ok=True)
            open(f"{d}/leases", "w").close()               # no dnsmasq on the bed: no leases given
            self.spawn(n, "fdf-facts", ["python3", f"{self.fdf}/bin/fdf-facts", "--watch"])
            self.spawn(n, "fdf-client", [f"{self.bins}/fdf-client"])
            self.spawn(n, "reader", ["python3", os.path.join(_HERE, "s4_reader.py")], reader=True)

    def wait(self, test, timeout_s):
        """Held on the readers' lines (and the supervisors' restarts): test(view, restarts) after each one."""
        t_end = time.time() + timeout_s
        with self.cv:
            while not test(self.view, self.restarts):
                left = t_end - time.time()
                if left <= 0:
                    return False
                self.cv.wait(left)
            return True

    def stop(self):
        self.stopping = True
        for p in self.procs:
            if p.poll() is None:
                p.terminate()
        for p in self.procs:
            try:
                p.wait(5)
            except subprocess.TimeoutExpired:
                p.kill()


def ram_bytes(bed):
    """Bytes the region's traffic put on the wire, per hop (tx of TCP port 8840 on every interface of every node),
    from nftables counters in each namespace's egress hooks. Set up by count_ram()."""
    tot = 0
    for n in bed.mine:
        r = B.sh("nft", "-j", "list", "table", "netdev", "s4ram", ns=n, check=False)
        if r.returncode:
            continue
        for o in json.loads(r.stdout).get("nftables", []):
            for x in (o.get("rule") or {}).get("expr", []):
                if "counter" in x:
                    tot += x["counter"]["bytes"]
    return tot


def count_ram(bed):
    for n in bed.mine:
        devs = [i for i in bed.p["nodes"][n]["ifaces"] if i not in ("lan0", "ram0")]   # as wire_bytes: the sender's own
        B.sh("nft", "add", "table", "netdev", "s4ram", ns=n)
        for dev in devs:
            B.sh("nft", "add", "chain", "netdev", "s4ram", f"o_{dev}",
                 f"{{ type filter hook egress device {dev} priority -10 ; }}", ns=n)
            B.sh("nft", "add", "rule", "netdev", "s4ram", f"o_{dev}", "tcp", "sport", str(RAM_PORT), "counter", ns=n)
            B.sh("nft", "add", "rule", "netdev", "s4ram", f"o_{dev}", "tcp", "dport", str(RAM_PORT), "counter", ns=n)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", default="tuned")
    ap.add_argument("--S", type=int, default=20)
    ap.add_argument("--E", type=int, default=2)
    ap.add_argument("--ring", action="store_true")
    ap.add_argument("--steady", type=float, default=30)
    ap.add_argument("--settle", type=float, default=15, help="after a repair: how long to keep watching for a late session loss")
    ap.add_argument("--timeout", type=float, default=240)
    ap.add_argument("--out", default=".")
    ap.add_argument("--frr-bin", default=None)
    ap.add_argument("--ram-bin", default=None)
    ap.add_argument("--fdf", default=None)
    a = ap.parse_args()
    arch = os.uname().machine
    ram_bin = a.ram_bin or f"/opt/fdf/experiments/day-claims/scale/bin/{arch}"
    fdf = a.fdf or "/opt/fdf/experiments/day-claims/s4/fdf"
    p = B.plan(a.S, a.E, [{"name": "local", "ip": "127.0.0.1"}], ring=a.ring)
    sp, own = B.shortest(p), B.owners(p)
    work = tempfile.mkdtemp(prefix=f"s4-layer-{a.profile}-S{a.S}E{a.E}-", dir=a.out)
    bed = B.Bed(p, "local").build()
    names = sorted(p["nodes"])
    cut = {n for n, d in p["nodes"].items() if n == "e1" or d.get("egress") == "e1"}
    res = {"profile": a.profile, "S": a.S, "E": a.E, "ring": a.ring, "nodes": len(names), "work": work}
    w = isis = reg = None

    def walks(t, cutset=frozenset()):
        ok = tot = 0
        bad = []
        served = {d["served"]: n for n, d in p["nodes"].items() if d.get("served")}
        for src in p["nodes"]:
            for net, dst in served.items():
                if dst == src:
                    continue
                tot += 1
                path, v = B.walk(p, t, src, net, own, frozenset(bed.silenced))
                if (src in cutset) != (dst in cutset):
                    good = v == "NO ROUTE"
                else:
                    good = v == "ok"
                ok += good
                if not good and len(bad) < 5:
                    bad.append(f"{src}->{net}: {v} {'>'.join(path)}")
        return ok, tot, bad

    def ready(view, names_expected):
        return all(view.get(n, {}).get("with_facts") == names_expected for n in names_expected)

    def restarts_since(t):
        return [(round(x - t, 2), n, nm) for x, n, nm, _rc in reg.restarts if x >= t]

    def isis_wait(cutset=frozenset()):
        t0 = time.time()
        good, tables = w.wait_until(lambda t: walks(t, cutset)[0] == walks(t, cutset)[1], a.timeout)
        return (round(time.time() - t0, 2) if good else None), walks(tables, cutset)

    try:
        w = B.Watcher(bed)
        count_ram(bed)
        # ---- IS-IS
        b0 = B.wire_bytes(bed)
        isis = s4_isis.Isis(bed, os.path.join(work, "isis"), a.profile, a.frr_bin)
        t0 = isis.start()
        good, tables = w.wait_until(lambda t: walks(t)[0] == walks(t)[1], a.timeout)
        res["isis_cold_s"] = round(time.time() - t0, 2) if good else None
        res["isis_cold_bad"] = walks(tables)[2]
        b1 = B.wire_bytes(bed)
        res["isis_cold_bytes"] = b1[0] - b0[0]
        time.sleep(a.steady)
        b2 = B.wire_bytes(bed)
        res["isis_steady_bytes"] = b2[0] - b1[0]
        # ---- the region above it
        reg = Region(bed, work, ram_bin, fdf)
        r0 = ram_bytes(bed)
        t1 = time.time()
        reg.start()
        ok = reg.wait(lambda v, r: ready(v, names), a.timeout)
        res["ram_cold_s"] = round(time.time() - t1, 2) if ok else None
        b3, r1 = B.wire_bytes(bed), ram_bytes(bed)
        res.update(ram_cold_bytes=r1 - r0, ram_cold_restarts=restarts_since(t1))
        time.sleep(a.steady)
        b4, r2 = B.wire_bytes(bed), ram_bytes(bed)
        res.update(steady_s=a.steady, steady_bytes_all=b4[0] - b3[0], steady_bytes_ram=r2 - r1)

        def phase(tag, links_off, links_on, cutset, want):
            """Silence/restore links. Then, measured: IS-IS's repair (every walk right again: a hop over a silenced
            link counts as lost); the applications, from every reader line in the window: which were disrupted (a
            line whose view is not the wanted one, or a session that ended) and when the last of them settled on the
            wanted view for good; the sessions lost (reader views that ended; fdf-client sessions that ended)."""
            ts = time.time()
            rb = ram_bytes(bed); wb = B.wire_bytes(bed)
            for n, dev in links_off:
                bed.silence(n, dev)
            for n, dev in links_on:
                bed.silence(n, dev, False)
            dt, (ok_, tot_, bad_) = isis_wait(cutset)
            res[f"{tag}_isis_s"] = dt
            res[f"{tag}_isis_ok"] = f"{ok_}/{tot_}"
            if bad_:
                res[f"{tag}_isis_bad"] = bad_
            reg.wait(lambda v, r: all(want(n, v.get(n, {})) for n in names), a.timeout)
            time.sleep(a.settle)                 # a session ends ~5 s after its far end stops answering: watch for it
            with reg.cv:
                lines = [(n, d) for n, d in reg.lines if d["t"] >= ts]
                restarts = [(round(x - ts, 2), n, nm) for x, n, nm, _rc in reg.restarts if x >= ts]
                view = dict(reg.view)
            disrupted, settled = set(), {}
            for n, d in lines:
                if want(n, d):
                    settled.setdefault(n, d["t"])
                else:
                    disrupted.add(n)
                    settled.pop(n, None)
            final_ok = all(want(n, view.get(n, {})) for n in names)
            res[f"{tag}_apps_disrupted"] = sorted(disrupted)
            res[f"{tag}_apps_settled_s"] = (round(max([settled.get(n, ts) for n in names]) - ts, 2) if final_ok else None)
            res[f"{tag}_apps_ok_at_end"] = final_ok
            res[f"{tag}_views_ended"] = sorted(n for n, d in lines if d.get("ev") == "gone")   # one per ended session
            res[f"{tag}_client_sessions_ended"] = sorted(n for _t, n, nm in restarts if nm == "fdf-client")
            res[f"{tag}_first_loss_s"] = min([x for x, _n, _nm in restarts], default=None)
            res[f"{tag}_bytes_ram"] = ram_bytes(bed) - rb
            res[f"{tag}_bytes_all"] = B.wire_bytes(bed)[0] - wb[0]
            sizes = [d.get("read_bytes", 0) for _n, d in lines if d.get("read_bytes")]
            res[f"{tag}_reads"] = len(sizes)
            res[f"{tag}_read_bytes_max"] = max(sizes, default=0)

        hub_link = [("e1", "hl0"), ("hub", "hl1")]
        ring_links = [(l["a"][0], l["a"][1]) for l in p["links"] if l["kind"] == "ring" and "e1" in (l["a"][0], l["b"][0])] + \
                     [(l["b"][0], l["b"][1]) for l in p["links"] if l["kind"] == "ring" and "e1" in (l["a"][0], l["b"][0])]
        full = lambda n, d: d.get("with_facts") == names
        if a.ring:
            phase("reroute", hub_link, [], frozenset(), full)
            phase("restore", [], hub_link, frozenset(), full)
        far = sorted(set(names) - cut)
        near = sorted(cut)

        def partitioned(n, d):
            # the hub's side sees exactly itself; e1's side has no view (its session ended) or none of the far side
            if n in far:
                return d.get("alive") == far
            return d.get("ev") == "gone" or (bool(d) and set(d.get("alive", [])) <= set(near))
        phase("partition", hub_link + ring_links, [], frozenset(cut), partitioned)
        phase("heal", [], hub_link + ring_links, frozenset(), full)
        u = isis.usage()
        res["isis_rss_kb_p50"] = sorted(v[1] for v in u.values())[len(u) // 2]
    finally:
        for x in (reg, isis):
            if x:
                x.stop()
        if w:
            w.close()
        bed.teardown()
    print(json.dumps(res, indent=1))
    json.dump(res, open(os.path.join(a.out, f"s4-layer-{a.profile}-S{a.S}-E{a.E}{'-ring' if a.ring else ''}.json"), "w"), indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
