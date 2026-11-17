#!/usr/bin/env python3
################################################################
#  Copyright (C) 2016-2026 Fawcett Innovations LLC             #
#                                                              #
#  SPDX-License-Identifier: GPL-2.0-only                       #
#                                                              #
#  See COPYRIGHT and LICENSE at the root of this tree.         #
################################################################
"""
core/homa_socket.py -- [HOMA_TRANSPORT_V1] the Homa kernel module's socket, as a carrier of whole messages.

Homa (PlatformLab/HomaModule, IPPROTO_HOMA = 146) carries request and response MESSAGES, not a byte stream. This file
is the only place that knows its system-call interface. Layouts and constants are homa.h's at commit 07ce63e; they are
checked against the header by core/test_homa_layout_oracle.py, which compiles the header and compares.

The carrier contract (the same four calls every carrier under the FNW1 wire gives; the engine above never sees more):

    send_request(dest, payload, cookie) -> rpc id          a new RPC; the kernel names it
    send_reply(dest, rpc_id, payload)                      the response to a request this socket received
    recv() -> Msg(id, cookie, peer, payload, is_request)   the next request or response, whole
              raises RpcFailed(id, cookie, errno)          one RPC ended without its response (ETIMEDOUT, ...)
    abort(rpc_id)                                          give up a request this socket sent
    peer_gone(peer) -> n                                   membership says the end at `peer` has left: a transport
                                                           that keeps no timeout of its own drops its state with it

Buffers (the ownership contract, A2-HOMA-BUFFER-OWNERSHIP): this socket registers ONE receive region with the kernel
(SO_HOMA_RCVBUF) for its life. recvmsg returns bpage offsets into it; those bpages are this process's until handed
back. recv() copies the message out into a Python bytes and hands the bpages back on the SAME thread's next recvmsg,
so no bpage is ever held across two messages and nothing above this file can see the region.

[NO_FALLBACK_V1] No Homa socket, no TCP in its place: HomaUnavailable is raised with the kernel's reason and the
caller stops. A message over HOMA_MAX_MESSAGE_LENGTH is refused here with its size. This file does not split: the
driver above it does ([FRAMES_GO_IN_PIECES_V1], proxy/transport_homa.py), to max_message() below.
"""
from __future__ import annotations

import ctypes
import ctypes.util
import errno
import mmap
import os
import socket
import struct
import sys
import threading
from typing import List, NamedTuple, Optional, Tuple

BUILD = "homa-transport-v4-20261117p"
HOMA_PINNED_COMMIT = "07ce63ef153207a953c42a749ee2429fb5282f55"

IPPROTO_HOMA = 146
HOMA_MAX_MESSAGE_LENGTH = 1000000
HOMA_BPAGE_SHIFT = 16
HOMA_BPAGE_SIZE = 1 << HOMA_BPAGE_SHIFT
HOMA_MAX_BPAGES = (HOMA_MAX_MESSAGE_LENGTH + HOMA_BPAGE_SIZE - 1) >> HOMA_BPAGE_SHIFT
SO_HOMA_RCVBUF = 10
SO_HOMA_SERVER = 11


class homa_sendmsg_args(ctypes.Structure):
    _fields_ = [("id", ctypes.c_uint64), ("completion_cookie", ctypes.c_uint64),
                ("flags", ctypes.c_uint32), ("reserved", ctypes.c_uint32)]


class homa_recvmsg_args(ctypes.Structure):
    _fields_ = [("id", ctypes.c_uint64), ("completion_cookie", ctypes.c_uint64),
                ("num_bpages", ctypes.c_uint32), ("reserved", ctypes.c_uint32),
                ("bpage_offsets", ctypes.c_uint32 * HOMA_MAX_BPAGES)]


class homa_rcvbuf_args(ctypes.Structure):
    _fields_ = [("start", ctypes.c_uint64), ("length", ctypes.c_uint64)]


class homa_abort_args(ctypes.Structure):
    _fields_ = [("id", ctypes.c_uint64), ("error", ctypes.c_uint32), ("_pad1", ctypes.c_uint32),
                ("_pad2", ctypes.c_uint64 * 2)]


def _iowr(typ: str, nr: int, size: int) -> int:
    return (3 << 30) | (size << 16) | (ord(typ) << 8) | nr


HOMAIOCABORT = _iowr("h", 0x92, ctypes.sizeof(homa_abort_args))


class _iovec(ctypes.Structure):
    _fields_ = [("iov_base", ctypes.c_void_p), ("iov_len", ctypes.c_size_t)]


class _msghdr(ctypes.Structure):
    _fields_ = [("msg_name", ctypes.c_void_p), ("msg_namelen", ctypes.c_uint32),
                ("msg_iov", ctypes.POINTER(_iovec)), ("msg_iovlen", ctypes.c_size_t),
                ("msg_control", ctypes.c_void_p), ("msg_controllen", ctypes.c_size_t),
                ("msg_flags", ctypes.c_int)]


class _sockaddr_in(ctypes.Structure):
    _fields_ = [("sin_family", ctypes.c_uint16), ("sin_port", ctypes.c_uint16),
                ("sin_addr", ctypes.c_uint8 * 4), ("sin_zero", ctypes.c_uint8 * 8)]


class Msg(NamedTuple):
    id: int                       # the RPC id as this socket knows it
    cookie: int                   # for a response: the cookie given to send_request; for a request: 0
    peer: Tuple[str, int]         # (ip, port) of the sender
    payload: bytes
    is_request: bool


class HomaUnavailable(RuntimeError):
    """This machine has no usable Homa socket. Carries the kernel's reason. Nothing is tried in its place."""


class RpcFailed(OSError):
    """One RPC ended without its response. errno is the kernel's (ETIMEDOUT: the peer went silent for timeout_ticks)."""

    def __init__(self, rpc_id: int, cookie: int, err: int):
        super().__init__(err, "homa rpc %d (cookie %d): %s" % (rpc_id, cookie, os.strerror(err)))
        self.rpc_id, self.cookie = rpc_id, cookie


class CarrierClosed(RuntimeError):
    """The socket was closed under a recv(): the receiver's thread ends."""


def _sockaddr(dest: Tuple[str, int]) -> _sockaddr_in:
    sa = _sockaddr_in()
    sa.sin_family = socket.AF_INET
    sa.sin_port = socket.htons(int(dest[1]))
    sa.sin_addr[:] = socket.inet_aton(dest[0])
    return sa


SYSCTL_DIR = "/proc/sys/net/homa"
METRICS_PATH = "/proc/net/homa_metrics"


def sysctls() -> dict:
    """Every value under /proc/sys/net/homa, as the kernel states it. Raises HomaUnavailable when the directory is not
    there (the module is not loaded)."""
    try:
        names = sorted(os.listdir(SYSCTL_DIR))
    except OSError as e:
        raise HomaUnavailable("%s is not there (%s): the Homa module is not loaded on this kernel (%s)"
                              % (SYSCTL_DIR, e.strerror, os.uname().release)) from e
    out = {}
    for n in names:
        try:
            with open(os.path.join(SYSCTL_DIR, n)) as f:
                out[n] = f.read().strip()
        except OSError as e:
            out[n] = "unreadable: %s" % e.strerror
    return out


# [THE_DRIVER_LOADS_ITS_OWN_HOMA_V1] John 2026-11-17: "The driver should be independent and able to run alongside the
# existing drivers. There should be no risk to existing systems at all." Soft Homa is a library (the Homa API interface,
# ribbit/examples/homa libsofthoma.so). This driver opens that library itself and calls it, and only this driver's
# socket goes through it. It is NOT put into the process with LD_PRELOAD: that would stand it in front of every
# socket in the process, the TCP driver's included. (LD_PRELOAD is for a program that cannot be changed, his cp_node.)
# Where the library is: FROGNET_SOFTHOMA_LIB, else the one line of SOFTHOMA_LIB_FILE. Neither: there is no soft Homa
# here, the library is never loaded, and Homa means the kernel module.
SOFTHOMA_LIB_FILE = os.environ.get("FROGNET_SOFTHOMA_LIB_FILE", "/etc/frognet/softhoma_library")
_soft_lib = None
_soft_lib_asked = False
_soft_lib_lock = threading.Lock()


def soft_homa_library():
    """The soft Homa library this node was told to use, opened once, or None when it was told of none."""
    global _soft_lib, _soft_lib_asked
    with _soft_lib_lock:
        if _soft_lib_asked:
            return _soft_lib
        path, src = os.environ.get("FROGNET_SOFTHOMA_LIB", "").strip(), "FROGNET_SOFTHOMA_LIB"
        if not path:
            try:
                with open(SOFTHOMA_LIB_FILE) as f:
                    path, src = f.read().strip(), SOFTHOMA_LIB_FILE
            except FileNotFoundError:
                path = ""
        if path:
            try:
                _soft_lib = ctypes.CDLL(path, use_errno=True)       # this handle only: nothing else in the process sees it
                _soft_lib.softhoma_build.restype = ctypes.c_char_p
                _soft_lib.softhoma_record_bytes.restype = ctypes.c_size_t
                _soft_lib.softhoma_await.argtypes = [ctypes.c_char_p, ctypes.c_double]
                _soft_lib.softhoma_wait_servers.argtypes = [ctypes.c_uint64]
                _soft_lib.softhoma_wait_servers.restype = ctypes.c_uint64
            except (OSError, AttributeError) as e:
                raise HomaUnavailable("%s names the soft Homa library %r and it could not be used: %s: %s. This process "
                                      "does not use another Homa in its place." % (src, path, type(e).__name__, e)) from e
            print("[HOMA] [THE_DRIVER_LOADS_ITS_OWN_HOMA_V1] soft Homa %s opened from %s (%s) by the Homa driver; no "
                  "other socket in this process goes through it" % (_soft_lib.softhoma_build().decode(), path, src), flush=True)
        _soft_lib_asked = True
        return _soft_lib


# [THE_MEMORY_IS_WHERE_THE_ROLE_IS_V1] John 2026-11-17: "You know it can change address during a run. Right?" Soft Homa's
# memory is on the machine that holds the SystemRam role, on soft Homa's own port. The role is elected and it moves when
# the network splits or merges; a node's own address can change too. So no address is kept in any setting: both are
# asked for when a socket is opened and again at every network event (the drivers' hosts-file watcher), and when either
# has changed the library is told (softhoma_memory): its sockets on the old memory go down, the sessions on them end,
# and the next socket opens on the memory where it is now.
SOFTHOMA_ROLE = "databasehost.frognet"                       # the SystemRam, as the hosts file names it
SOFTHOMA_PORT = int(os.environ.get("FROGNET_SOFTHOMA_PORT", "9131"))
_soft_told: Optional[Tuple[str, str]] = None
_soft_named = threading.Event()                              # set each time the library is told where the memory is


def soft_memory_now() -> Tuple[str, str]:
    """(HOST:PORT of soft Homa's memory, this node's own address), both as they are NOW. A test replaces this function.

    [ONE_INPUT_ONE_RULE_V1] John 2026-11-17: "the calculation of the ram host ip address is deterministic. One option is
    to just set it to databasehost.frognet". The address is the one the hosts file gives that name: the file the merge's
    election writes and the drivers' watcher watches (frognet_held.HOSTS), read here, not asked of a resolver, which
    could answer from somewhere else or from before. Every node applies the same rule to its own copy of that file, so
    two nodes whose files agree get the same address, and a node whose file has not changed yet is the case
    [A_PEER_IS_THERE_WHEN_ITS_CELL_IS_V1] covers. No line for the name: there is no memory to name, and it is said."""
    from core import frognet_tuples
    d = os.path.dirname(os.path.abspath(frognet_tuples.__file__))       # [CORE_ON_THE_PATH_V1] as homa_liveness does
    if d not in sys.path:
        sys.path.append(d)
    import frognet_held
    found = []
    with open(frognet_held.HOSTS) as f:
        for line in f:
            p = line.split("#", 1)[0].split()
            if len(p) >= 2 and SOFTHOMA_ROLE in p[1:]:
                found.append(p[0])
    if len(set(found)) != 1:
        raise LookupError("%s has %s for %s; the role must be named by exactly one address"
                          % (frognet_held.HOSTS, "no line" if not found else "the addresses %s" % ", ".join(sorted(set(found))), SOFTHOMA_ROLE))
    return "%s:%d" % (found[0], SOFTHOMA_PORT), frognet_tuples.my_ip()


def soft_memory_tell(why: str) -> bool:
    """Ask where the memory and this node are now and, if either changed, tell the library. True when it was told.
    Raises HomaUnavailable when it cannot be said where they are: nothing is guessed and nothing kept from before."""
    global _soft_told
    lib = soft_homa_library()
    if lib is None:
        return False
    try:
        now = soft_memory_now()
    except Exception as e:
        raise HomaUnavailable("soft Homa's memory is on the machine %s names and it cannot be said where that is now "
                              "(%s: %s); no address from before is used" % (SOFTHOMA_ROLE, type(e).__name__, e)) from e
    with _soft_lib_lock:
        if now == _soft_told:
            return False
        was, _soft_told = _soft_told, now
    down = lib.softhoma_memory(now[0].encode(), now[1].encode())
    _soft_named.set()
    print("[HOMA] [THE_MEMORY_IS_WHERE_THE_ROLE_IS_V1] %s: soft Homa's memory is at %s and this node is %s (before: %s); "
          "%d socket(s) on the memory before went down" % (why, now[0], now[1], "%s, node %s" % was if was else "nothing", down), flush=True)
    return True


def soft_peer_there(dest: Tuple[str, int], wait_s: float) -> bool:
    """[A_PEER_IS_THERE_WHEN_ITS_CELL_IS_V1] Whether a Homa listener is bound at `dest` in the memory this process is on
    now; with wait_s > 0, one held read on its capability cell for up to that long. On kernel Homa there is no such
    thing to ask and the answer is True."""
    lib = soft_homa_library()
    if lib is None:
        return True
    soft_memory_tell("a peer is being looked for")
    return lib.softhoma_await(("%s:%d" % (dest[0], int(dest[1]))).encode(), ctypes.c_double(float(wait_s))) == 1


def soft_on_servers_change(tell, who: str) -> None:
    """[A_SERVER_ARRIVING_IS_AN_EVENT_V1] Call tell() each time a server arrives in or leaves soft Homa's memory, or the
    memory itself changes. One thread, held in the library on the standing read on the capability cells. A driver's
    end gives it its liveness's wake(): a hold that could not be taken because the ControlRam's machine was not in the
    memory yet is taken when that machine arrives, which is the network event it was waiting for. Nothing on kernel
    Homa."""
    lib = soft_homa_library()
    if lib is None:
        return

    def held():
        seen = 0
        while True:
            try:
                if _soft_told is None:
                    soft_memory_tell("%s is waiting for servers to arrive" % who)
                seen = lib.softhoma_wait_servers(seen)
            except HomaUnavailable as e:
                print("[HOMA] %s: %s; servers arriving are waited for once the memory can be named" % (who, e), flush=True)
                _soft_named.wait(); _soft_named.clear()
                continue
            try:
                tell()
            except Exception as e:
                print("[HOMA] %s: the handler for a server arriving raised %s: %r" % (who, type(e).__name__, e), flush=True)
    threading.Thread(target=held, daemon=True, name="softhoma-servers").start()


def soft_homa_loaded() -> Optional[str]:
    """Soft Homa's build if this node was told of a soft Homa library (soft_homa_library), else None."""
    lib = soft_homa_library()
    return lib.softhoma_build().decode() if lib is not None else None


def max_message() -> int:
    """[FRAMES_GO_IN_PIECES_V1] The largest message this process's Homa carries as ONE message on its fast path: Homa's
    own limit under the kernel module, and one cell (softhoma_record_bytes) under soft Homa. The driver cuts a frame
    into pieces of this size. A soft Homa that cannot say is refused: there is no guessing its record size."""
    lib = soft_homa_library()
    return HOMA_MAX_MESSAGE_LENGTH if lib is None else int(lib.softhoma_record_bytes())


def startup_line(who: str) -> str:
    """[DIAGNOSTICS_SAY_IT_V1] One log line that says what this process is running on, so nobody runs a command to find
    out: build, kernel, the pinned Homa commit this file's layouts are from, and every Homa sysctl."""
    soft = soft_homa_loaded()
    if soft:                                    # [SOFT_HOMA_CARRIER_V1] no module, so no sysctls: it has no timers to set
        return ("[HOMA] %s build=%s kernel=%s homa_h=%s on SOFT HOMA %s (Homa's calls on shared memory, memory at %s, "
                "this machine named %s): no kernel module, no Homa sysctls"
                % (who, BUILD, os.uname().release, HOMA_PINNED_COMMIT[:7], soft,
                   _soft_told[0] if _soft_told else "(not asked yet)", _soft_told[1] if _soft_told else "(not asked yet)"))
    s = sysctls()
    keys = ("timeout_ticks", "resend_ticks", "resend_interval", "request_ack_ticks", "unsched_bytes", "hijack_tcp",
            "max_gso_size", "link_mbps", "max_overcommit", "num_priorities")
    head = " ".join("%s=%s" % (k, s[k]) for k in keys if k in s)
    rest = " ".join("%s=%s" % (k, v) for k, v in s.items() if k not in keys)
    return ("[HOMA] %s build=%s kernel=%s homa_h=%s %s | %s"
            % (who, BUILD, os.uname().release, HOMA_PINNED_COMMIT[:7], head, rest))


class HomaSocket:
    """One Homa socket. server_port: bind it and take requests (a port under 32768); None: a client socket on a port
    the kernel picks, which still receives the responses to what it sent."""

    #: receive region: enough bpages for this many maximum-size messages waiting to be copied out at once
    REGION_MESSAGES = 64

    def __init__(self, bind_ip: str = "0.0.0.0", server_port: Optional[int] = None):
        # [THE_DRIVER_LOADS_ITS_OWN_HOMA_V1] every call this socket makes goes to one place: the soft Homa library
        # this node was told of, or else the system's own calls (the kernel module's socket). The socket is a bare
        # descriptor so that no call on it can go anywhere else.
        soft = soft_homa_library()
        if soft is not None:
            soft_memory_tell("a Homa socket is being opened")
        self._libc = soft if soft is not None else ctypes.CDLL(None, use_errno=True)
        self._fd = self._libc.socket(socket.AF_INET, socket.SOCK_DGRAM, IPPROTO_HOMA)
        if self._fd < 0:
            e = ctypes.get_errno()
            raise HomaUnavailable("socket(AF_INET, SOCK_DGRAM, IPPROTO_HOMA=146) failed on kernel %s: errno=%s (%s). %s; "
                                  "this process does not use another transport."
                                  % (os.uname().release, e, os.strerror(e),
                                     "Soft Homa could not open it (is SOFTHOMA_RAM set and its memory there?)" if soft is not None
                                     else "The Homa module is not loaded here"))
        self._closed = False
        self._tls = threading.local()            # per thread: the bpages of its last message, to hand back
        self.region_len = self.REGION_MESSAGES * HOMA_MAX_BPAGES * HOMA_BPAGE_SIZE
        self._region = mmap.mmap(-1, self.region_len)           # page-aligned, anonymous, this socket's for its life
        self._region_addr = ctypes.addressof(ctypes.c_char.from_buffer(self._region))
        rb = homa_rcvbuf_args(self._region_addr, self.region_len)
        if self._libc.setsockopt(self._fd, IPPROTO_HOMA, SO_HOMA_RCVBUF, ctypes.byref(rb), ctypes.sizeof(rb)) != 0:
            e = ctypes.get_errno()
            self._libc.close(self._fd)
            raise HomaUnavailable("setsockopt(SO_HOMA_RCVBUF, %d bytes) failed: errno=%d (%s)"
                                  % (self.region_len, e, os.strerror(e)))
        self.port = 0
        if server_port is not None:
            sa = _sockaddr((bind_ip, int(server_port)))
            if self._libc.bind(self._fd, ctypes.byref(sa), ctypes.sizeof(sa)) != 0:
                e = ctypes.get_errno()
                self._libc.close(self._fd)
                raise OSError(e, "homa bind to %s:%d: %s" % (bind_ip, int(server_port), os.strerror(e)))
            one = ctypes.c_int(1)
            if self._libc.setsockopt(self._fd, IPPROTO_HOMA, SO_HOMA_SERVER, ctypes.byref(one), ctypes.sizeof(one)) != 0:
                e = ctypes.get_errno()
                self._libc.close(self._fd)
                raise HomaUnavailable("setsockopt(SO_HOMA_SERVER) on port %d failed: errno=%d (%s)"
                                      % (server_port, e, os.strerror(e)))
            self.port = int(server_port)
        # [HOMA_COUNTERS_V1] what this socket carried, for the proof that a run used Homa (with /proc/net/homa_metrics)
        self.n_req_out = self.n_req_in = self.n_rep_out = self.n_rep_in = self.n_failed = 0
        self.bytes_out = self.bytes_in = 0

    # ---------------------------------------------------------------------------------------------------- sending
    def _sendmsg(self, dest: Tuple[str, int], payload: bytes, rpc_id: int, cookie: int) -> int:
        n = len(payload)
        if n <= 0 or n > HOMA_MAX_MESSAGE_LENGTH:
            raise ValueError("homa: a message of %d bytes cannot be sent (1..%d); it is not split here"
                             % (n, HOMA_MAX_MESSAGE_LENGTH))
        sa = _sockaddr(dest)
        buf = (ctypes.c_char * n).from_buffer_copy(payload)
        iov = _iovec(ctypes.cast(buf, ctypes.c_void_p), n)
        args = homa_sendmsg_args(rpc_id, cookie, 0, 0)
        mh = _msghdr(ctypes.cast(ctypes.byref(sa), ctypes.c_void_p), ctypes.sizeof(sa), ctypes.pointer(iov), 1,
                     ctypes.cast(ctypes.byref(args), ctypes.c_void_p), ctypes.sizeof(args), 0)
        if self._libc.sendmsg(self._fd, ctypes.byref(mh), 0) < 0:
            e = ctypes.get_errno()
            raise OSError(e, "homa sendmsg to %s:%d (%d bytes, id %d): %s" % (dest[0], dest[1], n, rpc_id, os.strerror(e)))
        self.bytes_out += n
        return int(args.id)

    def send_request(self, dest: Tuple[str, int], payload: bytes, cookie: int) -> int:
        rid = self._sendmsg(dest, payload, 0, cookie)
        self.n_req_out += 1
        return rid

    def send_reply(self, dest: Tuple[str, int], rpc_id: int, payload: bytes) -> None:
        self._sendmsg(dest, payload, rpc_id, 0)
        self.n_rep_out += 1

    def abort(self, rpc_id: int) -> None:
        a = homa_abort_args(rpc_id, 0, 0)
        if self._libc.ioctl(self._fd, ctypes.c_ulong(HOMAIOCABORT), ctypes.byref(a)) != 0:
            e = ctypes.get_errno()
            if e != errno.EINVAL:                 # EINVAL: the RPC is already gone, which is what was asked
                raise OSError(e, "homa abort of rpc %d: %s" % (rpc_id, os.strerror(e)))

    def peer_gone(self, peer: Tuple[str, int]) -> int:
        """The module as shipped has no such call and needs none: its own timeout_ticks ends what is outstanding with a
        silent peer. The driver has already aborted the requests it sent that peer and answered the ones it owed."""
        return 0

    # -------------------------------------------------------------------------------------------------- receiving
    def recv(self) -> Msg:
        """The next whole message for this socket, copied out. The bpages of the message this thread received last are
        handed back in this call. Blocks."""
        owed: List[int] = getattr(self._tls, "owed", [])
        args = homa_recvmsg_args()
        args.num_bpages = len(owed)
        for i, off in enumerate(owed):
            args.bpage_offsets[i] = off
        sa = _sockaddr_in()
        mh = _msghdr(ctypes.cast(ctypes.byref(sa), ctypes.c_void_p), ctypes.sizeof(sa), None, 0,
                     ctypes.cast(ctypes.byref(args), ctypes.c_void_p), ctypes.sizeof(args), 0)
        n = self._libc.recvmsg(self._fd, ctypes.byref(mh), 0)
        if n < 0:
            e = ctypes.get_errno()
            # the kernel leaves num_bpages as passed when it could not take the bpages back: they are still ours
            self._tls.owed = [int(args.bpage_offsets[i]) for i in range(int(args.num_bpages))]
            if self._closed or e in (errno.EBADF, errno.ESHUTDOWN):
                raise CarrierClosed("homa socket closed")
            if e == errno.ENETDOWN and soft_homa_library() is not None:
                # [THE_MEMORY_IS_WHERE_THE_ROLE_IS_V1] soft Homa: the memory under this socket is gone, or has moved
                raise CarrierClosed("the memory under this soft Homa socket is gone or has moved")
            if int(args.id) != 0:
                self.n_failed += 1
                raise RpcFailed(int(args.id), int(args.completion_cookie), e)
            raise OSError(e, "homa recvmsg: %s" % os.strerror(e))
        nb = int(args.num_bpages)
        offs = [int(args.bpage_offsets[i]) for i in range(nb)]
        out = bytearray(n)
        pos = 0
        for off in offs:                           # every bpage is full but the last
            take = min(HOMA_BPAGE_SIZE, n - pos)
            out[pos:pos + take] = self._region[off:off + take]
            pos += take
        if pos != n:
            raise RuntimeError("homa recvmsg: message of %d bytes arrived in %d bpages holding %d" % (n, nb, pos))
        self._tls.owed = offs                      # ours until this thread's next recvmsg hands them back
        rid = int(args.id)
        is_req = bool(rid & 1)
        if is_req:
            self.n_req_in += 1
        else:
            self.n_rep_in += 1
        self.bytes_in += n
        peer = (socket.inet_ntoa(bytes(sa.sin_addr)), socket.ntohs(sa.sin_port))
        return Msg(rid, int(args.completion_cookie), peer, bytes(out), is_req)

    def counters(self) -> dict:
        return {"req_out": self.n_req_out, "req_in": self.n_req_in, "rep_out": self.n_rep_out, "rep_in": self.n_rep_in,
                "failed": self.n_failed, "bytes_out": self.bytes_out, "bytes_in": self.bytes_in, "port": self.port}

    def close(self) -> None:
        if self._closed:
            return                                   # once: the descriptor's number may belong to something else by now
        self._closed = True
        if self._libc.shutdown(self._fd, socket.SHUT_RDWR) != 0:   # wakes threads blocked in recvmsg (ESHUTDOWN)
            e = ctypes.get_errno()
            print("[HOMA] shutdown of the socket on port %d at close: %s (closing it anyway)" % (self.port, os.strerror(e)), flush=True)
        self._libc.close(self._fd)
