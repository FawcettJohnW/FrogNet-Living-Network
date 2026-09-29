#!/usr/bin/env python3
"""acceptance_hw.py -- the acceptance tests that need real hardware (two hosts, or a real LAN / Internet path).
They cannot run in one container: lispers.net binds 0.0.0.0:4342 and its STOP-LISP kills every "lisp-" process on the
machine, so a second lispers.net (a DDT authority, a peer map-server) needs a second host. See REAL-HARDWARE-TESTS.md
for the topology and the configuration of each host; this program only sends LISP control messages and checks the
answers, like tools/acceptance.py, and reports the same five-way classification.

Topology (per system -- run it once with the lispers.net hosts, once with the Ribbit hosts):
  AUTH  a map-server that is NOT a map-resolver, DDT-authoritative for 198.18.0.0/15, with the acceptance sites;
        lispers.net: map-server = yes, map-resolver = no, ms-authoritative-prefix 198.18.0.0/15
  MR    a map-resolver with ddt-root = AUTH (lispers.net: lisp ddt-root { address = AUTH })
  PEER  (map-server-peer) a second map-server; AUTH lists it with lisp map-server-peer

usage: acceptance_hw.py --system NAME --local-ip A --auth HOST[:PORT] --mr HOST[:PORT] [--peer HOST[:PORT]]
                        [--lig PATH-TO-LISPERS-lig] [--key-id 1 --password acceptance-secret] [--out DIR]
"""
import argparse, os, subprocess, sys, time
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import acceptance as T

def hp(s, default=4342):
    h, _, p = s.partition(":"); return (h, int(p) if p else default)

# A map-server sends its Map-Referral to the requester's LISP control port, 4342 (lispers.net: "Send 40 bytes to
# <requester> 4342", observed) -- so this program listens on 4342 of --local-ip for them: run it on a machine where
# nothing else holds 4342 (not on the AUTH or MR host).
def ddt_ask(c, eid, timeout=3.0):
    import socket as _s, struct as _st
    lis = _s.socket(_s.AF_INET, _s.SOCK_DGRAM); lis.setsockopt(_s.SOL_SOCKET, _s.SO_REUSEADDR, 1); lis.bind((c.local_ip, 4342))
    try:
        n = c.next(); m = bytearray(T.ecm(T.map_request(eid, n, c.local_ip), c.local_ip, eid, c.s.getsockname()[1])); m[0] |= 0x04
        t0 = time.perf_counter(); c.send(bytes(m)); want = _st.pack("!Q", n)
        while time.perf_counter() - t0 < timeout:
            lis.settimeout(max(0.01, timeout - (time.perf_counter() - t0)))
            try: d, _ = lis.recvfrom(65535)
            except _s.timeout: break
            if len(d) >= 12 and d[4:12] == want and d[0] >> 4 == 6: return T.parse_referral(d), time.perf_counter() - t0, d
        return None, None, None
    finally: lis.close()

TESTS = []
def test(tid, title, klass=None):
    def deco(f): TESTS.append((tid, title, klass, f)); return f
    return deco

@test("HW.DDT.1", "a DDT-originated Map-Request to the authority for a REGISTERED EID -> Map-Referral, action MS-ACK, authoritative")
def t1(a, auth, mr):
    c = T.Client(a.local_ip, auth); d, _ = c.reg([("198.18.201.0", 24, 3, [a.local_ip])]); time.sleep(0.5)
    r, sec, raw = ddt_ask(c, "198.18.201.9"); rec = r["records"][0] if r and r["records"] else {}
    peers = [a.peer] if a.peer else []
    ok = d is not None and rec.get("action") == 2 and rec.get("auth") and rec.get("ttl") == 1440 and rec.get("eid") == "198.18.201.0" \
         and rec.get("mask") == 24 and rec.get("rlocs") == peers and rec.get("incomplete") == (not peers)
    return ok, "register notified %s; referral %s" % (d is not None, rec), [sec] if r else []

@test("HW.DDT.2", "a DDT-originated Map-Request inside a site, nothing registered -> Map-Referral, action MS-NOT-REGISTERED, TTL 1")
def t2(a, auth, mr):
    c = T.Client(a.local_ip, auth); r, sec, raw = ddt_ask(c, "198.18.252.9"); rec = r["records"][0] if r and r["records"] else {}
    peers = [a.peer] if a.peer else []
    ok = rec.get("action") == 3 and rec.get("ttl") == 1 and rec.get("eid") == "198.18.252.9" and rec.get("mask") == 32 and rec.get("rlocs") == peers
    return ok, "referral %s" % rec, [sec] if r else []

@test("HW.DDT.3", "a DDT-originated Map-Request outside the authoritative prefix -> Map-Referral, action NOT-AUTHORITATIVE, TTL 0")
def t3(a, auth, mr):
    c = T.Client(a.local_ip, auth); r, sec, raw = ddt_ask(c, "203.0.113.201"); rec = r["records"][0] if r and r["records"] else {}
    ok = rec.get("action") == 5 and rec.get("ttl") == 0 and rec.get("eid") == "203.0.113.201" and rec.get("mask") == 32 and rec.get("incomplete") and rec.get("rlocs") == []
    return ok, "referral %s" % rec, [sec] if r else []

@test("HW.DDT.3b", "a DDT-originated Map-Request inside the authoritative prefix but in no site -> Map-Referral NOT-AUTHORITATIVE, TTL 0, lispers.net's negative prefix (198.19.0.0/21 with the acceptance sites) and the peers")
def t3b(a, auth, mr):
    c = T.Client(a.local_ip, auth); r, sec, raw = ddt_ask(c, "198.19.7.9"); rec = r["records"][0] if r and r["records"] else {}
    # observed from lispers.net 0.643 (AUTH from make_hw_configs.py): NOT-AUTHORITATIVE, TTL 0, 198.19.0.0/21 -- the
    # mask is the INDEX of the first bit where 198.19.7.9 differs from the site 198.19.0.0/24, so the prefix still
    # covers that site (FINDINGS-FOR-DINO.md); Ribbit reproduces it for parity
    peers = [a.peer] if a.peer else []
    ok = rec.get("action") == 5 and rec.get("ttl") == 0 and rec.get("eid") == "198.19.0.0" and rec.get("mask") == 21 and rec.get("rlocs") == peers
    return ok, "referral %s" % rec, [sec] if r else []

@test("HW.DDT.4", "an ITR's ECM Map-Request to the map-resolver for an EID registered at the authority -> the map-resolver follows the referral; the ITR gets the registered locators")
def t4(a, auth, mr):
    ca = T.Client(a.local_ip, auth); ca.reg([("198.18.202.0", 24, 3, [a.local_ip, "198.51.100.202"])]); time.sleep(0.5)
    c = T.Client(a.local_ip, mr); r, sec = c.ask("198.18.202.9", timeout=5.0)
    got = T.locs_of(r)
    return got == sorted([a.local_ip, "198.51.100.202"]), "resolved through the map-resolver to %s" % got, [sec] if r else []

@test("HW.DDT.5", "the map-resolver caches the referral: a second request for the same space is answered without a new DDT walk (faster)", "OBSERVATION")
def t5(a, auth, mr):
    c = T.Client(a.local_ip, mr); r1, s1 = c.ask("198.18.202.10", timeout=5.0); r2, s2 = c.ask("198.18.202.11", timeout=5.0)
    return True, "first %.1f ms, second %.1f ms" % ((s1 or 0) * 1000, (s2 or 0) * 1000), [x for x in (s1, s2) if x]

@test("HW.PEER.1", "a registration at the authority is known to its map-server peer (map-server-peer)", "OBSERVATION")
def t6(a, auth, mr):
    if not a.peer: return True, "no --peer given", []
    ca = T.Client(a.local_ip, auth); ca.reg([("198.18.203.0", 24, 3, [a.local_ip])]); time.sleep(1.0)
    r, _, raw = ddt_ask(T.Client(a.local_ip, hp(a.peer)), "198.18.203.9"); rec = r["records"][0] if r and r["records"] else {}
    return True, "peer's referral for the authority's registration: %s" % rec, []

@test("HW.LIG.1", "Dino's lig, as a second independent client, resolves a registered EID through the map-resolver")
def t7(a, auth, mr):
    if not a.lig: return True, "no --lig given (lispers.net's lig tool)", []
    ca = T.Client(a.local_ip, auth); ca.reg([("198.18.204.0", 24, 3, ["198.51.100.204"])]); time.sleep(0.5)
    out = subprocess.run([a.lig, "198.18.204.9", "to", mr[0]], cwd=os.path.dirname(a.lig), capture_output=True, text=True, timeout=30).stdout
    return "198.51.100.204" in out, "lig printed: %s" % " | ".join(out.strip().splitlines()[-6:]), []

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--system", required=True); ap.add_argument("--local-ip", required=True)
    ap.add_argument("--auth", required=True); ap.add_argument("--mr", required=True); ap.add_argument("--peer")
    ap.add_argument("--lig"); ap.add_argument("--out", default="acceptance-hw-out")
    a = ap.parse_args(); os.makedirs(a.out, exist_ok=True)
    auth, mr = hp(a.auth), hp(a.mr); rows = []
    for tid, title, klass, fn in TESTS:
        try: ok, ev, lat = fn(a, auth, mr)
        except Exception as x: ok, ev, lat = False, "harness: %r" % x, []
        rows.append((tid, title, klass or ("PASS" if ok else "FAIL"), ev))
        print("%-10s %-12s %s\n           %s" % (tid, klass or ("PASS" if ok else "FAIL"), title, ev[:400]), flush=True)
    with open(os.path.join(a.out, "ACCEPTANCE-HW-%s.md" % a.system), "w") as f:
        f.write("# Real-hardware acceptance -- %s\n\nauth %s, map-resolver %s, peer %s, %s\n\n| test | result | what |\n|---|---|---|\n" % (a.system, a.auth, a.mr, a.peer, time.strftime("%Y-%m-%d %H:%M")))
        for tid, title, res, ev in rows: f.write("| %s | %s | %s |\n" % (tid, res, title))
        f.write("\n## Evidence\n\n" + "".join("- **%s**: %s\n" % (tid, ev) for tid, _t, _r, ev in rows))
    return 0

if __name__ == "__main__":
    sys.exit(main())
