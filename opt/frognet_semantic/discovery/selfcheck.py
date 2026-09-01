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
"""selfcheck - run every oracle/structural proof in-process. Exit 0 iff all pass.
This is the first thing to run after install: it confirms the port reproduces the
captured merge log on THIS machine. It touches nothing on the system."""
import importlib

TESTS = [
    "test_kernel_oracle", "test_kernel_parity", "test_routes_oracle", 
    "test_hosts_oracle", "test_fixdefault_oracle", "test_fixdefault_modeb",
    "test_runmerge_oracle",
    "test_propagate_oracle", "test_mapinterfaces_oracle",
    "test_healthcheck_oracle", "test_fabric_integration",
    "test_snapshot_struct", "test_real_backends", "sim.proof_plane",
]

def run():
    import os as _os
    _os.environ.setdefault("FROGNET_OFFLINE_TUPLES", "1")  # self-tests are offline/deterministic
    passed = failed = 0
    for name in TESTS:
        mod = importlib.import_module(f"discovery.{name}")
        rc = mod.main()
        ok = (rc == 0)
        print(f"[{'PASS' if ok else 'FAIL'}] {name}")
        passed += ok
        failed += (not ok)
    print(f"\n{passed}/{len(TESTS)} suites pass"
          + (f", {failed} FAIL" if failed else " - all green"))
    return 0 if failed == 0 else 1

if __name__ == "__main__":
    import sys
    sys.exit(run())
