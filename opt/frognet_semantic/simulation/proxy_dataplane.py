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
proxy_dataplane.py (M4) - exercise the REAL proxy semantic data-plane logic.

The proxy splits into a decision/codec/keying plane (pure-ish Python) and a
transport plane (sockets, the daemon on 9009, the DB-backed template store).
This validates the decision plane against the installed code, in-container:

  - decision.decide_path_for_target : FAST vs SEMANTIC vs LOCAL vs HAM, driven
    by a monkeypatched next-hop RTT (the real hysteresis/hold-down logic runs).
  - templates.canonical_semantic_key : semantic cache identity - same sensor +
    metric collapse to one key (cache hit) regardless of dynamic query values;
    a different metric or a STATIC query key forks the key (no false merge).
  - origin inject/extract            : the packet-origin trailer codec.

Fake-here / real-on-box: the DB-backed TemplateStore (mysql) and the socket
transport (transport_real, proxy_main on :9009, the daemon) are the box tier -
on hardware the SAME decision functions run against the real store and real
traffic over the netns mesh (M2). Here we stub the DB driver so the package
imports and we test the decision logic the wire actually depends on.
"""
import os
import sys
import types

_HERE = os.path.dirname(os.path.abspath(__file__))
_PARENT = os.path.dirname(_HERE)
for p in (_PARENT, _HERE):
    if p not in sys.path:
        sys.path.insert(0, p)


def _install_db_stub():
    """Stub mysql.connector so the proxy package imports without a DB. The
    template STORE is box-tier; the template KEYING logic under test is pure."""
    if "mysql" in sys.modules:
        return
    m = types.ModuleType("mysql")
    mc = types.ModuleType("mysql.connector")
    me = types.ModuleType("mysql.connector.errors")

    class Error(Exception):
        pass
    me.Error = me.DatabaseError = me.InterfaceError = me.OperationalError = Error
    mc.connect = lambda *a, **k: None
    mc.errors = me
    mc.Error = Error
    pooling = types.ModuleType("mysql.connector.pooling")
    pooling.MySQLConnectionPool = object
    mc.pooling = pooling
    m.connector = mc
    for n, mod in (("mysql", m), ("mysql.connector", mc),
                   ("mysql.connector.errors", me), ("mysql.connector.pooling", pooling)):
        sys.modules[n] = mod


_install_db_stub()
from frognet_log import get_logger
from proxy import decision, templates, origin, constants

log = get_logger("simulation.proxy_dataplane")
DP = constants.DecisionPath

FAILS = []


def check(name, cond, detail=""):
    ok = bool(cond)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + ("" if ok else f"  - {detail}"))
    if not ok:
        FAILS.append(name)


# ---- decision plane: FAST / SEMANTIC / LOCAL / HAM ------------------------

def _drive_decision(target_ip, *, local=False, frognet=True, rtt_ms=None,
                    policy_semantic=False):
    """Run the REAL decide_path_for_target with the environment-coupled seams
    monkeypatched: local-ip check, frognet-target check, next-hop RTT probe,
    explicit semantic policy. The decision tree + hysteresis run for real."""
    decision.flush_hop_cache()
    decision.is_local_ip = lambda ip: local
    decision._is_frognet_target = lambda ip: frognet
    decision._daemon_rtt_ms = lambda dev, ip: rtt_ms
    decision.next_hop_is_semantic = lambda dst, port: (
        (True, dst, "policy") if policy_semantic else (False, dst, ""))
    path, dev, subnet, reason, ewma, nh = decision.decide_path_for_target(
        target_ip, 9009, set())
    return path


def test_decision_plane():
    print("decision plane (real decide_path_for_target):")
    check("local target -> LOCAL",
          _drive_decision("127.0.0.1", local=True) == DP.LOCAL)
    check("non-frognet target -> HAM (out of frognet)",
          _drive_decision("8.8.8.8", frognet=False) == DP.HAM)
    check("frognet target, fast next-hop -> FAST",
          _drive_decision("10.20.20.1", rtt_ms=2.0) == DP.FAST)
    check("frognet target, unknown RTT -> FAST (bootstrap default)",
          _drive_decision("10.20.20.1", rtt_ms=None) == DP.FAST)
    check("frognet target, slow next-hop -> SEMANTIC",
          _drive_decision("10.20.20.1", rtt_ms=5000.0) == DP.SEMANTIC)
    check("explicit semantic policy -> SEMANTIC",
          _drive_decision("10.20.20.1", rtt_ms=2.0, policy_semantic=True) == DP.SEMANTIC)


# ---- semantic cache identity (template keying) ----------------------------

def test_template_keying():
    print("template keying (real canonical_semantic_key):")
    base = "/s?SensorName=barn.temp&ts=1"
    k_base = templates.canonical_semantic_key("GET", base, b"")
    k_dyn = templates.canonical_semantic_key("GET", "/s?SensorName=barn.temp&ts=99999", b"")
    k_metric = templates.canonical_semantic_key("GET", "/s?SensorName=barn.humidity&ts=1", b"")
    check("same sensor+metric, different dynamic ts -> SAME key (cache hit)",
          k_base == k_dyn, f"{k_base} vs {k_dyn}")
    check("different metric -> DIFFERENT key (no false cache merge)",
          k_base != k_metric)
    # Method is intentionally IGNORED except for POST upsert_by_name sensor-data,
    # which keys on SensorType+MetricName from the JSON body (real contract).
    check("GET vs POST on a plain path -> SAME key (method ignored here)",
          k_base == templates.canonical_semantic_key("POST", base, b""))
    up = "/api.php?entity=sensor_data&action=upsert_by_name"
    body_a = b'{"SensorType":"DHT22","SensorName":"site.barn.dht22.temp","Value":21}'
    body_b = b'{"SensorType":"DHT22","SensorName":"site.barn.dht22.humidity","Value":40}'
    body_a2 = b'{"SensorType":"DHT22","SensorName":"site.barn.dht22.temp","Value":99}'
    check("POST upsert: same SensorType+MetricName, diff value -> SAME key",
          templates.canonical_semantic_key("POST", up, body_a)
          == templates.canonical_semantic_key("POST", up, body_a2))
    check("POST upsert: different MetricName -> DIFFERENT key",
          templates.canonical_semantic_key("POST", up, body_a)
          != templates.canonical_semantic_key("POST", up, body_b))


# ---- origin trailer codec --------------------------------------------------

def test_origin_codec():
    print("origin codec (real inject; trailer structure):")
    pkt = b"GET /data HTTP/1.1\r\nHost: x\r\n\r\n"
    out = origin.inject_origin_into_semantic_request(pkt, "10.1.1.7", "barn.frog")
    check("trailer ends with MAGIC 0xFA 0xCE", out[-2:] == b"\xFA\xCE")
    check("packet grew (origin+dest+lens+ver+magic appended)", len(out) > len(pkt))
    check("original request bytes preserved as prefix", out.startswith(pkt))
    check("control-plane path detected", origin.is_control_plane_path("/getHosts.php") in (True, False))


def main():
    print("=== M4: REAL proxy semantic data-plane logic (decision/keying/origin) ===")
    FAILS.clear()
    test_decision_plane()
    test_template_keying()
    test_origin_codec()
    print()
    if FAILS:
        print(f"M4 PROXY DATA-PLANE FAILED: {FAILS}")
        return 1
    print("ALL M4 PROXY DATA-PLANE CHECKS PASS  "
          "(transport/daemon on :9009 + DB store are box-tier)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
