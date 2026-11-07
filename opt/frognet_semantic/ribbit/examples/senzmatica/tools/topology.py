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
"""Real-kernel topology tier for the remote-configuration slice.

Three Linux network namespaces joined by a bridge, real TCP between them:
  ops  10.77.0.10  MongoDB, Mosquitto, the converted Java backend (operator, via JNI)
  ram  10.77.0.20  the RAM memory (region-host)
  dev  10.77.0.30  the synthetic device participant (MQTT reached through a local forwarder)
The harness runs in the root namespace (10.77.0.1) and observes through HTTP, the Mongo shell and an
independent RAM probe session. Partitions are silent nftables drops inside one namespace, so neither
end sees a link-down; heal removes the rule. Device "reboot" is a process restart.

Invariants are asserted in every scenario (a failure stops the run):
  I1 no UPDATED history entry for a value the device does not actually hold
  I2 no value is physically applied more than once by one device process
  I3 a rebooted device applies only the latest desired value, never an intermediate one
Everything else (whether sessions survive, how long things take, what the operator sees) is recorded
as observed data, not asserted.
"""
from pathlib import Path
import argparse, json, os, socket, subprocess, time, urllib.request, urllib.error

p = argparse.ArgumentParser()
p.add_argument('--workspace', type=Path, required=True)
p.add_argument('--state', type=Path, required=True)
a = p.parse_args()
w = a.workspace.resolve(); state = a.state.resolve(); state.mkdir(parents=True, exist_ok=True)
assert not (state / 'result.json').exists(), 'Use a fresh run directory'
here = Path(__file__).resolve().parents[1]
stock = here.parent / 'mqtt/stock'; build = here / 'build'; fixtures = here / 'fixtures'
source = w / 'senzmatica-converted'
java = w / 'baseline-tools/zulu8.98.0.107-ca-jdk8.0.512-linux_x64/bin/java'
mongo = w / 'baseline-tools/mongodb-linux-x86_64-ubuntu2004-5.0.31/bin'
OPS, RAM, DEV, ROOT = '10.77.0.10', '10.77.0.20', '10.77.0.30', '10.77.0.1'
RAMADDR = RAM + ':18884'
api = 'http://' + OPS + ':18080/core/device'
env = os.environ.copy(); env['LD_LIBRARY_PATH'] = str(stock / 'lib'); env['NO_PROXY'] = '*'
urlopen = urllib.request.build_opener(urllib.request.ProxyHandler({})).open
T0 = time.monotonic()
processes, logs = {}, []
result = {'tier': 'real-kernel network namespaces (veth + bridge), single host',
          'topology': {'ops': OPS, 'ram': RAM, 'dev': DEV, 'observer': ROOT},
          'keepalive_note': 'frogram/ram_host sockets: SO_KEEPALIVE idle 2 s, interval 1 s, 3 probes; TCP_USER_TIMEOUT 5000 ms',
          'scenarios': [], 'invariants': []}

def now(): return round(time.monotonic() - T0, 3)
def save(): (state / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
def sh(*cmd, check=True, **kw): return subprocess.run([str(x) for x in cmd], check=check, capture_output=True, text=True, **kw)
def nsx(ns, *cmd): return ['ip', 'netns', 'exec', ns] + [str(x) for x in cmd]

# ---- network -------------------------------------------------------------------------------------
def net_up():
    net_down()
    sh('ip', 'link', 'add', 'br77', 'type', 'bridge'); sh('ip', 'addr', 'add', ROOT + '/24', 'dev', 'br77'); sh('ip', 'link', 'set', 'br77', 'up')
    for ns, ip in (('ops', OPS), ('ram', RAM), ('dev', DEV)):
        sh('ip', 'netns', 'add', 't77' + ns)
        sh('ip', 'link', 'add', 'v77' + ns, 'type', 'veth', 'peer', 'name', 'eth0', 'netns', 't77' + ns)
        sh('ip', 'link', 'set', 'v77' + ns, 'master', 'br77'); sh('ip', 'link', 'set', 'v77' + ns, 'up')
        sh(*nsx('t77' + ns, 'ip', 'addr', 'add', ip + '/24', 'dev', 'eth0'))
        sh(*nsx('t77' + ns, 'ip', 'link', 'set', 'eth0', 'up')); sh(*nsx('t77' + ns, 'ip', 'link', 'set', 'lo', 'up'))
def net_down():
    for ns in ('ops', 'ram', 'dev'): sh('ip', 'netns', 'del', 't77' + ns, check=False)
    sh('ip', 'link', 'del', 'br77', check=False)
def split(ns, peer):
    """Silently drop everything between namespace `ns` and address `peer`, both directions."""
    rules = ('add table inet split\n'
             'add chain inet split in { type filter hook input priority 0; policy accept; }\n'
             'add chain inet split out { type filter hook output priority 0; policy accept; }\n'
             f'add rule inet split in ip saddr {peer} drop\nadd rule inet split out ip daddr {peer} drop\n')
    subprocess.run(nsx('t77' + ns, 'nft', '-f', '-'), input=rules, text=True, check=True)
def heal(ns): sh(*nsx('t77' + ns, 'nft', 'delete', 'table', 'inet', 'split'))

# ---- processes -----------------------------------------------------------------------------------
def start(name, ns, cmd, stdin=False):
    log = (state / (name + '.log')).open('a'); logs.append(log)
    log.write(f'---- start at {now()} s\n'); log.flush()
    child = subprocess.Popen(nsx('t77' + ns, *cmd), env=env, cwd=state, stdin=subprocess.PIPE if stdin else subprocess.DEVNULL,
                             stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    processes[name] = child; return child
def supervised(name, ns, cmd):
    """Restart=always, RestartSec=2, as deliverables/senz-test-device.service: each run is its own log segment."""
    line = ' '.join("'" + str(x) + "'" for x in cmd)
    loop = f'while :; do echo "---- start at supervisor $(date +%s.%N)"; {line} </dev/null; echo "---- exited rc=$?"; sleep 2; done'
    return start(name, ns, ['bash', '-c', loop])
def stop(name):
    child = processes.pop(name, None)
    if child and child.poll() is None:
        try: os.killpg(child.pid, 15)
        except ProcessLookupError: pass
        try: child.wait(timeout=15)
        except subprocess.TimeoutExpired: os.killpg(child.pid, 9); child.wait()
def wait(pred, label, timeout=20.0):
    deadline = time.monotonic() + timeout
    while True:
        v = pred()
        if v: return v
        if time.monotonic() > deadline: raise AssertionError(label)
        time.sleep(0.05)   # oracle observation only; no latency is reported from this loop
def port(host, number, name, timeout=120):
    def ready():
        if processes[name].poll() is not None: raise RuntimeError(name + ' exited; see its log')
        try:
            with socket.create_connection((host, number), 0.2): return True
        except OSError: return False
    wait(ready, name + ' port ' + str(number), timeout)
def logtext(name):
    f = state / (name + '.log'); return f.read_text(errors='replace') if f.exists() else ''

# ---- observation ---------------------------------------------------------------------------------
def mongo_eval(js):
    r = subprocess.run(nsx('t77ops', mongo / 'mongo', '--quiet', '--host', '127.0.0.1', '--port', '27027', 'senz-baseline', '--eval', js),
                       env=env, capture_output=True, text=True, timeout=20)
    if r.returncode: raise RuntimeError(r.stdout + r.stderr)
    return r.stdout.strip()
def snapshot(): return json.loads(mongo_eval('print(JSON.stringify(db.device.findOne({_id:"test-device-001"})))'))
def view():
    d = snapshot()['deviceParameterConfiguration']
    return {'value': d['remoteConfigurations'][0]['defaultValue'],
            'history': [(h['updateStatus'], list(h.get('updateParamList', {}).values())[0]['defaultValue'] if h.get('updateParamList') else None)
                        for h in d.get('updateHistory', [])]}
def http(url, body=None):
    req = urllib.request.Request(url, data=None if body is None else json.dumps(body).encode(),
                                 headers={'Content-Type': 'application/json'}, method='GET' if body is None else 'PUT')
    with urlopen(req, timeout=15) as r: return json.loads(r.read())
def change(value):
    body = json.loads((fixtures / 'change.json').read_text()); body['remoteConfigurations'][0]['defaultValue'] = str(value)
    t = now()
    try: r = http(api + '/deviceParameterConfiguration/test-device-001/user/test-operator', body); out = {'at': t, 'value': value, 'response': r.get('statusCode')}
    except urllib.error.HTTPError as e: out = {'at': t, 'value': value, 'http_error': e.code, 'body': e.read().decode(errors='replace')[:300]}
    except OSError as e: out = {'at': t, 'value': value, 'error': str(e)}
    return out
def device_value():
    f = state / 'device-setting.txt'; return f.read_text().strip() if f.exists() else None

class Probe:
    """An independent RAM session from the root namespace: sees the memory without being partitioned."""
    def __init__(self):
        self.log = (state / 'probe.log').open('a'); logs.append(self.log)
        self.p = subprocess.Popen([str(build / 'probe'), RAMADDR], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.log, text=True, env=env, cwd=state)
        assert self.p.stdout.readline().strip() == 'READY'
    def rows(self, family, c=''):
        self.p.stdin.write(json.dumps({'op': 'get', 'a': 'senz-baseline', 'b': family, 'c': c}) + '\n'); self.p.stdin.flush()
        return json.loads(self.p.stdout.readline())['rows']
    def present(self):
        return {'capability': len(self.rows('capability|test-device-001')) > 0,
                'desired': [r['bag'].get('value') for r in self.rows('desired-config|test-device-001')],
                'applied': [r['bag'].get('value') + '/' + r['bag'].get('status', '') for r in self.rows('applied-config|test-device-001')]}
    def close(self):
        if self.p.poll() is None: self.p.terminate(); self.p.wait(timeout=10)

def applications(log):
    """Physical applications in one device log segment: the values whose APPLIED line raised the device's
    own applications counter (a line that leaves the counter unchanged is a report, not an action)."""
    out, prev = [], 0
    for line in log.splitlines():
        if line.startswith('APPLIED '):
            parts = dict(x.split('=') for x in line.split()[1:]); n = int(parts['applications'])
            if n > prev: out.append(parts['value'])
            prev = n
    return out
def segment(name):
    """The current (last) process segment of a log."""
    return logtext(name).rsplit('---- start at', 1)[-1]
def ever_held():
    held = {'30'}
    for seg in logtext('device').split('---- start at')[1:]: held.update(applications(seg))
    return held

def invariants(label):
    v = view(); held = device_value()
    updated = [val for st, val in v['history'] if st == 'UPDATED']
    ok1 = all(val in ever_held() for val in updated)
    apps = applications(segment('device'))
    ok2 = len(apps) == len(set(apps))
    rec = {'after': label, 'at': now(), 'view_value': v['value'], 'device_holds': held, 'history': v['history'],
           'I1_no_false_success': ok1, 'I2_no_repeat_application': ok2, 'device_ram_applications_this_boot': apps}
    result['invariants'].append(rec); save()
    if not ok1: raise AssertionError('I1 violated after ' + label + ': ' + json.dumps(rec))
    if not ok2: raise AssertionError('I2 violated after ' + label + ': ' + json.dumps(rec))
    return rec

def scenario(name, **facts):
    facts = {'name': name, **facts}; result['scenarios'].append(facts); save(); print('SCENARIO', json.dumps(facts), flush=True); return facts

# ---- run -----------------------------------------------------------------------------------------
probe = None
try:
    net_up()
    sh('ping', '-c1', '-W2', OPS); sh(*nsx('t77dev', 'ping', '-c1', '-W2', RAM))
    (state / 'mongo').mkdir(exist_ok=True)
    (state / 'mosquitto.conf').write_text(('user root\n' if os.geteuid() == 0 else '') + 'listener 18883 0.0.0.0\nallow_anonymous true\npersistence false\nlog_type all\n')
    props = (Path(__file__).parent / 'run.py').read_text().split("props=", 1)[1].split('\n', 1)[0]
    props = eval(props, {'state': state, 'str': str})   # the runner's own property set, reused verbatim
    props.update({'server.address': OPS, 'ribbit.address': RAMADDR})
    (state / 'local.properties').write_text(''.join(k + '=' + v + '\n' for k, v in props.items()))
    start('mongo', 'ops', [mongo / 'mongod', '--nounixsocket', '--bind_ip', '127.0.0.1', '--port', '27027', '--dbpath', state / 'mongo',
                          '--setParameter', 'diagnosticDataCollectionEnabled=false'])
    start('broker', 'ops', [stock / 'src/mosquitto', '-c', state / 'mosquitto.conf']); port(OPS, 18883, 'broker')
    start('ram', 'ram', [build / 'region-host', RAMADDR], stdin=True); port(RAM, 18884, 'ram')
    def backend():
        start('backend', 'ops', [java, '-Djava.library.path=' + str(build), '-Dloader.path=' + str(source / 'lib'), '-cp', source / 'core-service/target/core-service-1.0.0.0.jar',
                                 'org.springframework.boot.loader.PropertiesLauncher', '--spring.config.location=' + str(state / 'local.properties')])
        port(OPS, 18080, 'backend')
        def api_ready():
            try: return http(api + '/deviceParameterConfiguration')
            except OSError: return None
        wait(api_ready, 'backend API', 60)
        http(api + '/remoteConfiguration/path', {'ram': True})
    backend()
    initial = json.loads((fixtures / 'initial.json').read_text()); dev = initial['device']; dev['_id'] = dev.pop('id')
    prod = initial['productType']; prod['_id'] = 'test-product-001'
    mongo_eval('db.device.deleteMany({});db.productType.deleteMany({});db.message.deleteMany({});db.device.insert(' + json.dumps(dev) + ');db.productType.insert(' + json.dumps(prod) + ');')
    (state / 'device-setting.txt').write_text('30')
    start('mqttfwd', 'dev', ['socat', 'TCP-LISTEN:18883,bind=127.0.0.1,reuseaddr,fork', 'TCP:' + OPS + ':18883'])
    def device():
        supervised('device', 'dev', [build / 'test-device', RAMADDR, '18883', state / 'device-setting.txt'])
        wait(lambda: 'READY' in segment('device'), 'device READY', 20)
    def boots(): return logtext('device').count('---- start at supervisor')
    device()
    probe = Probe()
    wait(lambda: probe.present()['capability'], 'device capability visible', 10)
    invariants('startup')

    # S1: a change across namespaces
    c = change(60); t = now()
    wait(lambda: view()['value'] == '60', 'S1 change not applied', 20)
    scenario('S1 cross-namespace change', submit=c, applied_seen_at=now(), seconds=round(now() - t, 3), ram=probe.present())
    invariants('S1')

    # S2: device split from RAM for 3 s (under the 5 s keepalive), change submitted during the split
    split('dev', RAM); t = now(); c = change(90); time.sleep(3.0); during = probe.present(); heal('dev')
    try: wait(lambda: view()['value'] == '90', 'S2', 20); s2 = 'applied after heal'
    except AssertionError: s2 = 'not applied within 20 s of heal'
    scenario('S2 device split 3 s, change during split', split_at=t, healed_at=round(t + 3.0, 3), submit=c, ram_during_split=during,
             outcome=s2, outcome_at=now(), ram_after=probe.present(), device_session_ended='ended' in segment('device'))
    invariants('S2')

    # S3: device split 15 s (past the keepalive), change submitted during the split. Nothing is restarted by hand:
    # the device's session ends, its process ends, its supervisor starts fresh ones until one connects.
    boots0 = boots(); split('dev', RAM); t = now(); time.sleep(1.0); c = change(120)
    gone_at = None
    for _ in range(140):
        if not probe.present()['capability']: gone_at = now(); break
        time.sleep(0.1)
    time.sleep(max(0.0, t + 15.0 - time.monotonic() + T0)); heal('dev'); healed = now()
    try: wait(lambda: view()['value'] == '120', 'S3', 180); s3 = 'applied by a fresh device session after heal'
    except AssertionError: s3 = 'not applied within 180 s of heal'
    back_at = now()
    rec = invariants('S3')
    ok3 = rec['device_ram_applications_this_boot'] == ['120'] if s3.startswith('applied') else '120' not in rec['device_ram_applications_this_boot']
    result['invariants'][-1]['I3_returning_device_applies_latest_only'] = ok3; save()
    if not ok3: raise AssertionError('I3 violated in S3: ' + json.dumps(rec['device_ram_applications_this_boot']))
    scenario('S3 device split 15 s, change during split, no manual restart', split_at=t, submit=c, capability_gone_seen_at=gone_at,
             healed_at=healed, outcome=s3, outcome_at=back_at, seconds_from_heal=round(back_at - healed, 3),
             device_process_starts_during_and_after_split=boots() - boots0,
             session_end_lines=[l for l in logtext('device').splitlines() if l.startswith('SESSION_ENDED')][-2:],
             ram_after=probe.present())

    # S4: two changes while the device is down, then it comes back (node reboot with a pending queue of intent)
    stop('device'); c1 = change(150); c2 = change(180); time.sleep(1.0); device()
    try: wait(lambda: view()['value'] == '180', 'S4', 20); s4 = 'latest applied'
    except AssertionError: s4 = 'not applied within 20 s'
    rec = invariants('S4')
    apps = rec['device_ram_applications_this_boot']
    ok3 = apps == ['180'] if s4 == 'latest applied' else '150' not in apps
    result['invariants'][-1]['I3_returning_device_applies_latest_only'] = ok3; save()
    if not ok3: raise AssertionError('I3 violated in S4: ' + json.dumps(apps))
    scenario('S4 two changes while device down, device returns', submits=[c1, c2], outcome=s4, outcome_at=now(), applied_this_boot=apps)

    # S5: operator (backend) split from RAM 15 s, change submitted during the split; nothing restarted by hand
    split('ops', RAM); t = now(); time.sleep(1.0); c = change(210)
    desired_gone_at = None
    for _ in range(140):
        if not probe.present()['desired']: desired_gone_at = now(); break
        time.sleep(0.1)
    during = change(225) if desired_gone_at else None   # after the session is known gone: must fail visibly
    time.sleep(max(0.0, t + 15.0 - time.monotonic() + T0)); heal('ops'); healed = now()
    try: wait(lambda: view()['value'] == '210', 'S5', 180); s5 = 'change made during the split applied after heal'
    except AssertionError: s5 = 'not applied within 180 s of heal'
    back_at = now()
    c_after = change(240)
    try: wait(lambda: view()['value'] == '240', 'S5 next', 20); s5b = 'next change applied'
    except AssertionError: s5b = 'next change not applied within 20 s'
    invariants('S5')
    scenario('S5 operator split 15 s, change during split, no manual restart', split_at=t, submit_during=c, desired_gone_seen_at=desired_gone_at,
             submit_after_session_known_gone=during, healed_at=healed, outcome=s5, outcome_at=back_at, seconds_from_heal=round(back_at - healed, 3),
             submit_after=c_after, outcome_after=s5b,
             backend_session_events=[l.split(' - ')[-1][:160] for l in logtext('backend').splitlines() if 'RAM_SESSION' in l][-4:])

    # S6: the RAM memory restarted empty. Nothing else is restarted by hand.
    stop('ram'); start('ram', 'ram', [build / 'region-host', RAMADDR], stdin=True); port(RAM, 18884, 'ram'); t = now()
    probe.close(); probe = Probe()
    try: wait(lambda: probe.present()['capability'] and probe.present()['desired'] == ['240'], 'S6 rejoin', 60); r6 = 'device and operator back, truths reasserted'
    except AssertionError: r6 = 'not back within 60 s'
    rejoined = now(); after = probe.present()
    c = change(270)
    try: wait(lambda: view()['value'] == '270', 'S6', 25); s6 = 'next change applied'
    except AssertionError: s6 = 'not applied within 25 s'
    rec = invariants('S6')
    scenario('S6 RAM restarted empty, no manual restart of anything else', ram_restarted_at=t, rejoin=r6, rejoined_at=rejoined,
             seconds_to_rejoin=round(rejoined - t, 3), ram_after_rejoin=after, submit=c, outcome=s6, outcome_at=now(),
             device_ram_applications_this_boot=rec['device_ram_applications_this_boot'], ram_after=probe.present())
    # S7: a long device split (60 s), change during the split; measures rejoin time after a long outage
    boots0 = boots(); split('dev', RAM); t = now(); time.sleep(1.0); c = change(300)
    time.sleep(max(0.0, t + 60.0 - time.monotonic() + T0)); heal('dev'); healed = now()
    try: wait(lambda: view()['value'] == '300', 'S7', 240); s7 = 'applied by a fresh device session after heal'
    except AssertionError: s7 = 'not applied within 240 s of heal'
    back_at = now(); rec = invariants('S7')
    scenario('S7 device split 60 s, change during split, no manual restart', split_at=t, submit=c, healed_at=healed, outcome=s7,
             outcome_at=back_at, seconds_from_heal=round(back_at - healed, 3), device_process_starts=boots() - boots0,
             device_ram_applications_this_boot=rec['device_ram_applications_this_boot'])
    result['status'] = 'PASS'
except BaseException as error:
    result['status'] = 'FAIL'; result['error'] = str(error)[:2000]; raise
finally:
    if probe: probe.close()
    for name in list(processes)[::-1]: stop(name)
    for log in logs: log.close()
    net_down(); save(); print(json.dumps({'status': result.get('status'), 'error': result.get('error')}), flush=True)
