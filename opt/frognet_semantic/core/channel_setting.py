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
channel_setting.py -- [CHANNEL_SETTING_IS_A_TUPLE_V1] the semantic wire's channel setting, held in a capability tuple.

John, 2026-11-19: "It does not have to introduce anything. It is a tuple. A control tuple and part of the system
capability tuple. You do realize we have full control over the contents of the json that tuple holds, right? It does
not need to be standard in any way." And: "You can also introduce other capability tuples. There is not a fixed form,
and the consumer is the thing that wrote it."

The tuple is (channel, setting, semantic) in the ControlRam, where the capability tuples live. Its JSON is this
module's and nobody else's: this module writes it (write(), the owner's act) and this module reads it (the held read
below). What it holds today:

    driver    "tcp" or "softhoma": the driver a NEW session opens on. Absent: the node's own word decides, as before.
    key       the NAME of the key a message is sent under. Absent or "": messages are sent as the driver packs them.
    accept    the names of the keys a message may arrive under. Absent: only `key`.

The key itself is never in the tuple. The tuple crosses the wire; a key is a file on the machine
(FROGNET_CHANNEL_KEYS, default /etc/frognet/channel_keys/<name>, 32 bytes), put there by hand.

A process learns of a change through its held read on the tuple (frognet_held.wait_change). Nothing polls and nothing
runs on a timer. When the hold is not taken, it is held again at the next network event (the hosts file is rewritten).
A process with no such tuple in its ControlRam behaves exactly as it did before this file existed.

Protection, when a key is named: AES-256-GCM over the whole frame, before the driver cuts it into its units. A
protected message carries its key's name in the clear, a 12-byte nonce and the ciphertext; the message's number is
bound in as associated data. A reply goes under the key its request came under.
"""
import json
import os
import struct
import sys
import threading
import time
from typing import Callable, List, Optional, Tuple

BUILD = "channel-setting-is-a-tuple-v1-20261119b"
SERVICE, VAR, SCOPE = "channel", "setting", "semantic"
CONTROL_RAM = "databasehost_control.frognet"
HOLD_S = 300.0
KEYS = os.environ.get("FROGNET_CHANNEL_KEYS", "/etc/frognet/channel_keys")

_lock = threading.Lock()
_cur: Optional[dict] = None             # the tuple's JSON as last read (without its ts), None when there is none
_started = False
_listeners: List[Callable[[Optional[dict]], None]] = []
_keys = {}                               # name -> AESGCM
_kick = threading.Event()
_parked = False


class KeyRefused(Exception):
    """A message arrived under a key this channel does not accept, or under one this machine does not hold."""


def _tuples():
    from core import frognet_tuples
    d = os.path.dirname(os.path.abspath(frognet_tuples.__file__))       # [CORE_ON_THE_PATH_V1] as homa_liveness does
    if d not in sys.path:
        sys.path.append(d)
    import frognet_held
    return frognet_tuples, frognet_held


def write(value: dict, dbhost: str = CONTROL_RAM) -> bool:
    """The owner's act: set the channel. own=False: the tuple outlives the process that wrote it."""
    T, _ = _tuples()
    return bool(T.put(SERVICE, VAR, SCOPE, dict(value), dbhost=dbhost, own=False))


def _value_of(rows) -> Optional[dict]:
    T, _ = _tuples()
    name = T.var_name(VAR, SCOPE)
    for r in rows or []:
        if str(r.get("SensorName") or r.get("name") or "") == name or (r.get("var") == VAR and r.get("scope") == SCOPE):
            v = r.get("data") if isinstance(r.get("data"), dict) else r.get("value")
            if isinstance(v, dict):
                v = dict(v); v.pop("ts", None)
                return v
    return None


def _take(v: Optional[dict], how: str) -> None:
    global _cur
    with _lock:
        if v == _cur:
            return
        _cur = v
        hear = list(_listeners)
    print("[CHANNEL-SETTING] %.6f %s %s: the channel is now %s" % (time.time(), BUILD, how, json.dumps(v, sort_keys=True)), flush=True)
    # [A_KEY_IS_READY_BEFORE_ITS_FIRST_MESSAGE_V1] the keys this setting names are loaded here, on this thread, when the
    # setting is read: the first message under a key does not wait for the cipher to be loaded (19 Nov: one run had no
    # answer for 766 ms at the first message under a key; the others 42 to 59 ms).
    for name in sorted(set(_accepted(v)) | {str((v or {}).get("key") or "")}):
        if name:
            try:
                _aes(name)
            except KeyRefused as e:
                print("[CHANNEL-SETTING] the channel names the key %r: %s" % (name, e), flush=True)
    threading.Thread(target=_say_where_i_am, args=(v,), daemon=True, name="channel-setting-at").start()
    for fn in hear:
        try:
            fn(v)
        except Exception as e:
            print("[CHANNEL-SETTING] a listener raised %s: %r" % (type(e).__name__, e), flush=True)


def _hosts_watch() -> None:
    """The network event: the hosts file was rewritten. It wakes the hold when the hold is waiting for an event."""
    _, H = _tuples()
    ino = H._Inotify(os.path.dirname(H.HOSTS) or "/")
    while True:
        if os.path.basename(H.HOSTS) in ino.names():     # names() blocks in read()
            _kick.set()


def _say_where_i_am(v: Optional[dict]) -> None:
    """[AN_END_SAYS_WHAT_IT_RUNS_V1] this end's own capability tuple, (channel, at, <its address>:<its process>): the
    setting it is running. The owner reads these (a held read) before it takes away something an end may still be
    using, so no end is ever sent what it does not accept. The process that wrote it removes it when it stops."""
    try:
        T, _ = _tuples()
        ok = T.put(SERVICE, "at", "%s:%s.%d" % (T.my_ip(), os.path.basename(sys.argv[0]) or "py", os.getpid()),
                   {"is": v}, dbhost=CONTROL_RAM, own=True)
        if not ok:
            print("[CHANNEL-SETTING] this end's own tuple (what it runs) was not taken by the ControlRam", flush=True)
    except Exception as e:
        print("[CHANNEL-SETTING] this end's own tuple (what it runs) was not written: %r" % (e,), flush=True)


def _run() -> None:
    global _parked
    T, H = _tuples()
    seen = None
    threading.Thread(target=_hosts_watch, daemon=True, name="channel-setting-hosts").start()
    while True:
        try:
            rows = H.wait_change(SERVICE, CONTROL_RAM, seen, name_like=T.var_name(VAR, "%"), wait_s=HOLD_S)
            seen = H.signature(rows)
        except Exception as e:
            # The hold was not taken. It is not chased on a timer: it is held again at the next event, which is the
            # hosts file being rewritten or this process next using the channel (a send or an arrival).
            print("[CHANNEL-SETTING] the hold on (%s, %s) was not taken: %r; holding again at the next event"
                  % (SERVICE, VAR, e), flush=True)
            _kick.clear()
            _parked = True
            _kick.wait()
            _parked = False
            continue
        _take(_value_of(rows), "the held read returned")


def start() -> None:
    """This process watches the tuple from here on. Called by whatever first asks what the channel is."""
    global _started
    with _lock:
        if _started:
            return
        _started = True
    threading.Thread(target=_run, daemon=True, name="channel-setting").start()


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
    for r in rows:
        if str(r.get("scope") or "") == SCOPE or str(r.get("name") or "").endswith(SCOPE):
            v = r.get("value")
            if isinstance(v, dict):
                v = dict(v); v.pop("ts", None)
    if v is not None:
        _take(v, "a read made at an arrival")
    return _cur


def driver() -> Optional[str]:
    s = current()
    d = (s or {}).get("driver")
    return d if d in ("tcp", "softhoma") else None


def sending_key() -> str:
    return str((current() or {}).get("key") or "")


def _accepted(s: Optional[dict]) -> Tuple[str, ...]:
    s = s or {}
    a = s.get("accept")
    if isinstance(a, list):
        return tuple(str(x) for x in a)
    return (str(s["key"]),) if s.get("key") else ()


def refuses_no_key(s: Optional[dict]) -> bool:
    """Whether a request under no key is refused: the channel names keys to accept and "" is not among them."""
    a = _accepted(s)
    return bool(a) and "" not in a


def _aes(name: str):
    with _lock:
        k = _keys.get(name)
    if k is None:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        try:
            with open(os.path.join(KEYS, name), "rb") as f:
                raw = f.read()
        except OSError as e:
            raise KeyRefused("this machine does not hold the key %r (%s)" % (name, e))
        if len(raw) != 32:
            raise KeyRefused("the key %r on this machine is %d bytes, not 32" % (name, len(raw)))
        k = AESGCM(raw)
        with _lock:
            _keys[name] = k
    return k


def protect(n: int, frame: bytes, name: str) -> bytes:
    """frame under the key `name`: the name's length and the name, a nonce, the ciphertext."""
    nb = name.encode()
    nonce = os.urandom(12)
    return bytes([len(nb)]) + nb + nonce + _aes(name).encrypt(nonce, bytes(frame), struct.pack("!Q", n & ~1) + nb)


def unprotect(n: int, blob: bytes, is_request: bool) -> Tuple[bytes, str]:
    """(the frame, the key's name). A request must be under a key the channel accepts; a reply is under the key its
    request went out under, which this end chose."""
    ln = blob[0]
    name = blob[1:1 + ln].decode()
    if is_request and name not in _accepted(current()) and name not in _accepted(read_now()):
        raise KeyRefused("a request arrived under the key %r and the channel accepts %s" % (name, list(_accepted(_cur)) or "none"))
    nonce, ct = blob[1 + ln:13 + ln], blob[13 + ln:]
    try:
        return _aes(name).decrypt(nonce, bytes(ct), struct.pack("!Q", n & ~1) + name.encode()), name
    except KeyRefused:
        raise
    except Exception as e:
        raise KeyRefused("a message under the key %r did not open (%s): it was not made with this machine's copy of that key"
                         % (name, type(e).__name__))
