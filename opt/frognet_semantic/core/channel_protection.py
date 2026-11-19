#!/usr/bin/env python3
# Copyright (C) 2016-2026 Fawcett Innovations LLC
# SPDX-License-Identifier: GPL-2.0-only
"""channel_protection.py -- [A_PROTECTION_IS_REGISTERED_V1] what a keyed channel does to a message, by name.

John, 2026-11-19, of the driver's packing and unpacking: "I can do whatever the fuck they want, right there, and it
goes out on the wire." And: "The insertion point and the extraction points are written and the handler is working.
Adding a private key is trivial."

A protection is two functions and a name:

    seal(key, nonce, associated data, frame) -> bytes
    open(key, nonce, associated data, bytes) -> frame      (raises ValueError when it does not open)

core/channel_setting.py calls the one the channel's tuple names ("protection", default "aes-256-gcm") at the driver's
packing (protect) and unpacking (unprotect). register_protection adds one; nothing else in the tree changes for it.

aes-256-gcm here is the machine's own libcrypto (OpenSSL), reached with ctypes. Every node has it (the installer
puts libssl there for the build); nothing is added to a node for this. Its output is byte for byte the output of the
Python module `cryptography` for the same key, nonce and associated data (checked 19 Nov), which this replaces: that
module is not on the nodes.
"""
import ctypes
import ctypes.util
import threading
from typing import Callable, Dict, Optional, Tuple

TAG = 16


class ProtectionFailed(Exception):
    """This machine cannot run the protection (no libcrypto, or it refused the key)."""




# ------------------------------------------------------------------------------------------------ aes-256-gcm
_lib = None
_lib_lock = threading.Lock()


def _crypto():
    """This machine's libcrypto, opened once."""
    global _lib
    with _lib_lock:
        if _lib is None:
            name = ctypes.util.find_library("crypto")
            if not name:
                raise ProtectionFailed("aes-256-gcm needs this machine's libcrypto (OpenSSL) and it was not found")
            L = ctypes.CDLL(name)
            L.EVP_CIPHER_CTX_new.restype = ctypes.c_void_p
            L.EVP_aes_256_gcm.restype = ctypes.c_void_p
            for f in ("EVP_EncryptInit_ex", "EVP_DecryptInit_ex"):
                getattr(L, f).argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_char_p, ctypes.c_char_p]
            for f in ("EVP_EncryptUpdate", "EVP_DecryptUpdate"):
                getattr(L, f).argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_int), ctypes.c_char_p, ctypes.c_int]
            for f in ("EVP_EncryptFinal_ex", "EVP_DecryptFinal_ex"):
                getattr(L, f).argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_int)]
            L.EVP_CIPHER_CTX_ctrl.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_void_p]
            L.EVP_CIPHER_CTX_free.argtypes = [ctypes.c_void_p]
            _lib = L
        return _lib


_GCM_SET_IVLEN, _GCM_GET_TAG, _GCM_SET_TAG = 0x9, 0x10, 0x11


def _gcm(key: bytes, nonce: bytes, aad: bytes, data: bytes, tag: Optional[bytes]) -> bytes:
    """Seal (tag None: returns the sealed bytes and the tag) or open (returns the frame; raises if it does not open)."""
    L = _crypto()
    seal = tag is None
    init, update, final = ((L.EVP_EncryptInit_ex, L.EVP_EncryptUpdate, L.EVP_EncryptFinal_ex) if seal else
                           (L.EVP_DecryptInit_ex, L.EVP_DecryptUpdate, L.EVP_DecryptFinal_ex))
    ctx = L.EVP_CIPHER_CTX_new()
    try:
        n = ctypes.c_int(0)
        ok = (init(ctx, L.EVP_aes_256_gcm(), None, None, None) == 1
              and L.EVP_CIPHER_CTX_ctrl(ctx, _GCM_SET_IVLEN, len(nonce), None) == 1
              and init(ctx, None, None, key, nonce) == 1
              and update(ctx, None, ctypes.byref(n), aad, len(aad)) == 1)
        if not ok:
            raise ProtectionFailed("aes-256-gcm: libcrypto refused the key or the nonce")
        out = ctypes.create_string_buffer(len(data) + 16)
        if update(ctx, out, ctypes.byref(n), data, len(data)) != 1:
            raise ProtectionFailed("aes-256-gcm: libcrypto refused the data")
        got = n.value
        if not seal:
            L.EVP_CIPHER_CTX_ctrl(ctx, _GCM_SET_TAG, TAG, ctypes.c_char_p(tag))
        if final(ctx, ctypes.byref(out, got), ctypes.byref(n)) != 1:
            raise ValueError("does not open")
        got += n.value
        if seal:
            t = ctypes.create_string_buffer(TAG)
            L.EVP_CIPHER_CTX_ctrl(ctx, _GCM_GET_TAG, TAG, t)
            return out.raw[:got] + t.raw
        return out.raw[:got]
    finally:
        L.EVP_CIPHER_CTX_free(ctx)


def _aes_256_gcm_seal(key: bytes, nonce: bytes, aad: bytes, frame: bytes) -> bytes:
    return _gcm(key, nonce, aad, frame, None)


def _aes_256_gcm_open(key: bytes, nonce: bytes, aad: bytes, sealed: bytes) -> bytes:
    return _gcm(key, nonce, aad, sealed[:-TAG], sealed[-TAG:])


#: name -> (seal, open). Each takes (key, nonce, associated data, bytes). open raises ValueError if it does not open.
PROTECTIONS: Dict[str, Tuple[Callable, Callable]] = {"aes-256-gcm": (_aes_256_gcm_seal, _aes_256_gcm_open)}


def register_protection(name: str, seal: Callable, open_: Callable) -> None:
    """The owner of a channel puts what they choose at the driver's insertion and extraction points."""
    PROTECTIONS[name] = (seal, open_)


