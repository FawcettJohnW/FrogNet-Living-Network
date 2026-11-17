# Copyright (C) 2016-2026 Fawcett Innovations LLC
# SPDX-License-Identifier: GPL-2.0-only
#
# Kept as it was run in the sandbox (EXPLORATION-LOG.md section 8); its paths are that sandbox's.
# Does a hold with this name_like see a tuple written by frognet_tuples.put, on the real api.php?
import sys, os, threading, time
tree, me = sys.argv[1], sys.argv[2]
sys.path.insert(0, tree); sys.path.insert(0, os.path.join(tree, "core"))
from core import frognet_tuples as T1
import frognet_tuples as T2, frognet_held as H
for T in (T1, T2): T.my_ip = lambda: me
print("same module object:", T1 is T2)
db = me
ok = T1.put("homa", "can", me, {"ip": me, "port": 9009}, dbhost=db, own=False)
print("put ok:", ok)
for pat in ("can.%", "SD:can.%"):
    hid, rows = H.holder().hold("homa", db, name_like=pat)
    print("name_like=%-10r rows=%d %s" % (pat, len(rows), [r["SensorName"] for r in rows]))
