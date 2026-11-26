# Copyright (C) 2016-2026 Fawcett Innovations LLC
# SPDX-License-Identifier: GPL-2.0-only
"""channel_codec.py -- [THE_FRAME_IS_A_LIBRARY_V1] the protocol layer's frame for any open connection, from Python.

The binding of libchannel_frame (ribbit/cpp/src/channel_frame.cpp; its header says what the frame is). A Codec is one
end of one connection and has no socket: pack() gives the bytes to put on the connection, feed() takes the bytes that
arrived. The bytes are core/channel_frame.py's, so the other end may be that file's Link.

    codec = Codec(opener=True, far="media", fnid="...", secrets="/etc/frognet/channel_secrets")
    codec.setting("mediahost", {"driver": "tcp", "number": 0})        # and again whenever the held read fires
    unit = codec.pack("mediahost", body)        # None: messages are being sent again; take next() first
    ... the unit did not go on the connection:  codec.undo()
    for role, sequence, counter, reply, message in codec.feed(arrived): ...
    while (unit := codec.next()) is not None: ...                       # calls this end owes when it is not sending

One thread writes (pack, next, undo) and one reads (feed). A failure raises Gone with the library's reason.
"""
import ctypes
import os
import sys


class Gone(Exception):
    """The connection cannot go on, or this machine cannot make the key: the reason is the text."""


def _load():
    names = ["channel_frame.dll", "libchannel_frame.dll"] if sys.platform == "win32" else ["libchannel_frame.so"]
    here = os.path.dirname(os.path.abspath(__file__))
    dirs = [os.environ.get("CHANNEL_FRAME_LIB", ""), here, os.path.join(here, "..", "bin")]
    for d in dirs:
        for n in names:
            if d and os.path.exists(os.path.join(d, n)):
                return ctypes.CDLL(os.path.abspath(os.path.join(d, n)))
    raise OSError("libchannel_frame not found (looked in %s); build it, or set CHANNEL_FRAME_LIB to its folder" % [d for d in dirs if d])


_L = _load()
_c, _p, _e = ctypes.c_char_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)
_u8, _sz = ctypes.POINTER(ctypes.c_ubyte), ctypes.c_size_t
_L.cf_build.restype = _c
_L.cf_free.argtypes = [_p]
_L.cf_open.restype = _p; _L.cf_open.argtypes = [ctypes.c_int, _c, _c, _c, _e]
_L.cf_close.argtypes = [_p]
_L.cf_setting.argtypes = [_p, _c, ctypes.c_int, _c, ctypes.c_ulonglong, _c, ctypes.c_long, _e]
_L.cf_pack.argtypes = [_p, _c, _c, _sz, ctypes.POINTER(_p), ctypes.POINTER(_sz), _e]
_L.cf_pack_reply.argtypes = [_p, _c, ctypes.c_int, ctypes.c_ulonglong, _c, _sz, ctypes.POINTER(_p), ctypes.POINTER(_sz), _e]
_L.cf_next.argtypes = [_p, ctypes.POINTER(_p), ctypes.POINTER(_sz), _e]
_L.cf_undo.argtypes = [_p, _e]
_L.cf_feed.argtypes = [_p, _c, _sz, _e]
_L.cf_message.argtypes = [_p, ctypes.POINTER(_p), ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_ulonglong), ctypes.POINTER(ctypes.c_int),
                          ctypes.POINTER(_p), ctypes.POINTER(_sz)]
_L.cf_said.restype = _p; _L.cf_said.argtypes = [_p]
_L.cf_running.restype = _p; _L.cf_running.argtypes = [_p]
_L.cf_counts.argtypes = [_p, ctypes.POINTER(ctypes.c_ulonglong * 14)]
_L.cf_take_setting.restype = _p; _L.cf_take_setting.argtypes = [_c, _c, _c, _e]
_L.cf_check_of.restype = _p; _L.cf_check_of.argtypes = [_c, _c, _c, _e]
_L.cf_secret.restype = _p; _L.cf_secret.argtypes = [_c, _c, _e]
_L.cf_key_tag.restype = _p; _L.cf_key_tag.argtypes = [_c, _c, ctypes.c_ulonglong, ctypes.c_long, _c, _e]
BUILD = _L.cf_build().decode()


def _text(p):
    s = ctypes.string_at(p).decode("utf-8", "replace")
    _L.cf_free(p)
    return s


def _raise(err):
    raise Gone(_text(err.value) if err.value else "libchannel_frame gave no reason")


def _fields(value):
    """A setting's JSON as the library takes it: name=value lines, each value as Python prints it."""
    return "".join("%s=%s\n" % (k, ("true" if v else "false") if isinstance(v, bool) else v) for k, v in sorted(value.items()) if k != "ts").encode()


def check_of(role, value, secrets=""):
    """The check a setting for this Role must carry: made with this machine's secret for the Role."""
    err = ctypes.c_void_p()
    p = _L.cf_check_of(role.encode(), _fields({k: v for k, v in value.items() if k != "check"}), secrets.encode(), ctypes.byref(err))
    return _text(p) if p else _raise(err)


def secret_of(role, secrets=""):
    """This machine's secret for a Role, as a program may know it: {"own", "step", "fingerprint"}. Raises Gone when it holds none."""
    err = ctypes.c_void_p()
    p = _L.cf_secret(role.encode(), secrets.encode(), ctypes.byref(err))
    if not p:
        _raise(err)
    got = dict(line.split("=", 1) for line in _text(p).splitlines())
    return {"own": got["own"] == "1", "step": int(got["step"]), "fingerprint": got["fingerprint"]}


def key_tag(role, sender, number, step, secrets=""):
    """8 characters of a hash of the key `sender` sends under for this setting, for a screen. Never the key."""
    err = ctypes.c_void_p()
    p = _L.cf_key_tag(role.encode(), sender.encode(), int(number), int(step), secrets.encode(), ctypes.byref(err))
    if not p:
        _raise(err)
    return _text(p)


def take_setting(role, value, secrets=""):
    """A setting as read from its tuple: {"taken", "moved", "why", "fingerprint", "driver", "number", "protection", "step"}.
    Taken only if its check verifies under this machine's secret for the Role (a machine with no secret takes it)."""
    err = ctypes.c_void_p()
    p = _L.cf_take_setting(role.encode(), _fields(value), secrets.encode(), ctypes.byref(err))
    if not p:
        _raise(err)
    got = dict(line.split("=", 1) for line in _text(p).splitlines())
    return {"taken": got["taken"] == "1", "moved": got["moved"] == "1", "why": got["why"], "fingerprint": got["fingerprint"],
            "driver": got["driver"], "number": int(got["number"]), "protection": got["protection"], "step": int(got["step"])}


class Codec:
    def __init__(self, opener, far="", fnid="", secrets=""):
        err = ctypes.c_void_p()
        self._h = _L.cf_open(1 if opener else 0, far.encode(), fnid.encode(), secrets.encode(), ctypes.byref(err))
        if not self._h:
            _raise(err)

    def close(self):
        h, self._h = self._h, None
        if h:
            _L.cf_close(h)

    def setting(self, role, value):
        """The setting this process holds for a Role now (None: it holds none): {driver, number, protection, step}."""
        err = ctypes.c_void_p()
        v = value or {}
        if _L.cf_setting(self._h, role.encode(), 1 if value is not None else 0, str(v.get("driver") or "").encode(), int(v.get("number") or 0),
                         str(v.get("protection") or "").encode(), int(v.get("step") if v.get("step") is not None else -1), ctypes.byref(err)) < 0:
            _raise(err)

    def _unit(self, rc, unit, n, err):
        if rc < 0:
            _raise(err)
        if rc == 0:
            return None
        b = ctypes.string_at(unit.value, n.value)
        _L.cf_free(unit)
        return b

    def pack(self, role, message):
        unit, n, err = ctypes.c_void_p(), _sz(), ctypes.c_void_p()
        return self._unit(_L.cf_pack(self._h, role.encode(), bytes(message), len(message), ctypes.byref(unit), ctypes.byref(n), ctypes.byref(err)), unit, n, err)

    def pack_reply(self, role, sequence, counter, message):
        unit, n, err = ctypes.c_void_p(), _sz(), ctypes.c_void_p()
        return self._unit(_L.cf_pack_reply(self._h, role.encode(), sequence, counter, bytes(message), len(message), ctypes.byref(unit), ctypes.byref(n),
                                           ctypes.byref(err)), unit, n, err)

    def next(self):
        unit, n, err = ctypes.c_void_p(), _sz(), ctypes.c_void_p()
        return self._unit(_L.cf_next(self._h, ctypes.byref(unit), ctypes.byref(n), ctypes.byref(err)), unit, n, err)

    def undo(self):
        err = ctypes.c_void_p()
        if _L.cf_undo(self._h, ctypes.byref(err)) < 0:
            _raise(err)

    def feed(self, arrived):
        """Bytes as they arrived, in any pieces -> [(role, sequence, counter, reply, message)] in the order opened."""
        err = ctypes.c_void_p()
        if _L.cf_feed(self._h, bytes(arrived), len(arrived), ctypes.byref(err)) < 0:
            _raise(err)
        out = []
        role, seq, counter, reply, data, n = ctypes.c_void_p(), ctypes.c_int(), ctypes.c_ulonglong(), ctypes.c_int(), ctypes.c_void_p(), _sz()
        while _L.cf_message(self._h, ctypes.byref(role), ctypes.byref(seq), ctypes.byref(counter), ctypes.byref(reply), ctypes.byref(data), ctypes.byref(n)):
            message = ctypes.string_at(data.value, n.value)
            _L.cf_free(data)
            out.append((_text(role.value), seq.value, counter.value, bool(reply.value), message))
        return out

    def said(self):
        return _text(_L.cf_said(self._h)).splitlines()

    def running(self):
        return dict(line.split("\t", 1) for line in _text(_L.cf_running(self._h)).splitlines())

    def counts(self):
        v = (ctypes.c_ulonglong * 14)()
        _L.cf_counts(self._h, ctypes.byref(v))
        names = ("protocol", "encryption", "not opened", "again", "received")
        return {"sent": v[0], "received": v[1], "not_opened": v[2], "sent_again": v[3],
                "calls": {name: [v[4 + 2 * i], v[5 + 2 * i]] for i, name in enumerate(names)}}
