#!/usr/bin/env python3
"""Prove the node tarballs before they go to real machines: four "nodes", each its own network namespace
and its own /opt/senz (a private mount namespace with that node's untarred tarball bound on /opt/senz),
installed and started exactly as on a real node with `senz <role> ...`, then `senz test` run on the ops
node while one device node is "rebooted" (role stopped and its network cut for 20 s, then started again)
and another is cut off silently for 15 s.

  ops   10.88.0.10  x86_64 tarball: senz ops 10.88.0.20:18884, then senz test
  ram   10.88.0.20  x86_64 tarball: senz ram 0.0.0.0:18884
  devA  10.88.0.30  x86_64 tarball: senz device 10.88.0.20:18884 10.88.0.10:18883 devA
  devB  10.88.0.40  aarch64 tarball (run under qemu-user): senz device ... devB

Usage: prove_nodes.py --x86 TARBALL --arm TARBALL --work DIR [--arm-sysroot DIR]
"""
import argparse, os, subprocess, sys, threading, time, json, socket
from pathlib import Path
p = argparse.ArgumentParser(); p.add_argument('--x86', required=True); p.add_argument('--arm', required=True)
p.add_argument('--work', required=True, type=Path); p.add_argument('--arm-sysroot', default='')
p.add_argument('--ops-arch', choices=['x86_64', 'aarch64'], default='x86_64')
p.add_argument('--rounds', type=int, default=16); p.add_argument('--every', type=float, default=3.0)
a = p.parse_args()
W = a.work.resolve(); W.mkdir(parents=True, exist_ok=True)
NODES = {'ops': ('10.88.0.10', a.x86 if a.ops_arch == 'x86_64' else a.arm), 'ram': ('10.88.0.20', a.x86), 'devA': ('10.88.0.30', a.x86), 'devB': ('10.88.0.40', a.arm)}
log = (W / 'prove.log').open('a'); T0 = time.monotonic()
def say(t):
    line = f'{time.monotonic() - T0:8.2f}  {t}'; print(line, flush=True); log.write(line + '\n'); log.flush()
def sh(*c, check=True, **kw): return subprocess.run([str(x) for x in c], check=check, capture_output=True, text=True, **kw)
def ns(n): return 't88' + n

def net_down():
    for n in NODES: sh('ip', 'netns', 'del', ns(n), check=False)
    sh('ip', 'link', 'del', 'br88', check=False)
def net_up():
    net_down()
    sh('ip', 'link', 'add', 'br88', 'type', 'bridge'); sh('ip', 'addr', 'add', '10.88.0.1/24', 'dev', 'br88'); sh('ip', 'link', 'set', 'br88', 'up')
    for n, (ip, _) in NODES.items():
        sh('ip', 'netns', 'add', ns(n)); sh('ip', 'link', 'add', 'v88' + n, 'type', 'veth', 'peer', 'name', 'eth0', 'netns', ns(n))
        sh('ip', 'link', 'set', 'v88' + n, 'master', 'br88'); sh('ip', 'link', 'set', 'v88' + n, 'up')
        sh('ip', 'netns', 'exec', ns(n), 'ip', 'addr', 'add', ip + '/24', 'dev', 'eth0')
        sh('ip', 'netns', 'exec', ns(n), 'ip', 'link', 'set', 'eth0', 'up'); sh('ip', 'netns', 'exec', ns(n), 'ip', 'link', 'set', 'lo', 'up')
def cut(n):
    rules = ('add table inet cut\nadd chain inet cut in { type filter hook input priority 0; policy drop; }\n'
             'add chain inet cut out { type filter hook output priority 0; policy drop; }\n'
             'add rule inet cut in iif lo accept\nadd rule inet cut out oif lo accept\n')
    subprocess.run(['ip', 'netns', 'exec', ns(n), 'nft', '-f', '-'], input=rules, text=True, check=True)
def uncut(n): sh('ip', 'netns', 'exec', ns(n), 'nft', 'delete', 'table', 'inet', 'cut')

def install(n):
    root = W / 'nodes' / n; root.mkdir(parents=True, exist_ok=True)
    with open(NODES[n][1], 'rb') as tgz: subprocess.run(['tar', 'xzf', '-', '-C', str(root)], stdin=tgz, check=True)   # cd / && tar xvzf - < tgz
    return root / 'opt/senz'
def on_node(n, command, wait=True):
    """Run `command` on node n: its network namespace, its own /opt/senz."""
    senz = W / 'nodes' / n / 'opt/senz'
    env = dict(os.environ, SENZ_YES='1')
    if NODES[n][1] == a.arm and a.arm_sysroot: env['QEMU_LD_PREFIX'] = a.arm_sysroot
    script = f'mount --make-rprivate / && mount --bind {senz} /opt/senz && {command}'
    cmd = ['ip', 'netns', 'exec', ns(n), 'unshare', '-m', 'bash', '-c', script]
    if wait:
        r = subprocess.run(cmd, env=env, capture_output=True, text=True); say(f'[{n}] {command}\n' + (r.stdout + r.stderr).rstrip()); return r
    return subprocess.Popen(cmd, env=env, stdout=(W / f'{n}-test.out').open('w'), stderr=subprocess.STDOUT, text=True)
def stop_role(n, service):
    pidf = W / 'nodes' / n / 'opt/senz/var' / (service + '.pid')
    if pidf.exists():
        pid = pidf.read_text().strip(); sh('pkill', '-P', pid, check=False); sh('kill', pid, check=False); pidf.unlink()

try:
    os.makedirs('/opt/senz', exist_ok=True)
    net_up()
    for n in NODES: install(n); say(f'{n}: installed {Path(NODES[n][1]).name}')
    on_node('ram', '/opt/senz/bin/senz ram 0.0.0.0:18884')
    on_node('ops', '/opt/senz/bin/senz ops 10.88.0.20:18884')
    for n in ('devA', 'devB'): on_node(n, f'/opt/senz/bin/senz device 10.88.0.20:18884 10.88.0.10:18883 {n}')
    # setup wait (not a measurement): the backend's HTTP port in the ops namespace
    deadline = time.monotonic() + 900
    while time.monotonic() < deadline:
        if sh('ip', 'netns', 'exec', ns('ops'), 'bash', '-c', 'exec 3<>/dev/tcp/127.0.0.1/18080', check=False).returncode == 0: break
        time.sleep(1)
    else: raise SystemExit('backend did not open 18080 within 900 s; see nodes/ops/opt/senz/var/senz-backend.log')
    time.sleep(8)
    say('backend up; starting senz test on ops')
    test = on_node('ops', f'/opt/senz/bin/senz test --rounds {a.rounds} --every {a.every} --discover 6', wait=False)
    def disturb():
        time.sleep(6 + a.every * 3)
        say('devA: reboot begins (role stopped, network cut)'); stop_role('devA', 'senz-device'); cut('devA')
        time.sleep(20); uncut('devA'); on_node('devA', '/opt/senz/bin/senz device 10.88.0.20:18884 10.88.0.10:18883 devA'); say('devA: back up')
        time.sleep(a.every * 2)
        say('devB: silent cut for 15 s'); cut('devB'); time.sleep(15); uncut('devB'); say('devB: cut healed')
    threading.Thread(target=disturb, daemon=True).start()
    rc = test.wait(timeout=1800)
    out = (W / 'ops-test.out').read_text(); say('senz test output:\n' + out[-3500:])
    res = sorted((W / 'nodes/ops/opt/senz/var').glob('hwtest-*/result.json'))[-1]
    (W / 'result.json').write_text(res.read_text())
    say(f'senz test exit {rc}; result copied to {W / "result.json"}')
finally:
    for n, svc in (('devA', 'senz-device'), ('devB', 'senz-device'), ('ops', 'senz-backend'), ('ops', 'senz-broker'), ('ops', 'senz-mongo'), ('ram', 'senz-ram')):
        stop_role(n, svc)
    time.sleep(1); net_down()
