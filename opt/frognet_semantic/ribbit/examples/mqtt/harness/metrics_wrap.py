#!/usr/bin/env python3
# Copyright (C) 2016-2026 Fawcett Innovations LLC
# SPDX-License-Identifier: GPL-2.0-only
#
# metrics_wrap.py -- run ONE unmodified Mosquitto test script and measure it from the outside.
#
#   metrics_wrap.py <out.jsonl> <label> <test.py> [test args...]
#
# The test runs exactly as Mosquitto ships it (runpy, its own directory, its own argv). Nothing in the broker is
# touched: whichever broker BUILD_ROOT/src/mosquitto names -- stock Mosquitto or the Ribbit front -- is measured by
# this same code, so the two rows are comparable. One JSON line per run is appended to <out.jsonl>:
#   wall_s                 the test's wall time
#   rc                     the test's exit status (0 = pass)
#   tx_bytes, rx_bytes     bytes the test's own sockets sent / received (MQTT, websockets, TLS records)
#   tx_calls, rx_calls     socket send / recv calls
#   expects                packets the test waited for; expect_ms_sum / expect_ms_max: time spent waiting for them
#   broker_starts          brokers the test started
#   child_user_s, child_sys_s   CPU of every child the test reaped (the broker and all it started, any clients)
#   child_maxrss_kb        largest resident set among them
#   child_vcsw, child_ivcsw     voluntary / involuntary context switches among them
import json, os, resource, runpy, socket, subprocess, sys, time

out_path, label, test = sys.argv[1], sys.argv[2], sys.argv[3]
m = dict(label=label, test=os.path.basename(test), tx_bytes=0, rx_bytes=0, tx_calls=0, rx_calls=0,
         expects=0, expect_ms_sum=0.0, expect_ms_max=0.0, broker_starts=0)

_send, _sendall, _recv, _recv_into = socket.socket.send, socket.socket.sendall, socket.socket.recv, socket.socket.recv_into
def send(self, data, *a):
    n = _send(self, data, *a); m['tx_bytes'] += n; m['tx_calls'] += 1; return n
def sendall(self, data, *a):
    r = _sendall(self, data, *a); m['tx_bytes'] += len(data); m['tx_calls'] += 1; return r
def recv(self, n, *a):
    d = _recv(self, n, *a); m['rx_bytes'] += len(d); m['rx_calls'] += 1; return d
def recv_into(self, buf, *a):
    n = _recv_into(self, buf, *a); m['rx_bytes'] += n; m['rx_calls'] += 1; return n
socket.socket.send, socket.socket.sendall, socket.socket.recv, socket.socket.recv_into = send, sendall, recv, recv_into

_popen_init = subprocess.Popen.__init__
def popen_init(self, args, *a, **k):
    first = args[0] if isinstance(args, (list, tuple)) else str(args).split()[0]
    if str(first).endswith('src/mosquitto'):
        m['broker_starts'] += 1
    return _popen_init(self, args, *a, **k)
subprocess.Popen.__init__ = popen_init

test_dir = os.path.dirname(os.path.abspath(test))
sys.path.insert(0, test_dir)
sys.path.insert(0, os.path.dirname(test_dir))
import mosq_test                                    # the suite's own helper, wrapped once
_expect = mosq_test.expect_packet
def expect_packet(sock, name, expected):
    t = time.perf_counter()
    try:
        return _expect(sock, name, expected)
    finally:
        ms = (time.perf_counter() - t) * 1000.0
        m['expects'] += 1; m['expect_ms_sum'] += ms; m['expect_ms_max'] = max(m['expect_ms_max'], ms)
mosq_test.expect_packet = expect_packet

def finish(rc):
    m['wall_s'] = round(time.perf_counter() - t0, 4)
    m['rc'] = rc
    ru = resource.getrusage(resource.RUSAGE_CHILDREN)
    m.update(child_user_s=round(ru.ru_utime, 4), child_sys_s=round(ru.ru_stime, 4), child_maxrss_kb=ru.ru_maxrss,
             child_vcsw=ru.ru_nvcsw, child_ivcsw=ru.ru_nivcsw)
    m['expect_ms_sum'] = round(m['expect_ms_sum'], 3); m['expect_ms_max'] = round(m['expect_ms_max'], 3)
    with open(out_path, 'a') as f:
        f.write(json.dumps(m) + '\n')

os.chdir(test_dir)
sys.argv = [os.path.basename(test)] + sys.argv[4:]
t0 = time.perf_counter()
rc = 0
try:
    runpy.run_path(os.path.basename(test), run_name='__main__')
except SystemExit as e:
    rc = e.code if isinstance(e.code, int) else (0 if e.code is None else 1)
except BaseException as e:
    print(f"metrics_wrap: test raised {type(e).__name__}: {e}", file=sys.stderr)
    rc = 1
finally:
    sys.stdout.flush(); sys.stderr.flush()
finish(rc)
os._exit(rc)
