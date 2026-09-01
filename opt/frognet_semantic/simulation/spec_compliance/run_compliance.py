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
run_compliance.py - codex language compliance gate (PRIMER 2).

Runs every per-handler spec-compliance suite and returns 0 iff all gate.
Wire into simulation/run_all.py as TIER C. Each suite's main() returns
0 (all positives round-trip, all negatives behave as documented, all
security negatives reject) or 1.
"""
from __future__ import annotations

import sys

from simulation.spec_compliance import _harness  # path/stubs side-effects
from simulation.spec_compliance.test_json_compliance import main as json_main
from simulation.spec_compliance.test_xml_compliance import main as xml_main
from simulation.spec_compliance.test_html_compliance import main as html_main
from simulation.spec_compliance.test_text_compliance import main as text_main


def main() -> int:
    print("=== TIER C - codex language compliance (PRIMER 2) ===\n")
    rc = 0
    for fn in (json_main, xml_main, html_main, text_main):
        rc |= fn()
        print()
    print("=== compliance gate:", "PASS ===" if rc == 0 else "FAIL ===")
    return 1 if rc else 0


if __name__ == "__main__":
    sys.exit(main())
