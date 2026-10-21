#!/usr/bin/env python3
# Copyright (C) 2016-2026 Fawcett Innovations LLC
# SPDX-License-Identifier: GPL-2.0-only
"""chat.py -- the chat client library (libchat.so / chat.dll) from Python, through ctypes.

    import chat
    c = chat.Client("ram.example.net", 8800)
    c.send("Bob", "Dave", "hello")
    after = c.position("Dave")
    for m in c.receive("Dave", after, wait_s=25): ...     # waits for Bob's reply; `after` moves past what it returns

The wire is the library's: FNW1 to chat-ram, semantically compressed, on the session's own sockets. This file only
declares the C functions and turns their JSON into Python values.
"""
import ctypes, json, os, sys

def _load():
    here = os.path.dirname(os.path.abspath(__file__))
    name = "chat.dll" if sys.platform == "win32" else ("libchat.dylib" if sys.platform == "darwin" else "libchat.so")
    for d in (here, os.path.join(here, "..", "build")):
        p = os.path.join(d, name)
        if os.path.exists(p):
            return ctypes.CDLL(p)
    raise OSError("%s not found next to chat.py or in ../build (build it: ./build.sh)" % name)

_lib = _load()
_c = ctypes.c_char_p
_err = ctypes.POINTER(ctypes.c_void_p)
_lib.chat_open.restype = ctypes.c_void_p;    _lib.chat_open.argtypes = [_c, ctypes.c_int, _err]
_lib.chat_close.argtypes = [ctypes.c_void_p]
_lib.chat_send.restype = ctypes.c_int;       _lib.chat_send.argtypes = [ctypes.c_void_p, _c, _c, _c, _err]
_lib.chat_position.restype = ctypes.c_int64; _lib.chat_position.argtypes = [ctypes.c_void_p, _c, _err]
_lib.chat_receive.restype = ctypes.c_void_p; _lib.chat_receive.argtypes = [ctypes.c_void_p, _c, ctypes.POINTER(ctypes.c_int64), ctypes.c_double, _err]
_lib.chat_free.argtypes = [ctypes.c_void_p]

class ChatError(RuntimeError):
    pass

def _take(p):
    s = ctypes.cast(p, ctypes.c_char_p).value.decode(); _lib.chat_free(p); return s

def _check(ok, err):
    if not ok:
        raise ChatError(_take(err.value) if err.value else "chat call failed")

class Client:
    def __init__(self, host, port):
        err = ctypes.c_void_p()
        self._h = _lib.chat_open(host.encode(), int(port), ctypes.byref(err))
        _check(self._h, err)
    def close(self):
        if self._h: _lib.chat_close(self._h); self._h = None
    def __enter__(self): return self
    def __exit__(self, *a): self.close()
    def send(self, to, frm, text):
        err = ctypes.c_void_p(); _check(_lib.chat_send(self._h, to.encode(), frm.encode(), text.encode(), ctypes.byref(err)) == 0, err)
    def position(self, me):
        err = ctypes.c_void_p(); p = _lib.chat_position(self._h, me.encode(), ctypes.byref(err)); _check(p >= 0, err); return p
    def receive(self, me, after, wait_s=0.0):
        """(messages, new_after): what was written to me after `after`, waiting up to wait_s for the first."""
        err = ctypes.c_void_p(); a = ctypes.c_int64(after)
        p = _lib.chat_receive(self._h, me.encode(), ctypes.byref(a), float(wait_s), ctypes.byref(err)); _check(p, err)
        return json.loads(_take(p)), a.value
