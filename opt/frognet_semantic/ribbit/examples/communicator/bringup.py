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
bringup.py - the Communicator's first job: stand the node up.

Installing the Communicator IS node bring-up. This models that as an idempotent state
machine driven through injected collaborators. Each collaborator is an INTERFACE with a
Fake stand-in here; on the box, the real backend drops in WITHOUT changing the state
machine.

Surface status (after reading comm_ref):
  - The setup-admin action API and the pond_admin admin API are READ; the collaborators
    below are bound to their real shapes.
  - The *behavior* behind them is still in source I have NOT read, marked [BEHAVIOR UNREAD]:
    setup_helper.bash (what auto_ip computes, what apply_identity writes) and
    broker_admin_v4.php (the node self-join / 10/8 allocate path). Those are not inferred.

State order:
  NEW -> PROVISIONED -> ADDRESSED -> IDENTIFIED -> REGISTERED -> LILLYPAD -> READY

  PROVISIONED   BLE provisioning handed the node its join creds + display name
  ADDRESSED     setup_helper.auto_ip gave the node its address        [API READ; behavior unread]
  IDENTIFIED    keypair ensured (idempotent) + setup_helper.apply_identity(name, ip)  [API READ]
  REGISTERED    broker join allocated a 10/8 subnet (node_base+node_increment) + pond  [join path unread]
  LILLYPAD      setup_lillypad brought up local services (disco on .2, db election, presence)
  READY         the node is a complete standalone member of its 10/8 plane

Idempotent: bring_up() can run from any state and converges to READY; re-running a
completed step is a no-op (keypair returns the existing pair; join returns the held lease).
"""
from __future__ import annotations

import ipaddress
from enum import IntEnum
from typing import Any, Callable, Dict, List, Optional


class State(IntEnum):
    NEW = 0
    PROVISIONED = 1
    ADDRESSED = 2
    IDENTIFIED = 3
    REGISTERED = 4
    LILLYPAD = 5
    READY = 6


# -- collaborator interfaces (real backends drop in on the box) --------------
class BleProvisioner:
    def provision(self) -> Dict[str, Any]: raise NotImplementedError

class SetupHelper:        # contract READ (frognet_setup_admin_api -> setup_helper.bash); behavior UNREAD
    # Real action surface the api.php sudo pass-through forwards to setup_helper.bash:
    #   state | wifi_devices | scan_wifi <iface> | other_ifaces | auto_ip
    #   connect_wifi <iface> <ssid> [pass] | disconnect <iface> | apply_identity <name> <ip>
    # Bring-up uses auto_ip and apply_identity(name, ip); the rest are first-run Wi-Fi setup.
    def auto_ip(self) -> str: raise NotImplementedError
    def apply_identity(self, name: str, ip: str) -> None: raise NotImplementedError

class Identity:
    def ensure_keypair(self) -> Dict[str, str]: raise NotImplementedError   # idempotent

class PondAdmin:          # admin API READ; node self-join/allocate path in broker_admin_v4.php UNREAD
    def join(self, pubkey: str, name: str) -> Dict[str, Any]: raise NotImplementedError

class Lillypad:                                      # setup_lillypad: local services
    def setup(self, addr: str) -> Dict[str, bool]: raise NotImplementedError


# -- Fake stand-ins (sim rung) -----------------------------------------------
class FakeBle(BleProvisioner):
    def __init__(self, name="Frog", join_token="pond:ratpond"):
        self.name, self.join_token = name, join_token
    def provision(self):
        return {"name": self.name, "join_token": self.join_token}

class FakeSetupHelper(SetupHelper):
    def __init__(self): self.identity_applied = None
    def auto_ip(self): return "10.179.179.1"          # node's own .1 (FrogNetHost)
    def apply_identity(self, name, ip): self.identity_applied = (name, ip)   # real action: <name> <ip>

class FakeIdentity(Identity):
    def __init__(self): self._kp: Optional[Dict[str, str]] = None
    def ensure_keypair(self):
        if self._kp is None:                          # generate once; idempotent thereafter
            self._kp = {"pub": "PUBKEY-DEMO", "priv": "PRIVKEY-DEMO"}
        return dict(self._kp)

class FakePondAdmin(PondAdmin):
    # The pond_admin admin API is READ: create-pond carries node_base + node_increment (the
    # allocation scheme), and the `nodes` action returns records shaped
    #   {pond, name, label, subnet, pubkey, active, blocked}.
    # The node self-join/allocate path itself lives in broker_admin_v4.php and is UNREAD, so this
    # fake reflects only the *grounded scheme and record shape*, not invented join/allocate logic.
    def __init__(self, pond="ratpond", node_base="10.179.179.0", node_increment=256):
        self.pond, self._base, self._incr = pond, node_base, node_increment
        self._leases: Dict[str, Dict[str, Any]] = {}
    def _subnet_for(self, k: int) -> str:
        base = int(ipaddress.IPv4Address(self._base))
        return str(ipaddress.IPv4Address(base + self._incr * k))      # node_base + k*increment
    def join(self, pubkey, name):
        if pubkey not in self._leases:                # allocate once; same lease on rejoin
            k = len(self._leases)
            net = ipaddress.ip_network(f"{self._subnet_for(k)}/24", strict=False)
            self._leases[pubkey] = {
                "pond": self.pond, "name": name, "label": name,
                "subnet": str(net), "addr": str(net.network_address + 1),  # .1 = FrogNetHost
                "pubkey": pubkey, "active": True, "blocked": False}
        return dict(self._leases[pubkey])

class FakeLillypad(Lillypad):
    def setup(self, addr):
        return {"discovery": True, "db_election": True, "presence": True}


# -- the state machine -------------------------------------------------------
class BringUp:
    def __init__(self, ble: BleProvisioner, helper: SetupHelper,
                 identity: Identity, pond: PondAdmin, lillypad: Lillypad):
        self.ble, self.helper = ble, helper
        self.identity, self.pond, self.lillypad = identity, pond, lillypad
        self.state = State.NEW
        self.ctx: Dict[str, Any] = {}
        self.trace: List[str] = []

    def _step(self, target: State, label: str, fn: Callable[[], None]):
        if self.state >= target:
            return
        fn()
        self.state = target
        self.trace.append(label)

    def bring_up(self) -> State:
        self._step(State.PROVISIONED, "ble:provision",
                   lambda: self.ctx.update(self.ble.provision()))
        self._step(State.ADDRESSED, "helper:auto_ip",
                   lambda: self.ctx.update(addr=self.helper.auto_ip()))
        self._step(State.IDENTIFIED, "identity:keypair+apply", self._identify)
        self._step(State.REGISTERED, "pond:join", self._register)
        self._step(State.LILLYPAD, "lillypad:setup",
                   lambda: self.ctx.update(services=self.lillypad.setup(self.ctx["addr"])))
        self._step(State.READY, "ready", lambda: None)
        return self.state

    def _identify(self):
        kp = self.identity.ensure_keypair()           # idempotent
        self.ctx["pubkey"] = kp["pub"]                # pubkey rides the broker join (registration)
        # apply_identity binds name + ip per the real action surface (<name> <ip>). Which ip the
        # helper writes (the auto_ip address vs the later pond subnet) is in setup_helper.bash (unread).
        self.helper.apply_identity(self.ctx.get("name", "Frog"), self.ctx["addr"])

    def _register(self):
        lease = self.pond.join(self.ctx["pubkey"], self.ctx.get("name", "Frog"))
        self.ctx.update(addr=lease["addr"], subnet=lease.get("subnet"), pond=lease["pond"])

    def is_ready(self) -> bool:
        return self.state == State.READY and all(self.ctx.get("services", {}).values())


def default_bringup(name: str = "Frog") -> BringUp:
    """A fully-faked bring-up for the sim rung."""
    return BringUp(FakeBle(name=name), FakeSetupHelper(), FakeIdentity(),
                   FakePondAdmin(), FakeLillypad())
