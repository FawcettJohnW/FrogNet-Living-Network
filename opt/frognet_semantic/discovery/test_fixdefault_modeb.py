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
test_fixdefault_modeb - structural proof of the newly-ported fixDefaultRoute
paths that the oracle never exercises: the FORCED override and Mode B (mesh
exit-host synthesis). Drives FixDefaultRoute over a FakeKernel with injected
fake backends and asserts the resulting default-route table.
"""
from __future__ import annotations

import time

from .kernel import FakeKernel
from .fixdefault import FixDefaultRoute, DEFAULT_ROUTE_METRIC


def _defaults(k):
    return [ln for ln in k.show_default()]


def test_forced_override():
    k = FakeKernel()
    k.seed(
        "10.50.50.0/24 dev eth0 proto kernel scope link src 10.50.50.2",
        "default via 192.168.9.1 dev eth0 metric 100",
    )
    fdr = FixDefaultRoute(
        k,
        dev_ip4={"eth0": "10.50.50.2"},
        up_devs={"eth0"},
        connected_prefixes={"eth0": {"10.50.50"}},
        ping=lambda via, dev: True,
        frognet_interfaces=["eth0"],
        local_ips={"10.50.50.2", "127.0.0.1"},
        forced_line=("10.50.50.1", "eth0"),
        write_exit_sentinel=lambda t: None,
    )
    fdr.run()
    d = _defaults(k)
    assert len(d) == 1, f"forced should leave exactly one default, got {d}"
    line = d[0]
    assert "via 10.50.50.1" in line and "dev eth0" in line, line
    assert f"metric {DEFAULT_ROUTE_METRIC}" in line, line
    assert "192.168.9.1" not in line, "other default must be removed"
    assert " onlink" not in line, "gw on connected subnet -> no onlink"


def test_mode_b_exit_synth():
    captured = {}
    k = FakeKernel()
    # wg0 connected /24 only; no off-LAN candidate, no pre-existing default ->
    # Mode A finds nothing, falls through to Mode B.
    k.seed("10.102.60.0/24 dev wg0 proto kernel scope link src 10.102.60.9")

    def route_get(host):
        return ("wg0", "10.102.60.1")

    def get_default_route(host):
        return {"ok": True, "exit_present": True,
                "ts": int(time.time()), "ttl_sec": 120}

    fdr = FixDefaultRoute(
        k,
        dev_ip4={"wg0": "10.102.60.9"},
        up_devs={"wg0"},
        connected_prefixes={"wg0": {"10.102.60"}},
        ping=lambda via, dev: True,
        frognet_interfaces=["wg0"],
        local_ips={"10.102.60.9", "127.0.0.1"},
        forced_line=None,
        route_get=route_get,
        get_default_route=get_default_route,
        known_hosts=lambda: ["10.102.60.5"],
        semantic_cidrs=set(),  # -> FAST
        write_exit_sentinel=lambda t: captured.setdefault("sent", t),
    )
    fdr.run()
    d = _defaults(k)
    assert len(d) == 1, f"Mode B should install one default, got {d}"
    line = d[0]
    assert "via 10.102.60.1" in line and "dev wg0" in line, line
    assert f"metric {DEFAULT_ROUTE_METRIC}" in line, line
    assert captured.get("sent", "").startswith("10.102.60.5\t10.102.60.1\twg0"), \
        f"exit sentinel not written as expected: {captured}"


def test_mode_b_rejects_stale_or_no_exit():
    """Peer with stale JSON or exit_present=false must NOT be installed."""
    for bad in ({"ok": True, "exit_present": False, "ts": int(time.time()), "ttl_sec": 120},
                {"ok": True, "exit_present": True, "ts": 0, "ttl_sec": 120},
                {"ok": False, "exit_present": True, "ts": int(time.time()), "ttl_sec": 120}):
        k = FakeKernel()
        k.seed("10.102.60.0/24 dev wg0 proto kernel scope link src 10.102.60.9")
        fdr = FixDefaultRoute(
            k, dev_ip4={"wg0": "10.102.60.9"}, up_devs={"wg0"},
            connected_prefixes={"wg0": {"10.102.60"}}, ping=lambda v, d: True,
            frognet_interfaces=["wg0"], local_ips={"10.102.60.9"},
            route_get=lambda h: ("wg0", "10.102.60.1"),
            get_default_route=lambda h: bad,
            known_hosts=lambda: ["10.102.60.5"], semantic_cidrs=set(),
            write_exit_sentinel=lambda t: None)
        fdr.run()
        assert _defaults(k) == [], f"unusable peer must yield no default: {bad}"


def main():
    try:
        test_forced_override()
        test_mode_b_exit_synth()
        test_mode_b_rejects_stale_or_no_exit()
    except AssertionError as e:
        print(f"  fixdefault Mode B/forced FAIL: {e}")
        return 1
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
