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
"""
mode_preflight.py - one-shot dependency check for the mode 0/1/2 test chain.

Run this on the box BEFORE the tests. It reports, for every module the SotF
test scripts import (transitively), whether it RESOLVES and from which file -
so a partial deployment shows up as a list of MISSING modules in a single run
instead of one ModuleNotFoundError at a time.

    cd /opt/frognet_semantic
    python3 simulation/mode_preflight.py

Exit 0 = everything the chain needs is present. Exit 1 = something's missing
(the names printed are exactly what to add). It does not run any test or touch
the network; it only attempts imports.
"""
from __future__ import annotations
import importlib
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_PARENT = os.path.dirname(_HERE)
for p in (_PARENT, _HERE):
    if p and p not in sys.path:
        sys.path.insert(0, p)

# The closure the mode 0/1/2 chain depends on. simulation/-local first, then
# the core handler family the SotF + ladder paths reach for.
PROBE = [
    # simulation-local (shipped in the overlay)
    "transport_sim_tier",
    "transport_factories",
    "sotf_media_tier",
    "remote_test_daemon",
    "sotf_video_stream_test",
    "sotf_degradation_ladder",
    # core runtime (should already be on the box; flag if not)
    "core.codec",
    "core.semcache_wire",
    "core.json_handler",
    "core.text_handler",
    "core.html_handler",
    "core.xml_handler",
]

def main() -> int:
    found, missing = [], []
    for name in PROBE:
        try:
            m = importlib.import_module(name)
            path = getattr(m, "__file__", "(builtin)")
            found.append((name, path))
        except ModuleNotFoundError as e:
            missing.append((name, f"ModuleNotFoundError: {e.name}"))
        except Exception as e:  # import-time error that isn't a missing module
            missing.append((name, f"{type(e).__name__}: {e}"))

    print("=== mode preflight: dependency resolution ===")
    for name, path in found:
        print(f"  [FOUND]   {name:28s} {path}")
    for name, why in missing:
        print(f"  [MISSING] {name:28s} {why}")

    if missing:
        names = sorted({why.split(":")[-1].strip() for _, why in missing
                        if "ModuleNotFoundError" in why})
        print(f"\nMISSING {len(missing)} module(s). Names to add: "
              f"{', '.join(names) if names else '(see errors above)'}")
        return 1
    print("\nALL DEPENDENCIES PRESENT - mode 0/1/2 chain is satisfied.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
