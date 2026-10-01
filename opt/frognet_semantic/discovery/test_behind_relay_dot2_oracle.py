"""test_behind_relay_dot2_oracle.py - a node BEHIND a seg-relay is found on .2 or
not at all. [DISCOVERY_ON_DOT2_ONLY_V1], John 2026-09-25: all discovery happens on
.2, and NO FALLBACKS.

Replaces test_behind_relay_pingpong_oracle, which asserted the [PROVE_DOT1_V1]
rescue: when X's .2 did not reflect through an avenue, _prove_dot1 installed a
metric-6 /32 to X's PRODUCTION .1 through that avenue and ping-ponged it. On
Seattle5 2026-09-25 that pointed 10.199.199.1 (databasehost), 10.251.251.1
(databasehost_control), 10.155.155.1 and 10.123.123.1 at 10.250.250.100 (AI-Host),
.20 and .85 in turn, ~2 s each, every merge - and still produced no candidate.

Node-faithful Seattle5 segment:
  - eth0 10.250.250.0/24; Seattle5 is .1.
  - 10.250.250.191 is Seattle3 (a seg-relay: echoes remote identity 10.130.130.1).
  - .20/.85/.134 are PLAIN clients (no FrogNet identity).
  - Seattle2 (10.120.120.1) sits BEHIND Seattle3, surfaced only by Seattle3's getHosts.
  - Three tunnels: wg0->BABox, wg1->BAMacBook, wg2->New-York-1.

Two runs:
  reach: Seattle2's .2 reflects through .191   -> 10.120.120.0/24 via .191 INSTALLS.
  dark:  Seattle2's .2 does NOT reflect through .191, while its .1 WOULD pong
         through .191 (the fixture makes the old rescue succeed)
         -> NO route to Seattle2, a PROBE_FAIL line naming x, the .2 target, dev,
            via and src, and no .1 address is ever given a probe route.

FAILS on the old code: in `dark` it probes 10.120.120.1 via .191, gets the pong and
installs the route. PASSES when discovery stays on .2.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from discovery.discovery import Discovery
from discovery.routes import Routes
from discovery.kernel import FakeKernel
from discovery.sources import (FakeEcho, FakeRtt, FakeGetHosts, FakeBroker,
                               FakeReflect, FakeVerify, HostStore)
from discovery import descend as _descend

IDENT = "10.250.250.1"

MESH5 = [("10.250.250.1", "Seattle5"), ("10.130.130.1", "Seattle3"),
         ("10.102.60.1", "New-York-1"), ("10.111.11.1", "BABox"),
         ("10.179.178.1", "BAMacBook")]
S3LIST = [("10.250.250.1", "Seattle5"), ("10.130.130.1", "Seattle3"),
          ("10.102.60.1", "New-York-1"), ("10.111.11.1", "BABox"),
          ("10.120.120.1", "Seattle2"), ("10.179.178.1", "BAMacBook")]


def build(seattle2_dot2_reflects_via_relay):
    logs = []
    echo = FakeEcho(answers={
        "10.250.250.191": "Seattle3,10.130.130.1,10.250.250.191,0.0.0.0",  # seg-relay
        "10.111.11.2":  "BABox,10.111.11.1,,",
        "10.179.178.2": "BAMacBook,10.179.178.1,,",
        "10.102.60.2":  "New-York-1,10.102.60.1,,",
        "10.250.250.2": "Seattle5,10.250.250.1,,",
    })
    rtt = FakeRtt(table={
        ("eth0", "10.250.250.191"): [16],
        ("wg0", "10.111.11.2"): [120], ("wg1", "10.179.178.2"): [115],
        ("wg2", "10.102.60.2"): [119],
    })
    gethosts = FakeGetHosts(children={
        "10.250.250.1": MESH5,
        "10.130.130.1": S3LIST,    # Seattle3 surfaces its child Seattle2
        "10.111.11.1": MESH5, "10.179.178.1": MESH5, "10.102.60.1": MESH5,
    })
    broker = FakeBroker(by_one={}, iface_channel={
        "wg0": "BABox-10.111.11", "wg1": "BAMacBook-10.179.178", "wg2": "New-York-1-10.102.60"})
    k = FakeKernel()
    k.seed(
        "default via 192.168.0.1 dev wlan1 metric 601",
        "10.250.250.0/24 dev eth0 proto kernel scope link src 10.250.250.1",
        "10.111.11.0/24 dev wg0 scope link metric 22",
        "10.179.178.0/24 dev wg1 scope link metric 22",
        "10.102.60.0/24 dev wg2 scope link metric 22",
    )
    routes = Routes(k, logger=lambda s: None, clock=lambda: 0.0)
    reach = {
        ("10.130.130.2", "eth0", "10.250.250.191"),
        ("10.111.11.2", "wg0", ""), ("10.179.178.2", "wg1", ""), ("10.102.60.2", "wg2", ""),
    }
    if seattle2_dot2_reflects_via_relay:
        reach.add(("10.120.120.2", "eth0", "10.250.250.191"))
    reflect = FakeReflect(kernel=k, reach=reach)
    # Every .1 pongs through the avenue that forwards to it - including Seattle2's
    # through .191. A .1 rescue would therefore always succeed in this fixture.
    verify = FakeVerify(kernel=k, reach={
        ("10.130.130.1", "eth0", "10.250.250.191"),
        ("10.120.120.1", "eth0", "10.250.250.191"),
    })

    disc = Discovery(routes, echo, rtt, gethosts, broker, HostStore(),
                     local_ips={"10.250.250.1", "10.250.250.2", "127.0.0.1"},
                     dev_src_map={"eth0": "10.250.250.1"},
                     reflect=reflect, self_identity=IDENT, verify=verify,
                     logger=logs.append)
    disc.DEAD_IFACES = set()
    immediate = [
        ("10.111.11.1", "wg0"), ("10.179.178.1", "wg1"), ("10.102.60.1", "wg2"),
        ("10.250.250.191", "eth0"),
        ("10.250.250.20", "eth0"), ("10.250.250.85", "eth0"), ("10.250.250.134", "eth0"),
    ]
    k.mutate_log.clear()
    _descend.descend(disc, ["eth0", "wg0", "wg1", "wg2"], immediate)
    probe_writes = list(k.mutate_log)
    disc.promote()
    return k.route_show(), logs, probe_writes


def _has(table, dest, via):
    for line in table.replace(";", "\n").splitlines():
        if line.startswith(dest) and "dev eth0" in line:
            if via == "" or f"via {via} " in (line + " "):
                return True
    return False


def _routed_via_self(table, dest):
    for line in table.replace(";", "\n").splitlines():
        if line.startswith(dest) and "via 10.250.250.1 " in (line + " "):
            return True
    return False


def _dot1_probe_routes(writes):
    """Every host route discovery wrote to a .1 during descend."""
    out = []
    for verb, dest, _metric, _rc in writes:
        host = dest[:-3] if dest.endswith("/32") else dest
        if "/" not in host and host.endswith(".1"):
            out.append((verb, dest))
    return out


def main():
    reach_tbl, reach_logs, reach_writes = build(seattle2_dot2_reflects_via_relay=True)
    dark_tbl, dark_logs, dark_writes = build(seattle2_dot2_reflects_via_relay=False)
    dark_blob = "\n".join(dark_logs)
    checks = []

    checks.append(("Seattle3 10.130.130.0/24 via .191 (reach run)",
                   _has(reach_tbl, "10.130.130.0/24", "10.250.250.191")))
    checks.append(("Seattle3 10.130.130.0/24 via .191 (dark run)",
                   _has(dark_tbl, "10.130.130.0/24", "10.250.250.191")))

    checks.append(("Seattle2 10.120.120.0/24 via .191 when its .2 reflects through the relay",
                   _has(reach_tbl, "10.120.120.0/24", "10.250.250.191")))

    checks.append(("Seattle2 NOT routed when its .2 is dark, even though its .1 would pong",
                   not _has(dark_tbl, "10.120.120.0/24", "")))

    named = [l for l in dark_logs if l.startswith("PROBE_FAIL plane=.2 x=10.120.120.1 ")
             and "target=10.120.120.2" in l and "dev=eth0" in l
             and "via=10.250.250.191" in l and "src=10.250.250.1" in l
             and "reason=reflect_verdict=" in l]
    checks.append(("dark .2 is a PROBE_FAIL naming x, target .2, dev, via and src",
                   bool(named)))

    d1r, d1d = _dot1_probe_routes(reach_writes), _dot1_probe_routes(dark_writes)
    checks.append((f"no .1 address given a probe route (reach run: {d1r or 'none'})", not d1r))
    checks.append((f"no .1 address given a probe route (dark run: {d1d or 'none'})", not d1d))

    checks.append(("Seattle2 NOT routed via self 10.250.250.1",
                   not _routed_via_self(reach_tbl, "10.120.120.0/24")))
    checks.append(("Seattle3 NOT routed via self 10.250.250.1",
                   not _routed_via_self(reach_tbl, "10.130.130.0/24")))

    print("=== BEHIND-RELAY .2 ORACLE ===")
    for name, ok in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
    if all(ok for _, ok in checks):
        print("ALL BEHIND-RELAY DOT2 ORACLE CHECKPOINTS PASS")
        return 0
    print("BEHIND-RELAY DOT2 ORACLE FAILED")
    print("--- dark table ---"); print(dark_tbl)
    print("--- dark PROBE_FAIL lines ---")
    print("\n".join(l for l in dark_logs if l.startswith("PROBE_FAIL")) or "(none)")
    return 1


if __name__ == "__main__":
    sys.exit(main())
