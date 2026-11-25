#!/usr/bin/env python3
################################################################
#  Copyright (C) 2016-2026 Fawcett Innovations LLC             #
#  SPDX-License-Identifier: GPL-2.0-only                       #
################################################################
"""core/held_fire.py -- [THE_FIRE_RIDES_THE_HOLDERS_CONNECTION_V1] how a held read's fire reaches its holder.

John, 22 Nov 2026: "The held read should stick a message directly onto the outgoing socket. You cannot fire an API
down to something behind a firewall." And on 7 Nov, the ruling this follows: the role host "accepts and completes an
update to the tuple, runs down the list of systems holding a read on that tuple or family, and sends each a message
down its daemon socket that the held read fired."

What was built on 7 Nov did something else: api.php made a NEW request toward the holder, an HTTP POST to
http://<holder>/held_fire.php with a 5 s limit, which the holder's Apache and PHP then handed to the waiting process.
Found on his network, 22 Nov: a node whose web server refused the POST (403) never heard a fire and nothing said so
on that node; and a node that can dial out but cannot be dialled could never be fired at all.

Now:
  1. api.php on the role host completes the update, replies to the writer, and hands each fire to this node's daemon:
     one datagram to INTAKE in the held directory. It does not wait and there is no time limit.
  2. The role host's daemon (start_intake, one thread) hands the fire to the writer of a connection the HOLDER'S NODE
     opened to it, and is done with it: the TCP driver's return channel (a frame numbered TCP_FIRE_SEQ), or the node's
     framed connection (a message that begins FRAME_MARK). Each carrier's listener registers how (add_sender).
  3. The holder's proxy reads it off that connection and hands it to the waiting process's own socket,
     <held directory>/<hid>.sock (deliver_here). No web server is in the path on the holder's node.
  4. No connection from that node: the fire has nowhere to go. That is said here, naming the node, and the hold has
     ended; the holder holds again at its next event. Gone is gone. Nothing is tried in its place.

A holder on the role host itself is handed its fire directly (deliver_here).
The holder's side is unchanged: core/frognet_held.py, one socket and one thread for a process's holds.
"""
import json
import os
import re
import socket
import threading
import time
from typing import Callable, List, Optional

BUILD = "held-fire-v2-20261123g"
HELD_DIR = os.environ.get("FROGNET_HELD_DIR", "/run/frognet/held")
INTAKE = "role-host-fires.sock"          # in the held directory: api.php writes fires here; the daemon reads them
TCP_FIRE_SEQ = 0xFFFFFFFE                # the TCP driver: a frame from the daemon with this number answers no request; it is a fire
FRAME_MARK = b"HELDFIRE\x01"             # the frame carrier: a message from the daemon's end that begins so is a fire

_HID = re.compile(r"^[A-Za-z0-9._:-]{1,64}$")
_senders: List[Callable[[str, bytes], Optional[str]]] = []
_lock = threading.Lock()
_started = False


def _say(text: str) -> None:
    print("[HELD-FIRE] %.6f %s %s" % (time.time(), BUILD, text), flush=True)


def add_sender(fn: Callable[[str, bytes], Optional[str]]) -> None:
    """A carrier's listener says how it puts a fire on a connection from a node: fn(node's address, the fire) returns
    the name of what it was handed to, or None when it has no connection from that node."""
    with _lock:
        _senders.append(fn)


def holds_ended(why: str) -> int:
    """[A_HOLD_THAT_ENDED_IS_HELD_AGAIN_V1] This node's proxy: a wire session of this node's ended, or the proxy has
    just started. The holds whose fires rode it are gone at the role host. Every process on this node that holds a
    read is told, a datagram to its socket, and holds again. Returns how many were told."""
    told = 0
    try:
        names = [n for n in os.listdir(HELD_DIR) if n.endswith(".sock") and n != INTAKE]
    except OSError:
        return 0
    raw = json.dumps({"ended": why}).encode("utf-8")
    for n in names:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        try:
            s.setblocking(False)
            s.sendto(raw, os.path.join(HELD_DIR, n))
            told += 1
        except OSError:
            pass                                       # a socket file with no process behind it: that holder is gone
        finally:
            s.close()
    _say("[A_HOLD_THAT_ENDED_IS_HELD_AGAIN_V1] %s: %d holder(s) on this node told to hold again" % (why, told))
    return told


def deliver_here(raw: bytes, how: str) -> bool:
    """A fire for a holder on THIS node: hand it, unchanged, to the process that holds the read, a datagram to its
    socket. `how`: by what it came, for the log."""
    try:
        msg = json.loads(raw)
        hid = str(msg.get("hid") or "")
    except (ValueError, AttributeError):
        _say("what arrived %s is not a fire (%d bytes): dropped" % (how, len(raw)))
        return False
    if not _HID.match(hid):
        _say("a fire arrived %s with no holder's id: dropped" % how)
        return False
    path = os.path.join(HELD_DIR, hid + ".sock")
    what = "hold=%s hid=%s %s.%s" % (msg.get("hold"), hid, msg.get("SensorType"), msg.get("SensorName"))
    s = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    try:
        s.setblocking(False)
        s.sendto(raw, path)
    except OSError as e:
        _say("fire %s arrived %s and there is no such holder here (%s: %s): the hold has ended" % (what, how, path, e))
        return False
    finally:
        s.close()
    _say("fire %s arrived %s: handed to the holder's socket" % (what, how))
    return True


def _route(holder: str, raw: bytes, local: Callable[[], set]) -> None:
    try:
        mine = set(local())
    except Exception as e:
        mine = set()
        _say("this node's own addresses could not be listed (%s: %r)" % (type(e).__name__, e))
    if holder in mine or holder.startswith("127."):
        deliver_here(raw, "from this node's own api.php")
        return
    with _lock:
        senders = list(_senders)
    for fn in senders:
        try:
            went = fn(holder, raw)
        except Exception as e:
            went = None
            _say("a fire for %s could not be handed to a connection (%s: %r)" % (holder, type(e).__name__, e))
        if went:
            try:
                m = json.loads(raw)
            except ValueError:
                m = {}
            _say("fire hold=%s hid=%s %s.%s for %s: on %s" % (m.get("hold"), m.get("hid"), m.get("SensorType"), m.get("SensorName"), holder, went))
            return
    _say("a fire for %s has nowhere to go: that node has no connection open to this daemon. Its hold has ended; "
         "it holds again at its next event (%s)" % (holder, raw[:160].decode("utf-8", "replace")))


def start_intake(local_ips: Callable[[], set]) -> None:
    """The role host's daemon: take the fires api.php hands over, and put each on its holder's connection. Once."""
    global _started
    with _lock:
        if _started:
            return
        _started = True
    os.makedirs(HELD_DIR, exist_ok=True)
    try:
        os.chmod(HELD_DIR, 0o1777)                       # as core/frognet_held.py leaves it: the web server's user writes here
    except OSError:
        pass
    path = os.path.join(HELD_DIR, INTAKE)
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass
    s = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    s.bind(path)
    os.chmod(path, 0o666)                                # api.php runs as the web server's user

    def run() -> None:
        while True:
            try:
                data = s.recv(1 << 20)                   # blocks: each datagram is one fire
            except OSError as e:
                _say("the intake %s ended (%s): fires from this node's api.php are not taken any more" % (path, e))
                return
            try:
                item = json.loads(data)
                holder, fire = str(item["holder"]), item["fire"]
                raw = (fire if isinstance(fire, str) else json.dumps(fire)).encode("utf-8")
            except (ValueError, KeyError, TypeError) as e:
                _say("what api.php handed over is not a fire (%s): dropped" % e)
                continue
            _route(holder, raw, local_ips)

    threading.Thread(target=run, daemon=True, name="held-fire-intake").start()
    _say("the daemon takes this node's fires at %s and puts each on its holder's connection" % path)
