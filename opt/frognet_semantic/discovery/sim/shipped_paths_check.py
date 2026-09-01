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
sim/shipped_paths_check.py - [A_MISSING_THING_MUST_NEVER_LOOK_LIKE_AN_ANSWER_V1]

A guard on a path that never ships is not a safety check. It is a switched-off
branch that reads, in the log, exactly like a completed step.

Two found on 2026-08-29, both on the discovery trigger:

  frognet-netstart:285   if [[ -x /usr/local/bin/runMerge ]]      -> runMerge.bash
  frognet-roam:438,464   if [[ -x /usr/local/bin/sync_interfaces ]] -> never existed

Both said "Triggering discovery..." inside a test that could not pass, so a node
changed network mode or roamed onto a new SSID and never merged, while the line
above logged success.

This walks every shipped shell script and fails on any `[ -x/-f/-e <path> ]` test
against a /usr/local or /opt/frognet_semantic path the tree does not contain.
Paths a phase CREATES (the venv, blob_cache) are not guards on shipped files and
do not appear as literals in these tests.
"""
from __future__ import annotations
import os, re

FAILS = []
_P = r'/(?:usr/local/(?:bin|sbin|lib)|opt/frognet_semantic)/[A-Za-z0-9_./+-]+'
# A guard written against a literal path.
GUARD = re.compile(r'\[\[?\s*-[xfre]\s+"?(' + _P + r')"?\s*\]\]?')
# ...and one written against a variable holding a literal path. The first version
# of this check only matched literals, so refactoring `[[ -x /usr/local/bin/x ]]`
# into `V=/usr/local/bin/x; [[ -x "$V" ]]` made it pass while the branch stayed
# just as dead. Verified by reintroducing the netstart bug through a variable: the
# literal-only form reported PASS.
ASSIGN = re.compile(r'^\s*([A-Za-z_][A-Za-z0-9_]*)=(?:"?)(' + _P + r')(?:"?)\s*$', re.M)
GUARD_VAR = re.compile(r'\[\[?\s*-[xfre]\s+"?\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?"?\s*\]\]?')


def _tree():
    here = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.dirname(os.path.dirname(here))


def run():
    tree = _tree()
    roots = [os.path.join(tree, p) for p in
             ("usr/local/bin", "usr/local/sbin", "usr/local/lib")]
    roots = [r for r in roots if os.path.isdir(r)]
    if not roots:
        print("  [SKIP] no shipped script directories under this tree")
        return

    scanned = 0
    bad = []
    for root in roots:
        for dp, _, fns in os.walk(root):
            if "python3.11" in dp or "__pycache__" in dp:
                continue
            for fn in fns:
                f = os.path.join(dp, fn)
                try:
                    txt = open(f, encoding="utf-8", errors="replace").read()
                except OSError:
                    continue
                if not txt.startswith("#!"):
                    continue
                scanned += 1
                # var -> literal path, for guards written through a variable
                env = {m.group(1): m.group(2) for m in ASSIGN.finditer(txt)}
                sites = [(m.start(), m.group(1)) for m in GUARD.finditer(txt)]
                sites += [(m.start(), env[m.group(1)])
                          for m in GUARD_VAR.finditer(txt) if m.group(1) in env]
                for pos, p in sites:
                    # /opt/frognet_semantic/venv is BUILT by install phase C2, not
                    # shipped - guarding on it is correct and its absence from the
                    # tree is expected. Nothing else under these roots is created
                    # at install time.
                    if p.startswith("/opt/frognet_semantic/venv"):
                        continue
                    if not os.path.exists(os.path.join(tree, p.lstrip("/"))):
                        line = txt[:pos].count("\n") + 1
                        bad.append(f"{os.path.relpath(f, tree)}:{line} guards on {p}, "
                                   f"which the tree does not ship - the branch can never run")
    print(f"  scanned {scanned} shipped script(s)")
    if bad:
        FAILS.extend(bad)
        print("  [FAIL] every guarded path is one the release ships")
        for b in bad:
            print(f"         - {b}")
    else:
        print("  [PASS] every guarded path is one the release ships")


def main():
    print("=== guards resolve to files that actually ship ===")
    run()
    print("\n" + ("ALL SHIPPED-PATH CHECKS PASS" if not FAILS
                  else f"SHIPPED-PATH CHECKS FAILED: {len(FAILS)}"))
    return 0 if not FAILS else 1


if __name__ == "__main__":
    raise SystemExit(main())
