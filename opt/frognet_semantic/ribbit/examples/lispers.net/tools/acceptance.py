#!/usr/bin/env python3
"""acceptance.py -- the LISP control-plane acceptance test (ACCEPTANCE-TEST-PLAN.md), lispers.net vs Ribbit-LISP.

The SAME stimulus goes to both systems, as UDP LISP control messages on each one's map-server / map-resolver port;
every answer is decoded and checked field by field; a system that stops answering is a result, recorded with its
evidence, and is restarted before the next test. Each test is classified PARITY, A FAIL (lispers.net), B FAIL
(Ribbit), KNOWN DIFFERENCE or OBSERVATION. With --record, every result and every timed sample goes to the tuple space.

Run it from the package directory with a RELATIVE path (python3 tools/acceptance.py ...): starting lispers.net runs
its STOP-LISP, which kills every process whose command line contains "lisp-" -- this harness's own included if its
path does. The harness starts lispers.net first and (re)starts Ribbit's front after every lispers.net restart.

usage: python3 tools/acceptance.py --local-ip A.B.C.D --lispers-dir DIR --lispers-api-port 8800
          --ram-server ./ribbit_cpp/lisper-ram --ram-port 8870 --ribbit-udp 14342
          [--lispers-env K=V ...] [--record HOST:PORT --wire LABEL] [--out DIR]
"""
import threading, random
import argparse
import array
import multiprocessing, hashlib, hmac, json, os, shutil, signal, socket, struct, subprocess, sys, time
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
from compare_lispers import ecm, map_request, ip4, Recorder

# ------------------------------------------------------------------------------------------------ the fixture
KEY_ID, PASSWORD = 1, "acceptance-secret"
ENC_KEY_ID, ENC_KEY = 2, "acceptance-enc-key"          # Map-Register encryption (lisp encryption-keys)
SITES = [   # one fixture configures BOTH systems (P3)
    {"name": "ams", "iid": "0", "prefix": "198.18.0.0/16", "ams": True},
    {"name": "exact", "iid": "0", "prefix": "198.19.0.0/24", "ams": False},
    {"name": "v6", "iid": "0", "prefix": "2001:db8:acce::/48", "ams": True},
    {"name": "iid7", "iid": "7", "prefix": "198.18.0.0/16", "ams": True},
    {"name": "shut", "iid": "0", "prefix": "198.21.0.0/16", "ams": True},      # deleted and restored by L2.13
    # site options (L2.15): each site carries one of lispers.net's allowed-prefix / site knobs
    {"name": "opt-shutdown", "iid": "0", "prefix": "198.22.0.0/16", "ams": True, "shutdown": True},
    {"name": "opt-rloc", "iid": "0", "prefix": "198.23.0.0/16", "ams": True, "allowed_rlocs": ["LOCAL_IP"]},
    {"name": "opt-ttl", "iid": "0", "prefix": "198.24.0.0/16", "ams": True, "force_ttl": 30},
    {"name": "opt-nry", "iid": "0", "prefix": "198.25.0.0/16", "ams": True, "proxy_reply_action": "not-registered-yet"},
    {"name": "opt-echo", "iid": "0", "prefix": "198.26.0.0/16", "ams": True, "echo_nonce_capable": True},
    {"name": "opt-drop", "iid": "0", "prefix": "198.27.0.0/16", "ams": True, "force_proxy_reply": False, "proxy_reply_action": "drop"},
    {"name": "opt-pitr", "iid": "0", "prefix": "198.28.0.0/16", "ams": True, "force_proxy_reply": False, "pitr_proxy_reply_drop": True},
    {"name": "opt-nat", "iid": "0", "prefix": "198.33.0.0/16", "ams": True, "force_proxy_reply": False, "force_nat_proxy_reply": True},
    # multicast (S,G) (L2.16): sources in 198.29.0.0/16 sending to groups in 224.1.0.0/16
    {"name": "mcast", "iid": "0", "prefix": "198.29.0.0/16", "group": "224.1.0.0/16", "ams": True},
    # policies (L2.17): each site names one of POLICIES
    {"name": "pol-ttl", "iid": "0", "prefix": "198.30.0.0/16", "ams": True, "policy_name": "acc-ttl"},
    {"name": "pol-drop", "iid": "0", "prefix": "198.31.0.0/16", "ams": True, "policy_name": "acc-drop"},
    {"name": "pol-rloc", "iid": "0", "prefix": "198.32.0.0/16", "ams": True, "policy_name": "acc-rloc"},
    {"name": "pol-geo", "iid": "0", "prefix": "198.34.0.0/16", "ams": True, "policy_name": "acc-pgeo"},
    {"name": "pol-elp", "iid": "0", "prefix": "198.35.0.0/16", "ams": True, "policy_name": "acc-pelp"},
    {"name": "pol-rle", "iid": "0", "prefix": "198.36.0.0/16", "ams": True, "policy_name": "acc-prle"},
    {"name": "pol-json", "iid": "0", "prefix": "198.37.0.0/16", "ams": True, "policy_name": "acc-pjson"},
]
# named locator objects (lisp geo-coordinates / explicit-locator-path / replication-list-entry / json) -- the same
# objects the LCAF vectors were encoded from (tools/coverage/lcaf_vectors_from_lispers.py)
NAMED = {"geo": {"name": "acc-geo", "geo_tag": "45-30-10-N-122-40-20-W"},
         "elp": {"name": "acc-elp", "nodes": [{"address": "198.51.100.1", "strict": True}, {"address": "198.51.100.2", "strict": False}]},
         "rle": {"name": "acc-rle", "nodes": [{"address": "198.51.100.3", "level": 0}, {"address": "198.51.100.4", "level": 1}]},
         "json": {"name": "acc-json", "json_string": '{"site":"acceptance","n":1}'}}
POLICIES = [   # lisp policy (lispers.net) / ms.policy (Ribbit)
    {"name": "acc-ttl", "match": [{"destination_eid": "198.30.1.0/24"}], "set_record_ttl": 10},
    {"name": "acc-drop", "match": [{"destination_eid": "198.31.0.0/16"}], "set_action": "drop"},
    {"name": "acc-rloc", "match": [{"source_rloc": "LOCAL_IP/32"}], "set_rloc_address": "198.51.100.232"},
    {"name": "acc-pgeo", "match": [{"destination_eid": "198.34.0.0/16"}], "set_rloc_address": "198.51.100.9", "set_geo_name": "acc-geo"},
    {"name": "acc-pelp", "match": [{"destination_eid": "198.35.0.0/16"}], "set_rloc_address": "198.51.100.9", "set_elp_name": "acc-elp"},
    {"name": "acc-prle", "match": [{"destination_eid": "198.36.0.0/16"}], "set_rloc_address": "198.51.100.9", "set_rle_name": "acc-rle"},
    {"name": "acc-pjson", "match": [{"destination_eid": "198.37.0.0/16"}], "set_rloc_address": "198.51.100.9", "set_json_name": "acc-json"},
]
def policy_args(p):
    q = dict(p); q["match"] = [{k: (v.replace("LOCAL_IP", LOCAL_IP)) for k, v in m.items()} for m in p["match"]]; return q
LOCAL_IP = None                                   # set from --local-ip: the one RLOC opt-rloc allows

def lispers_config(sites):
    # The header exactly as lispers.net's own lisp.config.example writes it: lispers.net refuses a file without it as
    # corrupt, and when its API changes the configuration it rewrites the date after the header's ':' -- a header
    # without ':' is destroyed by that rewrite (the text before the date is dropped) and the next start refuses the file.
    out = ["# lispers.net lisp.config file, last changed: " + time.strftime("%a %b %d %H:%M:%S %Z %Y"), "lisp enable {", "    itr = no", "    etr = no", "    rtr = no",
           "    map-server = yes", "    map-resolver = yes", "    ddt-node = no", "}",
           "lisp user-account {", "    username = root", "    password =", "    super-user = yes", "}"]
    for s in sites:
        yn = lambda b: "yes" if b else "no"
        out += ["lisp site {", "    site-name = acceptance-%s" % s["name"], "    authentication-key = [%d]%s" % (KEY_ID, PASSWORD)]
        if s.get("shutdown"): out += ["    shutdown = yes"]
        out += ["    allowed-prefix {", "        instance-id = %s" % s["iid"], "        eid-prefix = %s" % s["prefix"]] + \
               (["        group-prefix = %s" % s["group"]] if s.get("group") else []) + [
                "        accept-more-specifics = %s" % yn(s["ams"]), "        force-proxy-reply = %s" % yn(s.get("force_proxy_reply", True))]
        if "force_ttl" in s: out += ["        force-ttl = %d" % s["force_ttl"]]
        if "proxy_reply_action" in s: out += ["        proxy-reply-action = %s" % s["proxy_reply_action"]]
        if s.get("echo_nonce_capable"): out += ["        echo-nonce-capable = yes"]
        if s.get("pitr_proxy_reply_drop"): out += ["        pitr-proxy-reply-drop = yes"]
        if s.get("force_nat_proxy_reply"): out += ["        force-nat-proxy-reply = yes"]
        if s.get("policy_name"): out += ["        policy-name = %s" % s["policy_name"]]
        out += ["    }"]
        for r in s.get("allowed_rlocs", []): out += ["    allowed-rloc {", "        address = %s" % (LOCAL_IP if r == "LOCAL_IP" else r), "    }"]
        out += ["}"]
    g = NAMED["geo"]; out += ["lisp geo-coordinates {", "    geo-name = %s" % g["name"], "    geo-tag = %s" % g["geo_tag"], "}"]
    e = NAMED["elp"]; out += ["lisp explicit-locator-path {", "    elp-name = %s" % e["name"]]
    for nd in e["nodes"]: out += ["    elp-node {", "        address = %s" % nd["address"], "        strict = %s" % ("yes" if nd["strict"] else "no"), "    }"]
    out += ["}"]
    r = NAMED["rle"]; out += ["lisp replication-list-entry {", "    rle-name = %s" % r["name"]]
    for nd in r["nodes"]: out += ["    rle-node {", "        address = %s" % nd["address"], "        level = %d" % nd["level"], "    }"]
    out += ["}"]
    j = NAMED["json"]; out += ["lisp json {", "    json-name = %s" % j["name"], "    json-string = %s" % j["json_string"], "}"]
    for p in POLICIES:
        p = policy_args(p)
        out += ["lisp policy {", "    policy-name = %s" % p["name"]]
        for m in p["match"]:
            out += ["    match {"] + ["        %s = %s" % (k.replace("_", "-"), v) for k, v in m.items()] + ["    }"]
        if "set_action" in p: out += ["    set-action = %s" % p["set_action"]]
        if "set_record_ttl" in p: out += ["    set-record-ttl = %d" % p["set_record_ttl"]]
        if "set_rloc_address" in p: out += ["    set-rloc-address = %s" % p["set_rloc_address"]]
        for k in ("set_geo_name", "set_elp_name", "set_rle_name", "set_json_name"):
            if k in p: out += ["    %s = %s" % (k.replace("_", "-"), p[k])]
        out += ["}"]
    # LISP-DDT (RFC 8111): the map-server is authoritative for its sites' space, so a DDT-originated Map-Request is
    # answered with a Map-Referral
    out += ["lisp ms-authoritative-prefix {", "    instance-id = 0", "    eid-prefix = 198.18.0.0/15", "}"]
    out += ["lisp encryption-keys {", "    map-register-key = [%d]%s" % (ENC_KEY_ID, ENC_KEY), "}"]
    return "\n".join(out) + "\n"

def ribbit_site_args(s):
    a = {"iid": s["iid"], "prefix": s["prefix"], "group": s.get("group", ""), "accept_more_specifics": s["ams"], "key_id": KEY_ID, "password": PASSWORD}
    for k in ("shutdown", "force_ttl", "proxy_reply_action", "echo_nonce_capable", "force_proxy_reply", "force_nat_proxy_reply", "pitr_proxy_reply_drop", "policy_name"):
        if k in s: a[k] = s[k]
    if "allowed_rlocs" in s: a["allowed_rlocs"] = [LOCAL_IP if r == "LOCAL_IP" else r for r in s["allowed_rlocs"]]
    return a

# ------------------------------------------------------------------------------------------------ LISP messages
def eid_bytes(prefix, iid=0):
    v6 = ":" in prefix
    plain = struct.pack("!H", 2 if v6 else 1) + (socket.inet_pton(socket.AF_INET6, prefix) if v6 else ip4(prefix))
    if not iid: return plain
    return struct.pack("!HBBBBHI", 16387, 0, 0, 2, 0, 4 + len(plain), iid) + plain     # LCAF instance-id (RFC 8060)

def register(records, nonce, key_id=KEY_ID, password=PASSWORD, alg=2, want_notify=True, count=None, iid=0, xtr_id=None, merge=False, site_id=0):
    """records: [(prefix, mask, ttl_field, [rlocs])]; alg 2 = HMAC-SHA-256-128, 1 = HMAC-SHA-1-96, 0 = none."""
    alen = {0: 0, 1: 20, 2: 32}[alg]
    first = (3 << 28) | 0x800 | (0x100 if want_notify else 0) | (len(records) if count is None else count)
    if xtr_id is not None: first |= 0x02000000                 # I: a 128-bit xTR-ID and 64-bit site-ID follow the records
    if merge: first |= 0x400                                     # merge semantics requested
    p = bytearray(struct.pack("!IQBBH", first, nonce, key_id, alg, alen) + bytes(alen))
    for prefix, mask, ttl, rlocs in records:
        p += struct.pack("!IBBHH", ttl, len(rlocs), mask, 0, 0) + eid_bytes(prefix, iid)
        for r in rlocs:
            if isinstance(r, bytes): p += r                      # a complete RLOC record, as given (LCAF locators)
            else: p += struct.pack("!BBBBHH", 1, 100, 0, 0, 0x5, 1) + ip4(r)
    if xtr_id is not None: p += xtr_id.to_bytes(16, "big") + site_id.to_bytes(8, "big")
    if alg:
        dig = hashlib.sha256 if alg == 2 else hashlib.sha1
        p[16:16 + alen] = hmac.new(password.encode(), bytes(p), dig).digest()
    return bytes(p)
def ttl_minutes(m): return m
def ttl_seconds(s): return 0x80000000 | s

def parse_reply(d):
    """Map-Reply -> dict(nonce, records=[{ttl, action, eid, mask, locs}]); raises on anything malformed."""
    if len(d) < 12 or d[0] >> 4 != 2: raise ValueError("not a Map-Reply (type %d)" % (d[0] >> 4 if d else -1))
    out = {"nonce": d[4:12], "records": []}; o = 12
    for _ in range(d[3]):
        ttl, nloc, mask, flags, _v, afi = struct.unpack("!IBBHHH", d[o:o + 12]); o += 12
        iid = 0
        if afi == 16387:                             # LCAF instance-id: the instance, then the plain AFI
            _r, _f, typ, _ml, ln, iid, afi = struct.unpack("!BBBBHIH", d[o:o + 12]); o += 12
            if typ != 2: raise ValueError("LCAF type %d" % typ)
        if afi not in (1, 2): raise ValueError("record AFI %d" % afi)
        n = 4 if afi == 1 else 16
        eid = socket.inet_ntoa(d[o:o + 4]) if afi == 1 else socket.inet_ntop(socket.AF_INET6, d[o:o + 16]); o += n; locs = []
        for _ in range(nloc):
            _p, _w, _mp, _mw, _fl, lafi = struct.unpack("!BBBBHH", d[o:o + 8]); o += 8
            if lafi != 1: raise ValueError("locator AFI %d" % lafi)
            locs.append(socket.inet_ntoa(d[o:o + 4])); o += 4
        out["records"].append({"ttl": ttl, "action": flags >> 13, "eid": eid, "mask": mask, "locs": sorted(locs), "iid": iid})
    return out

def notify_valid(d, password=PASSWORD):
    """A Map-Notify's HMAC recomputed with the site key over the message with its authentication data zeroed."""
    if len(d) < 16 or d[0] >> 4 != 4: return False
    alg, alen = d[13], struct.unpack("!H", d[14:16])[0]
    if alg not in (1, 2) or len(d) < 16 + alen: return False
    z = bytearray(d); z[16:16 + alen] = bytes(alen)
    return hmac.compare_digest(hmac.new(password.encode(), bytes(z), hashlib.sha256 if alg == 2 else hashlib.sha1).digest()[:alen], d[16:16 + alen])

def map_request_iid(eid, nonce, itr_rloc, iid):
    return (struct.pack("!IQH", (1 << 28) | 1, nonce, 0) + struct.pack("!H", 1) + ip4(itr_rloc)
            + struct.pack("!BB", 0, 32) + eid_bytes(eid, iid))
def map_request6(eid, nonce, itr_rloc):
    """A Map-Request for an IPv6 EID: source EID AFI 0, one IPv4 ITR-RLOC, one EID record /128."""
    return (struct.pack("!IQH", (1 << 28) | 1, nonce, 0) + struct.pack("!H", 1) + ip4(itr_rloc)
            + struct.pack("!BBH", 0, 128, 2) + socket.inet_pton(socket.AF_INET6, eid))
def ecm6(inner, eid, sport):
    """An Encapsulated Control Message whose inner packet is IPv6 (the EID's family) + UDP."""
    udp = struct.pack("!HHHH", sport, 4342, 8 + len(inner), 0)
    ip6 = struct.pack("!IHBB", 6 << 28, 8 + len(inner), 17, 64) + socket.inet_pton(socket.AF_INET6, "2001:db8:ffff::1") + socket.inet_pton(socket.AF_INET6, eid)
    return struct.pack("!I", 8 << 28) + ip6 + udp + inner

# ------------------------------------------------------------------------------------------------ one client
class Client:
    def __init__(self, local_ip, target):
        self.s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); self.s.bind((local_ip, 0))
        self.local_ip, self.target, self.nonce = local_ip, target, 0x0acc000000000000 + (os.getpid() << 20)
    def next(self): self.nonce += 1; return self.nonce
    def send(self, msg): self.s.sendto(msg, self.target)
    def wait_for(self, nonce, timeout):
        t0 = time.perf_counter(); want = struct.pack("!Q", nonce)
        while True:
            left = timeout - (time.perf_counter() - t0)
            if left <= 0: return None, None
            self.s.settimeout(left)
            try: d, _ = self.s.recvfrom(65535)
            except socket.timeout: return None, None
            if len(d) >= 12 and d[4:12] == want: return d, time.perf_counter() - t0
    def reg(self, records, timeout=2.0, **kw):
        n = self.next(); self.send(register(records, n, **kw)); return self.wait_for(n, timeout)
    def ask(self, eid, timeout=2.0, iid=0):
        n = self.next()
        if iid: self.send(ecm(map_request_iid(eid, n, self.local_ip, iid), self.local_ip, eid, self.s.getsockname()[1]))
        elif ":" in eid: self.send(ecm6(map_request6(eid, n, self.local_ip), eid, self.s.getsockname()[1]))
        else: self.send(ecm(map_request(eid, n, self.local_ip), self.local_ip, eid, self.s.getsockname()[1]))
        d, sec = self.wait_for(n, timeout)
        return (parse_reply(d) if d else None), sec

# ------------------------------------------------------------------------------------------------ the systems
class Lispers:
    name = "lispers.net"
    def __init__(self, a):
        self.dir, self.api_port, self.env = a.lispers_dir, a.lispers_api_port, dict(e.split("=", 1) for e in a.lispers_env)
        self.target = (a.local_ip, 4342); self.local_ip = a.local_ip
        # a lispers.net release directory has these; say which is missing and where it was looked for
        missing = [f for f in ("RUN-LISP", "STOP-LISP", "lisp-version.txt") if not os.path.exists(os.path.join(self.dir, f))]
        if missing: raise RuntimeError("--lispers-dir %s is not a lispers.net release directory: no %s there" % (os.path.abspath(self.dir), ", ".join(missing)))
        self.version = open(os.path.join(self.dir, "lisp-version.txt")).read().strip()
        cfg = os.path.join(self.dir, "lisp.config")
        self.saved = open(cfg).read() if os.path.exists(cfg) else None
        open(cfg, "w").write(lispers_config(SITES))
    def start(self):
        # lispers.net is up when its Map-Server answers a valid register -- not when RUN-LISP returns.
        env = dict(os.environ); env.update(self.env)
        # the fixture is the configuration: lispers.net rewrites lisp.config itself when its API changes a site
        # (L2.13), so every start writes it from the fixture again
        open(os.path.join(self.dir, "lisp.config"), "w").write(lispers_config(SITES))
        subprocess.run(["tcsh", "-c", "./STOP-LISP"], cwd=self.dir, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60)
        left = lispers_pids()
        if left:   # a second lispers.net on top of a first one cannot work: say which processes survived
            raise RuntimeError("STOP-LISP left lispers.net processes running (stop them, as root): " + " | ".join(left))
        subprocess.run(["tcsh", "-c", "./RUN-LISP %d" % self.api_port], cwd=self.dir, env=env,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60)
        c = Client(self.local_ip, self.target); t0 = time.time()
        while time.time() - t0 < 60:
            if still_serving(c): return
            time.sleep(1)
        # one start, no retries: lispers.net not answering a valid register is the result -- with its own evidence
        raise RuntimeError("lispers.net did not answer a valid Map-Register within 60 s of RUN-LISP; its logs:\n%s"
                           % (self.evidence() or "(no logs)"))
    def stop(self):
        subprocess.run(["tcsh", "-c", "./STOP-LISP"], cwd=self.dir, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60)
        if self.saved is not None: open(os.path.join(self.dir, "lisp.config"), "w").write(self.saved)
    def api(self):
        # lispers.net's own API client (lispapi, shipped with lispers.net), against its own REST port
        if self.dir not in sys.path: sys.path.insert(0, self.dir)
        import lispapi
        return lispapi.api_init(self.local_ip, "root", "", port=self.api_port)
    def delete_site(self, site): return self.api().delete_ms_site("acceptance-%s" % site["name"])
    def add_site(self, site):
        a = self.api()
        pl = []                                   # build_ms_site_allowed_prefix appends to the list it is given
        a.build_ms_site_allowed_prefix(pl, iid=site["iid"], prefix=site["prefix"], ams=site["ams"], fpr=True)
        return a.add_ms_site("acceptance-%s" % site["name"], "[%d]%s" % (KEY_ID, PASSWORD), pl)
    def restart_keep_state(self):                 # L4.10; starting lispers.net kills Ribbit's front too
        self.start(); self.ribbit_needs_restart = True; return {"ribbit_needs_restart": True}
    def lose_ram_host(self): return None          # L4.11: lispers.net holds its state in its own process
    def evidence(self):
        """The last lines of every lispers.net log (lisp-traceback.log alone can hold only its own failed handler)."""
        import glob as _g
        out = []
        for f in sorted(_g.glob(os.path.join(self.dir, "logs", "lisp-*.log"))):
            try: tail = open(f, errors="replace").read().splitlines()[-6:]
            except OSError: continue
            if tail: out.append("[%s] %s" % (os.path.basename(f), " / ".join(t.strip() for t in tail)))
        return "\n".join(out)

class Ribbit:
    name = "ribbit"
    def __init__(self, a):
        self.a = a; self.target = (a.local_ip, a.ribbit_udp); self.srv = self.front = None
        # the package's version (ribbit_cpp/version.hpp) -- the binaries print the same string
        import re as _re
        self.version = _re.search(r'RIBBIT_LISP_VERSION "([^"]+)"', open(os.path.join(HERE, "..", "ribbit_cpp", "version.hpp")).read()).group(1)
        self.front_bin = a.ribbit_front or os.path.join(HERE, "lisp-boundary")
    def ram(self):
        """The RAM host the front uses: the neutral one given with --ram-external, else one started here."""
        if self.a.ram_external: h, _, p = self.a.ram_external.rpartition(":"); return h, int(p)
        return "127.0.0.1", self.a.ram_port
    def start(self):
        self.stop_procs()
        if not self.a.ram_external:
            self.srv = subprocess.Popen([self.a.ram_server, "--listen", "127.0.0.1:%d" % self.a.ram_port], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            time.sleep(0.5)
        self.errlog = open(os.path.join(self.a.out, "ribbit-front.log"), "a")
        rh, rp = self.ram()
        self.front = subprocess.Popen([self.front_bin, "--ram", rh, str(rp), "--udp", str(self.a.ribbit_udp)],
                                      stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.errlog, text=True)
        for kind, o in NAMED.items():
            a = {"kind": kind, "name": o["name"]}
            if kind == "geo": a["geo_tag"] = o["geo_tag"]
            elif kind == "json": a["json_string"] = o["json_string"]
            else: a["nodes"] = o["nodes"]
            self.cfg("ms.named_locator", **a)
        for p in POLICIES: self.cfg("ms.policy", **policy_args(p))
        for s in SITES:
            self.cfg("site.add", **ribbit_site_args(s))
            self.cfg("resolver.wait_site", iid=s["iid"], prefix=s["prefix"], group=s.get("group", ""), active=True)
        self.cfg("ms.encryption_key", key_id=ENC_KEY_ID, key=ENC_KEY)
        c = Client(self.a.local_ip, self.target); t0 = time.time()
        while time.time() - t0 < 30 and not still_serving(c): time.sleep(0.5)
    def delete_site(self, site): self.cfg("site.delete", iid=site["iid"], prefix=site["prefix"], group=""); return "good"
    def add_site(self, site):
        self.cfg("site.add", **ribbit_site_args(site))
        return "good"
    def restart_keep_state(self): self.restart_front(); return {}
    def lose_ram_host(self):
        if self.a.ram_external: return None       # the neutral RAM host is not this run's to kill (L4.11 n/a)
        listeners = subprocess.run(["ss", "-ltnpH", "sport = :%d" % self.a.ram_port], capture_output=True, text=True).stdout.strip()
        if ("pid=%d," % self.srv.pid) not in listeners or listeners.count("\n") != 0:
            return {"killed": False, "why": "the RAM host on port %d is not the one this run started (pid %d): %s" % (self.a.ram_port, self.srv.pid, listeners)}
        self.srv.kill(); self.srv.wait()
        gone = subprocess.run(["ss", "-ltnpH", "sport = :%d" % self.a.ram_port], capture_output=True, text=True).stdout.strip()
        return {"killed": True, "gone": gone}
    def restart_front(self):
        if self.front and self.front.poll() is None: self.front.terminate(); self.front.wait()
        rh, rp = self.ram()
        self.front = subprocess.Popen([self.front_bin, "--ram", rh, str(rp), "--udp", str(self.a.ribbit_udp)],
                                      stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.errlog, text=True)
        for s in SITES: self.cfg("site.add", **ribbit_site_args(s))
        self.cfg("ms.encryption_key", key_id=ENC_KEY_ID, key=ENC_KEY)
        time.sleep(0.5)
    def cfg(self, op, **args):
        self.front.stdin.write(json.dumps({"operation": op, "args": args}) + "\n"); self.front.stdin.flush()
        r = json.loads(self.front.stdout.readline())
        if not r.get("ok"): raise RuntimeError("ribbit %s: %s" % (op, r.get("error")))
    def stop_procs(self):
        for p in (self.front, self.srv):          # SIGTERM first: a coverage build writes its data on it
            if p and p.poll() is None:
                p.terminate()
                try: p.wait(timeout=5)
                except subprocess.TimeoutExpired: p.kill(); p.wait()
    def stop(self): self.stop_procs()
    def evidence(self):
        try: return open(os.path.join(self.a.out, "ribbit-front.log")).read()[-1500:]
        except OSError: return ""

# ------------------------------------------------------------------------------------------------ the tests
TESTS = []
def test(tid, title, klass=None):
    def deco(f): TESTS.append((tid, title, klass, f)); return f
    return deco

def fresh(c, prefix, mask=24, rlocs=None, ttl=ttl_minutes(3)):
    d, sec = c.reg([(prefix, mask, ttl, rlocs or [c.local_ip])]); return d, sec

@test("P4", "liveness: a Map-Request outside every site is answered negatively")
def t_p4(c, ctx):
    r, sec = c.ask("203.0.113.9")
    return (r is not None and r["records"] and r["records"][0]["locs"] == []), "reply %s" % r, [sec] if r else []

@test("L1.1", "authenticated Map-Register (M set) -> Map-Notify: same nonce, HMAC valid with the site key")
def t_l11(c, ctx):
    d, sec = fresh(c, "198.18.11.0")
    if d is None: return False, "no Map-Notify in 2 s", []
    return (d[0] >> 4 == 4 and notify_valid(d)), "type %d, %d B, HMAC %s" % (d[0] >> 4, len(d), "valid" if notify_valid(d) else "INVALID"), [sec]

@test("L1.2", "Map-Register with M clear: no Map-Notify, and the registration resolves")
def t_l12(c, ctx):
    n = c.next(); c.send(register([("198.18.12.0", 24, ttl_minutes(3), [c.local_ip])], n, want_notify=False))
    d, _ = c.wait_for(n, 1.0)
    time.sleep(0.3); r, sec = c.ask("198.18.12.9")
    ok = d is None and r and r["records"][0]["locs"] == [c.local_ip]
    return ok, "notify %s; reply %s" % ("none" if d is None else "type %d" % (d[0] >> 4), r), [sec] if r else []

@test("L1.3", "ECM Map-Request for a registered EID: exact EID-prefix, mask and locator set")
def t_l13(c, ctx):
    fresh(c, "198.18.13.0", rlocs=[c.local_ip, "198.51.100.13"]); time.sleep(0.2)
    r, sec = c.ask("198.18.13.9")
    rec = r["records"][0] if r and r["records"] else {}
    ok = rec.get("eid") == "198.18.13.0" and rec.get("mask") == 24 and rec.get("locs") == sorted([c.local_ip, "198.51.100.13"])
    return ok, "record %s" % rec, [sec] if r else []

@test("L1.5", "ECM Map-Request inside an accept-more-specifics site, nothing registered: negative for the EID itself (/32), TTL 1, natively-forward")
def t_l15(c, ctx):
    # lispers.net: a site that accepts more-specifics answers for the requested EID; one that does not, with its own
    # prefix (lisp_ms_process_map_request). Until the hardware run this test accepted any mask -- and both answers.
    r, sec = c.ask("198.18.250.9"); rec = r["records"][0] if r and r["records"] else {}
    ok = rec.get("locs") == [] and rec.get("eid") == "198.18.250.9" and rec.get("mask") == 32 and rec.get("ttl") == 1 and rec.get("action") == 1
    return ok, "record %s" % rec, [sec] if r else []

@test("L1.5b", "ECM Map-Request inside a site that does NOT accept more-specifics, nothing registered: negative with the site's prefix")
def t_l15b(c, ctx):
    r, sec = c.ask("198.19.0.77"); rec = r["records"][0] if r and r["records"] else {}
    ok = rec.get("locs") == [] and rec.get("eid") == "198.19.0.0" and rec.get("mask") == 24 and rec.get("ttl") == 1
    return ok, "record %s" % rec, [sec] if r else []

@test("L1.6", "ECM Map-Request outside every site: negative, natively-forward")
def t_l16(c, ctx):
    r, sec = c.ask("203.0.113.77"); rec = r["records"][0] if r and r["records"] else {}
    return (rec.get("locs") == [] and rec.get("action") == 1), "record %s" % rec, [sec] if r else []

@test("L1.7", "a bare (non-ECM) Map-Request to the map-resolver port", "OBSERVATION")
def t_l17(c, ctx):
    n = c.next(); c.send(map_request("198.18.13.9", n, c.local_ip)); d, sec = c.wait_for(n, 1.0)
    return True, "answered type %d" % (d[0] >> 4) if d else "not answered", [sec] if d else []

@test("L2.1", "refresh (same locator set) keeps resolving")
def t_l21(c, ctx):
    fresh(c, "198.18.21.0"); d, sec = fresh(c, "198.18.21.0"); time.sleep(0.2); r, _ = c.ask("198.18.21.9")
    return (d is not None and r and r["records"][0]["locs"] == [c.local_ip]), "notify %s, reply %s" % (d is not None, r), [sec] if d else []

@test("L2.2", "change of locator set: the new set resolves, the old never again")
def t_l22(c, ctx):
    fresh(c, "198.18.22.0", rlocs=[c.local_ip]); d, sec = fresh(c, "198.18.22.0", rlocs=[c.local_ip, "198.51.100.22"]); time.sleep(0.2)
    r, _ = c.ask("198.18.22.9"); got = r["records"][0]["locs"] if r and r["records"] else None
    return got == sorted([c.local_ip, "198.51.100.22"]), "resolves to %s" % got, [sec] if d else []

@test("L2.3", "withdraw (TTL 0) from a registered RLOC: Map-Notify, then negative")
def t_l23(c, ctx):
    fresh(c, "198.18.23.0"); d, sec = c.reg([("198.18.23.0", 24, 0, [])]); time.sleep(0.3); r, _ = c.ask("198.18.23.9")
    got = r["records"][0]["locs"] if r and r["records"] else None
    return (d is not None and got == []), "notify %s, resolves to %s" % (d is not None, got), [sec] if d else []

@test("L2.4", "withdraw from a source that is not a registered RLOC is ignored")
def t_l24(c, ctx):
    fresh(c, "198.18.24.0", rlocs=["198.51.100.24"])            # registered RLOC is NOT this client's address
    c.reg([("198.18.24.0", 24, 0, [])]); time.sleep(0.3); r, sec = c.ask("198.18.24.9")
    got = r["records"][0]["locs"] if r and r["records"] else None
    return got == ["198.51.100.24"], "resolves to %s" % got, [sec] if r else []

@test("L2.5", "TTL expiry without refresh (3-second TTL): resolves before; how long until it stops resolving")
def t_l25(c, ctx):
    # lispers.net expires registrations on a sweep every LISP_SITE_TIMEOUT_CHECK_INTERVAL (60 s, lisp.py); Ribbit's
    # registrations carry their expiry and stop resolving at it (no reaper). Both must stop within TTL + 61 s; the
    # delay is the measurement.
    print("  .. waiting for the registration to expire (lispers.net sweeps once a minute: up to 64 s)", flush=True)
    fresh(c, "198.18.25.0", ttl=ttl_seconds(3)); t0 = time.time(); time.sleep(0.2)
    r1, _ = c.ask("198.18.25.9"); a = r1["records"][0]["locs"] if r1 and r1["records"] else None
    gone = None
    while time.time() - t0 < 64:
        r2, _ = c.ask("198.18.25.9")
        if r2 and r2["records"] and r2["records"][0]["locs"] == []: gone = time.time() - t0; break
        time.sleep(0.5)
    return (a == [c.local_ip] and gone is not None), "resolved before: %s; stopped resolving %s" % (a, "after %.1f s (TTL 3 s)" % gone if gone else "NOT within 64 s"), []

@test("L2.6", "a more-specific inside an accept-more-specifics site is accepted")
def t_l26(c, ctx):
    d, sec = fresh(c, "198.18.26.128", mask=25); return d is not None, "notify %s" % (d is not None), [sec] if d else []

@test("L2.7", "a more-specific without accept-more-specifics is rejected, no state")
def t_l27(c, ctx):
    d, _ = fresh(c, "198.19.0.128", mask=25); r, sec = c.ask("198.19.0.130"); got = r["records"][0]["locs"] if r and r["records"] else None
    return (d is None and got == []), "notify %s, resolves to %s" % (d is not None, got), [sec] if r else []

@test("L2.8", "a prefix outside every site is rejected, no state")
def t_l28(c, ctx):
    d, _ = fresh(c, "203.0.113.0"); r, sec = c.ask("203.0.113.9"); got = r["records"][0]["locs"] if r and r["records"] else None
    return (d is None and got == []), "notify %s, resolves to %s" % (d is not None, got), [sec] if r else []

@test("L2.10", "two-record register: both applied, one Map-Notify")
def t_l210(c, ctx):
    d, sec = c.reg([("198.18.31.0", 24, 3, [c.local_ip]), ("198.18.32.0", 24, 3, [c.local_ip])]); time.sleep(0.3)
    r1, _ = c.ask("198.18.31.9"); r2, _ = c.ask("198.18.32.9")
    ok = d is not None and all(r and r["records"][0]["locs"] == [c.local_ip] for r in (r1, r2))
    return ok, "notify %s" % (d is not None), [sec] if d else []

@test("L2.11", "longest-prefix match across nested registrations")
def t_l211(c, ctx):
    fresh(c, "198.18.40.0", mask=22, rlocs=["198.51.100.40"]); fresh(c, "198.18.41.0", mask=24, rlocs=["198.51.100.41"]); time.sleep(0.3)
    r, sec = c.ask("198.18.41.9"); got = r["records"][0]["locs"] if r and r["records"] else None
    return got == ["198.51.100.41"], "resolves to %s" % got, [sec] if r else []

@test("L3.1", "HMAC-SHA-256-128 with the correct key is accepted")
def t_l31(c, ctx):
    d, sec = c.reg([("198.18.51.0", 24, 3, [c.local_ip])], alg=2); return d is not None, "notify %s" % (d is not None), [sec] if d else []

@test("L3.2", "HMAC-SHA-1-96 with the correct key is accepted")
def t_l32(c, ctx):
    d, sec = c.reg([("198.18.52.0", 24, 3, [c.local_ip])], alg=1); return d is not None, "notify %s" % (d is not None), [sec] if d else []

def rejected(c, tid_prefix, records, **kw):
    d, _ = c.reg(records, timeout=1.0, **kw); time.sleep(0.2)
    r, _ = c.ask(records[0][0][:-1] + "9"); got = r["records"][0]["locs"] if r and r["records"] else None
    return (d is None and got == []), "notify %s, resolves to %s" % (d is not None, got), []

@test("L3.3", "wrong key: rejected, no Map-Notify, no state")
def t_l33(c, ctx): return rejected(c, "L3.3", [("198.18.53.0", 24, 3, [c.local_ip])], password="wrong")
@test("L3.4", "wrong key-id: rejected")
def t_l34(c, ctx): return rejected(c, "L3.4", [("198.18.54.0", 24, 3, [c.local_ip])], key_id=9)
@test("L3.5", "no authentication on a keyed site: rejected")
def t_l35(c, ctx): return rejected(c, "L3.5", [("198.18.55.0", 24, 3, [c.local_ip])], alg=0)

@test("L3.6", "one bit flipped in the authentication data / in the body: rejected")
def t_l36(c, ctx):
    ok, ev = True, []
    for where, pfx in ((20, "198.18.56.0"), (50, "198.18.57.0")):
        n = c.next(); m = bytearray(register([(pfx, 24, 3, [c.local_ip])], n)); m[where] ^= 0x01; c.send(bytes(m))
        d, _ = c.wait_for(n, 1.0); time.sleep(0.2); r, _ = c.ask(pfx[:-1] + "9"); got = r["records"][0]["locs"] if r and r["records"] else None
        ok &= (d is None and got == []); ev.append("byte %d: notify %s, resolves %s" % (where, d is not None, got))
    return ok, "; ".join(ev), []

@test("L3.7", "two-record register with the second record unauthorized: nothing applied", "KNOWN DIFFERENCE")
def t_l37(c, ctx):
    d, _ = c.reg([("198.18.58.0", 24, 3, [c.local_ip]), ("203.0.113.0", 24, 3, [c.local_ip])], timeout=1.0); time.sleep(0.3)
    r, _ = c.ask("198.18.58.9"); got = r["records"][0]["locs"] if r and r["records"] else None
    return (d is None and got == []), "notify %s, first record resolves to %s" % (d is not None, got), []

def still_serving(c):
    d, _ = c.reg([("198.18.99.0", 24, 3, [c.local_ip])], timeout=2.0)
    return d is not None

@test("L4.1", "truncation: a valid Map-Register cut at every byte offset; after each, a valid register still works")
def t_l41(c, ctx):
    whole = register([("198.18.60.0", 24, 3, [c.local_ip])], c.next()); bad = []
    for cut in range(1, len(whole)):
        c.send(whole[:cut])
        if cut % 8 == 0 and not still_serving(c): bad.append(cut); break
    ok = not bad and still_serving(c)
    return ok, "%d truncations; %s" % (len(whole) - 1, "stopped serving after cut %s" % bad if bad else "still serving"), []

@test("L4.2", "wrong type values (0, 9-15) and an ECM whose inner packet is not a Map-Request; still serving after")
def t_l42(c, ctx):
    for t in (0, 9, 10, 11, 12, 13, 14, 15): c.send(bytes([t << 4]) + bytes(23))
    c.send(ecm(b"\x30" + bytes(20), c.local_ip, "198.18.1.1", c.s.getsockname()[1]))
    ok = still_serving(c); return ok, "still serving" if ok else "STOPPED SERVING", []

@test("L4.3", "record count larger than the records present, and locator count mismatch; still serving after")
def t_l43(c, ctx):
    c.send(register([("198.18.61.0", 24, 3, [c.local_ip])], c.next(), count=5))
    m = bytearray(register([("198.18.62.0", 24, 3, [c.local_ip])], c.next())); m[16 + 32 + 4] = 7; c.send(bytes(m))
    ok = still_serving(c); return ok, "still serving" if ok else "STOPPED SERVING", []

@test("L4.4", "unknown AFI and mask > 32; still serving after")
def t_l44(c, ctx):
    m = bytearray(register([("198.18.63.0", 24, 3, [c.local_ip])], c.next())); m[16 + 32 + 10:16 + 32 + 12] = b"\x00\x63"; c.send(bytes(m))
    c.send(register([("198.18.64.0", 40, 3, [c.local_ip])], c.next()))
    ok = still_serving(c); return ok, "still serving" if ok else "STOPPED SERVING", []

@test("L4.5", "authentication length disagreeing with the algorithm; still serving after")
def t_l45(c, ctx):
    m = bytearray(register([("198.18.65.0", 24, 3, [c.local_ip])], c.next())); m[14:16] = struct.pack("!H", 7); c.send(bytes(m))
    ok = still_serving(c); return ok, "still serving" if ok else "STOPPED SERVING", []

@test("L4.8", "an oversize datagram (8 KiB of garbage after a valid header); still serving after")
def t_l48(c, ctx):
    c.send(register([("198.18.66.0", 24, 3, [c.local_ip])], c.next()) + bytes(8192))
    ok = still_serving(c); return ok, "still serving" if ok else "STOPPED SERVING", []

@test("L4.9", "a flood of 500 malformed packets interleaved with valid registers: every valid one answered")
def t_l49(c, ctx):
    missed, lat = 0, []
    for k in range(50):
        for j in range(10): c.send(bytes([(3 << 4)]) + os.urandom(20 + j))
        d, sec = c.reg([("198.18.%d.0" % (100 + k), 24, 3, [c.local_ip])], timeout=2.0)
        if d is None: missed += 1
        else: lat.append(sec)
    return missed == 0, "%d of 50 valid registers unanswered" % missed, lat

def locs_of(r): return r["records"][0]["locs"] if r and r["records"] else None

@test("L1.8", "IPv6 EIDs: register -> Map-Notify (HMAC valid); ECM (inner IPv6) request -> exact record; unregistered -> negative")
def t_l18(c, sysm):
    d, sec = c.reg([("2001:db8:acce:18::", 64, 3, [c.local_ip])]); time.sleep(0.3)
    r, _ = c.ask("2001:db8:acce:18::9"); rec = r["records"][0] if r and r["records"] else {}
    rn, _ = c.ask("2001:db8:acce:99::9")
    ok = d is not None and notify_valid(d) and rec.get("eid") == "2001:db8:acce:18::" and rec.get("mask") == 64 and rec.get("locs") == [c.local_ip] and locs_of(rn) == []
    return ok, "notify %s; record %s; unregistered %s" % ("valid" if d is not None and notify_valid(d) else "none/invalid", rec, locs_of(rn)), [sec] if d else []

@test("L2.9", "merge semantics: two xTRs register one prefix with merge -> the union; one replacing its own changes only its own")
def t_l29(c, sysm):
    A, B = 0xA0A0A0A0A0A0A0A0A0A0A0A0A0A0A0A0, 0xB0B0B0B0B0B0B0B0B0B0B0B0B0B0B0B0
    da, sec = c.reg([("198.18.130.0", 24, 3, ["198.51.100.130"])], xtr_id=A, merge=True)
    db, _ = c.reg([("198.18.130.0", 24, 3, ["198.51.100.131"])], xtr_id=B, merge=True); time.sleep(0.3)
    u = locs_of(c.ask("198.18.130.9")[0])
    c.reg([("198.18.130.0", 24, 3, ["198.51.100.132"])], xtr_id=A, merge=True); time.sleep(0.3)
    v = locs_of(c.ask("198.18.130.9")[0])
    # the SEMANTICS decide; whether the sender gets the Map-Notify is recorded (lispers.net notifies merged
    # registrations elsewhere, not to the sender -- see the evidence)
    ok = u == ["198.51.100.130", "198.51.100.131"] and v == ["198.51.100.131", "198.51.100.132"]
    return ok, "notify A %s B %s; union %s; after A replaced its own %s" % (da is not None, db is not None, u, v), [sec] if da else []

@test("L2.13", "site deleted while registered, then restored: a new register is accepted and resolves (what resolves in between is recorded)")
def t_l213(c, sysm):
    site = [x for x in SITES if x["name"] == "shut"][0]
    d1, sec = c.reg([("198.21.1.0", 24, 3, [c.local_ip])]); time.sleep(0.3); before = locs_of(c.ask("198.21.1.9")[0])
    r1 = sysm.delete_site(site); time.sleep(1.5); during = locs_of(c.ask("198.21.1.9")[0])
    r2 = sysm.add_site(site); time.sleep(1.5)
    d2, _ = c.reg([("198.21.1.0", 24, 3, [c.local_ip])]); time.sleep(0.3); after = locs_of(c.ask("198.21.1.9")[0])
    # Neither implementation withdraws existing registrations when their site is deleted (both keep answering until
    # the registration expires); no normative requirement is asserted for the in-between state -- it is recorded.
    ok = d1 is not None and before == [c.local_ip] and r1 == "good" and r2 == "good" and d2 is not None and after == [c.local_ip]
    return ok, "registered %s -> %s; site deleted (%s) -> %s; restored (%s), re-registered %s -> %s" % (d1 is not None, before, r1, during, r2, d2 is not None, after), [sec] if d1 else []

@test("L2.12", "instance-id isolation: the same prefix registered in iid 0 and iid 7, each resolves only its own")
def t_l212(c, sysm):
    d7, sec = c.reg([("198.18.120.0", 24, 3, ["198.51.100.7"])], iid=7)
    d0, _ = c.reg([("198.18.120.0", 24, 3, [c.local_ip])]); time.sleep(0.3)
    r7, _ = c.ask("198.18.120.9", iid=7); r0, _ = c.ask("198.18.120.9")
    rec7 = r7["records"][0] if r7 and r7["records"] else {}
    ok = d7 is not None and d0 is not None and rec7.get("locs") == ["198.51.100.7"] and rec7.get("iid") == 7 and locs_of(r0) == [c.local_ip]
    return ok, "notify iid7 %s, iid0 %s; iid 7 -> %s (record iid %s); iid 0 -> %s" % (d7 is not None, d0 is not None, rec7.get("locs"), rec7.get("iid"), locs_of(r0)), [sec] if d7 else []

def info_request(nonce, hostname=None):
    p = struct.pack("!IQIII", 7 << 28, nonce, 0, 0, 0)
    return p + (struct.pack("!H", 17) + hostname.encode() + b"\0" if hostname else struct.pack("!H", 0))

def parse_info_reply(d):
    if len(d) < 24 or d[0] >> 4 != 7 or not (d[0] & 0x08): raise ValueError("not an Info-Reply")
    o = 24
    afi, _r, typ, _r2, ln, ms_port, etr_port, gafi = struct.unpack("!HHBBHHHH", d[o:o + 14]); o += 14
    if afi != 16387 or typ != 7: raise ValueError("not a NAT-traversal LCAF (AFI %d type %d)" % (afi, typ))
    g = socket.inet_ntoa(d[o:o + 4]) if gafi == 1 else None; o += 4 if gafi == 1 else 0
    _z, pafi = struct.unpack("!HH", d[o:o + 4]); o += 4
    private = None
    if pafi == 17: e = d.index(b"\0", o); private = d[o:e].decode(); o = e + 1
    return {"nonce": d[4:12], "ms_port": ms_port, "etr_port": etr_port, "global": g, "private": private}

@test("L1.11", "Info-Request (NAT traversal) -> Info-Reply: same nonce, the global ETR RLOC and port it came from, the private name")
def t_l111(c, sysm):
    n = c.next(); c.send(info_request(n, "acceptance-etr")); d, sec = c.wait_for(n, 2.0)
    if d is None: return False, "no Info-Reply", []
    try: r = parse_info_reply(d)
    except ValueError as x: return False, "reply: %s (%d B)" % (x, len(d)), []
    ok = r["global"] == c.local_ip and r["etr_port"] == c.s.getsockname()[1] and r["private"] == "acceptance-etr"
    return ok, "%s" % {k: v for k, v in r.items() if k != "nonce"}, [sec]

@test("L1.12", "Map-Notify-Ack (authenticated, for a registered prefix) is consumed: no answer, still serving")
def t_l112(c, sysm):
    fresh(c, "198.18.140.0")
    n = c.next(); m = bytearray(register([("198.18.140.0", 24, 3, [c.local_ip])], n, want_notify=False))
    m[0] = (5 << 4) | (m[0] & 0x0f); m[16:48] = bytes(32); m[16:48] = hmac.new(PASSWORD.encode(), bytes(m), hashlib.sha256).digest()
    c.send(bytes(m)); d, _ = c.wait_for(n, 1.0)
    ok = d is None and still_serving(c)
    return ok, "answer %s; still serving %s" % ("none" if d is None else "type %d" % (d[0] >> 4), ok), []

def parse_referral(d):
    """Map-Referral (type 6) -> dict(nonce, records=[{ttl, action, auth, incomplete, eid, mask, rlocs}])."""
    if len(d) < 12 or d[0] >> 4 != 6: raise ValueError("not a Map-Referral (type %d)" % (d[0] >> 4))
    out = {"nonce": d[4:12], "records": []}; o = 12
    for _ in range(d[3]):
        ttl, nloc, mask, flags, _v, afi = struct.unpack("!IBBHHH", d[o:o + 12]); o += 12
        n = 4 if afi == 1 else 16 if afi == 2 else 0
        eid = socket.inet_ntoa(d[o:o + 4]) if afi == 1 else socket.inet_ntop(socket.AF_INET6, d[o:o + 16]) if afi == 2 else None; o += n
        rl = []
        for _ in range(nloc):
            _p, _w, _mp, _mw, _fl, lafi = struct.unpack("!BBBBHH", d[o:o + 8]); o += 8
            rl.append(socket.inet_ntoa(d[o:o + 4]) if lafi == 1 else None); o += 4 if lafi == 1 else 16
        out["records"].append({"ttl": ttl, "action": flags >> 13, "auth": bool(flags & 0x1000), "incomplete": bool(flags & 0x0800),
                               "eid": eid, "mask": mask, "rlocs": rl})
    return out

def ddt_ask(c, eid):
    """A DDT-originated Map-Request: an ECM with the D bit (RFC 8111), as a DDT map-resolver sends to a map-server."""
    # a map-server answers a DDT-originated request with a Map-Reply to the ITR AND a Map-Referral to the ECM's source
    # (lispers.net lisp_process_map_request -> lisp_ms_process_map_request, then lisp_ms_send_map_referral): collect
    # everything with this nonce for up to 2 s and take the Map-Referral
    n = c.next(); m = bytearray(ecm(map_request(eid, n, c.local_ip), c.local_ip, eid, c.s.getsockname()[1])); m[0] |= 0x04
    t0 = time.perf_counter(); c.send(bytes(m)); want = struct.pack("!Q", n); seen = []
    while time.perf_counter() - t0 < 2.0:
        c.s.settimeout(max(0.01, 2.0 - (time.perf_counter() - t0)))
        try: d, _ = c.s.recvfrom(65535)
        except socket.timeout: break
        if len(d) >= 12 and d[4:12] == want:
            seen.append(d[0] >> 4)
            if d[0] >> 4 == 6: return parse_referral(d), time.perf_counter() - t0, d
    if seen: raise ValueError("no Map-Referral; answered with type(s) %s" % seen)
    return None, None, None

@test("L1.13", "DDT-originated Map-Request to the map-server -> Map-Referral: MS-ACK for a registered EID, MS-NOT-REGISTERED inside a site, and outside every site", "OBSERVATION")
def t_l113(c, sysm):
    fresh(c, "198.18.141.0"); time.sleep(0.3); out = []
    for eid in ("198.18.141.9", "198.18.251.9", "203.0.113.141"):
        try: r, sec, d = ddt_ask(c, eid)
        except ValueError as x: out.append("%s: %s" % (eid, x)); continue
        rec = r["records"][0] if r and r["records"] else None
        out.append("%s: %s" % (eid, "no answer" if r is None else rec))
    return True, "; ".join(out), []

# LCAF locator records produced by lispers.net's OWN encoder (lisp.py lisp_rloc_record.encode, from its source tree;
# tools/coverage/lcaf_vectors_from_lispers.py regenerates them): an AFI-list LCAF carrying 192.0.2.2 and, inside it,
# a geo-coordinates (type 5), explicit-locator-path (10), replication-list-entry (13) or JSON (14) LCAF. The locator is
# 198.51.100.9, local to neither system (a local one comes back with the L bit set).
LCAF_RLOCS = {"geo": "01640000000140030000010000240001c63364094003000005000016400000002d1b9e507a24ed2000000000000000000000", "elp": "016400000001400300000100001e0001c6336409400300000a00001000010001c633640100000001c6336402", "rle": "01640000000140030000010000220001c6336409400300000d000014000000000001c6336403000000010001c6336404", "json": "016400000001400300000100002d0001c6336409400300000e000021001b7b2273697465223a22616363657074616e6365222c226e223a317d0000"}

def reply_rloc_bytes(d):
    """The raw bytes of the one RLOC record in a one-record, one-locator Map-Reply."""
    if len(d) < 24 or d[0] >> 4 != 2 or d[3] != 1 or d[12 + 4] != 1: raise ValueError("not a one-record one-locator Map-Reply")
    afi = struct.unpack("!H", d[12 + 10:12 + 12])[0]
    return d[12 + 12 + (4 if afi == 1 else 16):]

for _kind in ("geo", "elp", "rle", "json"):
    def _mk(kind=_kind):
        def f(c, sysm):
            pfx = "198.18.%d.0" % (150 + ("geo", "elp", "rle", "json").index(kind) + 40)
            rec = bytes.fromhex(LCAF_RLOCS[kind])
            d, sec = c.reg([(pfx, 24, 3, [rec])]); time.sleep(0.3)
            n = c.next(); c.send(ecm(map_request(pfx[:-1] + "9", n, c.local_ip), c.local_ip, pfx[:-1] + "9", c.s.getsockname()[1]))
            r, _ = c.wait_for(n, 2.0)
            try: got = reply_rloc_bytes(r) if r else None
            except ValueError as x: got = None; why = str(x)
            ok = d is not None and got == rec
            return ok, "notify %s; locator returned %s" % (d is not None, "byte-identical to lispers.net's encoding" if ok else ("none" if got is None else got.hex())), [sec] if d else []
        return f
    test("L2.14.%s" % _kind, "an LCAF %s locator (encoded by lispers.net's own encoder) is registered and returned byte for byte" % _kind)(_mk())

def encrypted(msg, key_id=ENC_KEY_ID, key=ENC_KEY):
    """An encrypted Map-Register as lispers.net's ETR sends it: E bit and key-id in the first long, authentication
    already computed over the plaintext, then everything after the first 4 bytes ChaCha20 (Bernstein, 20 rounds),
    key = the key string left-padded with '0' to 32, nonce = eight ASCII '0's."""
    from Crypto.Cipher import ChaCha20
    first = struct.unpack("!I", msg[:4])[0] | 0x2000 | (key_id << 14)
    plain = struct.pack("!I", first) + msg[4:]
    # the E bit and key-id are part of what was authenticated: recompute the HMAC over the plaintext with them set
    p = bytearray(plain); p[16:48] = bytes(32); p[16:48] = hmac.new(PASSWORD.encode(), bytes(p), hashlib.sha256).digest()
    return bytes(p[:4]) + ChaCha20.new(key=key.zfill(32).encode(), nonce=b"00000000").encrypt(bytes(p[4:]))

@test("L3.12", "encrypted Map-Registers (lisp encryption-keys): the right key -> accepted and resolves; a wrong key or an unknown key-id -> rejected; still serving")
def t_l312(c, sysm):
    out, ok = [], True
    for case, kid, key, pfx, want in (("right key", ENC_KEY_ID, ENC_KEY, "198.18.230.0", True),
                                      ("wrong key", ENC_KEY_ID, "not-the-key", "198.18.231.0", False),
                                      ("unknown key-id", 5, ENC_KEY, "198.18.232.0", False)):
        n = c.next(); c.send(encrypted(register([(pfx, 24, 3, [c.local_ip])], n), kid, key)); d, _ = c.wait_for(n, 1.5); time.sleep(0.3)
        got = locs_of(c.ask(pfx[:-1] + "9")[0])
        good = (d is not None and got == [c.local_ip]) if want else (d is None and got == [])
        ok &= good; out.append("%s: notify %s, resolves %s" % (case, d is not None, got))
    ok &= still_serving(c)
    return ok, "; ".join(out), []

def raw_ask(c, eid, pitr=False):
    n = c.next(); mr = bytearray(map_request(eid, n, c.local_ip))
    if pitr: mr[1] |= 0x80                        # the Map-Request's P (PITR) bit: 0x00800000 of the first long
    c.send(ecm(bytes(mr), c.local_ip, eid, c.s.getsockname()[1])); return c.wait_for(n, 2.0)

@test("L2.15.shutdown", "site shutdown = yes: a register is rejected, nothing resolves")
def t_opt_shut(c, sysm):
    d, _ = fresh(c, "198.22.1.0"); time.sleep(0.3); got = locs_of(c.ask("198.22.1.9")[0])
    # rejected means not applied AND not acknowledged (the rule of L3.3 / L3.6); the hardware run showed lispers.net
    # Map-Notifying this rejected register -- the test used to look only at what resolves
    return got == [] and d is None, "notify %s; resolves %s" % (d is not None, got), []

@test("L2.15.allowed-rloc", "allowed-rloc: a register with an allowed RLOC is accepted, one with another RLOC is not")
def t_opt_rloc(c, sysm):
    d1, _ = fresh(c, "198.23.1.0", rlocs=[c.local_ip]); d2, _ = fresh(c, "198.23.2.0", rlocs=["198.51.100.23"]); time.sleep(0.3)
    g1 = locs_of(c.ask("198.23.1.9")[0]); g2 = locs_of(c.ask("198.23.2.9")[0])
    return (g1 == [c.local_ip] and g2 == [] and d1 is not None and d2 is None), "allowed: notify %s resolves %s; other: notify %s resolves %s" % (d1 is not None, g1, d2 is not None, g2), []

@test("L2.15.force-ttl", "force-ttl 30: the Map-Reply's record TTL is 30 seconds (seconds encoding), registered and unregistered")
def t_opt_ttl(c, sysm):
    fresh(c, "198.24.1.0"); time.sleep(0.3)
    r1, _ = c.ask("198.24.1.9"); r2, _ = c.ask("198.24.99.9")
    t1 = r1["records"][0]["ttl"] if r1 and r1["records"] else None; t2 = r2["records"][0]["ttl"] if r2 and r2["records"] else None
    want = 0x80000000 | 30
    return (t1 == want and t2 == want), "registered TTL %s, unregistered TTL %s (want %s)" % (hex(t1) if t1 is not None else t1, hex(t2) if t2 is not None else t2, hex(want)), []

@test("L2.15.not-registered-yet", "proxy-reply-action not-registered-yet: an unregistered EID gets a negative reply for the EID itself, action 7, TTL 1")
def t_opt_nry(c, sysm):
    r, _ = c.ask("198.25.99.9"); rec = r["records"][0] if r and r["records"] else {}
    return (rec.get("action") == 7 and rec.get("ttl") == 1 and rec.get("eid") == "198.25.99.9" and rec.get("mask") == 32 and rec.get("locs") == []), "record %s" % rec, []

@test("L2.15.echo-nonce", "echo-nonce-capable: the Map-Reply has the E bit")
def t_opt_echo(c, sysm):
    fresh(c, "198.26.1.0"); time.sleep(0.3); d, _ = raw_ask(c, "198.26.1.9")
    e = bool(d and (struct.unpack("!I", d[:4])[0] & 0x04000000))
    return e, "E bit %s" % e, []

@test("L2.15.drop", "force-proxy-reply no, proxy-reply-action drop: a registered EID is answered with action drop, TTL 1440, its locators")
def t_opt_drop(c, sysm):
    fresh(c, "198.27.1.0"); time.sleep(0.3); r, _ = c.ask("198.27.1.9"); rec = r["records"][0] if r and r["records"] else {}
    return (rec.get("action") == 3 and rec.get("ttl") == 1440 and rec.get("locs") == [c.local_ip]), "record %s" % rec, []

@test("L2.15.pitr-drop", "pitr-proxy-reply-drop: a PITR's Map-Request for a registered EID is answered with action drop, TTL 1440, its locators")
def t_opt_pitr(c, sysm):
    fresh(c, "198.28.1.0"); time.sleep(0.3); d, _ = raw_ask(c, "198.28.1.9", pitr=True)
    try: rec = parse_reply(d)["records"][0] if d else {}
    except ValueError as x: rec = {"error": str(x)}
    return (rec.get("action") == 3 and rec.get("ttl") == 1440 and rec.get("locs") == [c.local_ip]), "record %s" % rec, []

def eid_sg(src, sml, grp, gml, iid=0):
    """An (S,G) EID: LCAF type 9 (multicast info) as lispers.net's lcaf_encode_sg writes it."""
    body = struct.pack("!IHBB", iid, 0, sml, gml) + struct.pack("!H", 1) + ip4(src) + struct.pack("!H", 1) + ip4(grp)
    return struct.pack("!HBBBBH", 16387, 0, 0, 9, 0, len(body)) + body

def register_sg(src, sml, grp, gml, rlocs, nonce, ttl=3):
    first = (3 << 28) | 0x800 | 0x100 | 1
    p = bytearray(struct.pack("!IQBBH", first, nonce, KEY_ID, 2, 32) + bytes(32))
    p += struct.pack("!IBBHH", ttl, len(rlocs), sml, 0, 0) + eid_sg(src, sml, grp, gml)
    for r in rlocs: p += struct.pack("!BBBBHH", 1, 100, 0, 0, 0x5, 1) + ip4(r)
    p[16:48] = hmac.new(PASSWORD.encode(), bytes(p), hashlib.sha256).digest()
    return bytes(p)

def request_sg(src, grp, nonce, itr_rloc):
    return (struct.pack("!IQH", (1 << 28) | 1, nonce, 0) + struct.pack("!H", 1) + ip4(itr_rloc)
            + struct.pack("!BB", 0, 32) + eid_sg(src, 32, grp, 32))

@test("L2.16", "multicast (S,G): a Map-Register for (198.29.1.0/24, 224.1.1.1/32) in a site with a group-prefix is accepted; a Map-Request for (S,G) returns its locators")
def t_l216(c, sysm):
    n = c.next(); c.send(register_sg("198.29.1.0", 24, "224.1.1.1", 32, ["198.51.100.216"], n)); d, sec = c.wait_for(n, 2.0); time.sleep(0.3)
    n2 = c.next(); c.send(ecm(request_sg("198.29.1.9", "224.1.1.1", n2, c.local_ip), c.local_ip, "224.1.1.1", c.s.getsockname()[1]))
    r, _ = c.wait_for(n2, 2.0)
    got = None
    if r:
        try:
            o = 12; ttl, nloc, mask, flags, _v, afi = struct.unpack("!IBBHHH", r[o:o + 12]); o += 12
            ln = struct.unpack("!H", r[o + 4:o + 6])[0] if afi == 16387 else 4; o += (6 + ln) if afi == 16387 else 4
            locs = []
            for _ in range(nloc):
                o += 6; lafi = struct.unpack("!H", r[o:o + 2])[0]; o += 2; locs.append(socket.inet_ntoa(r[o:o + 4])); o += 4
            got = {"afi": afi, "locs": locs, "records": r[3]}
        except Exception as x: got = {"error": repr(x), "hex": r.hex()}
    ok = d is not None and got and got.get("locs") == ["198.51.100.216"] and got.get("afi") == 16387
    return ok, "notify %s; reply %s" % (d is not None, got), [sec] if d else []

def eid_sg_lispers(src, sml, grp, gml, iid=0):
    """(S,G) as lispers.net's lcaf_encode_sg actually writes it: struct "BBBBHIHBB" in NATIVE alignment -- two pad
    bytes after the length -- so not RFC 8060's layout (see L2.16 and FINDINGS-FOR-DINO.md)."""
    import socket as _s
    body_len = (4 + 2) * 2 + 8                                  # lispers.net's lcaf_length for type 9, IPv4
    lcaf = struct.pack("BBBBHIHBB", 0, 0, 9, 0, _s.htons(body_len), _s.htonl(iid), 0, sml, gml)
    lcaf += struct.pack("!H", 1) + ip4(src) + struct.pack("!H", 1) + ip4(grp)
    return struct.pack("!H", 16387) + lcaf

@test("L2.16b", "multicast (S,G) in lispers.net's own (non-RFC, padded) layout: what each system does with it", "OBSERVATION")
def t_l216b(c, sysm):
    first = (3 << 28) | 0x800 | 0x100 | 1; n = c.next()
    p = bytearray(struct.pack("!IQBBH", first, n, KEY_ID, 2, 32) + bytes(32))
    p += struct.pack("!IBBHH", 3, 1, 24, 0, 0) + eid_sg_lispers("198.29.2.0", 24, "224.1.2.2", 32)
    p += struct.pack("!BBBBHH", 1, 100, 0, 0, 0x5, 1) + ip4("198.51.100.217")
    p[16:48] = hmac.new(PASSWORD.encode(), bytes(p), hashlib.sha256).digest()
    c.send(bytes(p)); d, _ = c.wait_for(n, 2.0)
    return True, "register in lispers.net's layout: %s" % ("notified" if d else "not notified"), []

@test("L1.14", "a positive proxy Map-Reply carries TTL 1440 (registered with a 3-minute TTL)")
def t_l114(c, sysm):
    fresh(c, "198.18.233.0"); time.sleep(0.3); r, _ = c.ask("198.18.233.9"); rec = r["records"][0] if r and r["records"] else {}
    return rec.get("ttl") == 1440, "record TTL %s" % rec.get("ttl"), []

@test("L2.15.force-nat-proxy-reply", "force-nat-proxy-reply (no xTR behind NAT): a registered EID is proxy-replied with its locators, TTL 1440")
def t_opt_nat(c, sysm):
    fresh(c, "198.33.1.0"); time.sleep(0.3); r, _ = c.ask("198.33.1.9"); rec = r["records"][0] if r and r["records"] else {}
    return (rec.get("locs") == [c.local_ip] and rec.get("ttl") == 1440 and rec.get("action") == 0), "record %s" % rec, []

@test("L2.17.set-record-ttl", "policy: a matching Map-Request gets set-record-ttl 10; one no clause matches gets the implied drop (action 4, no locators)")
def t_pol_ttl(c, sysm):
    fresh(c, "198.30.1.0"); fresh(c, "198.30.2.0"); time.sleep(0.3)
    r1, _ = c.ask("198.30.1.9"); r2, _ = c.ask("198.30.2.9")
    a = r1["records"][0] if r1 and r1["records"] else {}; b = r2["records"][0] if r2 and r2["records"] else {}
    ok = a.get("ttl") == 10 and a.get("locs") == [c.local_ip] and b.get("action") == 4 and b.get("locs") == []
    return ok, "matching %s; not matching %s" % (a, b), []

@test("L2.17.set-action-drop", "policy set-action drop: action 4 (policy-denied), no locators")
def t_pol_drop(c, sysm):
    fresh(c, "198.31.1.0"); time.sleep(0.3); r, _ = c.ask("198.31.1.9"); rec = r["records"][0] if r and r["records"] else {}
    return (rec.get("action") == 4 and rec.get("locs") == []), "record %s" % rec, []

@test("L2.17.set-rloc-address", "policy matching the requesting RLOC with set-rloc-address: the reply's locator is the set address")
def t_pol_rloc(c, sysm):
    fresh(c, "198.32.1.0"); time.sleep(0.3)
    n = c.next(); c.send(ecm(map_request("198.32.1.9", n, c.local_ip), c.local_ip, "198.32.1.9", c.s.getsockname()[1])); d, _ = c.wait_for(n, 2.0)
    try: raw = reply_rloc_bytes(d).hex() if d else None
    except ValueError as x: raw = str(x)
    want = "ff00ff0000010001" + ip4("198.51.100.232").hex()      # lispers.net's policy locator: 255/0/255/0, R bit
    return raw == want, "locator %s" % raw, []

for _kind in ("geo", "elp", "rle", "json"):
    def _mkp(kind=_kind):
        def f(c, sysm):
            pfx = "198.%d.1.0" % {"geo": 34, "elp": 35, "rle": 36, "json": 37}[kind]
            fresh(c, pfx); time.sleep(0.3)
            n = c.next(); c.send(ecm(map_request(pfx[:-1] + "9", n, c.local_ip), c.local_ip, pfx[:-1] + "9", c.s.getsockname()[1]))
            r, _ = c.wait_for(n, 2.0)
            try: got = reply_rloc_bytes(r).hex() if r else None
            except ValueError as x: got = "not one locator: %s" % (r.hex() if r else x)
            # expected: lispers.net's policy locator header (priority 255, weight 0, m-priority 255, m-weight 0, R)
            # and the AFI-list LCAF lispers.net's own encoder produced for the same address and object
            want = "ff00ff000001" + LCAF_RLOCS[kind][12:]
            return got == want, "locator %s" % ("as lispers.net encodes it" if got == want else got), []
        return f
    test("L2.18.%s" % _kind, "policy set-rloc-address + set-%s-name: the answer's locator is the AFI-list LCAF lispers.net's encoder builds for that address and object" % _kind)(_mkp())

@test("L2.9b", "merged Map-Notify: a merge register from a second xTR is acknowledged to EVERY registered xTR RLOC with the merged locator set", "OBSERVATION")
def t_l29b(c, ctx):
    A, B = 0x0123456789abcdef0123456789ab0a0a, 0x0123456789abcdef0123456789ab0b0b
    c.reg([("198.18.135.0", 24, 3, [c.local_ip])], xtr_id=A, merge=True); time.sleep(0.3)
    n = c.next(); c.send(register([("198.18.135.0", 24, 3, ["198.51.100.135"])], n, xtr_id=B, merge=True))
    t0 = time.time(); want = struct.pack("!Q", n); got = []
    while time.time() - t0 < 2.0:
        c.s.settimeout(max(0.01, 2.0 - (time.time() - t0)))
        try: d, frm = c.s.recvfrom(65535)
        except socket.timeout: break
        if d[4:12] == want: got.append((d[0] >> 4, d.hex()))
    return True, "answers to B's register at A's RLOC %s: %s" % (c.local_ip, got), []

@test("L2C.1", "causality: registered through one client, resolved at once through another")
def t_l2c1(c, sysm):
    c2 = Client(c.local_ip, c.target); d, _ = fresh(c, "198.18.71.0"); r, sec = c2.ask("198.18.71.9")
    return (d is not None and locs_of(r) == [c.local_ip]), "resolves to %s" % locs_of(r), [sec] if r else []

@test("L3.8", "replay: the same accepted Map-Register sent again", "OBSERVATION")
def t_l38(c, sysm):
    n = c.next(); m = register([("198.18.72.0", 24, 3, [c.local_ip])], n); c.send(m); d1, _ = c.wait_for(n, 1.5); c.send(m); d2, _ = c.wait_for(n, 1.5)
    return True, "first %s, replay %s" % ("notified" if d1 else "not notified", "notified" if d2 else "not notified"), []

@test("L3.9", "the authentication matrix: {SHA-1, SHA-256} x {valid, wrong key, auth length wrong}; a valid SHA-256 register after each is accepted")
def t_l39(c, sysm):
    out, ok = [], True
    for alg in (1, 2):
        for case in ("valid", "wrong key", "bad length"):
            pfx = "198.18.%d.0" % (80 + alg * 4 + ("valid", "wrong key", "bad length").index(case))
            n = c.next()
            m = bytearray(register([(pfx, 24, 3, [c.local_ip])], n, alg=alg, password=PASSWORD if case != "wrong key" else "wrong"))
            if case == "bad length": m[14:16] = struct.pack("!H", 5)
            c.send(bytes(m)); d, _ = c.wait_for(n, 1.0)
            want = case == "valid"; cell_ok = (d is not None) == want
            follow = still_serving(c)
            ok &= cell_ok and follow
            out.append("%s/%s: %s%s" % ({1: "SHA-1", 2: "SHA-256"}[alg], case, "notified" if d else "no notify", "" if follow else " THEN STOPPED SERVING"))
            if not follow: return False, "; ".join(out), []
    return ok, "; ".join(out), []

def three(c, bad_at, bad):
    base = [("198.18.%d.0" % (90 + k), 24, 3, [c.local_ip]) for k in range(3)]
    if bad == "unauthorized": base[bad_at] = ("203.0.113.%d" % (10 * bad_at), 24, 3, [c.local_ip])
    else: base[bad_at] = (base[bad_at][0], 40, 3, [c.local_ip])      # invalid: IPv4 mask 40
    d, _ = c.reg(base, timeout=1.0); time.sleep(0.3)
    applied = [locs_of(c.ask(r[0][:-1] + "9")[0]) == [c.local_ip] for k, r in enumerate(base) if k != bad_at]
    for k in range(3):
        if k != bad_at: c.reg([(base[k][0], 24, 0, [])], timeout=0.5)          # clean up (withdraw)
    return d, applied

for _pos, _name in ((0, "first"), (1, "middle"), (2, "last")):
    for _bad in ("unauthorized", "invalid"):
        def _mk(pos=_pos, name=_name, bad=_bad):
            def f(c, sysm):
                d, applied = three(c, pos, bad)
                return (d is None and not any(applied)), "notify %s; valid records applied: %s" % (d is not None, applied), []
            return f
        test("L3.10.%s.%s" % (_name, _bad), "three-record register, the %s record %s: nothing applied" % (_name, _bad), "KNOWN DIFFERENCE" if _bad == "unauthorized" else None)(_mk())

@test("L4.6a", "a Map-Register with zero records: no Map-Notify; still serving")
def t_l46a(c, sysm):
    n = c.next(); c.send(register([], n)); d, _ = c.wait_for(n, 1.0)
    return (d is None and still_serving(c)), "notify %s" % (d is not None), []

@test("L4.6b", "the most records that fit a 1500-byte datagram (40): all applied, one Map-Notify")
def t_l46b(c, sysm):
    recs = [("198.18.%d.0" % (150 + k), 24, 3, [c.local_ip]) for k in range(40)]   # inside the 198.18.0.0/16 site
    d, sec = c.reg(recs, timeout=3.0); time.sleep(0.5)
    miss = [r[0] for r in recs if locs_of(c.ask(r[0][:-1] + "9")[0]) != [c.local_ip]]
    return (d is not None and not miss), "notify %s; %d of 40 not resolving" % (d is not None, len(miss)), [sec] if d else []

@test("L4.6c", "the most locators in one record that fit a 1500-byte datagram (100): all returned")
def t_l46c(c, sysm):
    rl = [c.local_ip] + ["198.51.100.%d" % k for k in range(1, 100)]
    d, sec = c.reg([("198.18.73.0", 24, 3, rl)], timeout=3.0); time.sleep(0.3); got = locs_of(c.ask("198.18.73.9")[0])
    return (d is not None and got == sorted(rl)), "notify %s; %s locators returned" % (d is not None, len(got) if got else got), [sec] if d else []

@test("L4.7", "TTL extreme: 0x7fffffff minutes is accepted and resolves")
def t_l47(c, sysm):
    d, sec = c.reg([("198.18.74.0", 24, 0x7fffffff, [c.local_ip])]); time.sleep(0.2); got = locs_of(c.ask("198.18.74.9")[0])
    return (d is not None and got == [c.local_ip]), "notify %s; resolves to %s" % (d is not None, got), [sec] if d else []

@test("L4.10", "restart of the map-server with live registrations: what is retained", "OBSERVATION")
def t_l410(c, sysm):
    fresh(c, "198.18.75.0"); time.sleep(0.3)
    sysm.restart_keep_state()
    time.sleep(1.0); got = locs_of(Client(c.local_ip, c.target).ask("198.18.75.9")[0])
    return True, "after restart: %s" % ("retained, resolves to %s" % got if got else "not retained (%s)" % got), []

@test("L4.11", "the RAM host is lost mid-run (Ribbit only; lispers.net holds its state in its own process)", "OBSERVATION")
def t_l411(c, sysm):
    ev = sysm.lose_ram_host()
    if ev is None: return True, "not applicable", []
    if not ev.get("killed"): return False, ev.get("why", "the RAM host was not killed"), []
    d, _ = c.reg([("198.18.76.0", 24, 3, [c.local_ip])], timeout=1.5)
    r, _ = c.ask("198.18.77.9", timeout=1.5)
    sysm.start()
    return True, "listener after the kill: %s; register while the host is gone: %s; request: %s; front restarted with a new host" % (ev.get("gone") or "none", "notified" if d else "not answered", "answered" if r else "not answered"), []

# ------------------------------------------------------------------------------------------------ two machines (L6)
# For the LAN / Internet runs the LISP traffic must cross the network: the harness sends from one machine and the
# systems answer on another. Connections go UP only, to a neutral RAM host (John 2026-09-28): the agent on the
# systems' machine and the harness each connect to it, and nothing connects into either machine for control. The
# harness writes each command as a cell (acceptance_ctl / <agent>|cmd / <run>.<n>); the agent holds a read on that
# variable, runs the command on its systems exactly as the harness does on one machine, and writes the answer
# (acceptance_ctl / <agent>|res / the same instance), on which the harness holds a read.
ACCEPTANCE_VERSION = "v0.63-bounded"      # this file's own version: the banner names the file that is running
AGENT_CALLS = {"start", "stop", "evidence", "delete_site", "add_site", "restart_keep_state", "lose_ram_host"}
CTL = "acceptance_ctl"

def address_toward(host, port):
    """The local address this machine's kernel uses to reach host:port (a UDP connect sends nothing)."""
    k = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try: k.connect((socket.gethostbyname(host), int(port))); return k.getsockname()[0]
    finally: k.close()

def check_local(ip, who):
    k = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try: k.bind((ip, 0))
    except OSError:
        raise SystemExit("%s: --local-ip %s is not an address of this machine" % (who, ip))
    finally: k.close()

LISP_PROCS = ("lisp-core", "lisp-ms", "lisp-mr", "lisp-itr", "lisp-etr", "lisp-rtr", "lisp-ddt")
def lispers_pids():
    out = subprocess.run(["ps", "-eo", "pid=,args="], capture_output=True, text=True).stdout
    return [l.strip() for l in out.splitlines() if any((p + ".py") in l for p in LISP_PROCS)]

class Space:
    """The neutral RAM host, through tools/tuple-write (write, get, held read)."""
    def __init__(self, host_port, api="/lisper-api"):
        h, _, p = host_port.rpartition(":")
        self.p = subprocess.Popen([os.path.join(HERE, "tuple-write"), "--ram", h, p] + ["--api", api],
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    def _line(self, o):
        self.p.stdin.write(json.dumps(o) + "\n"); self.p.stdin.flush()
        r = json.loads(self.p.stdout.readline())
        if not r.get("ok"): raise RuntimeError("tuple space: %s" % r.get("error"))
        return r
    def write(self, variable, instance, bag):             # "bag" last: tuple-write passes it through as written
        return self._line({"service": CTL, "variable": variable, "instance": instance, "bag": bag})["id"]
    def wait(self, variable, after, timeout_s):
        return self._line({"wait": {"service": CTL, "variable": variable, "after": after, "timeout_s": timeout_s}})["cells"]

def run_agent(a):
    if os.geteuid() != 0:
        raise SystemExit("the agent runs lispers.net, which runs as root (its STOP-LISP stops processes with sudo kill): run it as root")
    h, _, p = a.agent_space.rpartition(":")
    if not a.local_ip:
        a.local_ip = address_toward(h, p)
        print("agent address: %s (this machine's address toward the RAM server; give --local-ip if the harness must use another)" % a.local_ip, flush=True)
    else: check_local(a.local_ip, "agent")
    import shutil, importlib.util
    need = [x for x in ("tcsh", "python") if not shutil.which(x)]
    mods = ["cheroot", "bottle", "netifaces", "pcapy", "OpenSSL", "requests", "geopy", "ecdsa", "Crypto", "pytun", "future", "distro", "curve25519"]
    need += ["python module " + m for m in mods if importlib.util.find_spec(m) is None]
    if need:   # lispers.net's RUN-LISP / STOP-LISP are tcsh and call "python"; lisp-core imports these as root
        raise SystemExit("lispers.net cannot run on this machine as root; missing: " + ", ".join(need))
    systems = {}; sp = Space(a.agent_space); cmd, res = a.agent_name + "|cmd", a.agent_name + "|res"
    after = max([0] + [c["id"] for c in sp.wait(cmd, 0, 0)])          # commands already there are not ours
    print("acceptance agent '%s' on the RAM host %s (lispers.net in %s; Ribbit UDP %d)" % (a.agent_name, a.agent_space, a.lispers_dir, a.ribbit_udp), flush=True)
    global LOCAL_IP
    while True:
        for c in sp.wait(cmd, after, 60):
            after = max(after, c["id"]); m = c["bag"]
            try:
                if m["call"] == "where":                # the harness asks where the systems are before it picks its address
                    out = {"ok": True, "result": {"host": a.local_ip}}; sp.write(res, c["instance"], out); continue
                if m["call"] == "hello":                # the harness's address: the fixture's RLOC and policy source
                    LOCAL_IP = m["local_ip"]
                    for s in systems.values():
                        try: s.stop()
                        except Exception: pass
                    systems["lispers.net"], systems["ribbit"] = Lispers(a), Ribbit(a)
                    out = {"ok": True, "result": {n: {"version": s.version, "host": a.local_ip, "port": s.target[1]} for n, s in systems.items()}}
                elif m["call"] in AGENT_CALLS:
                    r = getattr(systems[m["sys"]], m["call"])(*m.get("args", []))
                    if m["call"] == "restart_keep_state" and m["sys"] == "lispers.net": systems["ribbit"].start()
                    out = {"ok": True, "result": r}
                else: raise ValueError("unknown call %r" % m["call"])
            except Exception as x:
                import traceback; tb = traceback.format_exc(); print(tb, flush=True)   # the agent's terminal, and ...
                out = {"ok": False, "error": "%r on %s:\n%s" % (x, a.local_ip, tb)}         # ... the harness's, in full
            sp.write(res, c["instance"], out)

class RemoteSystem:
    """A system on the agent's machine: the same methods as Lispers / Ribbit, run there through the tuple space."""
    def __init__(self, name, link, info):
        self.name, self.link, self.version, self.target = name, link, info["version"], (info["host"], info["port"])
    def _call(self, call, *args): return self.link.call({"sys": self.name, "call": call, "args": list(args)})
    def start(self): return self._call("start")
    def stop(self): return self._call("stop")
    def evidence(self): return self._call("evidence")
    def delete_site(self, site): return self._call("delete_site", site)
    def add_site(self, site): return self._call("add_site", site)
    def restart_keep_state(self): return self._call("restart_keep_state")     # the agent restarts Ribbit if needed
    def lose_ram_host(self): return self._call("lose_ram_host")

class AgentLink:
    def __init__(self, a):
        self.sp = Space(a.agent_space); self.cmd, self.res = a.agent_name + "|cmd", a.agent_name + "|res"
        self.run = "%s.%d" % (socket.gethostname(), os.getpid()); self.n = 0
        self.after = max([0] + [c["id"] for c in self.sp.wait(self.res, 0, 0)])
    def call(self, m, timeout_s=600):
        self.n += 1; inst = "%s.%d" % (self.run, self.n); self.sp.write(self.cmd, inst, m)
        t0 = time.time()
        while time.time() - t0 < timeout_s:
            for c in self.sp.wait(self.res, self.after, 30):
                self.after = max(self.after, c["id"])
                if c["instance"] == inst:
                    r = c["bag"]
                    if not r.get("ok"): raise RuntimeError("agent: %s" % r.get("error"))
                    return r["result"]
        raise RuntimeError("agent '%s' did not answer %s within %d s" % (self.cmd.split("|")[0], m.get("call"), timeout_s))

def remote_systems(a):
    link = AgentLink(a)
    sys_host = link.call({"call": "where"})["host"]
    if not a.local_ip:
        a.local_ip = address_toward(sys_host, 4342)
        print("harness address: %s (toward the systems at %s; give --local-ip if another is right)" % (a.local_ip, sys_host), flush=True)
    else: check_local(a.local_ip, "harness")
    info = link.call({"call": "hello", "local_ip": a.local_ip})
    return RemoteSystem("lispers.net", link, info["lispers.net"]), RemoteSystem("ribbit", link, info["ribbit"])

# ================================================================================================ performance
# The performance qualification (L5), integrated: it runs after the behaviour tests, against the same systems started
# by the same agent from the same fixture, with every machine reporting its own counters (see run_performance).
PERF_VERSION = "perf v2 (package " + ACCEPTANCE_VERSION + ")"
CLK = os.sysconf("SC_CLK_TCK")

# ------------------------------------------------------------------------------------------------ CPU accounting
def proc_cpu(pids):
    """user+system seconds of the given processes (and their reaped children), from /proc."""
    t = 0.0
    for pid in pids:
        try: f = open("/proc/%d/stat" % pid).read().rsplit(")", 1)[1].split()
        except OSError: continue
        t += (int(f[11]) + int(f[12]) + int(f[13]) + int(f[14])) / CLK
    return t

def pids_named(*names):
    out = subprocess.run(["ps", "-eo", "pid=,args="], capture_output=True, text=True).stdout
    return [int(l.split()[0]) for l in out.splitlines() if any(n in l for n in names)]

def lispers_cpu(): return proc_cpu(pids_named(*[p + ".py" for p in LISP_PROCS]))
def ribbit_front_cpu(): return proc_cpu(pids_named("lisp-boundary"))
def ram_server_cpu(): return proc_cpu(pids_named("lisper-ram --listen"))

# ------------------------------------------------------------------------------------------------ the monitor (every machine)
GROUPS = {   # what each component's processes are, by command line
    "lispers.net": [p + ".py" for p in LISP_PROCS],
    "ribbit-front": ["lisp-boundary"],
    "ram-server": ["lisper-ram --listen"],
}
def proc_rss_mb(pids):
    t = 0
    for pid in pids:
        try:
            for l in open("/proc/%d/status" % pid):
                if l.startswith("VmRSS:"): t += int(l.split()[1])
        except OSError: pass
    return t / 1024.0

def host_counters():
    f = open("/proc/stat").readline().split()[1:]; v = [int(x) for x in f]
    total = sum(v[:8]) / CLK; idle = (v[3] + v[4]) / CLK
    rx = tx = rxp = txp = 0
    for l in open("/proc/net/dev").read().splitlines()[2:]:
        name, d = l.split(":", 1); d = d.split()
        if name.strip() == "lo": continue
        rx += int(d[0]); rxp += int(d[1]); tx += int(d[8]); txp += int(d[9])
    return {"cpu_total_s": total, "cpu_busy_s": total - idle, "ncpu": os.cpu_count(), "net_rx_B": rx, "net_tx_B": tx,
            "net_rx_pk": rxp, "net_tx_pk": txp, "load1": float(open("/proc/loadavg").read().split()[0])}

def snapshot(groups, extra=None):
    procs = {}
    for g in groups:
        pids = pids_named(*GROUPS[g]); procs[g] = {"cpu_s": proc_cpu(pids), "rss_mb": proc_rss_mb(pids), "n": len(pids)}
    if extra: procs.update(extra())
    return {"t": time.time(), "procs": procs, "host": host_counters()}

class Monitor(threading.Thread):
    """Publishes this machine's counters: a snapshot at every run boundary the harness writes, and one sample a second.
    [MONITOR_ROWS_V1] Each monitor owns two rows and overwrites them -- its snapshot and its sample -- so a monitor left
    running does not grow the RAM server (it used to add a new row every second). Whoever wants the history (the
    harness) holds a read on these rows and keeps what it sees."""
    def __init__(self, ram, name, groups, extra=None):
        super().__init__(daemon=True); self.sp = Space(ram); self.name, self.groups, self.extra = name, groups, extra
    def run(self):
        sp = self.sp; after = max([0] + [c["id"] for c in sp.wait("perf|phase", 0, 0)]); seq = 0
        sp.write("perf|monitors", self.name, {"groups": self.groups, "host": socket.gethostname(), "t": time.time()})
        print("perf monitor '%s' (%s) publishing through the RAM server" % (self.name, ", ".join(self.groups) or "machine only"), flush=True)
        while True:
            for c in sp.wait("perf|phase", after, 1.0):
                after = max(after, c["id"])
                snap = snapshot(self.groups, self.extra); snap["boundary"] = c["bag"].get("boundary", c["instance"])
                sp.write("perf|snap", self.name, snap)
            seq += 1
            sample = snapshot(self.groups, self.extra); sample["seq"] = seq
            sp.write("perf|sample", self.name, sample)

def run_monitor(a):
    groups = [g for g in (a.groups or "ram-server").split(",") if g]
    m = Monitor(a.ram, a.monitor, groups); m.start(); m.join()

# ------------------------------------------------------------------------------------------------ the systems' agent
def run_agent_with_monitor(a):
    """The acceptance agent, with this machine's monitor ("control") beside it."""
    os.makedirs(a.out, exist_ok=True)                 # the systems' own logs (Ribbit's front) go here
    Monitor(a.ram, "control", ["lispers.net", "ribbit-front"]).start()
    run_agent(a)

# ------------------------------------------------------------------------------------------------ load generation
def reg_packet(eid, mask, rlocs, nonce, ttl_min=1440):
    return register([(eid, mask, ttl_min, rlocs)], nonce)

def many_records_packet(recs, nonce):
    return register(recs, nonce)

class Worker(threading.Thread):
    """One closed-loop client: its own socket, one outstanding request, latency per answered operation."""
    def __init__(self, idx, local_ip, target, kind, pick, stop_at, measure_from, mix_req=0.8):
        super().__init__(daemon=True)
        self.idx, self.target, self.kind, self.pick = idx, target, kind, pick
        self.stop_at, self.measure_from, self.mix_req = stop_at, measure_from, mix_req
        self.s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); self.s.bind((local_ip, 0)); self.s.settimeout(1.0)
        self.local_ip = local_ip; self.lat = []; self.errors = self.unanswered = 0; self.flip = False
        self.nonce = (idx + 1) << 40
    def one(self):
        self.nonce += 1; n = self.nonce
        kind = self.kind if self.kind != "mixed" else ("req" if random.random() < self.mix_req else "reg")
        if kind == "reg":
            eid = "198.18.%d.%d" % (200 + self.idx // 250, self.idx % 250 + 1)        # this client's own EID
            self.flip = not self.flip
            pkt = reg_packet(eid, 32, [self.local_ip] if self.flip else [self.local_ip, "198.51.100.%d" % (self.idx % 250 + 1)], n)
            want = 4
        else:
            eid = self.pick()
            pkt = ecm(map_request(eid, n, self.local_ip), self.local_ip, eid, self.s.getsockname()[1]); want = 2
        t0 = time.perf_counter(); self.s.sendto(pkt, self.target); nb = struct.pack("=Q", n) if want == 2 else struct.pack("!Q", n)
        while True:
            try: d, _ = self.s.recvfrom(65535)
            except socket.timeout: return None, kind
            if len(d) >= 12 and (d[4:12] == struct.pack("!Q", n) or d[4:12] == struct.pack("=Q", n)):
                if d[0] >> 4 != want: return False, kind
                return time.perf_counter() - t0, kind
    def run(self):
        while time.time() < self.stop_at:
            r, kind = self.one()
            if time.time() < self.measure_from: continue
            if r is None: self.unanswered += 1
            elif r is False: self.errors += 1
            else: self.lat.append(r)

def _load_child(conn, idxs, local_ip, target, kind, pick, stop_at, measure_from):
    """[LOAD_PROCESSES_V1] One load-generator process: its share of the clients, as threads. Returns what it measured
    and the CPU it spent inside the measured window."""
    try:
        random.seed(os.getpid() ^ int(time.time() * 1e6))                    # forked: every child its own sequence
        ws = [Worker(i, local_ip, target, kind, pick, stop_at, measure_from) for i in idxs]
        for w in ws: w.start()
        time.sleep(max(0.0, measure_from - time.time())); c0 = time.process_time()
        for w in ws: w.join()
        c1 = time.process_time()
        lat = array.array("d", [x for w in ws for x in w.lat])
        conn.send((lat.tobytes(), sum(w.errors for w in ws), sum(w.unanswered for w in ws), c1 - c0, None))
    except Exception as x:
        conn.send((b"", 0, 0, 0.0, "%s: %s" % (type(x).__name__, x)))
    finally:
        conn.close()

def pct(v, p):
    if not v: return None
    v = sorted(v); return v[min(len(v) - 1, int(p / 100.0 * len(v)))]

# ------------------------------------------------------------------------------------------------ the harness
class PerfHarness:
    def __init__(self, a, A, B):
        self.a = a
        self.A, self.B = A, B                   # the same systems, started by the same agent, as the acceptance run
        self.local_ip = a.local_ip
        self.sp = Space(a.ram)
        self.start_id = max([0] + [c["id"] for c in self.sp.wait("perf|sample", 0, 0)] + [c["id"] for c in self.sp.wait("perf|snap", 0, 0)])
        self.monitors = sorted(c["instance"] for c in self.sp.wait("perf|monitors", 0, 0))
        self.run_n = 0; self.run_tag = "%s.%d" % (socket.gethostname(), os.getpid()); self.snap_after = self.start_id
        self.phase_t = {}                                        # (run, phase) -> this machine's time
        self.load_snaps = {}
        # [MONITOR_ROWS_V1] the monitors overwrite their rows; the harness keeps their history as it arrives
        self.samples = {}; self.snaps = {}; self.snap_t = {}; self.edges = {}
        self.hist_stop = False
        self.hist = threading.Thread(target=self.keep_samples, daemon=True); self.hist.start()
    def keep_samples(self):
        sp = Space(self.a.ram); after = self.start_id
        while not self.hist_stop:
            for c in sp.wait("perf|sample", after, 2.0):
                after = max(after, c["id"]); self.samples.setdefault(c["instance"], []).append(c["bag"])
    def phase(self, run, ph):
        inst = "%s|%s" % (run, ph); self.phase_t[(run, ph)] = time.time()
        self.load_snaps[(run, ph)] = {"t": time.time(), "procs": {"load-generator": {"cpu_s": time.process_time(), "rss_mb": proc_rss_mb([os.getpid()]), "n": 1}}, "host": host_counters()}
        self.sp.write("perf|phase", "boundary", {"run": run, "phase": ph, "boundary": inst})   # one row, overwritten
    def edge(self, run, ph):
        """[MONITOR_ROWS_V1] Every monitor's snapshot at ONE boundary, read as soon as it is written. Each monitor keeps
        one snapshot row and overwrites it at the next boundary, so the start snapshot has to be taken before the run's
        end overwrites it (read after the run, as it was, the harness got the start only when it happened to be
        faster than the network -- here it was, on the hardware it was not)."""
        want = set(self.monitors); t0 = time.time()
        got = self.edges.setdefault(run, {})
        while want - {m for (m, p) in got if p == ph} and time.time() - t0 < 30:
            for c in self.sp.wait("perf|snap", self.snap_after, 5):
                self.snap_after = max(self.snap_after, c["id"])
                m = c["instance"]; r, p = c["bag"].get("boundary", "|").split("|")
                self.snap_t[(r, p, m)] = c["bag"].get("t")
                if r == run: got[(m, p)] = c["bag"]
    def collect(self, run):
        """Every monitor's snapshots at this run's two boundaries (taken by edge() as each boundary passed)."""
        got = self.edges.get(run, {})
        missing = sorted({(m, ph) for m in self.monitors for ph in ("start", "end")} - set(got))
        if missing: print("  !! no snapshot from %s -- that machine's numbers are missing for this run" % missing, flush=True)
        return got
    def preload(self, s, eids):
        """Register the given (eid, mask) set, 40 records per Map-Register, and wait for every Map-Notify."""
        c = Client(self.local_ip, s.target)
        for i in range(0, len(eids), 40):
            recs = [(e, m, 1440, [self.local_ip]) for e, m in eids[i:i + 40]]
            n = c.next(); c.send(many_records_packet(recs, n)); d, _ = c.wait_for(n, 3.0)
            if d is None: raise RuntimeError("%s: preload Map-Register %d (of %d) not acknowledged" % (s.name, i // 40 + 1, (len(eids) + 39) // 40))
    def withdraw(self, s, eids):
        c = Client(self.local_ip, s.target)
        for i in range(0, len(eids), 40):
            n = c.next(); c.send(many_records_packet([(e, m, 0, []) for e, m in eids[i:i + 40]], n)); c.wait_for(n, 2.0)
    def run(self, s, kind, clients, pick, warm, dur, state):
        # [LOAD_PROCESSES_V1] The clients are spread over several processes. In one Python process every client thread
        # shares one interpreter lock, so the load generator itself stopped near one core's worth of work (Ribbit's
        # 18,460 lookups/s at 64 clients used 1.1 cores on the Pi): the ceiling measured was the tool's, not the
        # system's. Each process runs its share of the clients as threads and reports its latencies, its errors and the
        # CPU it spent in the measured window; the load generator's cost is their sum plus this process's own.
        self.run_n += 1; run = "%s.%03d" % (self.run_tag, self.run_n); t0 = time.time()
        nproc = max(1, min(clients, self.a.load_procs))
        ctx = multiprocessing.get_context("fork"); kids = []
        for k in range(nproc):
            rd, wr = ctx.Pipe(duplex=False)
            pr = ctx.Process(target=_load_child, args=(wr, list(range(k, clients, nproc)), self.local_ip, s.target, kind, pick,
                                                       t0 + warm + dur, t0 + warm), daemon=True)
            pr.start(); wr.close(); kids.append((pr, rd))
        time.sleep(max(0, t0 + warm - time.time())); self.phase(run, "start"); self.edge(run, "start")
        lat = array.array("d"); errors = unanswered = 0; child_cpu = 0.0; failed = []
        for pr, rd in kids:
            try: b, e, u, c, err = rd.recv()
            except EOFError: b, e, u, c, err = b"", 0, 0, 0.0, "load process %d ended without reporting" % pr.pid
            pr.join(); lat.frombytes(b); errors += e; unanswered += u; child_cpu += c
            if err: failed.append(err)
        if failed: raise RuntimeError("load generator: " + "; ".join(failed))
        self.phase(run, "end"); self.edge(run, "end")
        snaps = self.collect(run)
        for ph in ("start", "end"): snaps[("load", ph)] = self.load_snaps[(run, ph)]
        snaps[("load", "end")]["procs"]["load-generator"]["cpu_s"] += child_cpu      # the children's measured CPU
        snaps[("load", "end")]["procs"]["load-generator"]["n"] = nproc + 1
        lat = list(lat); ok = len(lat)
        r = {"run": run, "system": s.name, "workload": kind, "clients": clients, "state": state, "ops": ok, "ops_s": ok / dur,
             "p50_ms": (pct(lat, 50) or 0) * 1e3, "p95_ms": (pct(lat, 95) or 0) * 1e3, "p99_ms": (pct(lat, 99) or 0) * 1e3,
             "errors": errors, "unanswered": unanswered, "duration_s": dur, "load_processes": nproc, "components": {}}
        for m in sorted({k[0] for k in snaps}):
            if (m, "start") not in snaps or (m, "end") not in snaps: continue
            a0, a1 = snaps[(m, "start")], snaps[(m, "end")]
            comp = {"host_busy_pct": 100.0 * (a1["host"]["cpu_busy_s"] - a0["host"]["cpu_busy_s"]) / max(1e-9, a1["host"]["cpu_total_s"] - a0["host"]["cpu_total_s"]),
                    "net_B_per_op": ((a1["host"]["net_rx_B"] - a0["host"]["net_rx_B"]) + (a1["host"]["net_tx_B"] - a0["host"]["net_tx_B"])) / ok if ok else None,
                    "net_pk_per_op": ((a1["host"]["net_rx_pk"] - a0["host"]["net_rx_pk"]) + (a1["host"]["net_tx_pk"] - a0["host"]["net_tx_pk"])) / ok if ok else None,
                    "procs": {}}
            for g, v in a1["procs"].items():
                if g not in a0["procs"]: continue
                d = v["cpu_s"] - a0["procs"][g]["cpu_s"]
                comp["procs"][g] = {"cpu_s": d, "cpu_s_per_M": d / ok * 1e6 if ok else None, "rss_mb": v["rss_mb"], "n": v["n"]}
            r["components"][m] = comp
        return r

def host_eids(n, base=(198, 18)):
    """n distinct host EIDs inside the accept-more-specifics site 198.18.0.0/16 (avoiding the reg clients' 198.18.200+)."""
    out = []
    for k in range(n):
        third, fourth = divmod(k, 254); out.append(("%d.%d.%d.%d" % (base[0], base[1], third % 200, fourth + 1), 32))
    return out

def nested_eids(n):
    """n registrations as nested prefixes: /20 over /24 over /28 over /32, so lookups exercise longest-prefix match."""
    out, k = [], 0
    for a20 in range(0, 200, 16):
        out.append(("198.18.%d.0" % a20, 20)); k += 1
        for a24 in range(a20, a20 + 16):
            out.append(("198.18.%d.0" % a24, 24)); k += 1
            for a28 in range(0, 256, 16):
                out.append(("198.18.%d.%d" % (a24, a28), 28)); k += 1
                out.append(("198.18.%d.%d" % (a24, a28 + 1), 32)); k += 1
                if k >= n: return out[:n]
    return out[:n]

def cost_cell(r, comp, group):
    c = r["components"].get(comp, {}).get("procs", {}).get(group)
    return "%.1f" % c["cpu_s_per_M"] if c and c["cpu_s_per_M"] is not None else "-"

def write_perf_report(a, h, rows):
    json.dump(rows, open(os.path.join(a.out, "perf.json"), "w"), indent=1)
    with open(os.path.join(a.out, "PERF-REPORT.md"), "w") as f:
        f.write("# Performance -- lispers.net %s vs Ribbit-LISP %s\n\n%s. Load generator %s; monitors: %s; warmup %ss, measured %ss per run.\n\n"
                % (h.A.version, h.B.version, PERF_VERSION, h.local_ip, ", ".join(h.monitors) or "none", a.warmup, a.duration))
        f.write("## Throughput and latency (load generator)\n\n| system | workload | clients | state | ops/s | p50 ms | p95 ms | p99 ms | errors | unanswered |\n|---|---|---|---|---|---|---|---|---|---|\n")
        for r in rows:
            f.write("| %s | %s | %d | %d | %.0f | %.2f | %.2f | %.2f | %d | %d |\n" % (r["system"], r["workload"], r["clients"], r["state"], r["ops_s"], r["p50_ms"], r["p95_ms"], r["p99_ms"], r["errors"], r["unanswered"]))
        f.write("\n## Work per successful operation -- CPU-seconds per 1M operations, by component\n\n"
                "control = the systems' machine (lispers.net's lisp-* processes, or Ribbit's front); shared memory = the RAM server's machine; load = the generator.\n\n"
                "| system | workload | clients | state | control: lispers.net | control: ribbit-front | shared memory: ram-server | system total | load-gen |\n|---|---|---|---|---|---|---|---|---|\n")
        for r in rows:
            tot = 0.0; parts = []
            for comp, g in (("control", "lispers.net"), ("control", "ribbit-front"), ("ram", "ram-server")):
                c = r["components"].get(comp, {}).get("procs", {}).get(g)
                if c and c["cpu_s_per_M"] is not None and (g != "lispers.net" or r["system"] == "lispers.net") and (g == "lispers.net" or r["system"] == "ribbit"): tot += c["cpu_s_per_M"]
            f.write("| %s | %s | %d | %d | %s | %s | %s | %.1f | %s |\n" % (r["system"], r["workload"], r["clients"], r["state"],
                    cost_cell(r, "control", "lispers.net"), cost_cell(r, "control", "ribbit-front"), cost_cell(r, "ram", "ram-server"), tot, cost_cell(r, "load", "load-generator")))
        f.write("\n## Machines -- CPU utilization and network per operation\n\n| system | workload | clients | state | control busy % | shared-memory busy % | load busy % | control B/op | shared-memory B/op | control pk/op | shared-memory pk/op | ram-server RSS MB |\n|---|---|---|---|---|---|---|---|---|---|---|---|\n")
        g = lambda r, m, k, fmt="%.1f": (fmt % r["components"][m][k]) if m in r["components"] and r["components"][m].get(k) is not None else "-"
        for r in rows:
            rss = r["components"].get("ram", {}).get("procs", {}).get("ram-server", {}).get("rss_mb")
            f.write("| %s | %s | %d | %d | %s | %s | %s | %s | %s | %s | %s | %s |\n" % (r["system"], r["workload"], r["clients"], r["state"],
                    g(r, "control", "host_busy_pct"), g(r, "ram", "host_busy_pct"), g(r, "load", "host_busy_pct"),
                    g(r, "control", "net_B_per_op", "%.0f"), g(r, "ram", "net_B_per_op", "%.0f"), g(r, "control", "net_pk_per_op", "%.2f"), g(r, "ram", "net_pk_per_op", "%.2f"),
                    "%.0f" % rss if rss is not None else "-"))
    write_perf_timeline(a, h, rows)

def write_perf_timeline(a, h, rows):
    """PERF-TIMELINE.html: CPU busy % per machine, one point a second, the measured runs shaded; each machine's clock
    lined up with the harness's by the run boundaries (both sides stamped the same boundary)."""
    h.hist_stop = True
    samples = {m: list(v) for m, v in h.samples.items()}              # what the harness kept as the monitors wrote it
    snaps = {k: t for k, t in h.snap_t.items() if t is not None}
    offs = {}
    for m in samples:
        d = sorted(h.phase_t[(r, ph)] - t for (r, ph, mm), t in snaps.items() if mm == m and (r, ph) in h.phase_t)
        offs[m] = d[len(d) // 2] if d else 0.0
    t0 = min([t for t in h.phase_t.values()] or [time.time()]) - 30
    W, H, PAD = 1400, 160, 40
    tmax = max([t for t in h.phase_t.values()] or [t0 + 60]) - t0 + 30
    X = lambda t: PAD + (t - t0) / tmax * (W - 2 * PAD)
    out = ['<!doctype html><meta charset="utf-8"><title>perf timeline</title><body style="font-family:sans-serif">',
           "<h2>Timeline -- CPU busy %% per machine (lispers.net %s vs Ribbit-LISP %s)</h2>" % (h.A.version, h.B.version)]
    for m in sorted(samples):
        pts = sorted(samples[m], key=lambda b: b["t"]); poly = []
        for p0, p1 in zip(pts, pts[1:]):
            dt = p1["host"]["cpu_total_s"] - p0["host"]["cpu_total_s"]
            if dt <= 0: continue
            busy = 100.0 * (p1["host"]["cpu_busy_s"] - p0["host"]["cpu_busy_s"]) / dt
            poly.append("%.1f,%.1f" % (X(p1["t"] + offs[m]), H - 10 - busy / 100.0 * (H - 30)))
        svg = ['<svg width="%d" height="%d" style="border:1px solid #ccc">' % (W, H)]
        for r in rows:
            s0, s1 = h.phase_t.get((r["run"], "start")), h.phase_t.get((r["run"], "end"))
            if s0 and s1:
                col = "#e8eefc" if r["system"] == "ribbit" else "#fcebe8"
                svg.append('<rect x="%.1f" y="0" width="%.1f" height="%d" fill="%s"><title>%s %s %d clients state %d: %.0f ops/s</title></rect>'
                           % (X(s0), max(1, X(s1) - X(s0)), H, col, r["system"], r["workload"], r["clients"], r["state"], r["ops_s"]))
        svg.append('<polyline fill="none" stroke="#333" stroke-width="1" points="%s"/>' % " ".join(poly))
        svg.append('<text x="5" y="14" font-size="12">%s (busy %%, 0-100; red runs lispers.net, blue Ribbit)</text></svg>' % m)
        out.append("<h3>%s</h3>%s" % (m, "".join(svg)))
    open(os.path.join(a.out, "PERF-TIMELINE.html"), "w").write("\n".join(out))


def run_performance(a, A, B):
    """The performance qualification, on the systems the acceptance run just qualified (same agent, same fixture)."""
    print("#" * 80 + "\n##  performance -- " + PERF_VERSION + "\n" + "#" * 80, flush=True)
    if a.perf_quick: a.warmup, a.duration, a.clients, a.scale = 1, 3, "1,4", "1,100"
    h = PerfHarness(a, A, B)
    print("load generator %s (up to %d processes) -> lispers.net %s:%d, Ribbit %s:%d; monitors publishing: %s" % (h.local_ip, a.load_procs,
          h.A.target[0], h.A.target[1], h.B.target[0], h.B.target[1], ", ".join(h.monitors) or "NONE"), flush=True)
    for need in ("control", "ram"):
        if need not in h.monitors: print("  !! no '%s' monitor is publishing: its numbers will be missing" % need, flush=True)
    clients = [int(x) for x in a.clients.split(",")]; scale = [int(x) for x in a.scale.split(",")]
    rows = []
    def show(r):
        c = r["components"]
        print("  %-11s %-11s %3d cl %6d st %9.0f ops/s  p50 %6.2f p95 %6.2f p99 %6.2f ms  err %d unans %d | CPU s/1M: control %s ram %s load %s"
              % (r["system"], r["workload"], r["clients"], r["state"], r["ops_s"], r["p50_ms"], r["p95_ms"], r["p99_ms"], r["errors"], r["unanswered"],
                 cost_cell(r, "control", "lispers.net" if r["system"] == "lispers.net" else "ribbit-front"), cost_cell(r, "ram", "ram-server"),
                 cost_cell(r, "load", "load-generator")), flush=True)
        rows.append(r)
    for s in (h.A, h.B):
        print("== %s: starting fresh" % s.name, flush=True); s.start()
        base = host_eids(1000); h.preload(s, base); pick = lambda: random.choice(base)[0]
        for kind in ("reg", "req", "mixed"):
            for n in clients:
                print("  .. %s %s, %d clients (%gs warmup, %gs measured)" % (s.name, kind, n, a.warmup, a.duration), flush=True)
                show(h.run(s, kind, n, pick, a.warmup, a.duration, len(base)))
        h.withdraw(s, base)
        for label, gen in (("host", host_eids), ("nested", nested_eids)):
            for n in scale:
                # [SCALE_FROM_FRESH_V1] every scale step starts from a freshly started system. A withdraw (TTL 0) is not
                # a removal: on the hardware, lispers.net after the 10,000-host step answered the NEXT step (nested, 1
                # registration) at the 10,000-host rate -- 95 lookups/s against 504 -- so every later row was measured
                # against a table its label did not describe.
                s.stop(); s.start()
                eids = gen(n); print("  .. %s scale (%s prefixes): started fresh, preloading %d registrations" % (s.name, label, n), flush=True)
                h.preload(s, eids)
                if label == "host": pick = (lambda e=eids: random.choice(e)[0])
                else:
                    def pick(e=eids):
                        p, m = random.choice(e); o = [int(x) for x in p.split(".")]
                        if m < 32:
                            span = 1 << (32 - m); v = ((o[0] << 24) | (o[1] << 16) | (o[2] << 8) | o[3]) + random.randrange(span)
                            o = [(v >> 24) & 255, (v >> 16) & 255, (v >> 8) & 255, v & 255]
                        return "%d.%d.%d.%d" % tuple(o)
                r = h.run(s, "req", 16, pick, a.warmup, a.duration, n); r["workload"] = "req-" + label; show(r)
                h.withdraw(s, eids)
        s.stop()
    write_perf_report(a, h, rows)
    return rows

# ------------------------------------------------------------------------------------------------ the run
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--local-ip", help="this machine's address (the harness's, or the agent's own)")
    ap.add_argument("--lispers-dir", help="where lispers.net is installed on this machine")
    ap.add_argument("--lispers-api-port", type=int, default=8080, help="the port lispers.net's own REST API is started on (RUN-LISP's argument; default 8080)")
    ap.add_argument("--lispers-env", action="append", default=[])
    ap.add_argument("--ram-server"); ap.add_argument("--ram-port", type=int)
    ap.add_argument("--ribbit-udp", type=int, default=14342, help="the UDP port Ribbit's map-server front listens on (default 14342)")
    ap.add_argument("--ram", help="HOST:PORT of the RAM server (e.g. streamingfrog.com:8800): Ribbit's memory, and the "
                                  "meeting point of the two machines -- the same as --ram-external plus --agent-space")
    ap.add_argument("--only", default="")
    ap.add_argument("--ram-external", help="HOST:PORT of the neutral RAM host Ribbit uses (instead of starting one here)")
    ap.add_argument("--agent", action="store_true", help="run as the systems' agent on their machine (L6)")
    ap.add_argument("--agent-space", help="HOST:PORT of the neutral RAM host the agent and the harness both connect up to (L6)")
    ap.add_argument("--agent-name", default="acceptance", help="the agent's name in that space (L6)")
    ap.add_argument("--monitor", help="run as this machine's monitor under this name (on the RAM server's machine: --monitor ram)")
    ap.add_argument("--groups", help="with --monitor: the process groups on this machine (default ram-server)")
    ap.add_argument("--skip-perf", action="store_true", help="behaviour only: no performance qualification after it")
    ap.add_argument("--perf-quick", action="store_true", help="a short performance run, to check the three machines report")
    ap.add_argument("--perf-only", action="store_true", help="the performance qualification alone (the behaviour tests are skipped)")
    ap.add_argument("--load-procs", type=int, default=os.cpu_count() or 4,
                    help="load-generator processes the clients are spread over (default: this machine's cores)")
    ap.add_argument("--clients", default="1,4,16,64"); ap.add_argument("--warmup", type=float, default=5)
    ap.add_argument("--duration", type=float, default=20); ap.add_argument("--scale", default="1,100,1000,10000")
    ap.add_argument("--ribbit-front", help="the Map-Server front binary (default tools/lisp-boundary); P5 uses a coverage build")
    ap.add_argument("--record"); ap.add_argument("--wire"); ap.add_argument("--out", default="acceptance-out")
    a = ap.parse_args(); os.makedirs(a.out, exist_ok=True)
    _v = ACCEPTANCE_VERSION
    print("#" * 80 + "\n##  acceptance.py -- package %s\n" % _v + "#" * 80, flush=True)
    missing = [b for b in ("tools/tuple-write", "tools/lisp-boundary", "ribbit_cpp/lisper-ram") if not os.path.exists(os.path.join(HERE, "..", b))]
    if missing:                                   # say so, instead of a traceback from the first program that is missing
        print("not built: %s -- run STAGES=build tools/qualify.sh in %s" % (", ".join(missing), os.path.abspath(os.path.join(HERE, ".."))), flush=True)
        return 2
    if a.monitor:                                 # the RAM server's machine (or any machine): report its counters
        if not a.ram: ap.error("--monitor needs --ram")
        a.agent_space = a.ram; return run_monitor(a)
    if a.ram:                                     # one flag for the one RAM server
        a.ram_external = a.ram_external or a.ram; a.agent_space = a.agent_space or a.ram
    global LOCAL_IP; LOCAL_IP = a.local_ip
    if a.record and not a.wire: ap.error("--record needs --wire")
    local_needs = ("local_ip", "lispers_dir", "lispers_api_port", "ribbit_udp") + (() if a.ram_external else ("ram_server", "ram_port"))
    if a.agent:
        missing = [k for k in local_needs if k != "local_ip" and getattr(a, k) is None] + ([] if a.agent_space else ["agent_space"])
        if missing: ap.error("--agent needs " + ", ".join("--" + k.replace("_", "-") for k in missing))
        run_agent_with_monitor(a); return 0          # the agent, with this machine's "control" monitor beside it
    if a.agent_space:
        A, B = remote_systems(a)
    else:
        missing = [k for k in local_needs if getattr(a, k) is None]
        if missing: ap.error("needs " + ", ".join("--" + k.replace("_", "-") for k in missing) + " (or --agent)")
        A, B = Lispers(a), Ribbit(a)
    A.start(); B.start()                           # lispers.net first: its start kills processes named lisp-*
    rec = Recorder(a.record, a.wire) if a.record else None
    runs = {s.name: (rec.next_run(s.name, s.version) if rec else 0) for s in (A, B)}
    if rec:
        for s in (A, B): rec.manifest(s.name, s.version, runs[s.name], {"schema": 1, "system": s.name, "version": s.version, "wire": a.wire,
                                      "run": runs[s.name], "suite": "acceptance", "sites": SITES, "host": rec.host,
                                      "started": time.strftime("%Y-%m-%dT%H:%M:%S%z")})
    rows = []
    for tid, title, klass, fn in ([] if a.perf_only else TESTS):
        if a.only and not any(tid.startswith(x) for x in a.only.split(",")): continue
        res = {}
        for s in (A, B):
            print("  .. %s on %s" % (tid, s.name), flush=True)
            c = Client(a.local_ip, s.target)
            try: ok, ev, lat = fn(c, s)
            except Exception as x: ok, ev, lat = False, "harness: %r" % x, []
            if getattr(A, "ribbit_needs_restart", False): A.ribbit_needs_restart = False; B.start()
            if not still_serving(c):                # a system that stopped serving is a result; restart it
                ev += " -- STOPPED SERVING after this test; evidence: " + (s.evidence().strip().replace("\n", " | ")[-400:] or "(none)")
                ok = False
                print("  .. %s stopped serving: restarting %s" % (s.name, "lispers.net and Ribbit" if s is A else "Ribbit"), flush=True)
                if s is A: A.start(); B.start()      # restarting lispers.net kills Ribbit's front: restart both
                else: B.start()
            res[s.name] = (ok, ev, lat)
            if rec:
                rec.write("acceptance.result", "%s|%s|%s|%s|r%d" % (s.name, s.version, a.wire, tid, runs[s.name]),
                          {"schema": 1, "test": tid, "title": title, "system": s.name, "version": s.version, "wire": a.wire,
                           "run": runs[s.name], "pass": ok, "evidence": ev})
                if lat: rec.samples(tid, "latency_us", "us", s.name, s.version, {"test": tid}, runs[s.name], [int(x * 1e6) for x in lat], [] if ok else [ev])
        pa, pb = res["lispers.net"][0], res["ribbit"][0]
        cls = klass if klass else ("PARITY" if pa and pb else "A FAIL" if pb and not pa else "B FAIL" if pa and not pb else "A FAIL + B FAIL")
        rows.append((tid, title, res, cls))
        print("%-6s %-18s A %s  B %s   %s" % (tid, cls, "PASS" if pa else "FAIL", "PASS" if pb else "FAIL", title), flush=True)
        for n in ("lispers.net", "ribbit"):
            if not res[n][0] or cls in ("OBSERVATION", "KNOWN DIFFERENCE"): print("         %s: %s" % (n, res[n][1][:300]), flush=True)
    A.stop(); B.stop()
    with open(os.path.join(a.out, "ACCEPTANCE-REPORT.md"), "w") as f:
        f.write("# Acceptance report\n\nlispers.net %s (A) vs Ribbit-LISP %s (B), wire %s, %s\n\n" % (A.version, B.version, a.wire or (("network: harness %s, systems %s" % (a.local_ip, A.target[0])) if a.agent_space else "loopback"), time.strftime("%Y-%m-%d %H:%M")))
        f.write("| test | classification | A | B | what |\n|---|---|---|---|---|\n")
        for tid, title, res, cls in rows:
            f.write("| %s | %s | %s | %s | %s |\n" % (tid, cls, "PASS" if res["lispers.net"][0] else "FAIL", "PASS" if res["ribbit"][0] else "FAIL", title))
        f.write("\n## Evidence\n\n")
        for tid, title, res, cls in rows:
            f.write("- **%s** (%s)\n  - A: %s\n  - B: %s\n" % (tid, cls, res["lispers.net"][1], res["ribbit"][1]))
    counts = {}
    for r in rows: counts[r[3]] = counts.get(r[3], 0) + 1
    print("\n" + ", ".join("%s %d" % kv for kv in sorted(counts.items())) + "   (report: %s/ACCEPTANCE-REPORT.md)" % a.out)
    # [PERF_INTEGRATED_V1] the performance qualification follows, on the same systems through the same agent, and its
    # results go into the same report: behaviour and performance of one frozen build from one run
    if not a.skip_perf and not a.only:
        if not a.agent_space:
            print("performance: needs the three-machine setup (--ram): skipped", flush=True)
        else:
            run_performance(a, A, B)
            with open(os.path.join(a.out, "ACCEPTANCE-REPORT.md"), "a") as f:
                f.write("\n\n" + open(os.path.join(a.out, "PERF-REPORT.md")).read().replace("# Performance", "# Performance (same run, same build)", 1))
            print("performance: in %s/ACCEPTANCE-REPORT.md (numbers: perf.json; timeline: PERF-TIMELINE.html)" % a.out, flush=True)
    return 0 if not any(r[3] == "B FAIL" or r[3] == "A FAIL + B FAIL" for r in rows) else 1

if __name__ == "__main__":
    sys.exit(main())
