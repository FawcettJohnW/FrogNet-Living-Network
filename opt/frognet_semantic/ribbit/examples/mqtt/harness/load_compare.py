#!/usr/bin/env python3
# Copyright (C) 2016-2026 Fawcett Innovations LLC
# SPDX-License-Identifier: GPL-2.0-only
#
# load_compare.py -- the same load, by the same driver (mqtt-load), against each broker in turn.
#
#   load_compare.py --broker stock=/path/to/stock/src/mosquitto --broker ribbit=/path/to/front/src/mosquitto \
#                   [--scenarios all|name,name] [--seconds 10] [--repeat 1] [--out results.jsonl] [--env NAME=VALUE]
#                   [--broker-env label:NAME=VALUE]     (that broker only; repeatable; one binary, several shapes)
#                   [--deadline S]   (a run past it is dumped and stopped, [HANG_DUMP_V1]; default warmup + seconds + 120)
#                   [--broker-conf 'label:max_queued_messages 0']   (a config line for that broker; repeatable: the
#                    broker is then started with -c on a file holding its listener, allow_anonymous and these lines)
#
# Each broker is started fresh for each scenario (default config, one listener on a free port), loaded, and stopped.
# Besides what mqtt-load reports, the broker's CPU (user + system, the broker process and every process it started --
# for Ribbit, its region host) is read from /proc before and after the measured interval. One JSON line per run.
import argparse, json, os, signal, socket, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
SCENARIOS = {
    # name: mqtt-load options
    'qos0-4x4':        dict(pubs=4, subs=4, topics=4, qos=0),
    'qos1-4x4':        dict(pubs=4, subs=4, topics=4, qos=1),
    'qos2-4x4':        dict(pubs=4, subs=4, topics=4, qos=2),
    'qos1-1x1':        dict(pubs=1, subs=1, topics=1, qos=1),
    '1to16-qos0':       dict(pubs=1, subs=16, topics=1, qos=0, wildcard=1),    # one publisher, 16 subscribers
    '16to1-qos1':       dict(pubs=16, subs=1, topics=1, qos=1, wildcard=1),    # 16 publishers, one subscriber
    'many-qos1-32x32':  dict(pubs=32, subs=32, topics=32, qos=1),
    'latency-qos1-1k':  dict(pubs=1, subs=1, topics=1, qos=1, rate=1000),
    # many clients at once: what fan-out / fan-in on the wire to the region is for. 4 driver threads.
    'many-256':         dict(pubs=256, subs=256, topics=256, qos=1, threads=4),               # as fast as they go
    'many-1024':        dict(pubs=1024, subs=1024, topics=1024, qos=1, rate=50, threads=4),   # 51,200 msgs/s offered
    'crowd-4096':       dict(pubs=4096, subs=64, topics=64, qos=1, rate=10, threads=4),       # 40,960 msgs/s offered
}
# thousands of connections: the driver and every broker it starts inherit a raised open-files limit
try:
    import resource
    _soft, _hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    _want = 65536 if _hard == resource.RLIM_INFINITY else min(_hard, 65536)
    if _soft < _want:
        resource.setrlimit(resource.RLIMIT_NOFILE, (_want, _hard))
except (ImportError, ValueError, OSError) as _x:
    print(f'load_compare: could not raise the open-files limit: {_x}', file=sys.stderr)

def free_port():
    s = socket.socket(); s.bind(('127.0.0.1', 0)); p = s.getsockname()[1]; s.close(); return p

def tree(pid):
    out, todo = [], [pid]
    while todo:
        p = todo.pop(); out.append(p)
        try:
            for t in os.listdir(f'/proc/{p}/task'):
                try: todo += [int(x) for x in open(f'/proc/{p}/task/{t}/children').read().split()]
                except OSError: pass
        except OSError: pass
    return out

def cpu(pid):
    tick = os.sysconf('SC_CLK_TCK'); total = 0.0
    for p in tree(pid):
        try:
            f = open(f'/proc/{p}/stat').read().rsplit(')', 1)[1].split()
            total += (int(f[11]) + int(f[12])) / tick
        except OSError: pass
    return total

def rss_kb(pid):
    total = 0
    for p in tree(pid):
        try:
            for line in open(f'/proc/{p}/status'):
                if line.startswith('VmRSS:'): total += int(line.split()[1])
        except OSError: pass
    return total

# [HANG_DUMP_V1] A run that passes its deadline, or a broker that will not stop, is not left to hang the bench: its state
# is dumped (every thread of the broker, mqtt-ram and the driver: state, wait channel, current syscall, kernel stack;
# their TCP sockets with queue sizes; the broker's $SYS counters; the driver's own per-connection state via SIGUSR1),
# printed and kept in the row, and the bench goes on to the next run.
def proc_dump(pids, tag):
    out = []
    for p in pids:
        try: cmd = open(f'/proc/{p}/cmdline', 'rb').read().replace(b'\0', b' ').decode(errors='replace').strip()
        except OSError as x: out.append(f'{tag} pid {p}: gone ({x.__class__.__name__})'); continue
        out.append(f'{tag} pid {p}: {cmd[:160]}')
        try: tids = sorted(os.listdir(f'/proc/{p}/task'), key=int)
        except OSError: tids = []
        for t in tids:
            base = f'/proc/{p}/task/{t}'
            def rd(name):
                try: return open(f'{base}/{name}').read().strip()
                except OSError as x: return f'<{x.__class__.__name__}>'
            stat = rd('stat'); comm = rd('comm')
            state = stat.rsplit(')', 1)[1].split()[0] if ')' in stat else '?'
            stack = ' < '.join(l.split(']', 1)[-1].strip() for l in rd('stack').splitlines()[:12])
            out.append(f'{tag}   tid {t} {comm} state={state} wchan={rd("wchan")} syscall={rd("syscall").split(" ")[0]} stack: {stack}')
    return out

def sock_dump(pids, tag):
    # every TCP socket each process holds, from /proc: local and remote port, state, bytes queued to send and to read
    states = {'01': 'ESTAB', '02': 'SYN_SENT', '03': 'SYN_RECV', '04': 'FIN_WAIT1', '05': 'FIN_WAIT2', '06': 'TIME_WAIT',
              '07': 'CLOSE', '08': 'CLOSE_WAIT', '09': 'LAST_ACK', '0A': 'LISTEN', '0B': 'CLOSING'}
    table = {}
    for f in ('/proc/net/tcp', '/proc/net/tcp6'):
        try:
            for l in open(f).read().splitlines()[1:]:
                x = l.split(); txq, rxq = x[4].split(':')
                table[x[9]] = (int(x[1].rsplit(':', 1)[1], 16), int(x[2].rsplit(':', 1)[1], 16), states.get(x[3], x[3]), int(txq, 16), int(rxq, 16))
        except FileNotFoundError:
            continue                                                     # tcp6 exists only with IPv6; the brokers listen on 127.0.0.1
        except OSError as e:
            return [f'{tag} sock: {f}: {e}']
    out = []
    for p in pids:
        try: fds = os.listdir(f'/proc/{p}/fd')
        except OSError as e: out.append(f'{tag} sock pid {p}: {e.__class__.__name__}'); continue
        for fd in sorted(fds, key=int):
            try: link = os.readlink(f'/proc/{p}/fd/{fd}')
            except OSError: continue
            if link.startswith('socket:['):
                t = table.get(link[8:-1])
                if t: out.append(f'{tag} sock pid {p} fd {fd}: :{t[0]} -> :{t[1]} {t[2]} send_q={t[3]} recv_q={t[4]}')
    return out or [f'{tag} sock: no TCP sockets in these processes']

def sys_dump(port, tag):
    def s16(x): b = x.encode(); return len(b).to_bytes(2, 'big') + b
    def pkt(h, body):
        n, v = len(body), b''
        while True:
            d = n % 128; n //= 128; v += bytes([d | (128 if n else 0)])
            if not n: break
        return bytes([h]) + v + body
    got = {}
    try:
        s = socket.create_connection(('127.0.0.1', port), timeout=2)
        s.sendall(pkt(0x10, s16('MQTT') + bytes([4, 2]) + (60).to_bytes(2, 'big') + s16('hang-dump')))
        s.sendall(pkt(0x82, (1).to_bytes(2, 'big') + s16('$SYS/#') + b'\0'))
        buf, end = b'', time.time() + 2
        while time.time() < end:
            s.settimeout(max(0.05, end - time.time()))
            try: d = s.recv(65536)
            except socket.timeout: break
            if not d: break
            buf += d
            while len(buf) >= 2:
                n, mul, i = 0, 1, 1
                while i < len(buf):
                    c = buf[i]; n += (c & 127) * mul; mul *= 128; i += 1
                    if not c & 128: break
                else: break
                if len(buf) < i + n: break
                h, body = buf[0], buf[i:i + n]; buf = buf[i + n:]
                if h >> 4 == 3:
                    tl = int.from_bytes(body[:2], 'big'); got[body[2:2 + tl].decode(errors='replace')] = body[2 + tl:].decode(errors='replace')
        s.close()
    except OSError as x:
        return [f'{tag} $SYS: no answer from the broker ({x.__class__.__name__}: {x})']
    keep = [k for k in sorted(got) if '/ribbit/' in k or k.endswith(('clients/connected', 'messages/dropped', 'store/messages/count',
                                                                    'messages/inflight', 'publish/messages/received', 'publish/messages/sent'))]
    return [f'{tag} $SYS {k} = {got[k]}' for k in keep] or [f'{tag} $SYS: connected, nothing received in 2 s ({len(got)} topics)']

def hang_dump(label, sc, why, broker, driver, port):
    tag = f'[HANG_DUMP_V1] {label}:{sc}'
    out = [f'{tag} {why}']
    pids = tree(broker.pid) + (tree(driver.pid) if driver else [])
    out += proc_dump(pids, tag) + sock_dump(pids, tag) + sys_dump(port, tag)
    # user-space stacks too, where gdb is installed (every thread of the broker and of its region host)
    import shutil
    if shutil.which('gdb'):
        for p in tree(broker.pid):
            try: r = subprocess.run(['gdb', '-p', str(p), '-batch', '-ex', 'thread apply all bt 12'], capture_output=True, text=True, timeout=30)
            except (OSError, subprocess.TimeoutExpired) as x: out.append(f'{tag} gdb {p}: {x}'); continue
            out += [f'{tag} gdb {p} {l}' for l in r.stdout.splitlines() if l.startswith(('Thread', '#'))]
    else:
        out.append(f'{tag} gdb: not installed here, user-space stacks not taken')
    if driver and driver.poll() is None:
        try: driver.send_signal(signal.SIGUSR1)
        except OSError: pass
    return out

ap = argparse.ArgumentParser()
ap.add_argument('--broker', action='append', required=True)
ap.add_argument('--scenarios', default='all'); ap.add_argument('--seconds', type=float, default=10)
ap.add_argument('--warmup', type=float, default=1); ap.add_argument('--repeat', type=int, default=1)
ap.add_argument('--threads', type=int, default=2); ap.add_argument('--out', default=None)
ap.add_argument('--env', action='append', default=[])
ap.add_argument('--broker-env', action='append', default=[])
ap.add_argument('--broker-conf', action='append', default=[])
ap.add_argument('--deadline', type=float, default=0)   # [HANG_DUMP_V1] seconds per run; 0: warmup + seconds + 120
a = ap.parse_args()
names = list(SCENARIOS) if a.scenarios == 'all' else a.scenarios.split(',')
load = os.path.join(HERE, 'mqtt-load')
env = dict(os.environ)
for e in a.env:
    k, v = e.split('=', 1); env[k] = v

for rep in range(a.repeat):
    for sc in names:
        for spec in a.broker:
            label, exe = spec.split('=', 1)
            port = free_port()
            benv = dict(env)
            for e in a.broker_env:
                bl, kv = e.split(':', 1)
                if bl == label:
                    k, v = kv.split('=', 1); benv[k] = v
            lines = [e.split(':', 1)[1] for e in a.broker_conf if e.split(':', 1)[0] == label]
            if lines:
                import tempfile
                cf = tempfile.NamedTemporaryFile('w', suffix='.conf', delete=False)
                cf.write(f'listener {port} 127.0.0.1\nallow_anonymous true\n' + ''.join(l + '\n' for l in lines)); cf.close()
                os.chmod(cf.name, 0o644)
                cmd = [exe, '-c', cf.name]
            else:
                cmd = [exe, '-p', str(port)]
            # [RUN_LOGS_KEPT_V1] the broker's and its mqtt-ram's output, every line, one file per run, in <--out without .jsonl>-logs/
            logdir = (os.path.splitext(os.path.abspath(a.out))[0] if a.out else os.path.abspath('load')) + '-logs'
            os.makedirs(logdir, exist_ok=True)
            logpath = os.path.join(logdir, f'r{rep + 1}-{sc}-{label}.log')
            blog = open(logpath, 'w')
            b = subprocess.Popen(cmd, stdout=blog, stderr=subprocess.STDOUT, env=benv)
            listening = False
            for _ in range(200):
                try: socket.create_connection(('127.0.0.1', port), timeout=0.2).close(); listening = True; break
                except OSError: time.sleep(0.05)
            opts = dict(SCENARIOS[sc]); args = [load, '--port', str(port), '--seconds', str(a.seconds), '--warmup', str(a.warmup),
                                                '--label', f'{label}:{sc}']
            opts.setdefault('threads', a.threads)
            for k, v in opts.items(): args += [f'--{k}', str(v)]
            # [HANG_DUMP_V1] the driver's own limits: 60 s to get every client ready, the window, 30 s of drain at most
            deadline = a.deadline or a.warmup + a.seconds + 60 + 30 + 30
            dump, flags = [], {}
            if not listening:
                flags['broker_not_listening'] = True
                dump += hang_dump(label, sc, f'broker not accepting on port {port} after 10 s', b, None, port)
            c0 = cpu(b.pid); t0 = time.time()
            d = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                dout, derr = d.communicate(timeout=deadline)
            except subprocess.TimeoutExpired:
                flags['hung'] = True
                dump += hang_dump(label, sc, f'run passed its {deadline:.0f} s deadline', b, d, port)
                time.sleep(2)                                            # the driver writes its SIGUSR1 state lines
                d.kill(); dout, derr = d.communicate()
                mine = [l for l in (derr or '').splitlines() if 'HANG_DUMP_V1' in l]
                dump += mine or [f'[HANG_DUMP_V1] {label}:{sc} driver wrote no state in 2 s after SIGUSR1 (its threads are above)']
            c1 = cpu(b.pid); t1 = time.time(); rss = rss_kb(b.pid)
            kids = tree(b.pid)[1:]                                       # mqtt-ram, in the own shape
            b.send_signal(signal.SIGTERM)
            try: b.wait(10)
            except subprocess.TimeoutExpired:
                flags['broker_stuck'] = True
                dump += hang_dump(label, sc, 'broker did not exit 10 s after SIGTERM', b, None, port)
                b.kill(); b.wait()
            time.sleep(0.2)
            def alive(p):
                try: return open(f'/proc/{p}/stat').read().rsplit(')', 1)[1].split()[0] != 'Z'
                except OSError: return False
            left = [p for p in kids if alive(p)]
            if left:                                                     # a region host outliving its broker is a defect: record it
                flags['left_behind'] = True
                dump += proc_dump(left, f'[HANG_DUMP_V1] {label}:{sc} left running after the broker exited:')
                for p in left:
                    try: os.kill(p, signal.SIGKILL)
                    except OSError: pass
            blog.close()
            for l in dump: print(l, flush=True)
            try: row = json.loads(dout.strip().splitlines()[-1])
            except Exception: row = {'label': f'{label}:{sc}', 'error': ((dout or '') + (derr or ''))[-500:]}
            row.update(flags)
            if dump: row['hang_dump'] = dump
            row.update(broker=label, scenario=sc, repeat=rep, broker_cpu_s=round(c1 - c0, 3), wall_s=round(t1 - t0, 3),
                       broker_cpu_per_1k_msgs_ms=round(1000 * 1000 * (c1 - c0) / max(1, row.get('received', 0) + row.get('sent', 0)), 4),
                       broker_rss_kb=rss, broker_log=logpath)   # [RUN_LOGS_KEPT_V1]
            line = json.dumps(row); print(line, flush=True)
            if a.out:
                with open(a.out, 'a') as f: f.write(line + '\n')
