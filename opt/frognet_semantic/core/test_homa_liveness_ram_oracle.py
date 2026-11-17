#!/usr/bin/env python3
################################################################
#  Copyright (C) 2016-2026 Fawcett Innovations LLC             #
#  SPDX-License-Identifier: GPL-2.0-only                       #
#  See COPYRIGHT and LICENSE at the root of this tree.         #
################################################################
"""
test_homa_liveness_ram_oracle.py -- [LIVENESS_IS_A_HELD_READ_V1] core/homa_liveness on the REAL memory.

Nothing is stood in for: the tree's api.php and held_fire.php served on port 80 over MariaDB, core/frognet_tuples
writing the tuples, core/frognet_held holding the reads and taking the fires, a hosts file rewritten the way the merge
rewrites it. Three nodes are three processes on three addresses of this machine (10.61.61.1 is also the ControlRam).

What it holds core/homa_liveness.PeerLiveness to:

  1  each end writes its own tuple and reads the others' (and a hold on "can.%", without the stored name's SD:
     prefix, reads nothing: the pattern must carry it)
  2  an end that stops says so in the memory: its peers know it is gone from one fire
  3  an end that is killed and started again is known gone by its inst, and there again
  4  a machine that is killed and leaves the hosts block is known gone from the file watcher's event alone, while
     its tuple is still in the memory; a machine still in the block is not touched; and the call the daemon's driver
     then makes (remove_writer, [WRITER_GONE_V1]) takes the machine's tuples out of the real memory
  5  the RAM is rebuilt and the hosts event comes: every living end writes its own again, each is told of one
     network event, and nobody calls a living peer gone
  6  the daemon end watches proxies the same way

Needs root, php (mysqli, curl), a MariaDB server and port 80 free; says SKIP and exits 0 otherwise.
    python3 core/test_homa_liveness_ram_oracle.py
"""
import json
import os
import queue
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SEM = os.path.dirname(HERE)
ROOT = os.path.dirname(os.path.dirname(SEM))
WEB = os.path.join(ROOT, "var", "www", "html")
DBNAME = "FrogNetHomaOracle"
A, B, C = "10.61.61.1", "10.62.62.1", "10.63.63.1"
RAM = A


# ------------------------------------------------------------------------------------------------- one node (child)
def node(ip, role, port):
    sys.path.insert(0, SEM)
    from core import frognet_tuples as T, homa_liveness as L
    T.my_ip = lambda: ip
    sys.path.append(HERE)
    import frognet_tuples as T2                   # the module object frognet_held uses
    T2.my_ip = lambda: ip

    out = threading.Lock()                        # liveness has two threads (the hold and the file watcher):
                                                  # each event is one whole line

    def say(**k):
        with out:
            sys.stdout.write("EV " + json.dumps(k) + "\n"); sys.stdout.flush()

    lv = L.PeerLiveness(role, port, lambda p, why, left: say(ev="gone", ip=p, why=why, machine_left=left), "%s %s" % (ip, role),
                        on_network_event=lambda: say(ev="netevent"), dbhost=RAM)
    _read = lv._read

    def read(rows):
        _read(rows)
        say(ev="read", there=sorted(lv.seen), block=sorted(lv.block or ()))
    lv._read = read
    say(ev="up", inst=lv.inst)
    for line in sys.stdin:
        if line.strip() == "stop":
            break
    sys.exit(0)                                   # a clean stop: atexit withdraws the tuple, then removes it


# ------------------------------------------------------------------------------------------------------ controller
class Node:
    def __init__(self, ip, role, port, env):
        self.ip, self.role, self.q = ip, role, queue.Queue()
        self.p = subprocess.Popen([sys.executable, os.path.abspath(__file__), "node", ip, role, str(port)],
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env)
        self.log = []
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self):
        for line in self.p.stdout:
            self.log.append(line.rstrip())
            i = line.find("EV {")                 # the node's own log lines share its stdout
            if i >= 0:
                self.q.put(json.loads(line[i + 3:]))

    def until(self, pred, wait_s=10.0):
        """Events up to and including the first that satisfies pred; None if it does not come."""
        got, end = [], time.time() + wait_s
        while time.time() < end:
            try:
                e = self.q.get(timeout=max(0.01, end - time.time()))
            except queue.Empty:
                break
            got.append(e)
            if pred(e):
                return got
        return None

    def drain(self, quiet_s=0.6):
        got = []
        while True:
            try:
                got.append(self.q.get(timeout=quiet_s))
            except queue.Empty:
                return got

    def stop(self):
        self.p.stdin.write("stop\n"); self.p.stdin.flush(); self.p.wait(15)

    def kill(self):
        self.p.send_signal(signal.SIGKILL); self.p.wait(5)


def sh(cmd, inp=None):
    return subprocess.run(cmd, input=inp, capture_output=True, text=True)


def main():
    ok = True

    def check(cond, msg):
        nonlocal ok
        ok = ok and bool(cond)
        print(f"  [{'PASS' if cond else 'FAIL'}] {msg}", flush=True)

    cfg = os.environ.get("FROGNET_DB_CONFIG", "/root/.frogsim/DB_CONFIG.json")
    mods = sh(["php", "-m"]).stdout.lower().split() if shutil.which("php") else []
    if os.geteuid() != 0 or "mysqli" not in mods or "curl" not in mods or not shutil.which("mariadb") or not os.path.exists(cfg):
        print("SKIP: needs root, php with mysqli and curl, mariadb, and %s" % cfg); return 0
    if sh(["mariadb", "-uroot", "-e", "SELECT 1"]).returncode != 0:
        print("SKIP: no MariaDB server answering root on its socket"); return 0
    import socket
    s = socket.socket()
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)   # as php -S binds: a closed test connection does not count
    try:
        s.bind(("0.0.0.0", 80))
    except OSError as e:
        print("SKIP: port 80 is in use here (%s); the held read fires to the holder's port 80" % e); return 0
    finally:
        s.close()
    dbc = json.load(open(cfg))

    tmp = tempfile.mkdtemp(prefix="homa-liveness-ram-")
    web = os.path.join(tmp, "html"); os.mkdir(web)
    for f in os.listdir(WEB):
        if f.endswith(".php"):
            shutil.copy(os.path.join(WEB, f), web)
    c = open(os.path.join(web, "config.php")).read()
    c = c.replace("__FROGNET_DB_PASS__", dbc["password"]).replace("define('DB_NAME', 'FrogNet')", "define('DB_NAME', '%s')" % DBNAME)
    c = c.replace("define('DB_USER', 'FrogUser')", "define('DB_USER', '%s')" % dbc["user"])
    open(os.path.join(web, "config.php"), "w").write(c)
    sql = open(os.path.join(WEB, "Create_Database.sql")).read().replace("`FrogNet`", "`%s`" % DBNAME)
    r1 = sh(["mariadb", "-uroot"], sql)
    r2 = sh(["mariadb", "-uroot"], open(os.path.join(WEB, "schema_fixups.sql")).read().replace("USE FrogNet;", "USE %s;" % DBNAME))
    users = sh(["mariadb", "-uroot", "-N", "-e", "SELECT CONCAT(\"'\",user,\"'@'\",host,\"'\") FROM mysql.user WHERE user='%s'" % dbc["user"]]).stdout.split()
    for u in users:
        sh(["mariadb", "-uroot", "-e", "GRANT ALL ON %s.* TO %s" % (DBNAME, u)])
    if r1.returncode or r2.returncode or not users:
        print("SKIP: could not make database %s: %s %s" % (DBNAME, r1.stderr[-200:], r2.stderr[-200:])); return 0
    for ip in (A, B, C):
        if (ip + "/") not in sh(["ip", "-4", "addr", "show", "lo"]).stdout:
            sh(["ip", "addr", "add", ip + "/32", "dev", "lo"])
    php = subprocess.Popen(["php", "-S", "0.0.0.0:80", "-t", web], env=dict(os.environ, PHP_CLI_SERVER_WORKERS="8"),
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    hosts = os.path.join(tmp, "etc", "hosts"); os.mkdir(os.path.dirname(hosts))

    def write_hosts(ips):                          # as the merge does: the whole file, moved into place
        with open(hosts + ".new", "w") as f:
            f.write("127.0.0.1 localhost\n" + "".join("%s FrogNetHost.N%s\n" % (ip, ip.split(".")[1]) for ip in ips))
        os.replace(hosts + ".new", hosts)
    write_hosts([A, B, C])
    env = dict(os.environ, FROGNET_HOSTS=hosts, FROGNET_HELD_DIR="/run/frognet/held")
    time.sleep(1.5)
    nodes = []

    def up(ip, role, port):
        n = Node(ip, role, port, env); nodes.append(n)
        return n

    def my(sqltext):
        return sh(["mariadb", "-uroot", "-N", DBNAME, "-e", sqltext]).stdout.strip()
    try:
        # 1
        a = up(A, "proxy", 0); b = up(B, "daemon", 9009); c_ = up(C, "daemon", 9009)
        got = a.until(lambda e: e["ev"] == "read" and e["there"] == [B, C])
        check(got is not None, f"1 the proxy at A reads both daemons' tuples from the real api.php ({got[-1] if got else a.log[-6:]})")
        gb = b.until(lambda e: e["ev"] == "read" and e["there"] == [A])
        check(gb is not None, "1 the daemon at B reads A's proxy tuple, and not the other daemon's as a peer of its own")
        names = sorted(my("SELECT SensorName FROM Sensor WHERE SensorType='homa'").split())
        check(names == sorted("SD:can.%s:%s" % x for x in ((A, "proxy"), (B, "daemon"), (C, "daemon"))), f"1 one tuple per end, each written by its owner ({names})")
        sys.path.insert(0, SEM); sys.path.append(HERE)
        os.environ["FROGNET_HOSTS"] = hosts
        import frognet_tuples as T2, frognet_held as FH
        T2.my_ip = lambda: A
        bare = FH.holder().hold("homa", RAM, name_like="can.%")[1]
        full = FH.holder().hold("homa", RAM, name_like="SD:can.%")[1]
        check(len(bare) == 0 and len(full) == 3, f"1 a hold on 'can.%' reads {len(bare)} rows and on 'SD:can.%' reads {len(full)}: the pattern carries the stored name's prefix")
        for n in (a, b, c_):
            n.drain()

        # 2
        t0 = time.time(); c_.stop()
        got = a.until(lambda e: e["ev"] == "gone")
        dt = (time.time() - t0) * 1000
        check(got is not None and got[-1]["ip"] == C and "no longer in the memory" in got[-1]["why"], f"2 the daemon at C stopped: A knows it is gone in {dt:.0f} ms from the stop, by one fire ({got[-1]['why'] if got else None})")
        check(my("SELECT COUNT(*) FROM Sensor WHERE SensorName='SD:can.%s:daemon'" % C) == "0", "2 and its tuple is out of the memory (removed by its own process at stop)")
        a.drain()

        # 3
        c_ = up(C, "daemon", 9009)
        got = a.until(lambda e: e["ev"] == "read" and C in e["there"])
        check(got is not None and not [e for e in got if e["ev"] == "gone"], "3 C's daemon started again: A reads it there, nobody called gone")
        a.drain(); c_.drain()
        c_.kill()
        c_ = up(C, "daemon", 9009)
        got = a.until(lambda e: e["ev"] == "read" and C in e["there"])
        gone = [e for e in (got or []) if e["ev"] == "gone"]
        check(got is not None and len(gone) == 1 and gone[0]["ip"] == C and "another started" in gone[0]["why"], f"3 C's daemon was killed and started again: A calls the old one gone once, by its inst, and reads the new one there ({gone[0]['why'] if gone else None})")
        for n in (a, b, c_):
            n.drain()

        # 4
        b.kill()
        quiet = a.drain(1.0)
        check(not [e for e in quiet if e["ev"] == "gone"] and my("SELECT COUNT(*) FROM Sensor WHERE SensorName='SD:can.%s:daemon'" % B) == "1",
              "4 B was killed: its tuple is still in the memory and nothing fires (no TCP close to remove it, no timeout to guess with)")
        t0 = time.time(); write_hosts([A, C])
        got = a.until(lambda e: e["ev"] == "gone")
        dt = (time.time() - t0) * 1000
        check(got is not None and got[-1]["ip"] == B and "left the network" in got[-1]["why"], f"4 the merge rewrote the hosts block without B: A knows B is gone in {dt:.0f} ms from the hosts event ({got[-1]['why'] if got else a.log[-5:]})")
        rest = a.drain(1.0)
        check(not [e for e in rest if e["ev"] == "gone"], "4 and C, still in the block, was not touched")
        c_.drain()
        write_hosts([A, C])                        # another event while B's tuple is still in the memory
        got = a.until(lambda e: e["ev"] == "read")
        check(got is not None and got[-1]["there"] == [C] and my("SELECT COUNT(*) FROM Sensor WHERE SensorName='SD:can.%s:daemon'" % B) == "1",
              f"4 B's tuple is still in the memory and A does not read B back as there ({got[-1]['there'] if got else None})")
        a.drain(); c_.drain()
        # the driver removes the closed connection: the call the daemon's driver makes when told a machine left
        os.environ["FROGNET_API_LOCAL"] = "http://%s/api.php" % RAM
        from daemon.engine import server as DS
        DS._writer_gone(B)
        after = a.drain(1.0) + c_.drain(0.2)
        check(my("SELECT COUNT(*) FROM Sensor WHERE SensorAddress LIKE '10.62.62.%'") == "0" and not [e for e in after if e["ev"] == "gone"],
              "4 the driver's remove_writer for B's /24 took B's tuples out of the memory (api.php, [WRITER_GONE_V1]); the holds it fired called nobody else gone")

        # 5
        my("DELETE FROM Sensor; DELETE FROM HeldRead;")
        write_hosts([A, C])
        ea, ec = a.until(lambda e: e["ev"] == "read" and e["there"] == [C], 15), c_.until(lambda e: e["ev"] == "read" and e["there"] == [A], 15)
        ea = (ea or []) + a.drain(1.0); ec = (ec or []) + c_.drain(1.0)
        names = sorted(my("SELECT SensorName FROM Sensor WHERE SensorType='homa'").split())
        check(names == ["SD:can.%s:proxy" % A, "SD:can.%s:daemon" % C] or names == sorted(["SD:can.%s:proxy" % A, "SD:can.%s:daemon" % C]),
              f"5 the RAM was rebuilt and the hosts event came: each living end wrote its own again; the departed one is absent ({names})")
        check([e["ev"] for e in ea].count("netevent") == 1 and [e["ev"] for e in ec].count("netevent") == 1, "5 each was told of ONE network event")
        check(not [e for e in ea + ec if e["ev"] == "gone"], "5 and nobody called a living peer gone on it")
        check(ea and ea[-1]["ev"] == "read" and ea[-1]["there"] == [C] if [e for e in ea if e["ev"] == "read"] else False, f"5 A reads C there again from the memory")

        # 6
        t0 = time.time(); a.stop()
        got = c_.until(lambda e: e["ev"] == "gone")
        dt = (time.time() - t0) * 1000
        check(got is not None and got[-1]["ip"] == A, f"6 the proxy at A stopped: the daemon at C knows it is gone in {dt:.0f} ms ({got[-1]['why'] if got else None})")
    finally:
        for n in nodes:
            if n.p.poll() is None:
                n.p.kill()
        php.kill()
        sh(["mariadb", "-uroot", "-e", "DROP DATABASE IF EXISTS %s" % DBNAME])
        if not ok:
            for n in nodes:
                print("---- %s %s" % (n.ip, n.role)); print("\n".join(n.log[-25:]))
        shutil.rmtree(tmp, ignore_errors=True)
    print()
    print("ALL HOMA-LIVENESS-RAM ORACLE CHECKPOINTS PASS" if ok else "HOMA-LIVENESS-RAM ORACLE FAILED", flush=True)
    return 0 if ok else 1


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "node":
        node(sys.argv[2], sys.argv[3], int(sys.argv[4]))
    else:
        rc = main(); sys.stdout.flush(); os._exit(rc)
