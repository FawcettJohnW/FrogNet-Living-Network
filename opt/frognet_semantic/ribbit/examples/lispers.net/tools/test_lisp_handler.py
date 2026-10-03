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
# frognet::LispHandler (ribbit_cpp/lisp_handler.hpp), a C++ UnRESTHandler, against a real RAM server and real UDP.
#   identity   role lisp / LispCandidate / 4342
#   election   score gates on lisp_udp_4342 + public_ip, ranks on static capability; a malformed field raises;
#              evaluate uses the WAN-inclusive hosts list, ignores the LAN list, ties go to the higher IP
#   lifecycle  advertise writes lisp/capability/host:<ip>:lisp and the bag reads back
#   codec      a mapping record learns, extracts and rebuilds; a body that is not one raises
#   boundary   a Map-Register over UDP (HMAC-SHA-256, want-notify) is verified, applied and answered with a Map-Notify;
#              a Map-Request for the registered EID is answered with a Map-Reply carrying the registered RLOC, and a
#              Map-Request for an unregistered EID with a negative reply; an ETR (LispSite) on its own Engine
#              resolves the same registration through memory
import json, os, socket, struct, subprocess, sys, time

RAM_HOST = os.environ.get('RIBBIT_RAM_HOST', '127.0.0.1'); RAM_PORT = os.environ.get('RIBBIT_RAM_PORT', '8788')
BIN = './tools/lisp-handler-driver'
UDP = int(os.environ.get('LISP_BOUNDARY_TEST_PORT', '43420'))
fails = []


def check(name, cond, detail=''):
    print('%s %s%s' % ('PASS' if cond else 'FAIL', name, (' -- ' + detail) if detail and not cond else ''))
    if not cond: fails.append(name)


class D:
    def __init__(self):
        self.p = subprocess.Popen([BIN, '--ram', RAM_HOST, RAM_PORT], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)

    def call(self, op, **a):
        self.p.stdin.write(json.dumps({'operation': op, 'args': a}) + '\n'); self.p.stdin.flush()
        r = json.loads(self.p.stdout.readline())
        if not r['ok']: raise RuntimeError(r['error'])
        return r['result']

    def err(self, op, **a):
        try: self.call(op, **a)
        except RuntimeError as e: return str(e)
        return None

    def close(self):
        self.p.stdin.close(); self.p.wait(timeout=10)


def req4(target, nonce):  # Map-Request, one EID record, ITR-RLOC 192.0.2.1 (as tools/test_held_etr_request.py)
    p = struct.pack('!I', (1 << 28) | 1) + struct.pack('=Q', nonce) + struct.pack('!H', 0) + struct.pack('!H', 1) + bytes((192, 0, 2, 1))
    return p + bytes((0, 32)) + struct.pack('!H', 1) + socket.inet_aton(target)


def parse_reply(b):  # Map-Reply (RFC 9301 5.4): type, nonce, first record's EID and locators
    typ, count = b[0] >> 4, b[3]
    nonce = struct.unpack('=Q', b[4:12])[0]
    ttl, nloc, mask = struct.unpack('!IBB', b[12:18])
    eid = socket.inet_ntoa(b[24:28])
    locs, p = [], 28
    for _ in range(nloc):
        pri, w = b[p], b[p + 1]; locs.append((socket.inet_ntoa(b[p + 8:p + 12]), pri, w)); p += 12
    return typ, count, nonce, ttl, mask, eid, locs


d = D()
try:
    # ---- identity
    check('identity', d.call('identity') == {'role': 'lisp', 'candidate': 'LispCandidate', 'port': 4342}, str(d.err('identity')))
    # ---- election
    good = {'lan_ip': '10.10.10.1', 'lisp_udp_4342': True, 'public_ip': '203.0.113.5', 'cpu_bench_total': 5000}
    check('score: capable, benchmark', d.err('score', cand=good) is None and d.call('score', cand=good) == 5.0, str(d.err('score', cand=good)))
    cores = {'lan_ip': '10.10.20.1', 'lisp_udp_4342': True, 'public_ip': '203.0.113.6', 'cores': 4}
    check('score: capable, no benchmark -> cores', d.err('score', cand=cores) is None and d.call('score', cand=cores) == 2.0)
    for why, c in (('udp 4342 closed', dict(good, lisp_udp_4342=False)), ('no udp field', {k: v for k, v in good.items() if k != 'lisp_udp_4342'}),
                   ('no public ip', dict(good, public_ip=''))):
        check('score: ineligible, ' + why, d.err('score', cand=c) is None and d.call('score', cand=c) == -1.0)
    e = d.err('score', cand=dict(good, cpu_bench_total='fast'))
    check('score: malformed capability field raises, naming it', e is not None and 'cpu_bench_total' in e and 'handler red' not in e, str(e))
    hosts = [dict(good, lan_ip='10.10.30.1', lisp_udp_4342=False), dict(good, lan_ip='10.10.40.1', cpu_bench_total=3000), good]
    lan = [dict(good, lan_ip='10.10.50.1', cpu_bench_total=90000)]
    check('evaluate: best capable host from the WAN-inclusive list; LAN list ignored',
          d.err('evaluate', hosts=hosts, lan=lan) is None and d.call('evaluate', hosts=hosts, lan=lan) == {'list': 'hosts', 'index': 2})
    tie = [dict(good, lan_ip='10.10.60.1'), dict(good, lan_ip='10.10.70.1')]
    check('evaluate: tie goes to the higher IP', d.err('evaluate', hosts=tie, lan=[]) is None and d.call('evaluate', hosts=tie, lan=[]) == {'list': 'hosts', 'index': 1})
    check('evaluate: nobody capable -> none', d.err('evaluate', hosts=[hosts[0]], lan=[]) is None and d.call('evaluate', hosts=[hosts[0]], lan=[]) is None)
    # ---- lifecycle
    cap = {'lisp_udp_4342': True, 'public_ip': '203.0.113.5', 'cpu_bench_total': 5000, 'lan_ip': '10.10.10.1'}
    e = d.err('advertise', ip='10.10.10.1', capability=json.dumps(cap))
    check('advertise writes lisp/capability', e is None, str(e))
    if e is None:
        cells = d.call('memory.read', service='lisp', variable='capability', instance='host:10.10.10.1:lisp')
        check('advertise: the capability reads back under host:<ip>:lisp', len(cells) == 1 and cells[0]['bag'].get('public_ip') == '203.0.113.5', str(cells))
    # ---- codec
    rec = {'iid': '1000', 'eid': '10.1.0.0/16', 'ttl': 1440,
           'rlocs': [{'address': '192.0.2.10', 'priority': 1, 'weight': 50}, {'address': '192.0.2.11', 'priority': 1, 'weight': 50}]}
    e = d.err('learn', body=json.dumps(rec), reply=True)
    check('codec: learn a mapping record', e is None, str(e))
    if e is None:
        frag = d.call('learn', body=json.dumps(rec), reply=True)
        check('codec: fragment mode lisp, fields iid/eid/ttl/rlocs', frag.get('mode') == 'lisp' and frag.get('field_order') == ['iid', 'eid', 'ttl', 'rlocs'], str(frag))
        fields = d.call('extract', fragment=json.dumps(frag), body=json.dumps(rec), reply=True)
        check('codec: extract', fields == [['iid', '1000'], ['eid', '10.1.0.0/16'], ['ttl', 1440], ['rlocs', rec['rlocs']]], str(fields))
        out = d.call('rebuild', fragment=json.dumps(frag), values=json.dumps([v for _, v in fields]))
        check('codec: rebuild is the record', json.loads(out) == rec, out)
        for why, body in (('not JSON', '{'), ('not an object', '[1]'), ('missing ttl', json.dumps({k: v for k, v in rec.items() if k != 'ttl'})),
                          ('rloc without address', json.dumps(dict(rec, rlocs=[{'priority': 1, 'weight': 1}])))):
            e = d.err('extract', fragment=json.dumps(frag), body=body, reply=True)
            check('codec: body %s raises' % why, e is not None and 'handler red' not in e, str(e))
    # ---- boundary + site
    SITE, EID, RLOC, PW = '198.18.0.0/16', '198.18.7.0/24', '192.0.2.77', 'boundary-secret'
    e = d.err('boundary.open', udp_port=UDP)
    check('boundary opens UDP %d' % UDP, e is None, str(e))
    if e is None:
        check('boundary: site with its password', d.call('boundary.call', engine_op='site.add', args=json.dumps(
            {'iid': '0', 'prefix': SITE, 'group': '', 'accept_more_specifics': True, 'key_id': 1, 'password': PW})) == 'good')
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.settimeout(3)
        reg = bytes.fromhex(d.call('encode_register', key_id=1, alg=2, password=PW, notify=True, nonce=4242, ttl=3, prefix=EID, rloc=RLOC))
        s.sendto(reg, ('127.0.0.1', UDP))
        try: nt = s.recv(4096)
        except socket.timeout: nt = b''
        check('Map-Register over UDP -> Map-Notify back (type 4, same nonce)', len(nt) > 12 and nt[0] >> 4 == 4 and nt[4:12] == reg[4:12], nt.hex())
        bad = bytearray(reg); bad[-1] ^= 1
        s.sendto(bytes(bad), ('127.0.0.1', UDP))
        try: nb = s.recv(4096)
        except socket.timeout: nb = b''
        check('Map-Register with a bad HMAC is not answered', nb == b'', nb.hex())
        rep = b''
        for _ in range(20):  # the registration reaches the boundary's held resolver through memory
            s.sendto(req4('198.18.7.9', 77), ('127.0.0.1', UDP))
            try: rep = s.recv(4096)
            except socket.timeout: rep = b''
            if len(rep) > 16 and rep[16] > 0: break  # locator count of the first record
            time.sleep(0.2)
        r = parse_reply(rep) if len(rep) >= 28 else None
        check('Map-Request -> Map-Reply with the registered RLOC', r is not None and r[0] == 2 and r[2] == 77 and r[5] == '198.18.7.0'
              and [x[0] for x in r[6]] == [RLOC], str(r))
        s.sendto(req4('203.0.113.200', 78), ('127.0.0.1', UDP))
        try: neg = s.recv(4096)
        except socket.timeout: neg = b''
        r = parse_reply(neg) if len(neg) >= 28 else None
        check('Map-Request for an unregistered EID -> negative reply (no locators)', r is not None and r[0] == 2 and r[2] == 78 and r[6] == [], str(r))
        c = d.call('boundary.counts')
        check('boundary counted what it handled', c['registers'] == 2 and c['requests'] >= 2, str(c))
        e = d.err('site.open')
        check('site opens on its own Engine', e is None, str(e))
        if e is None:
            got = d.call('site.call', engine_op='resolution.get', args=json.dumps({'iid': '0', 'prefix': '198.18.7.9/32', 'group': ''}))
            got = json.loads(got) if isinstance(got, str) else got
            check('the site resolves the boundary\'s registration through memory',
                  isinstance(got, dict) and [x['address'] for x in got.get('rlocs', [])] == [RLOC], str(got))
        check('boundary stops', d.call('boundary.stop') == 'good')
finally:
    d.close()
print('RESULT %s lisp_handler: %d fail' % ('PASS' if not fails else 'FAIL', len(fails)))
sys.exit(1 if fails else 0)
