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

A frame that does not open ends the connection with the reason (no fallback): the threads with requests on it retry
with CACHE_MISS on the connection that replaces it, which starts from the setting's count.
"""
import hashlib
import os
import struct
import threading
import time
from typing import Callable, Dict, List, Optional, Tuple

from core import channel_protection as CP
from core.transport_driver import ConnectionGone

BUILD = "channel-frame-v1-20261121a"
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


_nonce_lock = threading.Lock()
_nonce_head, _nonce_count = os.urandom(8), 0


def _next_nonce() -> bytes:
    """12 bytes never used twice by this process under any key: 8 drawn when the process starts (and again when the
    count runs out), never stored, and a count."""
    global _nonce_head, _nonce_count
    with _nonce_lock:
        _nonce_count += 1
        if _nonce_count >= 1 << 32:
            _nonce_head, _nonce_count = os.urandom(8), 1
        return _nonce_head + struct.pack("!I", _nonce_count)


class _Side:
    """One Role, one direction of one connection: the protocol in use, the count of switches, the key in use."""
    __slots__ = ("role", "word", "n", "k", "key", "seed", "protection", "state", "named")

    def __init__(self, role: str):
        self.role, self.word, self.n, self.k, self.key = role, None, 0, None, None
        self.seed, self.protection, self.state, self.named = "", DEFAULT_PROTECTION, {}, False

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
    fresh     fresh(role) -> the same, read again now (used when a call takes a count past what is held)
    keys      keys(role) -> the Role's set on this machine
    """

    def __init__(self, sock, far: Tuple[str, int], opener: bool, setting: Callable, fresh: Optional[Callable] = None,
                 keys: Callable = key_set, say: Callable = None):
        self.sock, self.far, self.opener = sock, far, opener
        self._setting, self._fresh, self._keys = setting, fresh or setting, keys
        self._say = say or (lambda text: print("[CHANNEL-FRAME] %.6f %s" % (time.time(), text), flush=True))
        self._send_lock = threading.Lock()
        self._out: Dict[int, _Side] = {}
        self._in: Dict[int, _Side] = {}
        self._stream: Dict[str, int] = {}
        self._last_out = self._last_in = -1          # the sequence the last frame each way was on
        self._count_out: Dict[int, int] = {}         # a sequence's last counter, each way
        self._count_in: Dict[int, int] = {}
        self._rf = sock.makefile("rb", buffering=1 << 16)
        self.sent = self.received = 0                # bytes this end has put on, and taken off, the connection
        self.calls = {"protocol": [0, 0], "encryption": [0, 0]}      # [sent, received]

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
        nonce = _next_nonce()
        return nonce + CP.PROTECTIONS[side.protection][0](side.key, nonce, struct.pack("!Q", pid), data)

    def _open(self, side: _Side, pid: int, body: bytes) -> bytes:
        if side.key is None:
            return body
        if len(body) < 12 + CP.TAG:
            raise DoesNotOpen("a frame from %s:%d for %r is %d bytes, shorter than a sealed frame: the two ends do not "
                              "agree that this channel is sealed" % (self.far[0], self.far[1], side.role, len(body)))
        try:
            return CP.PROTECTIONS[side.protection][1](side.key, body[:12], struct.pack("!Q", pid), body[12:])
        except ValueError:
            raise DoesNotOpen("a frame from %s:%d for %r does not open under the current key (%s, after %d switches): "
                              "the two ends are not at the same place in the stream, or do not hold the same set"
                              % (self.far[0], self.far[1], side.role, side.protection, side.n))

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
            self._say("to %s:%d for %r: SWITCH PROTOCOL sent; %s until here, %s from here"
                      % (self.far[0], self.far[1], side.role, was, WORDS[word]))
        want = int(s.get("switches") or 0)
        while side.n < want:
            self._put(packet_id(0, stream, True), self._seal(side, packet_id(0, stream, True), SWITCH_ENCRYPTION))
            self.calls["encryption"][0] += 1
            side.rekey(side.n + 1, s, self._keys)
            self._say("to %s:%d for %r: SWITCH ENCRYPTION sent; now %s" % (self.far[0], self.far[1], side.role, side.running()))

    def send(self, role: str, counter: int, reply: bool, message: bytes, stream: Optional[int] = None) -> int:
        """One message for a Role. counter: the request's number in the Role's sequence, 1 or more (a reply gives its
        request's, and the sequence the request arrived on). Returns the packet ID it went under."""
        if counter < 1:
            raise ValueError("a message's counter is 1 or more")
        with self._send_lock:
            stream, side = self._out_side(role, stream)
            self._catch_up(stream, side)
            pid = packet_id(counter, stream, reply)
            self._put(pid, self._seal(side, pid, side.word + DRIVERS[WORDS[side.word]][1](side.state, pid, message)))
            return pid

    def setting_changed(self, role: Optional[str] = None) -> None:
        """The held read on a Role's setting woke this process: each sending side for that Role (every Role when none
        is given) puts its calls into the stream now."""
        with self._send_lock:
            for stream, side in list(self._out.items()):
                if side.named and (role is None or side.role == role):
                    self._catch_up(stream, side)

    # ---- receiving
    def receive(self) -> Tuple[str, int, int, bool, bytes]:
        """The next message: (the Role, its sequence, its counter, whether it is a reply, the message). Names and
        calls are taken on the way and not returned."""
        while True:
            pid, body = self._take()
            counter, stream, reply = id_parts(pid)
            if counter == 0 and not reply:                       # the first frame of a sequence, this direction
                if len(body) < 4:
                    raise ConnectionGone("a sequence's first frame from %s:%d is %d bytes" % (self.far[0], self.far[1], len(body)))
                n, role = struct.unpack("!I", body[:4])[0], body[4:].decode("utf-8", "replace")
                side = self._in[stream] = _Side(role)
                s = self._setting(role) or {}
                if int(s.get("switches") or 0) < n:
                    s = self._fresh(role) or {}
                side.rekey(n, s, self._keys)
                side.named = True
                if not self.opener:
                    self._stream.setdefault(role, stream)
                self._say("from %s:%d, sequence %d is %r, at %d switches%s" % (self.far[0], self.far[1], stream, role, n,
                          ", sealed with " + side.protection if side.key else ", not sealed"))
                continue
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
                    s = self._setting(side.role) or {}
                    if int(s.get("switches") or 0) < side.n + 1:
                        s = self._fresh(side.role) or {}
                    side.rekey(side.n + 1, s, self._keys)
                    self._say("from %s:%d for %r: SWITCH ENCRYPTION read; now at %d switches%s" % (self.far[0], self.far[1], side.role,
                              side.n, ", sealed with " + side.protection if side.key else ", not sealed"))
                else:
                    raise ConnectionGone("a call from %s:%d for %r that is neither switch: %r" % (self.far[0], self.far[1], side.role, data[:8]))
                continue
            word = bytes(data[:4])
            name = WORDS.get(word)
            if name is None:
                raise ConnectionGone("data from %s:%d for %r begins %r, which is no registered protocol's word: refused"
                                     % (self.far[0], self.far[1], side.role, word))
            if word != side.word:
                side.word, side.state = word, {}
            return side.role, stream, counter, reply, DRIVERS[name][2](side.state, pid, data[4:])

    def running(self) -> Dict[str, dict]:
        """What this end is sending under, for each Role it carries here: the report's content (no key, no seed)."""
        with self._send_lock:
            return {side.role: side.running() for side in self._out.values() if side.named}

    def close(self) -> None:
        s, self.sock = self.sock, None
        if s is not None:
            try:
                s.shutdown(2)
            except OSError:
                pass
            s.close()
