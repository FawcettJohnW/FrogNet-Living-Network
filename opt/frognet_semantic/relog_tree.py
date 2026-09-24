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
relog_tree.py - fold a source subtree onto the frognet_log spine.

Two operations:
  1. SAFE / automatic: rewrite `logging.getLogger("X")` -> `get_logger("X")`
     and ensure `from frognet_log import get_logger` is imported. This reparents
     every module logger under the "frognet" tree so FROGNET_LOG_LEVEL /
     FROGNET_TRACE / frognet_log.set_level() control it from one place.
  2. REPORT only: list every `print(...)` site. These are NOT auto-rewritten,
     because a print to stdout is often product output (CLI data, TSV dumps),
     not a diagnostic - only a human can tell which. The report tags likely
     diagnostics (print(..., file=sys.stderr)) separately.

Default is dry-run. Pass --apply to write changes. Idempotent: running twice
is a no-op (already-converted files are skipped).

Usage:
    python3 relog_tree.py /opt/frognet_semantic/daemon            # dry run
    python3 relog_tree.py /opt/frognet_semantic/daemon --apply
    python3 relog_tree.py /opt/frognet_semantic /usr/local/bin/frognet_monitor_py --apply
"""
import argparse
import os
import re
import sys

GETLOGGER_RE = re.compile(r'logging\.getLogger\(')
IMPORT_LINE = "from frognet_log import get_logger\n"


def process_file(path, apply):
    with open(path, "r", encoding="utf-8") as f:
        src = f.read()
    lines = src.splitlines(keepends=True)

    getlogger_hits = [i for i, l in enumerate(lines) if GETLOGGER_RE.search(l)]
    print_sites = []
    for i, l in enumerate(lines):
        if re.search(r'\bprint\(', l):
            kind = "stderr-diag" if "file=sys.stderr" in l else "stdout"
            print_sites.append((i + 1, kind, l.strip()))

    changed = False
    if getlogger_hits:
        # Rewrite getLogger -> get_logger
        new_lines = [GETLOGGER_RE.sub("get_logger(", l) for l in lines]
        # Ensure the import exists (insert after the first import block line).
        joined = "".join(new_lines)
        if "from frognet_log import get_logger" not in joined:
            insert_at = 0
            for i, l in enumerate(new_lines):
                if l.startswith("import ") or l.startswith("from "):
                    insert_at = i + 1
            new_lines.insert(insert_at, IMPORT_LINE)
        if "".join(new_lines) != src:
            changed = True
            if apply:
                with open(path, "w", encoding="utf-8") as f:
                    f.write("".join(new_lines))

    return getlogger_hits, print_sites, changed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("roots", nargs="+")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    total_logger = total_print = total_changed = 0
    report = []
    for root in args.roots:
        for dirpath, _dirs, files in os.walk(root):
            if "__pycache__" in dirpath:
                continue
            for fn in files:
                if not fn.endswith(".py") or fn in ("frognet_log.py", "relog_tree.py"):
                    continue
                p = os.path.join(dirpath, fn)
                gl, ps, ch = process_file(p, args.apply)
                if gl:
                    total_logger += len(gl)
                if ps:
                    total_print += len(ps)
                    report.append((p, ps))
                if ch:
                    total_changed += 1

    mode = "APPLIED" if args.apply else "DRY-RUN (use --apply to write)"
    print(f"=== relog_tree {mode} ===")
    print(f"getLogger->get_logger rewrites in {total_changed} files "
          f"({total_logger} call sites)")
    print(f"print() sites needing human review: {total_print}\n")
    for p, ps in report:
        print(f"  {p}")
        for lineno, kind, text in ps:
            tag = "DIAG?" if kind == "stderr-diag" else "stdout"
            print(f"    L{lineno:<4} [{tag}] {text[:90]}")
    print("\nNote: stderr-diag prints are likely log.warning/error candidates.")
    print("      stdout prints are usually product output - leave unless proven diagnostic.")


if __name__ == "__main__":
    main()
