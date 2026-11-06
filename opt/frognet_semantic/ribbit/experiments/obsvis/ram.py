# Copyright (C) 2016-2026 Fawcett Innovations LLC
# SPDX-License-Identifier: GPL-2.0-only
#
# ram.py -- the default Ribbit API (ribbit/defaults/api: libtuples.so, ribbit::TuplesClient behind a C ABI) from
# Python, as the experiment uses it: put, and a held read of one var after a write-order id (tuples_get_after).
# Used for the control region and for conditions D and D-all. Condition C uses the chat example's own libchat.
import ctypes, json, os

HERE = os.path.dirname(os.path.abspath(__file__))
LIB = os.environ.get("OBSVIS_LIB", os.path.join(HERE, "build"))
_l = ctypes.CDLL(os.path.join(LIB, "libtuples.so"))
_p, _c, _e = ctypes.c_void_p, ctypes.c_char_p, ctypes.POINTER(ctypes.c_void_p)
_l.tuples_open.restype = _p; _l.tuples_open.argtypes = [_c, _c, ctypes.c_int, _e]
_l.tuples_close.argtypes = [_p]
_l.tuples_put.restype = ctypes.c_int64; _l.tuples_put.argtypes = [_p, _c, _c, _c, _c, ctypes.c_int, _e]
_l.tuples_get_after.restype = _p; _l.tuples_get_after.argtypes = [_p, _c, _c, ctypes.c_double, ctypes.c_int64, _e]
_l.tuples_free.argtypes = [_p]


class RamError(RuntimeError):
    pass


def _take(p):
    s = ctypes.cast(p, ctypes.c_char_p).value.decode()
    _l.tuples_free(p)
    return s


def _fail(err, what):
    raise RamError("%s: %s" % (what, _take(err.value) if err.value else "failed"))


class Ram:
    """one session to <vendor>-ram at host:port"""
    def __init__(self, host, port, vendor="chat"):
        err = ctypes.c_void_p()
        self.h = _l.tuples_open(vendor.encode(), host.encode(), int(port), ctypes.byref(err))
        if not self.h:
            _fail(err, "open %s-ram at %s:%s" % (vendor, host, port))

    def close(self):
        if self.h:
            _l.tuples_close(self.h); self.h = None

    def put(self, svc, var, scope, value, own=False):
        err = ctypes.c_void_p()
        i = _l.tuples_put(self.h, svc.encode(), var.encode(), scope.encode(), json.dumps(value).encode(), 1 if own else 0, ctypes.byref(err))
        if i < 0:
            _fail(err, "put %s.%s.%s" % (svc, var, scope))
        return i

    def get_after(self, svc, var, after=-1, wait_s=0.0):
        """rows of (svc, var) written after `after` ({var, scope, value, id, ...}), held up to wait_s until one is"""
        err = ctypes.c_void_p()
        p = _l.tuples_get_after(self.h, svc.encode(), var.encode(), float(wait_s), int(after), ctypes.byref(err))
        if not p:
            _fail(err, "read %s.%s after %s" % (svc, var, after))
        return json.loads(_take(p))
