################################################################
#  Copyright (C) 2016-2026 Fawcett Innovations LLC             #
#  SPDX-License-Identifier: GPL-2.0-only                       #
################################################################
"""fdf_seal -- [SEALED_TO_ITS_KEY_V1] a secret written into a cell is sealed to the public key of the one who will read
it: the RAM holds only what that key's owner can open. (John 2026-11-03: "not like a messaging thing"; a RAM manager has
no per-cell privacy, so a secret is never in a cell as itself.)

The key is a WireGuard key (X25519, RFC 7748): a member's own. seal(pub, data): an ephemeral X25519 key, the shared
secret with `pub`, two keys from it (HKDF-SHA256), the data XORed with HMAC-SHA256 in counter mode, and an HMAC-SHA256
tag over the ephemeral public key and the ciphertext. open_(priv, sealed) checks the tag before anything is returned.
Only the standard library: the same primitives FrogNet already uses (HMAC-SHA256)."""
import base64
import hashlib
import hmac
import secrets

_P = 2 ** 255 - 19
_A24 = 121665


def _x25519(k, u):
    k = bytearray(k)
    k[0] &= 248; k[31] &= 127; k[31] |= 64
    k = int.from_bytes(k, "little")
    x1 = int.from_bytes(u, "little") & ((1 << 255) - 1)
    x2, z2, x3, z3, swap = 1, 0, x1, 1, 0
    for t in reversed(range(255)):
        kt = (k >> t) & 1
        swap ^= kt
        if swap:
            x2, x3, z2, z3 = x3, x2, z3, z2
        swap = kt
        a, b = (x2 + z2) % _P, (x2 - z2) % _P
        aa, bb = a * a % _P, b * b % _P
        e = (aa - bb) % _P
        c, d = (x3 + z3) % _P, (x3 - z3) % _P
        da, cb = d * a % _P, c * b % _P
        x3, z3 = (da + cb) ** 2 % _P, x1 * (da - cb) ** 2 % _P
        x2, z2 = aa * bb % _P, e * (aa + _A24 * e) % _P
    if swap:
        x2, x3, z2, z3 = x3, x2, z3, z2
    return (x2 * pow(z2, _P - 2, _P) % _P).to_bytes(32, "little")


def public(priv_b64):
    """the public key of a WireGuard private key (base64), as `wg pubkey` gives it"""
    return base64.b64encode(_x25519(base64.b64decode(priv_b64), (9).to_bytes(32, "little"))).decode()


def _keys(shared, eph_pub, pub):
    prk = hmac.new(b"fdf-seal-v1", shared, hashlib.sha256).digest()
    okm = hmac.new(prk, eph_pub + pub + b"\x01", hashlib.sha256).digest() + hmac.new(prk, eph_pub + pub + b"\x02", hashlib.sha256).digest()
    return okm[:32], okm[32:]


def _stream(key, n):
    out, c = b"", 0
    while len(out) < n:
        out += hmac.new(key, c.to_bytes(8, "big"), hashlib.sha256).digest(); c += 1
    return out[:n]


def seal(pub_b64, data):
    pub = base64.b64decode(pub_b64)
    eph = secrets.token_bytes(32)
    eph_pub = _x25519(eph, (9).to_bytes(32, "little"))
    ek, mk = _keys(_x25519(eph, pub), eph_pub, pub)
    ct = bytes(a ^ b for a, b in zip(data, _stream(ek, len(data))))
    tag = hmac.new(mk, eph_pub + ct, hashlib.sha256).digest()
    return base64.b64encode(eph_pub + ct + tag).decode()


def open_(priv_b64, sealed_b64):
    raw = base64.b64decode(sealed_b64)
    eph_pub, ct, tag = raw[:32], raw[32:-32], raw[-32:]
    priv = base64.b64decode(priv_b64)
    pub = _x25519(priv, (9).to_bytes(32, "little"))
    ek, mk = _keys(_x25519(priv, eph_pub), eph_pub, pub)
    if not hmac.compare_digest(tag, hmac.new(mk, eph_pub + ct, hashlib.sha256).digest()):
        raise ValueError("sealed data: the tag does not match (not sealed to this key, or changed)")
    return bytes(a ^ b for a, b in zip(ct, _stream(ek, len(ct))))
