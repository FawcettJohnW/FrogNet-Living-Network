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
frognet_hosts_safeboot.py — boot-time neutralizer for databasehost.frognet.

/etc/hosts persists across reboots. The databasehost.frognet line from the previous run may
point at a REMOTE host we lost during downtime (it left, we're now isolated, or it is no longer
the database host). Any autonomous sender that fires at boot before discovery re-runs — daemon/
proxy metrics, sensor writes, the reaper — would send to that stale host. So at boot we point
databasehost.frognet at localhost: a pre-convergence write lands on us (harmless / our own pond),
never on a stale peer. Discovery drops this line BY NAME and writes the elected host at the first
converged merge; if we stay isolated, localhost is the correct answer anyway (we are our own pond).

Ordered before the senders; idempotent; safe to run every boot.
"""
from __future__ import annotations
import os, sys, subprocess

DBHOST_NAME = "databasehost.frognet"
LOCAL = "127.0.0.1"
HOSTS = "/etc/hosts"


def neutralize(lines, name=DBHOST_NAME, local=LOCAL):
    """Point `name` at localhost, preserving any other names that shared its line and every
    other entry. Collapses to a single localhost line for `name`. Idempotent."""
    out, seen = [], False
    for ln in lines:
        s = ln.strip()
        if s and not s.startswith("#"):
            parts = s.split()
            if len(parts) >= 2 and name in parts[1:]:
                if not seen:
                    out.append(f"{local} {name}"); seen = True
                others = [p for p in parts[1:] if p != name]
                if others:
                    out.append(" ".join([parts[0]] + others))     # keep co-located names at their IP
                continue
        out.append(ln)
    if not seen:
        out.append(f"{local} {name}")
    return out


def _hup_dnsmasq():
    try:
        subprocess.run(["pkill", "-HUP", "-x", "dnsmasq"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass


def main():
    try:
        lines = [l.rstrip("\n") for l in open(HOSTS)]
    except OSError:
        return 0
    new = neutralize(lines)
    if new != lines:
        tmp = HOSTS + ".safeboot.tmp"
        with open(tmp, "w") as f:
            f.write("\n".join(new) + "\n")
        os.replace(tmp, HOSTS)
        _hup_dnsmasq()
        print(f"safeboot: {DBHOST_NAME} -> {LOCAL} until discovery re-elects")
    return 0


if __name__ == "__main__":
    sys.exit(main())
