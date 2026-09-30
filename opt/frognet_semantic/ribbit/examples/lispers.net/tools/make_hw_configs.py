#!/usr/bin/env python3
"""make_hw_configs.py -- the configuration files for the two-host tests (REAL-HARDWARE-TESTS.md section 1), written
from the same fixture tools/acceptance.py uses, so nothing is typed by hand.

usage: tools/make_hw_configs.py --harness-ip IP --auth-ip IP --mr-ip IP [--peer-ip IP]
                                --ram-host HOST --ram-port PORT [--ram-api /lisper-api] [--ribbit-udp 4342] --out DIR
writes:
  DIR/lispers-auth/lisp.config      lispers.net AUTH: map-server only, the acceptance sites, ms-authoritative-prefix
                                    198.18.0.0/15 [, map-server-peer PEER]  -> copy into its lispers.net directory
  DIR/lispers-mr/lisp.config        lispers.net MR: map-resolver only, ddt-root = AUTH
  DIR/ribbit-auth/lisp-service.config   Ribbit AUTH: lisp-service, role map-server, the same sites and key, DDT authority
  DIR/ribbit-mr/lisp-service.config     Ribbit MR: lisp-service, role map-resolver   -> /etc/lispers.d/ on each host
"""
import argparse, json, os, sys
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import acceptance as T

HEADER = ["# lispers.net lisp.config file", "lisp user-account {", "    username = root", "    password =", "    super-user = yes", "}"]
def enable(ms, mr):
    return ["lisp enable {", "    itr = no", "    etr = no", "    rtr = no", "    map-server = %s" % ("yes" if ms else "no"),
            "    map-resolver = %s" % ("yes" if mr else "no"), "    ddt-node = no", "}"]

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--harness-ip", required=True); ap.add_argument("--auth-ip", required=True); ap.add_argument("--mr-ip", required=True)
    ap.add_argument("--peer-ip"); ap.add_argument("--ram-host", required=True); ap.add_argument("--ram-port", type=int, required=True)
    ap.add_argument("--ram-api", default="/lisper-api"); ap.add_argument("--ribbit-udp", type=int, default=4342); ap.add_argument("--out", required=True)
    a = ap.parse_args()
    T.LOCAL_IP = a.harness_ip                                  # the RLOC the fixture's allowed-rloc site and policy name
    full = T.lispers_config(T.SITES).splitlines()
    body = full[full.index("}", full.index("lisp user-account {")) + 1:]          # everything after the user account
    auth = HEADER + enable(True, False) + body
    if a.peer_ip:
        auth += ["lisp map-server-peer {", "    prefix {", "        instance-id = 0", "        eid-prefix = 198.18.0.0/15", "    }",
                 "    peer {", "        address = %s" % a.peer_ip, "    }", "}"]
    mr = HEADER + enable(False, True) + ["lisp ddt-root {", "    address = %s" % a.auth_ip, "}"]
    for d, text in (("lispers-auth", auth), ("lispers-mr", mr)):
        os.makedirs(os.path.join(a.out, d), exist_ok=True)
        open(os.path.join(a.out, d, "lisp.config"), "w").write("\n".join(text) + "\n")
    ram = {"host": a.ram_host, "port": a.ram_port, "api": a.ram_api}
    sites = []
    for s in T.SITES:                                            # the sites as lisp-service passes them to site.add
        x = T.ribbit_site_args(s); sites.append(x)
    rauth = {"name": "acceptance-auth", "ram": ram, "roles": ["map-server"], "udp": {"address": a.auth_ip, "port": a.ribbit_udp},
             "heartbeat_s": 5, "startup_wait_s": 5, "sites": sites, "ms_authoritative_prefixes": ["198.18.0.0/15"]}
    if a.peer_ip: rauth["ms_peers"] = [a.peer_ip]
    rmr = {"name": "acceptance-mr", "ram": ram, "roles": ["map-resolver"], "udp": {"address": a.mr_ip, "port": a.ribbit_udp},
           "heartbeat_s": 5, "startup_wait_s": 5}
    for d, cfg in (("ribbit-auth", rauth), ("ribbit-mr", rmr)):
        os.makedirs(os.path.join(a.out, d), exist_ok=True)
        json.dump(cfg, open(os.path.join(a.out, d, "lisp-service.config"), "w"), indent=1)
    print("wrote %s/{lispers-auth,lispers-mr}/lisp.config and %s/{ribbit-auth,ribbit-mr}/lisp-service.config" % (a.out, a.out))

if __name__ == "__main__":
    main()
