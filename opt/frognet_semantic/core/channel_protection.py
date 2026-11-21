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
            L.EVP_chacha20_poly1305.restype = ctypes.c_void_p
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


def _gcm(key: bytes, nonce: bytes, aad: bytes, data: bytes, tag: Optional[bytes], cipher: str = "EVP_aes_256_gcm") -> bytes:
    """Seal (tag None: returns the sealed bytes and the tag) or open (returns the frame; raises if it does not open).
    `cipher`: libcrypto's name for an AEAD cipher with a 12-byte nonce and a 16-byte tag (the control numbers below
    are the AEAD ones, the same for AES-GCM and ChaCha20-Poly1305)."""
    L = _crypto()
    which = getattr(L, cipher)
    seal = tag is None
    init, update, final = ((L.EVP_EncryptInit_ex, L.EVP_EncryptUpdate, L.EVP_EncryptFinal_ex) if seal else
                           (L.EVP_DecryptInit_ex, L.EVP_DecryptUpdate, L.EVP_DecryptFinal_ex))
    ctx = L.EVP_CIPHER_CTX_new()
    try:
        n = ctypes.c_int(0)
        ok = (init(ctx, which(), None, None, None) == 1
              and L.EVP_CIPHER_CTX_ctrl(ctx, _GCM_SET_IVLEN, len(nonce), None) == 1
              and init(ctx, None, None, key, nonce) == 1
              and update(ctx, None, ctypes.byref(n), aad, len(aad)) == 1)
        if not ok:
            raise ProtectionFailed("%s: libcrypto refused the key or the nonce" % cipher)
        out = ctypes.create_string_buffer(len(data) + 16)
        if update(ctx, out, ctypes.byref(n), data, len(data)) != 1:
            raise ProtectionFailed("%s: libcrypto refused the data" % cipher)
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


# [THE_ALGORITHM_IS_PART_OF_THE_SETTING_V1] a second algorithm from the same library. John, 2026-11-21: "The algorithm
# employed is part of the capability tuple." It stands in for whatever algorithm is mandated next: a channel is moved
# to it by three writes to its setting, as a key is replaced, and the key does not change.
def _chacha20_poly1305_seal(key: bytes, nonce: bytes, aad: bytes, frame: bytes) -> bytes:
    return _gcm(key, nonce, aad, frame, None, "EVP_chacha20_poly1305")


def _chacha20_poly1305_open(key: bytes, nonce: bytes, aad: bytes, sealed: bytes) -> bytes:
    return _gcm(key, nonce, aad, sealed[:-TAG], sealed[-TAG:], "EVP_chacha20_poly1305")


#: name -> (seal, open). Each takes (key, nonce, associated data, bytes). open raises ValueError if it does not open.
PROTECTIONS: Dict[str, Tuple[Callable, Callable]] = {"aes-256-gcm": (_aes_256_gcm_seal, _aes_256_gcm_open),
                                                     "chacha20-poly1305": (_chacha20_poly1305_seal, _chacha20_poly1305_open)}
#: A sealed message says which algorithm sealed it, in its first byte: the algorithm's number.
NUMBERS: Dict[str, int] = {"aes-256-gcm": 1, "chacha20-poly1305": 2}
NAMES: Dict[int, str] = {1: "aes-256-gcm", 2: "chacha20-poly1305"}


def register_protection(name: str, seal: Callable, open_: Callable, number: Optional[int] = None) -> None:
    """The owner of a channel puts what they choose at the driver's insertion and extraction points. `number` is what a
    sealed message carries to name it (64 to 255; both ends register the same). Not given: one is made from the name,
    the same on every machine."""
    if number is None:
        import hashlib
        number = 64 + int.from_bytes(hashlib.sha256(name.encode()).digest()[:2], "big") % 192
    if not 3 <= number <= 255 or NAMES.get(number, name) != name:
        raise ValueError("the protection %r cannot have the number %d: it is %s" % (name, number, "taken by " + NAMES[number] if number in NAMES else "out of range"))
    PROTECTIONS[name] = (seal, open_)
    NUMBERS[name] = number
    NAMES[number] = name


