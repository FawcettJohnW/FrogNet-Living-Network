#!/opt/frognet_semantic/venv/bin/python3
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
"""
core/build_id.py - [BASE_BUILD_V1 - T4b] the FrogNet base's release id, said by the services at start.

/opt/frognet_semantic/BUILD (this package root's BUILD) holds `build=<tag>` (the tree's one release tag, the same
FDF_BUILD_TAG the FD&F binaries carry) and, written by the installer when it installs from a git checkout,
`commit=<sha>[+changes]` and `installed=<UTC time>`. base_build() is what the proxy, the data daemon and the tunnel
daemon print in their first log line, so a node's log says what is deployed without anyone running a command.

[BUILD_UPDATES_SAY_SO_V1] An update laid over the base (a tarball of changed files, not a new base) drops one file in
BUILD.d/ named for its tag, holding `<tag> commit=<sha>: <what it changes>` on its first line. base_build() appends
each, in name order, as ` + <first line>`: the start line then says the base AND every update on top of it.
"""
from pathlib import Path

BUILD_FILE = Path(__file__).resolve().parents[1] / "BUILD"
UPDATES_DIR = BUILD_FILE.parent / "BUILD.d"


def base_build() -> str:
    try:
        lines = BUILD_FILE.read_text().splitlines()
    except OSError as e:
        return f"<no {BUILD_FILE}: {e.strerror}>"
    kv = dict(ln.split("=", 1) for ln in lines if "=" in ln and not ln.startswith("#"))
    out = kv.get("build", f"<{BUILD_FILE} has no build= line>")
    if kv.get("commit"):
        out += " commit=" + kv["commit"]
    try:                                                     # [BUILD_UPDATES_SAY_SO_V1]
        ups = sorted(p for p in UPDATES_DIR.iterdir() if p.is_file() and not p.name.startswith("."))
    except OSError:
        ups = []
    for u in ups:
        try:
            first = (u.read_text().splitlines() or [""])[0].strip()
        except OSError as e:
            first = f"<{u}: {e.strerror}>"
        out += " + " + (first or u.name)
    return out
