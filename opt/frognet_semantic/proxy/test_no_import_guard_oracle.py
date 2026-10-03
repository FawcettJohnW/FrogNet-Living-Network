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
around an import substitutes SILENTLY: the handler stubs, flags, passes or
returns without raising and without reporting what failed.

[IMPORT_GUARD_V2] John, 2026-09-25: "In some cases the guards are legit and there
is work done in the handler. These are valid uses." V1 failed every guard on a
first-party module, whatever its handler did, and read any assignment as a
swallow -- so a handler that raises a named error or logs the failure and takes
a real path failed exactly like `except: pass`. The test is now what the rule is
about: a guard is valid when its handler RAISES or REPORTS (print / a logger /
a recorded check / stderr). Rulings the same day: discovery is left alone;
daemon template init aborts; game_origin removed; torch is required. Run from the tree root:

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
    "tests/test_sotf_handler.py": "test harness stubs lz4 deliberately (same as test_channel_sets)",
    "proxy/test_no_import_guard_oracle.py": "this file",
}
EXEMPT_DIRS = ("internet_tunnels_v3",
               # stale copies tools left INSIDE the tree; not running code (they should not be in the tree at all)
               ".frognet-ai-backup-", ".netbench-backup-", "_fix_meta")
# John, 2026-09-25: "Leave discovery alone." Discovery's own guards, and the election / role machinery it runs on,
# are not changed by this gate. They are REPORTED every run as PENDING so they are never out of sight.
PENDING_DIRS = ("discovery",)
PENDING = {
    "core/live.py": "twin of discovery/live.py (discovery)",
    "core/frognet_role_elect.py": "election (discovery)",
    "core/unrest_handler.py": "role publishing (discovery)",
    "core/role_publish.py": "role publishing (discovery)",
    "core/sotf_handler.py": "guard exists for discovery's election context (its own comment)",
    "core/frognet_tuples.py": "platform guards: the module must import on the Windows Communicator",
    "agent_workload/tuplespace/torch_backend.py": "native-extension fallback to the Python Work: not yet ruled",
    "frognet_route/committer.py": "route-commit spine: stdlib logging when frognet_log is absent -- not yet ruled",
    "frognet_route/frognet_trace.py": "route-commit spine: NullHandler fallback can drop TRACE silently -- not yet ruled",
    "frognet_trace.py": "copy of frognet_route/frognet_trace.py -- not yet ruled",
}
_REPORTS = ("print", "log", "logger", "error", "warning", "warn", "info", "exception", "critical", "ck", "check",
            "append", "write", "fail")


def _raises_or_reports(handler):
    """[IMPORT_GUARD_V2] The handler raises, names the failure somewhere a person will see it, writes the caught
    exception into what it returns (in-band: "UNRESOLVED(<error>)"), or imports the same thing from another location
    with nothing stubbed -- where a second failure raises."""
    body = ast.Module(body=handler.body, type_ignores=[])
    if handler.name and any(isinstance(n, ast.Name) and n.id == handler.name for n in ast.walk(body)):
        return True                                   # the caught exception is used: it is named, not dropped
    if (all(isinstance(b, (ast.Import, ast.ImportFrom, ast.Assign, ast.Expr, ast.If)) for b in handler.body)
            and any(isinstance(n, (ast.Import, ast.ImportFrom)) or (isinstance(n, ast.Attribute) and n.attr == "spec_from_file_location")
                    for n in ast.walk(body))
            and not any(isinstance(n, (ast.Pass, ast.FunctionDef, ast.ClassDef, ast.Return)) for n in ast.walk(body))):
        return True                                   # the same module from another location; a second failure raises
    for n in ast.walk(ast.Module(body=handler.body, type_ignores=[])):
        if isinstance(n, ast.Raise):
            return True
        if isinstance(n, ast.Call):
            f = n.func
            name = f.attr if isinstance(f, ast.Attribute) else (f.id if isinstance(f, ast.Name) else "")
            if name in _REPORTS:
                return True
    return False

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
    violations, pending = [], []
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
                    if _raises_or_reports(h):
                        continue                          # a guard that does its job: valid
                    if not (fp or _swallows(h)):
                        continue
                    what = (f"guards first-party import {fp}" if fp else f"substitutes for import {names}")
                    msg = f"{rel}:{h.lineno} `except {ty}` {what} silently"
                    top = rel.split(os.sep)[0]
                    if top in PENDING_DIRS or rel in PENDING:
                        pending.append(msg + "   [" + PENDING.get(rel, "discovery: left alone, ruled") + "]")
                    else:
                        violations.append(msg)
    for v in sorted(pending):
        print("PENDING  " + v)

    for v in sorted(violations):
        print("FAIL  " + v)
    if violations:
        print(f"\n{len(violations)} guarded-import violation(s)")
        return 1
    print("PASS  every import guard outside the pending list raises or reports (%d pending, listed above)" % len(pending))
    return 0


if __name__ == "__main__":
    sys.exit(main())
