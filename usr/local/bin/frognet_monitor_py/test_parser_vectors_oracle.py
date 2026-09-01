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
"""test_parser_vectors_oracle.py — run the shared vectors against the REAL
frognet_monitor_py parsers.

The C++ and C# ports read the same file. If this passes and they pass, the three
implementations agree by measurement rather than by inspection.

    PYTHONPATH=/usr/local/bin python3 -m frognet_monitor_py.test_parser_vectors_oracle
"""
import json
import os
import sys

VECTORS = os.environ.get(
    "FROGNET_PARSER_VECTORS",
    os.path.join(os.path.dirname(os.path.abspath(__file__)),
                 "..", "frognet_monitor_shared", "parser_vectors.json"))

PASS = 0
FAILURES = []


def ok(msg):
    global PASS
    PASS += 1
    print("  ok    " + msg)


def bad(msg):
    FAILURES.append(msg)
    print("  FAIL  " + msg)


# --------------------------------------------------------------------------
# parse_echo_line and dot_one_of live in identity.py alongside the node-side
# helpers. A CLIENT never needs the node-side ones: it reads its own address,
# derives the .1, and asks. These two are the whole client identity path.
# --------------------------------------------------------------------------
def _load():
    from frognet_monitor_py import discovery
    try:
        from frognet_monitor_py.identity import parse_echo_line, dot_one_of
    except ImportError as e:
        print("FATAL: identity.parse_echo_line / dot_one_of missing: %s" % e)
        print("       The C++ and C# ports implement these; the Python must too,")
        print("       or the shared vectors only prove two of three agree.")
        sys.exit(1)
    return discovery.parse_etc_hosts_content, parse_echo_line, dot_one_of


def main():
    parse_etc_hosts_content, parse_echo_line, dot_one_of = _load()

    with open(os.path.normpath(VECTORS)) as f:
        v = json.load(f)
    print("vectors: %s" % os.path.normpath(VECTORS))

    print("\n=== echo_line (identity) ===")
    for c in v["echo_line"]["cases"]:
        got = parse_echo_line(c["input"])
        if not c["ok"]:
            if got is None:
                ok(c["name"] + " -- rejected, as it must be")
            else:
                bad(c["name"] + " -- accepted %r, expected rejection" % (got,))
            continue
        if got is None:
            bad(c["name"] + " -- rejected, expected four fields")
            continue
        want = (c["domain"], c["eth0"], c["wlan0"], c["wlan1"])
        if tuple(got) == want:
            ok(c["name"])
        else:
            bad(c["name"] + " -- got %r want %r" % (tuple(got), want))

    print("\n=== etc_hosts (discovery) ===")
    for c in v["etc_hosts"]["cases"]:
        got = [list(x) for x in parse_etc_hosts_content(c["input"])]
        want = [list(x) for x in c["expect"]]
        if got == want:
            ok(c["name"])
        else:
            bad(c["name"] + " -- got %r want %r" % (got, want))

    print("\n=== dot_one_of (identity endpoint) ===")
    for c in v["dot_one_of"]["cases"]:
        got = dot_one_of(c["input"])
        if not c["ok"]:
            if not got:
                ok(c["name"] + " -- rejected")
            else:
                bad(c["name"] + " -- returned %r, expected rejection" % got)
            continue
        if got == c["expect"]:
            ok(c["name"])
        else:
            bad(c["name"] + " -- got %r want %r" % (got, c["expect"]))

    print()
    print("%d passed, %d failed" % (PASS, len(FAILURES)))
    if FAILURES:
        print("FAILED")
        return 1
    print("PASS - Python parsers agree with the shared vectors")
    return 0


if __name__ == "__main__":
    sys.exit(main())
