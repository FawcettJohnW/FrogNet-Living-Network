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
# S3 oracle: C++ templates (include/semtpl.hpp through tools/semtpl-driver) against John's Python, imported and run:
# core/json_handler.py, text_handler.py, template.py, template_utils.py, format_registry.py, blob_store.py, store.py
# (ids), proxy/templates.py, and urllib.parse as they call it. Every case is compared exactly.
#
# Declined semantics (see semtpl.hpp) are detected from the Python's own execution where it raises and swallows:
# json_handler's json.loads, ast.literal_eval, BlobStore.load and xml.etree.ElementTree.fromstring are wrapped and
# record each call. The branches that swallow without an exception (json_shape, array_type, pair_heuristic,
# unknown_mode) are recomputed from the inputs, and every recomputation is cross-checked against the hooks: a case
# where they disagree is an oracle error, not a pass. For a declined case the expected C++ answer is
# "raise declined <case>"; what the Python returned instead is counted and reported.
import argparse, contextlib, copy, io, json, math, os, random, shutil, struct, subprocess, sys, tempfile, types, zlib
from collections import Counter

sys.dont_write_bytecode = True
ap = argparse.ArgumentParser()
ap.add_argument('root'); ap.add_argument('--driver', default='./bin/semtpl-driver')
ap.add_argument('--seed', type=int, default=20260924); ap.add_argument('--scale', type=int, default=1)
a = ap.parse_args()
S = a.scale
rng = random.Random(a.seed)

work = tempfile.mkdtemp(prefix='semtpl-oracle-')
PYBLOB, CXXBLOB = os.path.join(work, 'py'), os.path.join(work, 'cxx')
os.makedirs(PYBLOB); os.makedirs(CXXBLOB)
os.environ['FROGNET_BLOB_ROOT'] = PYBLOB
sys.path.insert(0, a.root)
with contextlib.redirect_stdout(io.StringIO()):
    import core.json_handler as JH  # noqa: E402
    import core.text_handler as TH  # noqa: E402,F401
    import core.template as TP  # noqa: E402
    import core.template_utils as TU  # noqa: E402
    import core.format_registry as FR  # noqa: E402
    import core.blob_store as BS  # noqa: E402
    import core.store as ST  # noqa: E402
    import proxy.templates as PT  # noqa: E402
import urllib.parse as UP  # noqa: E402
import xml.etree.ElementTree as ET  # noqa: E402

# ---------------------------------------------------------------- hooks on the swallowing sites
EV = []  # declined / not-ported events in execution order


class _J:
    JSONDecodeError = json.JSONDecodeError
    @staticmethod
    def loads(s, *k, **kw):
        try: return json.loads(s, *k, **kw)
        except Exception: EV.append('json_fail'); raise
    dumps = staticmethod(json.dumps)


JH.json = _J
_lit = JH.ast.literal_eval
JH.ast = types.SimpleNamespace(literal_eval=lambda s: (EV.append('array_literal'), _lit(s))[1])
_bload = BS.BlobStore.load
def _blob_load(bid):
    try: return _bload(bid)
    except Exception: EV.append('blob_missing'); raise
JH.BlobStore = types.SimpleNamespace(load=_blob_load, store=BS.BlobStore.store)
_etfs = ET.fromstring
ET.fromstring = lambda *x, **k: (EV.append('xml_parse'), _etfs(*x, **k))[1]
_empty_seen = []
_eai = PT._empty_arrays_in
PT._empty_arrays_in = lambda obj, path='': (lambda r: (_empty_seen.append(r), r)[1])(_eai(obj, path))
PORTED = ('json', 'text', 'raw')
NOTPORTED_MODES = ('xml', 'html', 'sotf_media')


def _mode_of(h):
    for k, v in FR.FORMAT_HANDLERS.items():
        if v is h: return k
    return '?'


for _n in ('detect_request_handler', 'detect_reply_handler'):
    def _wrap(f):
        def g(*x):
            h = f(*x)
            if _mode_of(h) in NOTPORTED_MODES: EV.append('notported')
            return h
        return g
    setattr(PT, _n, _wrap(getattr(PT, _n)))

# ---------------------------------------------------------------- canonical form (as tests/semcodec_driver.cpp)
def w8(s): return s.encode('utf-8', 'surrogatepass').hex()


def cv(v):
    if v is None: return 'N'
    if v is True: return 'B1'
    if v is False: return 'B0'
    if isinstance(v, int): return 'I%d' % v
    if isinstance(v, float): return 'F' + struct.pack('>d', v).hex()
    if isinstance(v, str): return 'S' + w8(v)
    if isinstance(v, (bytes, bytearray)): return 'R' + bytes(v).hex()
    if isinstance(v, (list, tuple)): return 'L(' + ','.join(cv(x) for x in v) + ')'
    if isinstance(v, dict): return 'D(' + ','.join('S%s=%s' % (w8(k), cv(x)) for k, x in v.items()) + ')'
    raise TypeError('canon: %r' % type(v))


def cq(pairs): return 'Q(' + ','.join('S%s=%s' % (w8(k), cv(x)) for k, x in pairs) + ')'
def cs(s): return 'S' + w8(s)
def csl(v): return 'L(' + ','.join(cs(x) for x in v) + ')'
def csp(v): return 'P(' + ','.join(cs(k) + '=' + cs(x) for k, x in v) + ')'
def hx(b): return bytes(b).hex() if b else '.'
def chd(h): return 'D(' + ','.join('S%s=S%s' % (w8(k), w8(v)) for k, v in h.items()) + ')'


JOHN = ('build_url:',)


def err(e):
    if isinstance(e, UnicodeEncodeError): return 'raise encode'
    if isinstance(e, json.JSONDecodeError): return 'raise json'
    if isinstance(e, RecursionError): return 'raise recursion'
    if isinstance(e, OverflowError): return 'raise overflow'
    if isinstance(e, TypeError): return 'raise type'
    if isinstance(e, IndexError): return 'raise index'
    if isinstance(e, ValueError):
        t = str(e)
        return 'raise value ' + (t.split(' ')[0] if t.startswith(JOHN) else '*')
    return 'raise other:' + type(e).__name__


# ---------------------------------------------------------------- declined recomputation, cross-checked with EV
REPR_TYPES = (list, tuple, dict, bytes, bytearray)


def _array_issue(v):
    """First declined case _coerce_atomic_array / the top-level branch would hit for one array value, else None."""
    if v is None or isinstance(v, list): return None
    if isinstance(v, str):
        s = v.strip()
        if not s: return None
        try:
            if isinstance(json.loads(s), list): return None
        except Exception: pass
        return 'array_literal'
    return 'array_type'


def rebuild_decline(frag, values):
    mode = (frag or {}).get('mode', 'raw')
    if mode in NOTPORTED_MODES: return 'notported'
    if mode not in FR.FORMAT_HANDLERS: return 'unknown_mode'
    if mode in ('text', 'raw'):
        if values and isinstance(values[0], (tuple, list)) and len(values[0]) == 2: return 'pair_heuristic'
        if values and isinstance(values[0], REPR_TYPES): return 'text_repr'
        return None
    fo = frag.get('field_order') or []
    if not fo: return None                              # json with no fields answers "{}" before looking at values
    if values and isinstance(values[0], (tuple, list)) and len(values[0]) == 2: return 'pair_heuristic'
    valmap = {fo[i]: values[i] for i in range(min(len(fo), len(values)))}
    if fo == ['value'] and (frag.get('type_map') or {}).get('value') == 'array':
        v = valmap.get('value')
        if v is None: v = (frag.get('baseline') or {}).get('value', [])
        return _array_issue(v)
    tm, blobs = frag.get('type_map') or {}, frag.get('baseline_blobs') or {}
    for p in fo:
        if tm.get(p) != 'array': continue
        v = valmap.get(p)
        i = _array_issue(v)
        if i: return i
        if isinstance(v, str): v = json.loads(v.strip()) if v.strip() else []
        if (v is None or v == []) and p in blobs and not os.path.exists(os.path.join(BS.BLOB_ROOT, blobs[p])):
            return 'blob_missing'
    return None


def extract_decline(frag, body):
    mode = (frag or {}).get('mode', 'raw')
    if not body: return None
    if mode in NOTPORTED_MODES: return 'notported'
    if mode not in FR.FORMAT_HANDLERS: return 'unknown_mode'
    if mode != 'json': return None
    t, fo = body.strip(), frag.get('field_order') or []
    if not t or not fo: return None
    try: obj = json.loads(t)
    except Exception: return 'json_unparsable'
    if isinstance(obj, list) and fo == ['value']: return None
    return None if isinstance(obj, dict) else 'json_shape'


class Mismatch(Exception): pass


def checked(expect_decl, events, allow):
    """expect_decl from recomputation; events the hook log. They must agree on whether a hooked case fired."""
    hooked = {'json_fail': 'json_unparsable', 'array_literal': 'array_literal', 'blob_missing': 'blob_missing'}
    fired = [hooked[e] for e in events if e in hooked and hooked[e] in allow]
    if expect_decl in allow and expect_decl in hooked.values() and expect_decl not in fired:
        raise Mismatch('recomputed %s, hooks saw %s' % (expect_decl, events))
    if fired and expect_decl is None:
        raise Mismatch('hooks saw %s, recomputation saw nothing' % events)


# netlocs the C++ does not port: bracketed (ipaddress check) or non-ASCII (NFKC check). Computed as urlsplit does, up to
# those checks; unbalanced brackets are a ValueError on both sides and stay a raise.
_SCHEME = set('abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789+-.')
def _netloc(url):
    url = url.lstrip(UP._WHATWG_C0_CONTROL_OR_SPACE)
    for b in '\t\r\n': url = url.replace(b, '')
    i = url.find(':')
    if i > 0 and url[0].isascii() and url[0].isalpha() and all(c in _SCHEME for c in url[:i]): url = url[i + 1:]
    if url[:2] != '//': return ''
    d = len(url)
    for c in '/?#':
        w = url.find(c, 2)
        if w >= 0: d = min(d, w)
    return url[2:d]
def url_notported(url):
    n = _netloc(url)
    if ('[' in n) != (']' in n): return False           # unbalanced: "Invalid IPv6 URL" on both sides, checked first
    return ('[' in n and ']' in n) or (bool(n) and not n.isascii())
def norm_notported(path):
    if not path: return False
    while path.startswith('//'): path = path[1:]
    return url_notported(path)


# ---------------------------------------------------------------- python side of each command
DECL = Counter()  # (command, case, python outcome head) -> count


def py(cmd, *x):
    EV.clear()
    np = ((cmd == 'urlparse' and url_notported(x[0])) or (cmd in ('normalize', 'canon_key') and norm_notported(x[-2] if cmd == 'canon_key' else x[0]))
          or (cmd == 'dyn_vals' and x[1] and x[0] and url_notported(x[0])) or (cmd == 'build_url' and url_notported(x[0] or '/')))
    if np:
        try: out = _py(cmd, *x)
        except Exception as e: out = err(e)
        return declined(cmd, 'notported', out)
    try:
        return _py(cmd, *x)
    except Mismatch:
        raise
    except Exception as e:
        return err(e)


def declined(cmd, name, pyout):
    DECL[(cmd, name, pyout.split(' ')[0] if not pyout.startswith('raise') else ' '.join(pyout.split(' ')[:2]))] += 1
    return 'raise declined ' + name if name != 'notported' else 'raise notported'


def run(f):
    try: return 'ok ' + f()
    except Exception as e: return err(e)


def _py(cmd, *x):
    if cmd == 'urlparse': return 'ok ' + ' '.join(cs(p) for p in UP.urlparse(x[0]))
    if cmd == 'parse_qsl': return 'ok ' + csp(UP.parse_qsl(x[0], keep_blank_values=True))
    if cmd == 'parse_qs':
        q = UP.parse_qs(x[0], keep_blank_values=True)
        return 'ok Q(' + ','.join(cs(k) + '=' + csl(v) for k, v in q.items()) + ')'
    if cmd == 'urlencode': return 'ok ' + cs(UP.urlencode(x[0]))
    if cmd == 'normalize':
        p, d = PT.normalize_path_for_semantics(x[0]); return 'ok %s %s' % (cs(p), csl(d))
    if cmd == 'dyn_vals': return 'ok ' + csp(PT.extract_dynamic_query_vals(x[0], x[1]))
    if cmd == 'dyn_shape': return 'ok ' + cs(PT._with_dynamic_shape(x[0], x[1]))
    if cmd == 'canon_key':
        with contextlib.redirect_stdout(io.StringIO()): return 'ok ' + cs(PT.canonical_semantic_key(x[0], x[1], x[2]))
    if cmd == 'first_row': return 'ok ' + cv(PT._first_row_only(x[0]))
    if cmd == 'empty_arrays': return 'ok ' + csl(_eai(x[0]))
    if cmd == 'tpl_id': return 'ok ' + cs(ST.TemplateStore._template_id_from_key(None, x[0], x[1]))
    if cmd == 'opcode': return 'ok %d' % ST.TemplateStore._opcode_from_semantic_key(None, x[0])
    if cmd == 'pred':
        f = {'ipv4': JH._is_ipv4, 'iface': JH._looks_like_interface, 'enum': JH._looks_like_enum,
             'host': JH._is_plausible_host}[x[0]]
        return 'ok ' + ('1' if f(x[1]) else '0')
    if cmd == 'leaf': return 'ok ' + cs(JH._leaf_type(x[0], x[1]))
    if cmd == 'sanitize': return 'ok ' + cv(JH._sanitize_nonfinite(x[0]))
    if cmd == 'dumps':
        return 'ok ' + cs(json.dumps(x[1]) if x[0] else json.dumps(x[1], separators=(',', ':')))
    if cmd == 'schema': return 'ok ' + cv(JH.infer_schema_preserve_arrays(x[0], x[1]))
    if cmd == 'merge': return 'ok ' + cv(JH._merge_schema(x[0], x[1]))
    if cmd == 'fields':
        fo, tm = JH._build_field_order_and_type_map(x[0]); return 'ok %s %s' % (csl(fo), cv(tm))
    if cmd == 'rebuild_json': return 'ok ' + cv(TU.rebuild_json(x[0], x[1]))
    if cmd == 'learn':
        mode, reply, body = x
        h = FR.FORMAT_HANDLERS[mode]
        out = run(lambda: cv((h.learn_reply_template if reply else h.learn_request_template)(body)))
        ev = list(EV)
        if mode in NOTPORTED_MODES: return declined(cmd, 'notported', out)
        if 'json_fail' in ev: return declined(cmd, 'json_unparsable', out)
        return out
    if cmd == 'extract':
        frag, reply, body = copy.deepcopy(x[0]), x[1], x[2]
        d = extract_decline(frag, body)
        if reply: t = TP.ReplyTemplate('t', 1, frag, frag.get('tokens') or {})
        else: t = TP.RequestTemplate('t', 1, 'GET', '/', [], frag, frag.get('tokens') or {})
        out = run(lambda: cq(t.extract_dynamic(body)))
        checked(d, EV, ('json_unparsable',))
        if d in ('json_unparsable', 'json_shape'):      # the Python's [] here is the daemon's RESP_RAW signal: C++ says so
            DECL[(cmd, 'cannot_hold(' + d + ')', out.split(' ')[0])] += 1
            return 'ok H'
        return declined(cmd, d, out) if d else out
    if cmd == 'rebuild':
        frag, values = copy.deepcopy(x[0]), x[1]
        d = rebuild_decline(frag, values)
        t = TP.ReplyTemplate('t', 1, frag, frag.get('tokens') or {})
        out = run(lambda: cs(t.rebuild(values)))
        checked(d, EV, ('array_literal', 'blob_missing'))
        return declined(cmd, d, out) if d else out
    if cmd == 'build_url':
        us, vals = x
        t = TP.RequestTemplate('t', 1, 'GET', us, [], {}, {})
        out = run(lambda: cs(t.build_url(vals)))
        for _, v in vals:  # in order: a None first is John's ValueError, a repr() value first is declined
            if v is None: break
            if isinstance(v, REPR_TYPES): return declined(cmd, 'text_repr', out)
        return out
    if cmd == 'rebuild_body':
        frag, vals = copy.deepcopy(x[0]), x[1]
        t = TP.RequestTemplate('t', 1, 'GET', '/', [], frag, frag.get('tokens') or {})
        out = run(lambda: cs(t.rebuild_body(vals)))
        if t.mode != 'json' and vals:
            m = {str(k): v for k, v in vals}
            if t.mode in ('raw', 'text', 'html', 'xml'): v0 = m.get('raw')
            else: v0 = next(iter(m.values()))
            if isinstance(v0, REPR_TYPES): return declined(cmd, 'text_repr', out)
        return out
    if cmd == 'ctype':
        return 'ok ' + cs(TP.ReplyTemplate('t', 1, copy.deepcopy(x[0]), {}).content_type())
    if cmd in ('sniff', 'detect_req', 'detect_rep'):
        if cmd == 'sniff': out = run(lambda: cs(FR.sniff_body_mode(x[0])))
        elif cmd == 'detect_req': out = run(lambda: cs(_mode_of(FR.detect_request_handler(x[0], x[1]))))
        else: out = run(lambda: cs(_mode_of(FR.detect_reply_handler(x[0], x[1]))))
        if 'xml_parse' in EV or any(out == 'ok ' + cs(m) for m in NOTPORTED_MODES): return declined(cmd, 'notported', out)
        return out
    if cmd == 'train':
        method, sem, rqh, rqb, rph, rpb, raw = x
        got, buf = [], io.StringIO()
        store = types.SimpleNamespace(store_templates=lambda m, p, q, r: got.append((q, r)))
        _empty_seen.clear()
        with contextlib.redirect_stdout(buf):
            PT.learn_templates_from_real(method=method, semantic_path=sem, req_headers=rqh, req_body=rqb,
                                         upstream={'status': 200, 'body': rpb, 'headers': rph}, store=store, raw_path=raw)
        if got: out = 'ok T %s %s' % (cv(got[0][0]), cv(got[0][1]))
        elif _empty_seen and _empty_seen[-1]: out = 'ok R ' + csl(_empty_seen[-1])
        else: out = 'learn-failed'
        for e in EV:
            if e in ('notported', 'xml_parse'): return declined(cmd, 'notported', out)
            if e == 'json_fail': return declined(cmd, 'json_unparsable', out)
        if 'learn FAILED' in buf.getvalue(): return declined(cmd, 'train_exception', out)
        if norm_notported(raw): return declined(cmd, 'notported', out)   # the C++ reaches raw_path last
        return out
    raise ValueError('oracle: unknown command ' + cmd)


def wire(cmd, *x):
    if cmd in ('urlparse', 'parse_qsl', 'parse_qs', 'normalize'): return '%s %s' % (cmd, cs(x[0]))
    if cmd == 'urlencode': return 'urlencode L(' + ','.join('L(%s,%s)' % (cs(k), cs(v)) for k, v in x[0]) + ')'
    if cmd == 'dyn_vals': return 'dyn_vals %s %s' % (cs(x[0]), csl(x[1]))
    if cmd == 'dyn_shape': return 'dyn_shape %s %s' % (cs(x[0]), csl(x[1]))
    if cmd == 'canon_key': return 'canon_key %s %s %s' % (cs(x[0]), cs(x[1]), hx(x[2]))
    if cmd in ('first_row', 'empty_arrays', 'sanitize'): return '%s %s' % (cmd, cv(x[0]))
    if cmd == 'tpl_id': return 'tpl_id %s %s' % (cs(x[0]), cs(x[1]))
    if cmd == 'opcode': return 'opcode ' + cs(x[0])
    if cmd == 'pred': return 'pred %s %s' % (x[0], cs(x[1]))
    if cmd in ('leaf', 'schema'): return '%s %s %s' % (cmd, cv(x[0]), cs(x[1]))
    if cmd == 'dumps': return 'dumps %d %s' % (1 if x[0] else 0, cv(x[1]))
    if cmd == 'merge': return 'merge %s %s' % ('-' if x[0] is None else cv(x[0]), '-' if x[1] is None else cv(x[1]))
    if cmd == 'fields': return 'fields ' + cv(x[0])
    if cmd == 'rebuild_json': return 'rebuild_json %s %s' % (cv(x[0]), csl(x[1]))
    if cmd == 'learn': return 'learn %s %d %s' % (x[0], 1 if x[1] else 0, cs(x[2]))
    if cmd == 'extract': return 'extract %s %d %s' % (cv(x[0]), 1 if x[1] else 0, cs(x[2]))
    if cmd == 'rebuild': return 'rebuild %s %s' % (cv(x[0]), cv(x[1]))
    if cmd == 'build_url': return 'build_url %s %s' % (cs(x[0]), cq(x[1]))
    if cmd == 'rebuild_body': return 'rebuild_body %s %s' % (cv(x[0]), cq(x[1]))
    if cmd == 'ctype': return 'ctype ' + cv(x[0])
    if cmd == 'sniff': return 'sniff ' + hx(x[0])
    if cmd in ('detect_req', 'detect_rep'): return '%s %s %s' % (cmd, chd(x[0]), hx(x[1]))
    if cmd == 'train':
        return 'train %s %s %s %s %s %s %s' % (cs(x[0]), cs(x[1]), chd(x[2]), hx(x[3]), chd(x[4]), hx(x[5]), cs(x[6]))
    raise ValueError(cmd)


# ---------------------------------------------------------------- corpus pieces
IPS = ['1.2.3.4', '0.0.0.0', '255.255.255.255', '256.1.1.1', ' 10.0.0.1 ', '01.02.003.4', '1.2.3', '1.2.3.4.5',
       '\u0661.\u0662.\u0663.\u0664', '1.2.3.4\n', '\u30001.2.3.4', '1.2.3.\uff14', '1.2.3.0004', '١٢٣.1.1.1', '1..2.3']
HOSTS = ['a.b', 'node.frognet', 'FrogNetHost7', 'FrogNetHost', 'frognethost', '-a.b', 'a.b-', 'a..b', 'a b.c', 'x.dev',
         '1.2.3.x', 'A.B.C', 'é.com', 'a.é', 'a_b.c', 'ab', 'a', 'a.b\n', ' h.x ', 'x.\u212a', 'Sensor.Node.cpu.0']
IFACES = ['eth0', 'wlan0', 'wl', 'enp3s0', 'wlp2s0', 'br-lan', 'br_lan', 'docker0', 'usb0', 'veth1a', 'lo', 'lo0',
          'eth\u00e9', 'eth\u0661', 'eth.0', 'ETH0', 'eth\u00b2', 'eth\u2167', 'wlan_', 'bond0']
ENUMS = ['FAST', 'fast', 'Semantic', '\u017femantic', 'o\u212a', 'OK', 'AB', 'A', 'A_B', 'A.B', 'A1', 'ZZ9', '\u00c0B',
         'WARN ', ' bad', 'UNKNOWN\u3000', '\ufb06', 'DO\ufb06', 'ı', 'LOCAl', '__', '_A', 'A-B']
MISC = ['', ' ', 'hello', 'x', '\ud800', 'a\udfffb', '\U0001f600', '\x7f', 'tab\there', '\u00a0', 'null', '123', '1e5',
        'true', 'SensorType', 'cpu_load']
STRS = IPS + HOSTS + IFACES + ENUMS + MISC
PATHS = ['', 'dev', 'x.dev', 'X.DEV', 'kind', 'a.kind', '\u212aind', 'run_id', 'SensorType', 'a.sensortype', 'MetricName',
         'method', 'action', 'tags', 'status', 'reason', 'x.reason', 'SensorName', 'a.SensorName', 'xsensorname',
         'NetworkName', 'peer_name', 'host_name', 'a.host_name', 'value', 'a.b.c', '\u0130dev', 'DEV\u0307', 'rows.x']
KEYS = ['a', 'b', 'ok', 'rows', 'value', 'SensorID', 'SensorName', 'NetworkName', 'jsonData', 'links', 'dev', 'kind',
        'status', 'x1', '_u', 'A_B', 'host_name']
BADKEYS = ['a.b', '10.0.0.1', 'REQ->SAME', 'has space', '', '1abc', 'abc\n', 'caf\u00e9', 'x-y', '\ud800']


def rfloat():
    return rng.choice([0.0, -0.0, 1.5, -2.25, 1e300, 5e-324, 1e16, 123456789.125, float('nan'), float('inf'),
                       -float('inf'), rng.uniform(-1e6, 1e6), float(rng.randrange(-100, 100))])


def rstr():
    r = rng.random()
    if r < 0.6: return rng.choice(STRS)
    if r < 0.8: return ''.join(chr(rng.choice([rng.randrange(32, 127), rng.randrange(0xa0, 0x3000),
                                                rng.randrange(0x10000, 0x10400)])) for _ in range(rng.randrange(8)))
    return rng.choice(STRS) + rng.choice(['', '.x', '0', ' '])


def ratom():
    r = rng.random()
    if r < 0.12: return None
    if r < 0.24: return rng.choice([True, False])
    if r < 0.42: return rng.choice([0, 1, -1, 2**63 - 1, -2**63, 2**64, 10**30, rng.randrange(-10**6, 10**6)])
    if r < 0.55: return rfloat()
    return rstr()


def rjson(depth=0, safe=0.9):
    r = rng.random()
    if depth >= 3 or r < 0.45: return ratom()
    if r < 0.65: return [rjson(depth + 1, safe) for _ in range(rng.randrange(4))]
    d = {}
    for _ in range(rng.randrange(5)):
        k = rng.choice(KEYS) if rng.random() < safe else rng.choice(BADKEYS)
        d[k] = rjson(depth + 1, safe)
    return d


def sensor_reply(rows):
    return {'ok': True, 'rows': [{'SensorID': rng.randrange(1000), 'SensorName': 'n%d.net.cpu.load' % i,
                                  'NetworkName': 'node%d.frognet' % i, 'SensorType': 'CPU', 'value': rng.random() * 100,
                                  'ip': '10.0.0.%d' % i, 'dev': 'eth0', 'status': 'OK'} for i in range(rows)]}


def rbody_obj():
    r = rng.random()
    if r < 0.25: return sensor_reply(rng.randrange(0, 4))
    if r < 0.35: return {'ok': True, 'jsonData': {'kind': 'x', 'links': [{'a': i} for i in range(rng.randrange(4))]}}
    if r < 0.45: return [rjson(1) for _ in range(rng.randrange(4))]
    if r < 0.5: return rjson(2)
    d = rjson(0, 0.95)
    return d if isinstance(d, dict) else {'value': d}


def dump(o):
    r = rng.random()
    if r < 0.5: return json.dumps(o, separators=(',', ':'), ensure_ascii=False)
    if r < 0.8: return json.dumps(o)
    return json.dumps(o, indent=rng.choice([None, 2]), ensure_ascii=rng.random() < 0.5)


BAD_JSON = ['{', '{"a":', '[1,2', '{"a":1}}', "{'a':1}", '{"a":NaN}', '[Infinity,-Infinity]', '{"a":1,"a":2}',
            '{"\\ud800":1}', '["\\udc00x"]', '{"a":"\\u00zz"}', '\ufeff{"a":1}', '1', '"s"', 'true', 'null', '2.5',
            '[]', '{}', '  {"a" : [ ] }  ', '{"rows":[]}', '{"a":1} x', '\t\n{"a":{"b":{"c":[1,{"d":2}]}}}\r\n',
            '{"abc\\n":1}', '{"a":1e400}', '{"a":-0.0}', '{"a":123456789012345678901234567890}', '[[1,2],[3]]',
            '{"rows":{"x":1,"y":[1,2]}}', '{"value":1}', '{"a":"1.2.3.4","b":"eth0","c":"FAST","d":"h.x"}']


# ---------------------------------------------------------------- build the corpus
cases = []
# urllib.parse
URLS = ['/api.php?entity=sensors&action=values&SensorName=X&parse=1', '//api.php?x=1', '///a//b?c=d', '/a;p?q#f',
        'http://host:80/p/a;b?c=1#frag', 'mailto:x@y', 'a:b', '1a:b', ':x', '/p?a=%20b+c&d=%zz&e=%ff&f', '?', '#', '',
        '/x?a=1&&b=2&=3&c', ' \x00/lead', '/in\tside\r\nx?a=\tb', 'http://[::1]/x', 'http://[bad/x', '//[', '/\u00e9?\u00e9=\u00e8',
        '/p?a=1;b=2', 'HTTP://Host/P', 'http:x', 'git+ssh://h/x', '/p?key=val%26more', '//user@h:9/x?y', 'x' * 300,
        '/p?\ud800=1', '/a b?c d=e f', 'http://h\uff0e/x', 'http://h/x?a=1#b?c', '/%2F%2f?%61=%62']
for u in URLS:
    for c in ('urlparse', 'parse_qsl', 'parse_qs', 'normalize'): cases.append((c, u))
for _ in range(1500 * S):
    u = ''.join(rng.choice(['/', '//', '?', '&', '=', ';', '#', ':', '%', '%2', '%41', '%e9', '+', 'a', 'B', '1', ' ', '\t',
                            'entity', 'action', 'SensorID', 'limit', '\u00e9', '\ud800', '\U0001f600', 'http', '@', '[', ']'])
                for _ in range(rng.randrange(12)))
    for c in ('urlparse', 'parse_qsl', 'parse_qs', 'normalize'): cases.append((c, u))
QS_WORDS = ['a', 'b c', 'x=y', '&', '+', '%', '\u00e9', '\ud800', '', '~-._', '/?:@!$\'()*,;', '\U0001f600', '\x7f\x00']
for _ in range(1000 * S):
    cases.append(('urlencode', [(rng.choice(QS_WORDS), rng.choice(QS_WORDS)) for _ in range(rng.randrange(4))]))
for _ in range(1500 * S):
    u = rng.choice(URLS) if rng.random() < 0.4 else '/api.php?' + '&'.join(
        '%s=%s' % (rng.choice(['entity', 'action', 'limit', 'order', 'x', 'SensorID', 'a', 'b', '']),
                   rng.choice(['1', '', 'v', '%41', 'a+b'])) for _ in range(rng.randrange(5)))
    keys = rng.sample(['entity', 'limit', 'order', 'x', 'SensorID', 'a', 'b', 'missing', ''], rng.randrange(4))
    cases.append(('dyn_vals', u, keys))
    try: base = PT.normalize_path_for_semantics(u)[0]
    except ValueError: base = u
    cases.append(('dyn_shape', base, keys))
# canonical keys
UPSERT = '/api.php?entity=sensor_data&action=upsert_by_name'
for m in ('POST', 'post', 'GET', 'Post'):
    for p in (UPSERT, UPSERT + '&x=1', '/api.php?action=upsert_by_name&entity=sensor_data', '/other.php?entity=sensor_data&'
              'action=upsert_by_name', '//api.php?entity=sensor_data&action=upsert_by_name&SensorType=Q'):
        for b in (b'', b'{bad', b'[1]', b'{"SensorType":"CPU","SensorName":"a.b.c.load"}',
                  b'{"SensorType":" CPU ","SensorName":"a.b.c"}', b'{"SensorType":"","SensorName":"a.b.c.d"}',
                  b'{"SensorType":7}', b'{"SensorType":"T","SensorName":" .a..b.c.d. "}', b'\xff\xfe{"SensorType":"x"}',
                  b'{"SensorType":"\xc3\xa9","SensorName":"w.x.y.z.\xe2\x82\xac"}', b'{"SensorType":"A B","SensorName":7}'):
            cases.append(('canon_key', m, p, b))
# ids, including forged crc32 0 and 0xFFFFFFFF


def forge(prefix, target):
    base = zlib.crc32(prefix + b'\0\0\0\0')
    cols = [zlib.crc32(prefix + (1 << i).to_bytes(4, 'little')) ^ zlib.crc32(prefix + b'\0\0\0\0') for i in range(32)]
    want, rows, x = base ^ target, list(range(32)), 0
    basis = {}
    for i, c in enumerate(cols):
        v, m = c, 1 << i
        for bit in range(31, -1, -1):
            if not (v >> bit) & 1: continue
            if bit in basis: v ^= basis[bit][0]; m ^= basis[bit][1]
            else: basis[bit] = (v, m); break
    v = want
    for bit in range(31, -1, -1):
        if (v >> bit) & 1:
            if bit not in basis: return None
            v ^= basis[bit][0]; x ^= basis[bit][1]
    return prefix + x.to_bytes(4, 'little')


forged = []
for target in (0, 0xFFFFFFFF):
    n = 0
    while len([f for f in forged if zlib.crc32(f) == target]) < 3 and n < 5000:
        n += 1
        f = forge(('GET /p%d' % n).encode(), target)
        if f and all(32 <= c < 127 for c in f[-4:]): forged.append(f)
for f in forged: cases.append(('opcode', f.decode()))
for _ in range(1000 * S):
    m, p = rng.choice(['GET', 'post', 'Pöst', '', 'DELETE']), rng.choice(URLS + ['/\ud800x', '/p\udfff'])
    cases.append(('tpl_id', m, p)); cases.append(('opcode', m.upper() + ' ' + p))
# predicates, leaf types
for s in STRS: cases += [('pred', w, s) for w in ('ipv4', 'iface', 'enum', 'host')]
for _ in range(3000 * S):
    s = rng.choice([rstr(), rng.choice(['eth', 'A', '', '1.2.3.', 'a.', 'FrogNetHost']) + chr(rng.randrange(0x30000))
                    + rng.choice(['', 'b', '.c'])])
    cases.append(('pred', rng.choice(['ipv4', 'iface', 'enum', 'host']), s))
for _ in range(4000 * S): cases.append(('leaf', ratom() if rng.random() < 0.7 else rstr(), rng.choice(PATHS)))
for p in PATHS:
    for s in ('h.x', 'eth0', 'FAST', '1.2.3.4', 'plain'): cases.append(('leaf', s, p))
# dumps / sanitize / first_row / empty_arrays
for _ in range(2500 * S):
    o = rjson(0)
    cases += [('dumps', True, o), ('dumps', False, o), ('sanitize', o), ('first_row', o), ('empty_arrays', o)]
for o in ([b'x'], {'a': b''}, float('nan'), ['\ud800\udc00', '\ud800', '\x7f\x1f'], {'\U0001f600': 1}):
    cases += [('dumps', True, o), ('dumps', False, o)]
# schema / merge / fields / rebuild_json
schemas = []
for _ in range(2500 * S):
    o, p = rjson(0, 0.85), rng.choice(['', 'a', 'x.dev', 'SensorName'])
    cases.append(('schema', o, p))
    schemas.append(JH.infer_schema_preserve_arrays(o, p))
ODD = [{'type': 'weird'}, {'type': 'int', 'x': {'type': 'any'}}, {'_array': {'type': 'int'}, 'b': {'type': 'ip'}}, {},
       {'a': {}}, {'a': {'_array': {'a': {'type': 'host'}}}}, 5, None, 'x', [], {'type': None},
       # schemas as a stored template may hold them: keys not sorted, numeric "type" values (Python == across types)
       {'b': {'type': 'int'}, 'a': {'type': 'ip'}}, {'z': {'y': {'type': 'host'}, 'x': {'type': 'int'}}, 'a': {'_array': {'type': 'any'}}},
       {'type': 1}, {'type': 1.0}, {'type': True}, {'type': 0}, {'type': False}]
for _ in range(2500 * S):
    x = rng.choice(schemas + ODD) if rng.random() < 0.9 else None
    y = rng.choice(schemas + ODD) if rng.random() < 0.9 else None
    cases.append(('merge', x, y))
NUMTYPE = [{'type': 1}, {'type': 1.0}, {'type': True}, {'type': 0}, {'type': False}, {'type': 'int'}, {'type': 'float'}]
for x in NUMTYPE:
    for y in NUMTYPE: cases.append(('merge', x, y))   # Python == across types decides these: 1 == 1.0 == True
for sc in schemas[:1500 * S] + ODD: cases.append(('fields', sc))
RJ_PATHS = ['a', 'b', 'a.b', 'a.b.c', 'rows', 'rows.x', 'rows.y.z', '', '.a', 'a.', 'a..b', 'value', 'rows.', 'x.rows.y']
for _ in range(2000 * S):
    fo = [rng.choice(RJ_PATHS) for _ in range(rng.randrange(6))]
    cases.append(('rebuild_json', [ratom() for _ in range(rng.randrange(7))], fo))
# learn / extract / rebuild through the handlers
bodies = BAD_JSON + [dump(rbody_obj()) for _ in range(2500 * S)]
big = {'ok': True, 'links': [{'n': i, 'h': 'node%d.frognet' % i} for i in range(400)], 'tail': [1] * 3000}
bodies += [json.dumps(big), json.dumps({'a': {'b': list(range(3000))}}), json.dumps(list(range(5000)))]
frags = []
for b in bodies:
    for reply in (False, True):
        cases.append(('learn', 'json', reply, b))
    with contextlib.redirect_stdout(io.StringIO()):
        try: frags.append(JH.JsonFormatHandler().learn_reply_template(b))
        except Exception: pass
for b in ['', 'plain text', ' x ', '\ud800', '{"a":1}', 'ümlaut\n'] + [rstr() for _ in range(200 * S)]:
    for m in ('text', 'raw'): cases += [('learn', m, False, b), ('learn', m, True, b)]
for m in ('xml', 'html'): cases.append(('learn', m, True, '<a>1</a>'))
tfr = [TH.TextFormatHandler().learn_reply_template('base'), TH.RawFormatHandler().learn_reply_template('base'),
       {'mode': 'raw'}, {}, {'mode': 'bogus', 'field_order': ['raw']}, {'mode': 'xml', 'field_order': ['a']}]
for _ in range(4000 * S):
    f = rng.choice(frags)
    b = rng.choice(bodies) if rng.random() < 0.7 else dump(rbody_obj())
    cases.append(('extract', f, rng.random() < 0.5, b))
for f in tfr:
    for b in ('', 'x', '{"a":1}', ' \n'): cases.append(('extract', f, True, b))


def extracted(f, b):
    with contextlib.redirect_stdout(io.StringIO()):
        try: return [v for _, v in JH.JsonFormatHandler()._extract_dynamic(b, f)]
        except Exception: return []


ARR_STR = ['', '  ', '[1,2]', ' ["a"] ', '[1, \'a\']', "['x', None]", '{}', '{"a":1}', 'nope', '5', 'null', '[1,', '[]']
for _ in range(5000 * S):
    f = rng.choice(frags)
    vals = extracted(f, rng.choice(bodies))
    if not vals: vals = [rjson(1) for _ in range(len(f.get('field_order') or []))]
    r = rng.random()
    if r < 0.3 and vals:
        tm = f.get('type_map') or {}
        for i, p in enumerate(f.get('field_order') or []):
            if tm.get(p) == 'array' and i < len(vals):
                vals[i] = rng.choice(ARR_STR + [None, [], 7, {'a': 1}, True, [9]])
    elif r < 0.4 and vals: vals[0] = [rng.choice(['a', 1]), rjson(1)]
    elif r < 0.5: vals = vals[:rng.randrange(len(vals) + 1)] + ([ratom()] if rng.random() < 0.5 else [])
    cases.append(('rebuild', f, vals))
for f in tfr:
    for vals in ([], ['x'], [None], [5], [2.5], [True], [['k', 'v']], [[1]], [{'a': 1}], [b'x'], ['a', 'b']):
        cases.append(('rebuild', f, vals))
for f in frags:
    if f.get('baseline_blobs'):
        fo = f['field_order']
        cases.append(('rebuild', f, [None] * len(fo)))
        cases.append(('rebuild', f, [[] for _ in fo]))
        g = json.loads(json.dumps(f)); g['baseline_blobs'] = {k: 'sha256-' + '0' * 64 for k in g['baseline_blobs']}
        cases.append(('rebuild', g, [None] * len(fo)))
# RequestTemplate
STATICS = ['/', '', '/api.php?entity=sensors&action=values', '/p?a=1&a=2', '/p;x?y=1#z', '//h/p?q', '/\u00e9?k=\u00e9']
for _ in range(2000 * S):
    vals = [(rng.choice(['a', 'limit', 'x y', '\u00e9', '&', '']),
             rng.choice(['1', '', 'a b', '\u00e9', 5, 2.5, True, float('nan'), '\ud800', None, [1], {'a': 1}, b'r']))
            for _ in range(rng.randrange(4))]
    cases.append(('build_url', rng.choice(STATICS), vals))
rfr = []
for b in bodies[:800 * S]:
    with contextlib.redirect_stdout(io.StringIO()):
        try: rfr.append(JH.JsonFormatHandler().learn_request_template(b))
        except UnicodeEncodeError: pass
for _ in range(3000 * S):
    f = rng.choice(rfr + tfr + [{'mode': 'html'}, {'mode': 'xml'}, {'mode': 'weird', 'field_order': ['q']}])
    fo = f.get('field_order') or ['raw']
    vals = [(p, rjson(1)) for p in fo if rng.random() < 0.9]
    if rng.random() < 0.1: vals.append(('extra', ratom()))
    cases.append(('rebuild_body', f, vals))
for m in ('json', 'xml', 'html', 'raw', 'text', 'weird', None):
    cases.append(('ctype', {} if m is None else {'mode': m}))
# detection
DBODIES = [b'', b'{"a":1}', b' [1] ', b'{bad', b'<a/>', b'<a>', b'<!DOCTYPE html><p>', b'<html><body>x', b'<?xml version="1.0"?><r/>',
           b'plain', b'\x00\x01\x02binary\x03', b'\xff\xfe\xfd', b'<p><b><i><u><s><a><em><q>x', b'<P><B><I><U><S><A><EM><Q>x',
           b'<\xc5\xbf1><\xc5\xbf2>' * 5, b'\xef\xbb\xbf{"a":1}', b'   ', b'<!doctype xml><x/>', b'< a>', b'<a:b xmlns:a="u"/>',
           b'text/with\x1bsome\x7f\xc2\x80ctl', b'\xc4\xb0<html>' * 3, b'<-x/>', b'<_y></_y>', b'<9/>']
HDRS = [{}, {'Content-Type': 'application/json'}, {'content-type': 'text/html'}, {'Content-Type': 'application/xml'},
        {'Content-Type': 'text/plain'}, {'CONTENT-TYPE': 'application/json'}, {'Content-Type': 'text/HTML; charset=x'},
        {'Content-Type': 'image/png'}]
for b in DBODIES:
    cases.append(('sniff', b))
    for h in HDRS: cases += [('detect_req', h, b), ('detect_rep', h, b)]
for _ in range(2000 * S):
    b = bytes(rng.choice([rng.randrange(256), rng.randrange(32, 127), ord('<'), ord('{')]) for _ in range(rng.randrange(40)))
    if rng.random() < 0.3: b = rng.choice(DBODIES) + b
    cases += [('sniff', b), ('detect_req', rng.choice(HDRS), b), ('detect_rep', rng.choice(HDRS), b)]
# training pipeline (learn_templates_from_real without its store)
for _ in range(1500 * S):
    raw = rng.choice(URLS[:4] + ['/api.php?entity=sensors&action=values&limit=5&order=d', '/frognet_echo.php', ''])
    try: sem = PT.normalize_path_for_semantics(raw)[0]
    except ValueError: sem = raw
    rq = rng.choice([b'', dump(rbody_obj()).encode('utf-8', 'surrogatepass'), b'plain', b'{bad', b'<a/>'])
    rp = rng.choice([dump(rbody_obj()).encode('utf-8', 'surrogatepass'), b'{"ok":true,"rows":[]}', b'text reply', b'{bad',
                     b'<html><body>x</body></html>', b'<r/>', b'', b'\x00\x01\x02', b'[[],[1]]', b'1.0e5', b'"s"'])
    cases.append(('train', rng.choice(['GET', 'POST']), sem, rng.choice(HDRS), rq, rng.choice(HDRS), rp, raw))

# Run 1: everything that does not write blobs. Both roots hold the blobs the Python learned while the corpus was built,
# so blob-backed rebuilds have their blobs. Run 2: learn and train, each side into a fresh, empty root; the roots must
# end up identical (names and bytes).
for n in os.listdir(PYBLOB): shutil.copy(os.path.join(PYBLOB, n), os.path.join(CXXBLOB, n))
PY2, CXX2 = os.path.join(work, 'py2'), os.path.join(work, 'cxx2')
os.makedirs(PY2); os.makedirs(CXX2)
WRITERS = ('learn', 'train')
count, bad, outcomes, oracle_err = Counter(), Counter(), Counter(), 0
shown = 0


def compare(batch, root):
    global oracle_err, shown
    lines = [wire(*c) for c in batch]
    r = subprocess.run([a.driver], input='\n'.join(lines) + '\n', capture_output=True, text=True,
                       env=dict(os.environ, FROGNET_BLOB_ROOT=root))
    got = r.stdout.split('\n')
    if r.returncode != 0 or len(got) < len(lines):
        print('FAIL driver exit %d, %d of %d answers; stderr: %s' % (r.returncode, len(got) - 1, len(lines), r.stderr[-3000:]))
        sys.exit(1)
    for c, line, have in zip(batch, lines, got):
        try:
            exp = py(*c)
        except Mismatch as e:
            oracle_err += 1
            if oracle_err <= 10: print('ORACLE-ERROR %s: %s\n  in=%s' % (c[0], e, line[:300]))
            continue
        count[c[0]] += 1
        head = ' '.join(exp.split(' ')[:3]) if exp.startswith('raise') else ('ok' if c[0] != 'train' else exp[:4])
        outcomes['%s %s' % (c[0], head)] += 1
        if exp != have:
            bad[c[0]] += 1
            if shown < 20:
                shown += 1
                print('DIFF %s\n  in    =%s\n  python=%s\n  c++   =%s' % (c[0], line[:300], exp[:400], have[:400]))


compare([c for c in cases if c[0] not in WRITERS], CXXBLOB)
BS.BLOB_ROOT = PY2
compare([c for c in cases if c[0] in WRITERS], CXX2)
pyb = {n: open(os.path.join(PY2, n), 'rb').read() for n in os.listdir(PY2)}
cxb = {n: open(os.path.join(CXX2, n), 'rb').read() for n in os.listdir(CXX2)}
count['blobs'] = len(pyb)
if pyb != cxb or not pyb:
    bad['blobs'] = max(1, len(set(pyb) ^ set(cxb)) + sum(1 for n in set(pyb) & set(cxb) if pyb[n] != cxb[n]))
print('corpus outcomes (python):')
for k, v in sorted(outcomes.items()): print('  %8d %s' % (v, k))
print('declined semantics (command, case, what the Python did instead):')
for k, v in sorted(DECL.items()): print('  %8d %s %s -> python %s' % (v, *k))
print('blobs written by learn/train into fresh roots: python %d, c++ %d' % (len(pyb), len(cxb)))
for k in sorted(count): print('%s %s: %d cases, %d differ' % ('PASS' if bad[k] == 0 else 'FAIL', k, count[k], bad[k]))
if oracle_err: print('FAIL oracle: %d cases where the declined recomputation disagreed with the hooks' % oracle_err)
ok = not bad and not oracle_err and len(count) == 31
print('RESULT %s semtpl vs %s (seed %d, scale %d, %d cases)' % ('PASS' if ok else 'FAIL', JH.__file__, a.seed, S, len(cases)))
shutil.rmtree(work)
sys.exit(0 if ok else 1)
