#!/usr/bin/env python3
# Copyright (C) 2016-2026 Fawcett Innovations LLC
# SPDX-License-Identifier: GPL-2.0-only
"""frognet_tuples -- the default Ribbit API (discovery's tuples) for applications that run off a FrogNet node.

The same functions, with the same signatures, as a FrogNet node's core/frognet_tuples.py -- put, get, get_all,
_values_raw, _delete_by_id, my_ip and the scope helpers -- so an application written against the node runs unchanged.
Underneath, every call is the default API's client library (libtuples.so / tuples.dll, ribbit/defaults/api): FNW1,
semantically compressed, to the vendor's own RAM host. There is no api.php, no MySQL and no FrogNet node.

Where the memory is:
  dbhost               a call's dbhost argument is the vendor's RAM host, "HOST:PORT"
  FROGNET_TUPLES_RAM   the default dbhost, "HOST:PORT". A node's names for its memory (databasehost.frognet,
                       databasehost_control.frognet) mean this; any other .frognet name is refused
  FROGNET_TUPLES_VENDOR  whose region: "comms" (comms-ram), "chat" (chat-ram), ...

No fallbacks: an unreachable RAM host raises StoreUnreachable, a refusal StoreRefusedLocally.
"""
import atexit, ctypes, json, os, sys, threading, time
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------------------------------- the library
def _load():
    here = os.path.dirname(os.path.abspath(__file__))
    name = "tuples.dll" if sys.platform == "win32" else ("libtuples.dylib" if sys.platform == "darwin" else "libtuples.so")
    dirs = [os.environ.get("FROGNET_TUPLES_LIB", ""), here, os.path.join(here, ".."), os.path.join(here, "build"), os.path.join(here, "..", "build")]
    for d in dirs:
        p = os.path.join(d, name) if d else ""
        if p and os.path.exists(p):
            return ctypes.CDLL(p)
    raise OSError("%s not found (set FROGNET_TUPLES_LIB to the directory holding it)" % name)

_lib = _load()
_c, _p, _e = ctypes.c_char_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)
_lib.tuples_open.restype = _p;      _lib.tuples_open.argtypes = [_c, _c, ctypes.c_int, _e]
_lib.tuples_close.argtypes = [_p]
_lib.tuples_put.restype = ctypes.c_int64; _lib.tuples_put.argtypes = [_p, _c, _c, _c, _c, ctypes.c_int, _e]
_lib.tuples_get_all.restype = _p;   _lib.tuples_get_all.argtypes = [_p, _c, ctypes.c_int, ctypes.c_double, ctypes.c_int, _c, _e]
_lib.tuples_remove.restype = ctypes.c_int;    _lib.tuples_remove.argtypes = [_p, _c, _c, _c, _e]
_lib.tuples_remove_id.restype = ctypes.c_int; _lib.tuples_remove_id.argtypes = [_p, ctypes.c_int64, _e]
_lib.tuples_my_ip.restype = _p;     _lib.tuples_my_ip.argtypes = [_p, _e]
_lib.tuples_free.argtypes = [_p]


# ------------------------------------------------------------------------------------------ the node's exceptions
class StoreError(Exception):
    pass
class StoreUnreachable(StoreError):
    pass
class StoreRefusedLocally(StoreError):
    pass
class StoreSlow(StoreError):
    pass
class StoreBroken(StoreError):
    pass


def _take(p) -> str:
    s = ctypes.cast(p, ctypes.c_char_p).value.decode(); _lib.tuples_free(p); return s

def _raise(err, what):
    msg = _take(err.value) if err.value else what
    low = msg.lower()
    raise (StoreUnreachable if ("unreachable" in low or "connect" in low or "closed" in low or "resolve" in low)
           else StoreRefusedLocally)(msg)


# --------------------------------------------------------------------------------------------- where the memory is
DEFAULT_DBHOST = os.environ.get("FROGNET_TUPLES_RAM", "")
VENDOR = os.environ.get("FROGNET_TUPLES_VENDOR", "")
SD_PREFIX = "SD:"
NODE_MEMORY_NAMES = ("databasehost.frognet", "databasehost_control.frognet", "databasehost", "databasehost_control")
SERVICE_ANY = ""
DEFAULT_TIMEOUT = None
STORE_TIMEOUT_S = 15.0

_clients: Dict[str, int] = {}
_clients_lock = threading.Lock()

def _client(dbhost: Optional[str]):
    where = dbhost or DEFAULT_DBHOST
    # A FrogNet node's names for its memory mean, off a node, the vendor's RAM host: code written for the node
    # passes them explicitly, and runs unchanged. Any other .frognet name is a node role that does not exist here.
    if where in NODE_MEMORY_NAMES:
        where = DEFAULT_DBHOST
    if not where or ":" not in where or where.endswith(".frognet"):
        raise StoreUnreachable("no RAM host: give dbhost as HOST:PORT or set FROGNET_TUPLES_RAM (got %r)" % (dbhost or where))
    if not VENDOR:
        raise StoreUnreachable("no vendor: set FROGNET_TUPLES_VENDOR (comms, chat, ...)")
    with _clients_lock:
        h = _clients.get(where)
        if h:
            return h
        host, port = where.rsplit(":", 1)
        err = ctypes.c_void_p()
        h = _lib.tuples_open(VENDOR.encode(), host.encode(), int(port), ctypes.byref(err))
        if not h:
            _raise(err, "cannot open %s-ram at %s" % (VENDOR, where))
        _clients[where] = h
        return h

def cleanup_owned() -> None:
    """Removes every tuple this process put with own=True (closing each client does it)."""
    with _clients_lock:
        for h in _clients.values():
            _lib.tuples_close(h)
        _clients.clear()

atexit.register(cleanup_owned)


# ----------------------------------------------------------------------------------------------------- the scopes
def my_ip() -> str:
    """This machine's address toward its RAM host."""
    h = _client(None)
    err = ctypes.c_void_p(); p = _lib.tuples_my_ip(h, ctypes.byref(err))
    if not p:
        _raise(err, "my_ip")
    return _take(p)

def host_scope(pid: Optional[int] = None) -> str:
    return "host:%s:%s" % (my_ip(), pid if pid is not None else os.getpid())

def node_scope() -> str:
    return "host:%s" % my_ip()

def role_scope(role: str) -> str:
    return "host:%s:%s" % (my_ip(), role)

def session_scope(session_id: str) -> str:
    return "session:%s" % session_id

def var_name(var: str, scope: str) -> str:
    return "%s%s.%s" % (SD_PREFIX, var, scope)


# ------------------------------------------------------------------------------------------------------- the API
def put(service: str, var: str, scope: str, value: Dict[str, Any], dbhost: str = DEFAULT_DBHOST,
        timeout=DEFAULT_TIMEOUT, own: bool = True) -> bool:
    """Write/refresh a variable, stamping ts. own=True: removed when this process exits; own=False: a refresh
    write that outlives the writer and ages out by freshness instead."""
    h = _client(dbhost)
    err = ctypes.c_void_p()
    if _lib.tuples_put(h, service.encode(), var.encode(), scope.encode(), json.dumps(value).encode(), int(bool(own)),
                       ctypes.byref(err)) < 0:
        _raise(err, "put %s/%s/%s" % (service, var, scope))
    return True

def _parse_name_like(name_like: Optional[str]):
    """discovery's patterns: 'SD:<var>.%' (one var, any scope) or an exact 'SD:<var>.<scope>'."""
    if name_like is None:
        return "", None
    n = name_like[len(SD_PREFIX):] if name_like.startswith(SD_PREFIX) else name_like
    var, _, scope = n.partition(".")
    return var, (None if scope in ("%", "") else scope)

def _rows(service: str, dbhost, fresh_s: int, wait_s: float, min_rows: int, name_like: Optional[str]):
    h = _client(dbhost)
    var, scope = _parse_name_like(name_like)
    err = ctypes.c_void_p()
    p = _lib.tuples_get_all(h, service.encode(), int(fresh_s or 0), float(wait_s or 0), int(min_rows or 0),
                            var.encode(), ctypes.byref(err))
    if not p:
        _raise(err, "get_all %s" % service)
    rows = json.loads(_take(p))
    if scope is not None:
        rows = [r for r in rows if r["scope"] == scope]
    return rows

def get_all(service: str, dbhost: str = DEFAULT_DBHOST, fresh_s: int = 0, timeout=DEFAULT_TIMEOUT,
            wait_s: float = 0.0, min_rows: int = 0, name_like: Optional[str] = None) -> List[Dict[str, Any]]:
    """Every variable of a service: rows {var, scope, name, addr, value, ts_env, age_s}. fresh_s filters on the
    memory's own clock; wait_s > 0 holds the read until min_rows exist."""
    out = []
    for r in _rows(service, dbhost, fresh_s, wait_s, min_rows, name_like):
        ts_env = int(r["ts_env"])
        out.append({"var": r["var"], "scope": r["scope"], "name": r["name"], "addr": r["addr"],
                    "value": r["value"], "ts_env": ts_env, "age_s": int(time.time()) - ts_env})
    return out

def get(service: str, var: str, dbhost: str = DEFAULT_DBHOST, fresh_s: int = 0, timeout=DEFAULT_TIMEOUT,
        wait_s: float = 0.0, min_rows: int = 0) -> List[Dict[str, Any]]:
    """Every instance of one variable, across scopes."""
    return get_all(service, dbhost, fresh_s=fresh_s, timeout=timeout, wait_s=wait_s, min_rows=min_rows,
                   name_like="%s%s.%%" % (SD_PREFIX, var))

def _values_raw(service: str, dbhost: str = DEFAULT_DBHOST, name_like: Optional[str] = None,
                timeout=DEFAULT_TIMEOUT, fresh_s: int = 0, wait_s: float = 0.0, min_rows: int = 0) -> List[Dict[str, Any]]:
    """The raw rows, in the node's shape: SensorID, SensorName, SensorType, SensorAddress, data, UpdatedAtEpoch."""
    return [{"SensorID": r["id"], "SensorName": r["name"], "SensorType": service, "SensorAddress": r["addr"],
             "data": r["value"], "jsonData": json.dumps(r["value"]), "UpdatedAtEpoch": int(r["ts_env"])}
            for r in _rows(service, dbhost, fresh_s, wait_s, min_rows, name_like)]

def _delete_by_id(dbhost: str, sensor_id: Any, timeout=DEFAULT_TIMEOUT) -> bool:
    h = _client(dbhost)
    err = ctypes.c_void_p()
    return _lib.tuples_remove_id(h, int(sensor_id), ctypes.byref(err)) == 0

def delete(service: str, var: str, scope: str, dbhost: str = DEFAULT_DBHOST) -> bool:
    h = _client(dbhost)
    err = ctypes.c_void_p()
    r = _lib.tuples_remove(h, service.encode(), var.encode(), scope.encode(), ctypes.byref(err))
    if r < 0:
        _raise(err, "delete %s/%s/%s" % (service, var, scope))
    return r == 1

def pool_stats():
    return {"reused": 0, "opened": len(_clients), "retried": 0}


def prune_self_stale_rows(service: str, var: str, keep_scope: str, dbhost: str = DEFAULT_DBHOST) -> int:
    """Delete THIS host's rows for service/var other than keep_scope: the bare host:<ip> scope and every
    host:<ip>:<suffix> variant, keeping exactly the one named. Only rows carrying this host's address are touched."""
    return _prune_self(service, var, var_name(var, keep_scope), dbhost)

def prune_self_stale_capability(role: str, var: str = "capability", dbhost: str = DEFAULT_DBHOST) -> int:
    """Delete THIS host's deprecated capability rows for a role, keeping only the current host:<ip>:<role> one."""
    return _prune_self(role, var, var_name(var, "host:%s:%s" % (my_ip(), role)), dbhost)

def _prune_self(service: str, var: str, keep: str, dbhost: str) -> int:
    base = "%s%s.host:%s" % (SD_PREFIX, var, my_ip())            # matches the bare scope and any :suffix
    n = 0
    for row in _values_raw(service, dbhost, name_like="%s%s.%%" % (SD_PREFIX, var)):
        nm = row["SensorName"]
        if nm != keep and (nm == base or nm.startswith(base + ":")) and _delete_by_id(dbhost, row["SensorID"]):
            n += 1
    return n
