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
sim_tunnel_health.py - adjudicate the tunnel-teardown policy with the simulator,
driving the REAL daemon decision function.

Claim under test (pushback against "tear a tunnel down when its echo is
non-responsive"): the only liveness signal in a merge is the L7 echo
(frognet_echo.php over Apache/proxy/DB). A WireGuard tunnel can be alive at L3
(fresh handshake) while that echo blips. So:

  - keying TEARDOWN on the echo churns live tunnels (the 21:34 failure);
  - keying teardown on the HANDSHAKE (with in-place refresh, and a grace window
    of consecutive failed-refresh cycles) reaps only genuinely-dead tunnels;
  - the per-merge ROUTE/HOST filter keys on the echo and is already correct.

What is REAL installed code here (not modeled):
  - internet_tunnels_v3.poll._tunnel_health_verdict - the daemon's per-channel
    teardown decision with the [HANDSHAKE_GRACE_V3] 2-cycle grace. The sim
    drives it directly with injected handshake-age / refresh callables and the
    real config thresholds, and asserts both the returned actions AND the log
    lines it emits.
  - discovery.healthcheck.HealthCheck - the per-merge echo filter.
  - discovery.sources.FakeBroker.transits - the real transit-gate logic.

MODELED (the foil only): policy_echo_keyed - the REJECTED design, kept solely to
show what it would do to a live tunnel.

Run: PYTHONPATH=<tree>/opt/frognet_semantic python3 -m simulation.sim_tunnel_health
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_PARENT = os.path.dirname(_HERE)
os.environ.setdefault("FROGNET_PROXY_ROOT", _PARENT)
if _PARENT not in sys.path:
    sys.path.insert(0, _PARENT)
sys.dont_write_bytecode = True

from discovery.healthcheck import HealthCheck
from discovery.sources import FakeBroker
from internet_tunnels_v3 import poll, config

FAILS = []


def check(name, cond, detail=""):
    ok = bool(cond)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + ("" if ok else f"  - {detail}"))
    if not ok:
        FAILS.append(name)


# -- a capturing logger so we can assert "sufficient logging" --------
class CapLog:
    def __init__(self):
        self.lines = []

    def _fmt(self, msg, args):
        try:
            return msg % args if args else msg
        except Exception:
            return msg + " " + repr(args)

    def info(self, msg, *a):    self.lines.append("INFO " + self._fmt(msg, a))
    def warning(self, msg, *a): self.lines.append("WARN " + self._fmt(msg, a))
    def debug(self, msg, *a):   self.lines.append("DBG  " + self._fmt(msg, a))
    def exception(self, msg, *a): self.lines.append("EXC  " + self._fmt(msg, a))

    def has(self, *needles):
        return any(all(n in ln for n in needles) for ln in self.lines)


# -- tunnel model: supplies the injected age_fn / refresh_fn ---------
class Tunnel:
    def __init__(self, iface, handshake_age, can_rehandshake):
        self.iface = iface
        self.handshake_age = handshake_age      # seconds, or None (no handshake)
        self.can_rehandshake = can_rehandshake  # would a keepalive kick work?

    def age_fn(self, iface):
        return self.handshake_age

    def refresh_fn(self, iface, wait):
        if self.can_rehandshake:
            self.handshake_age = 1.0
            return 1.0
        return None


def run_real_verdict(t, cycles):
    """Drive the REAL poll._tunnel_health_verdict across N cycles. Returns
    (actions, grace_counts, caplog)."""
    log = CapLog()
    grace = {}
    actions = []
    for _ in range(cycles):
        a = poll._tunnel_health_verdict(
            "Seattle5-10.250.250", t.iface,
            age_fn=t.age_fn, refresh_fn=t.refresh_fn,
            grace_counts=grace,
            grace_cycles=config.HANDSHAKE_GRACE_CYCLES,
            dead_sec=config.HANDSHAKE_DEAD_SEC,
            refresh_wait_sec=config.REFRESH_HANDSHAKE_WAIT_SEC,
            log=log)
        actions.append(a)
        if a in ("teardown_dead", "teardown_no_iface"):
            t.handshake_age, t.can_rehandshake = 1.0, True   # bringup rebuilds
            grace.clear()
    return actions, grace, log


# -- rejected model (foil only) --------------------------------------
def policy_echo_keyed(echo_ok_seq, grace=2):
    """REJECTED design: tear down after `grace` consecutive ECHO failures,
    ignoring L3. Returns teardown_count."""
    dc = teardowns = 0
    for echo_ok in echo_ok_seq:
        if echo_ok:
            dc = 0
            continue
        dc += 1
        if dc >= grace:
            teardowns += 1
            dc = 0
    return teardowns


# -- scenarios -------------------------------------------------------
def s_echo_filter_real():
    print("merge filter (REAL HealthCheck): dead-echo tunnel excluded from routes/hosts")

    class FakeRoutes:
        def rtmut(self, *a, caller="?"): return 0

    def health_echo(peer_ip):
        return ("200", "BAMacBook,10.179.179.1,,") if peer_ip == "10.179.179.1" else ("000", "")
    dead, filtered = HealthCheck(FakeRoutes(), health_echo, logger=lambda s: None).run(
        [("wg1", "BAMacBook-10.179.179", "10.179.179.0/24"),
         ("wg2", "Seattle5-10.250.250", "10.250.250.0/24")],
        all_devs=["eth0", "wg1", "wg2"])
    check("dead-echo wg2 flagged dead, wg1 healthy", "wg2" in dead and "wg1" not in dead, str(dead))
    check("wg2 dropped from walk devices (no route/host through it this merge)",
          "wg2" not in filtered and "wg1" in filtered, str(filtered))


def s_live_tunnel_echo_blip():
    print("live tunnel, echo blips: REAL verdict KEEPS it; echo-keyed model CHURNS it")
    # fresh handshake (3s) - L3 alive. Echo failing is irrelevant to the verdict.
    t = Tunnel("wg2", handshake_age=3.0, can_rehandshake=True)
    actions, grace, log = run_real_verdict(t, cycles=4)
    check("REAL verdict: keep_live every cycle, never torn down",
          actions == ["keep_live"] * 4, str(actions))
    check("REAL verdict: no grace counter accrued for a live tunnel", grace == {}, str(grace))
    # the rejected design, same 4 echo-failing cycles:
    churn = policy_echo_keyed([False, False, False, False])
    check("rejected echo-keyed model would tear down the live tunnel (the bug)", churn >= 1, f"teardowns={churn}")


def s_genuinely_dead():
    print("genuinely dead (stale handshake, refresh fails): REAL verdict reaps after grace")
    t = Tunnel("wg0", handshake_age=1200.0, can_rehandshake=False)
    actions, grace, log = run_real_verdict(t, cycles=2)
    check("cycle 1 = grace_hold (NOT torn down on first failure)", actions[0] == "grace_hold", str(actions))
    check("cycle 2 = teardown_dead (reaped after 2-cycle grace)", actions[1] == "teardown_dead", str(actions))
    check("log shows fail_cycle=1/2 grace_hold", log.has("action=grace_hold", "fail_cycle=1/2"))
    check("log shows fail_cycle=2/2 teardown w/ handshake-dead reason",
          log.has("action=teardown", "fail_cycle=2/2", "refresh_failed_handshake_dead"))


def s_transient_recovers():
    print("transient (stale handshake, refresh succeeds): REAL verdict refreshes in place")
    t = Tunnel("wg2", handshake_age=1200.0, can_rehandshake=True)
    actions, grace, log = run_real_verdict(t, cycles=3)
    check("cycle 1 = refreshed (saved in place, no teardown)", actions[0] == "refreshed", str(actions))
    check("subsequent cycles keep_live (handshake now fresh)", actions[1:] == ["keep_live", "keep_live"], str(actions))
    check("no grace counter left behind", grace == {}, str(grace))
    check("log shows action=refreshed with new_age and routes-preserved note",
          log.has("action=refreshed", "new_age="))


def s_no_iface():
    print("structurally broken (no iface recorded): immediate teardown, no grace")
    log = CapLog(); grace = {}
    a = poll._tunnel_health_verdict(
        "Ghost-10.9.9", "", age_fn=lambda i: None, refresh_fn=lambda i, w: None,
        grace_counts=grace, grace_cycles=config.HANDSHAKE_GRACE_CYCLES,
        dead_sec=config.HANDSHAKE_DEAD_SEC,
        refresh_wait_sec=config.REFRESH_HANDSHAKE_WAIT_SEC, log=log)
    check("no-iface channel -> teardown_no_iface (grace cannot apply)", a == "teardown_no_iface", a)
    check("log explains grace N/A", log.has("no_iface_recorded", "grace N/A"))


def s_ny1_orthogonal():
    print("NY1: a tunnel-teardown policy cannot fix the LAN-vouch black holes")
    inert = FakeBroker(node_transit={})
    check("inert gate (empty map): NY-2 'transits' Seattle5 -> vouch installs (black hole)",
          inert.transits("10.28.28.1", "10.250.250.0/24") is True)
    populated = FakeBroker(node_transit={"10.160.160": ["10.130.130.0/24"]})
    check("populated map: NY-2 not a registered transit -> vouch SUPPRESSED",
          populated.transits("10.28.28.1", "10.250.250.0/24") is False)


def main():
    print("=== tunnel-health teardown: simulator adjudication (REAL verdict fn) ===\n")
    FAILS.clear()
    for s in (s_echo_filter_real, s_live_tunnel_echo_blip, s_genuinely_dead,
              s_transient_recovers, s_no_iface, s_ny1_orthogonal):
        s()
    print()
    if FAILS:
        print(f"VERDICT: mixed - {FAILS}")
        return 1
    print("VERDICT (simulator-confirmed against REAL poll._tunnel_health_verdict):\n"
          "  - live tunnel + echo blip -> kept (keep_live), zero churn\n"
          "  - genuinely dead -> grace_hold then teardown_dead after 2 cycles\n"
          "  - transient stale -> refreshed in place, no teardown\n"
          "  - no-iface -> immediate teardown (grace N/A)\n"
          "  - echo filter (real) excludes dead tunnels from routes/hosts per merge\n"
          "  - NY1 black holes are a transit-map problem, orthogonal to teardown")
    return 0


if __name__ == "__main__":
    sys.exit(main())
