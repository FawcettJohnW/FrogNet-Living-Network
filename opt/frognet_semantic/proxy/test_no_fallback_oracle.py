#!/opt/frognet_semantic/venv/bin/python3
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
"""test_no_fallback_oracle.py — [NO_FALLBACK_V1]

Gate for the Tier 1 fallback removals. Each assertion below FAILS on the code
as it shipped and PASSES after. Run from the tree root:

    python3 proxy/test_no_fallback_oracle.py

The two `old_*` cases are documentation, not regression: they assert the OLD
behaviour so the diff has a recorded before-state. They read the pre-change file
if FROGNET_OLD_TREE points at one, and skip otherwise.
"""
import os
import sys
import types

OLD_TREE = os.environ.get("FROGNET_OLD_TREE", "")

_results = []


def check(name, fn):
    try:
        fn()
        _results.append(("PASS", name, ""))
    except AssertionError as e:
        _results.append(("FAIL", name, str(e)))
    except Exception as e:
        _results.append(("ERROR", name, f"{type(e).__name__}: {e}"))


def _load_netutil(src, hosts_path, map_path):
    """Load proxy/netutil.py with stubbed deps and redirected paths, so the
    oracle exercises the real code without touching /etc/hosts."""
    pkg = types.ModuleType("proxy")
    pkg.__path__ = []
    c = types.ModuleType("proxy.constants")
    c.debug = lambda *a, **k: None
    c.DEBUG = False
    sys.modules["proxy"] = pkg
    sys.modules["proxy.constants"] = c
    src = src.replace('_HOSTS_PATH = "/etc/hosts"', f'_HOSTS_PATH = {hosts_path!r}')
    src = src.replace('MAP_INTERFACES_PATH = "/usr/local/bin/mapInterfaces"',
                      f'MAP_INTERFACES_PATH = {map_path!r}')
    src = src.replace('path = "/usr/local/bin/mapInterfaces"', f'path = {map_path!r}')
    m = types.ModuleType("netutil_probe")
    exec(compile(src, "netutil_probe", "exec"), m.__dict__)
    return m


HERE = os.path.dirname(os.path.abspath(__file__))
NEW = open(os.path.join(HERE, "netutil.py")).read()

MISSING = "/nonexistent/frognet-no-fallback-oracle"
GOODMAP = "/tmp/fn_oracle_mapInterfaces"
GOODHOSTS = "/tmp/fn_oracle_hosts"
EMPTYHOSTS = "/tmp/fn_oracle_hosts_empty"

open(GOODMAP, "w").write('export eth0Name="enx0050b6246629"\n'
                         'export wlan0Name="wlx90de80b193db"\n'
                         'export wlan1Name="wlan1"\n')
open(GOODHOSTS, "w").write("10.250.250.1 FrogNetHost.Seattle5 databasehost.frognet\n")
open(EMPTYHOSTS, "w").write("# comments only\n")


# -- interface roles ------------------------------------------------

def t_roles_missing_map_raises():
    """An unreadable mapInterfaces must stop the proxy, not seed eth0/wlan0."""
    try:
        _load_netutil(NEW, GOODHOSTS, MISSING)
    except Exception as e:
        assert type(e).__name__ == "InterfaceRolesUnavailable", f"got {e!r}"
        return
    raise AssertionError("unreadable mapInterfaces did not raise")


def t_roles_incomplete_map_raises():
    """A map missing one key is incomplete, not two-thirds correct."""
    partial = "/tmp/fn_oracle_map_partial"
    open(partial, "w").write('export eth0Name="eth0"\n')
    try:
        _load_netutil(NEW, GOODHOSTS, partial)
    except Exception as e:
        assert type(e).__name__ == "InterfaceRolesUnavailable", f"got {e!r}"
        return
    raise AssertionError("incomplete mapInterfaces did not raise")


def t_roles_real_device_names_survive():
    """Systemd predictable names must come through verbatim."""
    m = _load_netutil(NEW, GOODHOSTS, GOODMAP)
    assert m.ETH0_NAME == "enx0050b6246629", m.ETH0_NAME
    assert m.WLAN0_NAME == "wlx90de80b193db", m.WLAN0_NAME
    assert m.DEFAULT_UPSTREAM_IFACES == {"wlx90de80b193db", "wlan1"}, \
        m.DEFAULT_UPSTREAM_IFACES


def t_old_roles_missing_map_guesses():
    """Recorded before-state: the shipped code invented 'eth0'."""
    if not OLD_TREE:
        return
    old = open(os.path.join(OLD_TREE, "proxy", "netutil.py")).read()
    m = _load_netutil(old, GOODHOSTS, MISSING)
    assert m.ETH0_NAME == "eth0", "old code was expected to guess"


# -- hosts map ------------------------------------------------------

def t_hosts_unreadable_raises():
    """Read failure must not present as 'name not found'."""
    m = _load_netutil(NEW, MISSING, GOODMAP)
    try:
        m.resolve_frognet_host("databasehost.frognet")
    except Exception as e:
        assert type(e).__name__ == "HostsMapUnreadable", f"got {e!r}"
        return
    raise AssertionError("unreadable /etc/hosts did not raise")


def t_hosts_empty_raises():
    """A file with no entries resolves nothing and must say so once."""
    m = _load_netutil(NEW, EMPTYHOSTS, GOODMAP)
    try:
        m.resolve_frognet_host("databasehost.frognet")
    except Exception as e:
        assert type(e).__name__ == "HostsMapUnreadable", f"got {e!r}"
        return
    raise AssertionError("empty hosts map did not raise")


def t_hosts_absent_name_is_none():
    """A genuinely absent name is still None - that fact did not change."""
    m = _load_netutil(NEW, GOODHOSTS, GOODMAP)
    assert m.resolve_frognet_host("nosuch.frognet") is None


def t_hosts_case_insensitive_both_sides():
    """RFC 4343: lowered on write and on lookup."""
    m = _load_netutil(NEW, GOODHOSTS, GOODMAP)
    assert m.resolve_frognet_host("frognethost.seattle5") == "10.250.250.1"
    assert m.resolve_frognet_host("FrogNetHost.Seattle5") == "10.250.250.1"


def t_old_hosts_unreadable_returns_none():
    """Recorded before-state: read failure was indistinguishable from a miss."""
    if not OLD_TREE:
        return
    old = open(os.path.join(OLD_TREE, "proxy", "netutil.py")).read()
    m = _load_netutil(old, MISSING, GOODMAP)
    assert m.resolve_frognet_host("databasehost.frognet") is None


# -- no silent swallows left in the changed files --------------------

def t_no_silent_swallows_reintroduced():
    """Structural gate: none of the Tier 1 files may carry a bare `except:`,
    and no handler may swallow into pass/return-constant without a narrowed
    exception type. Catches a regression reintroducing the class."""
    import ast
    root = os.path.dirname(HERE)
    files = [
        "proxy/netutil.py", "proxy/policy.py", "proxy/templates.py",
        "proxy/channel_sets.py", "daemon/util/hosts.py",
        "daemon/upstream/client.py",
    ]
    broad = {"Exception", "BaseException"}
    bad = []
    for rel in files:
        p = os.path.join(root, rel)
        if not os.path.exists(p):
            continue
        for n in ast.walk(ast.parse(open(p).read())):
            if not isinstance(n, ast.ExceptHandler):
                continue
            if n.type is None:
                bad.append(f"{rel}:{n.lineno} bare except")
                continue
            ty = ast.unparse(n.type)
            if ty in broad and len(n.body) == 1 and isinstance(
                    n.body[0], (ast.Pass, ast.Return)):
                bad.append(f"{rel}:{n.lineno} except {ty} -> silent")
    assert not bad, "; ".join(bad)


for _n, _f in sorted(globals().items()):
    if _n.startswith("t_"):
        check(_n[2:], _f)

_w = max(len(r[1]) for r in _results)
for _st, _n, _m in _results:
    print(f"{_st:6} {_n:<{_w}}  {_m}")
_bad = sum(1 for r in _results if r[0] != "PASS")
print(f"\n{len(_results) - _bad}/{len(_results)} passed")
sys.exit(1 if _bad else 0)
