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
"""S1 oracle: C++ include/semwire.hpp against John's core/semcache_wire.py, imported and run.

Every wrap_* is called on the same inputs in both; the frames must be byte-identical, and every rejection must be the
same kind (ValueError / struct.error / UnicodeEncodeError), with ValueError messages identical text. Every frame, every
prefix of every frame, trailing-byte extensions, length-field mutations, all 256 op bytes and a seeded fuzz corpus go
through try_parse in both; results must agree field for field, including None versus empty. is_fnw1 and op_name are
compared on the same inputs. Round trips run both ways: C++ frames parsed by Python, Python frames parsed by C++.

usage: test_semwire_oracle.py FROGNET_SEMANTIC_ROOT [--driver ./bin/semwire-driver] [--fuzz N] [--seed S]
FROGNET_SEMANTIC_ROOT is the directory holding core/semcache_wire.py (the tgz's opt/frognet_semantic). Needs: pip install lz4
(core/codec.py imports lz4.frame; semcache_wire imports REQ_HASH_LEN from it).
"""
import argparse, random, struct, subprocess, sys

sys.dont_write_bytecode = True
p = argparse.ArgumentParser()
p.add_argument('root'); p.add_argument('--driver', default='./bin/semwire-driver')
p.add_argument('--fuzz', type=int, default=200000); p.add_argument('--seed', type=int, default=20260924)
a = p.parse_args()
sys.path.insert(0, a.root)
import core.semcache_wire as W  # noqa: E402

FIELDS = ('req_hash', 'same_id', 'payload', 'status', 'headers', 'body',
          'ping_id', 'proxy_t_send_ns', 'daemon_t_recv_ns', 'daemon_t_reply_ns')


def hx(b):
    return b.hex() if b else '.'


def kind(e):
    if isinstance(e, UnicodeEncodeError): return 'encode'
    if isinstance(e, struct.error): return 'struct'
    if isinstance(e, ValueError): return 'value'
    return 'other:' + type(e).__name__


def canon_err(e):
    k = kind(e)
    return 'raise %s %s' % (k, str(e) if k == 'value' else '')


def canon_msg(m):
    if m is None: return 'none'
    out = ['msg', 'op=%d' % m.op]
    for f in FIELDS:
        v = getattr(m, f)
        if v is None: out.append(f + '=~')
        elif isinstance(v, bytes): out.append(f + '=' + v.hex())
        else: out.append('%s=%d' % (f, v))
    return ' '.join(out)


def py_wrap(cmd, args):
    fn = getattr(W, 'wrap_' + cmd)
    try: return 'ok ' + hx(fn(*args))
    except Exception as e: return canon_err(e)


def py_parse(frame):
    try: return canon_msg(W.try_parse(frame))
    except Exception as e: return canon_err(e)


def enc_arg(v):
    if isinstance(v, bytes): return hx(v)
    if isinstance(v, str): return hx(v.encode('utf-8'))
    return str(v)


rng = random.Random(a.seed)
wrap_cases = []   # (cmd, args)
H = W.REQ_HASH_LEN if hasattr(W, 'REQ_HASH_LEN') else 16
blobs = [b'', b'\x00', b'x', bytes(range(256)), b'\xff' * 1000, rng.randbytes(4096), rng.randbytes(65536),
         rng.randbytes(70001), rng.randbytes(1 << 20)]
hashes = [bytes(H), rng.randbytes(H), b'\xff' * H, b'', rng.randbytes(1), rng.randbytes(H - 1), rng.randbytes(H + 1),
          rng.randbytes(32)]
sids = [bytes(16), rng.randbytes(16), b'', rng.randbytes(15), rng.randbytes(17), rng.randbytes(32)]
for c in ('req_full', 'req_raw', 'req_diff'):
    for h in hashes:
        for b in blobs: wrap_cases.append((c, (h, b)))
for c in ('req_repeat', 'req_miss'):
    for h in hashes: wrap_cases.append((c, (h,)))
for s in sids:
    wrap_cases.append(('resp_same', (s,)))
    for b in blobs: wrap_cases.append(('resp_diff', (s, b)))
for s in sids[:2] + sids[3:5]:
    for st in (0, 1, 200, 404, 65535, 65536, 1 << 40):
        for hd in (b'', b'Content-Type: application/json\r\n', rng.randbytes(65535), rng.randbytes(65536)):
            for b in (b'', b'{}', rng.randbytes(5000)): wrap_cases.append(('resp_raw', (s, st, hd, b)))
msgs = ['', 'x', 'short ERROR', 'caf\u00e9 \u2603 \U0001F438', 'a' * 65535, 'a' * 65536, '\u00e9' * 32768,
        '\u00e9' * 32767 + 'a', 'nul\x00inside']
for st in (0, 500, 65535, 65536):
    for m in msgs: wrap_cases.append(('error', (st, m)))
wrap_cases.append(('seq_reset', ())); wrap_cases.append(('rtt_loop', ()))
for ip in ('', '10.0.0.1', 'fd00::1', 'a' * 255, 'a' * 256, 'a' * 1000, '\u00e9', 'a' * 300 + '\u00e9', '\x7f\x00'):
    wrap_cases.append(('hello', (ip,)))
u32, u64 = 0xFFFFFFFF, 0xFFFFFFFFFFFFFFFF
for pid in (0, 1, u32, u32 + 1, u64):
    for t in (0, 1, u64):
        for pad in (0, 1, 1000, 65536, u32 + 1): wrap_cases.append(('rtt_ping', (pid, t, pad)))
for pid in (0, u32, u32 + 1):
    for t in (0, u64):
        for r in (0, 12345, u64): wrap_cases.append(('rtt_pong', (pid, t, r, u64 - r)))
for _ in range(2000):
    wrap_cases.append(('rtt_pong', (rng.randrange(u32 + 1), rng.randrange(u64 + 1), rng.randrange(u64 + 1),
                                    rng.randrange(u64 + 1))))
    wrap_cases.append(('rtt_ping', (rng.randrange(u32 + 1), rng.randrange(u64 + 1), rng.randrange(64))))

# parse corpus
frames = []
py_frames = []
for c, args in wrap_cases:
    try: py_frames.append(getattr(W, 'wrap_' + c)(*args))
    except Exception: pass
small = [f for f in py_frames if len(f) <= 400]
for f in small:
    for n in range(len(f) + 1): frames.append(f[:n])          # every prefix: every short-frame branch
    frames.append(f + b'\x00'); frames.append(f + rng.randbytes(9))
    if len(f) >= 9:                                            # corrupt a length-ish field
        for i in range(5, min(len(f), 60)):
            g = bytearray(f); g[i] ^= 0xFF; frames.append(bytes(g))
for f in py_frames:
    if len(f) > 400: frames.append(f); frames.append(f[:-1]); frames.append(f + b'Z')
for op in range(256):
    for body in (b'', b'\x00', bytes(16), bytes(40), rng.randbytes(64)):
        frames.append(W.MAGIC + bytes([op]) + body)
frames += [b'', b'F', b'FNW', b'FNW1', b'FNW2\x01', b'fnw1\x01' + bytes(20), b'\x00FNW1\x01']
# RESP_RAW inner-length edges
for plen, inner in ((0, b''), (3, b'\x00\xc8\x00'), (4, b'\x00\xc8\x00\x00'), (4, b'\x00\xc8\x00\x01'),
                    (6, b'\x00\xc8\x00\x02ab'), (6, b'\x00\xc8\x00\x03ab'), (8, b'\x00\xc8\xff\xffabcd')):
    frames.append(W.MAGIC + b'\x14' + bytes(16) + struct.pack('!I', plen) + inner)
ops = [0x01, 0x02, 0x03, 0x04, 0x11, 0x13, 0x14, 0x21, 0x30, 0x40, 0x50, 0x60, 0x61, 0x62]
for _ in range(a.fuzz):
    op = rng.choice(ops) if rng.random() < 0.9 else rng.randrange(256)
    body = bytearray(rng.randbytes(rng.randrange(80)))
    # plant a small length at the op's length offset half the time so success paths are reached
    if rng.random() < 0.5:
        off = {0x01: 16, 0x03: 16, 0x04: 16, 0x11: 16, 0x14: 16, 0x30: 2, 0x50: 0, 0x60: 12}.get(op)
        if off is not None and len(body) >= off + 4:
            if op == 0x50: body[0] = rng.randrange(max(1, len(body)))
            elif op == 0x30: body[2:4] = struct.pack('!H', rng.randrange(len(body) + 2))
            else: body[off:off + 4] = struct.pack('!I', rng.randrange(len(body) + 2))
        if op == 0x14 and len(body) >= 24: body[22:24] = struct.pack('!H', rng.randrange(8))
    frames.append(W.MAGIC + bytes([op]) + bytes(body))

# ---- run the C++ driver once, all cases in one stream
lines = []
for c, args in wrap_cases: lines.append('wrap %s %s' % (c, ' '.join(enc_arg(x) for x in args)))
for f in frames: lines.append('parse ' + hx(f))
for f in frames[:5000]: lines.append('is_fnw1 ' + hx(f))
for op in range(256): lines.append('op_name %d' % op)
r = subprocess.run([a.driver], input='\n'.join(lines) + '\n', capture_output=True, text=True)
got = r.stdout.split('\n')
if r.returncode != 0 or len(got) < len(lines):
    print('FAIL driver exit %d, %d of %d answers; stderr: %s' % (r.returncode, len(got) - 1, len(lines), r.stderr[-2000:]))
    sys.exit(1)

bad = {'wrap': 0, 'parse': 0, 'is_fnw1': 0, 'op_name': 0, 'rt_cpp_to_py': 0, 'rt_py_to_cpp': 0}
count = dict.fromkeys(bad, 0)
shown = 0
i = 0


def miss(cat, what, exp, have):
    global shown
    bad[cat] += 1
    if shown < 15:
        shown += 1
        print('DIFF %s %s\n  python=%s\n  c++   =%s' % (cat, what[:160], exp[:300], have[:300]))


wrap_frames_cpp = []
for c, args in wrap_cases:
    exp, have = py_wrap(c, args), got[i]; i += 1; count['wrap'] += 1
    if exp != have: miss('wrap', '%s%r' % (c, tuple(x if not isinstance(x, (bytes, str)) or len(x) < 20
                                                     else '<%d>' % len(x) for x in args)), exp, have)
    wrap_frames_cpp.append((bytes.fromhex(have[3:]) if have[3:] != '.' else b'') if have.startswith('ok ') else None)
for f in frames:
    exp, have = py_parse(f), got[i]; i += 1; count['parse'] += 1
    if exp != have: miss('parse', f.hex(), exp, have)
for f in frames[:5000]:
    exp, have = ('true' if W.is_fnw1(f) else 'false'), got[i]; i += 1; count['is_fnw1'] += 1
    if exp != have: miss('is_fnw1', f.hex(), exp, have)
for op in range(256):
    exp, have = W.op_name(op), got[i]; i += 1; count['op_name'] += 1
    if exp != have: miss('op_name', str(op), exp, have)

# round trips: parse(wrap(x)) must give back x's fields -- C++ frames through Python's try_parse, Python frames
# through the C++ parser.
def expect(c, args):
    d = dict.fromkeys(FIELDS)
    if c in ('req_full', 'req_raw', 'req_diff'): d['req_hash'], d['payload'] = args
    elif c in ('req_repeat', 'req_miss'): d['req_hash'] = args[0]
    elif c == 'resp_same': d['same_id'] = args[0]
    elif c == 'resp_diff': d['same_id'], d['payload'] = args
    elif c == 'resp_raw': d['same_id'], d['status'], d['headers'], d['body'] = args
    elif c == 'error': d['status'], d['payload'] = args[0], args[1].encode('utf-8')
    elif c == 'hello': d['body'] = args[0].encode('ascii')
    elif c == 'rtt_ping': d['ping_id'], d['proxy_t_send_ns'], d['payload'] = args[0], args[1], b''
    elif c == 'rtt_pong': (d['ping_id'], d['proxy_t_send_ns'], d['daemon_t_recv_ns'], d['daemon_t_reply_ns']) = args
    m = W.WireMsg(op={'req_full': 1, 'req_repeat': 2, 'req_raw': 3, 'req_diff': 4, 'resp_diff': 0x11, 'resp_same': 0x13,
                      'resp_raw': 0x14, 'req_miss': 0x21, 'error': 0x30, 'seq_reset': 0x40, 'hello': 0x50,
                      'rtt_ping': 0x60, 'rtt_pong': 0x61, 'rtt_loop': 0x62}[c], **d)
    return canon_msg(m)


ok_cases = [(c, args, f) for (c, args), f in zip(wrap_cases, wrap_frames_cpp) if f is not None]
for c, args, f in ok_cases:
    count['rt_cpp_to_py'] += 1
    exp = expect(c, args)
    have = py_parse(f)
    if have != exp: miss('rt_cpp_to_py', c, exp, have)
py_ok = []
for c, args in wrap_cases:
    try: py_ok.append((c, args, getattr(W, 'wrap_' + c)(*args)))
    except Exception: pass
r2 = subprocess.run([a.driver], input=''.join('parse %s\n' % hx(f) for _, _, f in py_ok), capture_output=True, text=True)
got2 = r2.stdout.split('\n')
for j, (c, args, f) in enumerate(py_ok):
    count['rt_py_to_cpp'] += 1
    exp = expect(c, args)
    if j >= len(got2) or got2[j] != exp: miss('rt_py_to_cpp', c, exp, got2[j] if j < len(got2) else '<none>')

from collections import Counter
print('corpus wrap outcomes (python):', dict(Counter(py_wrap(c, x).split(' ')[0] + ('' if not py_wrap(c, x).startswith('raise')
      else ' ' + py_wrap(c, x).split(' ')[1]) for c, x in wrap_cases)))
import re


def pkey(f):
    r = py_parse(f)
    if r.startswith('msg'):
        n = W.op_name(int(r.split(' ')[1][3:]))
        return 'msg ' + ('unknown-op' if n.startswith('UNKNOWN') else n)
    return re.sub(r'[0-9]+', 'N', r).split(':')[0].strip()


print('corpus parse outcomes (python):')
for k, v in sorted(Counter(pkey(f) for f in frames).items()): print('  %8d %s' % (v, k))
for k in bad:
    print('%s %s: %d cases, %d differ' % ('PASS' if bad[k] == 0 and count[k] else 'FAIL', k, count[k], bad[k]))
ok = all(v == 0 for v in bad.values()) and all(count.values())
print('RESULT %s semwire vs %s (seed %d, fuzz %d)' % ('PASS' if ok else 'FAIL', W.__file__, a.seed, a.fuzz))
sys.exit(0 if ok else 1)
