#!/usr/bin/env python3
# Copyright (C) 2016-2026 Fawcett Innovations LLC
# SPDX-License-Identifier: GPL-2.0-only
"""lisper.py -- the LISP client library (liblisper.so / lisper.dll) from Python, through ctypes.

    import lisper
    c = lisper.Client("ram.example.net", 8800)
    c.add_site("0", "198.18.0.0/16", "", True, 1, "the-key")
    notify_hex = c.register(packet_hex, "192.0.2.1")
    print(c.resolve("0", "198.18.1.1"))
"""
import ctypes, json, os, sys

def _load():
    here = os.path.dirname(os.path.abspath(__file__))
    name = "lisper.dll" if sys.platform == "win32" else ("liblisper.dylib" if sys.platform == "darwin" else "liblisper.so")
    for d in (here, os.path.join(here, "..", "build"), os.path.join(here, "build")):
        p = os.path.join(d, name)
        if os.path.exists(p):
            return ctypes.CDLL(p)
    raise OSError("%s not found next to lisper.py or in ../build (build it: tools/qualify.sh or client/build.sh)" % name)

_lib = _load()
_c, _p = ctypes.c_char_p, ctypes.c_void_p
_e = ctypes.POINTER(ctypes.c_void_p)
_lib.lisper_open.restype = _p;       _lib.lisper_open.argtypes = [_c, ctypes.c_int, _e]
_lib.lisper_close.argtypes = [_p]
_lib.lisper_operation.restype = _p;  _lib.lisper_operation.argtypes = [_p, _c, _c, _e]
_lib.lisper_register.restype = _p;   _lib.lisper_register.argtypes = [_p, _c, _c, _e]
_lib.lisper_resolve.restype = _p;    _lib.lisper_resolve.argtypes = [_p, _c, _c, _c, _e]
_lib.lisper_add_site.restype = ctypes.c_int;    _lib.lisper_add_site.argtypes = [_p, _c, _c, _c, ctypes.c_int, ctypes.c_int, _c, _e]
_lib.lisper_delete_site.restype = ctypes.c_int; _lib.lisper_delete_site.argtypes = [_p, _c, _c, _c, _e]
_lib.lisper_free.argtypes = [_p]

class LisperError(RuntimeError):
    pass

def _take(p):
    s = ctypes.cast(p, ctypes.c_char_p).value.decode(); _lib.lisper_free(p); return s

def _check(ok, err):
    if not ok:
        raise LisperError(_take(err.value) if err.value else "lisper call failed")

def _b(s): return s.encode() if isinstance(s, str) else s

class Client:
    def __init__(self, host, port):
        err = ctypes.c_void_p(); self._h = _lib.lisper_open(_b(host), int(port), ctypes.byref(err)); _check(self._h, err)
    def close(self):
        if self._h: _lib.lisper_close(self._h); self._h = None
    def __enter__(self): return self
    def __exit__(self, *a): self.close()
    def _str(self, fn, *args):
        err = ctypes.c_void_p(); p = fn(self._h, *[_b(a) for a in args], ctypes.byref(err)); _check(p, err); return _take(p)
    def operation(self, name, args):
        return json.loads(self._str(_lib.lisper_operation, name, json.dumps(args)))
    def register(self, packet_hex, source):
        return self._str(_lib.lisper_register, packet_hex, source)
    def resolve(self, iid, prefix, group=""):
        return json.loads(self._str(_lib.lisper_resolve, iid, prefix, group))
    def add_site(self, iid, prefix, group, accept_more_specifics, key_id, password):
        err = ctypes.c_void_p()
        _check(_lib.lisper_add_site(self._h, _b(iid), _b(prefix), _b(group), int(bool(accept_more_specifics)), int(key_id), _b(password), ctypes.byref(err)) == 0, err)
    def delete_site(self, iid, prefix, group=""):
        err = ctypes.c_void_p(); _check(_lib.lisper_delete_site(self._h, _b(iid), _b(prefix), _b(group), ctypes.byref(err)) == 0, err)
