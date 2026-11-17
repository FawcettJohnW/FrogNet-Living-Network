#!/usr/bin/env python3
################################################################
#  Copyright (C) 2016-2026 Fawcett Innovations LLC             #
#  SPDX-License-Identifier: GPL-2.0-only                       #
#  See COPYRIGHT and LICENSE at the root of this tree.         #
################################################################
"""
core/homa_liveness.py -- [LIVENESS_IS_A_HELD_READ_V1] whether a peer is there, for the Homa driver.

John, 2026-11-16: "Liveness is a function of a held read on the capability tuple." Homa has no connection, so it
watched every RPC for silence and called a quiet one a dead peer (its per-RPC timeout). That is not the transport's
question. A machine going down is a NETWORK EVENT: the merge runs, /etc/hosts is rewritten, the roles are selected
again, and every owner writes its own truth again at the RAM now named. A machine that is gone writes nothing.

Each end of the Homa driver does two things, both in the memory and neither to the other end:

  writes its own   (homa, can, <its .1>:<proxy|daemon>) = {ip, role, port, inst, build} in the ControlRam: "this end of
                   the semantic wire is on Homa here". One tuple per END, because the two ends are two processes: a
                   proxy that stops has not taken its node's daemon with it. `inst` is new every time the process
                   starts. Written at start; written again whenever a read shows it is not there (the RAM was rebuilt
                   at a network event: reassert your own truth); written once more, marked stopped, when the process
                   stops (a write fires the holds on it; api.php's delete does not), and then removed.
  holds a read     on the (homa, can) family, for every peer. Each time the hold returns, this end reads which
                   ends have their tuple in the memory, and each one's inst.
  watches a file   [HOSTS_IS_WATCHED_V1] /etc/hosts, with a file watcher (inotify). John, 2026-11-16: "You don't need
                   to read hosts; you put a file watcher on it." The merge rewriting it IS the network event. Nothing
                   reads the file to find out whether something happened; when the watcher fires, the machines that
                   were in the block and are not in it now have left ([POOL_IS_THE_LIVE_BLOCK_V1]: "the block is the
                   membership"; the role selection's own rule, _alive_ones).

A peer end that WAS there is GONE, once, on:
     the watcher: its machine went out of the block    the machine left the network (the merge said so)
     the hold: its tuple is not in the memory, or stopped   that end stopped
     the hold: its tuple's inst is not the one seen    that end stopped and another started: the old one is gone
on_gone(ip, why, machine_left) is called and the driver ends everything outstanding with it, at once (the bus contract's "broken is
broken"). Nothing is asked of the peer and nothing is timed.

Two events say nothing, and nobody is called gone on them:
     the hold returns and this end's OWN tuple is not in the memory (or is one an earlier process left in a RAM
         host's database: not this process's inst). The RAM was rebuilt at a network event. This end writes its own
         again and tells the driver there was a network event (once); the living peers are doing the same.
     the watcher fires and this end's OWN machine is not in the block: this node's view is not the converged one yet.

THE DRIVER REMOVES THE CLOSED CONNECTION (John, 2026-11-16: "The driver is the thing that must remove the closed
connection"). On TCP the daemon's driver learns a machine left when its socket closes, and tells api.php
remove_writer ([WRITER_GONE_V1]): that is what takes a dead machine's tuples out of the memory. Homa has no close.
The watcher is the Homa driver's close: told here that a machine left, the daemon's driver ends its sessions and
makes the same remove_writer call. Nothing else removes them, and a hold on a dead machine's tuple alone never fires.

A machine that was never in the block as this end saw it is never called gone by the watcher, only by its tuple.

NOT COVERED, said plainly: a peer process that dies without stopping (SIGKILL) on a machine that stays in the
network, and is not started again, leaves its tuple and its machine both there. Nothing here calls it gone until it
starts again (its inst changes). On TCP the kernel closes the dead process's socket.
"""
from __future__ import annotations

import atexit
import os
import sys
import threading
from typing import Callable, Dict, Optional, Set, Tuple

SERVICE, VAR = "homa", "can"
ROLES = ("proxy", "daemon")
HOLD_S = 300.0                    # the caller's bound on one hold; at the bound it simply holds again
BUILD = "homa-liveness-v5-20261116k"
# [CAPABILITY_IN_THE_CONTROL_RAM_V1] John, 2026-11-16: "Highest static ip is control ram, where the capability tuples
# live. Reach it over whatever transport is loaded." The ControlRam's address is arithmetic (the highest .1), so it is
# known before anything is selected, and it is where the role selection already reads capability
# (frognet_tuples.DEFAULT_DBHOST, frognet_service_hosts.service_host_lines). A carrier's capability is needed to reach
# everything else, the ControlRam included, so it cannot live in a RAM that has to be reached first.
CONTROL_RAM = "databasehost_control.frognet"


def node_of(ip: str) -> str:
    """The FrogNet node an address belongs to: its /24's .1 (the rule [WRITER_GONE_V1] removes by)."""
    p = ip.split(".")
    return ".".join(p[:3]) + ".1" if len(p) == 4 and all(x.isdigit() for x in p) else ip


class _Ram:
    """The ControlRam and the hosts block, through the tree's own calls. A test replaces _ram() with a stand-in of
    the same calls."""

    def __init__(self, dbhost: str):
        from core import frognet_tuples
        # [CORE_ON_THE_PATH_V1] frognet_held imports frognet_tuples by bare name, as the node's daemons import it
        # with core/ on sys.path; this process imports core as a package, so core/ goes on the path here, once.
        d = os.path.dirname(os.path.abspath(frognet_tuples.__file__))
        if d not in sys.path:
            sys.path.append(d)
        import frognet_held
        self._t, self._h, self.dbhost = frognet_tuples, frognet_held, dbhost
        self._hosts_event = None

    def my_ip(self) -> str:
        return self._t.my_ip()

    def put(self, scope: str, value: dict) -> bool:
        return bool(self._t.put(SERVICE, VAR, scope, value, dbhost=self.dbhost, own=True))

    def hold(self, seen: Optional[str]):
        # The stored name is SD:<var>.<scope> (frognet_tuples.var_name) and api.php matches SensorName__like against
        # the stored name as it is: the pattern carries the prefix. "can.%" matches nothing (confirmed on api.php).
        rows = self._h.wait_change(SERVICE, self.dbhost, seen, name_like=self._t.var_name(VAR, "%"), wait_s=HOLD_S)
        return rows, self._h.signature(rows)

    def ends(self, rows) -> Dict[Tuple[str, str], str]:
        """(ip, role) -> inst, for every end whose tuple is in the memory and not marked stopped."""
        out: Dict[Tuple[str, str], str] = {}
        self._carriers: Dict[Tuple[str, str], Tuple[str, ...]] = {}
        prefix = self._t.var_name(VAR, "")
        for r in rows:
            name = str(r.get("SensorName") or "")
            if not name.startswith(prefix):
                continue
            ip, _, role = name[len(prefix):].rpartition(":")
            v = r.get("data")
            if role not in ROLES or not ip or not isinstance(v, dict) or v.get("stopped"):
                continue
            out[(ip, role)] = str(v.get("inst") or "")
            c = v.get("carriers")                  # an end that does not say is one told Homa only
            self._carriers[(ip, role)] = tuple(str(x) for x in c) if isinstance(c, list) and c else ("homa",)
        return out

    def live_block(self) -> Set[str]:
        """The .1 of every machine in the network: the hosts block, by the role selection's own rule."""
        from core.frognet_service_hosts import _alive_ones
        with open(self._h.HOSTS, "r") as f:
            return set(_alive_ones(f.read().splitlines()))

    def wait_network_event(self) -> None:
        """[HOSTS_IS_WATCHED_V1] Return when /etc/hosts has been rewritten (the merge). The file watcher: it blocks in
        the kernel until the file changes. Only the watcher thread calls it."""
        if self._hosts_event is None:
            self._hosts_event = self._h._Inotify(os.path.dirname(self._h.HOSTS) or "/")
        name = os.path.basename(self._h.HOSTS)
        while name not in self._hosts_event.names():
            pass                                           # names() blocks in read(); another file in /etc changed


def _ram(dbhost: str = CONTROL_RAM):
    return _Ram(dbhost)


class PeerLiveness:
    """One per process. role: which end this process is ("proxy" or "daemon"); it watches the other end of each peer.
    on_gone(ip, why, machine_left) is called once per departure, on the liveness thread; machine_left is True when
    the peer's machine left the network (the hosts block), False when only that end of it stopped."""

    def __init__(self, role: str, port: int, on_gone: Callable[[str, str, bool], None], who: str,
                 on_network_event: Optional[Callable[[], None]] = None, dbhost: str = CONTROL_RAM,
                 carriers: Tuple[str, ...] = ("homa",)):
        if role not in ROLES:
            raise ValueError("homa liveness: role is one of %r, not %r" % (ROLES, role))
        self.ram = _ram(dbhost)
        self.role, self.other = role, ROLES[1 - ROLES.index(role)]
        self.me, self.port, self.on_gone, self.who = self.ram.my_ip(), int(port), on_gone, who
        self.inst = os.urandom(6).hex()
        self.carriers = tuple(carriers)           # what this end carries the wire on ([CARRIER_PER_HOP_V1])
        self.peer_carriers: Dict[str, Tuple[str, ...]] = {}   # peer ip -> what its other end says it carries it on
        self.on_network_event = on_network_event
        self._lock = threading.Lock()
        self.seen: Dict[str, str] = {}            # peer ip -> the inst of its other-end tuple, as last read
        self.block: Optional[Set[str]] = None     # the hosts block as of the watcher's last event (and the start)
        self.left: Set[str] = set()               # peers whose machine left the hosts block and is not back in it
        self._watched: Set[str] = set()           # peers the driver has a session with
        self._displaced: Optional[str] = None     # an inst found under this end's name that was not this process's
        self.n_events = self.n_gone = self.n_reasserted = 0
        self._stop = threading.Event()
        self._at_exit = False
        # The first write is made on the liveness thread, not here: on a node the way to the ControlRam can be the
        # semantic wire itself, and the driver that is constructing this must not wait on its own wire.
        self._net = threading.Event()             # set by the watcher at each network event
        threading.Thread(target=self._run, daemon=True, name="homa-liveness").start()
        threading.Thread(target=self._watch, daemon=True, name="homa-liveness-hosts").start()

    # -- this end's own tuple
    def _scope(self) -> str:
        return "%s:%s" % (self.me, self.role)

    def _announce(self, why: str, stopped: bool = False) -> None:
        v = {"ip": self.me, "role": self.role, "port": self.port, "inst": self.inst, "build": BUILD, "who": self.who,
             "carriers": list(self.carriers)}
        if stopped:
            v["stopped"] = True
        kept = self.ram.put(self._scope(), v)
        if kept and not self._at_exit:
            self._at_exit = True
            atexit.register(self.withdraw)        # registered after the first put that was kept, so it runs BEFORE
                                                  # frognet_tuples' own atexit delete of this tuple (atexit is LIFO)
        print("[HOMA-LIVENESS] %s %s (%s, %s, %s) inst=%s in the ControlRam: %s"
              % (self.who, "wrote its own" if kept else "COULD NOT WRITE its own", SERVICE, VAR, self._scope(),
                 self.inst, why), flush=True)

    def withdraw(self) -> None:
        """This end is stopping: say so in the memory (the write fires every hold on it), then hold no more."""
        if self._stop.is_set():
            return
        self._stop.set()
        try:
            self._announce("stopping", stopped=True)
        except Exception as e:
            print("[HOMA-LIVENESS] %s: could not mark its tuple stopped: %r" % (self.who, e), flush=True)

    stop = withdraw

    # -- the driver's one call
    def watch(self, ip: str) -> None:
        """The driver has a session with this peer: when the watcher says its machine left, it is gone, even if its
        tuple was never read here."""
        with self._lock:
            self._watched.add(node_of(ip))

    def carriers_of(self, ip: str) -> Optional[Tuple[str, ...]]:
        """What the other end at `ip` says it carries the wire on, or None if its tuple has not been read."""
        with self._lock:
            return self.peer_carriers.get(node_of(ip))

    # -- the one thread
    def _gone(self, ip: str, why: str, machine_left: bool) -> None:
        self.n_gone += 1
        print("[HOMA-LIVENESS] %s: %s's %s end is GONE: %s" % (self.who, ip, self.other, why), flush=True)
        try:
            self.on_gone(ip, why, machine_left)
        except Exception as e:
            print("[HOMA-LIVENESS] %s: ending what was outstanding with %s raised %r" % (self.who, ip, e), flush=True)

    def _read(self, rows) -> None:
        """One return of the hold: which ends have their tuple in the memory. See the module docstring for the rule."""
        self.n_events += 1
        ends = self.ram.ends(rows)
        peers = {ip: inst for (ip, role), inst in ends.items() if role == self.other and ip != self.me}
        car = getattr(self.ram, "_carriers", {})
        gone = []
        with self._lock:
            self.peer_carriers = {ip: car.get((ip, self.other), ("homa",)) for ip in peers if ip not in self.left}
            # A machine that left the network is not there, whatever the memory still holds under its name (the RAM
            # host's daemon removes it; until that has happened, or if it could not, it is still stored).
            peers = {ip: inst for ip, inst in peers.items() if ip not in self.left}
            mine = ends.get((self.me, self.role))
            rebuilt = mine != self.inst            # not there, or a tuple a process before this one left in this RAM
            if mine is not None and rebuilt:
                if mine == self._displaced:
                    # It was written again after this process wrote over it: a second live process of this role on
                    # this node. Writing over it again would have the two fire each other without end.
                    print("[HOMA-LIVENESS] %s: (%s, %s, %s) is being written by ANOTHER running %s on this node "
                          "(inst %s; this one is %s). This process does not write over it again."
                          % (self.who, SERVICE, VAR, self._scope(), self.role, mine, self.inst), flush=True)
                    rebuilt = False
                self._displaced = mine
            if not rebuilt:
                for ip, inst in list(self.seen.items()):
                    if ip not in peers:
                        gone.append((ip, "its capability tuple is no longer in the memory", False)); self.seen.pop(ip)
                    elif peers[ip] != inst:
                        gone.append((ip, "it stopped and another started (inst %s, was %s)" % (peers[ip], inst), False))
                self.seen = dict(peers)
            else:
                self.seen = {}                     # nobody's tuple is judged on this read, nor against an old read
        if rebuilt:
            self.n_reasserted += 1
            self._announce("it was not in the memory (the RAM was rebuilt at a network event): reasserted")
            if self.on_network_event is not None:
                try:
                    self.on_network_event()
                except Exception as e:
                    print("[HOMA-LIVENESS] %s: the network event handler raised %r" % (self.who, e), flush=True)
        for ip, why, machine_left in gone:
            self._gone(ip, why, machine_left)

    def _block_now(self) -> Optional[Set[str]]:
        try:
            block = self.ram.live_block()
        except Exception as e:                     # a file that could not be read is not an empty block
            print("[HOMA-LIVENESS] %s: the hosts block could not be read (%r): no machine is judged on this event"
                  % (self.who, e), flush=True)
            return None
        return block if self.me in block else None  # without this node in it, it is not the converged block

    def _watch(self) -> None:
        """[HOSTS_IS_WATCHED_V1] The file watcher on /etc/hosts. Each time the merge rewrites it: the machines that
        were in the block and are not now have left."""
        with self._lock:
            self.block = self._block_now()
        while not self._stop.is_set():
            try:
                self.ram.wait_network_event()
            except Exception as e:
                print("[HOMA-LIVENESS] %s: the file watcher on the hosts file FAILED (%r): a machine leaving the "
                      "network is NOT learned in this process from here on" % (self.who, e), flush=True)
                return
            if self._stop.is_set():
                return
            self.n_events += 1
            now = self._block_now()
            gone = []
            with self._lock:
                if now is not None:
                    if self.block is not None:
                        for ip in sorted(self.block - now):
                            self.left.add(ip)
                            if ip in self.seen or ip in self._watched:
                                gone.append(ip)
                            self.seen.pop(ip, None); self._watched.discard(ip)
                    self.left -= now               # a machine that left and is in the block again is back
                    self.block = now
            self._net.set()
            for ip in gone:
                self._gone(ip, "its machine is no longer in the hosts block (it left the network)", True)

    def _run(self) -> None:
        seen = None
        self._announce("start")
        while not self._stop.is_set():
            try:
                rows, seen = self.ram.hold(seen)
            except Exception as e:
                # The RAM did not take this hold. It is not chased: the next network event is when it is held again.
                print("[HOMA-LIVENESS] %s: the hold on (%s, %s) was not taken: %r; holding again at the next network "
                      "event" % (self.who, SERVICE, VAR, e), flush=True)
                self._net.clear()
                self._net.wait()
                continue
            if self._stop.is_set():
                return
            self._read(rows)

    def stats(self) -> dict:
        with self._lock:
            return {"role": self.role, "me": self.me, "inst": self.inst, "there": sorted(self.seen),
                    "block": sorted(self.block or ()), "left": sorted(self.left), "events": self.n_events, "gone": self.n_gone,
                    "reasserted": self.n_reasserted}
