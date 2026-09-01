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
"""Read a capability blob on stdin; exit 0 if all election-numeric fields are
scalars (absent OPT fields allowed), 1 otherwise."""
import sys, json
blob = json.loads(sys.stdin.read())
REQ = ("cores","cpu_mhz","cpu_bench_total","mem_total_kb","mem_available_kb",
       "disk_write_mbps","disk_fsync_ms","disk_free_gb","av_port")
OPT = ("mysql_innodb_pool_bytes",)
def scalar(v): return (not isinstance(v, bool)) and isinstance(v, (int, float))
bad = {k: blob.get(k) for k in REQ if not scalar(blob.get(k))}
bad.update({k: blob[k] for k in OPT if k in blob and not scalar(blob[k])})
ma = blob.get("mem_available_kb")
print(f"  emitted mem_available_kb = {ma!r} ({type(ma).__name__})")
if bad:
    print(f"RESULT: FAIL — non-scalar numeric field(s) published: {bad}"); sys.exit(1)
print("RESULT: PASS — all numeric fields scalar; stale cache was not republished")
