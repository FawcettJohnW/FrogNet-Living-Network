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
"""merge.py -- [MERGE_IS_VISIBLE_V1]

A merge degrades this node's performance while it runs, and until now the
dashboard gave no sign of it: every latency and hit-rate number on screen could
be a merge rather than a fault, and there was no way to tell them apart from
the monitor.

Two things live here. The sentinel that says a merge is in progress, and the
launcher for starting one.

SENTINEL, NOT PROCESS TABLE. /etc/sentinels/mergePending is what runMerge.bash
maintains, so it is what the dashboard reads. Scraping ps for the script name
would report the wrapper's own zombie children as a live merge -- observed
2026-08-10, `[runMerge.bash] <defunct>` alongside a live sibling -- and would
disagree with whatever the merge itself believes.
"""

import os
import subprocess
import time

from .trace import trace

SENTINEL_DIR = "/etc/sentinels"
MERGE_SENTINEL = os.path.join(SENTINEL_DIR, "mergePending")
MERGE_SCRIPT = "/usr/local/bin/runMerge.bash"

# The dashboard repaints far faster than a file can meaningfully change, and a
# stat() per repaint per sentinel is a syscall on the draw path. Cache it.
_POLL_S = 1.0
_cache = {"at": 0.0, "state": None}


def merge_state(now=None):
    """(in_progress, since_s, note) for the merge sentinel.

    since_s is how long the sentinel has existed, from its mtime -- a merge that
    has been "in progress" for a very long time is the interesting case, and a
    bare boolean cannot show it.

    note is non-empty only when the answer is uncertain, and it says why. A
    sentinel directory that cannot be read is NOT the same as no merge running,
    and reporting the second when the first is true is how a monitor starts
    lying quietly.
    """
    now = now if now is not None else time.monotonic()
    if _cache["state"] is not None and (now - _cache["at"]) < _POLL_S:
        return _cache["state"]

    try:
        st = os.stat(MERGE_SENTINEL)
        state = (True, max(0.0, time.time() - st.st_mtime), "")
    except FileNotFoundError:
        state = (False, 0.0, "")
    except OSError as e:
        # Permissions, a dead mount, anything else: say so rather than paint a
        # clean dashboard over an unknown.
        state = (None, 0.0, "%s: %s" % (type(e).__name__, e))
        trace("[MERGE] sentinel unreadable: %r" % (e,))

    _cache["at"] = now
    _cache["state"] = state
    return state


def start_merge(force=False):
    """Launch runMerge.bash detached. Returns (ok, message) for the status line.

    Detached on purpose: the merge outlives this monitor, and a monitor that
    blocks on it stops drawing -- which is the one thing it exists to do.
    stdout goes to DEVNULL because a curses screen and a script's output cannot
    share a terminal.

    [SENTINEL_IS_A_CLAIM_NOT_A_LOCK_V1] The sentinel is what a merge says about
    itself, and a merge that dies leaves it behind. Using it as an interlock
    meant one crashed merge blocked the hotkey forever -- and the refusal was
    only ever on screen for a few seconds, so the key looked simply dead.

    So the sentinel warns and `force` overrides. The caller presses M twice: the
    first press explains, the second one goes anyway. That keeps an accidental
    double-merge deliberate without letting a stale file win permanently.

    Every outcome is TRACED. A refusal visible only in a status line that
    expires is a refusal the operator will miss, and then the key is "broken".
    """
    if not os.path.exists(MERGE_SCRIPT):
        trace("[MERGE] refused: %s not found" % MERGE_SCRIPT)
        return False, "%s not found" % MERGE_SCRIPT
    if not os.access(MERGE_SCRIPT, os.X_OK):
        trace("[MERGE] refused: %s not executable" % MERGE_SCRIPT)
        return False, "%s is not executable" % MERGE_SCRIPT

    in_progress, since, _note = merge_state()
    if in_progress and not force:
        trace("[MERGE] refused: sentinel present, age %s (press M again to "
              "force)" % fmt_since(since))
        return False, ("sentinel %s old -- M again to start anyway"
                       % fmt_since(since))

    try:
        subprocess.Popen([MERGE_SCRIPT],
                         stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL,
                         start_new_session=True)
    except OSError as e:
        trace("[MERGE] launch failed: %r" % (e,))
        return False, "launch failed: %s" % (e,)

    _cache["state"] = None
    trace("[MERGE] launched %s%s" % (MERGE_SCRIPT, " (forced)" if force else ""))
    return True, "merge started" + (" (forced)" if force else "")


def fmt_since(secs):
    """How long the sentinel has been there.

    Lives here rather than in the drawing code because it is a fact about the
    merge, not about the screen -- and because a merge that has been "in
    progress" for hours is the interesting case, which a bare flag hides.
    """
    s = int(secs)
    if s < 60:
        return "%ds" % s
    if s < 3600:
        return "%dm%02ds" % (s // 60, s % 60)
    return "%dh%02dm" % (s // 3600, (s % 3600) // 60)
