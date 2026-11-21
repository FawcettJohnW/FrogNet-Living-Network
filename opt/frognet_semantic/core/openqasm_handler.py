#!/usr/bin/env python3
################################################################
#  Copyright (C) 2016-2026 Fawcett Innovations LLC             #
#                                                              #
#  SPDX-License-Identifier: GPL-2.0-only                       #
#                                                              #
#  This program is free software; you can redistribute it      #
#  and/or modify it under the terms of the GNU General Public  #
#  License as published by the Free Software Foundation;       #
#  version 2 of the License, and no other version.             #
#                                                              #
#  See COPYRIGHT and LICENSE at the root of this tree.         #
################################################################
"""
openqasm_handler.py -- [OPENQASM_CODEX_V1] the circuit codex: an OpenQASM program sent as which circuit it is and the
values of its parameters.

John, 2026-11-21: "OpenQASM is the protocol to implement. If the payload right now doesn't send anything, put in
something that does."

Where it sits. Measured 21 Nov: the driver never sees a whole circuit. By the time a message reaches a driver's
pack(), the semantic layer has cut a changed string to one splice against the value before it (core/codec.py
_encode_changed). So the thing that understands a circuit is called from there, for a string value that begins with
"OPENQASM", on a connection whose channel setting names the driver openqasm (SemanticCodec.qasm). On every other
channel the program crosses as it always did.

A program is its fixed text and its parameters. The parameters are the numeric literals: digits, an optional decimal
point and digits, an optional exponent, and a minus sign when it follows "(" or ",". A number in the version line
(OPENQASM 3.0;), inside square brackets (q[2], qubit[3]), inside quotes or in a comment is fixed text: it says which
circuit this is, not what its values are. Two programs with the same fixed text are the same circuit.

What crosses, in place of the program:

    0x01  against the value before it (the reference both ends hold for this tuple), when that is the same circuit:
          a bitmap of which parameters changed, then each changed parameter
    0x02  a loaded circuit, when there is no such reference: the circuit's number, then every parameter
    a parameter:   one byte, kind << 4 | decimal places, then
          kind 0   an integer, as a zigzag varint
          kind 1   a decimal with that many places, as the zigzag varint of its digits   (2.827 -> places 3, 2827)
          kind 2   anything else: one byte of length and its characters

Loaded circuits are files in /etc/frognet/openqasm_circuits (FROGNET_OPENQASM_CIRCUITS), placed on each machine before
a run as keys are. They are numbered in order of file name, the same at both ends. Nothing is numbered during a run:
a circuit that is not loaded and has no reference crosses whole, once, and is the reference from then on.

Byte for byte: before a short form is used, the program is rebuilt from it and compared. One that does not come back
exactly is not sent short (encode() returns None and the caller sends it as it would any string).
"""
import os
import threading
from typing import List, Optional, Tuple

BUILD = "openqasm-codex-v1-20261121a"
WORD = "OPENQASM"
MARK = "\x00"
CIRCUITS = os.environ.get("FROGNET_OPENQASM_CIRCUITS", "/etc/frognet/openqasm_circuits")
AGAINST_REFERENCE, LOADED = 1, 2

_lock = threading.Lock()
_loaded: Optional[Tuple[List[str], dict]] = None      # (fixed texts by number, fixed text -> number)


def is_program(value) -> bool:
    return isinstance(value, str) and value.startswith(WORD)


def split(text: str) -> Optional[Tuple[str, List[str]]]:
    """(fixed text with MARK where each parameter stood, the parameters), or None when text is not a program this
    codex takes (it does not begin with OPENQASM, or it holds the marker itself)."""
    if not is_program(text) or MARK in text:
        return None
    out, lits = [], []
    i, n = 0, len(text)
    depth = 0                       # inside [ ]
    header = True                   # until the first ';' (the version line)
    quote = ""
    while i < n:
        c = text[i]
        if quote:
            out.append(c)
            if c == quote:
                quote = ""
            i += 1
            continue
        if c in "\"'":
            quote = c; out.append(c); i += 1; continue
        if c == "/" and text.startswith("//", i):
            j = text.find("\n", i)
            j = n if j < 0 else j
            out.append(text[i:j]); i = j; continue
        if c == "/" and text.startswith("/*", i):
            j = text.find("*/", i + 2)
            j = n if j < 0 else j + 2
            out.append(text[i:j]); i = j; continue
        if c == "[":
            depth += 1
        elif c == "]":
            depth = max(0, depth - 1)
        elif c == ";":
            header = False
        if c.isdigit() and not header and depth == 0 and not (i and (text[i - 1].isalnum() or text[i - 1] in "_.")):
            j = i
            while j < n and text[j].isdigit():
                j += 1
            if j + 1 < n and text[j] == "." and text[j + 1].isdigit():
                j += 1
                while j < n and text[j].isdigit():
                    j += 1
            if j < n and text[j] in "eE":
                k = j + 1
                if k < n and text[k] in "+-":
                    k += 1
                if k < n and text[k].isdigit():
                    while k < n and text[k].isdigit():
                        k += 1
                    j = k
            if j < n and (text[j].isalpha() or text[j] == "_"):
                out.append(text[i:j]); i = j; continue          # 2pi, 3x: not a plain number
            start = i
            p = len(out) - 1                                     # a minus sign that belongs to the number
            while p >= 0 and out[p] in " \t":
                p -= 1
            if p >= 1 and out[p] == "-":
                q = p - 1
                while q >= 0 and out[q] in " \t":
                    q -= 1
                if q >= 0 and out[q] in "(," and p == len(out) - 1:
                    out.pop(); start = i - 1
            lits.append(text[start:j])
            out.append(MARK)
            i = j
            continue
        out.append(c)
        i += 1
    return "".join(out), lits


def join(fixed: str, lits: List[str]) -> str:
    parts = fixed.split(MARK)
    if len(parts) != len(lits) + 1:
        raise ValueError("openqasm: a circuit of %d parameters given %d" % (len(parts) - 1, len(lits)))
    out = [parts[0]]
    for lit, rest in zip(lits, parts[1:]):
        out.append(lit); out.append(rest)
    return "".join(out)


def _varint(v: int) -> bytes:
    out = bytearray()
    while v >= 0x80:
        out.append((v & 0x7F) | 0x80); v >>= 7
    out.append(v)
    return bytes(out)


def _get_varint(b: bytes, p: int) -> Tuple[int, int]:
    v, shift = 0, 0
    while True:
        if p >= len(b) or shift > 70:
            raise ValueError("openqasm: a number that does not end")
        c = b[p]; p += 1
        v |= (c & 0x7F) << shift
        shift += 7
        if not c & 0x80:
            return v, p


def _zz(d: int) -> int:
    return (d << 1) if d >= 0 else ((-d << 1) - 1)


def _unzz(z: int) -> int:
    return (z >> 1) if not z & 1 else -((z + 1) >> 1)


def _lit_out(lit: str) -> bytes:
    neg = lit.startswith("-")
    body = lit[1:] if neg else lit
    if body.isdigit():
        v = -int(body) if neg else int(body)
        if str(v) == lit:
            return bytes([0x00]) + _varint(_zz(v))
    elif "." in body:
        whole, _, frac = body.partition(".")
        if whole.isdigit() and frac.isdigit() and 1 <= len(frac) <= 15:
            v = int(whole + frac)
            v = -v if neg else v
            if _lit_text(1, len(frac), v) == lit:
                return bytes([0x10 | len(frac)]) + _varint(_zz(v))
    raw = lit.encode("utf-8")
    if len(raw) > 255:
        raise ValueError("openqasm: a parameter longer than 255 bytes")
    return bytes([0x20, len(raw)]) + raw


def _lit_text(kind: int, places: int, v: int) -> str:
    if kind == 0:
        return str(v)
    digits = str(abs(v)).rjust(places + 1, "0")
    return ("-" if v < 0 else "") + digits[:-places] + "." + digits[-places:]


def _lit_in(b: bytes, p: int) -> Tuple[str, int]:
    if p >= len(b):
        raise ValueError("openqasm: a parameter is missing")
    tag = b[p]; p += 1
    kind, places = tag >> 4, tag & 15
    if kind == 2:
        ln = b[p]; p += 1
        if p + ln > len(b):
            raise ValueError("openqasm: a parameter is cut short")
        return b[p:p + ln].decode("utf-8"), p + ln
    if kind not in (0, 1) or (kind == 1 and not places):
        raise ValueError("openqasm: a parameter of a kind this codex does not have (%#x)" % tag)
    z, p = _get_varint(b, p)
    return _lit_text(kind, places, _unzz(z)), p


def loaded() -> Tuple[List[str], dict]:
    """The circuits placed on this machine before the run: their fixed texts by number, and the number of each."""
    global _loaded
    with _lock:
        if _loaded is None:
            texts, numbers = [], {}
            try:
                names = sorted(os.listdir(CIRCUITS))
            except OSError:
                names = []
            for name in names:
                try:
                    with open(os.path.join(CIRCUITS, name), encoding="utf-8") as f:
                        s = split(f.read())
                except (OSError, UnicodeDecodeError) as e:
                    print("[OPENQASM] %s the circuit file %s could not be read (%s): it is not loaded" % (BUILD, name, e), flush=True)
                    continue
                if s is None:
                    print("[OPENQASM] %s the file %s does not begin with OPENQASM: it is not loaded" % (BUILD, name), flush=True)
                    continue
                if s[0] not in numbers:
                    numbers[s[0]] = len(texts); texts.append(s[0])
            _loaded = (texts, numbers)
            print("[OPENQASM] %s %d circuit(s) loaded from %s" % (BUILD, len(texts), CIRCUITS), flush=True)
        return _loaded


def reload() -> None:
    global _loaded
    with _lock:
        _loaded = None


def encode(text: str, reference=None) -> Optional[bytes]:
    """The short form of a program, or None when it has none (or would not come back byte for byte)."""
    s = split(text)
    if s is None:
        return None
    fixed, lits = s
    try:
        blob = None
        r = split(reference) if isinstance(reference, str) else None
        if r is not None and r[0] == fixed:
            bits, body = 0, bytearray()
            for k, (now, was) in enumerate(zip(lits, r[1])):
                if now != was:
                    bits |= 1 << k
                    body += _lit_out(now)
            blob = bytes([AGAINST_REFERENCE]) + bits.to_bytes((len(lits) + 7) // 8, "little") + bytes(body)
        else:
            number = loaded()[1].get(fixed)
            if number is not None:
                blob = bytes([LOADED]) + _varint(number) + b"".join(_lit_out(x) for x in lits)
        if blob is None or decode(blob, reference) != text:
            return None
        return blob
    except ValueError:
        return None


def decode(blob: bytes, reference=None) -> str:
    """The program a short form stands for. Raises ValueError, with the reason, when it cannot be made whole."""
    if not blob:
        raise ValueError("openqasm: an empty short form")
    form = blob[0]
    if form == AGAINST_REFERENCE:
        r = split(reference) if isinstance(reference, str) else None
        if r is None:
            raise ValueError("openqasm: a circuit sent against the value before it, and this end holds no circuit there")
        fixed, lits = r[0], list(r[1])
        nb = (len(lits) + 7) // 8
        if len(blob) < 1 + nb:
            raise ValueError("openqasm: a short form cut short")
        bits, p = int.from_bytes(blob[1:1 + nb], "little"), 1 + nb
        for k in range(len(lits)):
            if bits >> k & 1:
                lits[k], p = _lit_in(blob, p)
    elif form == LOADED:
        number, p = _get_varint(blob, 1)
        texts = loaded()[0]
        if number >= len(texts):
            raise ValueError("openqasm: circuit %d is not loaded on this machine (%d are, from %s)" % (number, len(texts), CIRCUITS))
        fixed, lits = texts[number], []
        for _ in range(fixed.count(MARK)):
            lit, p = _lit_in(blob, p)
            lits.append(lit)
    else:
        raise ValueError("openqasm: a short form of a kind this codex does not have (%#x)" % form)
    if p != len(blob):
        raise ValueError("openqasm: %d byte(s) left over after the last parameter" % (len(blob) - p))
    return join(fixed, lits)
