"""frogtorch_ram.py -- FrogTorch's RAM: the memory every rank writes into and reads from.

[ALL_IN_THE_REGION_V1 - John 2026-11-11] "Make it all memory and role based, using whatever ram regions make sense."

Before this, every rank served its own tensors from its own process (tensor_plane.TensorServer), and every reader
opened a link to each peer and asked it: a conversation per (reader, writer), gloo's topology with reads in place of
sends. Because the bytes lived in the producer, the producer could not leave until its readers said they were done
(the `.done` acknowledgement, `linger`). That is messaging, and it is gone.

Now the memory is a service of its own, a FrogTorch RAM host, and the ranks are its clients:

  * A rank WRITES its own cells into a region: tensor cells (bytes, a generation) and control cells (JSON values).
    Every cell has one writer, its owner, and the owner's identity is part of its address.
  * A rank READS by holding a read on the cells it needs: the region parks the read and the owner's write answers it.
    There is no request to another rank, no acknowledgement, no polling and no retry.
  * The memory outlives the writer. A rank that has written its last cell may exit; its cells stay until it writes
    them again. Membership cells (life="session") are removed when the writer's session closes, which is how a
    departed rank stops being a member.

Which regions exist is a ROLE question (frogtorch_ram_client.Regions): every machine that runs this service writes
its capability tuple; every rank reads the capability family and applies the same deterministic placement, so every
rank reaches the same answer with no vote and no messages.

Wire: the framing tensor_plane already speaks (<!Q len><payload>), so TensorClient reads a region unchanged.

  tensor read   {name, gen, mode, have, want, inc, wait_s, verify}            -> SAME|DATA|GONE, as TensorServer
                (the digest is computed only when the reader asks to verify, or names a digest)
  tensor write  {"op":"tput", name, gen, inc, life?, ack?, acct?}  then the bytes -> nothing (OKAY {gen} only if ack)
                (AIConnect's objects; PyTorch's tensors are NOT here -- they stay in each rank's own memory,
                 memory_plane.py [THE_BITS_STAY_WHERE_THEY_ARE_V1], and this RAM holds where that memory is)
                (acct: the bytes are added to stats' by_acct[acct] -- how a writer that is never answered
                 learns what landed)
                (life="session": the cell goes when the writer's session closes -- AIConnect's objects)
  version       {"op":"version"}                                   -> OKAY {build}
  counters      {"op":"stats"}                                     -> OKAY {bytes_in, tputs, held_bytes, by_acct, ...}
  cell write    {"op":"cput", service, var, scope, value, life, ack?}          -> nothing (OKAY {id} only if ack)
  cell read     {"op":"cget", service, var?, min_rows, wait_s, after?}          -> OKAY {rows:[...]}
  cell remove   {"op":"cdel", id} / {"op":"cdelvar", service, var}             -> OKAY {removed}
  in the memory {"op":"ccas", service, var, expected, desired}                -> OKAY {value}
                {"op":"qpush", ..., ack?} -> nothing;  {"op":"qpop"|"qlen", ...} -> OKAY {...}
  every held read is held for the wait_s its caller gives, with no cap of the host's own

Run:  python3 -m agent_workload.tuplespace.frogtorch_ram --port <port> [--bind 0.0.0.0]
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import struct
import sys
import threading
import time
from typing import Any, Dict, List, Optional

from agent_workload.aiconnect.service import BULK_LEN_FMT

from .tensor_plane import (GONE_NEVER_PUBLISHED, GONE_SUPERSEDED, GONE_WAIT_EXPIRED,
                           READ_BY_IDENTITY, REPLY_DATA, REPLY_GONE, REPLY_SAME, _digest, _recv_frame, _send_frame)

BUILD = ("frogtorch_ram 20261112-r6 [ALL_IN_THE_REGION_V1] [SESSION_TENSORS_V1] [THE_ANNOUNCEMENT_IS_READ_BACK_V1] "
         "[HELD_FOR_THE_CALLERS_DEADLINE_V1] [DELIVERY_IS_COUNTED_WHERE_IT_LANDS_V1]")
#: Capability tuples live in the SystemRam (AI_READ_FIRST 10): every election reads capability there.
SYSTEM_RAM = os.environ.get("FROGTORCH_SYSTEM_RAM", "databasehost.frognet")
REPLY_OKAY = b"OKAY"


class _Key:
    """One address's value and the reads parked on it. The lock is the region's; the condition is this address's
    alone, so a write wakes only the reads waiting on what it wrote."""
    __slots__ = ("cond", "value")

    def __init__(self, lock):
        self.cond = threading.Condition(lock)
        self.value = None


class Region:
    """The memory: tensor cells by name, control cells by (service, var, scope)."""

    def __init__(self):
        self._lock = threading.Lock()
        self._tensors: Dict[str, _Key] = {}           # name -> (gen, bytes, inc, digest-or-None)
        self._services: Dict[str, _Key] = {}          # service -> {(var, scope): row}
        self._next_id = 0
        self._session_tensors: Dict[str, set] = {}    # [SESSION_TENSORS_V1] session -> names it holds
        self.tputs = 0
        self.bytes_in = self.bytes_out = 0
        self.bytes_by_acct: Dict[str, int] = {}       # [DELIVERY_IS_COUNTED_WHERE_IT_LANDS_V1] writer-named tallies
        self.same_replies = self.data_replies = 0

    # ---- tensors --------------------------------------------------------------------------------------------
    def _tkey(self, name: str) -> _Key:
        k = self._tensors.get(name)
        if k is None:
            k = self._tensors[name] = _Key(self._lock)
        return k

    def tput(self, name: str, gen: int, blob: bytes, inc, life: str = "data", session: str = "",
             acct: Optional[str] = None) -> None:
        with self._lock:
            k = self._tkey(name)
            k.value = (int(gen), blob, inc, None)
            self.bytes_in += len(blob)
            if acct:
                self.bytes_by_acct[acct] = self.bytes_by_acct.get(acct, 0) + len(blob)
            self.tputs += 1
            if life == "session":
                self._session_tensors.setdefault(session, set()).add(name)
            k.cond.notify_all()

    def stats(self) -> Dict[str, Any]:
        with self._lock:
            held = sum(len(k.value[1]) for k in self._tensors.values() if k.value is not None)
            return {"bytes_in": self.bytes_in, "bytes_out": self.bytes_out, "tputs": self.tputs,
                    "tensors": sum(1 for k in self._tensors.values() if k.value is not None), "held_bytes": held,
                    "by_acct": dict(self.bytes_by_acct)}

    def tget(self, name: str, want_gen, wait_s: float):
        """The current value, once it has reached want_gen (held until then, up to wait_s)."""
        deadline = time.monotonic() + wait_s      # [HELD_FOR_THE_CALLERS_DEADLINE_V1] no cap of the host's own
        with self._lock:
            k = self._tkey(name)
            while True:
                v = k.value
                if v is not None and (want_gen is None or v[0] >= int(want_gen)):
                    return v, True
                left = deadline - time.monotonic()
                if left <= 0:
                    return v, False
                k.cond.wait(left)

    def digest_of(self, name: str, v):
        """`v` with its digest, computed the first time it is asked for -- OUTSIDE the region's lock: a crc over a
        few MB under the one lock stalled every other write and read in the region for its length. Kept on the
        cell if the cell is still `v`."""
        if v[3] is not None:
            return v
        d = (v[0], v[1], v[2], _digest(v[1]))
        with self._lock:
            k = self._tensors.get(name)
            if k is not None and k.value is v:
                k.value = d
        return d

    # ---- control cells --------------------------------------------------------------------------------------
    def _skey(self, service: str) -> _Key:
        k = self._services.get(service)
        if k is None:
            k = self._services[service] = _Key(self._lock)
            k.value = {}
        return k

    def cput(self, service, var, scope, value, life, session) -> int:
        with self._lock:
            k = self._skey(service)
            self._next_id += 1
            k.value[(var, scope)] = {"id": self._next_id, "var": var, "scope": scope, "value": value,
                                     "t": time.time(), "life": life, "session": session}
            k.cond.notify_all()
            return self._next_id

    def cget(self, service, var, min_rows: int, wait_s: float, after: int = -1, unlike=None) -> List[Dict[str, Any]]:
        """Held until at least min_rows cells match and, with after >= 0, one of them is newer than after; with
        unlike (a list of ids), until the cells there are not exactly those -- a write OR a removal ends it."""
        deadline = time.monotonic() + wait_s      # [HELD_FOR_THE_CALLERS_DEADLINE_V1]
        with self._lock:
            k = self._skey(service)
            while True:
                rows = [r for (v, _s), r in k.value.items() if var is None or v == var]
                ready = (len(rows) >= min_rows and (after < 0 or any(r["id"] > after for r in rows))
                         and (unlike is None or {r["id"] for r in rows} != set(unlike)))
                left = deadline - time.monotonic()
                # [A_READ_THAT_EXPIRES_STILL_SAYS_WHAT_IS_THERE_V1] satisfied or not, the answer is what is there:
                # an expired read returned [] and a reader could not tell "nothing there" from "nothing new"
                if ready or wait_s <= 0 or left <= 0:
                    now = time.time()
                    return [{"id": r["id"], "var": r["var"], "scope": r["scope"], "value": r["value"],
                             "life": r["life"], "age_s": now - r["t"], "ts_env": r["t"]} for r in rows]
                k.cond.wait(left)

    def cdel(self, row_id: int) -> int:
        with self._lock:
            for k in self._services.values():
                for key, r in list(k.value.items()):
                    if r["id"] == row_id:
                        del k.value[key]
                        k.cond.notify_all()
                        return 1
        return 0

    # ---- operations that run IN the memory ([REGION_OPS_RUN_IN_THE_MEMORY_V1]) ----------------------------------
    # c10d's Store contract has primitives that are not "write your own cell": compare-and-set and a queue. They are
    # done here, in one call, against the memory itself -- the region's own API, as every vendor region does -- not
    # by a conversation between ranks.
    def ccas(self, service, var, expected, desired, session, life="data"):
        with self._lock:
            k = self._skey(service)
            rows = [r for (v, _s), r in k.value.items() if v == var]
            cur = max(rows, key=lambda r: r["id"])["value"] if rows else None
            if (cur is None and expected in (None, "")) or cur == expected:
                for key in [key for key in k.value if key[0] == var]:
                    del k.value[key]
                self._next_id += 1
                k.value[(var, "")] = {"id": self._next_id, "var": var, "scope": "", "value": desired,
                                      "t": time.time(), "life": life, "session": session}
                k.cond.notify_all()
                return desired
            return cur

    def cdelvar(self, service, var) -> int:
        with self._lock:
            k = self._skey(service)
            gone = [key for key in k.value if key[0] == var]
            for key in gone:
                del k.value[key]
            if gone:
                k.cond.notify_all()
            return len(gone)

    def qpush(self, service, var, value, session) -> int:
        with self._lock:
            k = self._skey(service)
            self._next_id += 1
            k.value[(var, "q%020d" % self._next_id)] = {"id": self._next_id, "var": var, "scope": "q",
                                                         "value": value, "t": time.time(), "life": "data",
                                                         "session": session}
            k.cond.notify_all()
            return self._next_id

    def qpop(self, service, var, wait_s: float):
        deadline = time.monotonic() + wait_s      # [HELD_FOR_THE_CALLERS_DEADLINE_V1]
        with self._lock:
            k = self._skey(service)
            while True:
                items = sorted(key for key in k.value if key[0] == var and k.value[key]["scope"] == "q")
                if items:
                    return k.value.pop(items[0])["value"], True
                left = deadline - time.monotonic()
                if left <= 0:
                    return None, False
                k.cond.wait(left)

    def qlen(self, service, var) -> int:
        with self._lock:
            k = self._skey(service)
            return sum(1 for key in k.value if key[0] == var and k.value[key]["scope"] == "q")

    def session_gone(self, session: str) -> None:
        """[WRITER_GONE_V1] A writer's session cells go with its session; its data cells stay."""
        with self._lock:
            for name in self._session_tensors.pop(session, ()):
                k = self._tensors.pop(name, None)
                if k is not None:
                    k.value = None
                    k.cond.notify_all()
            for k in self._services.values():
                gone = [key for key, r in k.value.items() if r["life"] == "session" and r["session"] == session]
                for key in gone:
                    del k.value[key]
                if gone:
                    k.cond.notify_all()


class RamServer:
    """One region on one port. A thread per client session; sessions are permanent connections."""

    def __init__(self, bind: str = "0.0.0.0", port: int = 0):
        self.region = Region()
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind((bind, port))
        self.sock.listen(1024)
        self.port = self.sock.getsockname()[1]
        self.accepts = 0          # connections ever accepted
        self.sessions = 0         # connections open now
        self._t = threading.Thread(target=self._accept, daemon=True, name="frogtorch-ram-%d" % self.port)
        self._t.start()

    def _accept(self):
        while True:
            try:
                conn, peer = self.sock.accept()
            except OSError:
                return
            conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            self.accepts += 1
            threading.Thread(target=self._serve, args=(conn, peer), daemon=True).start()

    def _serve(self, conn, peer):
        session = "%s:%d#%d" % (peer[0], peer[1], id(conn))
        R = self.region
        self.sessions += 1
        try:
            while True:
                req = json.loads(bytes(_recv_frame(conn)).decode())
                op = req.get("op")
                if op is None:
                    self._tensor_read(conn, req)
                elif op == "tput":
                    # the frame's own buffer, kept as it is: bytes() here was one more full copy of every write
                    blob = _recv_frame(conn)
                    R.tput(req["name"], req["gen"], blob, req.get("inc"), req.get("life", "data"), session,
                           req.get("acct"))
                    # [A_WRITE_IS_NOT_A_CONVERSATION_V1] a write is answered only if the writer asked to be told;
                    # FrogTorch's tensor writes stream, and the readers are what the cell landing releases
                    if req.get("ack", True):
                        _send_frame(conn, REPLY_OKAY, json.dumps({"gen": req["gen"]}).encode())
                elif op == "cput":
                    i = R.cput(req["service"], req["var"], req["scope"], req.get("value"),
                               req.get("life", "data"), session)
                    if req.get("ack", True):                # [A_WRITE_IS_NOT_A_CONVERSATION_V1]
                        _send_frame(conn, REPLY_OKAY, json.dumps({"id": i}).encode())
                elif op == "cget":
                    rows = R.cget(req["service"], req.get("var"), int(req.get("min_rows", 0)),
                                  float(req.get("wait_s", 0.0)), int(req.get("after", -1)), req.get("unlike"))
                    _send_frame(conn, REPLY_OKAY, json.dumps({"rows": rows}).encode())
                elif op == "cdel":
                    _send_frame(conn, REPLY_OKAY, json.dumps({"removed": R.cdel(int(req["id"]))}).encode())
                elif op == "ccas":
                    v = R.ccas(req["service"], req["var"], req.get("expected"), req.get("desired"), session,
                               req.get("life", "data"))
                    _send_frame(conn, REPLY_OKAY, json.dumps({"value": v}).encode())
                elif op == "cdelvar":
                    _send_frame(conn, REPLY_OKAY,
                                json.dumps({"removed": R.cdelvar(req["service"], req["var"])}).encode())
                elif op == "qpush":
                    i = R.qpush(req["service"], req["var"], req.get("value"), session)
                    if req.get("ack", True):                # [A_WRITE_IS_NOT_A_CONVERSATION_V1]
                        _send_frame(conn, REPLY_OKAY, json.dumps({"id": i}).encode())
                elif op == "qpop":
                    v, ok = R.qpop(req["service"], req["var"], float(req.get("wait_s", 0.0)))
                    _send_frame(conn, REPLY_OKAY, json.dumps({"value": v, "ok": ok}).encode())
                elif op == "qlen":
                    _send_frame(conn, REPLY_OKAY, json.dumps({"n": R.qlen(req["service"], req["var"])}).encode())
                elif op == "stats":
                    st = R.stats()
                    st["sessions"] = self.sessions
                    st["accepts"] = self.accepts
                    _send_frame(conn, REPLY_OKAY, json.dumps(st).encode())
                elif op == "version":
                    _send_frame(conn, REPLY_OKAY, json.dumps({"build": BUILD}).encode())
                else:
                    raise ValueError("unknown op %r" % op)
        except (ConnectionError, OSError):
            pass                                   # the session closed: what follows is its cells going with it
        except ValueError as e:                    # a request this host cannot serve: said, then the session ends
            print("[frogtorch-ram] session %s ended: %s" % (session, e), flush=True)
        finally:
            self.sessions -= 1
            R.session_gone(session)
            try:
                conn.close()
            except OSError:
                pass

    def _tensor_read(self, conn, req):
        """TensorServer's read contract, answered from the region."""
        R = self.region
        name, want_gen, mode = req.get("name"), req.get("gen"), req.get("mode", READ_BY_IDENTITY)
        have, want_digest, want_inc = req.get("have"), req.get("want"), req.get("inc")
        _t0 = time.monotonic()
        v, ok = R.tget(name, want_gen, float(req.get("wait_s") or 0.0))
        if v is None and not ok and float(req.get("wait_s") or 0.0) <= 0:
            _send_frame(conn, REPLY_GONE, json.dumps({"reason": GONE_NEVER_PUBLISHED}).encode())
            return
        if not ok:
            _send_frame(conn, REPLY_GONE, json.dumps({"reason": GONE_WAIT_EXPIRED,
                                                      "gen": None if v is None else v[0],
                                                      "want_gen": want_gen,
                                                      "waited_s": time.monotonic() - _t0}).encode())
            return
        need_digest = want_digest is not None or have is not None or bool(req.get("verify", False))
        gen, blob, inc, digest = R.digest_of(name, v) if need_digest else v
        if mode == READ_BY_IDENTITY and ((want_inc is not None and inc is not None and want_inc != inc)
                                         or (want_digest is not None and want_digest != digest)
                                         or (want_gen is not None and int(want_gen) != int(gen))):
            _send_frame(conn, REPLY_GONE, json.dumps({"reason": GONE_SUPERSEDED, "gen": gen,
                                                      "digest": digest, "inc": inc}).encode())
            return
        if have is not None and have == digest:
            R.same_replies += 1
            _send_frame(conn, REPLY_SAME, json.dumps({"gen": gen, "digest": digest, "inc": inc}).encode())
            return
        R.data_replies += 1
        R.bytes_out += len(blob)
        head = json.dumps({"gen": gen, "digest": digest, "inc": inc}).encode()
        _send_frame(conn, REPLY_DATA, struct.pack(BULK_LEN_FMT, len(head)), head, blob)

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


def _announce(port: int) -> None:
    """[CAPABILITY_ANNOUNCED_V1] This machine can hold FrogTorch regions: a capability tuple in the SystemRam,
    written by this service and gone with it."""
    from .frogtorch_ram_client import system_ram_tuples
    T = system_ram_tuples()
    me = T.my_ip()
    try:
        mem_mb = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") // (1 << 20)
    except (ValueError, OSError, AttributeError):
        mem_mb = 0
    T.put("frogtorch", "can", "%s:%d" % (me, port),
          {"host": me, "port": port, "mem_mb": int(mem_mb), "build": BUILD},
          dbhost=SYSTEM_RAM, own=True)
    # [THE_ANNOUNCEMENT_IS_READ_BACK_V1] A put the SystemRam did not keep (Seattle3B at boot: 503s while its
    # connections came up) left this machine's region invisible and its ranks placed elsewhere. Read it back; if it is
    # not there, exit non-zero -- systemd restarts this service in 2 s and the next start announces again.
    rows = T.get_all("frogtorch", dbhost=SYSTEM_RAM)
    if not any(r.get("var") == "can" and isinstance(r.get("value"), dict) and r["value"].get("host") == me
               and int(r["value"].get("port") or 0) == port for r in rows):
        raise SystemExit("[frogtorch-ram] the SystemRam (%s) did not keep this machine's announcement %s:%d; "
                         "exiting so systemd restarts and announces again" % (SYSTEM_RAM, me, port))
    print("[frogtorch-ram] announced %s:%d in the SystemRam (%s)" % (me, port, SYSTEM_RAM), flush=True)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="FrogTorch RAM host")
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--bind", default="0.0.0.0")
    ap.add_argument("--no-announce", action="store_true",
                    help="do not write the capability tuple (a test runs its regions from FROGTORCH_RAMS)")
    a = ap.parse_args(argv)
    srv = RamServer(a.bind, a.port)
    print("[frogtorch-ram] %s listening on %s:%d" % (BUILD, a.bind, srv.port), flush=True)
    if not a.no_announce:
        _announce(srv.port)
    threading.Event().wait()
    return 0


if __name__ == "__main__":
    sys.exit(main())
