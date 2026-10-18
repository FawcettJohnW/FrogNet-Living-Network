# ribbit.py -- the kart game's memory: one permanent FNW1 session per region, through libkartram (kartram.cpp, a C
# interface over this tree's frogram::Session). [KART_ON_THE_SESSION_V1]
#
#   m = Memory(host, port)                       one session to the region (api /ram.php)
#   m.write(s, v, i, bag, own=True)              posted: it goes out and this returns; nothing is waited for.
#                                                own=True: the region writes the tuple {} when this session ends
#   m.read(s, v, i, after=-1, wait_s=0)          a family read (i "" = any); after>=0 with wait_s>0 is the trigger,
#                                                held by the region until something is written past `after`
#   for changed in m.follow(s, v): ...           the family once, then only what is written to it, as it is written
#
# [RIBBIT_PY_SAYS_WHY_V1] A failure raises RibbitError naming the region, the call and the cause. There is no second
# way to reach the memory: no library, no game.
import ctypes, json, os, sys


class RibbitError(Exception):
    pass


def _load():
    here = os.path.dirname(os.path.abspath(__file__))
    name = "kartram.dll" if os.name == "nt" else "libkartram.so"
    path = os.path.join(os.environ.get("KARTRAM_LIB") or here, name)
    if not os.path.exists(path):
        raise RibbitError(f"{path} is not there: the game needs it beside ribbit.py (or KARTRAM_LIB=<its directory>)")
    lib = ctypes.CDLL(path)
    c, p, e = ctypes.c_char_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)
    lib.kr_open.restype = p;  lib.kr_open.argtypes = [c, ctypes.c_int, c, e]
    lib.kr_close.argtypes = [p]
    lib.kr_free.argtypes = [p]
    lib.kr_write.restype = ctypes.c_int; lib.kr_write.argtypes = [p, c, c, c, c, ctypes.c_int, e]
    lib.kr_read.restype = p;  lib.kr_read.argtypes = [p, c, c, c, ctypes.c_longlong, ctypes.c_double, e]
    lib.kr_call.restype = p;  lib.kr_call.argtypes = [p, c, c, c, e]
    lib.kr_call_text.restype = p; lib.kr_call_text.argtypes = [p, c, c, c, ctypes.c_double, e]
    lib.kr_refused.restype = ctypes.c_longlong; lib.kr_refused.argtypes = [p]
    return lib


_lib = None
HOLD_S = 30.0          # the region holds a read at most 30 s; a hold that ends with nothing written is simply held again


def _text(lib, ptr):
    s = ctypes.cast(ptr, ctypes.c_char_p).value.decode("utf-8", "replace"); lib.kr_free(ptr); return s


class Memory:
    def __init__(self, host, port, api="/ram.php"):
        global _lib
        if _lib is None: _lib = _load()
        self.where = f"{host}:{port}{api}"; self.api = api
        err = ctypes.c_void_p()
        self.h = _lib.kr_open(host.encode(), int(port), api.encode(), ctypes.byref(err))
        if not self.h:
            raise RibbitError(f"{self.where}: cannot open the session: {_text(_lib, err) if err.value else '?'}")

    def _err(self, what, err):
        return RibbitError(f"{self.where} {what}: {_text(_lib, err) if err.value else '?'}")

    def write(self, service, variable, instance, bag, own=False):
        err = ctypes.c_void_p()
        if _lib.kr_write(self.h, service.encode(), variable.encode(), instance.encode(), json.dumps(bag).encode(),
                         1 if own else 0, ctypes.byref(err)) != 0:
            raise self._err(f"write {service}.{variable}.{instance}", err)

    def read(self, service, variable="", instance="", after=-1, wait_s=0.0):
        err = ctypes.c_void_p()
        r = _lib.kr_read(self.h, service.encode(), variable.encode(), instance.encode(), int(after), float(wait_s),
                         ctypes.byref(err))
        if not r:
            raise self._err(f"read {service}.{variable or '*'}.{instance or '*'}", err)
        return json.loads(_text(_lib, r))

    def call(self, method, path, body=""):
        err = ctypes.c_void_p()
        r = _lib.kr_call(self.h, method.encode(), path.encode(), body.encode(), ctypes.byref(err))
        if not r:
            raise self._err(f"{method} {path}", err)
        return json.loads(_text(_lib, r))

    def call_text(self, method, path, body="", timeout_s=15.0):
        """a request whose answer is text, not JSON (FD&F's held op=rina_block wait)"""
        err = ctypes.c_void_p()
        r = _lib.kr_call_text(self.h, method.encode(), path.encode(), body.encode(), float(timeout_s), ctypes.byref(err))
        if not r:
            raise self._err(f"{method} {path}", err)
        return _text(_lib, r)

    def refused(self):
        """how many posted writes the region has refused on this session so far"""
        return _lib.kr_refused(self.h)

    def follow(self, service, variable):
        """Yields the family's cells once, then each batch of cells written to it after that, as they are written.
        A cell whose bag is {} is gone (its writer's session ended)."""
        cells = self.read(service, variable)
        last = max([c["id"] for c in cells] + [0])
        yield cells
        while True:
            cells = self.read(service, variable, "", after=last, wait_s=HOLD_S)
            if cells:
                last = max([c["id"] for c in cells] + [last]); yield cells

    def close(self):
        if self.h: _lib.kr_close(self.h); self.h = None
