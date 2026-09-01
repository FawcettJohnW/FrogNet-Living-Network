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
"""[AUDIT_ONE_RUN_V1] Lay the tap logs from ONE run side by side.

Every tap timestamps against the same RUN_T0, so a wall_ms in one log is the
same instant as that wall_ms in another. This walks them together and prints
one row per 500 ms window showing, for each stage, how much audio it produced
in that window against how much real time passed.

The column that matters is DRIFT: cumulative audio ms minus wall ms. A stage
keeping up holds drift flat. A stage that stalls shows drift falling, and the
amount it falls IS the audio that went missing. Where drift starts falling in
ONE stage while the others hold is the stage that did it.

  python3 compare_taps.py [/tmp]
"""
import glob, os, sys

d = sys.argv[1] if len(sys.argv) > 1 else "/tmp"
WIN = 500.0

logs = {}
for f in sorted(glob.glob(os.path.join(d, "live_*.log"))):
    name = os.path.basename(f)[5:-4]
    rows = []
    for ln in open(f):
        if ln.startswith("#"):
            continue
        p = ln.split()
        if len(p) == 5:
            rows.append(tuple(float(x) for x in p))
    if rows:
        logs[name] = rows

if not logs:
    raise SystemExit("no live_*.log in " + d)

stages = sorted(logs)
end = max(r[-1][0] for r in logs.values())
print("run length %.1f s   stages: %s\n" % (end / 1000.0, ", ".join(stages)))
print("  wall_s | " + " | ".join("%-22s" % s for s in stages))
print("         | " + " | ".join("%-22s" % "audio_ms  drift_ms" for s in stages))
print("-" * (11 + 25 * len(stages)))

worst = {s: 0.0 for s in stages}
t = 0.0
while t < end:
    cells = []
    for s in stages:
        rows = [r for r in logs[s] if t <= r[0] < t + WIN]
        if not rows:
            cells.append("%-22s" % "        --")
            continue
        produced = rows[-1][3] - (rows[0][3] - rows[0][1] / 32.0)
        drift = rows[-1][4]
        if drift < worst[s]:
            worst[s] = drift
        flag = "  <<<" if drift < -60 else ""
        cells.append("%8.1f %9.1f%s" % (produced, drift, flag))
    print("%8.1f | %s" % (t / 1000.0, " | ".join("%-22s" % c for c in cells)))
    t += WIN

print("\nworst drift per stage (audio owed to real time, ms):")
for s in stages:
    print("  %-14s %9.1f %s" % (s, worst[s],
                                "STALLED" if worst[s] < -60 else "keeping up"))
