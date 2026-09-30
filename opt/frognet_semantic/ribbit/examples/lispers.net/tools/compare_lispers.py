#!/usr/bin/env python3
"""compare_lispers.py -- the same LISP control-plane traffic, in parallel, against lispers.net and against Ribbit.

Each target is a Map-Server/Map-Resolver on UDP: lispers.net (lisp-core on 4342) or Ribbit's Map-Server front
(tools/lisp-boundary --udp PORT). Both are configured with the SAME site: instance 0, 198.18.0.0/16,
accept-more-specifics, one authentication key, and the Map-Server answering Map-Requests itself (lispers.net:
force-proxy-reply = yes; Ribbit always answers from its held registrations).

Every sender is its own xTR (its own UDP socket). All senders start together, per phase, per target:
  register    each xTR registers its prefixes (HMAC-SHA-256-128, want-map-notify): Map-Register -> Map-Notify
  refresh     the same registrations again, unchanged
  change      each registration re-sent with a different locator set (two RLOCs)
  resolve     Map-Requests, Encapsulated Control Messages as an ITR sends them to a map-resolver, for a registered EID
              (the answer must carry exactly the locators registered last), an unregistered EID inside the site, and
              an EID outside every site (both must be negative)
  withdraw    each registration withdrawn (record TTL 0), then resolved again (must be negative)
Every answer is matched to its request by nonce and CHECKED; a wrong answer is an error, not a sample.

usage: tools/compare_lispers.py --local-ip A.B.C.D --target lispers=HOST:4342 --target ribbit=HOST:PORT
          [--key-id 1 --password compare-secret] [--xtrs 16] [--prefixes 8] [--resolvers 16] [--timeout 3]
Authentication is HMAC-SHA-256-128: lispers.net 0.643 under Python 3 crashes its Map-Server process on an
HMAC-SHA-1-96 Map-Register (FINDINGS-FOR-DINO.md), so SHA-1 would measure a restart, not a register.
"""
import argparse, hashlib, hmac, json, os, socket, statistics, struct, subprocess, sys, threading, time

# ------------------------------------------------------------------------------------------------ LISP messages
def ip4(a): return socket.inet_aton(a)

def map_register(records, key_id, password, nonce, want_notify=True):
    """records: [(prefix 'a.b.c.0', masklen, ttl_seconds, [rloc, ...])]. HMAC-SHA-256-128 over the message."""
    first = (3 << 28) | 0x800 | (0x100 if want_notify else 0) | len(records)
    p = bytearray(struct.pack("!IQBBH", first, nonce, key_id, 2, 32) + bytes(32))
    for prefix, mask, ttl, rlocs in records:
        p += struct.pack("!IBBHHH", (0x80000000 | ttl) if ttl else 0, len(rlocs), mask, 0, 0, 1) + ip4(prefix)
        for r in rlocs:
            p += struct.pack("!BBBBHH", 1, 100, 0, 0, 1 | 0x4, 1) + ip4(r)      # priority 1, weight 100, R bit, AFI 1
    p[16:48] = hmac.new(password.encode(), bytes(p), hashlib.sha256).digest()
    return bytes(p)

def map_request(eid, nonce, itr_rloc):
    return (struct.pack("!IQH", (1 << 28) | 1, nonce, 0) + struct.pack("!H", 1) + ip4(itr_rloc)
            + struct.pack("!BBH", 0, 32, 1) + ip4(eid))

def _csum(b):
    s = sum(struct.unpack("!%dH" % (len(b) // 2), b)); s = (s >> 16) + (s & 0xffff); s += s >> 16
    return (~s) & 0xffff

def ecm(inner, src, dst, sport):
    udp = struct.pack("!HHHH", sport, 4342, 8 + len(inner), 0)
    ip = struct.pack("!BBHHHBBH4s4s", 0x45, 0, 20 + 8 + len(inner), 0, 0, 64, 17, 0, ip4(src), ip4(dst))
    ip = ip[:10] + struct.pack("!H", _csum(ip)) + ip[12:]
    return struct.pack("!I", 8 << 28) + ip + udp + inner

def parse_map_reply(d):
    """-> (eid prefix, masklen, [locator addresses]) of the first record; raises on anything malformed."""
    if len(d) < 12 or d[0] >> 4 != 2: raise ValueError("not a Map-Reply (type %d)" % (d[0] >> 4))
    if d[3] < 1: raise ValueError("Map-Reply with no records")
    o = 12
    ttl, nloc, mask, _flags, _ver, afi = struct.unpack("!IBBHHH", d[o:o + 12]); o += 12
    if afi != 1: raise ValueError("record AFI %d" % afi)
    eid = socket.inet_ntoa(d[o:o + 4]); o += 4
    locs = []
    for _ in range(nloc):
        _p, _w, _mp, _mw, _fl, lafi = struct.unpack("!BBBBHH", d[o:o + 8]); o += 8
        if lafi != 1: raise ValueError("locator AFI %d" % lafi)
        locs.append(socket.inet_ntoa(d[o:o + 4])); o += 4
    return eid, mask, locs

# ------------------------------------------------------------------------------------------------ one UDP peer
class Peer:
    """One xTR/ITR: its own socket; every exchange matched by nonce; returns (answer bytes, seconds)."""
    def __init__(self, local_ip, target, timeout):
        self.s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); self.s.bind((local_ip, 0))
        self.port = self.s.getsockname()[1]; self.target = target; self.timeout = timeout
        self.out = self.inb = 0
    def exchange(self, msg, nonce_bytes):
        t0 = time.perf_counter(); self.s.sendto(msg, self.target); self.out += len(msg)
        while True:
            left = self.timeout - (time.perf_counter() - t0)
            if left <= 0: return None, time.perf_counter() - t0
            self.s.settimeout(left)
            try: d, _ = self.s.recvfrom(4096)
            except socket.timeout: return None, time.perf_counter() - t0
            self.inb += len(d)
            if len(d) >= 12 and d[4:12] == nonce_bytes: return d, time.perf_counter() - t0

# ------------------------------------------------------------------------------------------------ recording
class Recorder:
    """Every measurement into the tuple space, as the run goes (ACCEPTANCE-TEST-PLAN.md section 6).
    Addresses are built only from what was measured -- never a clock, never a random number:
        service  lisp_acceptance
        variable <test id>.<metric>                              L5.2.latency_us
        instance <system>|<version>|<wire>|<condition>|r<run>|c<chunk>
    <condition> is the parameters, keys sorted, k=v joined by ','; <run> is 1 + the highest run already recorded for
    <system>|<version>|<wire> (read once, when the system's run starts); <chunk> splits samples into cells of <= 1000.
    Samples are written BETWEEN phases, never inside a timed window. A write to an existing address replaces it."""
    SERVICE, CHUNK = "lisp_acceptance", 1000
    def __init__(self, host_port, wire):
        host, port = host_port.rsplit(":", 1)
        tool = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tuple-write")
        self.p = subprocess.Popen([tool, "--ram", host, port, "--api", "/lisper-api"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        self.wire, self.host = wire, socket.gethostname()
    def _line(self, obj):
        self.p.stdin.write(json.dumps(obj) + "\n"); self.p.stdin.flush()
        r = json.loads(self.p.stdout.readline())
        if not r.get("ok"): raise RuntimeError("tuple space: %s" % r.get("error"))
        return r
    def write(self, variable, instance, bag):
        # "bag" must be the LAST key: tuple-write passes the bag through exactly as written
        return self._line({"service": self.SERVICE, "variable": variable, "instance": instance, "bag": bag})["id"]
    def next_run(self, system, version):
        prefix = "%s|%s|%s|r" % (system, version, self.wire); runs = [0]
        for inst in self._line({"read": {"service": self.SERVICE, "variable": "run.manifest"}})["instances"]:
            if inst.startswith(prefix) and inst[len(prefix):].isdigit(): runs.append(int(inst[len(prefix):]))
        return max(runs) + 1
    def manifest(self, system, version, run, bag):
        self.write("run.manifest", "%s|%s|%s|r%d" % (system, version, self.wire, run), bag)
    def samples(self, test, metric, unit, system, version, condition, run, values, errors):
        cond = ",".join("%s=%s" % (k, condition[k]) for k in sorted(condition))
        chunks = [values[i:i + self.CHUNK] for i in range(0, len(values), self.CHUNK)] or [[]]
        for c, part in enumerate(chunks):
            self.write("%s.%s" % (test, metric), "%s|%s|%s|%s|r%d|c%d" % (system, version, self.wire, cond, run, c),
                       {"schema": 1, "test": test, "metric": metric, "unit": unit, "system": system, "version": version,
                        "wire": self.wire, "condition": condition, "run": run, "chunk": c, "n": len(part), "samples": part,
                        "errors": {"count": len(errors), "first": errors[:5]}, "clock": "time.perf_counter", "host": self.host})

# ------------------------------------------------------------------------------------------------ the workload
class Phase:
    def __init__(self, name): self.name, self.lat, self.errors, self.lock = name, [], [], threading.Lock()
    def ok(self, sec):
        with self.lock: self.lat.append(sec)
    def bad(self, why):
        with self.lock: self.errors.append(why)

def run_target(label, target, a, rec=None, version=None):
    xtrs = [Peer(a.local_ip, target, a.timeout) for _ in range(a.xtrs)]
    itrs = [Peer(a.local_ip, target, a.timeout) for _ in range(a.resolvers)]
    nonce = [0x7000000000000000 + (hash(label) & 0xffffff) * 0x1000000]
    nlock = threading.Lock()
    def next_nonce():
        with nlock: nonce[0] += 1; return nonce[0]
    # xTR k owns /24s 198.18.(k*prefixes + j).0; locator sets it registered last, per prefix
    owned = {k: ["198.18.%d.0" % (k * a.prefixes + j) for j in range(a.prefixes)] for k in range(a.xtrs)}
    rlocset = {}
    phases, t_phase = [], {}
    run = rec.next_run(label, version) if rec else 0
    if rec:
        rec.manifest(label, version, run, {"schema": 1, "system": label, "version": version, "wire": rec.wire, "run": run,
                     "target": "%s:%d" % target, "xtrs": a.xtrs, "prefixes": a.prefixes, "resolvers": a.resolvers,
                     "requests": a.requests, "timeout_s": a.timeout, "host": rec.host,
                     "started": time.strftime("%Y-%m-%dT%H:%M:%S%z")})
    def record(ph):                              # between phases: this phase is over, the next has not started
        if rec:
            rec.samples("L5.2", "latency_us", "us", label, version,
                        {"class": ph.name.replace(" ", "_"), "xtrs": a.xtrs, "prefixes": a.prefixes, "resolvers": a.resolvers, "requests": a.requests},
                        run, [int(round(x * 1e6)) for x in ph.lat], ph.errors)

    def parallel(fn, peers):
        ths = [threading.Thread(target=fn, args=(i, p)) for i, p in enumerate(peers)]
        t0 = time.perf_counter()
        for t in ths: t.start()
        for t in ths: t.join()
        return time.perf_counter() - t0

    def register_phase(name, locs_for, ttl):
        ph = Phase(name); phases.append(ph)
        def go(k, peer):
            for pfx in owned[k]:
                rl = locs_for(k, pfx); n = next_nonce()
                d, sec = peer.exchange(map_register([(pfx, 24, ttl, rl)], a.key_id, a.password, n), struct.pack("!Q", n))
                if d is None: ph.bad("%s %s: no Map-Notify in %.1fs" % (name, pfx, a.timeout)); continue
                if d[0] >> 4 != 4: ph.bad("%s %s: answered type %d, not a Map-Notify" % (name, pfx, d[0] >> 4)); continue
                ph.ok(sec)
                if ttl: rlocset[pfx] = sorted(rl)
                else: rlocset.pop(pfx, None)
        t_phase[name] = parallel(go, xtrs)
        record(ph)

    def resolve_phase(name):
        ph = Phase(name); phases.append(ph)
        registered = sorted(rlocset.items())
        withdrawn = [p for k in owned for p in owned[k] if p not in rlocset]
        def go(i, peer):
            for j in range(a.requests):
                kind = (i + j) % 3
                if kind == 0 and registered:
                    pfx, want = registered[(i * a.requests + j) % len(registered)]
                    eid = pfx[:-1] + "9"
                elif kind == 0 and withdrawn:
                    pfx = withdrawn[(i * a.requests + j) % len(withdrawn)]; eid, want = pfx[:-1] + "9", []
                elif kind == 1:
                    eid, want = "198.18.%d.%d" % (250 - (j % 5), 1 + i % 200), []          # in the site, never registered
                else:
                    eid, want = "203.0.113.%d" % (1 + (i + j) % 250), []                    # outside every site
                n = next_nonce()
                d, sec = peer.exchange(ecm(map_request(eid, n, a.local_ip), a.local_ip, eid, peer.port), struct.pack("!Q", n))
                if d is None: ph.bad("%s %s: no Map-Reply in %.1fs" % (name, eid, a.timeout)); continue
                try: _e, _m, locs = parse_map_reply(d)
                except ValueError as x: ph.bad("%s %s: %s" % (name, eid, x)); continue
                if sorted(locs) != want: ph.bad("%s %s: locators %s, expected %s" % (name, eid, sorted(locs), want)); continue
                ph.ok(sec)
        t_phase[name] = parallel(go, itrs)
        record(ph)

    # Every locator set includes this machine's own address: a withdrawal (TTL 0) is honoured only from an RLOC of
    # the registration, and this machine is where the withdrawals come from. (Both implementations ignore a TTL-0
    # register from anyone else -- lispers.net still Map-Notifies it, Ribbit does not answer it.)
    me = a.local_ip
    register_phase("register", lambda k, p: [me], 180)
    register_phase("refresh", lambda k, p: [me], 180)
    register_phase("change", lambda k, p: [me, "198.51.100.%d" % (1 + k % 250)], 180)
    resolve_phase("resolve")
    register_phase("withdraw", lambda k, p: [], 0)
    resolve_phase("resolve after withdraw")
    total_out = sum(p.out for p in xtrs + itrs); total_in = sum(p.inb for p in xtrs + itrs)
    return phases, t_phase, total_out, total_in

def pct(xs, f):
    s = sorted(xs); return s[min(len(s) - 1, int(round(f * (len(s) - 1))))] * 1000 if s else float("nan")

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--target", action="append", required=True, help="NAME=HOST:PORT (repeat)")
    ap.add_argument("--local-ip", required=True, help="this machine's address the targets answer to (the ITR-RLOC)")
    ap.add_argument("--key-id", type=int, default=1); ap.add_argument("--password", default="compare-secret")
    ap.add_argument("--xtrs", type=int, default=16); ap.add_argument("--prefixes", type=int, default=8)
    ap.add_argument("--resolvers", type=int, default=16); ap.add_argument("--requests", type=int, default=30)
    ap.add_argument("--timeout", type=float, default=3.0)
    ap.add_argument("--record", help="RAM host HOST:PORT: write every measurement to its tuple space as the run goes")
    ap.add_argument("--wire", help="with --record: loopback | lan | inet")
    ap.add_argument("--version", action="append", default=[], help="with --record: NAME=VERSION for each target (repeat)")
    a = ap.parse_args()
    if a.xtrs * a.prefixes > 240: ap.error("xtrs * prefixes must fit 198.18.0-239.0/24 (240)")
    rec = None; versions = dict(v.split("=", 1) for v in a.version)
    if a.record:
        if not a.wire: ap.error("--record needs --wire")
        missing = [t.split("=", 1)[0] for t in a.target if t.split("=", 1)[0] not in versions]
        if missing: ap.error("--record needs --version for: " + ", ".join(missing))
        rec = Recorder(a.record, a.wire)
    results = {}
    for t in a.target:
        label, hp = t.split("=", 1); host, port = hp.rsplit(":", 1)
        print("== %s (%s:%s): %d xTRs x %d prefixes, %d resolvers x %d requests, all in parallel"
              % (label, host, port, a.xtrs, a.prefixes, a.resolvers, a.requests), flush=True)
        results[label] = run_target(label, (host, int(port)), a, rec, versions.get(label))
        phases, tp, bo, bi = results[label]
        for ph in phases:
            n = len(ph.lat) + len(ph.errors)
            print("   %-24s %5d msgs  %4d errors  median %7.2f ms  p90 %7.2f  p99 %7.2f  %8.0f msg/s"
                  % (ph.name, n, len(ph.errors), pct(ph.lat, .5), pct(ph.lat, .9), pct(ph.lat, .99),
                     n / tp[ph.name] if tp[ph.name] else 0), flush=True)
            for e in ph.errors[:3]: print("      ERROR " + e)
        print("   bytes on the wire: %d out, %d in" % (bo, bi))
    if len(results) == 2:
        (la, ra), (lb, rb) = list(results.items())
        print("\n%-24s %14s %14s %8s" % ("median ms", la, lb, lb + "/" + la))
        for pa, pb in zip(ra[0], rb[0]):
            ma, mb = pct(pa.lat, .5), pct(pb.lat, .5)
            print("%-24s %14.2f %14.2f %8.2f" % (pa.name, ma, mb, mb / ma if ma else float("nan")))
    bad = sum(len(ph.errors) for r in results.values() for ph in r[0])
    print("\n%s: %d wrong or missing answers" % ("COMPARE PASS" if not bad else "COMPARE FAIL", bad))
    return 0 if not bad else 1

if __name__ == "__main__":
    sys.exit(main())
