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
kernel_check.py - "Kernel Route OK" bring-up tool.

Given a declared topology and one observer node, run the REAL ported discovery
for that node against a real kernel - either:

  shadow (default): read the box's real `ip route` table, run the port against
    an in-memory copy, and PRINT the exact `ip route` commands it would issue +
    the table that would result. Nothing on the box changes.

  real (--real): wire the port to RealKernel so it ACTUALLY installs the routes,
    then print the resulting real table. This MUTATES the box's routing (root,
    destructive) - run only on a disposable/test node, ideally in a netns.

The peer sources (echo/rtt/getHosts/broker) here are derived from the declared
topology, so this validates the route-mutation argv against a real kernel - does
`ip route replace ... metric 22 onlink` actually parse/apply - not the live
network backends. Full live discovery is live.py.
"""
import argparse
import sys

from discovery.kernel import ShadowKernel, RealKernel
from discovery.sim.system import System, TopologySpec, NodeSpec


def _demo_spec():
    # tiny declared topology; observer M4 has a LAN peer M10 and a tunnel to GW
    return TopologySpec(
        nodes=[NodeSpec("M4", "10.14.14"),
               NodeSpec("M10", "10.20.20", guest_on=("M4", "10.14.14.230")),
               NodeSpec("GW", "10.9.9")],
        tunnels=[("M4", "GW")],
    )


def run(spec, observer, mode="shadow", real_kernel=None):
    s = System(spec)
    # converge peers first so the observer's getHosts horizon is realistic
    s.converge()
    if mode == "shadow":
        k = ShadowKernel(real=real_kernel)        # real=None -> RealKernel() on box
        out = s.run_node_on_kernel(observer, k, seed_synthetic=False)
        return {"mode": "shadow", "start_table": k.start_table,
                "planned": k.planned, "would_be_table": k.final_table(),
                "merge": out}
    elif mode == "real":
        k = real_kernel or RealKernel()
        out = s.run_node_on_kernel(observer, k, seed_synthetic=False)
        return {"mode": "real", "final_table": k.route_show(), "merge": out}
    raise ValueError(mode)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="frognet-discovery kernel-check")
    ap.add_argument("--observer", default="M4")
    ap.add_argument("--real", action="store_true",
                    help="ACTUALLY install routes into the kernel (mutates, root)")
    a = ap.parse_args(argv)
    spec = _demo_spec()
    if a.real:
        print("WARNING: --real will mutate this box's routing table. "
              "Use only on a disposable/test node.", file=sys.stderr)
        r = run(spec, a.observer, mode="real")
        print("[real] final kernel table:")
        for ln in r["final_table"].split(";"):
            if ln.strip():
                print("   ", ln.strip())
    else:
        r = run(spec, a.observer, mode="shadow")
        print(f"[shadow] observer={a.observer}  (real FIB NOT modified)\n")
        print("real starting table:")
        for ln in r["start_table"]:
            print("   ", ln)
        print("\nplanned `ip route` commands the port would issue:")
        for c in r["planned"]:
            print("   ", c)
        print("\nresulting table the real FIB WOULD become:")
        for ln in r["would_be_table"]:
            print("   ", ln)
    return 0


if __name__ == "__main__":
    import os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    sys.exit(main())
