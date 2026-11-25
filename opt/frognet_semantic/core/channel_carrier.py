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
"""core/channel_carrier.py -- [ONE_CONNECTION_PER_DESTINATION_V1] the semantic wire's message carrier on core/channel_frame.py.

The design's 10.5 (walkthrough, version 0.3): "A process holds one connection to each destination, whatever protocols
are in use on it." This is that process's carrier: the calls the Homa driver's two ends already make on a carrier
(proxy/transport_homa.py, daemon/engine/server_homa.py: send_request, send_reply, recv, abort, peer_gone, counters,
close), with a channel_frame.Link under each destination instead of a connection for each memory and each driver.

  * A request is for a Role (`ram`: the name core/channel_setting.ram_of gives a request, "" for traffic of no Role).
    The Role's setting, (channel, setting, <Role>), says the protocol and seeds the encryption; the Link applies it.
    Nothing here, and nothing above, names a protocol or a key.
  * When this process's held read on the settings wakes it (channel_setting.on_change), every Link puts its calls
    into its stream: "switch protocol", "switch encryption". No connection is closed and no session is moved.
  * What this end is running for each Role is this end's report, (channel, at, ...), which channel_setting writes
    (channel_setting.RUNNING is this carrier's running()).
  * The end that opens a connection says WORD first, so the daemon's port, which takes every kind of connection,
    knows this one (daemon/engine/server.py looks at the first four bytes and hands it to adopt()).

A connection that ends is gone: every request outstanding on it ends with it, and it is opened again when there is
next something to send. The threads that had requests on it retry.
"""
import errno
import os
import queue
import socket
import struct
import threading
import time
from typing import Dict, Optional, Tuple

from core import channel_frame as F
from core.homa_socket import Msg, RpcFailed, CarrierClosed, HomaUnavailable, PeerConnectionEnded
from core.transport_driver import ConnectionGone

BUILD = "channel-carrier-v8-20261123g"
WORD = b"FRM\x01"                     # what the end that opens a connection says first
DEFAULT_DRIVER = "chorus"            # the protocol of a Role (or of no Role) whose setting names none: this carrier's own
SERVER = None                        # this process's daemon end, when it has one: adopt() is its call
SERVER_UP = threading.Event()        # set when SERVER exists (core/homa_transport.frame_end_for waits on it)
# [THE_FRAME_RIDES_THE_WIRE_SOCKET_V1] John, 23 Nov 2026 08:40: "Why are we sending the protocol for Chorus over a
# separate socket? It should be multiplexed onto the wire socket." 08:42: "What goes on the physical wire is a TCP data
# packet. What is in that packet is mine. The packet is created by the protocol handler to be placed on the wire. On
# the other side, the sniffer says this is a Chorus packet and ships the frame to the Chorus handler."
# A node that was not told chorus has a wire session to each daemon (the TCP driver's). This layer's packets ride it:
# toward the daemon as a packet that begins WIRE_TAG, which the daemon's reader sniffs and hands here; toward the node
# as a packet numbered WIRE_SEQ on the return channel, as a held read's fire already rides it. No connection is opened
# for the frame. Every other packet on that socket is byte for byte what it was.
WIRE_TAG = b"FRM\x02"
WIRE_SEQ = 0xFFFFFFFD
_pipe_n = [0]


class WirePipe:
    """What core/channel_frame.Link asks of a socket, on a wire session. put(bytes) places one of this layer's packets
    on the session's wire; feed(bytes) is the session's reader handing one over. When the session ends, so does this."""

    def __init__(self, put, who: str):
        self._put, self.who = put, who
        self._buf, self._cv, self.closed = bytearray(), threading.Condition(), False

    def sendall(self, data) -> None:
        if self.closed:
            raise OSError("the wire session to %s that carried it has ended" % self.who)
        self._put(bytes(data))

    def feed(self, data: bytes) -> None:
        with self._cv:
            self._buf += data
            self._cv.notify_all()

    def makefile(self, *_a, **_k):
        return self

    def read(self, n: int) -> bytes:
        with self._cv:
            while len(self._buf) < n and not self.closed:
                self._cv.wait()
            out = bytes(self._buf[:n])                # (fewer than asked: the session ended)
            del self._buf[:n]
            return out

    def close(self) -> None:
        with self._cv:
            self.closed = True
            self._cv.notify_all()

    def shutdown(self, _how=2) -> None:
        self.close()

    def settimeout(self, _t) -> None:
        pass

    def setsockopt(self, *_a) -> None:
        pass


SHARED_PORT = False                  # True when the daemon's TCP listener holds the port and hands connections over
WIRE_BYTES = os.environ.get("FROGNET_WIRE_BYTES") == "1"      # [BYTES_ON_THE_CONNECTION_V1] a measurement's switch


def _setting(role: str) -> dict:
    from core import channel_setting
    s = dict(channel_setting.setting_of(role) or {})
    s.setdefault("driver", DEFAULT_DRIVER)
    return s


def _secret(role: str) -> bytes:
    """[A_ROLES_OWN_SECRET_MOVES_FORWARD_V1] the secret a Role's keys are computed from in this process: the one that
    goes with the setting this process holds (core/channel_setting.secret_in_use)."""
    from core import channel_setting
    return channel_setting.secret_in_use(role)


def first_word(sock: socket.socket, wait_s: float = 45.0) -> bytes:
    """The first four bytes of a connection, looked at and left where they are. All four, however slowly they arrive
    (the wait is the kernel's, SO_RCVTIMEO, so MSG_WAITALL means all: core/chorus.first_word says what a Python
    timeout did here at 4,800 baud)."""
    sock.settimeout(None)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVTIMEO, struct.pack("ll", int(wait_s), int((wait_s % 1) * 1000000)))
    try:
        return sock.recv(len(WORD), socket.MSG_PEEK | socket.MSG_WAITALL)
    finally:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVTIMEO, struct.pack("ll", 0, 0))


class FrameCarrier:
    """With server_port it is the daemon's end (it is connected to, on the daemon's port); without, the proxy's (it
    connects to each daemon it has something for)."""

    max_message = 1 << 31
    takes_ram = True                                  # send_request() is told the Role a request is for

    def __init__(self, bind_ip: str = "0.0.0.0", server_port: Optional[int] = None, src: str = ""):
        self.server = server_port is not None
        self.port = int(server_port) if self.server else 0
        self._src = src
        self._lock = threading.Lock()
        self._inbox: "queue.Queue" = queue.Queue()
        self._links: Dict[Tuple[str, int], F.Link] = {}          # proxy: the daemon's address -> the one connection to it
        self._opening: Dict[Tuple[str, int], threading.Lock] = {}
        self._out: Dict[int, list] = {}                          # proxy: request id -> [cookie, the daemon asked, its link, its packet ID]
        self._by_pid: Dict[Tuple[int, int], int] = {}            # proxy: (a link, a request's packet ID) -> request id
        self._owed: Dict[Tuple[Tuple[str, int], int], tuple] = {}  # daemon: (asker, packet ID) -> (link, Role, sequence, counter)
        self._accepted: set = set()                              # daemon: the connections that are open to it
        # [A_NODE_TURNED_AWAY_DOES_NOT_KEEP_DIALLING_V1] proxy: a destination that would not take this end's connection
        # -> how many times running
        self._turned_away: Dict[Tuple[str, int], int] = {}
        self._next_id = 2
        self._closed = False
        self.wire = None                              # [THE_FRAME_RIDES_THE_WIRE_SOCKET_V1] wire(far) -> a WirePipe, or None
        self.n_req_out = self.n_req_in = self.n_rep_out = self.n_rep_in = self.n_failed = 0
        self.bytes_out = self.bytes_in = 0
        self._ls = None
        from core import channel_setting
        channel_setting.RUNNING = self.running
        channel_setting.on_change(lambda _v: self.setting_changed())
        if self.server:
            global SERVER
            SERVER = self
            SERVER_UP.set()
            from core import held_fire                # [THE_FIRE_RIDES_THE_HOLDERS_CONNECTION_V1]
            held_fire.add_sender(self._fire_to)
        if self.server and not SHARED_PORT:
            ls = socket.socket()
            ls.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                ls.bind((bind_ip, self.port))
                ls.listen(256)
            except OSError as e:
                ls.close()
                raise HomaUnavailable("the frame carrier could not listen on %s:%d: %s" % (bind_ip, self.port, e)) from e
            self._ls = ls
            threading.Thread(target=self._accept, daemon=True, name="frame-accept").start()

    def _say(self, text: str) -> None:
        print("[CHANNEL-FRAME] %.6f %s" % (time.time(), text), flush=True)

    # ------------------------------------------------------------------------------------------ the daemon's end
    def _accept(self) -> None:
        while True:
            try:
                s, addr = self._ls.accept()
            except OSError:
                return
            threading.Thread(target=self._first, args=(s, addr), daemon=True, name="frame-first-%s" % addr[0]).start()

    def _first(self, s: socket.socket, addr) -> None:
        try:
            word = first_word(s)
        except OSError as e:
            word = b""
            self._say("%s a connection from %s:%d said nothing (%s): closed" % (BUILD, addr[0], addr[1], e))
        if word and not self.adopt(s, addr, word):
            self._say("%s a connection from %s:%d began %r, which is not this carrier's word: closed" % (BUILD, addr[0], addr[1], word))
            word = b""
        if not word:
            try:
                s.close()
            except OSError:
                pass

    def adopt(self, s: socket.socket, addr, word: bytes) -> bool:
        """A connection whose first bytes are this carrier's word: it is this carrier's from here."""
        if bytes(word) != WORD:
            return False
        s.settimeout(None)
        got = b""
        while len(got) < len(WORD):                   # the word was looked at and left in place: take it
            part = s.recv(len(WORD) - len(got))
            if not part:
                return False
            got += part
        s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        link = F.Link(s, (addr[0], addr[1]), False, _setting, secret=_secret, say=self._say)
        link._deliver = lambda *got, _l=link: self._arrived(_l, *got)
        link.on_broken = lambda why, _l=link: self._ended(_l, "its writer stopped: %s" % why, ours=True)
        link.on_sent = self._sent
        link.on_running = self._report
        with self._lock:
            self._accepted.add(link)
        self._say("%s a connection from %s:%d is this carrier's; it is held open" % (BUILD, addr[0], addr[1]))
        threading.Thread(target=self._read, args=(link,), daemon=True, name="frame-read-%s" % addr[0]).start()
        return True

    def _hold(self, link: F.Link) -> None:
        link._deliver = lambda *got, _l=link: self._arrived(_l, *got)
        link.on_broken = lambda why, _l=link: self._ended(_l, "its writer stopped: %s" % why, ours=True)
        link.on_sent = self._sent
        link.on_running = self._report

    def adopt_pipe(self, pipe: "WirePipe", peer_ip: str) -> None:
        """[THE_FRAME_RIDES_THE_WIRE_SOCKET_V1] The daemon's end: the wire session from `peer_ip` has carried a first
        packet of this layer. Its packets are this carrier's from here, on that session's own sockets."""
        _pipe_n[0] += 1
        addr = (peer_ip, _pipe_n[0])                  # (a number where a port would be: this is no connection of its own)
        link = F.Link(pipe, addr, False, _setting, secret=_secret, say=self._say)
        self._hold(link)
        with self._lock:
            self._accepted.add(link)
        self._say("%s [THE_FRAME_RIDES_THE_WIRE_SOCKET_V1] the wire session from %s carries this layer's packets (%d); no "
                  "connection is opened for them" % (BUILD, peer_ip, addr[1]))
        threading.Thread(target=self._read, args=(link,), daemon=True, name="frame-read-%s" % peer_ip).start()

    def send_reply(self, dest: Tuple[str, int], rpc_id: int, payload: bytes) -> None:
        with self._lock:
            hit = self._owed.pop((dest, rpc_id), None)
        if hit is None:
            raise ConnectionGone("the reply to request %d from %s:%d has nowhere to go: its connection has ended"
                                 % (rpc_id, dest[0], dest[1]))
        link, role, stream, counter = hit
        link.submit_reply(role, stream, counter, payload)      # its writer packs, seals and writes it
        self.n_rep_out += 1
        self.bytes_out += len(payload)

    # ------------------------------------------------------------------------------------------- the proxy's end
    def _link_to(self, far: Tuple[str, int]) -> F.Link:
        with self._lock:
            link = self._links.get(far)
            gate = self._opening.setdefault(far, threading.Lock())
        if link is not None:
            return link
        with gate:                                    # one thread opens it; the others find it open
            with self._lock:
                link = self._links.get(far)
            if link is not None:
                return link
            # [THE_FRAME_RIDES_THE_WIRE_SOCKET_V1] a node not told chorus: its wire session to that daemon carries it
            pipe = self.wire(far) if self.wire is not None else None
            if pipe is not None:
                link = F.Link(pipe, far, True, _setting, secret=_secret, say=self._say)
                self._hold(link)
                with self._lock:
                    self._links[far] = link
                self._say("%s [THE_FRAME_RIDES_THE_WIRE_SOCKET_V1] this layer's packets for %s:%d ride the wire session "
                          "to it; no connection is opened for them" % (BUILD, far[0], far[1]))
                threading.Thread(target=self._read, args=(link,), daemon=True, name="frame-read-%s" % far[0]).start()
                return link
            s = socket.socket()
            try:
                if self._src:
                    s.bind((self._src, 0))            # from this node's own address, as a node's traffic is
                s.settimeout(10)
                s.connect(far)
                s.settimeout(None)
                s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                s.sendall(WORD)
            except OSError as e:
                s.close()
                # (not _away: a node that cannot be connected to is down or unreachable. Seen in the simulator, 22 Nov:
                # a restarting daemon was reported as "does not take this node's framed connection".)
                raise ConnectionGone("no connection to %s:%d: %s" % (far[0], far[1], e)) from e
            link = F.Link(s, far, True, _setting, secret=_secret, say=self._say)
            link._deliver = lambda *got, _l=link: self._arrived(_l, *got)
            link.on_broken = lambda why, _l=link: self._ended(_l, "its writer stopped: %s" % why, ours=True)
            link.on_sent = self._sent
            link.on_running = self._report
            with self._lock:
                self._links[far] = link
            self._say("%s the connection to %s:%d is open; it is held open, and everything for that destination crosses it"
                      % (BUILD, far[0], far[1]))
            threading.Thread(target=self._read, args=(link,), daemon=True, name="frame-read-%s" % far[0]).start()
            return link

    def _fire_to(self, ip: str, raw: bytes) -> Optional[str]:
        """[THE_FIRE_RIDES_THE_HOLDERS_CONNECTION_V1] The daemon's end: a held read's fire for a holder on the node `ip`
        is handed to the writer of the framed connection that node opened here. It crosses as traffic of no Role,
        under that sequence's protocol and key like anything else. Returns what it was handed to, or None."""
        from core import held_fire
        with self._lock:
            links = [l for l in self._accepted if l.far[0] == ip and l.sock is not None]
        for link in reversed(links):
            # Only the end that opened a connection names a sequence on it. The fire rides one the holder's node has
            # named: the one for traffic of no Role if it is there, else a Role's (found between machines, 22 Nov:
            # handed to a sequence that node had not named, the writer stopped and the connection ended with it).
            named = link.named_to_this_end()
            if not named:
                continue
            stream = next((n for n, r in named.items() if r == ""), next(iter(named)))
            link.submit_on(stream, named[stream], held_fire.FRAME_MARK + raw)
            return "the framed connection from %s%s" % (ip, "" if named[stream] == "" else " (on its sequence for %s)" % named[stream])
        return None

    def _away(self, far: Tuple[str, int], why: str, locked: bool = False) -> str:
        """[A_NODE_TURNED_AWAY_DOES_NOT_KEEP_DIALLING_V1] Found on John's network, 22 Nov 2026: Seattle7's file said to
        reach the ControlRam on chorus, the node it was on had been set back to tcp, and Seattle7 opened a framed
        connection to it ten times a second, each reset, each ending three requests, with nothing to say why.

        A destination that ends this end's connection before it has said anything, or that cannot be connected to,
        has turned this end away. That is said here, with what to look at, the 1st, 2nd, 4th, 8th... time running, and
        it is the reason every request for that destination fails with. It is NOT an event (_ended): nothing was
        missed on a connection that said nothing, so nothing is read because of it. The ten a second were this
        process's own reads, each made because the last one's connection had ended. The destination is dialled again
        when there is next a request for it, and at no other time. Returns the reason."""
        if locked:
            n = self._turned_away[far] = self._turned_away.get(far, 0) + 1
        else:
            with self._lock:
                n = self._turned_away[far] = self._turned_away.get(far, 0) + 1
        what = ("%s:%d does not take this node's framed connection (%s). This node's settings put traffic for it on "
                "the frame carrier; if its daemon was not told of that carrier, its /etc/frognet/semantic_transport "
                "and this node's disagree, and one of the two is to change" % (far[0], far[1], why))
        if n & (n - 1) == 0:
            self._say("%s %s, %d time(s) running. It is dialled again when there is next a request for it."
                      % (BUILD, what, n))
        return what

    def send_request(self, dest: Tuple[str, int], payload: bytes, cookie: int, ram: str = "", word: str = "") -> int:
        """One request for the Role `ram` ("" for none), handed to the writer of the one connection to that
        destination. `word` is not consulted: the Role's setting says the protocol."""
        far = (dest[0], int(dest[1]))
        link = self._link_to(far)
        with self._lock:
            # [A_REQUEST_HANDED_TO_AN_ENDED_CONNECTION_IS_REFUSED_V1] found by core/test_turned_away_oracle.py, 22 Nov:
            # a connection reset between being opened and this line had already failed its requests (none), and a
            # request registered after that was handed to a writer that had stopped: it never ended.
            if self._links.get(far) is not link:
                raise ConnectionGone("the connection to %s:%d ended before the request was handed to it (%s)"
                                     % (far[0], far[1], getattr(link, "gone", None) or "it was closed"))
            rid = self._next_id
            self._next_id += 2
            self._out[rid] = [cookie, far, link, None]

        def known(pid: int) -> None:                  # on the writer's thread, before the frame is written
            with self._lock:
                hit = self._out.get(rid)
                if hit is not None:
                    hit[3] = pid
                    self._by_pid[(id(link), pid)] = rid
        try:
            link.submit(ram, payload, known)
        except ConnectionGone as e:
            with self._lock:
                self._out.pop(rid, None)
            self._ended(link, "it had ended: %s" % e)
            raise
        self.n_req_out += 1
        self.bytes_out += len(payload)
        return rid

    def _sent(self, role: str, reply: bool, n: int, protocol: str) -> None:
        if WIRE_BYTES:
            print("[WIRE-BYTES] %.6f %s %s %s %d" % (time.time(), protocol, role or "-", "rep" if reply else "req", n), flush=True)

    def release(self, dest: Tuple[str, int], ram: str = "", word: str = "") -> None:
        """Nothing to do: a connection is not closed because a Role's protocol changed."""

    def abort(self, rpc_id: int) -> None:
        with self._lock:
            hit = self._out.pop(rpc_id, None)
            if hit is not None:
                self._by_pid.pop((id(hit[2]), hit[3]), None)

    def peer_gone(self, peer: Tuple[str, int]) -> int:
        """The driver's word that a machine left: this end's connection to it is closed, and what was outstanding on it
        ends with it."""
        with self._lock:
            ls = [l for far, l in self._links.items() if far[0] == peer[0]]
            for far in [f for f in self._turned_away if f[0] == peer[0]]:
                del self._turned_away[far]
        return sum(self._ended(l, "its machine left") for l in ls)

    # --------------------------------------------------------------------------------------------------- both ends
    def setting_changed(self) -> None:
        """This process's held read on the settings woke it: every connection puts its calls into its stream now, and
        this end says what it is running."""
        with self._lock:
            self._turned_away.clear()                 # counted afresh under the new settings
            links = list(self._links.values()) + list(self._accepted)
        for link in links:
            try:
                link.setting_changed()
            except ConnectionGone as e:
                self._ended(link, "a call could not be sent on it: %s" % e)
        self._report()

    def _report(self) -> None:
        from core import channel_setting
        channel_setting.report()

    def running(self) -> Dict[str, dict]:
        """What this process is sending under, for each Role it carries: {Role: {driver, number, protection, sealed}}.
        Every connection of a process runs a Role the same way once its calls are out; a Role on two connections that
        differ is said as one that is not yet on the setting's number, so a switch is not reported until every
        connection has made it."""
        with self._lock:
            links = list(self._links.values()) + list(self._accepted)
        out: Dict[str, dict] = {}
        for link in links:
            for role, how in link.running().items():
                if role not in out or how["number"] != F.wanted(_setting(role))[0]:
                    out[role] = how
        return out

    def _arrived(self, link: F.Link, role: str, stream: int, counter: int, reply: bool, payload: bytes) -> None:
        """One message opened on a connection, in the order it was opened (the Link calls this)."""
        self.bytes_in += len(payload)
        if len(link._in) != getattr(link, "_reported", 0):     # a Role this end had not carried: it is in the report from here
            link._reported = len(link._in)
            threading.Thread(target=self._report, daemon=True, name="frame-report").start()
        if self.server:
            if reply:
                return
            pid = F.packet_id(counter, stream, False)
            with self._lock:
                self._owed[(link.far, pid)] = (link, role, stream, counter)
            self.n_req_in += 1
            self._inbox.put(Msg(pid, 0, link.far, payload, True))
            return
        if not reply:
            # [THE_FIRE_RIDES_THE_HOLDERS_CONNECTION_V1] this end asks and the far end answers; the one thing the far
            # end (a role host's daemon) puts on this connection of its own accord is a held read's fire
            from core import held_fire
            if payload.startswith(held_fire.FRAME_MARK):
                held_fire.deliver_here(payload[len(held_fire.FRAME_MARK):], "from %s on its framed connection" % link.far[0])
            else:
                self._say("%s %s:%d sent something that answers no request and is not a fire (%d bytes): dropped"
                          % (BUILD, link.far[0], link.far[1], len(payload)))
            return
        with self._lock:
            rid = self._by_pid.pop((id(link), F.packet_id(counter, stream, False)), None)
            hit = self._out.pop(rid, None) if rid is not None else None
        if hit is None:
            return                                        # aborted, or its session ended: not delivered
        self.n_rep_in += 1
        self._inbox.put(Msg(rid, hit[0], hit[1], payload, False))

    def _read(self, link: F.Link) -> None:
        why = "unset"
        try:
            link.pump()
        except ConnectionGone as e:
            why = str(e)
        except Exception as e:
            why = "%s: %r" % (type(e).__name__, e)
        self._ended(link, why)

    def _ended(self, link: F.Link, why: str, ours: bool = False) -> int:
        """A connection has ended. Gone is gone: every request outstanding on it ends here, and nothing is used in its
        place. It is opened again when there is next something to send."""
        if not self._closed and (self.server or link.received):
            # An event: read what the settings are now. A connection the far end said nothing on is not one: nothing
            # was missed on it, and the read would go to what has just turned this end away.
            from core import channel_setting
            channel_setting.connection_ended()
        with self._lock:
            mine = self._links.get(link.far) is link
            if mine:
                del self._links[link.far]
                if ours and link.wire_failed:
                    ours = False                      # the writer stopped because the connection failed under it
                if not self.server and not self._closed and not ours:     # (ours: this end's own writer stopped)
                    if link.received == 0:
                        why = self._away(link.far, "it ended the connection before it had said anything: %s" % why, True)
                    else:
                        self._turned_away.pop(link.far, None)     # it spoke on this one: it takes frames
                # [A_REQUEST_HANDED_TO_AN_ENDED_CONNECTION_IS_REFUSED_V1] said under the lock send_request registers
                # under: a request is either among `failed` below or sees this and is refused. Never neither.
                link.gone = why
            failed = [(rid, hit[0]) for rid, hit in self._out.items() if hit[2] is link]
            for rid, _ck in failed:
                hit = self._out.pop(rid)
                self._by_pid.pop((id(link), hit[3]), None)
            was_open = link in self._accepted
            self._accepted.discard(link)
            owed = [k for k, v in self._owed.items() if v[0] is link]
            for k in owed:
                del self._owed[k]
        if link.sock is None and not (mine or was_open):
            return 0                                  # already ended, and said
        link.close()
        self._say("%s the connection %s %s:%d ended (%s): %d request(s) outstanding on it end with it%s"
                  % (BUILD, "from" if self.server else "to", link.far[0], link.far[1], why, len(failed),
                     "" if self.server else "; it is opened again when there is next something to send"))
        for rid, ck in failed:
            self.n_failed += 1
            self._inbox.put(RpcFailed(rid, ck, errno.EHOSTUNREACH, "the framed connection %s %s:%d ended (%s)"
                                      % ("from" if self.server else "to", link.far[0], link.far[1], why)))
        if (mine or was_open) and not self._closed:
            self._inbox.put(PeerConnectionEnded(link.far, why))      # [A_SESSION_ENDS_WITH_ITS_CONNECTION_V1] every session on it
        return len(failed)

    def recv(self) -> Msg:
        """The next whole message. Blocks: it is a held read."""
        item = self._inbox.get()
        if isinstance(item, BaseException):
            raise item
        return item

    def counters(self) -> dict:
        return {"req_out": self.n_req_out, "req_in": self.n_req_in, "rep_out": self.n_rep_out, "rep_in": self.n_rep_in,
                "failed": self.n_failed, "bytes_out": self.bytes_out, "bytes_in": self.bytes_in, "port": self.port}

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._ls is not None:
            try:
                self._ls.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            self._ls.close()
        with self._lock:
            links = list(self._links.values()) + list(self._accepted)
        for link in links:
            link.close()
        self._inbox.put(CarrierClosed("closed"))


def startup_line(who: str, s: FrameCarrier) -> str:
    return ("[HOMA] %s build=%s: one connection to each destination, held open (frame %s): every Role's traffic crosses it "
            "under that Role's own protocol and key%s"
            % (who, BUILD, F.BUILD, (", arriving on the daemon's port %d%s" % (s.port, " (held by its TCP listener, which reads "
                                     "each connection's first word)" if SHARED_PORT else "")) if s.server else ""))
