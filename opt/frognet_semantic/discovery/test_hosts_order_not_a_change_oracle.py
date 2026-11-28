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
test_hosts_order_not_a_change_oracle.py - the same hosts in another order are the same hosts.

[HOSTS_ORDER_IS_NOT_A_CHANGE_V1] Found 2026-11-28 on four clean-installed nodes in a line
(H4 - H2 - H1 - H3). The network was converged; `runMerge.bash` was run by hand on H3, twice.
Both passes changed no route and no name, and both said

    TABLE_FINGERPRINT phase=after routes=5 changed=0 added=0 removed=0
    sync_required set reason=hosts_changed
    PROP converged=YES chain_dirty=1 action=propogateNotification

and H1 ran a whole merge for each (26 s and 29 s) to find nothing. The only difference between
H3's /etc/sentinels/frognet_hosts before and after was the ORDER of two nodes:

    <  10.72.72.1 H2 FrogNetHost.H2          >  10.74.74.1 H4 FrogNetHost.H4
    <  10.74.74.1 H4 FrogNetHost.H4          >  10.72.72.1 H2 FrogNetHost.H2

frognet_hosts is written in the order the walk found the peers (orchestrate.merge appends
disc.hosts.added as it stands), and two peers behind the same neighbour are probed in parallel,
so their order is whichever answered first. /etc/hosts never showed it: build_etc_hosts sorts.
live._topology_changed compared the two files line by line, in order.

What must hold:
  - the same topology lines in another order            -> NOT a change (no notification)
  - a change confined to <role>.frognet lines           -> NOT a change ([SERVICE_HOSTS_NO_SYNC_V1], unchanged)
CONTROLS (each must still be a change, or the check above proves nothing):
  - a node added, a node gone, a node renamed, a node at another address
"""
import os as _os
import sys

_os.environ.setdefault("FROGNET_OFFLINE_TUPLES", "1")

from discovery.live import _topology_changed

PROBS = []


def check(ok, label):
    print(f"  {'PASS' if ok else 'FAIL'} {label}")
    if not ok:
        PROBS.append(label)


def _file(*lines):
    return "\n".join(lines) + "\n"


H3 = ("10.73.73.1 FrogNetHost.H3", "10.73.73.2 FrogNetAdmin.H3")
H1 = ("10.71.71.1 H1 FrogNetHost.H1", "10.71.71.2 FrogNetAdmin.H1")
H2 = ("10.72.72.1 H2 FrogNetHost.H2", "10.72.72.2 FrogNetAdmin.H2")
H4 = ("10.74.74.1 H4 FrogNetHost.H4", "10.74.74.2 FrogNetAdmin.H4")
ROLES_A = ("10.74.74.1 databasehost_control.frognet", "10.74.74.1 databasehost.frognet")
ROLES_B = ("10.74.74.1 databasehost_control.frognet", "10.73.73.1 databasehost.frognet")


def main():
    print("=== the same hosts in another order ===")
    # the two files H3 wrote on 2026-11-28, as found
    before = _file(*H3, *H1, *H2, *H4)
    after = _file(*H3, *H1, *H4, *H2)
    check(not _topology_changed(before, after),
          "field case: H2 and H4 found in the other order is NOT a topology change")
    check(not _topology_changed(after, before),
          "and not in the other direction either")
    check(not _topology_changed(_file(*H3, *H1, *H2, *H4), _file(*H4, *H2, *H1, *H3)),
          "every node in another order is NOT a topology change")
    check(not _topology_changed(before, before), "identical files are not a change")

    print("\n=== role lines alone (unchanged rule) ===")
    check(not _topology_changed(_file(*H3, *H1, *ROLES_A), _file(*H3, *H1, *ROLES_B)),
          "a change confined to <role>.frognet lines is NOT a topology change")
    check(not _topology_changed(_file(*H3, *H1, *ROLES_A), _file(*H1, *ROLES_B, *H3)),
          "role lines changed AND the nodes in another order: still NOT a topology change")

    print("\n=== CONTROLS: a real change is still a change ===")
    check(_topology_changed(_file(*H3, *H1, *H2), _file(*H3, *H1, *H2, *H4)),
          "a node ADDED is a topology change")
    check(_topology_changed(_file(*H3, *H1, *H2, *H4), _file(*H3, *H1, *H2)),
          "a node GONE is a topology change")
    check(_topology_changed(_file(*H3, *H1, *H2, *H4), _file(*H3, *H1, *H4)),
          "a node gone from the middle, the rest in another order, is a topology change")
    check(_topology_changed(_file(*H3, *H1, *H2),
                            _file(*H3, *H1, "10.72.72.1 H2b FrogNetHost.H2b", "10.72.72.2 FrogNetAdmin.H2b")),
          "a node RENAMED is a topology change")
    check(_topology_changed(_file(*H3, *H1, *H2),
                            _file(*H3, *H1, "10.75.75.1 H2 FrogNetHost.H2", "10.75.75.2 FrogNetAdmin.H2")),
          "a node at ANOTHER ADDRESS is a topology change")
    check(_topology_changed("", _file(*H3)),
          "the first hosts file a clean machine writes is a topology change")

    print()
    if PROBS:
        print("HOSTS-ORDER ORACLE FAILED")
        return 1
    print("ALL HOSTS-ORDER CHECKPOINTS PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
