# Copyright (C) 2016-2026 Fawcett Innovations LLC
# SPDX-License-Identifier: GPL-2.0-only
"""The Communicator's memory (comms-ram) from Python, through libcomms_tuples."""
import ctypes, json, os, sys

SERVICE = "communicator"


def _load():
    names = ["comms_tuples.dll", "libcomms_tuples.dll"] if sys.platform == "win32" else ["libcomms_tuples.so"]
    here = os.path.dirname(os.path.abspath(__file__))
    dirs = [os.environ.get("FROGCOMMS_LIB", ""), here, os.path.join(here, "..", "..", "build"),
            os.path.join(here, "..", "..", "build-msvc"), os.path.join(here, "..", "..", "dist")]
    for d in dirs:
        for n in names:
            p = os.path.join(d, n)
            if d and os.path.exists(p):
                return ctypes.CDLL(os.path.abspath(p))
    raise OSError("libcomms_tuples not found (looked in %s); build it, or set FROGCOMMS_LIB to its folder" % dirs)


_lib = _load()
_c, _p, _e = ctypes.c_char_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)
_lib.ct_open.restype = _p; _lib.ct_open.argtypes = [_c, ctypes.c_int, _e]
_lib.ct_close.argtypes = [_p]
_lib.ct_shutdown.argtypes = [_p]
_lib.ct_put.restype = ctypes.c_longlong; _lib.ct_put.argtypes = [_p, _c, _c, _c, _c, ctypes.c_int, _e]
_lib.ct_get.restype = _p; _lib.ct_get.argtypes = [_p, _c, ctypes.c_int, ctypes.c_double, ctypes.c_int, _c, ctypes.c_longlong, _e]
_lib.ct_remove.restype = ctypes.c_int; _lib.ct_remove.argtypes = [_p, _c, _c, _c, _e]
_lib.ct_my_ip.restype = _p; _lib.ct_my_ip.argtypes = [_p, _e]
_lib.ct_free.argtypes = [_p]


class MemoryError_(Exception):
    """The memory could not be reached or refused the request. Never swallowed: the caller decides."""


def _take(p):
    s = ctypes.cast(p, ctypes.c_char_p).value.decode()
    _lib.ct_free(p)
    return s


def _check(err, what):
    if err.value:
        raise MemoryError_("%s: %s" % (what, _take(err.value)))


class Client:
    """One session to comms-ram (vendor "comms")."""

    def __init__(self, host, port):
        err = ctypes.c_void_p()
        self._h = _lib.ct_open(host.encode(), int(port), ctypes.byref(err))
        _check(err, "%s:%d" % (host, port))

    def close(self):
        if self._h:
            _lib.ct_close(self._h)
            self._h = None

    def shutdown(self):
        """Ends a held read in another thread now ([A_HELD_READ_ENDS_AT_ONCE_V1])."""
        if self._h:
            _lib.ct_shutdown(self._h)

    def put(self, var, scope, value, own=True):
        err = ctypes.c_void_p()
        r = _lib.ct_put(self._h, SERVICE.encode(), var.encode(), scope.encode(), json.dumps(value).encode(), int(own), ctypes.byref(err))
        _check(err, "put %s" % var)
        return r

    def get(self, fresh_s=0, wait_s=0.0, min_rows=0, var="", after=-1):
        err = ctypes.c_void_p()
        p = _lib.ct_get(self._h, SERVICE.encode(), int(fresh_s), float(wait_s), int(min_rows), var.encode(), int(after), ctypes.byref(err))
        _check(err, "get %s" % (var or "*"))
        return json.loads(_take(p))

    def remove(self, var, scope):
        err = ctypes.c_void_p()
        r = _lib.ct_remove(self._h, SERVICE.encode(), var.encode(), scope.encode(), ctypes.byref(err))
        _check(err, "remove %s" % var)
        return r == 1

    def my_ip(self):
        err = ctypes.c_void_p()
        p = _lib.ct_my_ip(self._h, ctypes.byref(err))
        _check(err, "my_ip")
        return _take(p)
