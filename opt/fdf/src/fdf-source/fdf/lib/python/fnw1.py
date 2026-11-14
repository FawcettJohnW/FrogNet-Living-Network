################################################################
#  Copyright (C) 2016-2026 Fawcett Innovations LLC             #
#  SPDX-License-Identifier: GPL-2.0-only                       #
################################################################
"""fnw1 -- [RAM_LIBRARY_PY_V1] one FNW1 session to a RAM host (fdf-ram), in pure Python: the Python twin of the Java
library's RamSession (opt/fdf/src/frognet-ram-java), for programs that need what plain HTTP cannot give -- a tuple that
goes with its writer: written with own=True on a session, it is written {} by the host when the session ends
([OWNED_CELL_GOES_WITH_THE_SESSION_V1]; the host refuses own on plain HTTP).

Two outbound TCP connections: the request socket (HELLO token, then REQ_RAW frames; a request's sequence is its order
on this socket from 0) and the return socket (HELLO RETURN:token, answered by the host's HELLO; replies arrive tagged
with that sequence). Frame = [len u32 BE][body]; body = "FNW1" + op + ...; REQ_RAW 0x03 = hash16 + [len u32] +
{"method","path","body"}; RESP_RAW 0x14 = same_id16 + [len u32] + [status u16][hlen u16][header][body]; OP_ERROR 0x30.
Nothing falls back: a lost connection fails every call in flight and every call after it.

  s = Session("10.170.170.1", 23827)
  s.write("openeoc-who", "m", "dev1", {...}, own=True)       posted, never waited for
  s.read("openeoc-who", "m")                                  -> cells
  s.close()                                              every own tuple of this session goes ({} at the host)
"""
import json, secrets, socket, struct, threading, urllib.parse

API = "/FawcettInnovations.RINA.ram_interface.php"
MAGIC = b"FNW1"
REQ_RAW, RESP_RAW, OP_ERROR, OP_HELLO = 0x03, 0x14, 0x30, 0x50
M64 = (1 << 64) - 1


class Gone(Exception):
    """the session is over (or never opened): say why, open another"""


class Refused(Exception):
    """the host answered ok:false, or an error"""


def hash16(b):
    """ram_host.cpp's hash16: two 64-bit FNV-style lanes, big-endian (the host keys its request cache with it)"""
    a, c = 1469598103934665603, 0x9E3779B97F4A7C15
    for x in b:
        a = ((a ^ x) * 1099511628211) & M64
        c = ((c ^ (x + 0x5b)) * 0x100000001B3) & M64
        c ^= c >> 29
    return a.to_bytes(8, "big") + c.to_bytes(8, "big")


def _frame(body):
    return struct.pack(">I", len(body)) + body


def _recv(sock):
    h = _exact(sock, 4)
    if h is None:
        return None
    n = struct.unpack(">I", h)[0]
    if n < 5 or n > (64 << 20):
        raise OSError("bad frame length %d" % n)
    return _exact(sock, n)


def _exact(sock, n):
    b = bytearray()
    while len(b) < n:
        d = sock.recv(n - len(b))
        if not d:
            return None
        b += d
    return bytes(b)


class Session:
    def __init__(self, host, port, api=API, timeout=5.0):
        self.host, self.port, self.api = host, int(port), api
        self.dead, self._seq, self._wait = None, 0, {}
        self._lock, self._send = threading.Lock(), threading.Lock()
        tok = secrets.token_hex(8).encode()
        try:
            self.req = socket.create_connection((host, self.port), timeout=timeout)
            self.req.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            self.req.sendall(_frame(MAGIC + bytes([OP_HELLO, len(tok)]) + tok))
            self.ret = socket.create_connection((host, self.port), timeout=timeout)
            rt = b"RETURN:" + tok
            self.ret.sendall(_frame(MAGIC + bytes([OP_HELLO, len(rt)]) + rt))
            f = _recv(self.ret)
            if not f or f[:4] != MAGIC or f[4] != OP_HELLO:
                raise OSError("did not accept the session (no HELLO on the return socket)")
            self.ret.settimeout(None); self.req.settimeout(None)
        except OSError as e:
            raise Gone("%s:%d: %s" % (host, self.port, e))
        threading.Thread(target=self._replies, daemon=True).start()

    def _replies(self):
        why = "the return socket closed"
        try:
            while True:
                f = _recv(self.ret)
                if f is None:
                    break
                if len(f) < 9 or f[4:8] != MAGIC:
                    why = "a reply that is not FNW1"; break
                s, op = struct.unpack(">I", f[:4])[0], f[8]
                with self._lock:
                    slot = self._wait.pop(s, None)
                if slot is None:
                    continue                                    # a posted write, or a caller that gave up
                if op == RESP_RAW and len(f) >= 33:
                    n = struct.unpack(">I", f[25:29])[0]; p = 29
                    status, hlen = struct.unpack(">HH", f[p:p + 4])
                    slot["body"] = f[p + 4 + hlen:p + n].decode("utf-8", "replace"); slot["status"] = status
                elif op == OP_ERROR and len(f) >= 11:
                    slot["error"] = "host error %d: %s" % (struct.unpack(">H", f[9:11])[0], f[11:].decode("utf-8", "replace"))
                else:
                    slot["error"] = "unexpected reply op 0x%x" % op
                slot["ev"].set()
        except OSError as e:
            why = str(e)
        self._fail(why)

    def _fail(self, why):
        if self.dead is None:
            self.dead = "%s:%d: %s" % (self.host, self.port, why)
        with self._lock:
            for slot in self._wait.values():
                slot["error"] = self.dead; slot["ev"].set()
            self._wait.clear()

    def _frame_for(self, method, path, body):
        r = json.dumps({"method": method, "path": path, "body": body or ""}).encode()
        return _frame(MAGIC + bytes([REQ_RAW]) + hash16(r) + struct.pack(">I", len(r)) + r)

    def call(self, method, path, body="", timeout=15.0):
        if self.dead:
            raise Gone(self.dead)
        slot = {"ev": threading.Event()}
        with self._send:
            mine = self._seq; self._seq += 1
            with self._lock:
                self._wait[mine] = slot
            try:
                self.req.sendall(self._frame_for(method, path, body))
            except OSError as e:
                self._fail("send: %s" % e); raise Gone(self.dead)
        if not slot["ev"].wait(timeout):
            with self._lock:
                self._wait.pop(mine, None)
            raise Gone("%s:%d: no reply in %.0f s to %s %s" % (self.host, self.port, timeout, method, path))
        if "error" in slot:
            if self.dead and slot["error"] == self.dead:
                raise Gone(slot["error"])
            raise Refused(slot["error"])
        j = json.loads(slot["body"])
        if not j.get("ok"):
            raise Refused("%s %s: %s" % (method, path, j.get("error")))
        return j

    def post(self, method, path, body=""):
        """[KART_POSTED_WRITE_V1] sent, never waited for (the host's answer finds no slot and is dropped)"""
        if self.dead:
            raise Gone(self.dead)
        with self._send:
            self._seq += 1
            try:
                self.req.sendall(self._frame_for(method, path, body))
            except OSError as e:
                self._fail("send: %s" % e); raise Gone(self.dead)

    def write(self, service, variable, instance, bag, own=False, wait=False):
        b = {"service": service, "variable": variable, "instance": instance, "bag": bag}
        if own:
            b["own"] = True
        if wait:
            return self.call("POST", self.api + "?op=write", json.dumps(b))
        self.post("POST", self.api + "?op=write", json.dumps(b))

    def read(self, service, variable="", instance="", after=-1, wait_s=0.0):
        q = {"op": "read", "service": service}
        if variable:
            q["variable"] = variable
        if instance:
            q["instance"] = instance
        if after >= 0:
            q["after"] = after
        if wait_s:
            q["wait_s"] = wait_s
        j = self.call("GET", self.api + "?" + urllib.parse.urlencode(q), "", 15.0 + wait_s)
        return j.get("cells", j.get("rows", []))

    def close(self):
        for s in (self.req, self.ret):
            try:
                s.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                s.close()
            except OSError:
                pass
        self._fail("closed")
