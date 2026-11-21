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
"""core/channel_frame.py -- [ONE_FRAME_UNDER_EVERY_PROTOCOL_V1] the frame every protocol's data crosses a connection in.

The design is "CroakOS Protocol Layer Demonstration: Requirements and Detailed Design", version 0.2, and its
walkthrough, version 0.3 (sections 4 to 7 and 10). John, 2026-11-21:

    "Protocols and encryption are different. Changing the protocol is a matter of changing the data inside the frame
    and only works if the encryption did not change. Changing the encryption is using a deterministic algorithm to
    decide which key to use. Then just start."
    "the sender switches and everything after goes. When the sender switches it does that using a call that says
    switch protocol or switch encryption. It does not say what from or to."
    "There is only the current key. There is no history and no old values to check."
    "The new key is one of the 20 or 50 or whatever that were provided offline."  "Key and protocol are independent."
    "Two roles with different keys can share the same connection through the packet ID."

A Link is one end of one permanently open connection to one destination. Everything for that destination crosses it,
whatever Role it is for and whatever protocol that Role's channel is set to.

    on the connection   <length> <packet ID> <the data, sealed under the Role's current key, or not sealed>
    the data, opened    <the protocol's word, 4 bytes> <what that protocol's driver packed>

  * The ENCRYPTION is the outside. This file seals every frame after a driver has packed it and opens it before a
    driver unpacks it. No driver seals; no driver requires or refuses a key.
  * The PROTOCOL is the inside. The receiver opens the frame and reads the word; it is not told the protocol by a
    port, by the sender or by the setting. So a protocol is switched only while the encryption holds still.
  * The PACKET ID says which Role a frame is for. An ID is <counter> <sequence, 8 bits> <reply, 1 bit>. The end that
    opened the connection gives each Role a sequence of its own; a reply has its request's ID with the reply bit.
    The first frame of a sequence, in each direction, names the Role and states the sender's count of encryption
    switches, in the clear, and carries no data (counter 0, reply bit 0). Nothing else is added to a frame.
  * A SWITCH is a frame like any other, sealed like any other, whose data is one word: SWP (switch protocol) or SWE
    (switch encryption). It says nothing of what from or to. It has counter 0 and the reply bit. Everything the
    sender puts on the connection for that Role after it is the new way, and every read after it is the new way.
    Each direction is its own stream: an end switches its sending when its held read on the setting wakes it
    (setting_changed), and its receiving when the call arrives.
  * The KEY in use after n switches is one of the Role's set, <keys>/<role>/ sorted by file name, 32 bytes each,
    placed offline. Which one is a fixed rule on the setting's seed and n (key_number). Only the key in use and n are
    kept. A setting with no seed is not sealed.
  * The length and the packet ID cross as small numbers: the length as a varint, the ID as what the far end does
    not already know of it (_put). The next request of the same Role spends one byte on its ID. (John, 2026-11-21: "All of the control should diff.")

  * [THE_KEY_THAT_OPENS_IS_THE_CURRENT_ONE_V1] John, 2026-11-21 13:17: "When a Role's frame does not open under
    the key and algorithm an end holds, at connection or after, the end runs through every key of the Role's set
    under every registered algorithm. The pair that opens the frame is the current one. The end takes it, logs what
    it held and what opened, and carries on. The count of switches is unchanged. If nothing opens, the frame is
    refused and logged as today. This replaces any limit on how fast an owner may command switches."
    So a receiver follows the sender with nothing but the Role's set: it needs neither the setting nor the count
    to be up to date. (The first build read the setting when a call took its count past what it held, on the very
    thread that reads the connection: the read went out on that connection, waited for its own reply until it timed
    out, and went on with the old algorithm. Found 21 Nov in the first mixed run between machines: a key switch took
    20 s and, after an algorithm switch, a frame did not open. A receiver NEVER reads the setting on that thread.)
    One thing the search cannot find is encryption turned off: there is nothing to open, and a plain frame is what a
    forgery looks like. An end takes unsealed frames for a Role only when the setting it holds, which has proved
    itself (core/channel_setting.py, [A_SETTING_PROVES_ITSELF_V1]), shows no seed.

  * [A_CONNECTIONS_WIRE_HAS_ONE_OWNER_V1] John, 2026-11-21 13:23: "A connection's wire has one owner. One writer
    for each connection takes messages from the request threads and alone packs, sends switch calls, seals and
    writes. No request thread touches the socket." So there is no lock on sending: submit() and setting_changed()
    hand the connection's writer what there is to do, in the order they are called, and the writer does it. Reading
    has one owner as well: the thread in pump() (or the one calling receive()).

A frame that does not open ends the connection with the reason (no fallback): the threads with requests on it retry
with CACHE_MISS on the connection that replaces it, which starts from the setting's count.
"""
import hashlib
import os
import queue
import struct
import threading
import time
from typing import Callable, Dict, List, Optional, Tuple

from core import channel_protection as CP
from core.transport_driver import ConnectionGone

BUILD = "channel-frame-v4-20261121d"
KEYS = os.environ.get("FROGNET_CHANNEL_KEYS", "/etc/frognet/channel_keys")
SWITCH_PROTOCOL = b"SWP\x01"
SWITCH_ENCRYPTION = b"SWE\x01"
DEFAULT_PROTECTION = "aes-256-gcm"
MAX_FRAME = 64 << 20
STREAMS = 256

#: a protocol: the word a setting names -> (its 4-byte word in a frame, pack, unpack).
#: pack(state, packet_id, message) -> the data after the word; unpack(state, packet_id, data) -> the message.
#: `state` is a dict that is this protocol's own, for one Role in one direction of one connection, empty at the start
#: and empty again after a switch of protocol: what a driver keeps to send differences against.
DRIVERS: Dict[str, Tuple[bytes, Callable, Callable]] = {}
WORDS: Dict[bytes, str] = {}


def register_driver(name: str, word: bytes, pack: Callable, unpack: Callable) -> None:
    if len(word) != 4 or word in (SWITCH_PROTOCOL, SWITCH_ENCRYPTION) or WORDS.get(word, name) != name:
        raise ValueError("the protocol %r cannot have the word %r" % (name, word))
    DRIVERS[name] = (word, pack, unpack)
    WORDS[word] = name


# TCP as a protocol under the frame (the design's 11.5): the message as it is. Its length and number are the frame's.
register_driver("tcp", b"TCP\x01", lambda state, pid, message: bytes(message), lambda state, pid, data: bytes(data))


class DoesNotOpen(ConnectionGone):
    pass


# ---------------------------------------------------------------------------------------------- the stored keys
def key_set(role: str, keys_dir: Optional[str] = None) -> List[bytes]:
    """A Role's set of keys on this machine, in the order every machine has them: by file name."""
    d = os.path.join(keys_dir or KEYS, role)
    try:
        names = sorted(n for n in os.listdir(d) if not n.startswith("."))
    except OSError as e:
        raise DoesNotOpen("this machine holds no set of keys for %r (%s): it does not carry that Role's traffic" % (role, e))
    keys = []
    for n in names:
        with open(os.path.join(d, n), "rb") as f:
            k = f.read()
        if len(k) != 32:
            raise DoesNotOpen("the key %s/%s is %d bytes; a key is 32" % (role, n, len(k)))
        keys.append(k)
    if len(keys) < 2:
        raise DoesNotOpen("the set of keys for %r holds %d; a set is two or more" % (role, len(keys)))
    return keys


def _h(seed: str, n: int) -> int:
    return int.from_bytes(hashlib.sha256(seed.encode("utf-8") + struct.pack("!Q", n)).digest()[:8], "big")


def key_number(seed: str, n: int, size: int, before: Optional[Tuple[int, int]] = None) -> int:
    """Which key of a set of `size` is in use after n switches. key(0) = h(0) mod size;
    key(n) = (key(n-1) + 1 + h(n) mod (size-1)) mod size, so a switch never lands on the key in use.
    `before`: (m, key(m)) already worked out for m <= n, to go on from."""
    m, k = before if before and before[0] <= n else (0, _h(seed, 0) % size)
    while m < n:
        m += 1
        k = (k + 1 + _h(seed, m) % (size - 1)) % size
    return k


# ---------------------------------------------------------------------------------------------- numbers on the wire
def packet_id(counter: int, stream: int, reply: bool) -> int:
    return (counter << 9) | (stream << 1) | (1 if reply else 0)


def id_parts(pid: int) -> Tuple[int, int, bool]:
    """(counter, sequence, reply) of a packet ID"""
    return pid >> 9, (pid >> 1) & 0xff, bool(pid & 1)


def _varint(z: int) -> bytes:
    out = bytearray()
    while z >= 0x80:
        out.append(0x80 | (z & 0x7f)); z >>= 7
    out.append(z)
    return bytes(out)


class _Side:
    """One Role, one direction of one connection: the protocol in use, the count of switches, the key in use."""
    __slots__ = ("role", "word", "n", "k", "key", "seed", "protection", "state", "named", "count")

    def __init__(self, role: str):
        self.role, self.word, self.n, self.k, self.key = role, None, 0, None, None
        self.seed, self.protection, self.state, self.named = "", DEFAULT_PROTECTION, {}, False
        self.count = 0                  # sending: the last counter of this Role's sequence of requests

    def rekey(self, n: int, setting: Optional[dict], keys: Callable) -> None:
        """The key after n switches, under the setting as it stands. Only it and n are kept."""
        s = setting or {}
        seed = str(s.get("seed") or "")
        self.protection = str(s.get("protection") or DEFAULT_PROTECTION)
        if self.protection not in CP.PROTECTIONS:
            raise DoesNotOpen("the channel of %r names the algorithm %r, which this machine has not registered"
                              % (self.role, self.protection))
        if not seed:
            self.n, self.k, self.key, self.seed = n, None, None, ""
            return
        ks = keys(self.role)
        before = (self.n, self.k) if seed == self.seed and self.k is not None else None
        self.k = key_number(seed, n, len(ks), before)
        self.n, self.key, self.seed = n, ks[self.k], seed

    def running(self) -> dict:
        return {"driver": WORDS.get(self.word, ""), "switches": self.n, "protection": self.protection if self.key else "",
                "sealed": bool(self.key)}


class Link:
    """One end of one permanently open connection (the module's text).

    sock      a connected stream socket
    opener    True at the end that opened the connection: it gives each Role its sequence of packet IDs
    setting   setting(role) -> that Role's channel setting as this process holds it: {driver, seed, protection,
              switches}, or None. role "" is traffic of no Role.
    deliver   deliver(role, sequence, counter, reply, message), called for each message in the order it is opened,
              by pump() and by setting_changed(). Without it, receive() returns them one at a time.
    keys      keys(role) -> the Role's set on this machine
    """

    def __init__(self, sock, far: Tuple[str, int], opener: bool, setting: Callable, deliver: Optional[Callable] = None,
                 keys: Callable = key_set, say: Callable = None):
        self.sock, self.far, self.opener = sock, far, opener
        self._setting, self._deliver, self._keys = setting, deliver, keys
        self._ready: List[tuple] = []                # messages opened and not yet returned by receive()
        self._say = say or (lambda text: print("[CHANNEL-FRAME] %.6f %s" % (time.time(), text), flush=True))
        self._todo: "queue.SimpleQueue" = queue.SimpleQueue()      # what the writer is handed, in order
        self._running: Dict[str, dict] = {}          # what this end is sending under, as the writer last left it
        self.broken: Optional[str] = None            # why the writer stopped, once it has
        self.on_broken: Optional[Callable] = None    # on_broken(why): the writer could not write
        self.on_sent: Optional[Callable] = None      # on_sent(role, reply, bytes, protocol): a message went on the connection
        self.on_running: Optional[Callable] = None   # on_running(): what running() returns has changed
        self._out: Dict[int, _Side] = {}
        self._in: Dict[int, _Side] = {}
        self._stream: Dict[str, int] = {}
        self._last_out = self._last_in = -1          # the sequence the last frame each way was on
        self._count_out: Dict[int, int] = {}         # a sequence's last counter, each way
        self._count_in: Dict[int, int] = {}
        self._rf = sock.makefile("rb", buffering=1 << 16)
        self.sent = self.received = 0                # bytes this end has put on, and taken off, the connection
        self.calls = {"protocol": [0, 0], "encryption": [0, 0]}      # [sent, received]
        self.found = 0                               # frames that opened under another key or algorithm than this end held
        self._nonce_head, self._nonce_n = os.urandom(8), 0
        self._writer = threading.Thread(target=self._write, daemon=True, name="frame-write-%s" % (far[0],))
        self._writer.start()

    # ---- the wire
    def _put(self, pid: int, body: bytes) -> None:
        """One frame: its length, its packet ID, its body. The ID crosses as what is not already known at the far end:
        whether it is on the sequence the last frame was on (else the sequence, one byte), and its counter as the
        change from one more than that sequence's last counter this way. The next request of the same Role is one
        byte of ID."""
        counter, stream, reply = id_parts(pid)
        same = stream == self._last_out
        if counter:
            d = counter - self._count_out.get(stream, 0) - 1
            x = 1 + ((d << 1) if d >= 0 else ((-d << 1) - 1))
            self._count_out[stream] = counter
        else:
            x = 0
        unit = _varint(len(body)) + _varint((x << 2) | (2 if reply else 0) | (1 if same else 0)) + (b"" if same else bytes([stream])) + body
        self._last_out = stream
        try:
            self.sock.sendall(unit)
        except (OSError, AttributeError) as e:
            raise ConnectionGone("send to %s:%d: %s" % (self.far[0], self.far[1], e)) from e
        self.sent += len(unit)

    def _take_varint(self) -> int:
        z, shift = 0, 0
        while True:
            b = self._rf.read(1)
            if not b:
                raise ConnectionGone("the far end at %s:%d closed the connection" % self.far)
            self.received += 1
            z |= (b[0] & 0x7f) << shift
            shift += 7
            if not b[0] & 0x80:
                return z
            if shift > 70:
                raise ConnectionGone("a number from %s:%d does not end: the far end does not speak this frame" % self.far)

    def _take(self) -> Tuple[int, bytes]:
        try:
            n = self._take_varint()
            z = self._take_varint()
            if n > MAX_FRAME:
                raise ConnectionGone("a frame from %s:%d gives its length as %d: the far end does not speak this frame"
                                     % (self.far[0], self.far[1], n))
            stream = self._last_in
            if not z & 1:
                b = self._rf.read(1)
                if not b:
                    raise ConnectionGone("the far end at %s:%d closed the connection" % self.far)
                stream = b[0]
                self.received += 1
            body = self._rf.read(n) if n else b""
            if body is None or len(body) < n:
                raise ConnectionGone("the far end at %s:%d closed the connection" % self.far)
        except (OSError, ValueError) as e:
            raise ConnectionGone("receive from %s:%d: %s" % (self.far[0], self.far[1], e)) from e
        self.received += n
        self._last_in = stream
        x, counter = z >> 2, 0
        if x:
            x -= 1
            counter = self._count_in.get(stream, 0) + 1 + ((x >> 1) if not x & 1 else -((x + 1) >> 1))
            self._count_in[stream] = counter
        return packet_id(counter, stream, bool(z & 2)), body

    # ---- sealing: the outside of every frame
    def _seal(self, side: _Side, pid: int, data: bytes) -> bytes:
        if side.key is None:
            return data
        # 12 bytes never used twice under any key: 8 drawn for this connection when it opens (and again when the
        # count runs out), never stored, and a count. The writer is the only thread here, so nothing guards it.
        self._nonce_n += 1
        if self._nonce_n >= 1 << 32:
            self._nonce_head, self._nonce_n = os.urandom(8), 1
        nonce = self._nonce_head + struct.pack("!I", self._nonce_n)
        return nonce + CP.PROTECTIONS[side.protection][0](side.key, nonce, struct.pack("!Q", pid), data)

    @staticmethod
    def _plain(data: bytes) -> bool:
        """Whether these bytes are what an unsealed frame holds: a call, or data that begins with a protocol's word."""
        return bytes(data) in (SWITCH_PROTOCOL, SWITCH_ENCRYPTION) or bytes(data[:4]) in WORDS

    def _open(self, side: _Side, pid: int, body: bytes) -> bytes:
        """The data of a frame. Under the key and algorithm this side holds; and when that does not open it, under
        whichever key of the Role's set and whichever registered algorithm does: that pair is the current one."""
        aad = struct.pack("!Q", pid)
        if side.key is None and self._plain(body):
            return body
        if side.key is not None and len(body) >= 12 + CP.TAG:
            try:
                return CP.PROTECTIONS[side.protection][1](side.key, body[:12], aad, body[12:])
            except ValueError:
                pass
        held = "key %d of its set under %s" % (side.k, side.protection) if side.key is not None else "no key"
        try:
            ks = self._keys(side.role)
        except DoesNotOpen:
            ks = []
        if len(body) >= 12 + CP.TAG:
            for name in [side.protection] + sorted(n for n in CP.PROTECTIONS if n != side.protection):
                if name not in CP.PROTECTIONS:
                    continue
                for i, key in enumerate(ks):
                    if key is side.key and name == side.protection:
                        continue
                    try:
                        data = CP.PROTECTIONS[name][1](key, body[:12], aad, body[12:])
                    except ValueError:
                        continue
                    self.found += 1
                    self._say("from %s:%d for %r: a frame did not open under what this end held (%s, at %d switches); it "
                              "opened under key %d of the set under %s, which is the current one from here"
                              % (self.far[0], self.far[1], side.role, held, side.n, i, name))
                    side.k, side.key, side.protection = i, key, name
                    side.seed = str((self._setting(side.role) or {}).get("seed") or side.seed)
                    return data
        if side.key is not None and self._plain(body) and not (self._setting(side.role) or {}).get("seed"):
            self._say("from %s:%d for %r: an unsealed frame, and the setting this end holds shows no seed: this Role is "
                      "not sealed from here (this end held %s)" % (self.far[0], self.far[1], side.role, held))
            side.k = side.key = None
            return body
        if side.key is None:
            raise DoesNotOpen("data from %s:%d for %r begins %r, which is no registered protocol's word, and it opens under "
                              "none of the %d keys of this machine's set for that Role: refused"
                              % (self.far[0], self.far[1], side.role, bytes(body[:4]), len(ks)))
        raise DoesNotOpen("a frame from %s:%d for %r does not open: not under what this end held (%s, at %d switches), and "
                          "not under any of the %d keys of this machine's set for that Role with any of the %d registered "
                          "algorithms%s" % (self.far[0], self.far[1], side.role, held, side.n, len(ks), len(CP.PROTECTIONS),
                                            "; an unsealed frame is taken only when the setting this end holds shows no seed"
                                            if self._plain(body) else ""))

    # ---- sending
    def _out_side(self, role: str, stream: Optional[int]) -> Tuple[int, _Side]:
        """This end's sending side for a Role, named on the connection before its first frame."""
        if stream is None:
            stream = self._stream.get(role)
            if stream is None:
                if not self.opener:
                    raise ConnectionGone("this end did not open the connection to %s:%d and has no sequence for %r"
                                         % (self.far[0], self.far[1], role))
                if len(self._stream) >= STREAMS:
                    raise ConnectionGone("the connection to %s:%d already carries %d Roles" % (self.far[0], self.far[1], STREAMS))
                stream = self._stream[role] = len(self._stream)
        side = self._out.get(stream)
        if side is None:
            side = self._out[stream] = _Side(role)
        if not side.named:
            s = self._setting(role) or {}
            side.rekey(int(s.get("switches") or 0), s, self._keys)
            side.word = DRIVERS[self._driver_of(s, role)][0]
            self._put(packet_id(0, stream, False), struct.pack("!I", side.n) + role.encode("utf-8"))
            side.named = True
            self._note()
            self._say("to %s:%d, sequence %d is %r: %s" % (self.far[0], self.far[1], stream, role, side.running()))
        return stream, side

    @staticmethod
    def _driver_of(s: dict, role: str) -> str:
        name = str((s or {}).get("driver") or "tcp")
        if name not in DRIVERS:
            raise ConnectionGone("the channel of %r names the protocol %r, which this machine has not registered" % (role, name))
        return name

    def _catch_up(self, stream: int, side: _Side) -> None:
        """Bring this end's sending for a Role to its setting: one call for a change of protocol, one for each
        encryption switch the owner has commanded since. Each call goes out the old way; what follows is new."""
        s = self._setting(side.role) or {}
        word = DRIVERS[self._driver_of(s, side.role)][0]
        if word != side.word:
            self._put(packet_id(0, stream, True), self._seal(side, packet_id(0, stream, True), SWITCH_PROTOCOL))
            self.calls["protocol"][0] += 1
            was, side.word, side.state = WORDS.get(side.word), word, {}
            self._note()
            self._say("to %s:%d for %r: SWITCH PROTOCOL sent; %s until here, %s from here"
                      % (self.far[0], self.far[1], side.role, was, WORDS[word]))
        want = int(s.get("switches") or 0)
        while side.n < want:
            self._put(packet_id(0, stream, True), self._seal(side, packet_id(0, stream, True), SWITCH_ENCRYPTION))
            self.calls["encryption"][0] += 1
            side.rekey(side.n + 1, s, self._keys)
            self._note()
            self._say("to %s:%d for %r: SWITCH ENCRYPTION sent; now %s" % (self.far[0], self.far[1], side.role, side.running()))

    # ---- what any thread may call: it hands the writer something to do
    def submit(self, role: str, message: bytes, on_id: Optional[Callable] = None) -> None:
        """One request for a Role. The writer gives it the next counter of the Role's sequence and calls on_id(packet
        ID) before the frame is written, so whoever waits for the reply knows what it will come back under."""
        self._hand(("request", role, message, on_id))

    def submit_reply(self, role: str, stream: int, counter: int, message: bytes) -> None:
        """The reply to the request that arrived on this sequence with this counter."""
        self._hand(("reply", role, stream, counter, message))

    def setting_changed(self, role: Optional[str] = None) -> None:
        """The held read on a Role's setting woke this process: each sending side for that Role (every Role when none
        is given) puts its calls into the stream. (Receiving needs nothing from the setting: it follows the calls.)"""
        self._hand(("changed", role))

    def settle(self, wait_s: float = 30.0) -> None:
        """Returns when the writer has done everything handed to it before this call (a check's aid, and close's)."""
        done = threading.Event()
        self._hand(("mark", done))
        if not done.wait(wait_s) and self.broken is None:
            raise ConnectionGone("the writer of the connection to %s:%d did not come to the mark in %.0f s" % (self.far[0], self.far[1], wait_s))
        if self.broken is not None:
            raise ConnectionGone(self.broken)

    def _hand(self, item: tuple) -> None:
        if self.broken is not None:
            raise ConnectionGone(self.broken)
        self._todo.put(item)

    # ---- the writer: the one owner of this connection's wire
    def _write(self) -> None:
        try:
            while True:
                item = self._todo.get()
                kind = item[0]
                if kind == "stop":
                    return
                if kind == "mark":
                    item[1].set()
                    continue
                if kind == "changed":
                    for stream, side in list(self._out.items()):
                        if side.named and (item[1] is None or side.role == item[1]):
                            self._catch_up(stream, side)
                    continue
                if kind == "request":
                    _k, role, message, on_id = item
                    stream, side = self._out_side(role, None)
                    side.count += 1
                    counter, reply = side.count, False
                else:
                    _k, role, stream, counter, message = item
                    stream, side = self._out_side(role, stream)
                    reply, on_id = True, None
                self._catch_up(stream, side)
                pid = packet_id(counter, stream, reply)
                if on_id is not None:
                    on_id(pid)
                was = self.sent
                self._put(pid, self._seal(side, pid, side.word + DRIVERS[WORDS[side.word]][1](side.state, pid, message)))
                if self.on_sent is not None:
                    self.on_sent(role, reply, self.sent - was, WORDS[side.word])
        except ConnectionGone as e:
            self.broken = str(e)
        except Exception as e:
            self.broken = "%s: %r" % (type(e).__name__, e)
        while True:                                   # nothing handed to a writer that has stopped waits for ever
            try:
                item = self._todo.get_nowait()
            except queue.Empty:
                break
            if item[0] == "mark":
                item[1].set()
        if self.on_broken is not None:
            self.on_broken(self.broken)

    def _note(self) -> None:
        """What this end is sending under changed (a Role named, a call sent): kept for running(), and said."""
        self._running = {side.role: side.running() for side in self._out.values() if side.named}
        if self.on_running is not None:
            self.on_running()

    # ---- receiving
    def _rekey_in(self, side: _Side, n: int) -> None:
        """A receiving side at n switches, by the rule, under the setting this process holds as it stands. That
        setting may be behind the sender's; the first frame that does not open says so, and _open finds the pair."""
        try:
            side.rekey(n, self._setting(side.role), self._keys)
        except DoesNotOpen as e:
            self._say("from %s:%d for %r: %s; frames for it are opened by whichever key of the set opens them" % (self.far[0], self.far[1], side.role, e))
            side.n = n

    def _one(self, pid: int, body: bytes) -> None:
        """One frame taken in, on the one thread that reads this connection: a name, a call, or a message, which is
        delivered."""
        counter, stream, reply = id_parts(pid)
        if counter == 0 and not reply:                       # the first frame of a sequence, this direction
            if len(body) < 4:
                raise ConnectionGone("a sequence's first frame from %s:%d is %d bytes" % (self.far[0], self.far[1], len(body)))
            n, role = struct.unpack("!I", body[:4])[0], body[4:].decode("utf-8", "replace")
            side = self._in[stream] = _Side(role)
            side.named = True
            if not self.opener:
                self._stream.setdefault(role, stream)
            self._rekey_in(side, n)
            self._say("from %s:%d, sequence %d is %r, at %d switches%s" % (self.far[0], self.far[1], stream, role, n,
                      ", sealed with " + side.protection if side.key else ", not sealed"))
            return
        side = self._in.get(stream)
        if side is None:
            raise ConnectionGone("a frame from %s:%d on sequence %d, which nothing has named" % (self.far[0], self.far[1], stream))
        data = self._open(side, pid, body)
        if counter == 0:                                     # a call
            if data == SWITCH_PROTOCOL:
                self.calls["protocol"][1] += 1
                side.state = {}
                self._say("from %s:%d for %r: SWITCH PROTOCOL read" % (self.far[0], self.far[1], side.role))
            elif data == SWITCH_ENCRYPTION:
                self.calls["encryption"][1] += 1
                self._rekey_in(side, side.n + 1)
                self._say("from %s:%d for %r: SWITCH ENCRYPTION read; now at %d switches%s" % (self.far[0], self.far[1], side.role,
                          side.n, ", sealed with " + side.protection if side.key else ", not sealed"))
            else:
                raise ConnectionGone("a call from %s:%d for %r that is neither switch: %r" % (self.far[0], self.far[1], side.role, data[:8]))
            return
        word = bytes(data[:4])
        name = WORDS.get(word)
        if name is None:
            raise ConnectionGone("data from %s:%d for %r begins %r, which is no registered protocol's word: refused"
                                 % (self.far[0], self.far[1], side.role, word))
        if word != side.word:
            side.word, side.state = word, {}
        got = (side.role, stream, counter, reply, DRIVERS[name][2](side.state, pid, data[4:]))
        if self._deliver is not None:
            self._deliver(*got)
        else:
            self._ready.append(got)

    def receive(self) -> Tuple[str, int, int, bool, bytes]:
        """The next message: (the Role, its sequence, its counter, whether it is a reply, the message). Names and
        calls are taken on the way and not returned. For an end built without `deliver`."""
        while True:
            if self._ready:
                return self._ready.pop(0)
            self._one(*self._take())

    def pump(self) -> None:
        """Read the connection for as long as it lasts, delivering each message as it is opened. It ends by raising
        ConnectionGone with the reason."""
        while True:
            self._one(*self._take())

    def running(self) -> Dict[str, dict]:
        """What this end is sending under, for each Role it carries here: the report's content (no key, no seed)."""
        return dict(self._running)

    def close(self) -> None:
        self._todo.put(("stop",))
        s, self.sock = self.sock, None
        if s is not None:
            try:
                s.shutdown(2)
            except OSError:
                pass
            s.close()
