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
sim/role_registry_lxmlfree_check.py - proves role election does NOT depend on lxml.
With lxml made unimportable: core.role_registry still imports and yields the databasehost
handler; core.format_registry still FAILS (the lxml-backed format handlers -
proxy concern); and the service-host loader returns the role handlers via the lxml-free path.
"""
from __future__ import annotations
import os, sys, importlib

_HERE = os.path.abspath(__file__)
_FS = os.path.dirname(os.path.dirname(os.path.dirname(_HERE)))     # opt/frognet_semantic
sys.path.insert(0, _FS)            # [CORE_IMPORTS_V1] core.*, as shipped; the old Communicator bundle dir is retired

FAILS = []
def check(label, problems):
    if problems:
        FAILS.extend(problems); print(f"  [FAIL] {label}")
        for p in problems: print(f"         - {p}")
    else:
        print(f"  [PASS] {label}")

class _BlockLxml:
    """Meta-path finder that makes `import lxml[...]` raise ModuleNotFoundError."""
    def find_spec(self, name, path=None, target=None):
        if name == "lxml" or name.startswith("lxml."):
            raise ModuleNotFoundError(f"No module named '{name}'")
        return None

def run():
    # drop any already-imported core.* so the blocked re-import is honest
    for m in list(sys.modules):
        if m == "core" or m.startswith("core.") or m == "lxml" or m.startswith("lxml."):
            del sys.modules[m]
    sys.meta_path.insert(0, _BlockLxml())
    try:
        # 1. role_registry imports lxml-free and carries the role handler
        probs = []
        try:
            rr = importlib.import_module("core.role_registry")
            # [ROLE_HANDLERS_1ROLE_V1] databasehost is the one elected role (boardgame went with the games, 2026-09-29;
            # mediahost with the old Python Communicator, T2 2026-10-12); aihost is intentionally NOT a handler -
            # exact set so a stray role still trips this.
            if set(rr.ROLE_HANDLERS) != {"databasehost"}:
                probs.append(f"ROLE_HANDLERS wrong: {set(rr.ROLE_HANDLERS)}")
            if rr.role_handler("databasehost") is None:
                probs.append("role_handler() returned None for databasehost")
            if rr.role_handler("mediahost") is not None:
                probs.append("role_handler('mediahost') answered: the retired role is still registered")
        except Exception as e:
            probs.append(f"core.role_registry failed to import without lxml: {e!r}")
        check("[LXMLFREE] core.role_registry imports + yields databasehost (only) without lxml", probs)

        # 2. format_registry STILL fails without lxml (proves the coupling is real,
        #    and that role_registry is the correct lxml-free seam)
        probs = []
        try:
            importlib.import_module("core.format_registry")
            probs.append("core.format_registry imported without lxml - block ineffective / coupling gone unexpectedly")
        except ModuleNotFoundError:
            pass  # expected: it pulls the lxml-backed xml/html handlers
        check("[COUPLED] core.format_registry still needs lxml (proxy path) - role path is separate", probs)

        # 3. the service-host loader returns the role handlers via the lxml-free path
        probs = []
        try:
            fsh = importlib.import_module("core.frognet_service_hosts")
            logs = []
            handlers = fsh._role_handlers(log=lambda s: logs.append(s))
            if set(handlers) != {"databasehost"}:  # [ROLE_HANDLERS_1ROLE_V1]
                probs.append(f"_role_handlers returned {set(handlers)}")
            if any("role_registry_import_failed" in l for l in logs):
                probs.append(f"core.role_registry failed (should import cleanly): {logs}")
        except Exception as e:
            probs.append(f"_role_handlers raised without lxml: {e!r}")
        check("[ELECT] service-host loader returns the role handler cleanly from core.role_registry", probs)
    finally:
        sys.meta_path = [f for f in sys.meta_path if not isinstance(f, _BlockLxml)]

def main():
    print("=== role election independent of lxml ===")
    run()
    print("\n" + ("ALL ROLE-REGISTRY-LXMLFREE CHECKS PASS" if not FAILS
                  else f"ROLE-REGISTRY-LXMLFREE CHECKS FAILED: {len(FAILS)}"))
    return 0 if not FAILS else 1

if __name__ == "__main__":
    sys.exit(main())
