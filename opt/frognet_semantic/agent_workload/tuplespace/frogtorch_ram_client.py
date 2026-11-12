"""frogtorch_ram_client.py -- a rank's side of FrogTorch's RAM ([ALL_IN_THE_REGION_V1], 2026-11-11).

Two things, and nothing else:

  Regions     which FrogTorch RAM hosts exist, in one deterministic order every rank computes the same way.
              From the capability family in the SystemRam (each frogtorch_ram service writes its own), or from
              FROGTORCH_RAMS="host:port,host:port" when the regions are configured rather than discovered.
  cells       put / get_all / get / remove on the one RAM, control(): the same calls, arguments and
              row shape (var, scope, value, id, age_s, ts_env) as core.frognet_tuples, so the backends read and
              write memory exactly as before -- only the memory is FrogTorch's own. A read with wait_s and min_rows
              is HELD by the region until enough cells are there; it is never re-asked.

Tensors are not here ([THE_BITS_STAY_WHERE_THEY_ARE_V1]): they stay in the memory of the rank that made them
(memory_plane.MemoryPlane, in its own process). The one RAM -- the region on the machine `databasehost.frognet`
resolves to, control() -- holds membership and each rank's reference (where its memory is); Placement reads them.

No fallbacks. A region that cannot be reached raises; a held read that runs out of time returns what is there and the
caller decides it has failed.
"""
from __future__ import annotations

import json
import os
import sys
import socket
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from .tensor_plane import REPLY_GONE, _recv_frame, _send_frame

DEFAULT_DBHOST = "frogtorch"           # accepted and ignored: callers pass the dbhost they always passed
SD_PREFIX = ""
SYSTEM_RAM = os.environ.get("FROGTORCH_SYSTEM_RAM", "databasehost.frognet")
REGIONS_WAIT_S = float(os.environ.get("FROGTORCH_REGIONS_WAIT_S", "300"))
CALL_TIMEOUT_S = float(os.environ.get("FROGTORCH_CALL_TIMEOUT_S", "330"))


def system_ram_tuples():
    """core.frognet_tuples, for the SystemRam. [CORE_ON_THE_PATH_V1] Its held read (frognet_held, the node's api.php
    hold) is imported by bare name, as the node's daemons import it with core/ on sys.path; a FrogTorch process
    imports core as a package, so core/ is put on the path here, once, before the first held read."""
    from core import frognet_tuples as T
    d = os.path.dirname(os.path.abspath(T.__file__))
    if d not in sys.path:
        sys.path.append(d)
    return T


def my_ip() -> str:
    T = system_ram_tuples()
    return T.my_ip()


def host_scope(pid: Optional[int] = None) -> str:
    return "%s:%d" % (my_ip(), os.getpid() if pid is None else pid)


# ---------------------------------------------------------------------------------------------------- regions
def _sort_key(hp: Tuple[str, int]):
    try:
        ip = tuple(int(x) for x in hp[0].split("."))
    except ValueError:
        ip = (999,)
    return (ip, hp[1])


def discover_regions() -> List[Tuple[str, int]]:
    cfg = os.environ.get("FROGTORCH_RAMS", "").strip()
    if cfg:
        out = []
        for part in cfg.split(","):
            h, p = part.strip().rsplit(":", 1)
            out.append((h, int(p)))
        return sorted(out, key=_sort_key)
    T = system_ram_tuples()
    rows = T.get_all("frogtorch", dbhost=SYSTEM_RAM, wait_s=REGIONS_WAIT_S, min_rows=1)
    out = sorted({(r["value"]["host"], int(r["value"]["port"])) for r in rows
                  if r.get("var") == "can" and isinstance(r.get("value"), dict)}, key=_sort_key)
    if not out:
        raise ConnectionError("frogtorch: no FrogTorch RAM host has announced itself in the SystemRam (%s) within "
                              "%.0fs. Start frogtorch_ram on at least one machine." % (SYSTEM_RAM, REGIONS_WAIT_S))
    return out


# ---------------------------------------------------------------------------------------------------- sessions
class _Session:
    """One permanent connection to one region, used by one thread at a time."""

    def __init__(self, host: str, port: int):
        self.host, self.port = host, port
        try:
            self.sk = socket.create_connection((host, port), timeout=CALL_TIMEOUT_S)
        except OSError as e:
            raise ConnectionError("frogtorch: cannot reach the RAM host at %s:%d (%s)" % (host, port, e)) from e
        self.sk.settimeout(CALL_TIMEOUT_S)
        self.sk.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

    def send(self, head: Dict[str, Any], payload=None) -> None:
        """A request nobody answers: written to the connection and done. A failure shows as the connection failing."""
        _send_frame(self.sk, json.dumps(head).encode())
        if payload is not None:
            _send_frame(self.sk, payload)

    def call(self, head: Dict[str, Any], payload=None):
        # the connection must outlast the hold this request asks for ([HELD_FOR_THE_CALLERS_DEADLINE_V1])
        self.sk.settimeout(max(CALL_TIMEOUT_S, float(head.get("wait_s") or 0.0) + 30.0))
        if payload is None:
            _send_frame(self.sk, json.dumps(head).encode())
        else:
            _send_frame(self.sk, json.dumps(head).encode())
            _send_frame(self.sk, payload)
        reply = bytes(_recv_frame(self.sk))
        tag, body = reply[:4], reply[4:]
        if tag == REPLY_GONE:
            raise ConnectionError("frogtorch: RAM host %s:%d refused %s: %s"
                                  % (self.host, self.port, head.get("op"), body[:200]))
        return json.loads(body.decode()) if body else {}

    def close(self) -> None:
        """Ends the session; a read held on it in another thread returns with an error at once (shutdown, then
        close -- close alone does not wake a thread blocked in recv)."""
        try:
            self.sk.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self.sk.close()
        except OSError:
            pass


class Ram:
    """The regions, seen from one process."""

    def __init__(self, regions: Optional[List[Tuple[str, int]]] = None):
        self.regions = regions if regions is not None else discover_regions()
        self._local = threading.local()
        # Session cells (membership) are held by ONE session per process, so they go when the process goes.
        self._member_lock = threading.Lock()
        self._member_session: Optional[_Session] = None

    def control(self) -> Tuple[str, int]:
        """[CONTROL_LIVES_WITH_THE_ROLE_V1] The region every rank agrees on for membership, the store and the gate:
        the one on the machine that holds the SystemRam role. It was regions[0] -- whichever machine sorted lowest
        among the regions THIS process happened to see, so two ranks starting a moment apart could pick different
        control regions and never meet. A role every rank resolves the same way has no such race.
        With FROGTORCH_RAMS (regions configured, not discovered) it is the first one configured."""
        c = getattr(self, "_control", None)
        if c is not None:
            return c
        if os.environ.get("FROGTORCH_RAMS", "").strip():
            c = self.regions[0]
        else:
            try:
                role_ip = socket.gethostbyname(SYSTEM_RAM)
            except OSError as e:
                raise ConnectionError("frogtorch: cannot resolve the SystemRam role %s (%s)" % (SYSTEM_RAM, e)) from e
            mine = [hp for hp in self.regions if hp[0] == role_ip]
            if not mine:
                raise RuntimeError("frogtorch: the SystemRam role %s is %s, and that machine has no FrogTorch RAM "
                                   "announced (announced: %s); on it: systemctl restart frogtorch-ram"
                                   % (SYSTEM_RAM, role_ip, " ".join("%s:%d" % hp for hp in self.regions)))
            c = mine[0]
        self._control = c
        return c

    def session(self, hp: Tuple[str, int]) -> _Session:
        d = getattr(self._local, "s", None)
        if d is None:
            d = self._local.s = {}
        s = d.get(hp)
        if s is None:
            s = d[hp] = _Session(*hp)
        return s

    # ---- the cell calls, core.frognet_tuples' shape --------------------------------------------------------
    def put(self, service, var, scope, value, dbhost=None, timeout=None, own=False, life="data") -> bool:
        # [A_WRITE_IS_NOT_A_CONVERSATION_V1] A put is written and done: nothing waits for the region to say it has
        # it. Whoever needs the cell holds a read on it; a write the region could not take ends the session.
        head = {"op": "cput", "service": service, "var": var, "scope": scope, "value": value, "life": life,
                "ack": False}
        if life == "session":
            with self._member_lock:
                if self._member_session is None:
                    self._member_session = _Session(*self.control())
                self._member_session.send(head)
            return True
        self.session(self.control()).send(head)
        return True

    def get_all(self, service, dbhost=None, fresh_s=0, timeout=None, wait_s=0.0, min_rows=0, name_like=None,
                var=None, after=-1, unlike=None) -> List[Dict[str, Any]]:
        r = self.session(self.control()).call({"op": "cget", "service": service, "var": var,
                                                "min_rows": int(min_rows), "wait_s": float(wait_s),
                                                "after": int(after), "unlike": unlike})
        rows = r.get("rows", [])
        if fresh_s:
            rows = [x for x in rows if x.get("age_s", 0) <= fresh_s]
        return rows

    def get(self, service, var, dbhost=None, fresh_s=0, timeout=None, wait_s=0.0, min_rows=0):
        return self.get_all(service, fresh_s=fresh_s, wait_s=wait_s, min_rows=min_rows, var=var)

    def remove(self, row_id: int) -> int:
        return int(self.session(self.control()).call({"op": "cdel", "id": int(row_id)}).get("removed", 0))

    def op(self, name: str, **kw) -> Dict[str, Any]:
        """One of the region's own operations, run in the memory (compare-and-set, the queue). A cell an op writes
        with life="session" is held by this process's member session, so it goes when the process goes."""
        kw["op"] = name
        if name == "qpush":
            kw["ack"] = False                       # [A_WRITE_IS_NOT_A_CONVERSATION_V1] a push is a write
        talk = (lambda sess: (sess.send(kw), {})[1]) if kw.get("ack") is False else (lambda sess: sess.call(kw))
        if kw.get("life") == "session":
            with self._member_lock:
                if self._member_session is None:
                    self._member_session = _Session(*self.control())
                return talk(self._member_session)
        return talk(self.session(self.control()))

    # ---- tensors --------------------------------------------------------------------------------------------
    def tput(self, hp: Tuple[str, int], name: str, gen: int, blob, inc=None, ack: bool = True,
             life: str = "data") -> None:
        head = {"op": "tput", "name": name, "gen": int(gen), "inc": inc, "life": life}
        if ack:
            self.session(hp).call(head, blob)
        else:
            head["ack"] = False
            self.session(hp).send(head, blob)


_RAM: Optional[Ram] = None
_RAM_LOCK = threading.Lock()


def ram() -> Ram:
    global _RAM
    with _RAM_LOCK:
        if _RAM is None:
            _RAM = Ram()
        return _RAM


# Module-level calls, so `from . import frogtorch_ram_client as T` drops in where core.frognet_tuples was.
def put(service, var, scope, value, dbhost=None, timeout=None, own=False, life="data"):
    return ram().put(service, var, scope, value, life=life)


def get_all(service, dbhost=None, fresh_s=0, timeout=None, wait_s=0.0, min_rows=0, name_like=None, var=None,
            after=-1, unlike=None):
    return ram().get_all(service, fresh_s=fresh_s, wait_s=wait_s, min_rows=min_rows, var=var, after=after,
                         unlike=unlike)


def get(service, var, dbhost=None, fresh_s=0, timeout=None, wait_s=0.0, min_rows=0):
    return ram().get(service, var, fresh_s=fresh_s, wait_s=wait_s, min_rows=min_rows)


def _delete_by_id(dbhost, row_id) -> int:
    return ram().remove(row_id)


# ---------------------------------------------------------------------------------------------------- placement
class Placement:
    """Which region a rank's cells live in. Derived by every rank from the members' own cells."""

    def __init__(self, r: Ram, group: str, rank: int, size: int, timeout_s: float, port: int,
                 build: Optional[str] = None):
        self.ram, self.rank, self.size = r, rank, size
        svc = "frogtorch.members.%s" % group
        me = os.environ.get("FROGTORCH_MEMBER_IP") or my_ip()     # a test running "machines" on loopback names them
        # [THE_BITS_STAY_WHERE_THEY_ARE_V1] the reference: where this rank's memory is, written once, in the one RAM
        r.put(svc, "r%d" % rank, "r%d" % rank, {"ip": me, "port": int(port), "pid": os.getpid(), "build": build},
              life="session")
        rows = r.get_all(svc, wait_s=timeout_s, min_rows=size)
        ips: Dict[int, Tuple[str, int]] = {}
        self.builds: Dict[int, Optional[str]] = {}
        for row in rows:
            v = row.get("var", "")
            if v.startswith("r"):
                ips[int(v[1:])] = (row["value"]["ip"], int(row["value"].get("port") or 0))
                self.builds[int(v[1:])] = row["value"].get("build")
        missing = sorted(set(range(size)) - set(ips))
        if missing:
            raise RuntimeError("frogtorch: ranks %s never wrote their member cell within %.0fs"
                               % (missing, timeout_s))
        self.of_rank = ips

    def region(self, rank: int) -> Tuple[str, int]:
        """Where rank's memory is: its MemoryPlane, in its own process."""
        return self.of_rank[rank]


def linger(r: Ram, group: str, rank: int, size: int, timeout_s: float) -> List[int]:
    """[A_PEER_IS_NOT_GONE_UNTIL_EVERY_PEER_SAYS_SO_V1] A rank's tensors are in its own memory, so it must not take
    that memory away while a peer may still read it. It writes its done cell beside its member cell and holds ONE
    read on the members family until every rank has either written done or is gone (its session-held member cell
    went with it). Returns the ranks still not done when the read ran out of time (empty when all are)."""
    svc = "frogtorch.members.%s" % group
    r.put(svc, "d%d" % rank, "d%d" % rank, {"t": time.time()}, life="session")
    deadline = time.monotonic() + timeout_s
    ids = None
    while True:
        rows = r.get_all(svc, wait_s=0.0 if ids is None else max(0.0, deadline - time.monotonic()), unlike=ids)
        have = {row.get("var", "") for row in rows}
        waiting = [k for k in range(size) if "d%d" % k not in have and "r%d" % k in have]
        if not waiting or time.monotonic() >= deadline:
            return waiting
        ids = sorted(row["id"] for row in rows)
