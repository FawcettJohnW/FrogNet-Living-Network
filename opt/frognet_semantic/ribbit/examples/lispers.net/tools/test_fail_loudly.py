#!/usr/bin/env python3
"""[FAIL_ONCE_LOUDLY_V1] When the RAM host goes away, ribbit-lisp says so ONCE -- naming the endpoint and the cause --
and exits with status 3. It must not abort (SIGABRT), and it must not keep answering from views that stopped.
usage: tools/test_fail_loudly.py RAM_SERVER_BIN PORT"""
import json, subprocess, sys, time
srv_bin, port = sys.argv[1], sys.argv[2]
srv = subprocess.Popen([srv_bin, "--listen", "127.0.0.1:" + port], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
time.sleep(0.5)
p = subprocess.Popen(["./ribbit_cpp/ribbit-lisp", "--ram", "127.0.0.1", port], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
def call(op, **a):
    p.stdin.write(json.dumps({"operation": op, "args": a}) + "\n"); p.stdin.flush(); return p.stdout.readline()
ok = json.loads(call("site.add", iid="0", prefix="198.18.0.0/16", group="", accept_more_specifics=True)).get("ok")
call("resolver.wait_site", iid="0", prefix="198.18.0.0/16", group="", active=True)
srv.kill(); srv.wait(); time.sleep(0.5)
answers = []
for k in range(3):
    try: line = call("registration.put", iid="0", prefix="198.18.%d.0/24" % k, group="", rloc_set=["192.0.2.1"])
    except BrokenPipeError: break
    if not line: break
    answers.append(json.loads(line))
try: rc = p.wait(timeout=10)
except subprocess.TimeoutExpired: p.kill(); rc = "still running"
err = p.stderr.read()
fails = []
if not ok: fails.append("site.add before the RAM host went away did not succeed")
if rc != 3: fails.append("exit status %r, expected 3 (-6 is SIGABRT)" % rc)
if len(answers) != 1: fails.append("%d answers after the RAM host went away, expected exactly one" % len(answers))
elif answers[0].get("ok") or "unreachable" not in answers[0].get("error", "") or ("127.0.0.1:" + port) not in answers[0].get("error", ""):
    fails.append("the one answer does not name the endpoint and say unreachable: %r" % answers[0])
if "terminate called" in err: fails.append("std::terminate on stderr")
for f in fails: print("  FAIL " + f)
print("%s: %s" % ("FAIL-LOUDLY PASS" if not fails else "FAIL-LOUDLY FAIL", answers[0].get("error", "")[:150] if answers else "no answer"))
sys.exit(1 if fails else 0)
