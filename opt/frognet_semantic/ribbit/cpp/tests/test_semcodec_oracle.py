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
#  This program is distributed in the hope that it will be     #
#  useful, but WITHOUT ANY WARRANTY; without even the implied  #
#  warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR     #
#  PURPOSE.  See the GNU General Public License for details.   #
#                                                              #
#  See COPYRIGHT and LICENSE at the root of this tree.         #
################################################################
"""S2 oracle: C++ include/semcodec.hpp against John's core/codec.py (SemanticCodec, WIRE_VERSION 5), imported and run.

Compared, case by case, on the same inputs:
  encode_request / encode_reply / encode_request_diff / encode_reply_diff  -> bytes (and new reference, is_identical)
  encode_error_reply / decode_error_reply
  decode_request / decode_reply (templates given as url_query_keys + fragment field_order; references None / {} / dict)
  _values_equal, _lz4_smart, lz4.frame.decompress as the codec calls it
  JSON through TYPE_JSON: json.dumps(separators=(",", ":"), ensure_ascii=False) bytes, json.loads of decoded text
Every result must be identical: bytes, every decoded value (type and exact bits, None versus absent), reference dicts
in insertion order. Rejections must be the same kind; ValueError text is compared where the text is John's
(TYPE_*, Unknown type_id, Unsupported semantic wire version) and kind-only where it is CPython's.

Canonical value form (both sides): N  B0/B1  I<decimal>  F<16 hex digits of the IEEE-754 bits>  S<hex of UTF-8, lone
surrogates as WTF-8>  R<hex>  L(v,...)  D(Skey=v,...)  and field lists Q(Sname=v,...) (order and duplicates kept).

usage: test_semcodec_oracle.py FROGNET_SEMANTIC_ROOT [--driver ./bin/semcodec-driver] [--seed S] [--scale K]
"""
import argparse, json, math, random, struct, subprocess, sys, types
from collections import Counter

sys.dont_write_bytecode = True
ap = argparse.ArgumentParser()
ap.add_argument('root'); ap.add_argument('--driver', default='./bin/semcodec-driver')
ap.add_argument('--seed', type=int, default=20260924); ap.add_argument('--scale', type=int, default=1)
a = ap.parse_args()
sys.path.insert(0, a.root)
import core.codec as C  # noqa: E402
import lz4.frame  # noqa: E402

codec = C.SemanticCodec()
JOHN = ('TYPE_', 'Unknown type_id', 'Unsupported semantic wire version')
rng = random.Random(a.seed)


# ---------------------------------------------------------------- canonical form
def w8(s):
    return s.encode('utf-8', 'surrogatepass').hex()


def cv(v):
    if v is None: return 'N'
    if v is True: return 'B1'
    if v is False: return 'B0'
    if isinstance(v, int): return 'I%d' % v
    if isinstance(v, float): return 'F' + struct.pack('>d', v).hex()
    if isinstance(v, str): return 'S' + w8(v)
    if isinstance(v, (bytes, bytearray)): return 'R' + bytes(v).hex()
    if isinstance(v, list): return 'L(' + ','.join(cv(x) for x in v) + ')'
    if isinstance(v, dict): return 'D(' + ','.join('S%s=%s' % (w8(k), cv(x)) for k, x in v.items()) + ')'
    raise TypeError('canon: %r' % type(v))


def cq(pairs):
    return 'Q(' + ','.join('S%s=%s' % (w8(k), cv(x)) for k, x in pairs) + ')'


def cref(r):
    return 'N' if r is None else cv(r)


def kind(e):
    if isinstance(e, UnicodeEncodeError): return 'encode'
    if isinstance(e, json.JSONDecodeError): return 'json'
    if isinstance(e, struct.error): return 'struct'
    if isinstance(e, RecursionError): return 'recursion'
    if isinstance(e, MemoryError): return 'memory'   # a frame that declares a size neither side can allocate
    if isinstance(e, OverflowError): return 'overflow'
    if isinstance(e, TypeError): return 'type'
    if isinstance(e, IndexError): return 'index'
    if isinstance(e, ValueError): return 'value'
    if isinstance(e, RuntimeError): return 'lz4'
    return 'other:' + type(e).__name__


def err(e):
    k = kind(e)
    if k == 'value':
        t = str(e)
        return 'raise value ' + (t if t.startswith(JOHN) else '*')
    return 'raise ' + k


def run(f):
    try: return f()
    except Exception as e: return err(e)


def tpl(url_keys, json_fields):
    return types.SimpleNamespace(url_query_keys=url_keys, fragment={'field_order': json_fields})


# ---------------------------------------------------------------- python side of each command
def py(cmd, *x):
    if cmd == 'enc_req':
        op, comp, u, j = x
        return run(lambda: 'ok ' + codec.encode_request(op, u, j, {}, None, compress=comp).hex())
    if cmd == 'enc_rep':
        op, comp, d = x
        return run(lambda: 'ok ' + codec.encode_reply(op, d, {}, None, compress=comp).hex())
    if cmd == 'enc_req_diff':
        op, comp, u, j, ref = x
        def f():
            b, r, same = codec.encode_request_diff(op, u, j, {}, ref, None, compress=comp)
            return 'ok %s %s %d' % (b.hex() or '.', cv(r), same)
        return run(f)
    if cmd == 'enc_rep_diff':
        op, comp, d, ref = x
        def f():
            b, r, same = codec.encode_reply_diff(op, d, {}, ref, None, compress=comp)
            return 'ok %s %s %d' % (b.hex() or '.', cv(r), same)
        return run(f)
    if cmd == 'enc_err':
        st, msg = x
        return run(lambda: 'ok ' + codec.encode_error_reply(st, msg).hex())
    if cmd == 'dec_err':
        (blob,) = x
        def f():
            r = codec.decode_error_reply(blob)
            return 'none' if r is None else 'ok I%d S%s' % (r[0], w8(r[1]))
        return run(f)
    if cmd == 'dec_req':
        pkt, uk, jf, ref = x
        def f():
            u, j = codec.decode_request(pkt, tpl(uk, jf), None, ref)
            return 'ok %s %s' % (cq(u), cq(j))
        return run(f)
    if cmd == 'dec_rep':
        pkt, fo, ref = x
        return run(lambda: 'ok ' + cq(codec.decode_reply(pkt, tpl([], fo), None, ref)))
    if cmd == 'veq':
        p, q = x
        return run(lambda: 'ok %d' % codec._values_equal(p, q))
    if cmd == 'lz4smart':
        (b,) = x
        def f():
            o, c = C._lz4_smart(b)
            return 'ok %s %d' % (o.hex() or '.', c)
        return run(f)
    if cmd == 'lz4dec':
        (b,) = x
        return run(lambda: 'ok ' + (lz4.frame.decompress(b).hex() or '.'))
    raise ValueError(cmd)


def wire(cmd, *x):
    """The same command for the C++ driver, one line."""
    def h(b): return b.hex() if b else '.'
    def q(pairs): return cq(pairs)
    def sl(names): return 'L(' + ','.join('S' + w8(n) for n in names) + ')'
    if cmd == 'enc_req': return 'enc_req %d %d %s %s' % (x[0], x[1], q(x[2]), q(x[3]))
    if cmd == 'enc_rep': return 'enc_rep %d %d %s' % (x[0], x[1], q(x[2]))
    if cmd == 'enc_req_diff': return 'enc_req_diff %d %d %s %s %s' % (x[0], x[1], q(x[2]), q(x[3]), cref(x[4]))
    if cmd == 'enc_rep_diff': return 'enc_rep_diff %d %d %s %s' % (x[0], x[1], q(x[2]), cref(x[3]))
    if cmd == 'enc_err': return 'enc_err %d %s' % (x[0], h(x[1].encode('utf-8', 'surrogatepass')))
    if cmd == 'dec_err': return 'dec_err ' + h(x[0])
    if cmd == 'dec_req': return 'dec_req %s %s %s %s' % (h(x[0]), sl(x[1]), sl(x[2]), cref(x[3]))
    if cmd == 'dec_rep': return 'dec_rep %s %s %s' % (h(x[0]), sl(x[1]), cref(x[2]))
    if cmd == 'veq': return 'veq %s %s' % (cv(x[0]), cv(x[1]))
    if cmd in ('lz4smart', 'lz4dec'): return '%s %s' % (cmd, h(x[0]))
    raise ValueError(cmd)


# ---------------------------------------------------------------- corpus
I64 = 2 ** 63
INTS = [0, 1, -1, 2, 255, 256, 65535, 65536, -129, 2 ** 31 - 1, -2 ** 31, 2 ** 32, I64 - 1, -I64, I64, -I64 - 1,
        2 ** 64, 10 ** 30, -10 ** 25, 7 ** 200, -(10 ** 4299), 10 ** 400]
FLOATS = [0.0, -0.0, 1.0, -1.5, 0.1, 1e-5, 1e-4, 0.0001, 1e15, 1e16, 1e17, 123456789012345678.0, 1e22, 1e23,
          5e-324, 2.2250738585072014e-308, 1.7976931348623157e308, float('inf'), float('-inf'), float('nan'),
          1 / 3, 2 / 3, 100.0, 1e-7, 9007199254740993.0, 0.30000000000000004, 1e308, 4.35, 1e21, 1e-320]
STRS = ['', 'a', 'hello world', 'caf\u00e9', '\u2603\U0001F438', '\u00e9' * 7, 'quote"back\\slash', 'ctl\x00\x01\x1f\x7f',
        'nl\nr\rt\tb\bf\f', '\ud83d\ude00', '/slash', '\u2028\u2029', '{"json":"in a string"}',
        '\ufeffbom', '\u0660\u0661']
BYTES = [b'', b'\x00', b'raw', bytes(range(256)), b'\xed\xa0\x80']
BIG = ['a' * 65535, 'a' * 65536, '\u00e9' * 32767 + 'a', '\u00e9' * 32768, b'\xff' * 65535, b'\xff' * 65536,
       {'k': 'x' * 70000}, ['\u00e9' * 40000]]


def rfloat():
    r = rng.random()
    if r < 0.4: return rng.choice(FLOATS)
    if r < 0.7: return struct.unpack('<d', rng.randbytes(8))[0]
    return rng.uniform(-1e6, 1e6)


def rstr(short=False):
    r = rng.random()
    if r < 0.4 and not short: return rng.choice(STRS) if rng.random() < 0.9 else rng.choice(['\ud800', 'x\udfffy'])
    alphabet = 'abcXYZ019 _-:"\\/\n\t\x01\x0b\x0e\x1a\x1f\u00e9\u4e2d\U0001F600'
    t = ''.join(rng.choice(alphabet) for _ in range(rng.randrange(12)))
    if rng.random() < 0.02: t += rng.choice('\ud800\udc00')   # a lone surrogate: Python can hold it, UTF-8 cannot
    return t


def ratom(allow_bytes=True, short=False):
    r = rng.randrange(8 if allow_bytes else 7)
    if r == 0: return None
    if r == 1: return rng.random() < 0.5
    if r == 2: return rng.choice(INTS) if rng.random() < 0.5 else rng.randrange(-10 ** 6, 10 ** 6)
    if r == 3: return rfloat()
    if r in (4, 5): return rstr(short)
    if r == 6: return rng.randrange(-5, 5)
    return rng.choice(BYTES) if rng.random() < 0.5 else rng.randbytes(rng.randrange(20))


CTL = ''.join(chr(c) for c in range(32)) + '\x7f'


def rjson(depth=0, allow_bytes=False):
    r = rng.random()
    if r < 0.02: return CTL
    if depth > 3 or r < 0.45: return ratom(allow_bytes, short=True)
    if r < 0.7: return [rjson(depth + 1, allow_bytes) for _ in range(rng.randrange(5))]
    d = {}
    for _ in range(rng.randrange(5)): d[rstr(True)] = rjson(depth + 1, allow_bytes)
    return d


def rval():
    r = rng.random()
    if r < 0.65: return ratom()
    return rjson(1, allow_bytes=rng.random() < 0.1)


NAMES = ['id', 'name', 'q', 'page', 'x', 'y', 'ts', 'body', 'id', '\u00e9t\u00e9', '']


def rfields(n=None):
    n = rng.randrange(9) if n is None else n
    return [(rng.choice(NAMES) if rng.random() < 0.8 else rstr(True), rval()) for _ in range(n)]


def mutate_ref(pairs):
    """A reference that equals, nearly equals, or differs from the fields."""
    ref = {}
    for k, v in pairs:
        r = rng.random()
        if r < 0.55: ref[k] = v
        elif r < 0.65 and isinstance(v, (int, float)) and not isinstance(v, bool):
            try: ref[k] = rng.choice([float(v), float(v) + 1e-10, float(v) + 1e-8, int(v) if math.isfinite(v) else v, True])
            except (OverflowError, ValueError): ref[k] = v
        elif r < 0.75: pass
        else: ref[k] = rval()
    if rng.random() < 0.2: ref['extra'] = rval()
    return ref


cases = []
S = a.scale
OPS = [0, 1, 0xDEADBEEF, 0xFFFFFFFF, 2 ** 32, 12345]
for _ in range(3000 * S):
    op = rng.choice(OPS) if rng.random() < 0.3 else rng.randrange(2 ** 32)
    comp = rng.random() < 0.7
    u, j = rfields(), rfields()
    cases.append(('enc_req', op, comp, u, j)); cases.append(('enc_rep', op, comp, u + j))
for _ in range(4000 * S):
    op = rng.randrange(2 ** 32)
    comp = rng.random() < 0.7
    u, j = rfields(), rfields()
    for ref in (None, {}, mutate_ref(u + j), dict(u + j)):
        cases.append(('enc_req_diff', op, comp, u, j, ref)); cases.append(('enc_rep_diff', op, comp, u + j, ref))
# explicit: zero fields, identical repeats, compression thresholds, int64 and length edges, 65536 fields
for ref in (None, {}, {'a': 1}):
    cases.append(('enc_req_diff', 7, True, [], [], ref)); cases.append(('enc_rep_diff', 7, True, [], ref))
for n in list(range(0, 120)) + [200, 500, 1000, 4000, 20000, 65530]:
    for filler in ('a' * n, ''.join(rng.choice('ab') for _ in range(n)), rng.randbytes(n)):
        cases.append(('enc_rep', 1, True, [('f', filler)])); cases.append(('enc_rep', 1, True, [('f', filler), ('g', filler)]))
for v in INTS + FLOATS + STRS + BYTES + BIG + [True, False, None, [], {}, [None], {'k': b'x'}, {'\ud800': 1}, ['\udfff']]:
    cases.append(('enc_rep', 3, False, [('v', v)])); cases.append(('enc_rep', 3, True, [('v', v)]))
cases.append(('enc_rep', 1, False, [('f', 0)] * 65535)); cases.append(('enc_rep', 1, False, [('f', 0)] * 65536))
cases.append(('enc_rep', 1, False, [('f', None)] * 65537))
cases.append(('enc_rep', 1, False, [('f', 1)] * 65536 + [('g', b'\xff' * 70000)]))
for st, msg in [(200, ''), (404, 'not found'), (-5, 'neg'), (0, 'a:b:c'), (503, 'no templates for opcode=17'),
                (10 ** 20, 'big'), (500, '\ud800 lone'), (500, 'caf\u00e9'), (500, 'x' * 65530), (500, 'x' * 65540)]:
    cases.append(('enc_err', st, msg))
# values_equal
VEQ = [(1, 1.0), (True, 1), (True, 1.0), (False, 0), (1, 1 + 1e-10), (0.1 + 0.2, 0.3), (1e300, 1e300 * (1 + 1e-15)),
       (float('nan'), float('nan')), (float('inf'), float('inf')), (None, None), (None, 0), ('a', 'a'), ('a', b'a'),
       (b'a', b'a'), ([1, 2], [1.0, 2.0]), ([1], [1, 2]), ({'a': 1}, {'a': 1.0}), ({'a': 1}, {'b': 1}),
       ({'a': [1, {'b': True}]}, {'a': [1.0, {'b': 1}]}), (10 ** 400, 1.0), (1.0, 10 ** 400), (10 ** 400, 10 ** 400),
       ({'x': 1, 'y': 10 ** 400}, {'x': 2, 'y': 0.5}), ({'x': 10 ** 400, 'y': 1}, {'x': 0.5, 'y': 2}), ([], {}),
       (I64 - 1, float(I64)), (2 ** 53 + 1, float(2 ** 53)), (-0.0, 0.0), ('\ud800', '\ud800')]
for p, q in VEQ: cases.append(('veq', p, q)); cases.append(('veq', q, p))
for _ in range(20000 * S):
    p = rval()
    q = p if rng.random() < 0.3 else (mutate_ref([('k', p)]).get('k', None) if rng.random() < 0.5 else rval())
    cases.append(('veq', p, q))
# lz4
for n in list(range(0, 80)) + [1000, 65535, 65536, 65537, 200000]:
    cases.append(('lz4smart', b'a' * n)); cases.append(('lz4smart', rng.randbytes(n)))
good = lz4.frame.compress(b'hello hello hello hello hello hello' * 20)
big = lz4.frame.compress(rng.randbytes(300000) + b'z' * 300000)
nosize = lz4.frame.compress(b'q' * 5000, store_size=False)
for f in (good, big, nosize, lz4.frame.compress(b''), lz4.frame.compress(b'', store_size=False)):
    cases.append(('lz4dec', f)); cases.append(('lz4dec', f + b'trailing'))
    cases.append(('lz4dec', f + f))
    for n in range(0, min(len(f), 40)): cases.append(('lz4dec', f[:n]))
    for n in range(len(f) - 12, len(f)): cases.append(('lz4dec', f[:n]))
    for _ in range(40):
        g = bytearray(f); i = rng.randrange(len(g)); g[i] ^= 1 << rng.randrange(8); cases.append(('lz4dec', bytes(g)))
cases.append(('lz4dec', b'')); cases.append(('lz4dec', rng.randbytes(100)))
# floats: repr() through json.dumps, float() through json.loads -- random bit patterns, decimal-boundary values,
# integers near 2**53, and every power of ten across the exponent range
fl = [struct.unpack('<d', rng.randbytes(8))[0] for _ in range(60000 * S)]
fl += [rng.uniform(-1e3, 1e3) for _ in range(20000 * S)] + [float(rng.randrange(2 ** 60)) for _ in range(5000)]
fl += [10.0 ** e for e in range(-323, 309)] + [-(10.0 ** e) for e in range(-20, 25)] + [x * 1.0 for x in range(-20, 21)]
fl += [2.0 ** e for e in range(-1074, 1024, 7)] + [9.999999999999999e22, 1e23, 1.0000000000000001e16, 123456789.0]
for k in range(0, len(fl), 40):
    cases.append(('enc_rep', 1, False, [('f', fl[k:k + 40])]))
FLOAT_TEXTS = [repr(x) for x in fl[:30000]] + ['%.17g' % x for x in fl[:5000] if math.isfinite(x)]
FLOAT_TEXTS += ['%de%d' % (rng.randrange(1, 10 ** rng.randrange(1, 25)), rng.randrange(-340, 320)) for _ in range(5000)]
FLOAT_TEXTS += ['0.' + '0' * rng.randrange(400) + str(rng.randrange(1, 10 ** 20)) for _ in range(500)]
FLOAT_TEXTS += ['2.4703282292062327e-324', '2.4703282292062328e-324', '1.7976931348623158e308', '1.7976931348623159e308']
FLOAT_TEXTS = [t for t in FLOAT_TEXTS if 'n' not in t]


# ---------------------------------------------------------------- decode corpus
def hdr(flags, op, n, ver=5): return struct.pack('<BBIH', ver, flags, op, n)


def fld(idx, t, body): return struct.pack('<HB', idx, t) + body


def json_packet(text_bytes):
    return hdr(0, 9, 1) + fld(0, 7, struct.pack('<I', len(text_bytes)) + text_bytes)


dec = []
JSON_TEXTS = ['null', 'true', 'false', '0', '-0', '1', '-1', '01', '1.', '1.5', '-1.5e3', '1e400', '-1e400', '1e-400',
              '2.5E+2', '1e', '1e+', '1.e5', '.5', '-', '--1', 'NaN', 'Infinity', '-Infinity', '-NaN', 'nan', 'infinity',
              '[]', '{}', '[1,2,3]', '[1,]', '[,1]', '{"a":1}', '{"a":1,}', '{"a":1,"a":2}', '{"a":1,"b":2,"a":3}',
              '{"b":[1,{"c":null}]}', '{a:1}', "{'a':1}", '"str"', '"\\u00e9"', '"\\ud83d\\ude00"', '"\\ud800"',
              '"\\ud800\\u0041"', '"\\udc00\\ud800"', '"\\ud800\\udc00\\ud800"', '"\\ud800\\uZZZZ"', '"\\uD800\\uDC00"',
              '"\\u12"', '"\\udc00\\udc00"', '"\\ud800\\ue000"', '"\\udbff\\udfff"', '"\\ud7ff\\udc00"', '"\\udfff\\udc00"',
              '"\\x41"', '"\\q"', '"\\/\\b\\f\\n\\r\\t\\"\\\\"', '"ctl\x01"', '"tab\t"', '"\x7f"', ' [1] ',
              '\t\n\r[1]\r\n', '[1] x', '[1]]', '\ufeff[1]', '\u00a0[1]', '', ' ', '[1, 2 , 3 ]', '{"a" : 1 }',
              '1' * 4300, '1' * 4301, '-' + '9' * 4300, '1' * 4300 + '.5', '[' * 50 + ']' * 50, '{"\\u0000":1}',
              '9223372036854775807', '9223372036854775808', '-9223372036854775808', '-9223372036854775809',
              '0.1', '1E5', '100000000000000000000000', '[0.30000000000000004]', '"\u00e9\u4e2d"', '[true,false,null]',
              '{"k":NaN}', '[Infinity,-Infinity]', '{"":""}', '[1e22,1e23,5e-324,2.2250738585072014e-308]']
for t in JSON_TEXTS: dec.append(('dec_rep', json_packet(t.encode('utf-8', 'surrogatepass')), ['v'], None))
for k in range(0, len(FLOAT_TEXTS), 40):
    t = '[' + ','.join(FLOAT_TEXTS[k:k + 40]) + ']'
    dec.append(('dec_rep', json_packet(t.encode()), ['v'], None))
for raw in (b'[1,"\xff"]', b'"\xed\xa0\x80"', b'"\xc3"', b'"\xe2\x82"', b'"\xf0\x9f\x98"', b'"\x80\x80"', b'\xff[1]',
            b'"\xf4\x90\x80\x80"', b'"\xc0\xaf"', b'"\xe0\x80\xaf"', b'"\xf8\x88\x80\x80\x80"', b'[1]\x00'):
    dec.append(('dec_rep', json_packet(raw), ['v'], None))
for _ in range(3000 * S):   # random JSON-ish texts built from tokens
    toks = ['[', ']', '{', '}', ',', ':', '"a"', '"\\u', 'd800', 'dc00', '"', '1', '-', '0', '.', 'e', '+', 'E', ' ',
            'true', 'null', 'NaN', '-Infinity', '\\', 'x', '\u00e9', '\n', '5']
    t = ''.join(rng.choice(toks) for _ in range(rng.randrange(1, 14)))
    dec.append(('dec_rep', json_packet(t.encode('utf-8', 'surrogatepass')), ['v'], None))
for _ in range(3000 * S):   # valid JSON from python values
    v = rjson(0)
    t = json.dumps(v, separators=(',', ':'), ensure_ascii=rng.random() < 0.3,
                   indent=rng.choice([None, None, 1]))
    dec.append(('dec_rep', json_packet(t.encode('utf-8', 'surrogatepass')), ['v'], None))
# every encoded packet decodes on both sides, with matching and mismatched templates and references
for c in cases:
    if c[0] not in ('enc_req', 'enc_rep', 'enc_req_diff', 'enc_rep_diff'): continue
    out = py(*c)
    if not out.startswith('ok '): continue
    if out.split(' ')[1] == '.': continue
    pkt = bytes.fromhex(out.split(' ')[1])
    if c[0] in ('enc_req', 'enc_req_diff'):
        uk, jf = [k for k, _ in c[3]], [k for k, _ in c[4]]
        ref = c[5] if c[0] == 'enc_req_diff' else None
        dec.append(('dec_req', pkt, uk, jf, ref))
        if rng.random() < 0.2: dec.append(('dec_req', pkt, uk[:1], jf + ['more'], mutate_ref(c[3] + c[4])))
    else:
        fo = [k for k, _ in c[3]]
        ref = c[4] if c[0] == 'enc_rep_diff' else None
        dec.append(('dec_rep', pkt, fo, ref))
        if rng.random() < 0.1: dec.append(('dec_rep', pkt, [], None))
        if rng.random() < 0.1: dec.append(('dec_rep', pkt, fo[:2], {}))
    if len(pkt) < 300 and rng.random() < 0.3:
        for n in range(len(pkt)): dec.append(('dec_rep', pkt[:n], ['a', 'b'], None))
        for _ in range(5):
            g = bytearray(pkt); i = rng.randrange(len(g)); g[i] = rng.randrange(256)
            dec.append(('dec_rep', bytes(g), ['a', 'b', 'c'], {'a': 1, 'c': None}))
# crafted field blocks: every type, short reads, clamped slices, unknown types, versions, flags
for ver in (0, 4, 5, 6, 255):
    dec.append(('dec_rep', hdr(0, 1, 0, ver), ['a'], None))
for flags in range(0, 8):
    dec.append(('dec_rep', hdr(flags, 1, 1) + fld(0, 3, struct.pack('<q', -7)), ['a'], {'a': 5}))
    dec.append(('dec_rep', hdr(flags, 1, 1) + good, ['a'], {'a': 5}))
for t in range(0, 12):
    for body in (b'', b'\x01', b'\x05\x00abc', b'\x03\x00\x00\x00[1]', struct.pack('<q', 2 ** 62), struct.pack('<d', 1.5),
                 b'\x02', b'\xff\xff' + b'a' * 10, b'\x00\x00\x00\x10' + b'{}'):
        dec.append(('dec_rep', hdr(0, 1, 1) + fld(0, t, body), ['a'], None))
        dec.append(('dec_rep', hdr(0, 1, 2) + fld(0, t, body) + fld(1, 5, b'\x01'), ['a', 'b'], None))
dec.append(('dec_rep', hdr(0, 1, 3) + fld(5, 5, b'\x00') + fld(0, 3, struct.pack('<q', 1)) + fld(0, 3, struct.pack('<q', 2)),
            [], None))
dec.append(('dec_rep', hdr(2, 1, 1) + fld(1, 0, b''), ['a', 'b', 'c'], {'a': 1, 'c': 'x'}))
dec.append(('dec_req', hdr(2, 1, 1) + fld(1, 0, b''), ['a', 'b'], ['c', 'a'], {'a': 1, 'c': 'x'}))
dec.append(('dec_req', hdr(2, 1, 0), ['a'], ['b'], {}))
dec.append(('dec_req', hdr(2, 1, 0), [], [], None))
for n in range(0, 9): dec.append(('dec_rep', hdr(0, 1, 1)[:n], ['a'], None))
comp_blk = lz4.frame.compress(fld(0, 2, struct.pack('<H', 3) + b'abc') * 3)
dec.append(('dec_rep', hdr(1, 1, 3) + comp_blk, ['a'], None))
dec.append(('dec_rep', hdr(1, 1, 3) + comp_blk[:-3], ['a'], None))
dec.append(('dec_rep', hdr(1, 1, 3) + comp_blk + b'junk', ['a'], None))
dec.append(('dec_rep', hdr(1, 1, 0), ['a'], None))
for _ in range(20000 * S):
    body = rng.randbytes(rng.randrange(40))
    dec.append(('dec_rep', hdr(rng.randrange(4), 1, rng.randrange(6)) + body, ['a', 'b', 'c'], rng.choice([None, {}, {'b': 2}])))
# error replies
for c in [x for x in cases if x[0] == 'enc_err']:
    out = py(*c)
    if out.startswith('ok '): dec.append(('dec_err', bytes.fromhex(out[3:])))
ERR_STATUS = ['200', ' 42 ', '+7', '-3', '1_000', '1__0', '_1', '1_', '0x10', '', '-', '+', '007', '\u0664\u0662',
              '\u3000 5\u2003', '\uff15', '5\u00a0', '\x1c9\x1f', '1' * 4300, '1' * 4301, '12a', '\ud800', '\U0001d7ce',
              '\u0e53', '٣_٤', ' 1 2 ', '\t-\t5', '\n12\n', '+-1', '\u200b1', '\x0b3\x0c', '१२']
for st in ERR_STATUS:
    for msg in ('', 'boom', ':x', '\u00e9'):
        payload = (st + ':' + msg).encode('utf-8', 'surrogatepass')
        dec.append(('dec_err', hdr(0, 0xFFFFFFFF, 1) + fld(0, 2, struct.pack('<H', len(payload)) + payload)))
    payload = st.encode('utf-8', 'surrogatepass')
    dec.append(('dec_err', hdr(0, 0xFFFFFFFF, 1) + fld(0, 2, struct.pack('<H', len(payload)) + payload)))
for raw in (b'\xff:bad', b'4\xff04:x', b'12:\xc3', b'\xd9\xa4:ok'):
    dec.append(('dec_err', hdr(0, 0xFFFFFFFF, 1) + fld(0, 2, struct.pack('<H', len(raw)) + raw)))
    dec.append(('dec_err', hdr(0, 0xFFFFFFFF, 1) + fld(0, 2, struct.pack('<H', len(raw) + 9) + raw)))
dec.append(('dec_err', hdr(0, 0xFFFFFFFE, 1) + fld(0, 2, b'\x03\x00200')))
for n in range(0, 16): dec.append(('dec_err', (hdr(0, 0xFFFFFFFF, 1) + fld(0, 2, b'\x03\x00200'))[:n]))
for _ in range(3000 * S): dec.append(('dec_err', hdr(0, 0xFFFFFFFF, 1) + rng.randbytes(rng.randrange(20))))
cases += dec

# ---------------------------------------------------------------- run both, compare
lines = [wire(*c) for c in cases]
r = subprocess.run([a.driver], input='\n'.join(lines) + '\n', capture_output=True, text=True)
got = r.stdout.split('\n')
if r.returncode != 0 or len(got) < len(lines):
    print('FAIL driver exit %d, %d of %d answers; stderr: %s' % (r.returncode, len(got) - 1, len(lines), r.stderr[-3000:]))
    sys.exit(1)
count, bad, outcomes = Counter(), Counter(), Counter()
shown = 0
for c, line, have in zip(cases, lines, got):
    exp = py(*c)
    count[c[0]] += 1
    outcomes['%s %s' % (c[0], exp.split(' ')[0] + ('' if exp.startswith('ok') else ' ' + ' '.join(exp.split(' ')[1:2])))] += 1
    if exp != have:
        bad[c[0]] += 1
        if shown < 20:
            shown += 1
            print('DIFF %s\n  in    =%s\n  python=%s\n  c++   =%s' % (c[0], line[:300], exp[:400], have[:400]))
print('corpus outcomes (python):')
for k, v in sorted(outcomes.items()): print('  %8d %s' % (v, k))
for k in sorted(count): print('%s %s: %d cases, %d differ' % ('PASS' if bad[k] == 0 else 'FAIL', k, count[k], bad[k]))
ok = not bad and len(count) == 11
print('RESULT %s semcodec vs %s (seed %d, scale %d, %d cases)' % ('PASS' if ok else 'FAIL', C.__file__, a.seed, S, len(cases)))
sys.exit(0 if ok else 1)
