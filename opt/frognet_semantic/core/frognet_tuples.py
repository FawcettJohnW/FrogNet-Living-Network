#!/usr/bin/env python3
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
#  This program is distributed in the hope that it will be     #
#  useful, but WITHOUT ANY WARRANTY; without even the implied  #
#  warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR     #
#  PURPOSE.  See the GNU General Public License for details.   #
#                                                              #
#  See COPYRIGHT and LICENSE at the root of this tree.         #
################################################################
"""
frognet_tuples.py - variables in the transient DB, the UnREST way.

Exchange memory, not messages. A variable is a tuple addressed
<Service><VarName><Session/Host> and read by associative query. Nobody sends
anybody anything: a node writes its variable, every node reads the converged
value on its next read. The transient floats (databasehost.frognet re-elects to
the highest IP), so writers re-resolve the host and re-assert on a heartbeat;
stale variables age out because the writer stopped re-asserting.

Mapping onto the real api.php sensor schema (SensorName is the unique key; we
own the JSON contents):

    SensorType    = <Service>                 e.g. "communicator"
    SensorName    = <VarName>.<scope>          scope = host:<ip>:<pid>  (node-scoped)
                                                       session:<id>     (call-scoped)
    SensorAddress = writer's 10/8 IP           ("who reported it")
    jsonData      = the value (a blob we define)

Write : POST api.php?entity=sensor_data&action=upsert_by_name  (atomic resolve-or-create)
Read  : GET  api.php?entity=sensors&action=values&SensorType=<svc>&parse=1
"""
from __future__ import annotations

import atexit
import errno
import json
import os
import socket
import subprocess
import sys
import threading
import time


def _warn(msg: str) -> None:
    """Surface a non-fatal data problem. Goes to stderr AND, if the process has set up
    logging, to the log with a timestamp.

    [TUPLE_WARNINGS_ARE_TIMESTAMPED_V1] These went to stderr only. On a node that is
    fine -- the merge journal captures it. In the Communicator it meant a PUT_FAILED
    could not be lined up against anything: no time, so no way to tell a burst at
    startup from a recurring stall, and on Windows it goes to a console that closes
    with the app. Anything with a `frognet.` logger configured now gets it too.
    """
    try:
        print(f"[TUPLE] {msg}", file=sys.stderr, flush=True)
    except Exception:
        pass
    try:
        import logging
        lg = logging.getLogger("frognet.communicator")
        if lg.handlers:
            lg.warning("[TUPLE] %s", msg)
    except Exception:
        pass

import io
import urllib.request
import urllib.parse
import urllib.error


# ---------------------------------------------------------------------------
# [ONE_CONNECTION_PER_PEER_NOT_PER_QUESTION_V1]
#
# Every call here went through urllib.request.urlopen, which builds an
# HTTPConnection, sends, reads and closes it. store_server has spoken HTTP/1.1
# with a correct Content-Length since it was written, so it holds the
# connection open and ready for the next question; the client hung up anyway
# and dialled again.
#
# This is the layer 99% of the system coordinates through -- presence,
# descriptors, rendezvous, barriers, reduce-group agreement -- so the saving
# is not one workflow's. A connection is kept per (thread, dbhost) and reused.
#
# Thread-local rather than a shared pool with a lock: http.client connections
# cannot be shared between threads, and a lock would serialise exactly the
# callers this is meant to speed up. One socket per thread that talks.
#
# A kept connection can be closed by the far side between calls, and that
# failure looks identical to a store that has gone away. It MUST NOT be
# treated as one: a stale socket reconnects, an absent store trips
# classify_store_failure and can spawn runMerge. So a request that fails on a
# socket we were ALREADY holding is retried once on a fresh connection, and
# only a failure on the fresh connection is real.
# ---------------------------------------------------------------------------
import http.client as _httpclient


class _Conn(_httpclient.HTTPConnection):
    """[A_KEPT_CONNECTION_MUST_NOT_WAIT_FOR_AN_ACK_V1]

    The first version of this pool was 60x SLOWER than the one-shot
    connections it replaced: 44.0 ms median against 0.709 ms, measured, on
    the loopback hop to the proxy. 44 ms is not a cost, it is the 40 ms
    delayed-ACK timer with the real work on top.

    A fresh connection has no unacknowledged data, so its first small write
    leaves immediately -- which is why urlopen never hit this. A REUSED
    connection can still have unacked bytes from the previous response, so
    Nagle holds the next small request until the peer's delayed ACK fires.
    Reuse is what exposes it; http.client does not set TCP_NODELAY and
    nothing else was going to.

    tensor_plane sets NODELAY on both ends of its own sockets for the same
    reason. The store path simply never reused a socket long enough to care.
    """

    def connect(self):
        super().connect()
        try:
            self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except OSError:
            # A transport with no TCP_NODELAY is one where Nagle is not in
            # play either. Nothing to disable, nothing to report.
            pass


_POOL_ENABLED = os.environ.get("FROGNET_TUPLES_POOL", "1") not in ("0", "no")
_pool_local = threading.local()
_POOL_STATS = {"reused": 0, "opened": 0, "retried": 0}


#: [CLIENT_DEADLINE_MUST_EXCEED_PROXY_BUDGET_V1] How long a store call waits.
#:
#: This was the literal 4.0, repeated as a default in six signatures. Every one
#: of these requests goes to the local proxy, which forwards it over the
#: semantic transport, and the proxy's OWN budgets are:
#:
#:     proxy/transport_semantic.py:119  FROGNET_SEM_RPC_TIMEOUT    default 10
#:     proxy/transport_semantic.py:157  FROGNET_RPC_BUDGET_FLOOR   default 15
#:     proxy/transport_real.py:51       FROGNET_REAL_LOCAL_TIMEOUT default 10
#:
#: The client gave up in 4 seconds on a proxy that is entitled to spend 15, and
#: on this fleet those are raised to 60. So any request the proxy takes longer
#: than four seconds to service is GUARANTEED to look like a timeout here --
#: while the proxy is still working on it and will answer. The host is
#: reachable, the store is fine, and the read fails anyway. That is a deadline
#: mismatch, not a network fault, and no amount of network repair fixes it.
#:
#: Derived from the same environment the proxy reads, so the two cannot drift
#: apart again: wait at least as long as the proxy is allowed to take, plus a
#: margin for the local hop. FROGNET_STORE_TIMEOUT_S overrides outright.
def _store_timeout_default() -> float:
    override = os.environ.get("FROGNET_STORE_TIMEOUT_S")
    if override:
        try:
            return float(override)
        except ValueError:
            pass
    budget = 0.0
    for var, fallback in (("FROGNET_RPC_BUDGET_FLOOR", "15.0"),
                          ("FROGNET_SEM_RPC_TIMEOUT", "10"),
                          ("FROGNET_REAL_LOCAL_TIMEOUT", "10")):
        try:
            budget = max(budget, float(os.environ.get(var, fallback)))
        except ValueError:
            continue
    return budget + 5.0


STORE_TIMEOUT_S = _store_timeout_default()


class _DefaultTimeout:
    """Sentinel for "caller did not choose a deadline".

    NOT None: None is already meaningful here. [ASK_ONCE_AND_WAIT_V1] passes
    timeout=None deliberately to block on a long poll, letting the SERVER bound
    the wait. Collapsing the two would put a socket deadline back on exactly the
    read that must not have one.
    """
    def __repr__(self):
        return "<default %.1fs>" % STORE_TIMEOUT_S


DEFAULT_TIMEOUT = _DefaultTimeout()


def _pool_conn(host: str, timeout):
    if isinstance(timeout, _DefaultTimeout):
        timeout = STORE_TIMEOUT_S
    conns = getattr(_pool_local, "conns", None)
    if conns is None:
        conns = _pool_local.conns = {}
    c = conns.get(host)
    if c is None:
        c = conns[host] = _Conn(host, timeout=timeout)
        _POOL_STATS["opened"] += 1
    else:
        _POOL_STATS["reused"] += 1
    # The timeout differs per call -- a blocking read passes None on purpose
    # -- so it is set per request on the connection and on the live socket,
    # not baked in when the connection was made.
    c.timeout = timeout
    if c.sock is not None:
        try:
            c.sock.settimeout(timeout)
        except OSError:
            pass
    return c


def _pool_drop(host: str):
    conns = getattr(_pool_local, "conns", None) or {}
    c = conns.pop(host, None)
    if c is not None:
        try:
            c.close()
        except Exception:
            pass


# Protocol-level signs that the connection we were holding is no longer
# usable. Socket-level ones are OSError and are handled separately below:
# a closed socket raises EBADF, a half-open one ENOTCONN or EPIPE, and only
# some of those have dedicated subclasses. Listing the subclasses missed
# EBADF entirely, which is the one a far side that hung up actually
# produces.
_RETRYABLE = (_httpclient.BadStatusLine, _httpclient.CannotSendRequest,
              _httpclient.ResponseNotReady, _httpclient.RemoteDisconnected,
              _httpclient.ImproperConnectionState)


class _PooledResponse:
    """The parts of an HTTPResponse the callers below actually use."""

    def __init__(self, status, raw, headers, url):
        self.status = status
        self.code = status
        self.headers = headers
        self.url = url
        self._raw = raw

    def read(self, *a):
        return self._raw

    def getheaders(self):
        return list(self.headers.items())

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _urlopen(req, timeout=DEFAULT_TIMEOUT):
    if isinstance(timeout, _DefaultTimeout):
        timeout = STORE_TIMEOUT_S
    """urlopen's contract, over a kept connection.

    Returns something with .status, .read(), .headers, usable as a context
    manager, and raises urllib.error.HTTPError on non-2xx exactly as urlopen
    does -- classify_store_failure and every caller below depend on that.
    """
    if not _POOL_ENABLED:
        return urllib.request.urlopen(req, timeout=timeout)

    parts = urllib.parse.urlsplit(req.full_url)
    if parts.scheme != "http":
        return urllib.request.urlopen(req, timeout=timeout)
    host = parts.netloc
    path = parts.path + (("?" + parts.query) if parts.query else "")
    method = req.get_method()
    body = req.data
    headers = dict(req.headers)

    for attempt in (0, 1):
        c = _pool_conn(host, timeout)
        had_socket = c.sock is not None
        try:
            c.request(method, path, body=body, headers=headers)
            r = c.getresponse()
            # The body must be drained before the connection can carry
            # another request, so it is read here rather than lazily.
            raw = r.read()
            if r.will_close:
                _pool_drop(host)
            if 200 <= r.status < 300:
                return _PooledResponse(r.status, raw, r.headers, req.full_url)
            raise urllib.error.HTTPError(req.full_url, r.status, r.reason,
                                         r.headers, io.BytesIO(raw))
        except urllib.error.HTTPError:
            raise
        except TimeoutError:
            # A timeout is the store being slow on a connection that works.
            # Retrying would spend the caller's timeout twice and hide the
            # thing it is trying to report.
            _pool_drop(host)
            raise
        except (_RETRYABLE + (OSError,)):
            _pool_drop(host)
            # Only a socket we were ALREADY holding can be stale. The same
            # error on a connection just opened is the store, not the pool,
            # and must reach classify_store_failure unchanged.
            if attempt == 0 and had_socket:
                _POOL_STATS["retried"] += 1
                continue
            raise
        except Exception:
            _pool_drop(host)
            raise


def pool_stats():
    """Connections reused vs opened, and stale-socket retries."""
    return dict(_POOL_STATS)

from typing import Any, Dict, List, Optional, Set, Tuple

# [DIAG-PUT-V1] Success-side write logging, OFF by default (one line per write
# per 5s heartbeat is a firehose). FROGNET_TUPLE_DIAG=1 turns it on. Failures are
# always logged -- see [PUT_FAILURE_IS_LOUD_V1] below.
_DIAG = bool(os.environ.get("FROGNET_TUPLE_DIAG"))

DEFAULT_DBHOST = "databasehost_control.frognet"   # the SD: coordination plane lives on
# the deterministic control host (highest .1), NOT on the elected data host. capability,
# presence, beacons and the Services table resolve here; durable sensor data stays on
# databasehost.frognet (the elected, capacity-clocked data host).

# Variables THIS process wrote, for atexit self-cleanup. atexit is the courtesy:
# it fires on normal exit / graceful signals, NOT on SIGKILL, power loss, or a
# crash - which on a contested mesh is exactly when nodes die. So the reaper
# (frognet_reaper.py, 30-min ts sweep) is the guarantee; this just keeps the
# space tidy in the common case.
_OWNED: Set[Tuple[str, str]] = set()      # (dbhost, SensorName)
_ATEXIT_ARMED = False


def _local_ipv4s():
    """[(ifname, ipv4)] for up interfaces, via SIOCGIFADDR. Linux-only; returns []
    on any platform/permission issue (caller falls back). Lazy fcntl import so this
    module still imports on the Windows communicator."""
    out = []
    try:
        import fcntl, struct
        names = os.listdir("/sys/class/net")
    except Exception:
        return out
    for name in names:
        if name == "lo":
            continue
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            ip = socket.inet_ntoa(fcntl.ioctl(
                s.fileno(), 0x8915,                       # SIOCGIFADDR
                struct.pack("256s", name[:15].encode()))[20:24])
            out.append((name, ip))
        except OSError:
            pass
        finally:
            s.close()
    return out


def my_ip() -> str:
    """This node's FrogNet 10/8 identity (the SensorAddress / scope key).

    Enumerate local IPv4s and return the FrogNet address: a 10/8 that is NOT the
    synthetic infra planes (10.253 cross-NAT transit, 10.254 chorus). Prefer the
    served-subnet host address (.1), then an eth* address, then the lowest.

    The old `connect(("10.0.0.1", 9))` trick returned the WRONG address: the FrogNet
    plane carries per-/24 routes, not a 10/8 default, so the connect fell through to
    the WAN default route (e.g. 192.168.0.21 dev wlan1) and keyed the capability tuple
    off-plane. The connect trick is kept only as a fallback, and only if it yields a
    10/8 address."""
    cands = [(ifn, ip) for ifn, ip in _local_ipv4s()
             if ip.split(".")[0] == "10" and ip.split(".")[1] not in ("253", "254")]
    if cands:
        cands.sort(key=lambda c: (not c[1].endswith(".1"),
                                  not c[0].startswith("eth"), c[1]))
        return cands[0][1]
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.0.0.1", 9))
        ip = s.getsockname()[0]
        return ip if ip.startswith("10.") else "0.0.0.0"
    except Exception:
        return "0.0.0.0"
    finally:
        s.close()


def host_scope(pid: Optional[int] = None) -> str:
    """Node scope: host:<ip>:<pid>. PID makes it unique per running instance and
    self-expiring - a new instance writes a new key, the dead one stops asserting."""
    return f"host:{my_ip()}:{pid if pid is not None else os.getpid()}"


def node_scope() -> str:
    """Stable per-node scope: host:<ip> (NO pid). For a periodic REFRESH writer (the
    candidate advertiser) whose tuple must be ONE upserted row per host that outlives
    any single process - not a fresh pid-keyed key every 60s. Pair with put(own=False)
    so it isn't atexit-reaped. Contrast host_scope(), whose pid suits a long-running
    service that wants its own tuple reaped when that instance stops."""
    return f"host:{my_ip()}"


def role_scope(role: str) -> str:
    """Stable per-(node, role) scope: host:<ip>:<role>. A capability tuple is written
    under MULTIPLE services at one node (mediahost AND databasehost). SensorName is the
    DB's unique key and does NOT carry SensorType, so a bare node_scope (host:<ip>)
    yields the SAME SensorName for both roles - upsert_by_name then overwrites one with
    the other, leaving a single row whose role flip-flops. Qualifying the scope with
    the role keeps the two rows distinct. role is alphabetic, so it never collides with
    a pid suffix and the self-prune can tell them apart."""
    return f"host:{my_ip()}:{role}"


def session_scope(session_id: str) -> str:
    """Call scope: session:<id>."""
    return f"session:{session_id}"


SD_PREFIX = "SD:"   # service-variable marker. A tuple whose SensorName starts SD:
                    # is ephemeral coordination state (caps, level, presence, call
                    # signaling) - self-identifying, so a dead service's orphans stay
                    # recognizable. Observed sensor data (DHT/GPS/System/LinkState)
                    # carries NO SD: prefix and is therefore never a reap candidate.


def var_name(var: str, scope: str) -> str:
    return f"{SD_PREFIX}{var}.{scope}"


def put(service: str, var: str, scope: str, value: Dict[str, Any],
        dbhost: str = DEFAULT_DBHOST, timeout=DEFAULT_TIMEOUT,
        own: bool = True) -> bool:
    """Write/refresh a variable. Stamps ts so readers can age it out.

    own=True (default): this PROCESS owns the tuple - it is registered for atexit
    cleanup so a long-running service's ephemeral coordination state (presence, call
    signaling) is removed when the service stops.
    own=False: a fire-and-forget REFRESH write - a oneshot/periodic writer (e.g. the
    60s candidate advertiser) whose tuple must OUTLIVE the short process and age out
    by ts staleness instead. Arming cleanup here would delete the tuple on the very
    next exit (milliseconds later), so a oneshot registrar's writes would never
    persist - which is exactly the failure that left the transient with no
    <role>/capability rows for the election to read.
    """
    payload = dict(value)
    payload.setdefault("ts", int(time.time()))
    url = f"http://{dbhost}/api.php?entity=sensor_data&action=upsert_by_name"
    body = json.dumps({
        "SensorName": var_name(var, scope),
        "SensorType": service,
        "SensorAddress": my_ip(),
        "jsonData": payload,
    }).encode()
    req = urllib.request.Request(url, data=body, method="POST",
                                 headers={"Content-Type": "application/json"})
    # [DIAG-PUT-V1] Log the SUCCESS side too. [PUT_FAILURE_IS_LOUD_V1] below made
    # failures visible; a write that succeeds against the wrong key or the wrong
    # plane is just as fatal and was still silent. The Sensor table showed nine
    # presence rows for one client -- george-3df4, -3684, -d5d5, -9c44, -6d7a,
    # -3528, -91ef, -12d7, -ff7c -- meaning every announce INSERTED under a new
    # SensorName instead of updating one, so no row's UpdatedAt ever advanced and
    # the peer aged out of every roster while writing successfully every 5s.
    # The key is what has to be watched, not just the status.
    _t0 = time.time()
    _name = var_name(var, scope)
    try:
        with _urlopen(req, timeout=timeout) as r:
            _status = getattr(r, "status", None)
            _raw = r.read().decode() or "{}"
            ok = bool(json.loads(_raw).get("ok", True))
        if _DIAG:
            print("[DIAG-PUT] ok=%s status=%s name=%r service=%r dbhost=%r src=%s "
                  "ts=%s dur_ms=%.1f resp=%r"
                  % (ok, _status, _name, service, dbhost, my_ip(),
                     payload.get("ts"), (time.time() - _t0) * 1000.0, _raw[:200]),
                  flush=True)
        if ok and own:
            _OWNED.add((dbhost, _name))
            _arm_atexit()
        return ok
    except Exception as e:
        # [PUT_FAILURE_IS_LOUD_V1] This swallowed everything and returned False, so a
        # store that rejected every write was indistinguishable from a policy "no".
        # A real one: api.php wrote `UpdatedAt = CURRENT_TIMESTAMP` against a
        # SensorData table with no such column, so EVERY capability write failed 1054
        # -- for as long as that schema was deployed -- and the only trace anywhere was
        # a node quietly ceasing to be electable. The caller still gets False; it now
        # also gets told what happened.
        detail = ""
        try:
            if hasattr(e, "read"):
                detail = " body=" + e.read().decode("utf-8", "replace")[:200]
        except Exception:
            pass
        # [PUT_FAILURE_NAMES_THE_HOST_V1] Print what the name RESOLVED TO. dbhost is
        # usually databasehost.frognet -- elected and floating -- so "the database host
        # was up" and "the name pointed at the box that is up" are different claims,
        # and the failure line could not tell them apart.
        # [HOSTS_ONLY_V1] /etc/hosts, never the resolver. This line printed
        # "-> 10.130.130.1" on a node whose /etc/hosts said 10.250.250.1, because
        # gethostbyname went to resolv.conf (nameserver 127.0.0.1) instead of the file
        # -- so the diagnostic accused the wrong machine.
        try:
            from core.hosts_only import resolve as _hres
            _ip = _hres(dbhost.split(":")[0])
        except Exception as _e:
            _ip = "UNRESOLVED(%s)" % type(_e).__name__
        _warn("PUT_FAILED dbhost=%s -> %s name=%s err=%s:%s%s"
              % (dbhost, _ip, var_name(var, scope), type(e).__name__, e, detail))
        # [UNREACHABLE_TRIGGERS_A_MERGE_V1] Same rule on the write path. put()
        # keeps returning False -- callers depend on that and the loudness is
        # already handled by PUT_FAILURE_IS_LOUD_V1 -- but an unreachable
        # store still needs the re-election provoked.
        if classify_store_failure(e, detail) is StoreUnreachable:
            trigger_merge("PUT %s unreachable (%s)" % (dbhost, type(e).__name__),
                          dbhost=dbhost)
        return False


def _arm_atexit() -> None:
    global _ATEXIT_ARMED
    if not _ATEXIT_ARMED:
        atexit.register(cleanup_owned)
        _ATEXIT_ARMED = True


def cleanup_owned() -> None:
    """Delete the tuples this process wrote. Registered via atexit; also callable
    explicitly. Best-effort - never raises."""
    _IN_CLEANUP.active = True          # [CLEANUP_MUST_NOT_PROVOKE_A_MERGE_V1]
    try:
        _cleanup_owned_inner()
    finally:
        _IN_CLEANUP.active = False


#: [CLEANUP_FAILURE_IS_NOT_CLEANUP_V1] Cells this process owned and could not
#: delete, as (dbhost, SensorName, reason). An owned cell is presence: while
#: it is in the store it says the writer is here. If cleanup fails and says
#: nothing, that claim outlives the writer and every reader believes it --
#: absence stops being observable, which is the one thing the whole
#: shared-state model rests on.
_CLEANUP_FAILED: List[Tuple[str, str, str]] = []


def cleanup_failures() -> List[Tuple[str, str, str]]:
    """Owned cells this process could not remove. Empty is the only good
    answer; anything here is presence that outlived its owner."""
    return list(_CLEANUP_FAILED)


def _cleanup_owned_inner() -> None:
    for dbhost, name in list(_OWNED):
        # [CLEANUP_FAILURE_IS_NOT_CLEANUP_V1] This was one try/except around
        # the whole thing with `pass` in the handler, so an unreachable store,
        # a store with no delete route, and a row that would not go away all
        # produced the same silence -- and _OWNED was discarded regardless, so
        # nothing downstream could tell either. Still never raises: throwing
        # out of atexit during interpreter shutdown helps nobody. But it is
        # now observable, per cell, with the reason.
        reason = ""
        try:
            rows = _values_raw(SERVICE_ANY, dbhost, name_like=name)
            hits = [r for r in rows if r.get("SensorName") == name
                    and r.get("SensorID") is not None]
            if not hits:
                # Nothing there to delete. Either it was never written or
                # something else reaped it; either way no presence survives.
                _OWNED.discard((dbhost, name))
                continue
            for row in hits:
                if not _delete_by_id(dbhost, row["SensorID"]):
                    reason = "store refused or could not reach delete for " \
                             "SensorID=%s" % row["SensorID"]
        except Exception as e:
            reason = "%s: %s" % (type(e).__name__, e)
        if reason:
            _CLEANUP_FAILED.append((dbhost, name, reason))
            _warn("CLEANUP_FAILED dbhost=%s name=%s -- %s. This cell is "
                  "presence: it still says this process is here. Absence is "
                  "the only authority on liveness, and this one is now a lie."
                  % (dbhost, name, reason))
        _OWNED.discard((dbhost, name))


SERVICE_ANY = ""   # sentinel: query without a SensorType filter (any service)


# [A_500_IS_NOT_UNREACHABLE_V1]
# Store failures were all one exception, so every caller had to treat "the
# store is broken", "I cannot reach the store", and "my own proxy refused to
# route to the store" identically. They prove completely different things and
# only one of them is a reason to go somewhere else.
#
# Measured 2026-09-05: every store read on this node returned
#   500 dispatch failed: ConnectionError:
#       10.250.250.1 cached non-FrogNet this session; no worker built
# That body was written by the LOCAL proxy about a peer it had decided not to
# dial after three refused :9009 connects. The databasehost itself was never
# contacted and may have been perfectly healthy. An HTTP status generated by
# your own side is evidence about your own side.
class StoreError(Exception):
    """Base: a tuple-store operation failed."""


class StoreUnreachable(StoreError):
    """No path from here to the store: refused, no route, or timed out.

    This is the ONLY class that justifies trying a different store host --
    nothing was executed, so nothing can have been half-written.
    """


class StoreRefusedLocally(StoreError):
    """Our own proxy declined to route to the store and answered for it.

    Says nothing about the store. The remedy is local (the not_frognet mark,
    the per-target semaphore, MAX_ACTIVE_REQUESTS), and failing over to a
    different store host would carry the same local refusal with it.
    """


class StoreSlow(StoreError):
    """The store did not answer inside our deadline.

    [A_TIMEOUT_IS_NOT_A_DEPARTURE_V1] Distinct from StoreUnreachable on
    purpose: not a usable answer, but not evidence the host has left, so it
    does NOT provoke a merge. Raise the client deadline before concluding a
    node is gone.
    """


class StoreBroken(StoreError):
    """The store answered and the answer was an error.

    It is reachable and it is executing. Failing over here risks writing the
    same tuple to two authoritative copies, which is worse than failing.
    """


#: Markers that identify an error body written by proxy_main.send_error_reply
#: rather than by api.php. The proxy stamps `where=` into every one.
_PROXY_ERROR_MARKERS = ("where=", "cached non-FrogNet", "Proxy overloaded",
                        "Node environment unreadable", "dispatch failed",
                        "semantic key failed", "per-target limit")


#: [UNREACHABLE_TRIGGERS_A_MERGE_V1]
#: When the elected databasehost resolves but cannot be reached, and no
#: re-election has happened yet, there is nothing to switch TO -- picking a
#: different store host would invent a second authoritative copy, which is the
#: split-brain the election exists to prevent. The complete set of correct
#: actions is: fail, let the caller decide, and provoke the merge that will
#: re-elect. The first two exist; this is the third.
#:
#: [THE_LOCK_IS_NOT_A_LIMITER_V1] This used to say "no rate limiter,
#: deliberately", on the grounds that runMerge takes an exclusive flock and
#: BAILS with reason=lock_held, so triggers cannot stack. Read what the bail
#: actually does:
#:
#:     if ! flock -n 9; then
#:         mkdir -p /etc/sentinels; : > /etc/sentinels/runAgain
#:
#: It does not discard the request. It touches runAgain, which makes the merge
#: already in flight RE-INVOKE itself when it finishes -- up to
#: MAX_MERGE_DEPTH=10 passes. The lock is an amplifier, not a limiter: every
#: suppressed trigger is converted into another full discovery pass. At the
#: ~145s per pass this fleet measures, ten store timeouts inside one merge is
#: twenty-four minutes of continuous merging, on a node whose topology never
#: moved.
#:
#: So the limiter is here, and it is shared across processes: the proxy and the
#: daemon both import this module, and a node under load has both of them
#: timing out on the same store at the same moment.

_MERGE_CMD = "/usr/local/bin/runMerge.bash"

#: Shortest interval between merges provoked from this node's store layer.
#: Longer than a merge pass, so a trigger can never land inside the merge it
#: caused and re-arm it through the lock bail.
MERGE_TRIGGER_MIN_INTERVAL_S = float(
    os.environ.get("FROGNET_MERGE_TRIGGER_MIN_INTERVAL_S", "600"))

#: How many CONSECUTIVE unreachable results for one store before a merge is
#: worth provoking. A single failure is not evidence of a topology change; it
#: is evidence of one bad moment. Re-election is the expensive remedy and it is
#: only correct if the host really is gone.
MERGE_TRIGGER_STRIKES = int(
    os.environ.get("FROGNET_MERGE_TRIGGER_STRIKES", "3"))

#: Cross-process rate-limit stamp. /run so it does not survive a reboot and is
#: not wiped by runMerge's /etc/sentinels purge.
_MERGE_STAMP = "/run/frognet/last_store_merge_trigger"

_STRIKES = {}
_STRIKES_LOCK = threading.Lock()


def note_store_reachable(dbhost: str) -> None:
    """Clear the strike count for a store that just answered.

    Called on every successful store call. Strikes must be CONSECUTIVE -- a
    store that answers between failures has not lost its host, and counting
    those failures toward a threshold would eventually provoke a merge on a
    link that is merely lossy."""
    with _STRIKES_LOCK:
        _STRIKES.pop(dbhost, None)


def _strike(dbhost: str) -> int:
    with _STRIKES_LOCK:
        n = _STRIKES.get(dbhost, 0) + 1
        _STRIKES[dbhost] = n
        return n


def _rate_limited() -> bool:
    """True if another process on this node provoked a merge too recently."""
    try:
        age = time.time() - os.stat(_MERGE_STAMP).st_mtime
        if age < MERGE_TRIGGER_MIN_INTERVAL_S:
            return True
    except OSError:
        pass
    return False


def _stamp() -> None:
    try:
        os.makedirs(os.path.dirname(_MERGE_STAMP), exist_ok=True)
        with open(_MERGE_STAMP, "w") as f:
            f.write("%.3f\n" % time.time())
    except OSError:
        pass


#: [CLEANUP_MUST_NOT_PROVOKE_A_MERGE_V1] Set while cleanup_owned() runs.
#: cleanup_owned is registered via atexit and is documented "best-effort --
#: never raises". It reads the store to find the rows it wrote, and at
#: interpreter exit that store is routinely gone: the process is shutting
#: down, its peer may be shutting down too, and on a node being rebooted
#: EVERY exiting process would hit an unreachable store at once. Measured in
#: this container: two ordinary reads at exit, two runMerge.bash spawned.
#:
#: A cleanup that cannot reach the store has learned nothing about the
#: topology -- it has learned that it is late. Merges are provoked by
#: READS THE PROGRAM ASKED FOR, not by teardown.
_IN_CLEANUP = threading.local()


def trigger_merge(why: str = "", dbhost: str = "") -> bool:
    """Fire runMerge in the background. Never raises, never waits.

    Only for StoreUnreachable, and only once that store has been unreachable
    MERGE_TRIGGER_STRIKES times in a row, and only if no merge has been
    provoked from this node's store layer in the last
    MERGE_TRIGGER_MIN_INTERVAL_S. See [THE_LOCK_IS_NOT_A_LIMITER_V1] above for
    why the flock does not do this job.

    A store that ANSWERED -- whether our own proxy refusing to route or api.php
    throwing -- has not lost its host, and merging would be treating a local or
    application fault as a topology change.

    Suppressed when FROGNET_SENTINEL_DIR is set, which is this tree's existing
    marker for an isolated run: simulation/run_all.py and
    discovery/run_discovery_oracles.py both set it per suite, and an oracle
    must not reconfigure the machine it is running on. [SENTINEL_DIR_HONOURED_V1]
    """
    if getattr(_IN_CLEANUP, "active", False):
        _warn("MERGE_TRIGGER suppressed (exit cleanup): %s" % why)
        return False
    if os.environ.get("FROGNET_SENTINEL_DIR"):
        _warn("MERGE_TRIGGER suppressed (isolated run): %s" % why)
        return False
    n = _strike(dbhost or "<unknown>")
    if n < MERGE_TRIGGER_STRIKES:
        _warn("MERGE_TRIGGER held: strike %d/%d for %s: %s"
              % (n, MERGE_TRIGGER_STRIKES, dbhost or "<unknown>", why))
        return False
    if _rate_limited():
        _warn("MERGE_TRIGGER held: another merge was provoked within %.0fs: %s"
              % (MERGE_TRIGGER_MIN_INTERVAL_S, why))
        return False
    if not os.access(_MERGE_CMD, os.X_OK):
        _warn("MERGE_TRIGGER wanted but %s is not executable: %s"
              % (_MERGE_CMD, why))
        return False
    try:
        subprocess.Popen(
            [_MERGE_CMD],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,        # survives this process exiting
            close_fds=True)
        _stamp()
        with _STRIKES_LOCK:
            _STRIKES.pop(dbhost or "<unknown>", None)
        _warn("MERGE_TRIGGERED after %d consecutive unreachable: %s" % (n, why))
        return True
    except Exception as e:
        # Best effort by definition: the store is already failing and the
        # caller is already getting an exception. This must not add a second.
        _warn("MERGE_TRIGGER failed: %s: %s (%s)"
              % (type(e).__name__, e, why))
        return False


def classify_store_failure(exc, body=""):
    """Which of the three a failed store call actually was.

    [A_500_IS_NOT_UNREACHABLE_V1] Errno first -- a refused connect or no route
    is unambiguous and needs no body. Then the body, because a 5xx carrying
    the proxy's own error envelope is a local refusal wearing a status code.
    Anything else that answered is the store answering badly.
    """
    err = getattr(exc, "errno", None)
    if err in (errno.ECONNREFUSED, errno.EHOSTUNREACH, errno.ENETUNREACH,
               errno.ENETDOWN):
        return StoreUnreachable
    # [A_TIMEOUT_IS_NOT_A_DEPARTURE_V1] A client-side timeout proves the answer
    # did not arrive inside OUR deadline. It does not prove the host is gone.
    # _values_raw defaults to timeout=4.0s, and this mesh routinely reaches a
    # store across a WireGuard link measured at ~97ms RTT and ~0.5 MB/s -- a
    # 15KB sensor table is several seconds of transfer on its own, before any
    # load. Classifying that as StoreUnreachable made every slow read provoke a
    # re-election, which is the expensive remedy for a problem the node did not
    # have. ETIMEDOUT is in the same class and for the same reason: the kernel
    # giving up on a connect is a statement about latency, not about presence.
    #
    # StoreSlow keeps the caller's failover behaviour unchanged where it
    # matters -- it is still not a usable answer -- while taking it out of the
    # set that provokes a merge. A host that has genuinely gone produces
    # ECONNREFUSED or EHOSTUNREACH, or answers nothing at all (URLError below),
    # and those still count.
    if err == errno.ETIMEDOUT or isinstance(exc, (socket.timeout, TimeoutError)):
        return StoreSlow
    if isinstance(exc, urllib.error.HTTPError):
        if any(m in (body or "") for m in _PROXY_ERROR_MARKERS):
            return StoreRefusedLocally
        return StoreBroken
    if isinstance(exc, urllib.error.URLError):
        # No status at all: the request never completed a round trip.
        return StoreUnreachable
    return StoreError

try:
    from core.frognet_diag import (diag, diag_exc, body_of, headers_of,
                                   resolve as _diag_resolve, Timer)
except ImportError:          # tolerate being imported without the tree on path
    def diag(*a, **k): pass
    def diag_exc(*a, **k): pass
    def body_of(e): return "<diag unavailable>"
    def headers_of(o): return "<diag unavailable>"
    def _diag_resolve(h): return "?"
    class Timer:
        ms = -1.0


def _values_raw(service: str, dbhost: str = DEFAULT_DBHOST,
                name_like: Optional[str] = None,
                timeout=DEFAULT_TIMEOUT, fresh_s: int = 0,
                wait_s: float = 0.0,
                min_rows: int = 0) -> List[Dict[str, Any]]:
    """Raw sensors/values rows (with SensorID), parsed JSON folded into 'data'.
    Filters by SensorType when service is given, and/or by SensorName."""
    q = "http://%s/api.php?entity=sensors&action=values&parse=1" % dbhost
    if service:
        q += f"&SensorType={service}"
    if name_like is not None:
        # [A_PATTERN_IS_NOT_A_NAME_V1 - John 2026-09-15] `SensorName__like`,
        # not `SensorName`.
        #
        # api.php's build_where_and_params whitelists both `<col>` and
        # `<col>__like`, and only the suffixed form becomes `LIKE ?`. Sending
        # the pattern as plain `SensorName` produced
        #
        #     SensorName = 'SD:probe.1789470575.%'
        #
        # -- an exact comparison against a string containing a wildcard, which
        # matches nothing, ever. A_TUPLE_HAS_THREE_COORDINATES_V1 added the var
        # coordinate to get_all and it has never filtered anything: every
        # caller passing a pattern got zero rows back from a store holding the
        # row. torch_store waits on `SD:<key>.%`, so its blocking read could
        # only ever burn its full wait_s and return empty, and the only reason
        # this looked like it worked is the one internal caller that passes an
        # exact name with no wildcard in it.
        #
        # LIKE with no wildcard is an exact match, so that caller is unaffected.
        q += f"&SensorName__like={urllib.parse.quote(name_like)}"
    # [ASK_ONCE_AND_WAIT_V1] Let the store hold the question open rather
    # than asking it again. A store that does not implement this ignores the
    # parameters and answers immediately, so the caller's own loop still
    # works and nothing breaks against a store that has not been updated.
    if wait_s > 0 and min_rows > 0:
        q += f"&wait_s={float(wait_s):.3f}&min_rows={int(min_rows)}"
        # No socket timeout on a blocking read. A client timeout racing the
        # server's wait turns the long poll back into polling with extra
        # steps: the socket dies, the caller retries, and the round trip it
        # was meant to remove comes straight back. The SERVER decides when
        # the answer is worth returning and bounds its own wait; the client
        # waits for it.
        #
        # This does not hang on a dead store. urlopen without a timeout
        # blocks on a live socket, and a store that dies closes it -- which
        # raises, and is classified like any other store failure.
        timeout = None
    if fresh_s:
        # [ENVELOPE_TS_V1] the STORE decides freshness, on its own clock
        q += f"&fresh_s={int(fresh_s)}"
    req = urllib.request.Request(q, method="GET")
    # [DIAG-STORE] Every call, in and out. A 503 from api.php reaches the
    # caller as a bare status line; the body is where the server says why,
    # and urlopen's exception is the only thing that still has it.
    _t = Timer()
    diag("DIAG-STORE", "GET ->", url=q, dbhost=dbhost, ip=_diag_resolve(dbhost),
         service=service or "<any>", name_like=name_like, fresh_s=fresh_s,
         timeout=timeout)
    try:
        with _urlopen(req, timeout=timeout) as r:
            raw = r.read()
            diag("DIAG-STORE", "GET <-", status=getattr(r, "status", None),
                 ms=round(_t.ms, 1), bytes=len(raw), hdrs=headers_of(r))
            rows = json.loads(raw.decode()).get("rows", [])
    except urllib.error.HTTPError as e:
        _body = body_of(e)
        _kind = classify_store_failure(e, _body)
        diag_exc("DIAG-STORE", "GET FAILED (HTTP)", e, url=q, dbhost=dbhost,
                 ip=_diag_resolve(dbhost), status=getattr(e, "code", None),
                 ms=round(_t.ms, 1), hdrs=headers_of(e), body=_body,
                 classified=_kind.__name__)
        # [A_500_IS_NOT_UNREACHABLE_V1] Re-raised as the class that says what
        # this failure PROVES, with the original attached. A caller deciding
        # whether to go somewhere else needs that distinction and could not
        # make it from a bare HTTPError.
        if _kind is StoreUnreachable:
            trigger_merge("GET %s unreachable (HTTP %s)"
                          % (dbhost, getattr(e, "code", "?")), dbhost=dbhost)
        raise _kind("%s on %s: HTTP %s: %s"
                    % (_kind.__name__, dbhost, getattr(e, "code", "?"),
                       _body[:200])) from e
    except Exception as e:
        _kind = classify_store_failure(e)
        diag_exc("DIAG-STORE", "GET FAILED", e, url=q, dbhost=dbhost,
                 ip=_diag_resolve(dbhost), ms=round(_t.ms, 1),
                 classified=_kind.__name__)
        if _kind is StoreUnreachable:
            trigger_merge("GET %s unreachable (%s)" % (dbhost, type(e).__name__),
                          dbhost=dbhost)
        raise _kind("%s on %s: %s: %s"
                    % (_kind.__name__, dbhost, type(e).__name__, e)) from e
    note_store_reachable(dbhost)      # strikes must be CONSECUTIVE
    diag("DIAG-STORE", "GET rows", n=len(rows), service=service or "<any>")
    for row in rows:
        if not isinstance(row.get("data"), dict):
            raw = row.get("jsonData")
            try:
                row["data"] = json.loads(raw) if isinstance(raw, str) else None
            except Exception:
                row["data"] = None
    return rows


def _delete_by_id(dbhost: str, sensor_id: Any, timeout=DEFAULT_TIMEOUT) -> bool:
    """Delete a sensor row by PK. SensorData cascades (ON DELETE CASCADE)."""
    url = f"http://{dbhost}/api.php?entity=sensors&action=delete&SensorID={sensor_id}"
    req = urllib.request.Request(url, method="DELETE")
    try:
        with _urlopen(req, timeout=timeout) as r:
            return bool(json.loads(r.read().decode() or "{}").get("ok", False))
    except Exception:
        return False


def prune_self_stale_rows(service: str, var: str, keep_scope: str,
                          dbhost: str = DEFAULT_DBHOST) -> int:
    """Delete THIS host's rows for `service`/`var` other than `keep_scope`.

    [ONE_ROW_PER_HOST_V1] Generalises prune_self_stale_capability for any
    host-scoped variable. Matches the bare host:<ip> key and every host:<ip>:<suffix>
    variant, keeps exactly the one named, and only ever touches rows whose scope
    carries THIS node's ip -- so it is non-racy across nodes.
    """
    ip = my_ip()
    if not ip.startswith("10."):
        return 0
    keep = var_name(var, keep_scope)
    base = f"{SD_PREFIX}{var}.host:{ip}"        # matches bare and any :suffix
    n = 0
    try:
        for row in _values_raw(service, dbhost):
            nm = row.get("SensorName", "")
            if nm == keep:
                continue
            if (nm == base or nm.startswith(base + ":")) and row.get("SensorID") is not None:
                if _delete_by_id(dbhost, row["SensorID"]):
                    n += 1
    except Exception:
        pass
    return n


def prune_self_stale_capability(role: str, var: str = "capability",
                                dbhost: str = DEFAULT_DBHOST) -> int:
    """Delete THIS host's DEPRECATED capability rows for `role`, keeping only the
    current role_scope row (host:<ip>:<role>). Clears the bare node_scope (host:<ip>,
    which collided across roles) and pid-keyed (host:<ip>:<pid>) variants left by
    earlier code, instead of waiting for the 30-min reaper. _values_raw is filtered by
    SensorType=role, so the OTHER role's row (host:<ip>:<other>) is never touched, and
    we only act on THIS host's own ip - non-racy across nodes."""
    ip = my_ip()
    if not ip.startswith("10."):
        return 0
    keep = var_name(var, f"host:{ip}:{role}")
    base = f"{SD_PREFIX}{var}.host:{ip}"        # matches bare and any :suffix
    n = 0
    try:
        for row in _values_raw(role, dbhost):
            nm = row.get("SensorName", "")
            if nm == keep:
                continue
            if (nm == base or nm.startswith(base + ":")) and row.get("SensorID") is not None:
                if _delete_by_id(dbhost, row["SensorID"]):
                    n += 1
    except Exception:
        pass
    return n


def get_all(service: str, dbhost: str = DEFAULT_DBHOST,
            fresh_s: int = 0, timeout=DEFAULT_TIMEOUT, wait_s: float = 0.0,
            min_rows: int = 0, name_like: Optional[str] = None) -> List[Dict[str, Any]]:
    """Read every variable for a service.

    Returns rows as {var, scope, name, addr, value(dict), ts_env, age_s}.

    [ENVELOPE_TS_V1] fresh_s is passed to the STORE, which filters on UpdatedAt --
    the row's insert/update time, on its own clock. Freshness is envelope
    information. A payload may carry a ts of its own and it is NOT considered here.

    This used to filter locally on `data["ts"]`, comparing the reader's clock against
    the writer's stamp: a node whose clock was two minutes slow vanished from every
    roster while publishing correctly, and the only symptom was an empty flock. One
    clock now decides, and it is the one that stamped the row, so skew between nodes
    cannot matter.

    ts_env / age_s are carried through for callers that need to REPORT an age (the
    election logs a ballot's age); they are the store's numbers, not the payload's.
    """
    # [DO_NOT_WIDEN_A_CALL_NOBODY_ASKED_TO_WIDEN_V1]
    # Passing wait_s/min_rows unconditionally broke every caller that
    # substitutes its own _values_raw -- three discovery oracles went red
    # with "unexpected keyword argument 'wait_s'". A blocking read is opt-in,
    # so the call keeps its old shape unless somebody asked to wait.
    # [A_TUPLE_HAS_THREE_COORDINATES_V1 - John 2026-09-14] name_like is the VAR
    # coordinate and it was not exposed here, so every caller had to read the
    # whole service and filter locally. A reader waiting for one cell then had
    # no satisfiable condition to give the store -- which is why torch_store
    # ended up waiting on "the service grew", a question nobody asked.
    #
    # SensorName is <var>.<scope>; scope is the writer. A reader knows the var
    # and must NOT have to know the writer, so the match is a prefix over the
    # var and a wildcard over the scope. api.php's build_where_and_params
    # already accepts SensorName__like, and _values_raw now SENDS that form --
    # see A_PATTERN_IS_NOT_A_NAME_V1 there. It sent plain SensorName until
    # 2026-09-15, so this coordinate matched nothing for a day.
    if wait_s > 0 and min_rows > 0:
        rows = _values_raw(service, dbhost, name_like=name_like,
                           fresh_s=fresh_s, timeout=timeout,
                           wait_s=wait_s, min_rows=min_rows)
    else:
        rows = _values_raw(service, dbhost, name_like=name_like,
                           fresh_s=fresh_s, timeout=timeout)
    out: List[Dict[str, Any]] = []
    no_envelope = 0
    for row in rows:
        data = row.get("data")
        if not isinstance(data, dict):
            continue
        try:
            ts_env = int(row.get("UpdatedAtEpoch") or 0)
        except (TypeError, ValueError):
            ts_env = 0
        if fresh_s and not ts_env:
            # [ENVELOPE_TS_V1] We asked the store for rows written in the last N
            # seconds. A row with no envelope means the store did not answer that
            # question -- an api.php older than this change ignores &fresh_s and
            # returns EVERYTHING. Handing those back would present a full history as
            # if it were current: months of departed peers in the roster, calls that
            # ended in March offered as joinable. A row whose freshness cannot be
            # established is not fresh. Drop it and say so.
            no_envelope += 1
            continue
        sn = row.get("SensorName", "")
        name = sn[len(SD_PREFIX):] if sn.startswith(SD_PREFIX) else sn
        var, _, scope = name.partition(".")
        out.append({"var": var, "scope": scope, "name": sn,
                    "addr": row.get("SensorAddress", ""), "value": data,
                    "ts_env": ts_env,
                    "age_s": (int(time.time()) - ts_env) if ts_env else None})
    if no_envelope:
        _warn("get_all service=%s dbhost=%s fresh_s=%s DROPPED %d row(s) with no "
              "UpdatedAt envelope -- this store does not honour &fresh_s. Update "
              "api.php and run schema_fixups.sql, or every read returns all history."
              % (service, dbhost, fresh_s, no_envelope))
    return out


def get(service: str, var: str, dbhost: str = DEFAULT_DBHOST,
        fresh_s: int = 0, timeout=DEFAULT_TIMEOUT,
        wait_s: float = 0.0, min_rows: int = 0) -> List[Dict[str, Any]]:
    """Read all instances of one variable across scopes (e.g. every node's caps).

    [A_TUPLE_HAS_THREE_COORDINATES_V1 - John 2026-09-14] Service and var are
    given, scope is wildcarded -- so this can be a real blocking read. It had
    no wait_s/min_rows at all and called get_all positionally, so a caller
    reading through it could not block even where the store supported it.
    """
    return [r for r in get_all(service, dbhost, fresh_s=fresh_s,
                               timeout=timeout, wait_s=wait_s,
                               min_rows=min_rows)
            if r["var"] == var]


if __name__ == "__main__":
    import sys
    db = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_DBHOST
    sc = host_scope()
    put("communicator", "av_caps", sc,
        {"no_cam": False, "no_mic": False, "no_video": False, "no_audio": False}, dbhost=db)
    print("wrote av_caps for", sc)
    for r in get("communicator", "av_caps", dbhost=db):
        print("  read:", r["name"], "from", r["addr"], "->", r["value"])
