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
"""Run the REAL discovery core for one node (build_live + seeds + run_merge).
Relies on the mount-ns having bound the fakes over /usr/sbin/ip, /usr/bin/curl,
/etc/hosts, /var/lib/frognet-tunnel - so unmodified real code hits the faked box."""
import sys, os
sys.path.insert(0, os.environ["FROGNET_SEMANTIC_ROOT"])
sys.path.insert(0,'/home/claude/frogsim_proc')
import frogsim_transport as _ft; _ft.install()
from discovery import live
from discovery.mapinterfaces import RealInterfaceMap, classify
from discovery.runmerge import run_merge

NODE = os.environ["FROGSIM_NODE"]
IDENT = os.environ.get("FROGSIM_IDENT", "")
def log(s): print(f"  [{NODE}]", s)

disc, k, broker, local_ips = live.build_live(log, self_identity=IDENT)
imap = RealInterfaceMap(); ifaces, leases_ne = imap.gather()
print(f"[{NODE}] ifaces seen:", {i.dev: i.ip4() for i in ifaces})
cls = classify(ifaces, leases_nonempty=leases_ne)
print(f"[{NODE}] FROGNET_INTERFACES:", cls.get("FROGNET_INTERFACES"))
devs = cls["FROGNET_INTERFACES"].split()
seeds = dict(active_devs=devs, dev_ip={i.dev:i.ip4() for i in ifaces},
             leases=live._leases(), disk_cache=[], active_states=live._active_states(),
             wg_kernel_nets=live._wg_kernel_nets(devs), arp_neigh=live._arp_neigh(devs))
run_merge(disc, k, seeds, {}, local=(IDENT,""), bringup_peers=[],
          prior_names={}, concurrent_attempt=False, logger=log)
print(f"=== [{NODE}] DERIVED TABLE ==="); print(k.route_show().replace(";","\n"))
