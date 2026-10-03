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
"""surface_lispers.py -- P5: the externally reachable map-server / map-resolver surface of lispers.net, and how much of
it the acceptance suite runs.

Entry points are read from lispers.net's own source:
  UDP  -- lisp.py lisp_parse_packet: the handler called for each control-message type a map-server / map-resolver
          receives (Map-Request, Map-Register, Map-Notify-Ack, Map-Referral, Info-Request, ECM). Map-Reply, Map-Notify
          and Info-Reply are xTR-side and listed separately.
  CONFIG -- the command tables of the map-server (lisp-ms.py lisp_ms_commands) and map-resolver (lisp-mr.py
          lisp_mr_commands): each command's handler.
From each entry point, a static call graph (ast: calls by name, and lisp.<name> / self-free attribute calls resolved by
name) gives the functions reachable. coverage.py's JSON says which lines ran. Reported: per entry point, reachable
functions, how many ran, lines run / lines reachable; then every reachable function that never ran, largest first.

usage: surface_lispers.py LISPERS_SOURCE_DIR COVERAGE_JSON > surface.md
"""
import ast, json, os, re, sys
src, cov_path = sys.argv[1], sys.argv[2]
FILES = ["lisp.py", "lisp-ms.py", "lisp-mr.py", "lispconfig.py", "lisp-core.py"]
cov = json.load(open(cov_path))["files"]

funcs = {}          # name -> list of (file, first, last, body line numbers)
calls = {}          # (file, name) -> set of called names
for f in FILES:
    tree = ast.parse(open(os.path.join(src, f)).read())
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            lines = set()
            for sub in ast.walk(node):
                if isinstance(sub, ast.stmt) and sub is not node: lines.add(sub.lineno)
            funcs.setdefault(node.name, []).append((f, node.lineno, getattr(node, "end_lineno", node.lineno), lines))
            called = set()
            for sub in ast.walk(node):
                if isinstance(sub, ast.Call):
                    fn = sub.func
                    if isinstance(fn, ast.Name): called.add(fn.id)
                    elif isinstance(fn, ast.Attribute): called.add(fn.attr)
                # handlers passed as values (threading.Timer(interval, f), command tables): names referenced
                if isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Load) and sub.id.startswith("lisp_"): called.add(sub.id)
            calls[(f, node.name)] = called

def executed(f):
    for k in cov:
        if os.path.basename(k) == f: return set(cov[k]["executed_lines"])
    return set()
EXE = {f: executed(f) for f in FILES}

def closure(entry):
    seen, todo = set(), [entry]
    while todo:
        n = todo.pop()
        if n in seen or n not in funcs: continue
        seen.add(n)
        for (f, _a, _b, _l) in funcs[n]: todo.extend(calls.get((f, n), ()))
    return seen

def ran_fn(n): return any(lines & EXE[f] for (f, _a, _b, lines) in funcs.get(n, []))
def stats(names):
    ran = run_lines = all_lines = 0; never = []
    for n in names:
        if ran_fn(n): ran += 1
        for (f, a, b, lines) in funcs[n]:
            e = lines & EXE[f]; all_lines += len(lines); run_lines += len(e)
            if not e: never.append((len(lines), n, f, a))
    return ran, run_lines, all_lines, never

def command_handlers(f, table):
    text = open(os.path.join(src, f)).read(); i = text.index(table + " = {"); j = text.index("\n}", i)
    return [(c, h.split(".")[-1]) for c, h in re.findall(r'"(lisp [^"]+)"\s*:\s*\[\s*([\w.]+)', text[i:j])]

udp = [("Map-Request", "lisp_process_map_request"), ("Map-Register", "lisp_process_map_register"),
       ("Map-Notify-Ack", "lisp_process_map_notify_ack"), ("Map-Referral", "lisp_process_map_referral"),
       ("Info-Request", "lisp_process_info_request"), ("ECM (Encapsulated Control Message)", "lisp_process_ecm")]
xtr = [("Map-Reply", "lisp_process_map_reply"), ("Map-Notify (unicast)", "lisp_process_unicast_map_notify"),
       ("Info-Reply", "lisp_process_info_reply")]
cfg = [("map-server", c, h) for c, h in command_handlers("lisp-ms.py", "lisp_ms_commands")] + \
      [("map-resolver", c, h) for c, h in command_handlers("lisp-mr.py", "lisp_mr_commands")]

print("# lispers.net map-server / map-resolver surface -- coverage under the acceptance suite\n")
print("Source: %s. Coverage: %s. Reachability: static call graph from each entry point (by function name).\n" % (src, os.path.basename(cov_path)))
allreach = set()
def table(title, rows):
    print("## %s\n\n| entry point | handler | handler ran | reachable functions | ran | lines ran / reachable |\n|---|---|---|---|---|---|" % title)
    for label, h in rows:
        r = closure(h); allreach.update(r); ran, rl, al, _ = stats(r)
        print("| %s | `%s` | %s | %d | %d | %d / %d (%.0f%%) |" % (label, h, "yes" if ran_fn(h) else "**NO**", len(r), ran, rl, al, 100.0 * rl / al if al else 0))
    print()
table("UDP -- map-server / map-resolver", udp)
table("UDP -- xTR side (not driven by this suite)", xtr)
table("Configuration -- lisp.config commands of the map-server and map-resolver", [("%s: `%s`" % (role, c), h) for role, c, h in cfg])
ran, rl, al, never = stats(allreach - set(h for _, h in xtr))
print("## Totals (map-server / map-resolver surface)\n\n%d reachable functions, %d ran; %d of %d reachable lines ran (%.0f%%).\n" % (len(allreach), ran, rl, al, 100.0 * rl / al if al else 0))
print("## Reachable functions that never ran (largest first)\n\n| lines | function | file:line |\n|---|---|---|")
for n_lines, name, f, a in sorted(never, reverse=True)[:80]: print("| %d | `%s` | %s:%d |" % (n_lines, name, f, a))
