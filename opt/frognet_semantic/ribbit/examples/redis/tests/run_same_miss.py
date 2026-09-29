#!/usr/bin/env python3
"""[SAME_MISS_FROM_CACHE_V1] oracle: a reply the host answers RESP_SAME for, whose id the client no longer holds, must
come back from the host's cache -- the request must NOT run again. Redis's own commandstats counts executions in
the region. Usage: run_same_miss.py <front-binary> <port>"""
import subprocess, socket, sys, time, re
FRONT, PORT = sys.argv[1], int(sys.argv[2])
f = subprocess.Popen([FRONT, "--port", str(PORT), "--save", ""], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
t0 = time.time()
while True:
    try: s = socket.create_connection(("127.0.0.1", PORT), timeout=1); break
    except OSError:
        if time.time() - t0 > 10: sys.exit("front did not start")
s.settimeout(10)
def cmd(*a):
    s.sendall(b"*%d\r\n" % len(a) + b"".join(b"$%d\r\n%s\r\n" % (len(x), x) for x in a))
    r = b""
    while True:
        r += s.recv(1 << 20)
        if r[:1] == b"$":
            n = int(r[1:r.index(b"\r\n")])
            if n < 0 or len(r) >= r.index(b"\r\n") + 2 + n + 2: return r
        elif r.endswith(b"\r\n"): return r
cmd(b"CONFIG", b"RESETSTAT")
first = cmd(b"SET", b"k", b"v")
for i in range(300): cmd(b"SET", b"other%d" % i, b"v")      # 300 distinct requests: the client's 256 SAME ids roll over
again = cmd(b"SET", b"k", b"v")                              # the identical request: the host holds it, answers SAME
stats = cmd(b"INFO", b"commandstats").decode()
calls = int(re.search(r"cmdstat_set:calls=(\d+)", stats).group(1))
fails = 0
def check(n, ok, why=""):
    global fails; print(("PASS " if ok else "FAIL ") + n + (" -- " + why if why else "")); fails += 0 if ok else 1
check("M1 the repeated request is answered (+OK)", again == b"+OK\r\n", repr(again))
check("M2 it was executed once, not re-run on a SAME the client could not resolve: SET calls == 302", calls == 302, "calls=%d" % calls)
f.kill()
print("RESULT", "GREEN" if fails == 0 else "RED", fails); sys.exit(1 if fails else 0)
