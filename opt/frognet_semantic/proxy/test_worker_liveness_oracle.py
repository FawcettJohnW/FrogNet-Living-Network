#!/opt/frognet_semantic/venv/bin/python3
"""test_worker_liveness_oracle.py — [WORKER_EXIT_REASON_V1] Tier 0 gate.

The New-York-2 wedge: a _DaemonWorker's writer and cleanup loops both gate on
`while not self._stopped.is_set()`. When the flag was set the loops returned in
silence, the worker stayed registered in _WORKERS, _get_worker kept handing it
out, and every call() enqueued onto a queue with no consumer and blocked the
full 60s safety cap. Observed: 180 safety-cap timeouts in five minutes with
zero [DIAG-WRITER] / [DIAG-READER] / [RPC-TRACE] / [CLEANUP] lines from the
process, because the threads that emit them had exited.

These assertions FAIL on the shipped code and PASS after. Run from tree root:

    python3 proxy/test_worker_liveness_oracle.py

Set FROGNET_OLD_TREE to a pre-change tree to see the before-state confirmed.
"""
import ast
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OLD = os.environ.get("FROGNET_OLD_TREE", "")

_results = []


class _Skip(Exception):
    """This check could not run here - NOT a pass."""


def check(name, fn):
    try:
        fn()
        _results.append(("PASS", name, ""))
    except _Skip as e:
        _results.append(("SKIP", name, str(e)))
    except AssertionError as e:
        _results.append(("FAIL", name, str(e)))
    except Exception as e:
        _results.append(("ERROR", name, f"{type(e).__name__}: {e}"))


def _src(rel, tree=None):
    return open(os.path.join(tree or ROOT, rel)).read()


def _fn(src, name):
    for n in ast.walk(ast.parse(src)):
        if isinstance(n, ast.FunctionDef) and n.name == name:
            return n
    raise AssertionError(f"function {name} not found")


# -- behavioural: a retired worker is never handed out ----------------

def _load_transport():
    """Import transport_semantic far enough to exercise the registry.
    Skips cleanly (returns None) when the tree's deps aren't importable."""
    sys.path.insert(0, ROOT)
    try:
        from proxy import transport_semantic as ts
        return ts
    except ImportError as e:
        # Dep-gated: off-box the tree's core.* deps are not importable, so the
        # three behavioural checks below cannot run and report SKIP. They DO
        # run on a node. The structural checks carry the gate either way -
        # do not read a green run off-box as proof the behaviour is right.
        _results.append(("SKIP", "transport_semantic import",
                         f"{type(e).__name__}: {e}"))
        return None


def t_retired_worker_is_deregistered():
    """_retire() must remove the worker from _WORKERS. The nf-marked path in
    _cleanup_loop set _stopped and broke WITHOUT de-registering; that is the
    wedge."""
    ts = _load_transport()
    if ts is None:
        raise _Skip("transport_semantic not importable off-box")
    w = ts._get_worker("10.99.99.1", 9009, 0)
    key = ("10.99.99.1", 9009, 0)
    assert ts._WORKERS.get(key) is w, "worker not registered"
    w._retire("oracle")
    assert ts._WORKERS.get(key) is not w, \
        "_retire() left the stopped worker in _WORKERS"


def t_stopped_worker_not_reissued():
    """_get_worker must never return a stopped worker."""
    ts = _load_transport()
    if ts is None:
        raise _Skip("transport_semantic not importable off-box")
    key = ("10.99.99.2", 9009, 0)
    w = ts._get_worker(*key)
    w._stopped.set()                            # stop WITHOUT de-registering
    w._stopped_reason = "oracle_forced"
    ts._WORKERS[key] = w                        # ensure it is still cached
    w2 = ts._get_worker(*key)
    assert w2 is not w, "_get_worker handed back a stopped worker"
    assert not w2.is_stopped(), "replacement worker is already stopped"


def t_call_refuses_stopped_worker_fast():
    """call() on a retired worker must raise at once, not after the 60s cap."""
    ts = _load_transport()
    if ts is None:
        raise _Skip("transport_semantic not importable off-box")
    w = ts._get_worker("10.99.99.3", 9009, 0)
    w._retire("oracle")
    t0 = time.time()
    try:
        w.call(b"x")
    except Exception as e:
        dt = time.time() - t0
        assert dt < 5.0, f"took {dt:.1f}s to refuse (safety cap is 60s)"
        assert "retired" in str(e), f"error does not name the retirement: {e!r}"
        return
    raise AssertionError("call() on a retired worker did not raise")


# -- structural: every long-running loop names its exit ---------------

LOOPS = [
    ("proxy/transport_semantic.py", "_cleanup_loop"),
    ("proxy/transport_semantic.py", "_write_loop"),
    ("proxy/transport_semantic.py", "_read_loop"),
    ("daemon/engine/session.py", "_read_loop"),
    ("daemon/engine/session.py", "_write_loop"),
    ("proxy/proxy_metrics.py", "_flusher_loop"),
    ("daemon/daemon_metrics.py", "_flusher_loop"),
    ("proxy/cache/semcache_db.py", "_eviction_loop"),
    ("daemon/cache/semcache_db.py", "_eviction_loop"),
    ("daemon/cache/semcache_db.py", "_flusher_loop"),
    ("daemon/pool_resizer.py", "_timer_loop"),
    ("daemon/pool_resizer.py", "_sentinel_loop"),
    ("proxy/frognet_media_planes.py", "_on_conn"),
]

MARKERS = ("WORKER_EXIT_REASON_V1", "READER_EXIT_REASON_V1",
           "exited", "DIED", "died")


def t_every_thread_loop_names_its_exit():
    missing = []
    for rel, name in LOOPS:
        p = os.path.join(ROOT, rel)
        if not os.path.exists(p):
            continue
        body = ast.unparse(_fn(open(p).read(), name))
        if not any(m in body for m in MARKERS):
            missing.append(f"{rel}:{name}")
    assert not missing, ("thread loops that can exit silently: "
                         + ", ".join(missing))


def t_stop_flag_set_only_by_retire():
    """_stopped.set() must appear exactly once - inside _retire(). Every other
    site is a stop that skips de-registration."""
    src = _src("proxy/transport_semantic.py")
    code_lines = [l for l in src.split("\n")
                  if "_stopped.set()" in l and not l.strip().startswith("#")]
    assert len(code_lines) == 1, (
        f"{len(code_lines)} site(s) set _stopped directly; only _retire() may: "
        + "; ".join(l.strip() for l in code_lines))
    retire = ast.unparse(_fn(src, "_retire"))
    assert "_stopped.set()" in retire, "_retire() does not set the flag"
    assert "_WORKERS.pop" in retire, "_retire() does not de-register"


def t_safety_cap_message_is_conditional():
    """The 60s message asserted 'cleanup thread appears stalled' even when the
    thread had exited. It must consult the threads."""
    src = _src("proxy/transport_semantic.py")
    call = ast.unparse(_fn(src, "call"))
    assert "is_alive()" in call, \
        "call() does not check thread liveness before blaming cleanup"
    assert "appears stalled" not in call, \
        "call() still asserts 'cleanup thread appears stalled' unconditionally"


def t_old_tree_fails_these():
    """Recorded before-state."""
    if not OLD:
        return
    src = _src("proxy/transport_semantic.py", OLD)
    sets = [l for l in src.split("\n")
            if "_stopped.set()" in l and not l.strip().startswith("#")]
    assert len(sets) > 1, "expected the old tree to set _stopped from >1 site"
    call = ast.unparse(_fn(src, "call"))
    assert "appears stalled" in call, \
        "expected the old tree to assert 'cleanup thread appears stalled'"
    gw = ast.unparse(_fn(src, "_get_worker"))
    assert "is_stopped" not in gw, \
        "expected the old _get_worker to have no liveness check"


for _n, _f in sorted(globals().items()):
    if _n.startswith("t_"):
        check(_n[2:], _f)

_w = max(len(r[1]) for r in _results)
for _st, _n, _m in _results:
    print(f"{_st:6} {_n:<{_w}}  {_m}")
_bad = sum(1 for r in _results if r[0] not in ("PASS", "SKIP"))
_skipped = sum(1 for r in _results if r[0] == "SKIP")
print(f"\n{len(_results) - _bad - _skipped}/{len(_results)} passed"
      + (f", {_skipped} SKIPPED (not run here - see notes)" if _skipped else ""))
sys.exit(1 if _bad else 0)
