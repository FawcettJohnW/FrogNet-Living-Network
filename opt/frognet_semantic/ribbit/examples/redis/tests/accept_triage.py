#!/usr/bin/env python3
# Copyright (C) 2016-2026 Fawcett Innovations LLC
# SPDX-License-Identifier: GPL-2.0-only
"""accept_triage.py -- sorts every suite test green on stock and not green on the region by what a CLIENT can observe,
and says for each class what the supported contract says about it. Reads accept.sh's SUMMARY.txt; tests that only
fail after an earlier failure in the same file are rerun alone on both arms to prove it.

Usage: accept_triage.py <SUMMARY.txt> <redis-src> [--rerun]     -> markdown on stdout

Classes, first rule that matches decides (the rule is printed beside every test):
  replication   the test reads what a replica receives (propagation, REPLICAOF, stale replica, min-replicas-to-write --
                "exec with write commands and state change" is the last, by name). The region has no
                replicas: REPLICAOF and the replication stream are not offered. A scoped exclusion.
  persistence   the test reloads, rewrites, saves or loads data (DEBUG RELOAD, AOF, RDB, SAVE, legacy loading). The
                region keeps data in the host's memory only: it does NOT survive a host restart (acceptance hostloss
                shows it). Not supported -- not excluded as irrelevant.
  scripting     Lua (EVAL, SCRIPT, script timeout) and SHUTDOWN. Not offered.
  encoding      the test reads Redis's internal representation (ziplist/listpack/quicklist, shared objects, hash-table
                chains, OBJECT ENCODING, the radix-tree node boundaries '~' and LIMIT trim on). The data contract holds;
                the reported encoding and the approximate-trim boundaries differ.
  cascade       named in TRIAGE-SUITE-REDS.md as failing only after an earlier test left a transaction open; rerun
                alone (--rerun) to show it.
  not-run       the suite skipped it on the region (e.g. it runs only when INFO reports jemalloc).
  unexplained   none of the above: a defect until shown otherwise.
"""
import re, subprocess, sys

summary, rsrc = sys.argv[1], sys.argv[2]; RERUN = "--rerun" in sys.argv
RULES = [
    ("replication", r"propagat|replica|REPLICAOF|slave|Replication tests|exec with write commands and state change|unblock(ed)? .*execution order|SWAPDB wants to wake|MULTI \+ LPUSH \+ EXPIRE"),
    ("persistence", r"DEBUG RELOAD|AOF|AOFRW|RDB|persistence|\bSAVE\b|BGREWRITEAOF|appendonly|plain nodes|seen-time|NOACK creates consumer|Loading from legacy"),
    ("scripting",   r"EVAL|[Ss]cript|SHUTDOWN"),
    ("encoding",    r"ziplist|listpack|quicklist|encoding|shared objects|long chain|sanitize dump|promoted|'~'|~ |LIMIT|is limited|MAXLEN option basic"),
    ("cascade",     r"Blocking commands ignores the timeout|use-memory command should fail|only read commands should not be rejected when OOM|Flushall while watching|MULTI with config error"),
]
rows = []; file_of = {}
cur = None
for line in open(summary, encoding="utf-8", errors="replace"):
    m = re.match(r"\s+(unit/\S+)\s+stock ok", line)
    if m: cur = m.group(1); continue
    m = re.match(r"\s+stock-only, (red on the region|not run on the region[^:]*): (.*)$", line.rstrip("\n"))
    if m:
        name = m.group(2); file_of[name] = cur
        if m.group(1).startswith("not run"): rows.append((name, "not-run", "the suite skipped it on the region")); continue
        for cls, rx in RULES:
            if re.search(rx, name): rows.append((name, cls, rx)); break
        else: rows.append((name, "unexplained", "-"))

def alone(arm, f, name):
    out = subprocess.run(["./runtest", "--single", f, "--only", name, "--ignore-encoding", "--ignore-digest", "--durable", "--clients", "1"],
                         cwd=rsrc, env=dict(__import__("os").environ, ACCEPT_SERVER=arm), capture_output=True, text=True, timeout=900).stdout
    return "ok" if re.search(r"^\[ok\]", out, re.M) and not re.search(r"^\[err\]", out, re.M) else ("err" if re.search(r"^\[err\]", out, re.M) else "did not run")

print("| class | test | file | rule%s |" % (" | alone: stock / region" if RERUN else ""))
print("|---|---|---|---%s|" % ("|---" if RERUN else ""))
counts = {}
for name, cls, why in sorted(rows, key=lambda r: (r[1], r[0])):
    counts[cls] = counts.get(cls, 0) + 1
    extra = ""
    if RERUN and cls == "cascade":
        extra = " | %s / %s" % (alone("stock", file_of[name], name), alone("ribbit", file_of[name], name))
    elif RERUN: extra = " | "
    print("| %s | %s | %s | `%s`%s |" % (cls, name.replace("|", "/"), file_of.get(name, "?"), why.replace("|", "\\|"), extra))
print()
print("Counts: " + ", ".join("%s %d" % (k, v) for k, v in sorted(counts.items())) + ", total %d" % len(rows))
