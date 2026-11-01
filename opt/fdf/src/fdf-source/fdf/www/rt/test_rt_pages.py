#!/usr/bin/env python3
################################################################
#  Copyright (C) 2016-2026 Fawcett Innovations LLC             #
#  SPDX-License-Identifier: GPL-2.0-only                       #
################################################################
# test_rt_pages.py -- [RT_PAGES_V1] the radio log app's node-served pages against a real RAM manager, driven in a real browser (Chromium, through
# Playwright): two posts (two web servers, each a node's ram.php, both on the region's RAM) and three devices -- the
# scribe at post A (station A), the scribe at post B (station B), a field phone on post A's Wi-Fi (station F).
#
#   python3 test_rt_pages.py RINAB_BUILD_STATIC    (rina-host and ram from it; ram.php from ../dc beside this directory)
#
# Checks, each with what it saw:
#   1  a mission started at A is on B's list; B opens it
#   2  an entry typed at A is on B's Radio Log (time from A's "Log it" to the row at B)
#   3  the phone logs one; it is on A and B, numbered as the phone's station (F001), no packet, no merge
#   4  the message numbers: A001.., B001.., F001 -- one count per device, printed as on the forms
#   5  two scribes logging at once, 25 each: all 50 on both screens, every number distinct (no clash by construction)
#   6  B reloads: every entry is back from the RAM (nothing kept in the browser but its own id and station)
import json, os, shutil, socket, subprocess, sys, tempfile, time
from playwright.sync_api import sync_playwright

B = sys.argv[1]
HERE = os.path.dirname(os.path.abspath(__file__)); DC = os.path.join(HERE, '..', 'dc')
FAILS = []
def check(ok, what):
    print(("PASS  " if ok else "FAIL  ") + what, flush=True)
    if not ok: FAILS.append(what)

def free():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p

T = tempfile.mkdtemp(prefix="rt-pages-")
RAMP = free(); RAM = "127.0.0.1:%d" % RAMP
os.makedirs(T + "/bin"); os.makedirs(T + "/www/dc"); shutil.copytree(HERE, T + "/www/rt", ignore=shutil.ignore_patterns("*.py", "__pycache__"))
for b in ("ram", "rina-host"): shutil.copy(os.path.join(B, b), T + "/bin/")
shutil.copy(os.path.join(DC, "ram.php"), T + "/www/dc/")
json.dump({"apps": [{"id": "rangertrak", "title": "Radio log", "version": "1", "page": "../rt/log.html",
                     "services": ["rt-mission", "rt-log", "rt-handled"]}]}, open(T + "/www/dc/apps.json", "w"))
open(T + "/fdf.env", "w").write("export FDF_BIN=%s/bin RINA_RAM=%s\n" % (T, RAM))
procs = []
procs.append(subprocess.Popen([T + "/bin/rina-host", "--listen", RAM, "--api", "/FawcettInnovations.RINA.ram_interface.php"],
                              env=dict(os.environ, RINA_REGION_FILE=T + "/area.perm", RINA_NET_CONFIG=T + "/none"),
                              stdout=subprocess.DEVNULL, stderr=open(T + "/host.log", "w")))
posts = {}
for name in ("A", "B"):
    p = free(); posts[name] = "http://127.0.0.1:%d/rt/log.html" % p
    # PHP_CLI_SERVER_WORKERS: the built-in server is one process otherwise, and a held read would hold every page
    procs.append(subprocess.Popen(["php", "-S", "127.0.0.1:%d" % p, "-t", T + "/www"], env=dict(os.environ, PHP_CLI_SERVER_WORKERS="16"), stdout=subprocess.DEVNULL, stderr=open(T + "/php-%s.log" % name, "w")))
time.sleep(1.0)
try:
    with sync_playwright() as pw:
        br = pw.chromium.launch(args=["--no-sandbox"])
        def device(post, name, station):
            ctx = br.new_context(); pg = ctx.new_page(); pg.goto(posts[post])
            pg.fill("#dev input[name=name]", name); pg.fill("#dev input[name=station]", station); pg.click("#saveDev")
            return pg
        a, b, ph = device("A", "scribe-a", "A"), device("B", "scribe-b", "B"), device("A", "phone", "F")
        def rows(pg): return pg.eval_on_selector_all("#rows tr", "rs => rs.map(r => [...r.children].map(c => c.textContent))")
        def wait_rows(pg, n, s=15):
            t0 = time.time()
            while time.time() - t0 < s:
                if len(rows(pg)) >= n: return True
                time.sleep(0.02)
            return False
        # 1
        a.fill("#mis input[name=name]", "26-0142"); a.fill("#mis input[name=op]", "2"); a.click("#startMission")
        t0 = time.time(); ok = False
        while time.time() - t0 < 10:
            if b.eval_on_selector("#mis select", "s => [...s.options].some(o => o.value === '26-0142')"): ok = True; break
            time.sleep(0.05)
        check(ok, "1 the mission started at A is on B's list (%.2f s)" % (time.time() - t0))
        for pg in (b, ph): pg.select_option("#mis select", "26-0142")
        time.sleep(0.5)
        # 2
        def log(pg, call, status="normal", where="", notes=""):
            pg.fill("#entry input[name=callsign]", call); pg.select_option("#entry select[name=status]", status)
            pg.fill("#entry input[name=location]", where); pg.fill("#entry input[name=notes]", notes); pg.click("#entry button")
        t0 = time.time(); log(a, "W7ABC", "location-report", "47.6062, -122.3321", "at the trailhead")
        ok = wait_rows(b, 1); dt = time.time() - t0
        r = rows(b)
        check(ok and r and r[0][2] == "W7ABC" and "47.6062" in r[0][4], "2 A's entry on B's Radio Log in %d ms: %s" % (dt * 1000, r[0] if r else None))
        # 3
        t0 = time.time(); log(ph, "K7XYZ", "urgent", "CN87uo", "needs water")
        ok = wait_rows(a, 2) and wait_rows(b, 2); dt = time.time() - t0
        rb = rows(b)
        check(ok and rb[1][0] == "F001" and rb[1][2] == "K7XYZ", "3 the phone's entry on A and B in %d ms, numbered as the phone's station: %s" % (dt * 1000, rb[1] if len(rb) > 1 else rb))
        # 4
        log(b, "N7QQQ"); wait_rows(a, 3); log(a, "W7ABC", notes="second"); wait_rows(b, 4)
        nos = [x[0] for x in rows(b)]
        check(nos == ["A001", "F001", "B001", "A002"], "4 message numbers in write order, one count per device: %s" % nos)
        # 5 two scribes at once
        t0 = time.time()
        # one browser driver: the two scribes' entries interleaved, each landing as the other's arrive
        for k in range(25):
            log(a, "AA%02d" % k); log(b, "BB%02d" % k)
        ok = wait_rows(a, 54, 60) and wait_rows(b, 54, 60); dt = time.time() - t0
        ra, rb = rows(a), rows(b)
        nos = [x[0] for x in rb]
        check(ok and len(ra) == len(rb) == 54 and len(set(nos)) == 54 and [x[0] for x in ra] == nos,
              "5 two scribes logging at once, 25 each: %d on A, %d on B, %d distinct numbers, the same order on both (%.1f s)" % (len(ra), len(rb), len(set(nos)), dt))
        # 6 reload
        b.reload(); ok = wait_rows(b, 54, 15)
        check(ok and [x[0] for x in rows(b)] == nos and b.input_value("#dev input[name=station]") == "B", "6 B reloaded: all %d entries back from the RAM, its station still B" % len(rows(b)))
        br.close()
finally:
    for p in procs: p.kill()
print("RESULT " + ("PASS" if not FAILS else "FAIL: %d" % len(FAILS)))
sys.exit(1 if FAILS else 0)
