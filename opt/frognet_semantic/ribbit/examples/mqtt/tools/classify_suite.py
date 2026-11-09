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
"""Classify Mosquitto 2.1.2's broker suite (test/broker/test.py run list) for the MQTT-on-Ribbit map.

Usage: classify.py <mosquitto-src> <run_list.txt>   -> markdown table on stdout, counts on stderr
Rules are explicit and ordered; the first rule that matches decides. Every row records which rule fired.
"""
import os, re, sys
from collections import Counter

src, run_list = sys.argv[1], sys.argv[2]
bdir = os.path.join(src, 'test', 'broker')

def text(t):
    with open(os.path.join(bdir, t), encoding='utf-8', errors='replace') as f:
        return f.read()

def desc(body):
    for line in body.splitlines():
        if line.startswith('# ') and len(line) > 2:
            return line[2:].strip()
    return ''

Q3 = {
    '01-connect-take-over.py':          'session takeover = contested truth; claim cell per connection, write-order decides',
    '07-will-takeover.py':              'takeover + will: the losing claim publishes the will',
    '02-shared-qos0-v5.py':             '$share = claims (as XREADGROUP in Redis)',
    '02-shared-nolocal.py':             '$share option rules (front validation) on top of claims',
    '05-session-expiry-v5.py':          'session expiry acts at a time: who acts, with no timer above the memory',
    '05-session-expiry-kick.py':        'session expiry acts at a time',
    '07-will-delay.py':                 'will delay acts at a time',
    '07-will-delay-reconnect.py':       'will delay cancelled by reconnect: reconnect is a newer truth than the pending will',
    '07-will-delay-recover.py':         'will delay + recovered session',
    '07-will-delay-session-expiry.py':  'will delay vs session expiry: whichever time comes first',
    '07-will-delay-session-expiry2.py': 'will delay vs session expiry',
    '02-subpub-qos0-queued-bytes.py':   'max_queued_*: there is no queue; a limit on a derived count of unread cells',
    '03-publish-qos1-queued-bytes.py':  'max_queued_*: offline "queue" is a watermark, the limit is arithmetic',
    '02-subpub-qos1-message-expiry.py': 'expiry is reader arithmetic; the republished interval is derived',
    '02-subpub-qos1-message-expiry-retain.py': 'retained + expiry: a stale cell is simply not believed',
    '02-subpub-qos1-message-expiry-will.py':   'will + expiry',
    '03-pattern-matching.py':           'wildcards +/# = partial index over levels: region API read, not a scan',
    '02-subhier-crash.py':              'subscription tree edge case: there is no subscription tree, a subscription is a held read',
}

def classify(t, body):
    conf = body
    persist = re.search(r'persistence true', conf) or t.startswith('15-persist') or '-persist' in t
    bridge = t.startswith('06-') or 'bridge' in t
    plugin = re.search(r'\.so\b|auth_plugin|^\s*f\.write\(.*plugin ', conf, re.M)
    if bridge:
        return 'N', '-', 'bridging brokers copies a topic tree between processes; one region, fronts anywhere'
    if t.startswith('14-dynsec'):
        return 'D', 'L6', 'Dynamic Security: ACL admin over $CONTROL (a plugin) - in scope?'
    if t.startswith('20-sparkplug'):
        return 'D', '-', 'Sparkplug-aware plugin - in scope?'
    if t.startswith('22-http-api') or t.startswith('17-control'):
        return 'D', '-', 'broker admin surface ($CONTROL/http_api) - in scope?'
    if t.startswith('09-extended-auth'):
        return 'D', 'L6', 'MQTT v5 AUTH exchange; Mosquitto supplies the method by plugin - which method, if any?'
    if t.startswith('09-plugin') or t.startswith('01-plugin'):
        return 'N', '-', "Mosquitto's C plugin ABI (internal); the visible auth/ACL contract is covered by pwfile/aclfile tests"
    if t.startswith('16-') or t in ('01-connect-windows-line-endings.py', '09-pwfile-parse-invalid.py',
                                     '01-connect-unix-socket.py') or t.startswith('23-'):
        return 'F', 'L8', "operator config / listener plumbing (REGION_CONFIG_IS_THE_HOSTS)"
    if persist:
        return 'D', 'L7', 'broker-restart persistence: Redis precedent says host restart = new region; or the Permanent flag'
    if t.startswith('21-proxy'):
        return 'F', 'L8', 'PROXY protocol on the listener'
    if t.startswith('08-') or re.search(r'certfile|cafile|psk_hint', conf):
        return 'F', 'L8', 'TLS on the listener'
    if t.startswith('13-') or re.search(r'protocol websockets', conf):
        return 'F', 'L8', 'websockets on the listener'
    if t in Q3:
        lay = {'01': 'L3', '07': 'L4', '02': 'L5' if 'shared' in t else 'L4', '05': 'L3', '03': 'L1', '11': 'L4'}[t[:2]]
        if t in ('02-subpub-qos0-queued-bytes.py', '03-publish-qos1-queued-bytes.py'):
            lay = 'L3'
        if t == '02-subhier-crash.py':
            lay = 'L1'
        return 'Q', lay, Q3[t]
    # applicable
    p = t[:2]
    if t == 'msg_sequence_test.py' or re.search(r'bad|invalid-utf8|long-topic|oversize|maximum-packet-size|575314|max-keepalive|zero-length-id|disconnect-v5|accept-protocol|auto-id|max-clients|max-connections', t):
        return 'A', 'L0', 'wire / CONNECT / limits (front, counts derived from presence)'
    if re.search(r'allow-anonymous|uname|acl|auth-bad-method', t):
        return 'A', 'L6', 'auth / ACL from the host operator\'s files'
    if p == '04':
        return 'A', 'L1', 'retained = one cell per topic; writing replaces; empty payload withdraws'
    if 'topic-alias' in t:
        return 'F', 'L5', 'per-connection topic alias: wire compression at the front'
    if t.startswith('10-'):
        return 'F', 'L1', 'mount_point: listener topic prefix at the front'
    if re.search(r'subscription-id|nolocal|retain-as-publish|send-retain|response-topic|content-type|payload-format|server-keepalive|assigned-client|pub-props', t):
        return 'A', 'L4', 'MQTT v5 options / properties carried in the bag'
    if p == '05' or re.search(r'recover-subscriptions|flipflop|b2c-disconnect|c2b-disconnect|clean-session', t):
        return 'A', 'L3', 'sessions: subscriptions and delivered-through marks are cells'
    if p == '07':
        return 'A', 'L4', 'will = a cell the session writes ahead; published when presence lapses'
    if re.search(r'qos1|qos2|inflight|receive-maximum|pubrec|reuse-mid|dup|1322|no-subscribers', t):
        return 'A', 'L2', 'QoS 1/2 flows: PUBACK/PUBREC after the write has an id; per-connection window at the front'
    if re.search(r'qos0|dollar|retain-disabled|subpub', t):
        return 'A', 'L1', 'publish = distinct-state cell; subscribe = held read on the matching partial index'
    return 'A', 'L?', 'UNRULED - review'

rows, counts, layers = [], Counter(), Counter()
for t in [l.strip() for l in open(run_list) if l.strip()]:
    body = text(t)
    c, lay, why = classify(t, body)
    counts[c] += 1
    layers[(lay, c)] += 1
    rows.append((t, c, lay, why, desc(body)))

print('| test | class | layer | why | incumbent\'s description |')
print('|---|---|---|---|---|')
for r in rows:
    print('| %s | %s | %s | %s | %s |' % tuple(x.replace('|', '/') for x in r))
print(dict(counts), file=sys.stderr)
print(sorted(layers.items()), file=sys.stderr)
