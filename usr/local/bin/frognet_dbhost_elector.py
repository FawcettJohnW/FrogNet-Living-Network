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
frognet_dbhost_elector.py — the post-merge databasehost re-evaluation.

Runs 1/min (systemd timer) but does work ONLY when both gates hold:
  GATE 1 — I am the control holder. Control = highest .1 == databasehost_control.frognet.
           Only control elects; it alone holds the full Services/capability picture, and a
           single elector means one writer, never racing views.
  GATE 2 — within WINDOW_S of the last merge. Candidates fill in async AFTER a merge
           converges (frognets emit on the _control change; registered DB boxes on their
           nslookup-delta), so control re-picks each minute for 10 min as the list settles,
           then goes quiet until the next merge re-arms.
Body: run the SAME generic election the merge runs (apply_to_etc_hosts over control's
candidates), write /etc/hosts only if it changed, and SIGHUP dnsmasq on a real change so
downstream resolvers see the new databasehost promptly. Idempotent: settled list -> no
change -> no write -> no HUP.

Memory, not messages: it reads capability tuples on control and the hosts file; it pushes
nothing and is pushed nothing.
"""
from __future__ import annotations
import os, sys, time, subprocess

WINDOW_S = 600                                  # 1/min for 10 min after a merge
LAST_MERGE = "/etc/sentinels/last_merge"
HOSTS = "/etc/hosts"
CONTROL_NAME = "databasehost_control.frognet"


def control_ip(lines):
    """Highest 10.x.x.1 gateway in the hosts block (mirrors hosts.control_host_ip)."""
    ones = []
    for ln in lines:
        if not ln or ln.startswith("#"):
            continue
        ip = ln.split()[0]
        o = ip.split(".")
        if len(o) == 4 and ip.startswith("10.") and o[3] == "1":
            ones.append(ip)
    if not ones:
        return ""
    return max(ones, key=lambda a: tuple(int(x) for x in a.split(".")))


def is_control(lines, my_ip):
    return bool(my_ip) and control_ip(lines) == my_ip


def within_window(now, last_merge, window=WINDOW_S):
    return last_merge > 0 and 0 <= (now - last_merge) <= window


def elect_once(lines, my_ip, now, last_merge, apply_fn):
    """Pure decision. Returns (new_lines, changed). No-op unless I am control AND inside
    the post-merge window. apply_fn(lines, dbhost) is the generic election."""
    if not is_control(lines, my_ip) or not within_window(now, last_merge):
        return lines, False
    ctl = control_ip(lines) or CONTROL_NAME
    new_lines = list(apply_fn(lines, ctl))
    return new_lines, (new_lines != lines)


def _reload_dnsmasq():
    try:
        subprocess.run(["pkill", "-HUP", "-x", "dnsmasq"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass


def _read_last_merge():
    try:
        return int(open(LAST_MERGE).read().strip())
    except Exception:
        return 0


def main():
    # paths for the live election + my FrogNet identity
    sys.path.insert(0, "/opt/frognet_semantic")
    sys.path.insert(0, "/etc/frognet_bundles/communicator")
    # [NO_FALLBACK_V1] `return 0  # never fail the timer` is the fallback in its
    # purest form: the election modules are missing, no election runs, and
    # systemd records a clean success. The timer then reports green forever
    # while /etc/hosts is never updated - which is exactly how nodes end up
    # disagreeing about who the databasehost is with nothing in the journal.
    #
    # A timer that cannot do its job must exit non-zero so `systemctl
    # list-timers` and the unit's status say so.
    # [NO_FALLBACK_V1] Not guarded. These are first-party; if they are missing
    # the traceback names the module and the timer exits non-zero, which is the
    # only way `systemctl list-timers` can tell anyone. The original
    # `except Exception: return 0  # never fail the timer` reported success
    # forever while no election ran and /etc/hosts was never updated.
    from frognet_service_hosts import apply_to_etc_hosts
    from frognet_tuples import my_ip as _my_ip
    # [NO_FALLBACK_V1] Same shape: an unreadable /etc/hosts reported success.
    try:
        lines = [l.rstrip("\n") for l in open(HOSTS)]
    except OSError as e:
        print(f"elector: cannot read {HOSTS}: {type(e).__name__} "
              f"errno={e.errno} ({e.strerror}) - no election performed",
              file=sys.stderr)
        return 1
    apply_fn = lambda ls, dbhost: apply_to_etc_hosts(ls, dbhost=dbhost)
    new_lines, changed = elect_once(lines, _my_ip(), int(time.time()),
                                    _read_last_merge(), apply_fn)
    if changed:
        tmp = HOSTS + ".elector.tmp"
        with open(tmp, "w") as f:
            f.write("\n".join(new_lines) + "\n")
        os.replace(tmp, HOSTS)
        _reload_dnsmasq()
        print("elector: databasehost re-elected; /etc/hosts updated + dnsmasq HUP")
    return 0


if __name__ == "__main__":
    sys.exit(main())
