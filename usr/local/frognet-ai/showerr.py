#!/usr/bin/env python3
"""showerr.py -- print why a rank failed, from its result JSON.

The log's last lines are the banner and the lock release. The reason a run
failed is recorded in the JSON, and a failure report that shows the wrong
five lines is worse than none: it looks like information.
"""
import glob
import json
import os
import sys

out, rank = sys.argv[1], sys.argv[2]
be = sys.argv[3] if len(sys.argv) > 3 else ""
red = sys.argv[4] if len(sys.argv) > 4 else ""

runid = sys.argv[5] if len(sys.argv) > 5 else ""
runid = sys.argv[5] if len(sys.argv) > 5 else ""
name = "%sr%s.%s%s.json" % ((runid + ".") if runid else "",
                            rank, be, ("." + red) if red else "")
path = os.path.join(out, name)
if not os.path.exists(path):
    # [DO_NOT_SHOW_ANOTHER_RUN_S_ERROR_V1]
    #
    # This used to glob for any r<rank>.*.json and show the newest. When a
    # rank dies BEFORE writing its result -- during rendezvous, say -- that
    # picks up a leftover file from a previous run and presents it as this
    # run's failure. Five consecutive campaigns were diagnosed against one
    # stale file that way: same group token, same dead port, every time,
    # because it was literally the same file.
    #
    # A missing result is a fact worth reporting. It says the rank did not
    # get far enough to write one, which is different from any error it
    # might have had, and the log is where to look.
    print("no result file %s -- this rank died before writing one." % name)
    print("the reason is in the log, not here.")
    others = [f for f in sorted(os.listdir(out))
              if f.endswith(".json") and (".r%s." % rank) in f]
    if others:
        print("(other runs' results present: %s)" % ", ".join(others[-3:]))
    raise SystemExit(0)

try:
    j = json.load(open(path))
except Exception as e:
    print("result file unreadable: %s" % e)
    raise SystemExit(0)
err = (j.get("error") or "").strip().splitlines()
if err:
    print("from %s:" % os.path.basename(path))
    for line in err[-5:]:
        print("  " + line[:170])
    for note in (j.get("notes") or []):
        print("  note: %s" % note)
else:
    print("status=%s, no error recorded in %s"
          % (j.get("status"), os.path.basename(path)))
