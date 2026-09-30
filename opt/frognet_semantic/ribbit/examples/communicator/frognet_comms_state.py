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
frognet_comms_state.py -- what the Communicator's tuple space actually holds, and
where this node thinks a call would gather.

Answers, from ONE box, in one run:

  * is the store honouring &fresh_s, or is every read returning all history
    (phantoms) because api.php predates [ENVELOPE_TS_V1] or the UpdatedAt column
    is missing
  * which presence rows are live and which are stale, BY THE STORE'S OWN CLOCK
  * every call row, who is on it, which media host it was pinned to, and by whom
  * what mediahost.frognet resolves to HERE, and whether anything is listening
  * whether a call this node started would gather at this node's media host

Reads only. Run on each box and compare.

    python3 frognet_comms_state.py
    python3 frognet_comms_state.py --dbhost 10.250.250.1
"""
import argparse
import json
import socket
import sys
import time
import urllib.request

sys.path.insert(0, "/opt/frognet_semantic")

def _resolve(name):
    """[HOSTS_ONLY_V1] /etc/hosts only. socket.gethostbyname goes to the resolver,
    which on a node is `nameserver 127.0.0.1` first, and answers differently from the
    file -- measured on Seattle3: file said 10.250.250.1, gethostbyname said
    10.130.130.1. A diagnostic that resolves differently from the system it is
    diagnosing is worse than none."""
    from core.hosts_only import resolve as _r
    return _r(name)

sys.path.insert(0, "/etc/frognet_bundles/communicator")

ap = argparse.ArgumentParser()
ap.add_argument("--dbhost", default=None, help="override databasehost.frognet")
args = ap.parse_args()

from core import frognet_tuples as T          # noqa: E402
import comms_control as CC                    # noqa: E402

DB = args.dbhost or CC.DBHOST
BAR = "=" * 74


def head(s):
    print("\n" + BAR + "\n== " + s + "\n" + BAR)


head("1. THIS NODE")
print("  hostname      : %s" % socket.gethostname())
print("  my_ip()       : %s" % T.my_ip())
for name in ("mediahost.frognet", "databasehost.frognet",
             "databasehost_control.frognet"):
    try:
        print("  %-28s -> %s" % (name, _resolve(name)))
    except Exception as e:
        print("  %-28s -> UNRESOLVED (%s)" % (name, type(e).__name__))

print()
try:
    mh, mp = CC.ControlPlane("probe", "probe", dbhost=DB)._resolve_media()
    print("  a call started HERE would gather at %s:%s" % (mh, mp))
    s = socket.socket()
    s.settimeout(3)
    try:
        s.connect((mh, mp))
        print("  and something IS listening there")
    except Exception as e:
        print("  but NOTHING is listening there: %s -- check "
              "`systemctl status frognet-mediahost` on that box" % type(e).__name__)
    finally:
        s.close()
except Exception as e:
    print("  a call started HERE could not resolve a media host: %s" % e)


head("2. IS THE STORE FILTERING? (phantoms live or die here)")
# Ask for everything, then ask for a 30s window. If the counts are identical and the
# rows are old, &fresh_s is being ignored -- every read is returning all history.
try:
    allrows = T.get_all("communicator", dbhost=DB, fresh_s=0)
    fresh = T.get_all("communicator", dbhost=DB, fresh_s=30)
    envel = [r for r in allrows if r.get("ts_env")]
    print("  rows, no window      : %d" % len(allrows))
    print("  rows, fresh_s=30     : %d" % len(fresh))
    print("  rows carrying UpdatedAt: %d of %d" % (len(envel), len(allrows)))
    # Judge by AGE, not by count. Counts can differ simply because the store changed
    # between the two reads, which hid a non-filtering store behind 29-vs-31.
    ages = sorted(r["age_s"] for r in fresh if r.get("age_s") is not None)
    if ages:
        print("  oldest row the 30s window returned: %ds" % ages[-1])
    if allrows and not envel:
        print("  >>> NO row carries an envelope. This api.php predates "
              "[ENVELOPE_TS_V1],\n      or SensorData has no UpdatedAt column. Install "
              "the new api.php and run\n      schema_fixups.sql -- until then every "
              "read returns all history.")
    elif ages and ages[-1] > 60:
        print("  >>> THE STORE IS NOT FILTERING. A 30-second window returned a row "
              "%ds old.\n      Every read on this node is returning all history: that "
              "is the phantoms.\n      Compare the raw HTTP below across nodes -- if "
              "curl disagrees between boxes\n      for the same database, the requests "
              "are not reaching the same place." % ages[-1])
    elif ages:
        print("  the store is filtering correctly on this node")

    # the same question, without the client in the way
    try:
        q = ("http://%s/api.php?entity=sensors&action=values&parse=1"
             "&SensorType=communicator&fresh_s=30" % DB)
        with urllib.request.urlopen(q, timeout=8) as r:
            raw = json.loads(r.read().decode())
        n = len(raw.get("rows", []))
        got_env = sum(1 for x in raw.get("rows", [])
                      if x.get("UpdatedAtEpoch") or x.get("UpdatedAt"))
        print("\n  RAW HTTP, same window, client bypassed:")
        print("    %s" % q)
        print("    -> %d row(s), %d carrying UpdatedAt" % (n, got_env))
        print("    Run this exact line on every node. Same database, different answers")
        print("    means the requests are not reaching the same server.")
    except Exception as e:
        print("\n  RAW HTTP failed: %r" % e)
except Exception as e:
    print("  read failed: %r" % e)


head("3. PRESENCE -- who this node can see, and how old each row is")
try:
    rows = T.get("communicator", "presence", dbhost=DB, fresh_s=0)
    if not rows:
        print("  no presence rows at all")
    for r in sorted(rows, key=lambda x: (x.get("age_s") is None, x.get("age_s") or 0)):
        v = r.get("value", {})
        age = r.get("age_s")
        mark = "live " if (age is not None and age <= CC.PRESENCE_FRESH_S) else "STALE"
        print("  %s %-22s %-14s age=%-7s addr=%s"
              % (mark, v.get("name", "?"), v.get("id", "?"),
                 ("%ds" % age) if age is not None else "NO ENVELOPE",
                 r.get("addr", "?")))
    live = [r for r in rows
            if r.get("age_s") is not None and r["age_s"] <= CC.PRESENCE_FRESH_S]
    print("\n  roster() would show %d of %d" % (len(live), len(rows)))
    print("  (stale rows are not phantoms unless roster() shows them -- if it does, "
          "section 2\n   is the reason)")
except Exception as e:
    print("  read failed: %r" % e)


head("4. CALLS -- where each one gathers, and who pinned it")
try:
    rows = T.get("communicator", "call", dbhost=DB, fresh_s=0)
    if not rows:
        print("  no call rows")
    here = None
    try:
        here = _resolve("mediahost.frognet")
    except Exception:
        pass
    for r in rows:
        v = r.get("value", {})
        age = r.get("age_s")
        members = v.get("members") or []
        host = v.get("host")
        note = ""
        if host and here:
            note = "  <- THIS node's media host" if host == here else \
                   "  <- a DIFFERENT media host from this node's (%s)" % here
        print("  session=%-14s age=%-7s members=%-28s host=%s:%s%s"
              % (str(v.get("session"))[:12],
                 ("%ds" % age) if age is not None else "NO ENVELOPE",
                 members, host, v.get("port"), note))
        if not members:
            print("       (nobody on it -- a row that has not aged out, not a call)")
    print("\n  A call row's host is pinned by whoever STARTED it, from that node's own")
    print("  resolution [MEDIAHOST_PIN_LOCAL_V1]. A call you start gathers at YOUR")
    print("  media host; a call you JOIN gathers wherever its starter pinned it.")
except Exception as e:
    print("  read failed: %r" % e)


head("5. THE OTHER TUPLES ON THIS SERVICE")
for var in ("chat", "link", "transcript", "settings"):
    try:
        rows = T.get("communicator", var, dbhost=DB, fresh_s=0)
        ages = [r["age_s"] for r in rows if r.get("age_s") is not None]
        print("  %-11s %3d row(s)%s" % (var, len(rows),
              ("   oldest %ds" % max(ages)) if ages else ""))
    except Exception as e:
        print("  %-11s read failed: %r" % (var, e))

head("6. IS THIS NODE READING THE SHARED STORE, OR ITS OWN?")
# The proxy listens on :80 and intercepts api.php; transport_real.real_upstream_local
# can route an intercepted read to the node's OWN Apache on :8080. Every node runs
# Apache and MySQL, so "read databasehost.frognet" can silently become "read my own
# database" -- and four nodes then give four different answers to one query.
import subprocess
try:
    r = subprocess.run(["mysql", "FrogNet", "-N", "-e",
                        "SELECT COUNT(*) FROM Sensor WHERE SensorType='communicator'"],
                       capture_output=True, text=True, timeout=10)
    local_n = r.stdout.strip() or ("(no local database: %s)" % r.stderr.strip()[:60])
except Exception as e:
    local_n = "(mysql not reachable here: %s)" % type(e).__name__
print("  communicator rows in THIS node's OWN database : %s" % local_n)
try:
    served = len(T.get_all("communicator", dbhost=DB, fresh_s=0))
except Exception:
    served = "?"
print("  communicator rows the client is being served  : %s" % served)
print()
print("  If these match and this node is NOT %s, the read never left the box:" % DB)
print("  the proxy served it from the local Apache and the transient plane is not")
print("  shared. Compare across nodes -- that is the whole question.")

print()
print("dbhost used for all of the above: %s" % DB)
