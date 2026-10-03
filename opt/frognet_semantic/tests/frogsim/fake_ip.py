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
"""fake_ip.py - the faked kernel boundary. Real services shell out to `ip`; this
answers as the kernel would, backed by ONE per-node table file so state persists
across the many separate `ip` invocations a running service makes.

Engine = discovery.kernel.FakeKernel (the oracle-proven in-memory FIB) so what we
render is byte-identical to what the real routing code installs and reads back.
Adds the two things FakeKernel doesn't model (not needed by the oracle): `route
get` longest-prefix resolution and `addr show`.

Env:
  FROGSIM_KTABLE  path to this node's route-table file (one `ip -o -4` line each)
  FROGSIM_KADDR   path to this node's addr file (lines: "<dev> <cidr>")
  FROGNET_SEMANTIC_ROOT  path to the work tree (for importing discovery.kernel)
"""
import os
import sys

ROOT = os.environ.get("FROGNET_SEMANTIC_ROOT",
                      "/home/claude/descend_good/opt/frognet_semantic")
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
from discovery.kernel import FakeKernel, _parse_route_argv  # noqa: E402

KTABLE = os.environ["FROGSIM_KTABLE"]
KADDR = os.environ.get("FROGSIM_KADDR", "")


def _load_table():
    if not os.path.exists(KTABLE):
        return []
    return [ln.rstrip("\n") for ln in open(KTABLE) if ln.strip()]


def _save_kernel(k):
    lines = _lines(k.route_show())
    with open(KTABLE, "w") as f:
        f.write("\n".join(lines) + ("\n" if lines else ""))


def _lines(show):
    return [x for x in show.replace(";", "\n").splitlines() if x.strip()]


def _kernel():
    k = FakeKernel()
    lines = _load_table()
    if lines:
        k.seed(*lines)
    return k


def _addrs():
    """dev -> cidr from the addr file."""
    out = {}
    if KADDR and os.path.exists(KADDR):
        for ln in open(KADDR):
            ln = ln.strip()
            if not ln:
                continue
            dev, cidr = ln.split()[:2]
            out.setdefault(dev, cidr)
    return out


def _net(cidr):
    ip, _, bits = cidr.partition("/")
    bits = int(bits) if bits else 32
    o = [int(x) for x in ip.split(".")]
    v = (o[0] << 24) | (o[1] << 16) | (o[2] << 8) | o[3]
    mask = (0xFFFFFFFF << (32 - bits)) & 0xFFFFFFFF if bits else 0
    return v & mask, bits, mask


def _in(dst_v, cidr):
    base, bits, mask = _net(cidr)
    return (dst_v & mask) == base, bits


def _dst_int(ip):
    o = [int(x) for x in ip.split(".")]
    return (o[0] << 24) | (o[1] << 16) | (o[2] << 8) | o[3]


def cmd_route_get(argv):
    # argv after 'route get': <dst> [oif <dev>]
    dst = argv[0]
    oif = ""
    if "oif" in argv:
        oif = argv[argv.index("oif") + 1]
    dst_v = _dst_int(dst)
    k = _kernel()
    best = None  # (bits, -metric, entry)
    for ln in _lines(k.route_show()):
        _, e, _ = _parse_route_argv(["__x__", *ln.split()])
        if e is None:
            continue
        cidr = "0.0.0.0/0" if e.dest == "default" else (e.dest if "/" in e.dest else e.dest + "/32")
        ok, bits = _in(dst_v, cidr)
        if not ok:
            continue
        if oif and e.dev and e.dev != oif:
            continue
        key = (bits, -(e.metric or 0))
        if best is None or key > best[0]:
            best = (key, e)
    addrs = _addrs()
    if best is None:
        # unreachable
        sys.stderr.write(f"RTNETLINK answers: Network is unreachable\n")
        return 2
    e = best[1]
    # preferred src: the route's own src, else the connected addr on the dev
    src = e.src
    if not src and e.dev in addrs:
        src = addrs[e.dev].split("/")[0]
    parts = [dst]
    if e.via:
        parts += ["via", e.via]
    parts += ["dev", e.dev]
    if src:
        parts += ["src", src]
    parts += ["uid", "0"]
    print(" ".join(parts))
    print("    cache")
    return 0


def cmd_route_show(argv):
    k = _kernel()
    filt = ""
    if argv:
        filt = argv[0]
    if filt == "default":
        for ln in _lines(k.route_show("default")):
            print(ln)
        return 0
    dest = filt if (filt and filt != "all") else ""
    for ln in _lines(k.route_show(dest)):
        print(ln)
    return 0


def cmd_route_mut(op, argv):
    k = _kernel()
    rc = k.route(op, *argv)
    _save_kernel(k)
    return rc if isinstance(rc, int) else 0


def cmd_addr_show(argv):
    addrs = _addrs()
    devfilt = argv[-1] if argv and not argv[-1].startswith("-") else ""
    idx = 1
    for dev, cidr in addrs.items():
        idx += 1
        if devfilt and dev != devfilt:
            continue
        print(f"{idx}: {dev}    inet {cidr} scope global {dev}\\       "
              f"valid_lft forever preferred_lft forever")
    return 0


def main(argv):
    # strip leading option flags like -4 -o -br etc, but remember -o/-4
    args = [a for a in argv if a not in ("-4", "-6", "-o", "-br", "-json",
                                         "-family", "inet")]
    if not args:
        return 0
    obj = args[0]
    rest = args[1:]
    if obj == "route":
        if not rest:
            return cmd_route_show([])
        sub = rest[0]
        if sub == "get":
            return cmd_route_get(rest[1:])
        if sub == "show" or sub == "list":
            return cmd_route_show(rest[1:])
        if sub in ("replace", "add", "del", "delete", "change", "append"):
            op = "del" if sub in ("del", "delete") else ("replace" if sub in ("replace", "change") else "add")
            return cmd_route_mut(op, rest[1:])
        return cmd_route_show([])
    if obj == "addr" or obj == "address":
        # forms: addr show [dev] / addr add CIDR dev DEV
        if rest and rest[0] in ("show", "list", "", None):
            return cmd_addr_show(rest[1:])
        if rest and rest[0] in ("add", "del", "delete", "replace"):
            # addr mutations: update the addr file
            try:
                cidr = rest[1]
                dev = rest[rest.index("dev") + 1]
                lines = []
                if KADDR and os.path.exists(KADDR):
                    lines = [l.rstrip() for l in open(KADDR) if l.strip()]
                if rest[0] == "add":
                    lines.append(f"{dev} {cidr}")
                else:
                    lines = [l for l in lines if not (l.split()[0] == dev and l.split()[1] == cidr)]
                if KADDR:
                    open(KADDR, "w").write("\n".join(lines) + "\n")
            except Exception:
                pass
            return 0
        return cmd_addr_show(rest)
    if obj == "neigh":
        nf = os.environ.get("FROGSIM_KNEIGH", "")
        if nf and os.path.exists(nf):
            sys.stdout.write(open(nf).read())
        return 0
    if obj == "link":
        if rest and rest[0] in ("show", "list"):
            addrs = _addrs(); i = 1
            for dev in list(addrs.keys()) + ["lo"]:
                i += 1
                print(f"{i}: {dev}: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 state UP mode DEFAULT")
            return 0
        return 0
    if obj == "-V" or obj == "version":
        print("ip utility, fake_ip frogsim")
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
