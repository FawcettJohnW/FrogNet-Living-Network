# Copyright (C) 2016-2026 Fawcett Innovations LLC
# SPDX-License-Identifier: GPL-2.0-only
#
# Kept as it was run in the sandbox (EXPLORATION-LOG.md section 8); its paths are that sandbox's.
import re, sys, os
def code(path):
    s = open(path, errors="replace").read()
    if path.endswith(".py"):
        s = re.sub(r'(?s)""".*?"""', '', s)
        lines = [l for l in s.split("\n") if l.strip() and not l.strip().startswith("#")]
    else:
        s = re.sub(r'(?s)/\*.*?\*/', '', s)
        lines = [l for l in s.split("\n") if l.strip() and not l.strip().startswith("//")]
    return len(lines), sum(len(l.strip()) for l in lines)
for label, files in [(a.split("=")[0], a.split("=")[1].split(",")) for a in sys.argv[1:]]:
    n = c = 0
    for f in files:
        if f:
            x, y = code(f); n += x; c += y
    print("%-34s files=%3d code_lines=%6d code_chars=%8d" % (label, len([f for f in files if f]), n, c))
