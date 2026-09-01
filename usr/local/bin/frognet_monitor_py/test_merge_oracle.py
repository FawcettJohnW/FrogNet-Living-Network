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
test_merge_oracle.py -- [MERGE_IS_VISIBLE_V1]

A merge in progress is on the dashboard, and M starts one.

A merge degrades this node while it runs, so every latency and hit-rate figure
on screen is suspect for its duration. Without an indicator there is no way to
tell a merge from a fault, from the monitor.

  G1  the sentinel is /etc/sentinels/mergePending, read by stat, not by
      scraping ps for the script name
  G2  present -> in progress, with an age
  G3  absent -> not in progress
  G4  unreadable -> UNKNOWN, which is neither of the above
  G5  the age is formatted so a merge stuck for hours is visible as such
  G6  the reading is cached, so a repaint is not a syscall storm
  G7  M refuses when a merge is already running, and says why
  G8  M refuses when the script is missing or not executable, and says why
  G9  the note is transient state on AppState, not a permanent field
 G10  the title line carries the indicator; the footer advertises the key

Run:  PYTHONPATH=/usr/local/bin python3 -m frognet_monitor_py.test_merge_oracle
  or: python3 test_merge_oracle.py    (from the package directory)
"""

import os
import sys
import tempfile
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))
_PKG = os.path.basename(_HERE)

merge = __import__("%s.merge" % _PKG, fromlist=["merge"])
state = __import__("%s.state" % _PKG, fromlist=["state"])

FAIL = []


def ck(name, cond, got=None):
    if cond:
        print("  PASS  %s" % name)
    else:
        print("  FAIL  %s   (got %r)" % (name, got))
        FAIL.append(name)


def fresh(path=None, script=None):
    """Point the module at a temp sentinel/script and clear its cache."""
    if path is not None:
        merge.MERGE_SENTINEL = path
    if script is not None:
        merge.MERGE_SCRIPT = script
    merge._cache["state"] = None
    merge._cache["at"] = 0.0


tmp = tempfile.mkdtemp()
SENT = os.path.join(tmp, "mergePending")

# ---- G1 --------------------------------------------------------------------
src = open(os.path.join(_HERE, "merge.py"), encoding="utf-8").read()
# The path is checked against a literal here on purpose. It was written as
# /opt/sentinels for one revision, and nothing failed -- the module was
# self-consistent and every other assertion passed, because they all read the
# module's own constant. An oracle that asks the code where it looks can only
# confirm the code is consistent with itself.
ck("G1 the sentinel directory is /etc/sentinels",
   merge.SENTINEL_DIR == "/etc/sentinels", merge.SENTINEL_DIR)
ck("G1 the sentinel path is the one runMerge maintains",
   merge.MERGE_SENTINEL == "/etc/sentinels/mergePending", merge.MERGE_SENTINEL)
ck("G1 and /opt is nowhere in the module", "/opt/" not in src,
   [l for l in src.splitlines() if "/opt/" in l])
ck("G1 the script path is runMerge.bash",
   "/usr/local/bin/runMerge.bash" in src)
ck("G1 it does not scrape the process table",
   "pgrep" not in src and "ps -ef" not in src and "psutil" not in src)

# ---- G3: absent ------------------------------------------------------------
fresh(SENT)
ck("G3 no sentinel means no merge", merge.merge_state() == (False, 0.0, ""),
   merge.merge_state())

# ---- G2: present -----------------------------------------------------------
open(SENT, "w").close()
os.utime(SENT, (time.time() - 90, time.time() - 90))
fresh()
inprog, since, note = merge.merge_state()
ck("G2 a sentinel means a merge is in progress", inprog is True, inprog)
ck("G2 with an age taken from its mtime", 85 <= since <= 95, since)
ck("G2 and no note when the answer is certain", note == "", note)

# ---- G4: unreadable is its own answer --------------------------------------
class Boom:
    pass

_real_stat = os.stat
def _explode(p, *a, **kw):
    if p == merge.MERGE_SENTINEL:
        raise PermissionError(13, "Permission denied")
    return _real_stat(p, *a, **kw)

os.stat = _explode
fresh()
inprog, since, note = merge.merge_state()
os.stat = _real_stat
ck("G4 an unreadable sentinel is UNKNOWN, not 'no merge'", inprog is None, inprog)
ck("G4 and it says why", "PermissionError" in note, note)
ck("G4 unknown is distinguishable from both answers",
   inprog is not False and inprog is not True, inprog)

# ---- G5 --------------------------------------------------------------------
ck("G5 seconds", merge.fmt_since(9) == "9s", merge.fmt_since(9))
ck("G5 minutes", merge.fmt_since(90) == "1m30s", merge.fmt_since(90))
ck("G5 a merge stuck for hours reads as hours",
   merge.fmt_since(7500) == "2h05m", merge.fmt_since(7500))

# ---- G6 --------------------------------------------------------------------
calls = {"n": 0}
def _counted(p, *a, **kw):
    if p == merge.MERGE_SENTINEL:
        calls["n"] += 1
    return _real_stat(p, *a, **kw)

os.stat = _counted
fresh()
for _ in range(50):
    merge.merge_state()
os.stat = _real_stat
ck("G6 fifty repaints do not mean fifty stat() calls", calls["n"] == 1, calls["n"])

# ---- G7/G8: the launcher refuses, with a reason ----------------------------
script = os.path.join(tmp, "runMerge.bash")
open(script, "w").write("#!/bin/sh\nexit 0\n")
os.chmod(script, 0o755)

fresh(SENT, script)                      # sentinel still present
ok, why = merge.start_merge()
ck("G7 M warns rather than launching while the sentinel is there",
   ok is False, (ok, why))
ck("G7 and reports how old the claim is", "old" in why, why)
ck("G7 and says how to proceed anyway", "again" in why, why)

# [SENTINEL_IS_A_CLAIM_NOT_A_LOCK_V1] A dead merge leaves the sentinel behind.
# If the sentinel were an interlock, one crash would disable the key forever --
# and the refusal only shows for a few seconds, so it would look simply dead.
fresh()
ok, why = merge.start_merge(force=True)
ck("G7 a forced launch goes through a stale sentinel", ok is True, (ok, why))
ck("G7 and says it was forced", "forced" in why, why)

os.remove(SENT)
fresh()
ok, why = merge.start_merge()
ck("G7 with no merge running it launches", ok is True, (ok, why))

fresh(SENT, os.path.join(tmp, "nope.bash"))
ok, why = merge.start_merge()
ck("G8 a missing script is refused", ok is False, (ok, why))
ck("G8 and named", "not found" in why, why)

noexec = os.path.join(tmp, "noexec.bash")
open(noexec, "w").write("#!/bin/sh\n")
os.chmod(noexec, 0o644)
fresh(SENT, noexec)
ok, why = merge.start_merge()
ck("G8 a non-executable script is refused", ok is False, (ok, why))
ck("G8 and the reason distinguishes it from missing",
   "not executable" in why, why)

# ---- G9 --------------------------------------------------------------------
st = state.AppState()
ck("G9 the note starts empty", st.merge_note == "", st.merge_note)
for f in ("merge_note", "merge_note_ok", "merge_note_at"):
    ck("G9 AppState carries %s" % f, hasattr(st, f))

ml = open(os.path.join(_HERE, "main_loop.py"), encoding="utf-8").read()
ck("G9 the note expires rather than persisting",
   "MERGE_NOTE_S" in ml and "st.merge_note = \"\"" in ml)

# ---- G10 -------------------------------------------------------------------
dd = open(os.path.join(_HERE, "draw_dashboard.py"), encoding="utf-8").read()
ck("G10 the indicator is on the title line, above the numbers it qualifies",
   "MERGE IN PROGRESS" in dd
   and dd.index("MERGE IN PROGRESS") < dd.index("def draw_peer_header"), None)
ck("G10 unknown gets its own wording", "MERGE STATE UNKNOWN" in dd)
ck("G10 the footer advertises the key", "M=merge" in dd)
ck("G10 the key is capital M, not a navigation letter",
   "ord('M')" in ml and "ord('m')" not in ml)

# ---- G11: the second press forces, and only while the warning stands -------
# The force is decided in main_loop from the note's state, so check that wiring
# rather than trusting start_merge's parameter alone.
ck("G11 the handler forces on a second press while the refusal stands",
   "_forcing = bool(st.merge_note) and not st.merge_note_ok" in ml
   and "start_merge(force=_forcing)" in ml)
ck("G11 and only inside the note window, so it is not armed forever",
   "<= MERGE_NOTE_S" in ml)

# ---- G12: every exit from start_merge is traced ----------------------------
# A refusal visible only in a status line that expires is a refusal the operator
# misses, and then the key is "broken". Counted rather than string-matched: the
# first version of this check was a tangle of ORs that could not fail.
import ast as _ast
msrc = open(os.path.join(_HERE, "merge.py"), encoding="utf-8").read()
_fn = next(n for n in _ast.parse(msrc).body
           if isinstance(n, _ast.FunctionDef) and n.name == "start_merge")
_returns = [n for n in _ast.walk(_fn) if isinstance(n, _ast.Return)]
_traces = [n for n in _ast.walk(_fn)
           if isinstance(n, _ast.Call) and getattr(n.func, "id", "") == "trace"]
ck("G12 start_merge has more than one way out", len(_returns) >= 4, len(_returns))
ck("G12 and every one of them is traced",
   len(_traces) >= len(_returns), (len(_traces), len(_returns)))
ck("G12 the successful launch is traced too", "[MERGE] launched" in msrc)

print()
if FAIL:
    print("FAILED %d: %s" % (len(FAIL), ", ".join(FAIL)))
    sys.exit(1)
print("ALL PASS")
