# Copyright (C) 2016-2026 Fawcett Innovations LLC
# SPDX-License-Identifier: GPL-2.0-only
#
# ram.py -- ribbit::Space from Python (build/libspace.so, space_c.cpp): the one publish/subscribe library on the memory,
# the one the MQTT and Redis conversions are built on. Used for the control region and for conditions D and D-all.
# (Condition C uses the chat example's own client, libchat.)
#
#   s = Space("HOST:PORT")              one FNWP session to <vendor>-ram (default chat-ram)
#   s.write(space, topic, inst, bag)    this process's own cell; a write is the whole of it
#   s.follow(space, topic, on, after)   a standing follow on a family (topic "" = the whole space): the memory streams
#                                       each batch as it lands and on(rows) is given it, in the family's write order;
#                                       ended(why) once if the session ends
#   s.read(space, topic, inst)          what a family holds now
import ctypes, json, os, threading

HERE = os.path.dirname(os.path.abspath(__file__))
LIB = os.environ.get("OBSVIS_LIB", os.path.join(HERE, "build"))
_l = ctypes.CDLL(os.path.join(LIB, "libspace.so"))
_p, _c, _e = ctypes.c_void_p, ctypes.c_char_p, ctypes.POINTER(ctypes.c_void_p)
ON_ROWS = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_char_p)
ON_END = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_char_p)
_l.space_open.restype = _p; _l.space_open.argtypes = [_c, _c, _e]
_l.space_close.argtypes = [_p]
_l.space_write.restype = ctypes.c_int; _l.space_write.argtypes = [_p, _c, _c, _c, _c, _e]
_l.space_linger.restype = ctypes.c_int; _l.space_linger.argtypes = [_p, _c, _c, ctypes.c_int, _e]
_l.space_read.restype = _p; _l.space_read.argtypes = [_p, _c, _c, _c, ctypes.c_longlong, ctypes.c_double, _e]
_l.space_follow.restype = ctypes.c_int; _l.space_follow.argtypes = [_p, _c, _c, ctypes.c_longlong, ON_ROWS, ON_END, _p, _e]
_l.space_newest.restype = ctypes.c_longlong; _l.space_newest.argtypes = [_p, _c, _c, _e]
_l.space_flush.restype = ctypes.c_int; _l.space_flush.argtypes = [_p, ctypes.c_double]
_l.space_stats.restype = _p; _l.space_stats.argtypes = [_p]
_l.space_free.argtypes = [_p]


class RamError(RuntimeError):
    pass


def _take(p):
    s = ctypes.cast(p, ctypes.c_char_p).value.decode()
    _l.space_free(p)
    return s


def _fail(err, what):
    raise RamError("%s: %s" % (what, _take(err.value) if err.value else "failed"))


class Space:
    def __init__(self, where, vendor="chat"):
        err = ctypes.c_void_p()
        self.where = where
        self.h = _l.space_open(where.encode(), vendor.encode(), ctypes.byref(err))
        if not self.h:
            _fail(err, "open %s-ram at %s" % (vendor, where))
        self._keep = []                                  # the follows' callbacks live as long as the session

    def write(self, space, topic, inst, bag):
        err = ctypes.c_void_p()
        if _l.space_write(self.h, space.encode(), topic.encode(), inst.encode(), json.dumps(bag, separators=(",", ":")).encode(), ctypes.byref(err)) != 0:
            _fail(err, "write %s/%s/%s" % (space, topic, inst))

    def read(self, space, topic, inst="", after=-1, wait_s=0.0):
        err = ctypes.c_void_p()
        p = _l.space_read(self.h, space.encode(), topic.encode(), inst.encode(), int(after), float(wait_s), ctypes.byref(err))
        if not p:
            _fail(err, "read %s/%s" % (space, topic))
        return json.loads(_take(p))

    def follow(self, space, topic, on, after=-1, ended=None):
        def rows(_ctx, j):
            on(json.loads(j.decode()))
        def end(_ctx, why):
            if ended: ended(why.decode())
        cb, ce = ON_ROWS(rows), ON_END(end)
        self._keep += [cb, ce]
        err = ctypes.c_void_p()
        if _l.space_follow(self.h, space.encode(), topic.encode(), int(after), cb, ce, None, ctypes.byref(err)) != 0:
            _fail(err, "follow %s/%s" % (space, topic))

    def flush(self, timeout_s=5.0):
        return bool(_l.space_flush(self.h, float(timeout_s)))

    def stats(self):
        return _take(_l.space_stats(self.h))

    def close(self):
        if self.h:
            _l.space_close(self.h); self.h = None


class Collect:
    """a standing follow on one family of the control region, gathered by instance: wait() returns when every name
    wanted is there (or the deadline passes) -- the follow is the only thing that wakes it"""
    def __init__(self, sp, space, topic):
        self.got, self.cv = {}, threading.Condition()
        sp.follow(space, topic, self._on, after=0)

    def _on(self, rows):
        with self.cv:
            for r in rows:
                if not r["gone"] and r["bag"] is not None:
                    self.got[r["instance"]] = r["bag"]
            self.cv.notify_all()

    def wait(self, names, deadline):
        import time
        with self.cv:
            while not set(names) <= set(self.got):
                left = deadline - time.time()
                if left <= 0:
                    break
                self.cv.wait(left)
            return dict(self.got)
