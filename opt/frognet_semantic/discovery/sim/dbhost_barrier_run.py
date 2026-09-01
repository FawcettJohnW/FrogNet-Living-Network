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
dbhost_barrier_run.py - simulate the ENTIRE service-election run end to end.

Flow (John's design): merge converges -> control is the highest .1 -> every live
machine publishes its capability to _control -> WAIT until _control holds a fresh
record for every live machine -> only THEN decide databasehost, and off the same
loop mediahost + boardgame. Proves, against the REAL discovery.hosts barrier:

  [STANDALONE] a lone FrogNetHost is ready at once (it is the only machine and it
               published) -> it is its own databasehost + mediahost + boardgame.
  [DEFER]      3 live, only 2 have published -> NOT ready -> databasehost pins to
               control, media/games are not decided yet (no split on a partial pool).
  [PARTIAL_STALE] a live machine whose record aged past the ballot window counts as
               NOT published -> still deferred.
  [COMPLETE]   every live machine has a fresh record -> ready -> decide over the
               full pool, identically on every node.

Only the tuple store is faked; the barrier logic is the real one.
"""
import sys
import time
import types


def _install(recorded_ips, ts_offset=0):
    """Fake frognet_tuples so _capability_index sees exactly `recorded_ips` as the
    set that has published a databasehost capability, each aged `ts_offset` seconds."""
    now = int(time.time())
    rows = {("databasehost", "capability"): [
        {"addr": ip, "var": "capability",
         "value": {"capability": {"lan_ip": ip, "mysql_running": True},
                   "ts": now - ts_offset}}
        for ip in recorded_ips]}
    T = types.ModuleType("frognet_tuples")
    T._rows = rows
    T.get = lambda s, v, dbhost=None, fresh_s=0, timeout=4.0: T._rows.get((s, v), [])
    T.put = lambda *a, **k: True
    T.DEFAULT_DBHOST = "databasehost_control.frognet"
    for m in ("frognet_tuples", "core.frognet_tuples"):
        sys.modules[m] = T
    import core as _core
    _core.frognet_tuples = T


def _block(dot1s, ctl):
    """A converged /etc/hosts block: one FrogNetHost line per live .1 + control."""
    lines = [f"{ip} FrogNetHost.{ip.replace('.', '-')}" for ip in dot1s]
    lines.append(f"{ctl} databasehost_control.frognet")
    return lines


def run():
    sys.modules.pop("discovery.hosts", None)
    from discovery.hosts import databasehost_barrier_ready
    probs = []

    A, B, C = "10.250.250.1", "10.160.160.1", "10.120.120.1"

    # [STANDALONE] lone node, it published -> ready
    _install([A])
    ready, alive, rec = databasehost_barrier_ready(_block([A], A), dbhost=A)
    if not (ready and alive == {A} and rec == {A}):
        probs.append(f"[STANDALONE] expected ready/self, got ready={ready} alive={alive} rec={rec}")

    # [DEFER] 3 live, C has not published -> NOT ready, C is the missing one
    _install([A, B])
    ready, alive, rec = databasehost_barrier_ready(_block([A, B, C], A), dbhost=A)
    if ready or (alive - rec) != {C}:
        probs.append(f"[DEFER] expected defer missing={{{C}}}, got ready={ready} missing={alive - rec}")

    # [PARTIAL_STALE] all three have a row, but C's aged past the 1800s ballot window
    _install([A, B], ts_offset=0)
    # add a STALE C row to the same store
    now = int(time.time())
    sys.modules["core.frognet_tuples"]._rows[("databasehost", "capability")].append(
        {"addr": C, "var": "capability",
         "value": {"capability": {"lan_ip": C, "mysql_running": True}, "ts": now - 4000}})
    ready, alive, rec = databasehost_barrier_ready(_block([A, B, C], A), dbhost=A)
    if ready or C in rec:
        probs.append(f"[PARTIAL_STALE] stale C must not count, got ready={ready} rec={rec}")

    # [COMPLETE] every live machine has a fresh record -> ready
    _install([A, B, C])
    ready, alive, rec = databasehost_barrier_ready(_block([A, B, C], A), dbhost=A)
    if not (ready and alive == {A, B, C} and {A, B, C} <= rec):
        probs.append(f"[COMPLETE] expected ready over full pool, got ready={ready} alive={alive} rec={rec}")

    print("=== DBHOST BARRIER FULL-RUN SIM ===")
    for name, ok in [("STANDALONE ready -> self is every role", "[STANDALONE]" not in str(probs)),
                     ("DEFER on a partial pool (missing publisher)", "[DEFER]" not in str(probs)),
                     ("stale record counts as not-published", "[PARTIAL_STALE]" not in str(probs)),
                     ("COMPLETE pool -> decide", "[COMPLETE]" not in str(probs))]:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
    if not probs:
        print("ALL DBHOST-BARRIER RUN CHECKPOINTS PASS")
        return 0
    for p in probs:
        print(f"  detail: {p}")
    print("DBHOST-BARRIER RUN FAILED")
    return 1


def main():
    return run()


if __name__ == "__main__":
    sys.exit(run())
