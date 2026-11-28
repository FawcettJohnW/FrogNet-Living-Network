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
  * [A_SWITCH_STATES_THE_SENDERS_NUMBER_V1] (v14, 28 Nov 2026.) A sender that switches its encryption for a Role then
    says, in the clear, the number its key is now computed from: the frame that names a sequence (counter 0, no reply
    bit), sent again. Until v14 that number was in the clear on a sequence's first frame only, and at a switch a
    receiver took it from its own copy of the Role's setting. That copy now comes through the Role's own channel
    ([A_ROLES_SETTING_IS_IN_THE_ROLES_OWN_RAM_V1], core/channel_setting.py), sealed with the very key it announces.
  * [THE_SECRET_IS_ADVANCED_UNTIL_ITS_KEY_OPENS_V1] A receiver whose setting is not on the stated number moves the
    secret it holds forward a step at a time, in memory, trying the key for the stated number under each registered
    algorithm, until a frame opens. John: "all we have to do is advance the existing secret until it has a key that
    matches the current one, and we're done. If we go too many generations (100?), it fails and must be reset by
    hand." "Reset means using the qr code again." The secret's step is not on the wire. Nothing is written to a secret's file from a frame.
  * A SWITCH is a frame like any other, sealed like any other, whose data is one word: SWP (switch protocol) or SWE
    (switch encryption). It says nothing of what from or to. It has counter 0 and the reply bit. Everything the
    sender puts on the connection for that Role after it is the new way, and every read after it is the new way.
    Each direction is its own stream: an end switches its sending when its held read on the setting wakes it
    (setting_changed), and its receiving when the call arrives.
  * The KEY ([THE_KEY_IS_COMPUTED_V1]): one for a Role in one direction of a connection, computed from the Role's
    secret on this machine, the sending node's FNID, the Role's name and the number in the Role's setting. The
    Admin sets that number, at random; a switch is "the number changed". No key is stored and none crosses a link.
    A setting with no number is not sealed.
  * A frame that does NOT OPEN under that one key is not tried under any other. It goes back for a retry: the
    receiver takes nothing more of that sequence, and says NOT OPENED (with the number of the first message it does
    not have) when its own setting changes or something of the sequence opens again; the sender then starts again
    from that message under its current key (AGAIN), from an empty protocol state. A sender keeps a copy of each
    message until the receiver has said RECEIVED for it (every 64 messages).
  * The length and the packet ID cross as small numbers: the length as a varint, the ID as what the far end does
    not already know of it (_put). The next request of the same Role spends one byte on its ID. (John, 2026-11-21: "All of the control should diff.")

  * (Withdrawn: [THE_KEY_THAT_OPENS_IS_THE_CURRENT_ONE_V1], John's ruling of 13:17 on 21 Nov, under which a frame
    that did not open was tried under every key of a stored set and every algorithm. John, 16:49 the same day: "The
    whole business about using old keys is bogus. There is only one key, and it is the active key, and it either
    works or it does not. No messing with new keys." A receiver still never reads the setting over the connection it
    is reading: _open looks only at the copy this process already holds.)

  * [A_CONNECTIONS_WIRE_HAS_ONE_OWNER_V1] John, 2026-11-21 13:23: "A connection's wire has one owner. One writer
    for each connection takes messages from the request threads and alone packs, sends switch calls, seals and
    writes. No request thread touches the socket." So there is no lock on sending: submit() and setting_changed()
    hand the connection's writer what there is to do, in the order they are called, and the writer does it. Reading
    has one owner as well: the thread in pump() (or the one calling receive()).

A frame that does not open ends the connection with the reason (no fallback): the threads with requests on it retry
with CACHE_MISS on the connection that replaces it, which starts from the setting's count.
"""
import hashlib
import hmac
import os
import queue
import struct
import threading
import time
from typing import Callable, Dict, List, Optional, Tuple

from core import channel_protection as CP
from core.transport_driver import ConnectionGone

BUILD = "channel-frame-v14-20261128a"
SECRETS = os.environ.get("FROGNET_CHANNEL_SECRETS", "/etc/frognet/channel_secrets")
SWITCH_PROTOCOL = b"SWP\x01"
SWITCH_ENCRYPTION = b"SWE\x01"
NOT_OPENED = b"NOT\x01"          # + a number: "your messages from this one on did not open here"
AGAIN = b"AGN\x01"               # + a number: "what follows starts again at this message, from an empty state"
RECEIVED = b"RCV\x01"            # + a number: "this many of your messages are in; keep no copy of them"
SAY_RECEIVED_EVERY = 64          # messages taken on a sequence between two RECEIVED calls
CALLS = (SWITCH_PROTOCOL, SWITCH_ENCRYPTION, NOT_OPENED, AGAIN, RECEIVED)
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
    if len(word) != 4 or word in CALLS or WORDS.get(word, name) != name:
        raise ValueError("the protocol %r cannot have the word %r" % (name, word))
    DRIVERS[name] = (word, pack, unpack)
    WORDS[word] = name


# TCP as a protocol under the frame (the design's 11.5): the message as it is. Its length and number are the frame's.
register_driver("tcp", b"TCP\x01", lambda state, pid, message: bytes(message), lambda state, pid, data: bytes(data))


class DoesNotOpen(ConnectionGone):
    pass


# ---------------------------------------------------------------------------------------------- the key
# [THE_KEY_IS_COMPUTED_V1] John, 2026-11-21 16:55 to 17:00: "not using pre-staged keys, but instead a pre-staged
# secret"; "a deterministic key that has a different value every time, from seeds I know"; "The count needs to be a
# random number set by the Admin, not a counter. It is not Is it one greater, it is just Did it change"; and 16:49:
# "There is only one key, and it is the active key, and it either works or it does not."
#   key = the first 32 bytes of HMAC-SHA384(the Role's secret on this machine, sender's FNID, the Role's name,
#         the number in its setting)      [KEY_AND_CHECK_USE_SHA_384_V1]
# One file is staged for a Role, offline: <secrets>/<role>. Nothing else is stored and no key crosses a link. The
# FNID is the SENDING node's (/etc/fnid), so each node sends a Role's traffic under its own key; a receiver learns it
# from the first frame of the Role's sequence. A number of 0, or none, means the Role is not sealed.
class NotOpened(Exception):
    """A frame did not open under the one key this end holds for it. It goes back for a retry (Link._lost)."""


DEFAULT_SECRET = "default"                    # the network's secret: the file every machine of the network holds
STEPPED = b"FROGNET ROLE SECRET AT STEP "   # the first line of a Role's own secret once it has moved forward


class SecretStepBehind(DoesNotOpen):
    """A setting names a step lower than the one this machine's secret for the Role is at."""


def read_secret(role: str, secrets_dir: Optional[str] = None) -> Tuple[bytes, int, bool]:
    """(the secret, the step it is at, whether it is the Role's own) for a Role on this machine.

    [A_DEFAULT_SECRET_AND_A_ROLES_OWN_V1] John, 22 Nov 2026: "one protocol and key for the machine default, with
    role-specific overrides possible." A Role with a file of its own in the secrets directory uses it; every other
    Role, and traffic of no Role, uses the file `default`, the network's.

    [A_ROLES_OWN_SECRET_MOVES_FORWARD_V1] John, 22 Nov 2026 23:48. A Role's own file as it was placed by hand is the
    secret at step 0: any bytes, at least 32. Once it has moved it is one line, STEPPED and the step in decimal, then
    the 48 bytes of the secret at that step. The default secret never moves: it is always at step 0."""
    # [A_SECRET_IS_NOT_READ_WHILE_IT_IS_BEING_REPLACED_V1] keep_secret renames the new file onto the Role's name and
    # then overwrites the bytes of the one it replaced. A process that had opened the old file just before would read
    # those zeros as a secret at step 0, fail a valid setting's check with it, and ignore the setting (seen between a
    # machine's proxy and its daemon in the simulator, 23 Nov 2026: 24 wrong reads of 2,873 when measured alone). So
    # a read holds the directory shared for as long as it has the file open, and keep_secret holds it alone.
    d = secrets_dir or SECRETS
    import fcntl
    try:
        dfd = os.open(d, os.O_RDONLY)
    except OSError as e:
        raise DoesNotOpen("this machine holds no secret for %r and no default secret (%s): it does not carry that "
                          "Role's sealed traffic" % (role, e))
    try:
        fcntl.flock(dfd, fcntl.LOCK_SH)
        return _read_secret(role, d)
    finally:
        os.close(dfd)


def _read_secret(role: str, d: str) -> Tuple[bytes, int, bool]:
    own = bool(role) and role != DEFAULT_SECRET and os.path.isfile(os.path.join(d, role))
    path = os.path.join(d, role if own else DEFAULT_SECRET)
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except OSError as e:
        raise DoesNotOpen("this machine holds no secret for %r and no default secret (%s): it does not carry that "
                          "Role's sealed traffic" % (role, e))
    step = 0
    if own and raw.startswith(STEPPED):
        line, _, rest = raw.partition(b"\n")
        try:
            step = int(line[len(STEPPED):])
        except ValueError:
            raise DoesNotOpen("the secret for %r in %s does not say its step (%r)" % (role, path, line[:60]))
        raw = rest
    if len(raw) < 32:
        raise DoesNotOpen("the secret for %r in %s is %d bytes; a secret is at least 32" % (role, path, len(raw)))
    return raw, step, own


def secret_of(role: str, secrets_dir: Optional[str] = None) -> bytes:
    """The secret this machine holds for a Role now: the Role's own at the step its file is at, or the default."""
    return read_secret(role, secrets_dir)[0]


def next_secret(secret: bytes, role: str, step: int) -> bytes:
    """[A_ROLES_OWN_SECRET_MOVES_FORWARD_V1] secret(s) = HMAC-SHA384(secret(s-1), "next", the Role's name, s).
    One way: secret(s-1) cannot be worked out from secret(s)."""
    return hmac.new(secret, b"next\n" + role.encode("utf-8") + b"\n" + str(int(step)).encode("ascii"),
                    hashlib.sha384).digest()


def secret_at(role: str, step: Optional[int], secrets_dir: Optional[str] = None) -> Tuple[bytes, int, bool]:
    """(the Role's secret at `step`, the step this machine's file is at, whether the secret is the Role's own),
    worked out IN MEMORY: the file is read and not written. A machine that missed switches repeats next_secret once
    for each step it missed. A step lower than the file's cannot be made (SecretStepBehind): the earlier secret was
    erased, and that is the point. The default secret has no steps: `step` is not looked at for it."""
    secret, at, own = read_secret(role, secrets_dir)
    if not own or step is None:
        return secret, at, own
    step = int(step)
    if step < at:
        raise SecretStepBehind("step %d is lower than the step this machine's secret for %r is at (%d)" % (step, role, at))
    for s in range(at + 1, step + 1):
        secret = next_secret(secret, role, s)
    return secret, at, own


def keep_secret(role: str, secret: bytes, step: int, secrets_dir: Optional[str] = None) -> bool:
    """[A_ROLES_OWN_SECRET_MOVES_FORWARD_V1] Write the Role's secret at `step` over the Role's file, whole (written
    beside it and renamed onto it), then erase the secret it replaced: the bytes of the file that was there are
    overwritten before its last handle is closed. Every process of a machine that sees the step does this; the first
    one writes, and the rest find it done. Returns whether this call wrote."""
    import fcntl
    d = secrets_dir or SECRETS
    path = os.path.join(d, role)
    tmp = "%s.next.%d" % (path, os.getpid())
    dfd = os.open(d, os.O_RDONLY)
    old = None
    try:
        # The daemon, the proxy and an Admin's command on one machine all see the same step, each in its own process.
        # Reading the file's step and replacing the file is one act among them, so a process that is a step behind
        # never puts an earlier secret back over a later one. (This is a file on one machine, not the wire.)
        fcntl.flock(dfd, fcntl.LOCK_EX)
        if _read_secret(role, d)[1] >= int(step):
            return False                            # another process of this machine has already moved it there
        old = os.open(path, os.O_RDWR)              # the file being replaced: held open so it can be erased after
        size = os.fstat(old).st_size
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, STEPPED + str(int(step)).encode("ascii") + b"\n" + secret)
            os.fsync(fd)
        finally:
            os.close(fd)
        os.rename(tmp, path)
        os.lseek(old, 0, os.SEEK_SET)
        os.write(old, b"\0" * size)
        os.fsync(old)
        os.fsync(dfd)
        return True
    finally:
        if old is not None:
            os.close(old)
        os.close(dfd)                               # (closing it releases the hold on the directory)


_FNID: List[str] = []


def fnid_here() -> str:
    """This node's FNID: the content of /etc/fnid (FROGNET_GUID_FILE names another file, as it does for the node's
    own scripts)."""
    if not _FNID:
        path = os.environ.get("FROGNET_GUID_FILE", "/etc/fnid")
        try:
            with open(path, "r") as f:
                fnid = f.read().strip()
        except OSError as e:
            raise DoesNotOpen("this node has no FNID (%s): it cannot compute the key it sends a sealed Role under" % e)
        if not fnid or len(fnid.encode("utf-8")) > 255:
            raise DoesNotOpen("the FNID in %s is %d bytes; it is 1 to 255" % (path, len(fnid.encode("utf-8"))))
        _FNID.append(fnid)
    return _FNID[0]


def key_for(secret: bytes, fnid: str, role: str, number: int) -> bytes:
    """The one key a node sends a Role's traffic under while the Role's setting holds this number.
    [KEY_AND_CHECK_USE_SHA_384_V1] John, 22 Nov 2026 13:58: the first 32 bytes of HMAC-SHA384 (48 bytes) over the
    sender's FNID, the Role's name and the number, keyed with the secret. Until then it was HMAC-SHA256."""
    return hmac.new(secret, b"frognet channel key\n" + fnid.encode("utf-8") + b"\n" + role.encode("utf-8") + b"\n"
                    + str(int(number)).encode("ascii"), hashlib.sha384).digest()[:32]


def secret_fingerprint(secret: bytes) -> str:
    """Eight hex digits that are the same wherever the same secret is: for comparing two nodes by eye (the monitor
    shows them, usr/local/bin/frognet_monitor_py/channel.py, which has this same computation). They say nothing of
    the secret, and this is not the computation any key or check is made by."""
    return hmac.new(secret, b"frognet channel secret fingerprint", hashlib.sha256).hexdigest()[:8]


def wanted(setting: Optional[dict]) -> Tuple[int, str]:
    """(the number, the algorithm) a setting says. The number is 0 when the Role is not sealed."""
    s = setting or {}
    try:
        number = int(s.get("number") or 0)
    except (TypeError, ValueError):
        number = 0
    return number, str(s.get("protection") or DEFAULT_PROTECTION)


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
    """One Role, one direction of one connection: the protocol in use, the number its key was computed from, the key."""
    __slots__ = ("role", "word", "number", "key", "protection", "state", "named", "count", "fnid", "owed", "unsure", "step", "lost",
                 "got", "skip", "kept", "base", "since")

    def __init__(self, role: str, fnid: str = ""):
        self.role, self.word, self.number, self.key = role, None, 0, None
        self.protection, self.state, self.named = DEFAULT_PROTECTION, {}, False
        self.fnid = fnid                # the SENDING node's FNID: this node's on a sending side, the far node's on a receiving one
        self.count = 0                  # sending: the last counter of this Role's sequence of requests
        self.owed = False               # receiving: a switch call was read and this end's setting had not changed yet
        self.unsure = False             # receiving: the sender stated its number and nothing has opened under it yet: a frame that does not open is searched for
        self.step = None                # sending: the step of the Role's secret this side's key was computed at
        self.lost = False               # receiving: a frame did not open; nothing is taken until the sender starts again
        self.got = 0                    # receiving: messages taken, in order
        self.skip = 0                   # receiving: messages the sender is sending again that this end already took
        self.kept: List[tuple] = []     # sending: (packet ID, reply, message) not yet said to be received
        self.base = 0                   # sending: how many messages came before kept[0]
        self.since = 0                  # receiving: messages taken since this end last said how many it has

    def rekey(self, number: int, protection: str, secret: Callable) -> None:
        """The key for this number, under this algorithm. Nothing of the key before is kept."""
        if protection not in CP.PROTECTIONS:
            raise DoesNotOpen("the channel of %r names the algorithm %r, which this machine has not registered"
                              % (self.role, protection))
        self.protection, self.number = protection, int(number)
        self.key = key_for(secret(self.role), self.fnid, self.role, self.number) if self.number else None

    def running(self) -> dict:
        return {"driver": WORDS.get(self.word, ""), "number": self.number, "protection": self.protection if self.key else "",
                "sealed": bool(self.key)}


class Link:
    """One end of one permanently open connection (the module's text).

    sock      a connected stream socket
    opener    True at the end that opened the connection: it gives each Role its sequence of packet IDs
    setting   setting(role) -> that Role's channel setting as this process holds it: {driver, number, protection,
              switches}, or None. role "" is traffic of no Role.
    deliver   deliver(role, sequence, counter, reply, message), called for each message in the order it is opened,
              by pump() and by setting_changed(). Without it, receive() returns them one at a time.
    secret    secret(role) -> the Role's secret on this machine
    fnid      this node's FNID (read from the node's file when not given)
    """

    def __init__(self, sock, far: Tuple[str, int], opener: bool, setting: Callable, deliver: Optional[Callable] = None,
                 secret: Callable = secret_of, say: Callable = None, fnid: Optional[str] = None, step: Optional[Callable] = None,
                 learned: Optional[Callable] = None):
        self.sock, self.far, self.opener = sock, far, opener
        self._setting, self._deliver, self._secret, self._fnid = setting, deliver, secret, fnid
        self._learned = learned                      # told (role, number, algorithm, step, secret) when a search opens a frame
        self._step = step or (lambda role: None)     # the step the secret `secret(role)` gives is at; None for a Role on the network's secret
        self._ready: List[tuple] = []                # messages opened and not yet returned by receive()
        self._say = say or (lambda text: print("[CHANNEL-FRAME] %.6f %s" % (time.time(), text), flush=True))
        self._todo: "queue.SimpleQueue" = queue.SimpleQueue()      # what the writer is handed, in order
        self._running: Dict[str, dict] = {}          # what this end is sending under, as the writer last left it
        self.broken: Optional[str] = None            # why the writer stopped, once it has
        self.wire_failed = False                     # True when it stopped because a write to the connection failed
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
        self.calls = {"protocol": [0, 0], "encryption": [0, 0], "not opened": [0, 0], "again": [0, 0], "received": [0, 0]}   # [sent, received]
        self.not_opened = 0                          # frames that did not open here and went back for a retry
        self.sent_again = 0                          # messages this end sent a second time because the far end said so
        self._parked: Dict[int, int] = {}            # the writer's: a receiving sequence that is lost -> its first message not taken
        self._asked: set = set()                     # the writer's: lost sequences it has said NOT OPENED for since they were lost
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
            self.wire_failed = True                  # the connection failed under the writer; the writer did not
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
        return bytes(data[:4]) in CALLS or bytes(data[:4]) in WORDS

    def _open(self, side: _Side, pid: int, body: bytes) -> bytes:
        """The data of a frame, under the one key and algorithm this side holds. There is no other key to try
        ([THE_KEY_IS_COMPUTED_V1]). The one thing this end may do first is take up its own setting, when a switch
        call has already told it the sender moved (owed) or it has already lost a frame: then it knows the key it
        holds is not the current one."""
        for attempt in (0, 1):
            if side.key is None:
                if self._plain(body):
                    return body
            elif len(body) >= 12 + CP.TAG:
                try:
                    data = CP.PROTECTIONS[side.protection][1](side.key, body[:12], struct.pack("!Q", pid), body[12:])
                    side.unsure = False
                    return data
                except ValueError:
                    pass
                if side.unsure:
                    data = self._search(side, pid, body)
                    if data is not None:
                        return data
            if attempt or not (side.owed or side.lost):
                break
            number, protection = wanted(self._setting(side.role))
            if (number, protection if number else "") == (side.number, side.protection if side.number else ""):
                break
            self._rekey_in(side, number, protection)           # this end's setting has arrived since
        raise NotOpened("not under the key for the number this end holds for it (%s)"
                        % ("sealed with " + side.protection if side.key else "not sealed"))

    # ---- sending
    def _mine(self) -> str:
        if self._fnid is None:
            self._fnid = fnid_here()
        return self._fnid

    @staticmethod
    def _stated(side: _Side) -> bytes:
        """What a sender says of a Role's sequence in the clear: the number its key is computed from, its node's FNID
        and the Role's name. Said on the sequence's first frame and again each time the sender switches its encryption
        ([A_SWITCH_STATES_THE_SENDERS_NUMBER_V1]). Nothing of the secret, and not its step."""
        fnid = side.fnid.encode("utf-8")
        return struct.pack("!QB", side.number, len(fnid)) + fnid + side.role.encode("utf-8")

    def _out_side(self, role: str, stream: Optional[int]) -> Tuple[int, _Side]:
        """This end's sending side for a Role, named on the connection before its first frame: the number its key is
        computed from, this node's FNID, the Role's name."""
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
            number, protection = wanted(s)
            # [A_SEQUENCE_NAMED_UNSEALED_SAYS_WHO_SENDS_IT_V1] Found on John's network, 23 Nov 2026, then between
            # machines: a sequence named while its Role was unsealed did not say the sender's FNID. The key is made
            # from that FNID, and the calls that switch encryption do not carry it, so once the Role was sealed the
            # two ends made different keys, nothing opened in either direction, and each said NOT OPENED to the other
            # for ever. The name always says who sends. (A node with no FNID file says none, and cannot seal.)
            try:
                side.fnid = self._mine()
            except DoesNotOpen:
                if number:
                    raise
                side.fnid = ""
            side.rekey(number, protection, self._secret)
            side.step = s.get("step") if number else None
            side.word = DRIVERS[self._driver_of(s, role)][0]
            self._put(packet_id(0, stream, False), self._stated(side))
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

    def _call(self, stream: int, side: _Side, data: bytes) -> None:
        pid = packet_id(0, stream, True)
        self._put(pid, self._seal(side, pid, data))

    def _catch_up(self, stream: int, side: _Side) -> None:
        """Bring this end's sending for a Role to its setting: one call for a change of protocol, one for a change of
        encryption (the number changed, or the algorithm). Each call goes out the old way; what follows is new. It
        does not matter how many times the owner changed the number since: it is "did it change"."""
        s = self._setting(side.role) or {}
        word = DRIVERS[self._driver_of(s, side.role)][0]
        if word != side.word:
            self._call(stream, side, SWITCH_PROTOCOL)
            self.calls["protocol"][0] += 1
            was, side.word, side.state = WORDS.get(side.word), word, {}
            self._note()
            self._say("to %s:%d for %r: SWITCH PROTOCOL sent; %s until here, %s from here"
                      % (self.far[0], self.far[1], side.role, was, WORDS[word]))
        number, protection = wanted(s)
        if number != side.number or (number and (protection != side.protection or s.get("step") != side.step)):
            self._call(stream, side, SWITCH_ENCRYPTION)
            self.calls["encryption"][0] += 1
            if number and not side.fnid:
                side.fnid = self._mine()
            side.rekey(number, protection, self._secret)
            side.step = s.get("step") if number else None    # (the setting's own word for it: no file is read per message)
            self._put(packet_id(0, stream, False), self._stated(side))    # [A_SWITCH_STATES_THE_SENDERS_NUMBER_V1]
            # The first thing under the new key says where this end is in its messages. A receiver that lost frames
            # before the switch reads it, sees it opens, and asks for them (NOT OPENED); one that lost none goes on.
            side.state = {}
            self._call(stream, side, AGAIN + _varint(side.base + len(side.kept)))
            self.calls["again"][0] += 1
            self._note()
            self._say("to %s:%d for %r: SWITCH ENCRYPTION sent; now %s" % (self.far[0], self.far[1], side.role, side.running()))

    # ---- what any thread may call: it hands the writer something to do
    def submit(self, role: str, message: bytes, on_id: Optional[Callable] = None) -> None:
        """One request for a Role. The writer gives it the next counter of the Role's sequence and calls on_id(packet
        ID) before the frame is written, so whoever waits for the reply knows what it will come back under."""
        self._hand(("request", role, message, on_id))

    def submit_on(self, stream: int, role: str, message: bytes) -> None:
        """[THE_FIRE_RIDES_THE_HOLDERS_CONNECTION_V1] A message this end sends of its own accord, on a sequence the far
        end has already named (`stream`, for `role`): a role host's daemon putting a held read's fire on the
        connection the holder's node opened. It answers nothing and nothing answers it."""
        self._hand(("request", role, message, None, stream))

    def named_to_this_end(self) -> Dict[int, str]:
        """The sequences the far end has named on this connection: {its number: the Role it is for}."""
        return {stream: side.role for stream, side in list(self._in.items())}

    def submit_reply(self, role: str, stream: int, counter: int, message: bytes) -> None:
        """The reply to the request that arrived on this sequence with this counter."""
        self._hand(("reply", role, stream, counter, message))

    def setting_changed(self, role: Optional[str] = None) -> None:
        """The held read on a Role's setting woke this process: each sending side for that Role (every Role when none
        is given) puts its calls into the stream, and each receiving side for it that has lost frames asks for them."""
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
    def _message(self, stream: int, side: _Side, pid: int, reply: bool, message: bytes) -> None:
        was = self.sent
        self._put(pid, self._seal(side, pid, side.word + DRIVERS[WORDS[side.word]][1](side.state, pid, message)))
        if self.on_sent is not None:
            self.on_sent(side.role, reply, self.sent - was, WORDS[side.word])

    def _ask_again(self, stream: int) -> None:
        """Say NOT OPENED for a receiving sequence that is lost: on this end's own sending side of that sequence."""
        first = self._parked.get(stream)
        there = self._in.get(stream)
        if first is None or there is None:
            return
        there.unsure = bool(there.number)    # what comes again is tried against the secret this machine holds NOW, and the steps after it
        stream, side = self._out_side(there.role, stream)
        self._catch_up(stream, side)
        self._call(stream, side, NOT_OPENED + _varint(first))
        self._asked.add(stream)
        self.calls["not opened"][0] += 1
        self._say("to %s:%d for %r: NOT OPENED sent: its messages from number %d on did not open here; send them again"
                  % (self.far[0], self.far[1], there.role, first))

    def _send_again(self, stream: int, first: int) -> None:
        """The far end said NOT OPENED from `first`: start again there, from an empty state, under the current key."""
        side = self._out.get(stream)
        if side is None or not side.named:
            return
        if first < side.base or first > side.base + len(side.kept):
            raise ConnectionGone("%s:%d asks for %r's messages again from number %d and this end holds %d to %d"
                                 % (self.far[0], self.far[1], side.role, first, side.base, side.base + len(side.kept)))
        self._catch_up(stream, side)
        side.state = {}
        self._call(stream, side, AGAIN + _varint(first))
        self.calls["again"][0] += 1
        again = side.kept[first - side.base:]
        for pid, reply, message in again:
            self._message(stream, side, pid, reply, message)
        self.sent_again += len(again)
        self._say("to %s:%d for %r: %d message(s) sent again, from number %d, under %s"
                  % (self.far[0], self.far[1], side.role, len(again), first, side.running()))

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
                    for stream in list(self._parked):          # this end's setting arrived: what did not open may now
                        there = self._in.get(stream)
                        if there is not None and (item[1] is None or there.role == item[1]):
                            self._ask_again(stream)
                    continue
                if kind == "not opened":                       # from the reader: a receiving sequence is lost from here
                    self._parked.setdefault(item[1], item[2])
                    continue
                if kind == "in step":                          # from the reader: something of a lost sequence opened
                    # A message that opens is asked on once (those already on their way open too). The sender's own
                    # word that it is past what this end has (AGAIN, after its switch) is always asked on: it has
                    # not started again from what this end lacks.
                    if item[2] or item[1] not in self._asked:
                        self._ask_again(item[1])
                    continue
                if kind == "taken up":                         # from the reader: the sender started again and it is in
                    self._parked.pop(item[1], None)
                    self._asked.discard(item[1])
                    continue
                if kind == "again":                            # from the reader: the far end said NOT OPENED
                    self._send_again(item[1], item[2])
                    continue
                if kind == "they have":                        # from the reader: the far end said RECEIVED
                    side = self._out.get(item[1])
                    if side is not None and item[2] > side.base:
                        del side.kept[:item[2] - side.base]
                        side.base = item[2]
                    continue
                if kind == "we have":                          # from the reader: say RECEIVED
                    there = self._in.get(item[1])
                    if there is not None:
                        stream, side = self._out_side(there.role, item[1])
                        self._catch_up(stream, side)
                        self._call(stream, side, RECEIVED + _varint(item[2]))
                        self.calls["received"][0] += 1
                    continue
                if kind == "request":
                    _k, role, message, on_id = item[:4]
                    stream, side = self._out_side(role, item[4] if len(item) > 4 else None)
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
                side.kept.append((pid, reply, message))       # until the far end says it has it
                self._message(stream, side, pid, reply, message)
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
    MAX_STEPS_AHEAD = 100                # John, 28 Nov 2026: "If we go too many generations (100?), it fails and must be reset by hand"

    def _rekey_stated(self, side: _Side, number: int) -> None:
        """[A_SWITCH_STATES_THE_SENDERS_NUMBER_V1] The sender stated the number its key is computed from. This end's key is
        that number under the secret and algorithm it holds. When its setting is not on that number, the sender may
        also be on a later step of the Role's secret or on the other algorithm: the next frame finds out (_search)."""
        side.owed = False
        held_number, protection = wanted(self._setting(side.role))
        side.unsure = bool(number)       # (the search runs only if a frame does not open under the key computed here)
        if protection not in CP.PROTECTIONS:
            protection = DEFAULT_PROTECTION
        side.protection, side.number = protection, int(number)
        try:
            side.key = key_for(self._secret(side.role), side.fnid, side.role, side.number) if side.number else None
        except DoesNotOpen as e:
            self._say("from %s:%d for %r: %s; its frames do not open here" % (self.far[0], self.far[1], side.role, e))
            side.key = None

    def _search(self, side: _Side, pid: int, body: bytes) -> Optional[bytes]:
        """[THE_SECRET_IS_ADVANCED_UNTIL_ITS_KEY_OPENS_V1] John, 28 Nov 2026: "all we have to do is advance the existing
        secret until it has a key that matches the current one, and we're done. If we go too many generations (100?),
        it fails and must be reset by hand." The secret this end holds is moved forward one step at a time, IN MEMORY,
        and at each step the key for the stated number is tried on this frame under each registered algorithm. The one
        that opens is the sender's. Nothing is written to the secret's file here: the setting, which arrives through
        the channel this opens, does that (core/channel_setting.py). A Role on the network's secret has no steps:
        only the algorithms are tried. None opened in MAX_STEPS_AHEAD steps: said once, and nothing opens until the
        Role's secret is set again on this machine. John, 28 Nov 2026: "Reset means using the qr code again."""
        try:
            secret, at = self._secret(side.role), self._step(side.role)     # at None: a Role on the network's secret
        except (DoesNotOpen, OSError) as e:
            self._say("from %s:%d for %r: %s; its frames do not open here" % (self.far[0], self.far[1], side.role, e))
            side.unsure = False
            return None
        aad = struct.pack("!Q", pid)
        for ahead in range(0, (self.MAX_STEPS_AHEAD if at is not None else 0) + 1):
            if ahead:
                secret = next_secret(secret, side.role, at + ahead)
            key = key_for(secret, side.fnid, side.role, side.number)
            for name in sorted(CP.PROTECTIONS):
                try:
                    data = CP.PROTECTIONS[name][1](key, body[:12], aad, body[12:])
                except ValueError:
                    continue
                side.key, side.protection, side.unsure = key, name, False
                if self._learned is not None:            # [AN_END_THAT_HAS_JUST_STARTED_LEARNS_THE_NUMBER_V1] (core/channel_setting.py)
                    self._learned(side.role, side.number, name, (at + ahead) if at is not None else None, secret if at is not None else None)
                self._say("from %s:%d for %r: the sender's number %d opens here with this end's secret moved forward %d step(s), %s"
                          % (self.far[0], self.far[1], side.role, side.number, ahead, name))
                return data
        side.unsure = False
        self._say("from %s:%d for %r: NOTHING OPENS under the sender's number %d with this machine's secret or any of the %d steps after it. "
                  "This machine's secret for the Role must be set again: take it again from the FrogNet QR code" % (self.far[0], self.far[1], side.role, side.number, self.MAX_STEPS_AHEAD))
        return None

    def _rekey_in(self, side: _Side, number: int, protection: str) -> None:
        side.owed = False
        try:
            side.rekey(number, protection, self._secret)
        except DoesNotOpen as e:
            self._say("from %s:%d for %r: %s; its frames do not open here" % (self.far[0], self.far[1], side.role, e))
            side.number, side.key = number, None

    def _lost(self, side: _Side, stream: int, why: str) -> None:
        """A frame did not open. It is not taken, and nothing after it on this sequence is, until the sender starts
        again from the first message this end does not have. The writer says NOT OPENED when there is a reason to
        think a retry will open: this end's setting changes, or something of the sequence opens again."""
        self.not_opened += 1
        if not side.lost:
            side.lost, side.state = True, {}
            self._say("from %s:%d for %r: a frame did not open (%s). It goes back for a retry: nothing of this sequence "
                      "is taken from message number %d until the sender starts again"
                      % (self.far[0], self.far[1], side.role, why, side.got))
        self._todo.put(("not opened", stream, side.got))

    @staticmethod
    def _number(data: bytes) -> int:
        z, shift = 0, 0
        for c in data:
            z |= (c & 0x7f) << shift
            shift += 7
            if not c & 0x80:
                return z
        raise ConnectionGone("a call's number does not end")

    def _one(self, pid: int, body: bytes) -> None:
        """One frame taken in, on the one thread that reads this connection: a name, a call, or a message, which is
        delivered."""
        counter, stream, reply = id_parts(pid)
        if counter == 0 and not reply:                       # the first frame of a sequence, this direction
            if len(body) < 9 or len(body) < 9 + body[8]:
                raise ConnectionGone("a sequence's first frame from %s:%d is %d bytes" % (self.far[0], self.far[1], len(body)))
            number, k = struct.unpack("!QB", body[:9])
            fnid, role = body[9:9 + k].decode("utf-8", "replace"), body[9 + k:].decode("utf-8", "replace")
            side = self._in.get(stream)
            if side is None or side.role != role or side.fnid != fnid:
                side = self._in[stream] = _Side(role, fnid)      # the sequence's first frame
            # (else [A_SWITCH_STATES_THE_SENDERS_NUMBER_V1]: the sender switched and says the number it is on now; the
            # sequence goes on, with what this end has taken of it)
            side.named = True
            if not self.opener:
                self._stream.setdefault(role, stream)
            self._rekey_stated(side, number)
            self._say("from %s:%d, sequence %d is %r, from node %s%s" % (self.far[0], self.far[1], stream, role, fnid or "(not said)",
                      ", sealed with " + side.protection if side.key else ", not sealed"))
            return
        side = self._in.get(stream)
        if side is None:
            raise ConnectionGone("a frame from %s:%d on sequence %d, which nothing has named" % (self.far[0], self.far[1], stream))
        try:
            data = self._open(side, pid, body)
        except NotOpened as e:
            if not side.number and not side.owed and not wanted(self._setting(side.role))[0]:
                raise ConnectionGone("data from %s:%d for %r, which is not sealed, begins %r: no registered protocol's word"
                                     % (self.far[0], self.far[1], side.role, bytes(body[:4])))
            self._lost(side, stream, str(e))
            return
        if counter == 0:                                     # a call
            call = bytes(data[:4])
            if call == SWITCH_PROTOCOL and len(data) == 4:
                self.calls["protocol"][1] += 1
                side.state = {}
                self._say("from %s:%d for %r: SWITCH PROTOCOL read" % (self.far[0], self.far[1], side.role))
            elif call == SWITCH_ENCRYPTION and len(data) == 4:
                self.calls["encryption"][1] += 1
                number, protection = wanted(self._setting(side.role))
                if number != side.number or (number and protection != side.protection):
                    self._rekey_in(side, number, protection)
                    self._say("from %s:%d for %r: SWITCH ENCRYPTION read; this end's setting says %s" % (self.far[0], self.far[1], side.role,
                              "sealed with " + side.protection if side.key else "not sealed"))
                else:
                    side.owed = True                         # the sender's setting changed and this end's has not yet
                    self._say("from %s:%d for %r: SWITCH ENCRYPTION read before this end's setting changed; what follows "
                              "goes back for a retry until it has" % (self.far[0], self.far[1], side.role))
            elif call == AGAIN:
                self.calls["again"][1] += 1
                first = self._number(data[4:])
                side.state = {}
                if first > side.got:                         # the sender is past what this end has: it is only saying where it is
                    if side.lost:
                        self._todo.put(("in step", stream, True))
                elif side.lost or first < side.got:
                    side.lost, side.skip = False, side.got - first
                    self._todo.put(("taken up", stream))
                    self._say("from %s:%d for %r: the sender starts again from message number %d; this end has %d"
                              % (self.far[0], self.far[1], side.role, first, side.got))
            elif call == NOT_OPENED:
                self.calls["not opened"][1] += 1
                self._todo.put(("again", stream, self._number(data[4:])))
            elif call == RECEIVED:
                self.calls["received"][1] += 1
                self._todo.put(("they have", stream, self._number(data[4:])))
            else:
                raise ConnectionGone("a call from %s:%d for %r that this end does not know: %r" % (self.far[0], self.far[1], side.role, data[:8]))
            return
        if side.lost:                                        # it opens, and it is from before the sender started again
            self._todo.put(("in step", stream, False))
            return
        word = bytes(data[:4])
        name = WORDS.get(word)
        if name is None:
            raise ConnectionGone("data from %s:%d for %r begins %r, which is no registered protocol's word: refused"
                                 % (self.far[0], self.far[1], side.role, word))
        if word != side.word:
            side.word, side.state = word, {}
        message = DRIVERS[name][2](side.state, pid, data[4:])
        if side.skip:                                        # sent again, and this end already had it
            side.skip -= 1
            return
        side.got += 1
        side.since += 1
        if side.since >= SAY_RECEIVED_EVERY:
            side.since = 0
            self._todo.put(("we have", stream, side.got))
        got = (side.role, stream, counter, reply, message)
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
