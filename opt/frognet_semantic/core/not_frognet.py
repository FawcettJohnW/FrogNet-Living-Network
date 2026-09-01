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
not_frognet.py - per-session negative cache of hosts proven NOT to be FrogNet.

A LAN host that refuses the connection (ECONNREFUSED) or is unreachable
(EHOSTUNREACH / ENETUNREACH), or that an on-segment discovery probe finds has no
frognet_echo, is not a FrogNet node - it's just another box on the wire.
Re-probing it every cycle burns a full RPC budget (~15s) and feeds the bootstrap
storm.  We remember it for the rest of this session and skip it.

"Session" = the interval between merges.  flush() runs at the top of every
runMerge (flush_not_frognet stage, runMerge.bash), so a host that joins FrogNet
later gets exactly one fresh probe on the next merge.

[FLUSH_HAS_A_CALLER_V1] That sentence was false from the day it was written and
the lie is what hid the bug for weeks.  The stage named here was
`flush_discovery_cache`, which runs
`/usr/local/bin/frognet_discovery_cache.sh flush` -- and that command's entire
body is `DELETE FROM TransitPeerCache;`, a MySQL table with no relationship to
this file.  NOTHING in the tree ever called not_frognet.flush(), so a mark was
permanent for the life of the proxy process.

Which is the SAME OUTCOME as the retire() bug described at the bottom of this
module, reached by the opposite route: retire()'s set was unreachable state the
merge could not clear, and this was reachable state the merge never asked to
clear.  Measured on Seattle3, 2026-08-08: one legitimate ECONNREFUSED at
09:02:09 during a two-second restart of Seattle5 marked 10.250.250.1 -- the
elected databasehost -- and it was never dialled again.  Every sensor upsert
503'd, the capability tuple never landed, and the databasehost floated.

The stage now exists and is named after what it flushes.  If you change the
stage name, change it HERE, and grep to prove the caller still exists.

Shared across processes (the proxy and discovery are separate) via a sentinel
file, matching the /etc/sentinels idiom.  Each process keeps an in-memory view
and reloads only when the file's mtime changes, so is_marked() is cheap on the
hot path.

IMPORTANT - what may populate this:
  Only DEFINITIVE non-FrogNet signals: connection refused, no route / network
  unreachable, or an on-segment probe with no echo.  A TimeoutError must NEVER
  mark a host - a real FrogNet that is merely slow or briefly unreachable would
  be wrongly silenced until the next merge.  ECONNRESET is also excluded: a
  reset means something WAS there and dropped, which a real peer can do under
  load - not proof of absence.
"""
from __future__ import annotations

import errno
import os
import threading
import time

# [SENTINEL_DIR_HONOURED_V1] Honour FROGNET_SENTINEL_DIR, read PER CALL.
#
# This was a module-level constant pinned at /etc/sentinels/not_frognet.tsv.
# discovery/run_discovery_oracles.py:59 hands every oracle its own
# FROGNET_SENTINEL_DIR, and simulation/run_all.py:39 does the same for the whole
# suite, precisely so a test run cannot read or write the box's real sentinels.
# This module ignored both, so on a live node the oracles marked and un-marked
# against the REAL not_frognet.tsv -- entries left by real merges made
# test_not_frognet_skip_oracle fail on the machine's history rather than on the
# code, and a test run could write marks back into production state.
#
# Per call, not at import: an oracle sets the env var after this module is
# imported, and _sentinel() is cheap.
_SENTINEL_DEFAULT_DIR = "/etc/sentinels"
_SENTINEL_NAME = "not_frognet.tsv"


def _sentinel():
    return os.path.join(
        os.environ.get("FROGNET_SENTINEL_DIR", _SENTINEL_DEFAULT_DIR),
        _SENTINEL_NAME)


class _SentinelPath(str):
    """Backwards compatibility: anything still reading not_frognet.SENTINEL as a
    string gets the CURRENT path, not the one that was live at import time."""
    def __str__(self):
        return _sentinel()
    def __repr__(self):
        return repr(_sentinel())


SENTINEL = _SentinelPath("/etc/sentinels/not_frognet.tsv")

# errnos that mean "nothing FrogNet is at this address" (structural absence).
# Deliberately excludes ECONNRESET (a real peer can reset under load) and any
# timeout (a real peer can be slow).
DEFINITIVE_ERRNOS = frozenset((
    errno.ECONNREFUSED,
    errno.EHOSTUNREACH,
    errno.ENETUNREACH,
))


# [ONE_STATE_V1] The .1/.2 carve-out is GONE, and with it the whole second
# mechanism it required.
#
# The old rule: mark() refused a protected role because "a down .1 is still
# FrogNet".  True and irrelevant.  The transport does not need to know whether
# an address is philosophically a member; it needs to know whether to dial it.
# Unreachable is unreachable.  Because mark() refused roles, a separate retire()
# set had to exist to stop dialing them -- and that set was memory-only, so a
# real node dropped out of service until the proxy process was restarted.  A
# carve-out meant to protect real nodes from a transient blacklist was the thing
# taking them permanently out of the mix.
#
# There was never any permanence to protect against: this cache is merge-scoped
# and cleared at the top of every merge, so a .1 that is briefly down gets one
# fresh probe on the next merge exactly like anything else.
#
# One state now: it answered, or it did not.  One set, disk-backed, one clear.
def _is_client_range(ip):
    """On the 10/8 plane, a real FrogNet host is at .1 (or admin .2). Addresses
    in the DHCP client range (.3-.254) are never FrogNet nodes, so a failed RPC
    to one - including a silent-drop TimeoutError - is definitive absence.
    Returns False for non-10/8, empty, or the .1/.2 role octets.

    [ONE_STATE_V1] This used to say the role octets were "guarded separately by
    _is_protected_role in mark()". There is no _is_protected_role -- it went
    with the carve-out. This function returns False for .1/.2 only so that the
    CLIENT-RANGE-timeout branch does not fire for them; a refusal or no-route
    still marks a role octet, by design."""
    if not ip or not ip.startswith("10."):
        return False
    last = ip.rsplit(".", 1)[-1]
    if not last.isdigit():
        return False
    return 3 <= int(last) <= 254


_lock = threading.RLock()
_cache = {}             # ip -> ts of the mark (was: a bare set)
_mtime = -1.0


_sent_path = None


# [STAT_IS_NOT_FREE_V1] Cap how often the sentinel is stat()ed.
#
# is_marked() takes a global lock and _reload_locked() then does two
# os.environ.get calls and an os.stat -- a filesystem syscall -- on EVERY call.
# Tolerable while is_marked was consulted once per worker lifetime;
# [NO_WORKER_FOR_A_MARKED_HOST_V1] moved it onto the PER-RPC path in
# _get_worker, so every request began serialising on one lock around a syscall.
#
# Measured 2026-08-09 -- same target, same daemon, only the CLIENT proxy
# differing: the fully-updated node ran 300 concurrent requests in 55.4s
# (5.4 req/s); a node without this change ran the same burst in 4.7s
# (63.9 req/s). Twelve times slower, entirely client-side.
#
# flush()'s docstring records what this must not break: "every other process
# picks that up on its next is_marked() because _reload_locked() stats the file
# per call." Cross-process visibility is the whole point of the disk file. The
# cap is 1.0s: the merge that calls flush() takes seconds, and a mark's own
# expiry window is 60s, so a second of staleness cannot change an outcome. This
# is not "cache it and forget" -- it is "do not stat it 300 times in one
# second".
_STAT_MIN_INTERVAL_S = float(os.environ.get("FROGNET_NF_STAT_INTERVAL_S", "1.0"))
_last_stat_at = -1e9


def _reload_locked():
    """Refresh _cache from the sentinel iff it changed on disk.

    Rate-limited per [STAT_IS_NOT_FREE_V1]. Caller holds _lock.
    """
    global _mtime, _cache, _sent_path, _last_stat_at
    _now = time.monotonic()
    if (_now - _last_stat_at) < _STAT_MIN_INTERVAL_S:
        return
    _last_stat_at = _now
    # [SENTINEL_DIR_HONOURED_V1] If the sentinel PATH changed (an oracle set
    # FROGNET_SENTINEL_DIR after we already cached), drop the cache: the old
    # marks belong to a different file and must not leak across.
    _p = _sentinel()
    if _p != _sent_path:
        _sent_path = _p
        _cache = {}
        _mtime = -1.0
    try:
        st = os.stat(_sentinel())
    except FileNotFoundError:
        if _cache or _mtime != -1.0:
            _cache = {}
            _mtime = -1.0
        return
    except OSError:
        return
    if st.st_mtime == _mtime:
        return
    fresh = {}
    try:
        with open(_sentinel(), "r") as f:
            for line in f:
                parts = line.split("\t")
                ip = parts[0].strip()
                if not ip:
                    continue
                # [NO_FALLBACK_V1] A corrupt timestamp is NOT "epoch 0".
                # ts=0.0 makes the mark look 56 years old, so
                # [MARK_EXPIRES_ON_ITS_OWN_V1] retires it on the next read and
                # the host is re-dialled immediately -- the exact behaviour the
                # mark exists to prevent, arrived at by silently mis-reading one
                # line. Skip the line and name it.
                if len(parts) < 2:
                    print("[not_frognet] %s: line has no timestamp column - "
                          "skipped" % (ip,), flush=True)
                    continue
                try:
                    ts = float(parts[1].strip())
                except ValueError:
                    print("[not_frognet] %s: unparseable timestamp %r - line "
                          "skipped, NOT treated as epoch 0"
                          % (ip, parts[1].strip()), flush=True)
                    continue
                # A host marked more than once keeps the LATEST mark: the retry
                # clock runs from the most recent refusal, not the first.
                if ts >= fresh.get(ip, -1.0):
                    fresh[ip] = ts
    except OSError:
        return
    _cache = fresh
    _mtime = st.st_mtime


# [MARK_EXPIRES_ON_ITS_OWN_V1] How long a mark stands before the host gets one
# fresh probe. Env: FROGNET_NF_RETRY_AFTER_S.
#
# The mark used to stand until a MERGE cleared it, and that is a dependency on
# something that may never happen. It has now failed that way twice on the same
# day: flush() had no caller at all, and once it had one, runMerge bailed with
# `lock_held` and never reached the stage. A negative cache whose only exit is an
# external event is a latch wearing a cache's name -- one refusal took the
# elected databasehost out of a node's world for the life of the proxy process.
#
# The rule is the one already applied to elections: do not REMEMBER whether
# something is there, ASK. The mark's job is to stop a dead-end being re-dialled
# every few seconds, and 60s of quiet does that completely. After that the host
# is worth one probe: if it is still absent it is re-marked instantly with a new
# timestamp and costs one connect; if it came back, it is back, with no merge,
# no flush and no restart.
NF_RETRY_AFTER_S = float(os.environ.get("FROGNET_NF_RETRY_AFTER_S", "60"))


def is_marked(ip):
    """True if ip is a known non-FrogNet AND the mark has not aged into a retry.

    [MARK_EXPIRES_ON_ITS_OWN_V1] An expired mark is DROPPED here, so the caller
    gets exactly one fresh probe rather than a permanent no.
    """
    if not ip:
        return False
    with _lock:
        _reload_locked()
        ts = _cache.get(ip)
        if ts is None:
            return False
        if (time.time() - ts) < NF_RETRY_AFTER_S:
            return True
        # Due for a retry. Drop it from the in-memory view so this process stops
        # refusing immediately; the disk line is left alone because rewriting the
        # file on a read path would make every reader fight for it. A host that
        # is still absent is re-marked by the next failed dial, with a fresh ts,
        # and mark() appends, so the newest line is the one _reload_locked keeps.
        _cache.pop(ip, None)
        print("[not_frognet] %s: mark aged past %.0fs - allowing one fresh probe"
              % (ip, NF_RETRY_AFTER_S), flush=True)
        return False


def mark(ip, reason=""):
    """Record ip as unreachable for the rest of this merge (idempotent).
    Returns True if cached (or already cached), False only for an empty ip.
    [ONE_STATE_V1] No role is exempt -- see the note above _is_client_range."""
    if not ip:
        return False
    with _lock:
        _reload_locked()
        _now = time.time()
        if _cache.get(ip) is not None and (_now - _cache[ip]) < NF_RETRY_AFTER_S:
            return True                      # already marked and still standing
        _cache[ip] = _now
        try:
            os.makedirs(os.path.dirname(_sentinel()), exist_ok=True)
            with open(_sentinel(), "a") as f:
                f.write("%s\t%d\t%s\n" % (ip, int(_now), reason))
            try:
                global _mtime, _last_stat_at
                _mtime = os.stat(_sentinel()).st_mtime
                # [STAT_IS_NOT_FREE_V1] we just stat'd it; start the window here
                # rather than forcing the next reader to stat again.
                _last_stat_at = time.monotonic()
            except OSError:
                pass
        except OSError:
            # best-effort: a failed write just means we re-probe, not fatal
            pass
        return True


# [THREE_STRIKES_V1] A definitive error marks on the THIRD consecutive one, not
# the first.
#
# "Definitive" describes the ERROR, not the HOST. ECONNREFUSED means nothing was
# listening at that instant -- which is equally true of a stray laptop and of a
# real node two seconds into a systemctl restart. Marking on the first one
# cannot tell them apart, and the cost is wildly asymmetric: a laptop marked
# late costs a few wasted dials, while a databasehost marked early goes silent.
#
# Measured 2026-08-08, Seattle3: ONE ECONNREFUSED at 09:02:09 during a two-second
# restart of the elected databasehost, then 28 worker retirements over four
# minutes with zero reconnect attempts. Every sensor upsert 503'd and the
# capability tuple never published.
#
# Three CONSECUTIVE strikes. Any successful contact calls clear_strikes() and
# the count goes back to zero, so a node that is merely slow or briefly
# restarting never accumulates them. A node that is genuinely absent reaches
# three within a few seconds and is marked exactly as before.
#
# The counter lives HERE, not at the call sites, because there are two of them
# with different structure -- the reconnect loop already counts its own connect
# failures, while the dispatch path at transport_semantic.py:2139 counted
# nothing -- and a rule enforced in one place cannot drift out of step with
# itself.
STRIKES_TO_MARK = int(os.environ.get("FROGNET_NF_STRIKES", "3"))
_strikes = {}          # ip -> consecutive definitive failures


def clear_strikes(ip):
    """Any successful contact with ip. Resets its strike count.

    Call this on a completed connect or RPC. Without it the strikes are
    cumulative rather than consecutive, and a node that fails once a day would
    eventually be marked for no reason.
    """
    if not ip:
        return
    with _lock:
        if _strikes.pop(ip, None):
            print("[not_frognet] %s: answered - strike count reset" % (ip,),
                  flush=True)


def strike_count(ip):
    """Consecutive definitive failures recorded for ip."""
    with _lock:
        return _strikes.get(ip, 0)


def mark_if_definitive(ip, exc):
    """If exc is a definitive non-FrogNet signal (refused / no-route /
    net-unreachable), mark ip and return True.  Timeouts, resets, and
    everything else return False - they must not silence a possibly-real peer.
    """
    if not ip:
        return False
    err = getattr(exc, "errno", None)
    if err is None:
        cause = getattr(exc, "__cause__", None)
        if cause is not None:
            err = getattr(cause, "errno", None)
    msg = str(exc)
    definitive = (
        err in DEFINITIVE_ERRNOS
        or isinstance(exc, ConnectionRefusedError)
        or "Connection refused" in msg
        or "No route to host" in msg
        or "Network is unreachable" in msg
        # [CLIENT_RANGE_TIMEOUT_DEFINITIVE_V1] A real FrogNet host lives at the
        # role octet (.1/.2). An on-plane address in the client-lease range
        # (.3-.254) that fails an RPC is a stray LAN box (e.g. a firewalled
        # Windows client that DROPS :9009, yielding a TimeoutError rather than a
        # refusal). For those, a timeout IS definitive absence - they can never
        # be a FrogNet node. mark() still refuses .1/.2, so a slow REAL peer at a
        # role octet is never culled by this branch, preserving the
        # timeout-never-silences-a-real-peer guarantee for actual nodes.
        or _is_client_range(ip)
    )
    if definitive:
        # [ONE_STATE_V1] mark() has NO protected-role guard - the .1/.2 carve-out
        # was deliberately deleted, on the ruling "it's either unreachable, in
        # which case it doesn't matter if it's a FrogNet, or it's not".
        #
        # The comment that used to sit here said the opposite: "mark() returns
        # False for a protected role (.1/.2) - caller then knows this is a real
        # node down and keeps retrying."  It described the guard that had just
        # been removed, so the transport's caller was written believing a
        # databasehost at a role octet was spared. It is not. A .1 that refuses
        # for two seconds is marked exactly like a stray laptop, and that is
        # INTENDED - it is safe only because flush() actually runs. See
        # [FLUSH_HAS_A_CALLER_V1] in the module docstring for what happened when
        # it did not.
        # [THREE_STRIKES_V1] Count it. Mark only on the third.
        with _lock:
            n = _strikes[ip] = _strikes.get(ip, 0) + 1
        _why = ("errno=%s" % err) if err is not None else "refused/no-route"
        if n < STRIKES_TO_MARK:
            print("[not_frognet] %s: strike %d of %d (%s) - NOT marked yet"
                  % (ip, n, STRIKES_TO_MARK, _why), flush=True)
            return False
        _strikes.pop(ip, None)
        return mark(ip, reason="%s after %d consecutive strikes" % (_why, n))
    return False


# [ONE_STATE_V1] retire()/is_retired() are DELETED.
#
# They existed only because mark() refused .1/.2.  With that carve-out gone
# there is one question and one answer, so there is one set.  Callers that
# imported is_retired/retire should call is_marked/mark.
#
# Historical note, because it cost a full day: retire()'s set was module-level
# and had no disk backing, while flush() -- which clears it -- runs in the
# MERGE's interpreter, not the proxy's.  The set that actually gated RPCs was
# in the long-running proxy and nothing ever cleared it.  Peers retired after
# three failed :9009 connects stayed retired until the process was restarted,
# and every request to them failed instantly while ping to the same address
# succeeded.
def flush():
    """Clear the cache - called at the top of every merge.
    Returns the number of entries cleared.

    [FLUSH_HAS_A_CALLER_V1] Invoked from runMerge.bash via
    `python3 -m core.not_frognet flush` (see __main__ at the bottom of this
    file). It runs in the MERGE's interpreter and clears the SENTINEL FILE, and
    every other process picks that up on its next is_marked() because
    _reload_locked() stats the file per call. That indirection is the whole
    point of the disk backing and it is why no proxy restart is needed."""
    global _cache, _mtime
    with _lock:
        n = len(_cache)
        _cache = {}
        _mtime = -1.0
        # [THREE_STRIKES_V1] A flush clears the strikes as well. Leaving them
        # would put a just-forgiven host one failure away from being marked
        # again, which is not what "flush" means.
        _strikes.clear()
        try:
            if os.path.exists(_sentinel()):
                os.remove(_sentinel())
        except OSError:
            pass
        return n


# [FLUSH_HAS_A_CALLER_V1] Command-line entry point. The merge is bash; without
# this there is no way for it to reach flush() and there was, in fact, no way -
# which is the bug. Prints what it cleared so the merge log carries the count.
#
#   python3 -m core.not_frognet flush     -> clear, print count, exit 0
#   python3 -m core.not_frognet status    -> list current marks, exit 0
#
# No fallback: an unrecognised argument exits non-zero rather than silently
# doing nothing, because a flush stage that quietly no-ops is exactly the
# failure this rev exists to end.
if __name__ == "__main__":
    import sys

    _cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if _cmd == "flush":
        _marks = sorted(_cache) if _cache else []
        with _lock:
            _reload_locked()
            _marks = sorted(_cache)
        _n = flush()
        print("[not_frognet] FLUSH cleared %d mark(s) from %s%s"
              % (_n, _sentinel(),
                 (": " + ", ".join(_marks)) if _marks else ""))
        sys.exit(0)
    if _cmd == "status":
        with _lock:
            _reload_locked()
            _marks = sorted(_cache)
        _now = time.time()
        _detail = ", ".join(
            "%s (%.0fs ago, %s)" % (
                _ip, _now - _cache.get(_ip, 0.0),
                "standing" if (_now - _cache.get(_ip, 0.0)) < NF_RETRY_AFTER_S
                else "DUE FOR RETRY")
            for _ip in _marks)
        print("[not_frognet] %s: %d mark(s), retry_after=%.0fs%s"
              % (_sentinel(), len(_marks), NF_RETRY_AFTER_S,
                 (": " + _detail) if _marks else ""))
        sys.exit(0)
    print("usage: python3 -m core.not_frognet {flush|status}", file=sys.stderr)
    sys.exit(2)
