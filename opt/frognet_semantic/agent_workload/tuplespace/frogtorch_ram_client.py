"""frogtorch_ram_client.py -- a rank's side of FrogTorch's RAM ([ALL_IN_THE_REGION_V1], 2026-11-11).

Three things, and nothing else:

  Regions     which FrogTorch RAM hosts exist, in one deterministic order every rank computes the same way.
              From the capability family in the SystemRam (each frogtorch_ram service writes its own), or from
              FROGTORCH_RAMS="host:port,host:port" when the regions are configured rather than discovered.
  cells       put / get_all / get / remove on the CONTROL region (the first region): the same calls, arguments and
              row shape (var, scope, value, id, age_s, ts_env) as core.frognet_tuples, so the backends read and
              write memory exactly as before -- only the memory is FrogTorch's own. A read with wait_s and min_rows
              is HELD by the region until enough cells are there; it is never re-asked.
  RegionPlane what a rank publishes tensors through. Same publish() as tensor_plane.TensorServer, but the bytes go
              INTO a region, addressed by name and owner, and the call returns as soon as they are queued. A reader
              holds a read on the region, never on the producer.

Placement ([A_CELL_LIVES_WITH_ITS_OWNER_V1]): a rank's cells go to the region on the rank's own machine, so the
traffic is the same shape it was when each rank served its own -- but the memory is a service, not the rank. When a
machine has no region of its own, rank r's cells go to region r mod (number of regions). Every rank derives this from
the members' own cells: no rank is told.

No fallbacks. A region that cannot be reached raises; a held read that runs out of time returns what is there and the
caller decides it has failed.
"""
from __future__ import annotations

import json
import os
import queue
import socket
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from .tensor_plane import REPLY_GONE, _recv_frame, _send_frame, tensor_bytes, tensor_view_bytes

DEFAULT_DBHOST = "frogtorch"           # accepted and ignored: callers pass the dbhost they always passed
SD_PREFIX = ""
SYSTEM_RAM = os.environ.get("FROGTORCH_SYSTEM_RAM", "databasehost.frognet")
REGIONS_WAIT_S = float(os.environ.get("FROGTORCH_REGIONS_WAIT_S", "300"))
CALL_TIMEOUT_S = float(os.environ.get("FROGTORCH_CALL_TIMEOUT_S", "330"))


def my_ip() -> str:
    from core import frognet_tuples as T
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
    from core import frognet_tuples as T
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

    def call(self, head: Dict[str, Any], payload=None):
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


class Ram:
    """The regions, seen from one process."""

    def __init__(self, regions: Optional[List[Tuple[str, int]]] = None):
        self.regions = regions if regions is not None else discover_regions()
        self._local = threading.local()
        # Session cells (membership) are held by ONE session per process, so they go when the process goes.
        self._member_lock = threading.Lock()
        self._member_session: Optional[_Session] = None

    def control(self) -> Tuple[str, int]:
        return self.regions[0]

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
        head = {"op": "cput", "service": service, "var": var, "scope": scope, "value": value, "life": life}
        if life == "session":
            with self._member_lock:
                if self._member_session is None:
                    self._member_session = _Session(*self.control())
                self._member_session.call(head)
            return True
        self.session(self.control()).call(head)
        return True

    def get_all(self, service, dbhost=None, fresh_s=0, timeout=None, wait_s=0.0, min_rows=0, name_like=None,
                var=None, after=-1) -> List[Dict[str, Any]]:
        r = self.session(self.control()).call({"op": "cget", "service": service, "var": var,
                                                "min_rows": int(min_rows), "wait_s": float(wait_s),
                                                "after": int(after)})
        rows = r.get("rows", [])
        if fresh_s:
            rows = [x for x in rows if x.get("age_s", 0) <= fresh_s]
        return rows

    def get(self, service, var, dbhost=None, fresh_s=0, timeout=None, wait_s=0.0, min_rows=0):
        return self.get_all(service, fresh_s=fresh_s, wait_s=wait_s, min_rows=min_rows, var=var)

    def remove(self, row_id: int) -> int:
        return int(self.session(self.control()).call({"op": "cdel", "id": int(row_id)}).get("removed", 0))

    def op(self, name: str, **kw) -> Dict[str, Any]:
        """One of the region's own operations, run in the memory (compare-and-set, the queue)."""
        kw["op"] = name
        return self.session(self.control()).call(kw)

    # ---- tensors --------------------------------------------------------------------------------------------
    def tput(self, hp: Tuple[str, int], name: str, gen: int, blob, inc=None) -> None:
        self.session(hp).call({"op": "tput", "name": name, "gen": int(gen), "inc": inc}, blob)


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
            after=-1):
    return ram().get_all(service, fresh_s=fresh_s, wait_s=wait_s, min_rows=min_rows, var=var, after=after)


def get(service, var, dbhost=None, fresh_s=0, timeout=None, wait_s=0.0, min_rows=0):
    return ram().get(service, var, fresh_s=fresh_s, wait_s=wait_s, min_rows=min_rows)


def _delete_by_id(dbhost, row_id) -> int:
    return ram().remove(row_id)


# ---------------------------------------------------------------------------------------------------- placement
class Placement:
    """Which region a rank's cells live in. Derived by every rank from the members' own cells."""

    def __init__(self, r: Ram, group: str, rank: int, size: int, timeout_s: float):
        self.ram, self.rank, self.size = r, rank, size
        svc = "frogtorch.members.%s" % group
        me = os.environ.get("FROGTORCH_MEMBER_IP") or my_ip()     # a test running "machines" on loopback names them
        r.put(svc, "r%d" % rank, "r%d" % rank, {"ip": me, "pid": os.getpid()}, life="session")
        rows = r.get_all(svc, wait_s=timeout_s, min_rows=size)
        ips: Dict[int, str] = {}
        for row in rows:
            v = row.get("var", "")
            if v.startswith("r"):
                ips[int(v[1:])] = row["value"]["ip"]
        missing = sorted(set(range(size)) - set(ips))
        if missing:
            raise RuntimeError("frogtorch: ranks %s never wrote their member cell within %.0fs"
                               % (missing, timeout_s))
        by_host: Dict[str, Tuple[str, int]] = {}
        for hp in r.regions:
            by_host.setdefault(hp[0], hp)
        self.of_rank = {k: by_host.get(ip, r.regions[k % len(r.regions)]) for k, ip in ips.items()}

    def region(self, rank: int) -> Tuple[str, int]:
        return self.of_rank[rank]


# ---------------------------------------------------------------------------------------------------- the plane
class RegionPlane:
    """Publish tensors into the regions. tensor_plane.TensorServer's publish(), aimed at memory.

    A tensor cell's address is `<name>@<owner>`: names repeat across ranks (every rank publishes "<group>.arr.<seq>"),
    and in a shared memory the owner is part of the address. Writes to one region are queued in order on one writer
    thread, so publish() returns at once and a later write can never overtake an earlier one to the same region.
    The bytes are frozen (copied) at publish: the caller may reuse its buffer the moment publish() returns.
    """

    def __init__(self, r: Ram, owner: int, where=None):
        self.ram, self.owner, self.where = r, owner, where
        self.region: Optional[Tuple[str, int]] = None      # this owner's region, once placement is known
        self.port = 0
        self.states_offered = 0
        self.states_materialised = 0
        self._queues: Dict[Tuple[str, int], "queue.Queue"] = {}
        self._qlock = threading.Lock()
        self.fault: Optional[BaseException] = None

    @staticmethod
    def key(name: str, owner: int) -> str:
        return "%s@%d" % (name, owner)

    def _writer(self, hp):
        q = self._queues[hp]
        while True:
            item = q.get()
            if item is None:
                return
            name, gen, blob, inc = item
            try:
                self.ram.tput(hp, name, gen, blob, inc)
            except BaseException as e:             # the next publish raises it: a lost write is never silent
                self.fault = e

    def publish(self, name: str, generation: int, tensor, incarnation=None, copy=True, region=None) -> None:
        if self.fault is not None:
            raise ConnectionError("frogtorch: an earlier write to a region failed: %r" % (self.fault,))
        hp = region or self.region or self.where()
        blob = tensor_bytes(tensor)                # frozen now; see the class note
        with self._qlock:
            q = self._queues.get(hp)
            if q is None:
                q = self._queues[hp] = queue.Queue()
                threading.Thread(target=self._writer, args=(hp,), daemon=True,
                                 name="region-writer-%s:%d" % hp).start()
        q.put((self.key(name, self.owner), int(generation), blob, incarnation))
        self.states_offered += 1
