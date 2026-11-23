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
#  See COPYRIGHT and LICENSE at the root of this tree.         #
################################################################
"""
channel_setting.py -- [CHANNEL_SETTING_IS_A_TUPLE_V1] a channel's setting, held in a capability tuple.

John, 2026-11-19: "It does not have to introduce anything. It is a tuple. A control tuple and part of the system
capability tuple. You do realize we have full control over the contents of the json that tuple holds, right? It does
not need to be standard in any way." And: "You can also introduce other capability tuples. There is not a fixed form,
and the consumer is the thing that wrote it."

The design is "CroakOS Protocol Layer Demonstration: Walkthrough and Detailed Design", version 0.4, section 10.1.

    (channel, setting, <Role>)     how that Role's traffic is carried, on whatever connection carries it. Its owner
                                   is the Role; the Role's admin tool is its only writer (write()).
    (channel, setting, semantic)   the setting of traffic that belongs to no Role (the SystemRam, the ControlRam).
    (channel, at, <ip>:<proc>.<pid>)  written by each end: what it holds and what it is sending under, for each Role.

They are in the ControlRam, where the capability tuples live. A Role's setting holds:

    driver      the protocol: a word a driver registered (tcp, chorus, openqasm). Absent: the carrier's own.
    number      a random number the Admin sets ([THE_KEY_IS_COMPUTED_V1], core/channel_frame.py). Each sending node
                computes its key for the Role from the Role's secret on the machine, its own FNID, the Role's name
                and this number. A switch of key is this number changing: not "one more", only "did it change".
                0 or absent: the channel is not sealed. The number is not a key and not a secret.
    protection  the algorithm frames are sealed with (core/channel_protection.py). Absent: aes-256-gcm.
    check       the setting's proof ([A_SETTING_PROVES_ITSELF_V1], below).

This module holds the read on those tuples (frognet_held.wait_change), says which Role a memory's name is
(role_of), and writes this end's report. It does not seal, open or switch anything: core/channel_frame.py does, on
each connection, told by on_change() that the setting moved. Nothing polls and nothing runs on a timer. When the hold
is not taken, it is held again at the next network event (the hosts file is rewritten). A process with no such tuple
in its ControlRam behaves exactly as it did before this file existed.
"""
import json
import os
import queue
import struct
import sys
import threading
import time
from typing import Callable, Dict, List, Optional, Tuple

BUILD = "channel-setting-is-a-tuple-v10-20261123d"
SERVICE, VAR, SCOPE = "channel", "setting", "semantic"
CONTROL_RAM = "databasehost_control.frognet"
HOLD_S = 300.0
SECRETS = os.environ.get("FROGNET_CHANNEL_SECRETS", "/etc/frognet/channel_secrets")

_lock = threading.Lock()
_cur: Optional[dict] = None             # the tuple's JSON as last read (without its ts), None when there is none
_started = False
_listeners: List[Callable[[Optional[dict]], None]] = []
_kick = threading.Event()
_woke: "queue.Queue" = queue.Queue()     # what each wake of the held read brought, for the one thread that takes it up
_parked = False
_rams: dict = {}                         # [A_SETTING_PER_RAM_CONNECTION_V1] a RAM's role name -> its tuple's JSON
_at: dict = {}                           # a RAM's role name -> the one address this node's hosts file puts it at now
_me: Optional[str] = None
_reports: "queue.SimpleQueue" = queue.SimpleQueue()
_ignored: dict = {}                      # a Role -> the last setting for it this end ignored (said once)


def _tuples():
    from core import frognet_tuples
    d = os.path.dirname(os.path.abspath(frognet_tuples.__file__))       # [CORE_ON_THE_PATH_V1] as homa_liveness does
    if d not in sys.path:
        sys.path.append(d)
    import frognet_held
    return frognet_tuples, frognet_held


# [A_SETTING_PROVES_ITSELF_V1] John, 2026-11-21 13:05. Nothing stops any member of the network writing any tuple, and
# that is not to be prevented. One forged write matters: a setting with no number turns a Role's encryption off.
# So the setting proves itself, to the ends that have something to protect:
#   1. A setting carries "check": HMAC-SHA384 over the Role's name and the setting's other fields, keyed with the
#      Role's secret on the machine (96 hex characters; [KEY_AND_CHECK_USE_SHA_384_V1], 22 Nov 2026). It
#      works for a setting with no number. Only a machine that holds the Role's secret can make it.
#   2. An end that holds the secret for the Role ignores a setting whose check does not verify: it says why and keeps
#      running the setting it has. An end with no secret for that Role has nothing to verify with and nothing to protect.
#   (There was a rule 3, 13:05 to 17:00 on 21 Nov: ignore a setting whose count of switches is lower than the count
#   running. The count is now a random number the Admin sets, and there is no lower. It is gone.)
# No identity, no signature, no access list. What is checked is the text below, which any language can make the same:
# the Role's name, then each other field as "name=value", sorted by name, one to a line.
def check_text(role: str, value: dict) -> bytes:
    lines = [role.lower()]
    for k in sorted(value):
        if k in ("check", "ts"):
            continue
        v = value[k]
        lines.append("%s=%s" % (k, ("true" if v else "false") if isinstance(v, bool) else v))
    return "\n".join(lines).encode("utf-8")


def check_of(role: str, value: dict) -> Optional[str]:
    """The check a setting for this Role must carry, or None when this machine holds no secret for the Role."""
    import hashlib
    import hmac
    from core import channel_frame
    try:
        # [A_ROLES_OWN_SECRET_MOVES_FORWARD_V1] the check is keyed with the secret AT THE SETTING'S STEP, worked out in
        # memory from the file. (A step the file has passed cannot be made: SecretStepBehind, a DoesNotOpen.)
        secret = channel_frame.secret_at(role.lower(), int(value.get("step") or 0), SECRETS)[0]
    except channel_frame.DoesNotOpen:
        return None
    # [KEY_AND_CHECK_USE_SHA_384_V1] John, 22 Nov 2026 13:58: HMAC-SHA384 over the Role's name and the setting's other
    # fields (check_text), keyed with the Role's secret: 96 hex characters. Until then it was HMAC-SHA256 under a key
    # made from the secret and the Role's name; the Role's name is in the text that is checked.
    return hmac.new(secret, check_text(role, value), hashlib.sha384).hexdigest()


def write(value: dict, dbhost: str = CONTROL_RAM, ram: Optional[str] = None) -> bool:
    """The owner's act: set a Role's channel (`ram`: the Role), or with none the setting of traffic that belongs to no
    Role. A Role's setting is written with its check, which only a machine holding that Role's secret can make: the
    owner's tool runs on one. own=False: the tuple outlives the process that wrote it."""
    T, _ = _tuples()
    value = {k: v for k, v in dict(value).items() if k != "check"}
    if ram:
        c = check_of(ram, value)
        if c is None:
            print("[CHANNEL-SETTING] this machine holds no secret for %r: a setting written from here carries no check, "
                  "and the ends that hold the secret will ignore it" % ram, flush=True)
        else:
            value["check"] = c
    scope = (ram or SCOPE).lower()
    ok = bool(T.put(SERVICE, VAR, scope, value, dbhost=dbhost, own=False))
    if ok:
        # [THE_NEWEST_CELL_IS_THE_SETTING_V1] the cells of this setting that other machines wrote before this one are
        # superseded: removed, so the setting is one cell again. Only cells the store puts BEFORE this machine's are
        # removed, so of two machines writing at once exactly one cell is left, the one every end takes.
        try:
            name = T.var_name(VAR, scope)
            cells = oldest_first([r for r in T._values_raw(SERVICE, dbhost, name_like=name) if str(r.get("SensorName") or "") == name])
            mine = max((i for i, r in enumerate(cells) if str(r.get("SensorAddress") or "") == T.my_ip()), default=None)
            for r in (cells[:mine] if mine is not None else []):
                if str(r.get("SensorAddress") or "") != T.my_ip() and T._delete_by_id(dbhost, r.get("SensorID")):
                    print("[CHANNEL-SETTING] %s [THE_NEWEST_CELL_IS_THE_SETTING_V1] the setting of %s written earlier from %s "
                          "is superseded by this write and removed" % (BUILD, scope, r.get("SensorAddress")), flush=True)
        except Exception as e:                       # not removed now: every end takes the newest cell all the same
            print("[CHANNEL-SETTING] %s an earlier writer's setting of %s was not removed (%s: %s); the ends take the "
                  "newest one, and the next write removes it" % (BUILD, scope, type(e).__name__, e), flush=True)
    return ok


# [A_ROLES_OWN_SECRET_MOVES_FORWARD_V1] role -> (step, the Role's own secret at that step): the secret that goes with the
# setting THIS PROCESS holds. Keys are computed from it (secret_in_use), not from whatever step the file has reached:
# another process of this machine can move the file a step before this one has taken that setting.
_secrets: Dict[str, tuple] = {}


def secret_in_use(role: str) -> bytes:
    """The secret this process computes a Role's keys from: the Role's own at the step of the setting it holds, or,
    for a Role on the network's secret or one this process holds no stepped setting for, the file as it is."""
    from core import channel_frame
    held = _secrets.get((role or "").lower())
    return held[1] if held else channel_frame.secret_of((role or "").lower(), SECRETS)


def step_in_use(role: str) -> Optional[int]:
    """The step of a Role's own secret that this process is on, or None (a Role on the network's secret has none)."""
    held = _secrets.get((role or "").lower())
    return held[0] if held else None


def _proved(role: str, new: dict, held: Optional[dict]) -> Optional[str]:
    """Why this end ignores a setting for a Role, or None when it takes it.

    [A_ROLES_OWN_SECRET_MOVES_FORWARD_V1] John, 22 Nov 2026 23:48. Only for a Role with its own secret file; the
    default secret never moves. The setting has "step", a count. On seeing step s this machine works out, in memory,
    secret(s) from the secret its file holds, one HMAC for each step it missed. Then, in this order:
      1. the setting's check is verified under secret(s). If it fails the setting is ignored, logged, and NOTHING is
         written;
      2. secret(s) is written with its step over the Role's file, whole, and secret(s-1) is erased;
      3. keys in use stay in memory until each direction passes its switch call, as at any switch (core/channel_frame).
    A setting whose step is lower than the file's is ignored and logged."""
    import hashlib
    import hmac
    from core import channel_frame
    role = role.lower()
    step = int(new.get("step") or 0)
    try:
        secret, at, own = channel_frame.secret_at(role, step, SECRETS)
    except channel_frame.SecretStepBehind as e:
        return "%s: a setting does not take a Role's secret back" % e
    except channel_frame.DoesNotOpen:
        return None                                  # no secret here: nothing to verify with, nothing to protect
    want = hmac.new(secret, check_text(role, new), hashlib.sha384).hexdigest()
    if not hmac.compare_digest(str(new.get("check") or ""), want):
        return ("its check does not verify against this machine's secret for %s%s; nothing is written"
                % (role, " at step %d" % step if own else ""))
    if own:
        if step > at:
            wrote = channel_frame.keep_secret(role, secret, step, SECRETS)
            print("[CHANNEL-SETTING] %s [A_ROLES_OWN_SECRET_MOVES_FORWARD_V1] %s: the setting is at step %d and this "
                  "machine's secret was at step %d: %s (hash %s)"
                  % (BUILD, role, step, at,
                     "moved forward %d step(s), written over the Role's file, and the earlier secret erased" % (step - at)
                     if wrote else "another process of this machine had already written it",
                     channel_frame.secret_fingerprint(secret)), flush=True)
        _secrets[role] = (step, secret)
    return None


def oldest_first(rows) -> list:
    """[THE_NEWEST_CELL_IS_THE_SETTING_V1] A tuple is one cell for each machine that wrote it. A Role's setting written
    from two nodes (an Admin at each, or John at two terminals, 22 Nov 2026) is therefore two cells with one name, and
    the store hands them back in no order it promises: every end took whichever came last, which was the cell made
    last, not the one written last. Found by the full test between machines, 23 Nov 2026. The setting is the cell the
    STORE stamped newest (its own clock, the same for every end; then the cell made later): put last here, so each
    reader below, which lets the last one stand, takes it. The writer removes the cells its write supersedes
    (write, below), so two cells are only ever there for a moment."""
    def when(r):
        try:
            return (int(r.get("UpdatedAtEpoch") or r.get("ts_env") or 0), int(r.get("SensorID") or 0))
        except (TypeError, ValueError):
            return (0, 0)
    return sorted(rows or [], key=when)                 # (a stable sort: rows it cannot tell apart keep their order)


def _value_of(rows) -> Optional[dict]:
    T, _ = _tuples()
    name = T.var_name(VAR, SCOPE)
    for r in reversed(oldest_first(rows)):
        if str(r.get("SensorName") or r.get("name") or "") == name or (r.get("var") == VAR and r.get("scope") == SCOPE):
            v = r.get("data") if isinstance(r.get("data"), dict) else r.get("value")
            if isinstance(v, dict):
                v = dict(v); v.pop("ts", None)
                return v
    return None


def _rams_of(rows) -> dict:
    """[A_SETTING_PER_RAM_CONNECTION_V1] every (channel, setting, <role name>) tuple that says something."""
    T, _ = _tuples()
    pre, out = T.var_name(VAR, ""), {}
    for r in oldest_first(rows):
        n = str(r.get("SensorName") or r.get("name") or "")
        scope = n[len(pre):] if n.startswith(pre) else (str(r.get("scope") or "") if r.get("var") == VAR else "")
        v = r.get("data") if isinstance(r.get("data"), dict) else r.get("value")
        if scope and scope != SCOPE and isinstance(v, dict):
            v = dict(v); v.pop("ts", None)
            if v:
                out[scope.lower()] = v
    return out


def _where() -> None:
    """Where this node's hosts file puts each RAM that has a tuple of its own. Read at a change of the tuples and at
    each hosts event, never per message."""
    global _at
    if not _rams:
        _at = {}
        return
    _, H = _tuples()
    at: dict = {}
    try:
        with open(H.HOSTS) as f:
            for line in f:
                p = line.split("#", 1)[0].split()
                for name in p[1:]:
                    if name.lower() in _rams:
                        at.setdefault(name.lower(), set()).add(p[0])
    except OSError as e:
        print("[CHANNEL-SETTING] this node's hosts file could not be read (%s): no RAM's own setting is used" % e, flush=True)
    _at = {n: next(iter(a)) for n, a in at.items() if len(a) == 1}       # a role with two addresses is at neither


def role_of(name: Optional[str]) -> str:
    """[THE_SETTING_IS_THE_ROLES_V1] The Role a memory's name says (the design's 11.1, version 0.2: "the tuple is keyed
    by the Role"). A Role's memory is an application memory (core/app_hosts.py): a Team's is <team>Host.<app> and the
    application's own is <app>Host.frognet, so the Role is the name before "Host.". "" for anything else: an address,
    the SystemRam, the ControlRam."""
    n = str(name or "").strip().lower()
    if n.startswith("["):
        return ""
    n = n.split(":", 1)[0]
    head, dot, _rest = n.partition("host.")
    if not dot or not head or "." in head or head in ("database", "frognet") or n.startswith("databasehost"):
        return ""
    return head


def ram_of(name: Optional[str]) -> str:
    """The Role a request is for, when that Role has a setting tuple of its own, (channel, setting, <Role>): read from
    the name the request itself gives (its Host). "" for anything else: that request is traffic of no Role, under
    (channel, setting, semantic)."""
    current()
    r = role_of(name)
    return r if r in _rams else ""


def setting_of(role: Optional[str]) -> Optional[dict]:
    """A Role's channel setting as this process holds it: {driver, seed, protection, switches}. With no Role, the
    setting of traffic that belongs to none, (channel, setting, semantic)."""
    s = current()
    return _rams.get(role.lower()) if role else s


RUNNING: Optional[Callable[[], dict]] = None     # the carrier's: {Role: what this process is sending under} (core/channel_carrier.py)


def report() -> None:
    """This end says what it is running now (its own tuple, (channel, at, ...)). The tuple has one writer in this
    process, the reporter's thread: whoever calls this hands it the word that something changed, and it writes the
    report as things stand when it gets there. Reports therefore leave in order and the last one is the latest."""
    start()
    _reports.put(1)


def _remove_reports_of_processes_that_ended() -> None:
    """[A_REPORT_ENDS_WITH_ITS_PROCESS_V1] Found on John's network, 22 Nov 2026: `channel_admin set units --seal` said
    "NOT every end is on it ... Behind: 10.251.251.1:proxy_main.py.103110, 10.251.251.1:proxy_main.py.91684". Those
    were reports of proxies that had been restarted. A process removes its own report when it exits; a process that
    is stopped while its ControlRam cannot be reached (on the ControlRam's own node, both are restarted together)
    leaves it, and the Admin's command then waits for an end that is not there.

    Once, when this process first reports: every report written from THIS node whose process is no longer running is
    removed. Only this node's reports are touched, and only for a pid this node says is gone (/proc/<pid>), so it is
    not racy across nodes. FrogNet has no reaper; this is the next process on the node tidying after the last."""
    try:
        T, _ = _tuples()
        mine = "%s:" % T.my_ip()
        pre = T.var_name("at", mine)
        gone = []
        for row in T._values_raw(SERVICE, CONTROL_RAM):
            name = str(row.get("SensorName") or "")
            pid = name.rsplit(".", 1)[-1]
            if name.startswith(pre) and pid.isdigit() and int(pid) != os.getpid() and not os.path.exists("/proc/%s" % pid) \
                    and row.get("SensorID") is not None and T._delete_by_id(CONTROL_RAM, row["SensorID"]):
                gone.append(name[len(pre) - len(mine):])
        if gone:
            print("[CHANNEL-SETTING] %s [A_REPORT_ENDS_WITH_ITS_PROCESS_V1] removed the report(s) of %d process(es) of "
                  "this node that are no longer running: %s" % (BUILD, len(gone), ", ".join(sorted(gone))), flush=True)
    except Exception as e:
        print("[CHANNEL-SETTING] the reports of this node's ended processes were not looked for: %r; they are looked "
              "for when a process of this node next starts" % (e,), flush=True)


def _reporter() -> None:
    first = True
    while True:
        _reports.get()
        if first:
            first = False
            _remove_reports_of_processes_that_ended()
        while True:                                  # several changes at once are one report
            try:
                _reports.get_nowait()
            except queue.Empty:
                break
        _say_where_i_am(None)


def setting_for(host: Optional[str], ram: Optional[str] = None) -> Optional[dict]:
    """The setting of this node's connection for the memory `ram` (John, 2026-11-21: "The setting in a tuple": it is
    the tuple (channel, setting, <the memory's name>), so it is read by that name). With no memory named, or one that
    has no tuple of its own, the channel's. Until 21 Nov this looked for a RAM at the address `host` and used the first
    by name: two memories on one machine then had one setting, and a request for a memory with no tuple of its own
    went under another memory's because it was on the same machine. `host` is not consulted any more."""
    s = current()
    if ram:
        r = _rams.get(ram.lower())
        if r is not None:
            return r
    return s


def here(ram: Optional[str] = None) -> Optional[dict]:
    """The setting of what ARRIVES at this machine for the memory `ram` (the connection says which, when it is for
    one): that memory's tuple. With no memory named: the channel's."""
    return setting_for(None, ram)


def read_here(ram: Optional[str] = None) -> Optional[dict]:
    read_now()
    return here(ram)


def _take(v: Optional[dict], how: str, rams: Optional[dict] = None) -> None:
    global _cur, _rams
    if rams is not None:
        kept = {}
        for role, new in rams.items():
            held = _rams.get(role)
            why = None if new == held else _proved(role, new, held)
            if why is None:
                kept[role] = new
                _ignored.pop(role, None)
                continue
            if _ignored.get(role) != new:            # said once for each setting ignored, not at every read
                _ignored[role] = new
                print("[CHANNEL-SETTING] %.6f %s [A_SETTING_PROVES_ITSELF_V1] a setting for %s is IGNORED: %s. This end keeps "
                      "running %s" % (time.time(), BUILD, role, why, json.dumps({k: x for k, x in (held or {}).items() if k != "check"}, sort_keys=True)
                                      if held else "with no setting for it"), flush=True)
                report()                             # [AN_END_SAYS_WHAT_IT_IGNORED_V1] (see _say_where_i_am)
            if held is not None:
                kept[role] = held
        rams = kept
    with _lock:
        new_rams = rams is not None and rams != _rams
        if v == _cur and not new_rams:
            return
        if new_rams:
            _rams = rams
            _where()
            print("[CHANNEL-SETTING] %.6f %s %s: [A_SETTING_PER_RAM_CONNECTION_V1] RAMs with a setting of their own: %s; at %s"
                  % (time.time(), BUILD, how, json.dumps(_rams, sort_keys=True) if _rams else "none", json.dumps(_at, sort_keys=True)), flush=True)
        changed = v != _cur
        _cur = v
        hear = list(_listeners)
    if changed:
        print("[CHANNEL-SETTING] %.6f %s %s: the channel is now %s" % (time.time(), BUILD, how, json.dumps(v, sort_keys=True)), flush=True)
    for fn in hear:                                  # the carrier puts its calls into its streams here, before the report
        try:
            fn(v)
        except Exception as e:
            print("[CHANNEL-SETTING] a listener raised %s: %r" % (type(e).__name__, e), flush=True)
    report()


def _hosts_watch() -> None:
    """The network event: the hosts file was rewritten. It wakes the hold when the hold is waiting for an event."""
    _, H = _tuples()
    ino = H._Inotify(os.path.dirname(H.HOSTS) or "/")
    while True:
        if os.path.basename(H.HOSTS) in ino.names():     # names() blocks in read()
            with _lock:
                _where()                                 # [A_SETTING_PER_RAM_CONNECTION_V1] a role may have moved
            _kick.set()


# [A_REPORT_NOT_WRITTEN_IS_WRITTEN_AT_THE_NEXT_EVENT_V1] this end's report has not reached the ControlRam. True from
# the start: an end says it is there, running nothing yet, when its held read first returns, and not only once a
# setting has changed (found between machines, 22 Nov: the Admin's first write counted the ends that happened to
# have used the channel).
_report_owed = True


def builds() -> str:
    """[AN_END_SAYS_ITS_BUILD_V1] The builds of the protocol layer's parts in this process, as one line. Each end puts
    it in its report; the hardware test (core/channel_test.py) fails an end whose line is not the controller's."""
    from core import channel_frame, held_fire
    return " ".join((BUILD, channel_frame.BUILD, held_fire.BUILD))


def _say_where_i_am(v: Optional[dict]) -> None:
    """[AN_END_SAYS_WHAT_IT_RUNS_V1] this end's own capability tuple, (channel, at, <its address>:<its process>): the
    setting it is running. The owner reads these (a held read) before it takes away something an end may still be
    using, so no end is ever sent what it does not accept. The process that wrote it removes it when it stops."""
    global _report_owed
    try:
        T, _ = _tuples()
        if True:                                     # (on the reporter's thread: the one writer of this tuple)
            # what this end HOLDS for each Role: the protocol, the algorithm, the count, whether it is sealed. Never
            # the seed, and never a key: "A report never contains a key or a seed" (the design's 3, step 4).
            v = _cur
            holds = {role: {"driver": x.get("driver"), "protection": x.get("protection") or ("aes-256-gcm" if x.get("number") else ""),
                            "number": int(x.get("number") or 0), "sealed": bool(x.get("number")),
                            # [A_ROLES_OWN_SECRET_MOVES_FORWARD_V1] "Each end's report carries its step": the step of
                            # the Role's own secret this process is on (none for a Role on the network's secret)
                            **({"step": step_in_use(role)} if step_in_use(role) is not None else {})}
                     for role, x in _rams.items()}
            # [AN_END_SAYS_WHAT_IT_IGNORED_V1] 23 Nov 2026. A machine that does not hold a Role's own secret cannot
            # verify that Role's setting and ignores it; that is right, it does not carry the Role. It says so here,
            # naming the setting it ignored by its number, protocol and step, so the owner's page can tell an end that
            # will never take the setting from one that has not taken it yet. (No reason and no secret is written.)
            for role, x in list(_ignored.items()):
                holds.setdefault(role, {})["ignored"] = {"number": int(x.get("number") or 0), "driver": x.get("driver"),
                                                         "step": int(x.get("step") or 0)}
            what = dict({"is": {k: x for k, x in (v or {}).items() if k != "check"} if v is not None else None},
                        **({"rams": holds} if holds else {}))
            what["build"] = builds()                  # [AN_END_SAYS_ITS_BUILD_V1] so nobody has to ask a node what it runs
            if RUNNING is not None:
                # [AN_END_REPORTS_WHAT_IT_RUNS_V1] the design's 10.6: for each Role this end carries, the protocol, the
                # count of switches and the algorithm it is sending under now. No key and no seed.
                what["running"] = RUNNING()
            ok = T.put(SERVICE, "at", "%s:%s.%d" % (T.my_ip(), os.path.basename(sys.argv[0]) or "py", os.getpid()),
                       what, dbhost=CONTROL_RAM, own=True)
        _report_owed = not ok
        if not ok:
            print("[CHANNEL-SETTING] this end's own tuple (what it runs) was not taken by the ControlRam; it is written "
                  "again when this end's held read next returns", flush=True)
    except Exception as e:
        _report_owed = True
        print("[CHANNEL-SETTING] this end's own tuple (what it runs) was not written: %r; it is written again when this "
              "end's held read next returns" % (e,), flush=True)


def _run() -> None:
    global _parked
    T, H = _tuples()
    seen = None
    threading.Thread(target=_hosts_watch, daemon=True, name="channel-setting-hosts").start()
    while True:
        # [AN_EVENT_DURING_THE_ATTEMPT_IS_NOT_LOST_V1] cleared BEFORE the hold is tried, not after it fails: found
        # between machines, 22 Nov 2026, once the read was held from the process's start. The hosts file was rewritten
        # while the hold was failing, the event was then cleared, and the end waited for an event that had come.
        _kick.clear()
        try:
            rows = H.wait_change(SERVICE, CONTROL_RAM, seen, name_like=T.var_name(VAR, "%"), wait_s=HOLD_S)
            seen = H.signature(rows)
        except Exception as e:
            # The hold was not taken. It is not chased on a timer: it is held again at the next event, which is the
            # hosts file being rewritten or this process next using the channel (a send or an arrival).
            print("[CHANNEL-SETTING] the hold on (%s, %s) was not taken: %r; holding again at the next event"
                  % (SERVICE, VAR, e), flush=True)
            _parked = True
            _kick.wait()
            _parked = False
            continue
        # [A_WAKE_IS_HANDED_ON_AND_THE_READ_IS_HELD_AGAIN_V1] John, 22 Nov 2026: "The held read should fire ON A THREAD
        # and immediately release." What the wake brought is handed to the one thread that takes settings up (in the
        # order the wakes came), and this thread holds again at once. It used to take the setting up itself, listeners
        # and all, and only then hold again.
        _woke.put((_value_of(rows), _rams_of(rows)))
        if _report_owed:
            # [A_REPORT_NOT_WRITTEN_IS_WRITTEN_AT_THE_NEXT_EVENT_V1] found between machines, 22 Nov 2026: an end that
            # started before the ControlRam could be reached failed its one report and never wrote another until a
            # setting changed; the Admin's command then counted five ends where there were six. The held read
            # returning is the event: the ControlRam has just answered.
            report()


def _taker() -> None:
    while True:
        v, rams = _woke.get()
        try:
            _take(v, "the held read returned", rams)
        except Exception as e:
            print("[CHANNEL-SETTING] %s what a wake brought was not taken up: %s: %r" % (BUILD, type(e).__name__, e), flush=True)


def start() -> None:
    """This process watches the tuple from here on. Called by whatever first asks what the channel is."""
    global _started
    with _lock:
        if _started:
            return
        _started = True
    threading.Thread(target=_taker, daemon=True, name="channel-setting-take").start()
    threading.Thread(target=_run, daemon=True, name="channel-setting").start()
    threading.Thread(target=_reporter, daemon=True, name="channel-setting-at").start()


def current() -> Optional[dict]:
    start()
    if _parked:
        _kick.set()                      # this use of the channel is the event the hold was waiting for
    return _cur


def connection_ended() -> None:
    """A connection of the channel ended. That is an event: the tuple is read once, off the caller's thread, so an end
    whose fire did not reach it learns what the channel is now."""
    threading.Thread(target=read_now, daemon=True, name="channel-setting-read").start()


def on_change(fn: Callable[[Optional[dict]], None]) -> None:
    with _lock:
        _listeners.append(fn)


def read_now() -> Optional[dict]:
    """One read of the tuple, made because something arrived that the setting as last read does not explain. It is
    made at that event, not on a timer."""
    T, _ = _tuples()
    try:
        rows = T.get(SERVICE, VAR, dbhost=CONTROL_RAM)
    except Exception as e:
        print("[CHANNEL-SETTING] the read made at an arrival failed: %r" % (e,), flush=True)
        return _cur
    v = None
    for r in oldest_first(rows):
        if str(r.get("scope") or "") == SCOPE or str(r.get("name") or "").endswith(SCOPE):
            v = r.get("value")
            if isinstance(v, dict):
                v = dict(v); v.pop("ts", None)
    if v is not None:
        _take(v, "a read made at an arrival", _rams_of(rows))
    return _cur


def driver(host: Optional[str] = None, ram: Optional[str] = None) -> Optional[str]:
    """The driver the tuple names for a session to `host`, when it is a word a driver has registered
    ([A_DRIVER_REGISTERS_ITS_WORD_V1], core/homa_transport.DRIVERS). A word nobody registered is said, and is no word."""
    from core import homa_transport
    d = (setting_for(host, ram) or {}).get("driver")
    if d is None:
        return None
    if d in homa_transport.DRIVERS and d not in ("auto", "homasim"):
        return d
    global _said_bad
    if d != _said_bad:
        _said_bad = d
        print("[CHANNEL-SETTING] the channel's tuple names the driver %r and no driver has registered that word (this build "
              "has %s): the node's own word decides" % (d, ", ".join(sorted(homa_transport.DRIVERS))), flush=True)
    return None


_said_bad = None
