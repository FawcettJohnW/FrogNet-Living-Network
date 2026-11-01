#!/usr/bin/env python3
################################################################
#  Copyright (C) 2016-2026 Fawcett Innovations LLC             #
#  SPDX-License-Identifier: GPL-2.0-only                       #
################################################################
# test_fdf_setup_ui.py -- [FDF_SETUP_UI_V1] the program in front of fdf-setup, driven at a real terminal (a pty), as
# a manager would: it opens on fdf-setup's defaults, the region renamed, the RAM port changed, a level added, the
# admin's password typed; Apply hands fdf-setup --define exactly that. fdf-setup here is a stand-in that prints the
# real defaults and keeps the definition it is given, so nothing is made.   usage: tools/test_fdf_setup_ui.py
import json, os, pty, select, shutil, subprocess, sys, tempfile, time
H = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); F = os.path.join(H, "..", "fdf")
T = tempfile.mkdtemp(prefix="setup-ui-"); os.makedirs(T + "/bin"); os.makedirs(T + "/hub")
shutil.copy(os.path.join(F, "bin", "fdf-setup-ui"), T + "/bin/")
real = os.path.join(F, "bin", "fdf-setup")
open(T + "/bin/fdf-setup", "w").write('''#!/usr/bin/env python3
import json, subprocess, sys
if sys.argv[1:] == ["--defaults"]:
    print(subprocess.run([sys.executable, %r, "--defaults"], capture_output=True, text=True).stdout, end=""); sys.exit(0)
if sys.argv[1] == "--define":
    json.dump(json.load(open(sys.argv[2])), open(%r, "w")); print("fdf-setup: region defined (stand-in)"); sys.exit(0)
sys.exit(2)
''' % (real, T + "/given.json"))
env = dict(os.environ, FDF_BIN=T + "/bin", FDF_PY=os.path.join(F, "lib", "python"), FDF_HUB_ROOT=T + "/hub", TERM="xterm", LINES="40", COLUMNS="120")
pid, fd = pty.fork()
if pid == 0:
    os.execve(sys.executable, [sys.executable, T + "/bin/fdf-setup-ui"], env)
screen = b""
def drain(t=0.4):
    global screen
    end = time.time() + t
    while time.time() < end:
        r, _, _ = select.select([fd], [], [], 0.05)
        if r:
            try: screen += os.read(fd, 65536)
            except OSError: return
def keys(*ks):
    for k in ks:
        os.write(fd, k.encode() if isinstance(k, str) else k); drain(0.25)
DOWN = b"j"                                         # (the arrow keys are this program's too)
drain(1.5)
opened = b"Restricted" in screen and b"51888" in screen and b"Admin" in screen
keys("\r", "RangerTest\r")                         # the region's name
keys(DOWN, DOWN, "\r", "51890\r")                  # the RAM manager's port
keys(DOWN, DOWN, "a", "Hospitals\r", "n\r", "\r", "\r", "\r")   # a level: no password, reach ram, ports chosen
keys(DOWN, DOWN, DOWN, DOWN, DOWN, "\r", "\r", "\r", "pw-admin-1\r")   # the admin's password
keys(DOWN, "\r"); drain(1.0); keys(" "); drain(1.0)
st = None
for _ in range(50):
    p_, st_ = os.waitpid(pid, os.WNOHANG)
    if p_:
        st = st_; break
    drain(0.1)
if st is None:
    os.kill(pid, 9); os.waitpid(pid, 0); st = 256
    import re; print("the screen at the end:", re.sub(rb"\x1b\[[0-9;?]*[A-Za-z]", b" ", screen[-1500:]).decode(errors="replace"))
FAILS = []
def check(ok, what):
    print(("PASS  " if ok else "FAIL  ") + what, flush=True)
    if not ok: FAILS.append(what)
check(opened, "1 it opens on fdf-setup's defaults (Restricted, Admin, RAM port 51888)")
g = json.load(open(T + "/given.json")) if os.path.exists(T + "/given.json") else {}
lv = {l["name"]: l for l in g.get("levels", [])}
check(g.get("area") == "RangerTest" and g.get("ram_port") == 51890 and g.get("hub_port") == 50889,
      "2 Apply gave fdf-setup the region as changed: %s, RAM port %s, hub port %s" % (g.get("area"), g.get("ram_port"), g.get("hub_port")))
check(set(lv) == {"Restricted", "Admin", "Hospitals"} and lv["Hospitals"] == {"name": "Hospitals", "password": False, "reach": "ram"},
      "3 the levels: %s" % g.get("levels"))
check(g.get("users") == [{"level": "Admin", "user": "admin", "password": "pw-admin-1"}] and b"pw-admin-1" not in screen,
      "4 the admin's password went to fdf-setup and was never shown on the screen: %s" % [(u["user"], u["level"]) for u in g.get("users", [])])
check(os.WEXITSTATUS(st) == 0, "5 it ended after Apply (rc %d)" % os.WEXITSTATUS(st))
print("RESULT", "PASS" if not FAILS else "FAIL: %d" % len(FAILS))
sys.exit(1 if FAILS else 0)
