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
"""frogsim_runagain_external - [RUNAGAIN_IS_EXTERNAL_ONLY_V1].

runAgain answers one question: did something arrive from OUTSIDE, while this
merge was in flight, that may have changed the routing table after this pass had
already read it? A DHCP lease, an NM interface event, a neighbour's
notification, a concurrent runMerge that bailed on the held lock.

It is not a convergence loop, and it is not the pass's opinion of its own work.
So the contract is:

  - runMerge.bash clears the sentinel at the top of the pass.
  - the merge never writes it.
  - anything present at the end of the pass therefore arrived DURING the pass,
    from another process, by definition -- and that, and only that, re-runs.

The consequence worth proving is the cross product: a pass that rewrote the
entire table does NOT re-run if nothing external arrived, and a pass that
changed nothing at all DOES re-run if something did. The old code could not
express either cell -- it re-ran on its own writes.

This models the sentinel mechanics the bash wrapper and live.main() implement,
against a temp sentinel dir, so the file-level contract is exercised rather than
asserted in prose.
"""
import os
import sys
import tempfile

sys.path.insert(0, os.environ.get(
    "FROGNET_SEMANTIC_ROOT",
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

SENT = tempfile.mkdtemp(prefix="frogsim_runagain_")
RUN_AGAIN = os.path.join(SENT, "runAgain")

FAILS = []


def check(label, ok, detail=""):
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f"  - {detail}" if detail else ""))
    if not ok:
        FAILS.append(label)


def _clear_at_top():
    """runMerge.bash: rm -f "$RUN_AGAIN" before the merge runs."""
    try:
        os.remove(RUN_AGAIN)
    except FileNotFoundError:
        pass


def _external_poke():
    """Another process: nm-dispatcher, dnsmasq dhcp-script, the merge
    accumulator's set_runAgain branch, or a concurrent runMerge hitting the
    held lock."""
    open(RUN_AGAIN, "w").close()


def _pass(table_changed, external_during, external_before=False):
    """One merge pass. Returns the bash wrapper's re-invoke decision.

    table_changed is what the pass did to the routing table -- deliberately
    unused in the decision, which is the entire point.
    """
    if external_before:
        _external_poke()          # arrived before the pass started: stale
    _clear_at_top()
    # ... merge runs here; it writes sync_required, never runAgain ...
    if external_during:
        _external_poke()
    return os.path.exists(RUN_AGAIN)


def main():
    print("=== runAgain IS EXTERNAL ONLY ===")

    print("\nthe cross product")
    check("changed the whole table, nothing external -> DOES NOT re-run",
          _pass(table_changed=True, external_during=False) is False)
    check("changed nothing, external arrived mid-pass -> DOES re-run",
          _pass(table_changed=False, external_during=True) is True)
    check("changed nothing, nothing external -> does not re-run",
          _pass(table_changed=False, external_during=False) is False)
    check("changed the table AND external arrived -> re-runs (for the external)",
          _pass(table_changed=True, external_during=True) is True)

    print("\nclear-at-top is what makes 'during' mean during")
    check("a poke that arrived BEFORE the pass is cleared, not honoured twice",
          _pass(table_changed=True, external_during=False,
                external_before=True) is False)
    check("a poke before AND during: the during one survives the clear",
          _pass(table_changed=False, external_during=True,
                external_before=True) is True)

    print("\nthe merge does not write the sentinel")
    import inspect
    from discovery import live, runmerge
    src = inspect.getsource(live) + inspect.getsource(runmerge)
    # strip docstrings and comments before looking: both files DISCUSS runAgain
    # at length, and prose about a touch is not a touch.
    import ast as _ast
    code = []
    for mod in (live, runmerge):
        tree = _ast.parse(inspect.getsource(mod))
        for node in _ast.walk(tree):
            if isinstance(node, _ast.Call):
                seg = _ast.unparse(node)
                if "runAgain" in seg and (".write" in seg or seg.startswith("open(")
                                          or "touch" in seg):
                    code.append(seg)
    writes = code
    check("no code path in live.py or runmerge.py creates runAgain",
          not writes, f"found={writes}")

    print("\n" + ("ALL RUNAGAIN-EXTERNAL CHECKS PASS" if not FAILS
                  else f"RUNAGAIN-EXTERNAL SIM FAILED: {FAILS}"))
    return 0 if not FAILS else 1


if __name__ == "__main__":
    sys.exit(main())
