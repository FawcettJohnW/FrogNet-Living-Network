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
"""Stub for broker_namespace_v4 — no-op namespace ops so _ensure_tunnel's DB
logic runs in the sandbox without touching WireGuard or netns. Injected into
sys.modules as 'broker_namespace_v4' BEFORE importing the broker module."""
import itertools

_kc = itertools.count(1)


def _wg_genkey():
    n = next(_kc)
    return (f"PRIV{n:040d}", f"PUB{n:040d}")


def create_wg_interface(*a, **k):
    return True


def add_wg_peer(*a, **k):
    return True


def add_route(*a, **k):
    return True


def setup_port_forward(*a, **k):
    return True


def teardown_port_forward(*a, **k):
    return True


def delete_wg_interface(*a, **k):
    return True


def ensure_namespace(*a, **k):
    return True


def get_public_ip():
    return "203.0.113.7"


def _veth_ips(group_index):
    return ("10.253.254.1", "10.253.254.2", 30)


class _R:
    returncode = 0
    stdout = ""
    stderr = ""


def _run(cmd, check=True, capture=True, netns=None):
    return _R()
