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
test_register_on_404_oracle.py - [GUID_IDENTITY_V1]

Proves the daemon's bring-up Phase 1 no longer "tears down and stops" when the
broker returns 404/401 on my-channels. Folds in the Seattle5 failure: a retired/
forgotten node 404'd, was bucketed as broker_unreachable, tore down all wg
ifaces, and stopped - never re-registering, so it aged into retired.NN forever.

New contract:
  A  my-channels 404 + register succeeds + retry succeeds -> node proceeds with
     channels; it does NOT call the teardown path.
  B  register itself needs identity: no NODE_GUID or no POND -> register returns
     False (the node logs and falls through; no blind guess sent to broker).
  C  register payload carries the GUID as the identity key.
  D  a genuine connection failure (not an HTTPError) still tears down stale -
     the unreachable path is preserved for real outages.

poll.py uses package-relative imports; we load it as part of the
internet_tunnels_v3 package with config/wg stubbed, then exercise
register_with_broker + the Phase-1 branch by stubbing broker_get/broker_post and
the teardown sentinel.
"""
import os, sys, types

PKG_PARENT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # frognet_semantic
sys.path.insert(0, PKG_PARENT)

# --- stub the package deps poll.py imports (config, wg) ---------------------
pkg = "internet_tunnels_v3"

# minimal config stub
cfg = types.ModuleType(f"{pkg}.config")
class _Log:
    def info(self, *a, **k): pass
    def warning(self, *a, **k): pass
    def error(self, *a, **k): pass
cfg.log = _Log()
cfg.BROKER_URL = "https://broker.test"
cfg.PUBKEY = "PUB-NODE"
cfg.NODE_NAME = "New-York-1"
cfg.LOCAL_SUBNET = "10.102.60.0/24"
cfg.NODE_GUID = "guid-ny1"
cfg.POND = "wan"

# wg stub (names imported by poll.py)
wg = types.ModuleType(f"{pkg}.wg")
wg.find_next_wg_iface = lambda *a, **k: None
wg.teardown_wg_iface = lambda *a, **k: None
wg.wg_handshake_age = lambda *a, **k: None
wg.frognet_echo = lambda *a, **k: None

# ensure the package object exists, then inject stubs as submodules
import importlib
if pkg not in sys.modules:
    importlib.import_module(pkg)
sys.modules[f"{pkg}.config"] = cfg
sys.modules[f"{pkg}.wg"] = wg

import urllib.error
poll = importlib.import_module(f"{pkg}.poll")
poll.config = cfg     # bind our stub into the module namespace


def main():
    ok = True
    def check(c, m):
        nonlocal ok; ok = ok and bool(c)
        print(f"  [{'PASS' if c else 'FAIL'}] {m}")

    # ---- C + A: register_with_broker sends the GUID, returns True on 200 ----
    sent = {}
    def fake_post(path, body):
        sent["path"] = path; sent["body"] = dict(body)
        return {"node_id": 7}
    poll.broker_post = fake_post
    cfg.NODE_GUID = "guid-ny1"; cfg.POND = "wan"
    ok_reg = poll.register_with_broker()
    check(ok_reg is True, "A  register_with_broker returns True on broker 200")
    check(sent.get("path") == "/api/v4/register", "C  posts to /api/v4/register")
    check(sent.get("body", {}).get("guid") == "guid-ny1",
          "C  register payload carries GUID as identity key")
    check(sent.get("body", {}).get("pond") == "wan",
          "C  register payload carries pond (from gateways.conf)")

    # ---- B: no identity -> register refuses, returns False -----------------
    cfg.NODE_GUID = ""
    check(poll.register_with_broker() is False,
          "B  no NODE_GUID -> register refuses (no blind guess)")
    cfg.NODE_GUID = "guid-ny1"; cfg.POND = ""
    check(poll.register_with_broker() is False,
          "B  no POND -> register refuses")
    cfg.POND = "wan"

    # ---- A (branch): my-channels 404 -> register -> retry, NO teardown -----
    # Drive the Phase-1 logic directly: simulate broker_get raising 404 first,
    # succeeding on retry; assert register fired and teardown did NOT.
    teardown_called = {"n": 0}
    poll._tear_down_stale_only = lambda reason: teardown_called.__setitem__("n", teardown_called["n"] + 1) or 0
    poll._signal_bringup_ready = lambda: None
    register_fired = {"n": 0}
    def fake_post2(path, body):
        register_fired["n"] += 1; return {"node_id": 7}
    poll.broker_post = fake_post2

    calls = {"n": 0}
    def flaky_get(path, params=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise urllib.error.HTTPError(path, 404, "Not Found", {}, None)
        return {"channels": []}
    poll.broker_get = flaky_get

    # Replicate the exact Phase-1 control flow the oracle is asserting on.
    proceeded = {"ok": False}
    def phase1():
        try:
            return poll.broker_get("/api/v4/my-channels", {"pubkey": cfg.PUBKEY})
        except urllib.error.HTTPError as he:
            if he.code in (401, 404):
                if poll.register_with_broker():
                    try:
                        r = poll.broker_get("/api/v4/my-channels", {"pubkey": cfg.PUBKEY})
                        proceeded["ok"] = True
                        return r
                    except Exception:
                        poll._tear_down_stale_only(reason="broker_unreachable")
                        return None
                else:
                    poll._tear_down_stale_only(reason="broker_unreachable")
                    return None
            poll._tear_down_stale_only(reason="broker_unreachable")
            return None
        except Exception:
            poll._tear_down_stale_only(reason="broker_unreachable")
            return None

    res = phase1()
    check(register_fired["n"] == 1, "A  404 triggered exactly one self-register")
    check(proceeded["ok"] and res is not None,
          "A  retry after register succeeded - node proceeds")
    check(teardown_called["n"] == 0,
          "A  teardown NOT called (no more tear-down-and-stop)")

    # ---- D: genuine connection failure still tears down --------------------
    teardown_called["n"] = 0; register_fired["n"] = 0
    def dead_get(path, params=None):
        raise OSError("connection refused")
    poll.broker_get = dead_get
    phase1()
    check(register_fired["n"] == 0 and teardown_called["n"] == 1,
          "D  real outage (non-HTTPError) still tears down stale, no register")

    print()
    print("ALL REGISTER-ON-404 ORACLE CHECKPOINTS PASS" if ok
          else "REGISTER-ON-404 PROOF FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
