#!/usr/bin/env python3
"""test_the_memory_goes_and_comes_back.py BUILD_DIR -- [A_FAILED_STEP_DOES_NOT_END_THE_WINDOW_V1] John's Windows window,
27 Nov 2026: comms-ram on the node went away during a call and the window ended ("Qt has caught an exception thrown
from an event handler"). Two comms-app windows in a call; comms-ram and comms-media are killed and, 8 s later, started
again on the same ports. Checked: both windows are still running; each said the step that failed; each is back in the
new memory's room; and the two can make a call again."""
import os, re, subprocess, sys, tempfile, time
B = os.path.abspath(sys.argv[1]); HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(os.path.dirname(HERE))
T = tempfile.mkdtemp(prefix="memory-back-")
env = dict(os.environ, FROGCOMMS_LIB=B, FROGNET_GUID_FILE=os.path.join(T, "no-fnid"), QT_QPA_PLATFORM="offscreen")
sys.path.insert(0, os.path.join(ROOT, "python")); os.environ.update(FROGCOMMS_LIB=B)
from frogcomms.room import Room  # noqa: E402
RAM, MEDIA = 21700, 21720
procs, fail = [], 0


def check(ok, what):
    global fail
    print(("PASS  " if ok else "FAIL  ") + what, flush=True); fail += 0 if ok else 1


def servers():
    r = subprocess.Popen([os.path.join(B, "comms-ram"), "--listen", "127.0.0.1:%d" % RAM], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(0.8)
    m = subprocess.Popen([os.path.join(B, "comms-media"), "--listen", "127.0.0.1:%d" % MEDIA, "--ram", "127.0.0.1:%d" % RAM], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(0.8)
    return r, m


def window(name, *more):
    log = os.path.join(T, name + ".log")
    p = subprocess.Popen([os.path.join(B, "comms-app"), "--ram", "127.0.0.1:%d" % RAM, "--media", "127.0.0.1:%d" % MEDIA, "--name", name, "--no-audio",
                          "--synthetic", "320x240"] + list(more), stdout=open(log, "w"), stderr=subprocess.STDOUT, env=env)
    procs.append(p); return p, log


def who(wait_s, want):
    end = time.time() + wait_s; names = set()
    while time.time() < end:
        try:
            r = Room("127.0.0.1", RAM, "watcher%d" % int(time.time() * 10)); time.sleep(1.5)
            names = {m.name for m in r.roster()}; r.close()
        except Exception:
            names = set()
        if want <= names: break
        time.sleep(1)
    return names


try:
    r, m = servers()
    donna, dlog = window("Donna", "--auto-answer")
    john, jlog = window("John", "--script", "wait-for Donna;call Donna;stay 14;wait-idle;wait-for Donna;call Donna;stay 4;hang;quit")
    time.sleep(8)
    check("in call" in open(jlog).read() and "in call" in open(dlog).read(), "the two windows are in a call")
    r.kill(); m.kill(); r.wait(); m.wait()
    time.sleep(8)
    check(john.poll() is None and donna.poll() is None, "comms-ram and comms-media killed for 8 s during the call: both windows are still running"
          + ("" if john.poll() is None and donna.poll() is None else " (John exit %s, Donna exit %s)" % (john.poll(), donna.poll())))
    r, m = servers()
    names = who(40, {"John", "Donna"})
    check({"John", "Donna"} <= names, "the servers are started again: both windows are in the new memory's room (%s)" % sorted(n for n in names if not n.startswith("watcher")))
    try: john.wait(90)
    except subprocess.TimeoutExpired: pass
    jl, dl = open(jlog).read(), open(dlog).read()
    check(john.returncode == 0 and "[script] finished: every step done" in jl and jl.count("in call") >= 2,
          "after that John called Donna again and hung up (script exit %s, %d calls)%s" % (john.returncode, jl.count("in call"), "" if john.returncode == 0 else ": " + " | ".join(jl.splitlines()[-6:])))
    check("Qt has caught an exception" not in jl + dl, "no exception left an event handler in either window")
    print("   John's console, the lines about the loss: " + " | ".join(l for l in jl.splitlines() if "failed" in l or "after the call" in l or "left the call" in l)[:900])
finally:
    for p in procs + [r, m]:
        try: p.kill()
        except Exception: pass
print("RESULT %s the memory goes and comes back: %d fail" % ("FAIL" if fail else "PASS", fail))
sys.exit(1 if fail else 0)
