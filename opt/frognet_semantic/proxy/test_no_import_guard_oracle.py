#!/opt/frognet_semantic/venv/bin/python3
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
"""test_no_import_guard_oracle.py — [NO_FALLBACK_V1] Tier 2 gate.

A guarded import on a FIRST-PARTY module is the failure mode the transition
primer records twice: the guard fires, the layer becomes a no-op that returns
the "nothing to do" answer, and nothing in the log distinguishes that from a
genuine miss.

This oracle is structural. It parses the tree and fails if any try/except
around an import either (a) swallows into a stub/flag/pass, or (b) catches a
first-party module at all. Run from the tree root:

    python3 proxy/test_no_import_guard_oracle.py
"""
import ast
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# First-party roots. An import of any of these must never be guarded.
FIRST_PARTY = ("core", "proxy", "daemon", "frognet_log", "frognet_trace",
               "frognet_avhost", "frognet_tuples", "frognet_service_hosts")

# Files exempt, with the reason. Tests may stub their own dependencies; the v3
# client tree is a separate deliverable and is not covered by this rev.
EXEMPT = {
    "proxy/test_channel_sets.py": "test harness stubs mysql/lz4 deliberately",
    "proxy/test_no_import_guard_oracle.py": "this file",
}
EXEMPT_DIRS = ("internet_tunnels_v3",)

SKIP = (".bak", ".backup", ".rej", ".v2", ".pysystemctl")


def _imported_names(try_node):
    out = []
    for s in try_node.body:
        if isinstance(s, ast.Import):
            out += [a.name for a in s.names]
        elif isinstance(s, ast.ImportFrom):
            out.append(s.module or "")
    return out


def _swallows(handler):
    """True if the handler substitutes for the import rather than reporting."""
    for b in handler.body:
        if isinstance(b, (ast.Pass, ast.FunctionDef)):
            return True          # `pass`, or a stub function definition
        if isinstance(b, ast.Assign):
            return True          # _HAS_X = False / _mod = None
        if isinstance(b, ast.Return):
            return True          # bail out as if there were nothing to do
    return False


def main():
    violations = []
    for d, _, fs in os.walk(ROOT):
        if any(x in d for x in EXEMPT_DIRS):
            continue
        for f in fs:
            if not f.endswith(".py") or any(s in f for s in SKIP):
                continue
            p = os.path.join(d, f)
            rel = os.path.relpath(p, ROOT)
            if rel in EXEMPT:
                continue
            try:
                tree = ast.parse(open(p, errors="replace").read())
            except SyntaxError as e:
                violations.append(f"{rel}: does not parse: {e}")
                continue
            for n in ast.walk(tree):
                if not isinstance(n, ast.Try):
                    continue
                names = _imported_names(n)
                if not names:
                    continue
                fp = [m for m in names
                      if m and m.split(".")[0] in FIRST_PARTY]
                for h in n.handlers:
                    ty = ast.unparse(h.type) if h.type else "BARE"
                    if fp:
                        violations.append(
                            f"{rel}:{h.lineno} guards first-party import "
                            f"{fp} with `except {ty}`")
                    elif _swallows(h):
                        violations.append(
                            f"{rel}:{h.lineno} `except {ty}` substitutes for "
                            f"import {names}")

    for v in sorted(violations):
        print("FAIL  " + v)
    if violations:
        print(f"\n{len(violations)} guarded-import violation(s)")
        return 1
    print("PASS  no first-party import is guarded; "
          "no import guard substitutes a stub")
    return 0


if __name__ == "__main__":
    sys.exit(main())
