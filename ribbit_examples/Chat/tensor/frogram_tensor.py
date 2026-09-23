################################################################
#  Copyright (C) 2016-2026 Fawcett Innovations LLC             #
#  SPDX-License-Identifier: GPL-2.0-only                       #
################################################################
"""frogram_tensor.py -- tensor_plane's TensorServer / TensorClient, with the bytes in C++.

EXPERIMENT A's seam. Same class names, same methods, same arguments, same
StateGone, same wire. psychedelic_backend.py does not change: swap the import.

    from frogram_tensor import TensorServer, TensorClient        # was: from .tensor_plane import ...

A tensor's bytes go straight between its storage (data_ptr()) and the socket:
publish(copy=False) serves the tensor's own memory, and read_into() receives
into the destination tensor. Anything exposing the buffer protocol works too,
which is how this is tested on a machine with no torch.

Library: FROGRAM_TENSOR_LIB, else libfrogram_tensor.so beside this file.
NO FALLBACKS: if the library is missing this raises; it does not quietly
become the Python plane.
"""
import ctypes, os

_LIB = os.environ.get("FROGRAM_TENSOR_LIB") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "libfrogram_tensor.so")
_L = ctypes.CDLL(_LIB)

READ_BY_IDENTITY, READ_CURRENT = "by_identity", "current"
GONE_NEVER_PUBLISHED, GONE_SUPERSEDED, GONE_WAIT_EXPIRED = "never_published", "superseded", "wait_expired"
try:                                   # the tree's own exception, so `except StateGone` above this still catches
    from agent_workload.tuplespace.tensor_plane import StateGone
except ImportError:
    class StateGone(KeyError):
        def __init__(self, message, reason, worker=None, name=None, want_gen=None, want_digest=None, current_gen=None, current_digest=None):
            super().__init__(message); self.reason = reason; self.worker = worker; self.name = name
            self.want_gen = want_gen; self.want_digest = want_digest; self.current_gen = current_gen; self.current_digest = current_digest


class _Result(ctypes.Structure):
    _fields_ = [("status", ctypes.c_int), ("nbytes", ctypes.c_ulonglong), ("gen", ctypes.c_longlong), ("has_gen", ctypes.c_int),
                ("digest", ctypes.c_char * 24), ("inc", ctypes.c_char * 128), ("reason", ctypes.c_char * 32), ("waited_s", ctypes.c_double), ("error", ctypes.c_char * 256)]


_L.ft_server_create.restype = ctypes.c_void_p
_L.ft_server_create.argtypes = [ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_int]
_L.ft_server_port.argtypes = [ctypes.c_void_p]
_L.ft_server_publish.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_longlong, ctypes.c_void_p, ctypes.c_ulonglong, ctypes.c_char_p, ctypes.c_int]
_L.ft_server_stats.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulonglong)]
_L.ft_server_close.argtypes = [ctypes.c_void_p]
_L.ft_client_create.restype = ctypes.c_void_p
_L.ft_client_create.argtypes = [ctypes.c_char_p]
_L.ft_client_read_into.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_longlong, ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p,
                                   ctypes.c_int, ctypes.c_int, ctypes.c_double, ctypes.c_void_p, ctypes.c_ulonglong, ctypes.POINTER(_Result)]
_L.ft_client_close.argtypes = [ctypes.c_void_p]


def _span(t, writable):
    """(address, nbytes, keepalive) of a torch tensor or of anything with the buffer protocol."""
    if hasattr(t, "data_ptr"):
        t = t.detach()
        if not t.is_contiguous() or t.device.type != "cpu":
            if writable:
                raise ValueError("the destination must be a contiguous CPU tensor: received bytes land in its storage")
            t = t.to("cpu").contiguous()
        return t.data_ptr(), t.numel() * t.element_size(), t
    mv = memoryview(t).cast("B")
    if writable and mv.readonly:
        raise ValueError("the destination is read-only")
    c = (ctypes.c_char * len(mv)).from_buffer(mv) if not mv.readonly else (ctypes.c_char * len(mv)).from_buffer_copy(mv)
    return ctypes.addressof(c), len(mv), (c, mv)


class TensorServer:
    def __init__(self, bind_ip="0.0.0.0", port=0, watch=None):
        err = ctypes.create_string_buffer(256)
        self._h = _L.ft_server_create(bind_ip.encode(), int(port), err, 256)
        if not self._h:
            raise OSError(err.value.decode())
        self.port = _L.ft_server_port(self._h); self.bind_ip = bind_ip; self._pinned = {}

    def publish(self, name, generation, tensor, incarnation=None, copy=True):
        addr, n, keep = _span(tensor, writable=False)
        _L.ft_server_publish(self._h, name.encode(), int(generation), addr, n, incarnation.encode() if incarnation is not None else None, 1 if copy else 0)
        if copy:
            self._pinned.pop(name, None)
        else:
            self._pinned[name] = keep            # its memory is what is being served
        return None

    def stats(self):
        a = (ctypes.c_ulonglong * 5)(); _L.ft_server_stats(self._h, a)
        return {"states_offered": a[0], "same_replies": a[1], "data_replies": a[2], "bytes_sent": a[3], "wait_expired": a[4]}

    def close(self):
        if self._h:
            _L.ft_server_close(self._h); self._h = None


class TensorClient:
    def __init__(self, me, connect_timeout=None, watch=None):
        self.me = me; self._h = _L.ft_client_create(me.encode())

    WAIT_RETRIES = 3                  # tensor_plane.TensorClient's own policy for an expired wait
    WAIT_RETRY_SLEEP_S = 0.05

    def _read_once(self, dst, worker, host, port, name, generation, mode, wait_s, verify, want_digest=None, want_incarnation=None, cache=True):
        addr, cap, keep = _span(dst, writable=True)
        r = _Result()
        _L.ft_client_read_into(self._h, host.encode(), int(port), name.encode(), -1 if generation is None else int(generation), mode.encode(),
                               want_digest.encode() if want_digest else None, want_incarnation.encode() if want_incarnation else None,
                               1 if verify else 0, 1 if cache else 0, float(wait_s), addr, cap, ctypes.byref(r))
        del keep
        if r.status == 3:
            raise ConnectionError(r.error.decode())
        if r.status == 2:
            reason = r.reason.decode()
            raise StateGone("%s: %r generation %s is %s (peer holds generation %s)" % (worker, name, generation, reason, r.gen if r.has_gen else None),
                            reason, worker=worker, name=name, want_gen=generation, want_digest=want_digest,
                            current_gen=r.gen if r.has_gen else None, current_digest=r.digest.decode() or None)
        self.last = {"gen": r.gen, "digest": r.digest.decode() or None, "inc": r.inc.decode() or None}
        return int(r.nbytes), r.status == 1

    def read_into(self, dst, worker, host, port, name, generation, mode=READ_CURRENT, wait_s=0.0, verify=True, channel="d", exact=True):
        """tensor_plane.TensorClient.read_into, same arguments, same answer:
        (wire_bytes, was_same, elements). The bytes land in dst's own storage."""
        last = None
        for attempt in range(self.WAIT_RETRIES):
            try:
                nbytes, same = self._read_once(dst, worker, host, port, name, generation, mode, wait_s, verify)
                break
            except StateGone as e:
                if e.reason != GONE_WAIT_EXPIRED:
                    raise
                last = e
                if attempt + 1 < self.WAIT_RETRIES:
                    import time; time.sleep(self.WAIT_RETRY_SLEEP_S)
        else:
            raise last
        _, cap, _k = _span(dst, writable=True); item = dst.element_size() if hasattr(dst, "element_size") else memoryview(dst).itemsize
        if same:
            return 0, True, cap // item
        if exact and nbytes != cap:
            raise ValueError("%s published %r generation %s as %d bytes; the destination holds %d" % (worker, name, generation, nbytes, cap))
        return nbytes, False, nbytes // item

    def close(self):
        if self._h:
            _L.ft_client_close(self._h); self._h = None
