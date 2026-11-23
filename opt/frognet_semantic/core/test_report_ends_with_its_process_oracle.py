#!/usr/bin/env python3
################################################################
#  Copyright (C) 2016-2026 Fawcett Innovations LLC             #
#  SPDX-License-Identifier: GPL-2.0-only                       #
################################################################
"""[A_REPORT_ENDS_WITH_ITS_PROCESS_V1] oracle: at its first report a process removes the reports of THIS node's
processes that are no longer running, and nothing else. The store is a stand-in with the tuple layer's own calls."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core import channel_setting as C  # noqa: E402

ME, DEAD = os.getpid(), 4194000          # (above any pid this machine gives out by default)
assert not os.path.exists("/proc/%d" % DEAD)
rows = {1: "sd.at.10.251.251.1:proxy_main.py.%d" % DEAD,        # this node, ended: goes
        2: "sd.at.10.251.251.1:proxy_main.py.%d" % ME,          # this process: stays
        3: "sd.at.10.251.251.1:daemon_main.py.1",               # this node, running (pid 1): stays
        4: "sd.at.10.250.250.1:proxy_main.py.%d" % DEAD,        # another node's: never touched
        5: "sd.setting.units"}                                  # not a report: never touched
deleted = []


class T:
    my_ip = staticmethod(lambda: "10.251.251.1")
    var_name = staticmethod(lambda var, scope: "sd.%s.%s" % (var, scope))
    _values_raw = staticmethod(lambda service, dbhost: [{"SensorID": k, "SensorName": v} for k, v in rows.items()])
    _delete_by_id = staticmethod(lambda dbhost, sid: deleted.append(sid) or True)


C._tuples = lambda: (T, None)
C._remove_reports_of_processes_that_ended()
ok = deleted == [1]
print("  %s  only the report of this node's ended process was removed (removed ids %r)" % ("PASS" if ok else "FAIL", deleted))
print("RESULT %s (%s)" % ("PASS" if ok else "FAIL", C.BUILD))
sys.exit(0 if ok else 1)
