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
frognet-discovery CLI.

  selfcheck   run all oracle/structural proofs (default; touches nothing)
  inspect     READ-ONLY: gather this node's discovery inputs + classification
              and the tunnels it WOULD mark dead, print them. No mutation.
  apply       Run a real merge against the live kernel. MUTATES ROUTES + hosts.
              Requires --i-understand-this-mutates-live-routing. Snapshots
              `ip route`/`/etc/hosts` to /var/tmp first. UNVALIDATED ON HARDWARE.
"""
import argparse, sys, subprocess, json, time


def _selfcheck(_a):
    from .selfcheck import run
    return run()


def _proof(_a):
    from .sim.proof_plane import main as proof_main
    return proof_main()


def _inspect(_a):
    from . import live
    from .mapinterfaces import RealInterfaceMap, classify
    imap = RealInterfaceMap()
    ifaces, leases_nonempty = imap.gather()
    cls = classify(ifaces, leases_nonempty=leases_nonempty)
    info = {
        "classification": cls,
        "dev_ip": {i.dev: i.ip4() for i in ifaces},
        "leases": live._leases(),
        "active_states": live._active_states(),
        "arp_neigh": live._arp_neigh(cls["FROGNET_INTERFACES"].split()),
        "local_ips": sorted(live._local_ips()),
    }
    print(json.dumps(info, indent=2))
    print("\n[inspect] read-only; nothing was changed. "
          "Health probing and discovery are NOT run in this mode "
          "(they install probe routes).", file=sys.stderr)
    return 0


def _apply(a):
    if not a.i_understand_this_mutates_live_routing:
        print("REFUSING: `apply` mutates the live routing table and /etc/hosts, "
              "and this port has NEVER run on hardware. Re-run with "
              "--i-understand-this-mutates-live-routing once you've read the "
              "code and are on a node you can afford to disrupt.\n"
              "Run `frognet-discovery inspect` first to see what it sees.",
              file=sys.stderr)
        return 2
    ts = time.strftime("%Y%m%d-%H%M%S")
    rt = subprocess.run(["/usr/sbin/ip", "-4", "route", "show"],
                        capture_output=True, text=True).stdout
    open(f"/var/tmp/frognet_route_snapshot.{ts}", "w").write(rt)
    try:
        hosts = open("/etc/hosts").read()
        open(f"/var/tmp/frognet_hosts_snapshot.{ts}", "w").write(hosts)
    except OSError:
        pass
    print(f"[apply] snapshotted route table + /etc/hosts to "
          f"/var/tmp/frognet_*_snapshot.{ts}", file=sys.stderr)
    from . import live
    out = live.main()
    print("\n[apply] merge pass complete. Compare the trace above + `ip r` + "
          "/etc/hosts against a bash runMerge.bash run for the differential.",
          file=sys.stderr)
    return 0


def _kernel_check(a):
    from .sim.kernel_check import main as kc_main
    args = ["--observer", a.observer]
    if getattr(a, "real", False):
        args.append("--real")
    return kc_main(args)


def _check_ip(a):
    from .live import check_ip_collision
    taken = check_ip_collision(a.ip)
    print("TAKEN" if taken else "FREE")
    return 1 if taken else 0


def main(argv=None):
    p = argparse.ArgumentParser(prog="frognet-discovery")
    sub = p.add_subparsers(dest="cmd")
    sub.add_parser("selfcheck")
    sub.add_parser("inspect")
    sub.add_parser("proof")
    ap = sub.add_parser("apply")
    ap.add_argument("--i-understand-this-mutates-live-routing",
                    dest="i_understand_this_mutates_live_routing", action="store_true")
    ci = sub.add_parser("check-ip")
    ci.add_argument("ip", help="FrogNet 10. address to test for a duplicate before claiming it")
    kc = sub.add_parser("kernel-check")
    kc.add_argument("--observer", default="M4")
    kc.add_argument("--real", action="store_true")
    a = p.parse_args(argv)
    return {"selfcheck": _selfcheck, "proof": _proof, "inspect": _inspect,
            "apply": _apply, "kernel-check": _kernel_check, "check-ip": _check_ip,
            None: _selfcheck}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
