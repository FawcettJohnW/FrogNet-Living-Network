#!/opt/frognet_semantic/venv/bin/python3
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
#  This program is distributed in the hope that it will be     #
#  useful, but WITHOUT ANY WARRANTY; without even the implied  #
#  warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR     #
#  PURPOSE.  See the GNU General Public License for details.   #
#                                                              #
#  See COPYRIGHT and LICENSE at the root of this tree.         #
################################################################
"""
core/codec.py - FrogNet Semantic Codec (v4.1) with DIFF encoding

v4.1 fixes:
- BUG 1 (REQ_REPEAT never fires): reference=None sentinel distinguishes
  "never sent" from "sent with zero fields".  encode_*_diff now correctly
  returns is_identical=True for repeated zero-field requests (echo, getHosts).
- BUG 3 (LZ4 inflates small payloads): lz4 skipped when the compressed
  output is larger than the raw payload.  LZ4 frame overhead is ~15 bytes;
  compressing 0-50 byte payloads makes them bigger.
- REQ_HASH_LEN constant (16 bytes / 128 bits) defined as single source of
  truth for wire-protocol request hashes.  Birthday collision at 10K items
  ~ 10?2? - astronomically safe.
"""

from __future__ import annotations

import struct
import json
from typing import Any, Dict, List, Tuple, Optional

import lz4.frame

# Fixed, authoritative wire version
WIRE_VERSION = 5   # v5: TYPE_INT widened int32(<i) -> int64(<q). v4 peers rejected by _assert_version.
FLAG_COMPRESSED = 0x01
FLAG_DIFF = 0x02  # Indicates diff (not all fields present)

# -----------------------------------------------------------------------
# Request hash size on the wire.  16 bytes saves 16 per request vs SHA256.
# Every REQ_FULL, REQ_REPEAT, REQ_RAW, REQ_MISS carries this hash.
# -----------------------------------------------------------------------
REQ_HASH_LEN = 16


def _lz4_smart(payload: bytes) -> Tuple[bytes, bool]:
    """
    Compress payload with LZ4 only when it actually shrinks the data.
    Returns (output_bytes, was_compressed).
    """
    if not payload:
        return payload, False
    # [LZ4_FLOOR_V1] 2026-10-19, measured on the C++ port (ribbit semcodec.hpp) under MQTT QoS 0 across two boxes:
    # inputs of 25-64 bytes were tried 2,232,831 times and came out smaller 0 times. Nothing at or under 64 bytes is
    # tried. The C++ codec applies the same floor, so the two stay byte-identical (test_semcodec_oracle.py).
    if len(payload) <= 64:
        return payload, False
    compressed = lz4.frame.compress(payload)
    if len(compressed) < len(payload):
        return compressed, True
    return payload, False


#: [WIRE_FIELD_IS_UINT16_V1] TYPE_STR and TYPE_RAW carry a 2-byte length
#: prefix ("<H"), so the largest payload either can hold is 65535 bytes --
#: NOT 65536, and not "64 KB", which is the number a person types when they
#: mean this limit and is one byte too many. Named here so callers bound
#: themselves against the codec instead of restating a literal that cannot be
#: checked against anything. TYPE_JSON uses a 4-byte prefix and is unaffected.
MAX_WIRE_FIELD_BYTES = 0xFFFF


class SemanticCodec:
    TYPE_NULL  = 0
    TYPE_RAW   = 1
    TYPE_STR   = 2
    TYPE_INT   = 3
    TYPE_FLOAT = 4
    TYPE_BOOL  = 5
    TYPE_JSON  = 7   # dict / list ONLY
    # [STRING_SPLICE_V1] [NUMBER_DELTA_V1] (ribbit platform 0.66/0.67) -- in a DIFFERENCE only, against the reference:
    TYPE_SPLICE       = 8    # u16 pre, u16 suf, u16 len, the middle: the reference's string with its middle replaced
    TYPE_INT_DELTA    = 9    # varint zigzag(new - ref) of an int64, mod 2**64
    TYPE_FLOAT_DELTA  = 10   # varint zigzag(bits(new) - bits(ref)) of the IEEE-754 bits, mod 2**64
    TYPE_DECSTR_DELTA = 11   # varint zigzag(new - ref) of a canonical decimal string that fits int64

    # ========================================================================
    # DIFF Encoding
    # ========================================================================

    def encode_request_diff(
        self,
        opcode: int,
        url_vals: List[Tuple[str, Any]],
        json_vals: List[Tuple[str, Any]],
        type_map: Dict[str, str],
        reference: Optional[Dict[str, Any]],  # None = never sent
        tokens,
        compress: bool = True,
    ) -> Tuple[bytes, Dict[str, Any], bool]:
        """
        Encode request as diff against reference values.

        Returns:
            (encoded_bytes, updated_reference, is_identical)

        reference semantics:
            None  -> first time for this (target_ip, opcode).  Send FULL.
            {}    -> previously sent with zero dynamic fields.  If still zero -> IDENTICAL.
            {k:v} -> previously sent with these values.  Diff against them.
        """
        merged = (url_vals or []) + (json_vals or [])

        # Build new reference and find changed fields
        new_reference = {}
        changed = []

        for idx, (field, value) in enumerate(merged):
            new_reference[field] = value
            if reference is not None:
                ref_value = reference.get(field)
                if not self._values_equal(value, ref_value):
                    changed.append((idx, field, value))
            else:
                # First time: every field is "changed"
                changed.append((idx, field, value))

        # BUG 1 FIX: reference is not None means we HAVE sent before.
        # If nothing changed (including zero-field -> zero-field), signal IDENTICAL.
        if not changed and reference is not None:
            return b"", new_reference, True

        fb = bytearray()
        for idx, field, value in changed:
            has_ref = reference is not None and field in reference
            fb.extend(self._encode_changed(idx, value, has_ref, reference[field] if has_ref else None))

        flags = FLAG_DIFF
        payload = bytes(fb)

        # BUG 3 FIX: only compress if it actually saves bytes
        if compress and payload:
            compressed, did_compress = _lz4_smart(payload)
            if did_compress:
                payload = compressed
                flags |= FLAG_COMPRESSED

        header = struct.pack("<BBIH", WIRE_VERSION, flags, opcode, len(changed))
        return header + payload, new_reference, False

    def encode_reply_diff(
        self,
        opcode: int,
        dynamic_vals: List[Tuple[str, Any]],
        type_map: Dict[str, str],
        reference: Optional[Dict[str, Any]],  # None = never sent
        tokens,
        compress: bool = True,
    ) -> Tuple[bytes, Dict[str, Any], bool]:
        """
        Encode reply as diff against reference values.

        Returns:
            (encoded_bytes, updated_reference, is_identical)
        """
        vals = dynamic_vals or []

        new_reference = {}
        changed = []

        for idx, (field, value) in enumerate(vals):
            new_reference[field] = value
            if reference is not None:
                ref_value = reference.get(field)
                if not self._values_equal(value, ref_value):
                    changed.append((idx, field, value))
            else:
                changed.append((idx, field, value))

        if not changed and reference is not None:
            return b"", new_reference, True

        fb = bytearray()
        for idx, field, value in changed:
            has_ref = reference is not None and field in reference
            fb.extend(self._encode_changed(idx, value, has_ref, reference[field] if has_ref else None))

        flags = FLAG_DIFF
        payload = bytes(fb)

        if compress and payload:
            compressed, did_compress = _lz4_smart(payload)
            if did_compress:
                payload = compressed
                flags |= FLAG_COMPRESSED

        header = struct.pack("<BBIH", WIRE_VERSION, flags, opcode, len(changed))
        return header + payload, new_reference, False

    def _values_equal(self, a: Any, b: Any) -> bool:
        """[EXACT_DIFF_V1] Equal means the receiver would rebuild the same value: same type (bool is not int, int is
        not float), floats bit for bit (NaN equals the same NaN; 0.0 is not -0.0), dict keys in the same order. The old
        tolerance (1e-9, and 1 == 1.0, True == 1) left real changes out of a difference: the receiver kept a value the
        sender no longer had. The C++ codec (ribbit semcodec.hpp values_equal) is the same test."""
        if type(a) is not type(b):
            return False
        if a is None:
            return True
        if isinstance(a, float):
            return struct.pack("<d", a) == struct.pack("<d", b)
        if isinstance(a, dict):
            if list(a.keys()) != list(b.keys()):
                return False
            return all(self._values_equal(a[k], b[k]) for k in a)
        if isinstance(a, list):
            if len(a) != len(b):
                return False
            return all(self._values_equal(x, y) for x, y in zip(a, b))
        return a == b

    # [STRING_SPLICE_V1] [NUMBER_DELTA_V1] one changed field of a difference: a delta or a splice against the reference
    # when that is shorter than the whole value, else the whole value. Byte for byte the C++ codec's encode_changed.
    @staticmethod
    def _zz(d: int) -> int:
        return ((d << 1) ^ (d >> 63)) & 0xFFFFFFFFFFFFFFFF

    @staticmethod
    def _varint(v: int) -> bytes:
        out = bytearray()
        while v >= 0x80:
            out.append((v & 0x7F) | 0x80); v >>= 7
        out.append(v)
        return bytes(out)

    @staticmethod
    def _s64(v: int) -> int:
        v &= 0xFFFFFFFFFFFFFFFF
        return v - (1 << 64) if v >= (1 << 63) else v

    @staticmethod
    def _decstr(s: str):
        """A canonical decimal integer that fits int64, or None."""
        if not s or len(s) > 19:
            return None
        i = 1 if s[0] == "-" else 0
        if i == len(s):
            return None
        if s[i] == "0" and (len(s) > i + 1 or i):
            return None
        if not all("0" <= ch <= "9" for ch in s[i:]):
            return None
        v = int(s)
        return v if -(1 << 63) <= v < (1 << 63) else None

    def _encode_changed(self, idx: int, value: Any, has_ref: bool, ref: Any) -> bytes:
        I64 = (-(1 << 63), (1 << 63) - 1)
        if has_ref:
            t = 0; z = 0; whole = 0
            if type(value) is int and type(ref) is int and I64[0] <= value <= I64[1] and I64[0] <= ref <= I64[1]:
                t, z, whole = self.TYPE_INT_DELTA, self._zz(self._s64(value - ref)), 8
            elif type(value) is float and type(ref) is float:
                a = struct.unpack("<Q", struct.pack("<d", value))[0]; b = struct.unpack("<Q", struct.pack("<d", ref))[0]
                t, z, whole = self.TYPE_FLOAT_DELTA, self._zz(self._s64(a - b)), 8
            elif type(value) is str and type(ref) is str:
                a, b = self._decstr(value), self._decstr(ref)
                if a is not None and b is not None and I64[0] <= a - b <= I64[1]:
                    t, z, whole = self.TYPE_DECSTR_DELTA, self._zz(a - b), 2 + len(value.encode("utf-8"))
            if t and len(self._varint(z)) < whole:
                return struct.pack("<HB", idx, t) + self._varint(z)
            if type(value) is str and type(ref) is str:
                try:
                    o = ref.encode("utf-8"); x = value.encode("utf-8")
                except UnicodeEncodeError:
                    o = x = None                   # not UTF-8: sent whole (and refused there, as a TYPE_STR)
                if o is not None:
                    lim = min(len(o), len(x)); pre = 0
                    while pre < lim and o[pre] == x[pre]:
                        pre += 1
                    while pre > 0 and pre < len(x) and (x[pre] & 0xC0) == 0x80:
                        pre -= 1
                    suf = 0
                    while suf < lim - pre and o[len(o) - 1 - suf] == x[len(x) - 1 - suf]:
                        suf += 1
                    while suf > 0 and (x[len(x) - suf] & 0xC0) == 0x80:
                        suf -= 1
                    mid = len(x) - pre - suf
                    if pre <= 0xFFFF and suf <= 0xFFFF and mid <= 0xFFFF and 6 + mid < 2 + len(x):
                        return struct.pack("<HBHHH", idx, self.TYPE_SPLICE, pre, suf, mid) + x[pre:pre + mid]
        type_id = self._type_to_id(value)
        return struct.pack("<HB", idx, type_id) + self._encode_value(type_id, value)

    # ========================================================================
    # FULL Encoding (original - for bootstrap/RAW)
    # ========================================================================

    def encode_request(
        self,
        opcode: int,
        url_vals: List[Tuple[str, Any]],
        json_vals: List[Tuple[str, Any]],
        type_map: Dict[str, str],
        tokens,
        compress: bool = True,
    ) -> bytes:
        merged = (url_vals or []) + (json_vals or [])

        fb = bytearray()
        for idx, (field, value) in enumerate(merged):
            type_id = self._type_to_id(value)
            fb.extend(struct.pack("<HB", idx, type_id))
            fb.extend(self._encode_value(type_id, value))

        flags = 0
        payload = bytes(fb)

        if compress and payload:
            compressed, did_compress = _lz4_smart(payload)
            if did_compress:
                payload = compressed
                flags |= FLAG_COMPRESSED

        header = struct.pack("<BBIH", WIRE_VERSION, flags, opcode, len(merged))
        return header + payload

    def encode_reply(
        self,
        opcode: int,
        dynamic_vals: List[Tuple[str, Any]],
        type_map: Dict[str, str],
        tokens,
        compress: bool = True,
    ) -> bytes:

        fb = bytearray()
        for idx, (field, value) in enumerate(dynamic_vals or []):
            type_id = self._type_to_id(value)
            fb.extend(struct.pack("<HB", idx, type_id))
            fb.extend(self._encode_value(type_id, value))

        flags = 0
        payload = bytes(fb)

        if compress and payload:
            compressed, did_compress = _lz4_smart(payload)
            if did_compress:
                payload = compressed
                flags |= FLAG_COMPRESSED

        header = struct.pack("<BBIH", WIRE_VERSION, flags, opcode, len(dynamic_vals or []))
        return header + payload

    def encode_error_reply(self, status: int, msg: str) -> bytes:
        # Sentinel opcode for engine-level errors. With 32-bit opcodes
        # we can pick a value far from anything CRC32 of a real path
        # would map to (and from store.py's 0-avoidance fallback).
        opcode = 0xFFFFFFFF
        flags = 0
        payload = f"{int(status)}:{msg}".encode("utf-8", "replace")

        fb = struct.pack("<HB", 0, self.TYPE_STR)
        fb += struct.pack("<H", len(payload)) + payload

        header = struct.pack("<BBIH", WIRE_VERSION, flags, opcode, 1)
        return header + fb

    def decode_error_reply(self, blob: bytes):
        """Inverse of encode_error_reply: recover (status, msg) from an engine error
        frame. The session layer must preserve the ACTUAL reason (e.g. "no templates
        for opcode=...") when it wraps the OP_ERROR -- flattening it to a generic string
        stripped the signal the proxy uses to trigger a RAW re-bootstrap, so a
        template-less opcode 503'd forever. Returns (status, msg) or None if not an
        error frame. Layout: header(BBIH,8) + <HB idx,type>(3) + <H len>(2) + payload."""
        # [NO_FALLBACK_ERROR_REPLY_V1] None answers only "not an error frame" (too short, or not the error opcode). An
        # error frame whose status is not an integer raises: it used to read as "not an error" and lose the reason. The
        # length check covers every unpack below, so nothing else can fail. (The C++ codec is the same.)
        if not blob or len(blob) < 13:
            return None
        if struct.unpack("<I", blob[2:6])[0] != 0xFFFFFFFF:
            return None
        n = struct.unpack("<H", blob[11:13])[0]
        payload = blob[13:13 + n].decode("utf-8", "replace")
        status_str, _, msg = payload.partition(":")
        return int(status_str), msg

    # ========================================================================
    # Decoding (with DIFF support)
    # ========================================================================

    def decode_request(
        self,
        packet: bytes,
        req_tpl,
        tokens,
        reference: Optional[Dict[str, Any]] = None,
    ) -> Tuple[List[Tuple[str, Any]], List[Tuple[str, Any]]]:
        version, flags, opcode, n = struct.unpack("<BBIH", packet[:8])
        self._assert_version(version)

        fb = packet[8:]
        if flags & FLAG_COMPRESSED:
            fb = lz4.frame.decompress(fb)

        is_diff = bool(flags & FLAG_DIFF)
        splices = [] if is_diff else None
        vals = self._decode_fieldblock(fb, n, splices)

        url_keys = getattr(req_tpl, "url_query_keys", []) or []
        json_fields = (getattr(req_tpl, "fragment", {}) or {}).get("field_order", []) or []
        self._merge_splices(vals, splices, url_keys + json_fields, reference)

        if is_diff and reference:
            all_fields = url_keys + json_fields
            for idx, field in enumerate(all_fields):
                if idx not in vals and field in reference:
                    vals[idx] = reference[field]

        url_vals = [(k, vals.get(i)) for i, k in enumerate(url_keys)]
        base = len(url_keys)
        json_vals = [(f, vals.get(base + j)) for j, f in enumerate(json_fields)]

        return url_vals, json_vals

    def decode_reply(
        self,
        packet: bytes,
        resp_tpl,
        tokens,
        reference: Optional[Dict[str, Any]] = None,
    ) -> List[Tuple[str, Any]]:
        version, flags, opcode, n = struct.unpack("<BBIH", packet[:8])
        self._assert_version(version)

        fb = packet[8:]
        if flags & FLAG_COMPRESSED:
            fb = lz4.frame.decompress(fb)

        is_diff = bool(flags & FLAG_DIFF)
        splices = [] if is_diff else None
        vals = self._decode_fieldblock(fb, n, splices)

        frag = getattr(resp_tpl, "fragment", {}) or {}
        field_order = frag.get("field_order", []) or []
        self._merge_splices(vals, splices, field_order, reference)

        if is_diff and reference:
            for idx, field in enumerate(field_order):
                if idx not in vals and field in reference:
                    vals[idx] = reference[field]

        if not field_order:
            return [(f"field{idx}", vals.get(idx)) for idx in range(n)]

        return [(field_order[i], vals.get(i)) for i in range(len(field_order))]

    # ========================================================================
    # Internal helpers
    # ========================================================================

    def _assert_version(self, version: int) -> None:
        if version != WIRE_VERSION:
            # [CODEC-DECODE] localization print - first16 of the buffer
            # we're trying to decode tells us whether it's an FNW1 frame
            # (464e5731 = 'FNW1') being wrong-layered, or some other 'F'
            # leading payload (daemon misencoded).
            import inspect, sys
            try:
                frame = inspect.currentframe().f_back
                packet = frame.f_locals.get('packet') or frame.f_locals.get('blob')
                first16 = packet[:16].hex() if packet else '<no-packet>'
                print(f"[CODEC-DECODE] first16={first16} caller={frame.f_code.co_name}", file=sys.stderr)
            except Exception as _pe:           # [IMPORT_GUARD_V2] the probe's own failure is named, not hidden
                print(f"[CODEC-DECODE] caller probe failed: {_pe!r}", file=sys.stderr)
            raise ValueError(f"Unsupported semantic wire version {version}")

    def _type_to_id(self, value: Any) -> int:
        if value is None:
            return self.TYPE_NULL
        if isinstance(value, (dict, list)):
            return self.TYPE_JSON
        if isinstance(value, bool):
            return self.TYPE_BOOL
        if isinstance(value, int):
            return self.TYPE_INT
        if isinstance(value, float):
            return self.TYPE_FLOAT
        if isinstance(value, (bytes, bytearray)):
            return self.TYPE_RAW
        return self.TYPE_STR

    def _encode_value(self, type_id: int, value: Any) -> bytes:
        if type_id == self.TYPE_NULL:
            return b""
        if type_id == self.TYPE_JSON:
            data = json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
            return struct.pack("<I", len(data)) + data  # 4-byte length, supports up to 4GB
        if type_id == self.TYPE_RAW:
            if not isinstance(value, (bytes, bytearray)):
                raise TypeError(f"TYPE_RAW requires bytes, got {type(value).__name__}: {value!r}")
            b = bytes(value)
            if len(b) > MAX_WIRE_FIELD_BYTES:
                raise ValueError(f"TYPE_RAW over {MAX_WIRE_FIELD_BYTES}-byte "
                                 f"wire field: {len(b)} bytes")
            return struct.pack("<H", len(b)) + b
        if type_id == self.TYPE_STR:
            if not isinstance(value, str):
                raise TypeError(f"TYPE_STR requires str, got {type(value).__name__}: {value!r}")
            b = value.encode("utf-8")  # strict: un-encodable raises, no silent 'replace'
            if len(b) > MAX_WIRE_FIELD_BYTES:
                raise ValueError(f"TYPE_STR over {MAX_WIRE_FIELD_BYTES}-byte "
                                 f"wire field: {len(b)} bytes")
            return struct.pack("<H", len(b)) + b
        if type_id == self.TYPE_INT:
            if not isinstance(value, int) or isinstance(value, bool):
                raise TypeError(f"TYPE_INT requires int, got {type(value).__name__}: {value!r}")
            if not (-9223372036854775808 <= value <= 9223372036854775807):
                raise ValueError(f"TYPE_INT does not fit signed int64 wire field: {value!r}")
            return struct.pack("<q", value)
        if type_id == self.TYPE_FLOAT:
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                raise TypeError(f"TYPE_FLOAT requires number, got {type(value).__name__}: {value!r}")
            return struct.pack("<d", float(value))
        if type_id == self.TYPE_BOOL:
            if not isinstance(value, (bool, int)):
                raise TypeError(f"TYPE_BOOL requires bool/int, got {type(value).__name__}: {value!r}")
            return struct.pack("<B", 1 if value else 0)
        raise ValueError(f"Unknown type_id {type_id}")

    def _get_varint(self, fb: bytes, p: int) -> Tuple[int, int]:
        v = 0
        for sh in range(0, 64, 7):
            if p >= len(fb):
                raise struct.error("semcodec: short varint")
            b = fb[p]; p += 1
            v |= (b & 0x7F) << sh
            if not b & 0x80:
                return v & 0xFFFFFFFFFFFFFFFF, p
        raise struct.error("semcodec: varint too long")

    def _merge_splices(self, vals: Dict[int, Any], splices, fields: List[str], reference) -> None:
        """[STRING_SPLICE_V1] [NUMBER_DELTA_V1] each splice or delta made whole against the reference's value of its field.
        Without a reference (a receiver reading only the location out of a difference) they are not read here."""
        if not splices or reference is None:
            return
        for idx, kind, a, b, mid in splices:
            name = fields[idx] if idx < len(fields) else None
            have = name is not None and name in reference
            rv = reference.get(name) if have else None
            if not have or (kind == self.TYPE_SPLICE and not isinstance(rv, str)):
                raise ValueError("semcodec: a splice without its reference string (field " + (name if name is not None else "?") +
                                 (", absent from the reference" if not have else ", not a string in the reference") + ")")
            if kind == self.TYPE_INT_DELTA:
                if type(rv) is not int or not (-(1 << 63) <= rv < (1 << 63)):
                    raise ValueError("semcodec: an integer delta without its reference integer")
                vals[idx] = self._s64(rv + a)
            elif kind == self.TYPE_FLOAT_DELTA:
                if type(rv) is not float:
                    raise ValueError("semcodec: a float delta without its reference float")
                u = (struct.unpack("<Q", struct.pack("<d", rv))[0] + a) & 0xFFFFFFFFFFFFFFFF
                vals[idx] = struct.unpack("<d", struct.pack("<Q", u))[0]
            elif kind == self.TYPE_DECSTR_DELTA:
                bb = self._decstr(rv) if isinstance(rv, str) else None
                if bb is None:
                    raise ValueError("semcodec: a decimal delta without its reference number")
                vals[idx] = str(bb + a)
            else:
                o = rv.encode("utf-8", "surrogatepass")
                if a + b > len(o):
                    raise ValueError("semcodec: a splice longer than its reference")
                vals[idx] = (o[:a] + mid + o[len(o) - b:]).decode("utf-8", "replace")

    def _decode_fieldblock(self, fb: bytes, n: int, splices=None) -> Dict[int, Any]:
        vals: Dict[int, Any] = {}
        p = 0
        for _ in range(n):
            idx, type_id = struct.unpack("<HB", fb[p:p+3])
            p += 3
            if type_id == self.TYPE_SPLICE:          # [STRING_SPLICE_V1] (idx, kind, pre, suf, middle)
                if splices is None:
                    raise ValueError("semcodec: a splice outside a difference")
                if len(fb) - p < 6:
                    raise struct.error("semcodec: short splice")
                pre, suf, L = struct.unpack("<HHH", fb[p:p+6]); p += 6
                if len(fb) - p < L:
                    raise struct.error("semcodec: short splice")
                vals.pop(idx, None)
                splices.append((idx, type_id, pre, suf, bytes(fb[p:p+L]))); p += L
                continue
            if type_id in (self.TYPE_INT_DELTA, self.TYPE_FLOAT_DELTA, self.TYPE_DECSTR_DELTA):   # [NUMBER_DELTA_V1]
                if splices is None:
                    raise ValueError("semcodec: a delta outside a difference")
                z, p = self._get_varint(fb, p)
                vals.pop(idx, None)
                splices.append((idx, type_id, (z >> 1) ^ -(z & 1), 0, b"")); continue
            if type_id == self.TYPE_NULL:
                vals[idx] = None
            elif type_id == self.TYPE_JSON:
                L = struct.unpack("<I", fb[p:p+4])[0]; p += 4
                vals[idx] = json.loads(fb[p:p+L].decode("utf-8", "replace")); p += L
            elif type_id == self.TYPE_RAW:
                # [TYPERAW_NATIVE_BYTES] TYPE_RAW carries opaque binary (VP8/Opus
                # frames, etc.). Return the raw bytes UNCHANGED. The old code folded
                # RAW in with STR and ran .decode("utf-8","replace"), which mangled
                # every non-UTF-8 byte to U+FFFD and returned a str (FINDING-1) -
                # which is why the media path had to base64 as TYPE_STR.
                L = struct.unpack("<H", fb[p:p+2])[0]; p += 2
                vals[idx] = fb[p:p+L]; p += L
            elif type_id == self.TYPE_STR:
                L = struct.unpack("<H", fb[p:p+2])[0]; p += 2
                vals[idx] = fb[p:p+L].decode("utf-8", "replace"); p += L
            elif type_id == self.TYPE_INT:
                vals[idx] = struct.unpack("<q", fb[p:p+8])[0]; p += 8
            elif type_id == self.TYPE_FLOAT:
                vals[idx] = struct.unpack("<d", fb[p:p+8])[0]; p += 8
            elif type_id == self.TYPE_BOOL:
                vals[idx] = bool(fb[p]); p += 1
            else:
                raise ValueError(f"Unknown type_id {type_id}")
        return vals

# ---- DEPLOYMENT MARKER ----
# (a fixed "BUILD=2026-03-24-v1" stamp was printed here; the service start line names the base build [BASE_BUILD_V1])
