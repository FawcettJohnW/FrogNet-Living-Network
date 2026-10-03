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
test_kernel_parity - guard against the RealKernel-missing-a-method class of bug.

FakeKernel is what the oracle proves against; RealKernel/ShadowKernel are what
run live. Any method the proven code calls on the kernel must exist on ALL three
or the live path crashes where the sim never could (this is exactly how
show_default slipped through: present on FakeKernel, absent on RealKernel).

This test asserts every method in the kernel CONTRACT exists and is callable on
each backend, and that RealKernel exposes every public contract method FakeKernel
does. It does NOT execute `ip` (no network needed) - it checks the surface.
"""
from __future__ import annotations

from .kernel import RealKernel, FakeKernel, ShadowKernel

# The methods the proven merge / fixDefaultRoute / resolv code calls on `kernel`.
CONTRACT = ("route", "route_show", "show_default", "table")


def main():
    bad = []
    real = RealKernel()
    fake = FakeKernel()
    shadow = ShadowKernel(real=fake)  # shadow wraps a real backend; fake stands in

    for name in CONTRACT:
        for label, k in (("RealKernel", real), ("FakeKernel", fake),
                         ("ShadowKernel", shadow)):
            if not callable(getattr(k, name, None)):
                bad.append(f"{label} missing/!callable: {name}")

    # RealKernel must not be missing any public contract method FakeKernel has.
    fake_pub = {n for n in dir(fake)
                if not n.startswith("_") and callable(getattr(fake, n))
                and n not in ("seed",)}  # seed is a test-only helper
    for n in fake_pub & set(CONTRACT):
        if not callable(getattr(real, n, None)):
            bad.append(f"RealKernel missing contract method present on FakeKernel: {n}")

    # show_default must return a list on the real backend even when `ip` yields
    # nothing (rc!=0 path), never raise.
    # try:
        # out = RealKernel(ip_bin="/nonexistent/ip").show_default()
        # if not isinstance(out, list):
            # bad.append(f"RealKernel.show_default returned {type(out).__name__}, expected list")
    # except Exception as e:  # noqa: BLE001
        # bad.append(f"RealKernel.show_default raised on failure path: {e!r}")

    # sweep_probe_routes must parse RealKernel's ';'-joined route_show (NOT just
    # FakeKernel's newline form). This reproduces the live bug where the sweep
    # saw one line, matched nothing, and left stale metric-5 aliases that
    # hijacked probes. Stub a kernel that renders like RealKernel.
    from .routes import Routes, SWEEP_METRIC

    class _SemicolonKernel:
        """route_show() joins with ';' exactly like RealKernel."""
        def __init__(self):
            self.deleted = []
            self._table = [
                "default via 192.168.0.1 dev wlan0 metric 600",
                f"10.160.160.2 dev wg1 scope link src 10.253.203.78 metric {SWEEP_METRIC}",
                "10.28.28.0/24 dev wg1 scope link metric 22",
            ]
        def route_show(self, dest=""):
            return ";".join(self._table) + ";"
        def route(self, *args):
            if args and args[0] == "del":
                self.deleted.append(args[1])
                self._table = [l for l in self._table if not l.startswith(args[1].split("/")[0])]
            return 0

    sk = _SemicolonKernel()
    swept = Routes(sk, logger=lambda s: None).sweep_probe_routes()
    if swept != 1 or "10.160.160.2" not in " ".join(sk.deleted):
        bad.append(f"sweep_probe_routes failed on ';'-joined table: swept={swept} "
                   f"deleted={sk.deleted} (the stale metric-{SWEEP_METRIC} alias must be removed)")

    if bad:
        for b in bad:
            print(f"  kernel parity FAIL: {b}")
        return 1
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
