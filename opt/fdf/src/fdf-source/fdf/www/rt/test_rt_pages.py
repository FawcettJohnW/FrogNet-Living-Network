#!/usr/bin/env python3
################################################################
#  Copyright (C) 2016-2026 Fawcett Innovations LLC             #
#  SPDX-License-Identifier: GPL-2.0-only                       #
################################################################
# test_rt_pages.py -- [RT_PAGES_V1] the radio log app's node-served pages, driven in a real browser (Chromium, through
# Playwright): two posts, each a member (its own session, fdf-client, and its own fdf-app serve, both reaching the
# network RAM at the post's one address for it) with a web server and ram.php; three devices -- the scribe at post A
# (station A), the scribe at post B (station B), a field phone on post A's Wi-Fi (station F). [APP_DROPLET_V1] the app's
# general RAM is served by a droplet the app's selection picks (A, the stronger); the pages reach it through ram.php,
# which follows the post's app-where.json.
#
#   python3 test_rt_pages.py RINAB_BUILD_STATIC    (rina-host, ram, fdf-client, fdf-where, fdf-events from it; ram.php
#                                                   from ../dc, fdf-app from ../../bin, beside this directory)
#
# Checks, each with what it saw:
#   1  a mission started at A is on B's list; B opens it
#   2  an entry typed at A is on B's Radio Log (time from A's "Log it" to the row at B)
#   3  the phone logs one; it is on A and B, numbered as the phone's station (F001), no packet, no merge
#   4  the message numbers: A001.., B001.., F001 -- one count per device, printed as on the forms
#   5  two scribes logging at once, 25 each: all 50 on both screens, every number distinct (no clash by construction)
#   6  B reloads: every entry is back from the RAM (nothing kept in the browser but its own id and station)
#   7  the handled mark: B marks A's first entry (click: ✓, again: →); A sees each mark
#   8  a ranger added on B's Rangers page: in A's callsign list, and B's roster shows when it was last heard
#   9  the ICS-309 at post A: every entry from every device in write order, with its number and mark
#   10 the map at B: each callsign at its newest position; A logs W7ABC somewhere new and B's map moves it
#   11 post B cut off (behind B's address for the network RAM is now its own side's RAM): B's droplet serves the app's
#      general RAM on B's side, B's page follows it, writes its own entries there and keeps logging; A keeps A's.
#      B back: the general RAM is A's again, B's page follows it and writes its own again, and A has every one of B's
#      entries -- nothing merged, restored or sent
#   12 the board on a fresh phone at post A (no device set up, nothing typed): every entry, newest first, and the teams
import json, os, shutil, socket, subprocess, sys, tempfile, time
from playwright.sync_api import sync_playwright

B = sys.argv[1]
HERE = os.path.dirname(os.path.abspath(__file__)); DC = os.path.join(HERE, '..', 'dc'); FDF = os.path.join(HERE, '..', '..')
sys.path.insert(0, os.path.join(FDF, "..", "rinab", "tools")); sys.path.insert(0, os.path.join(FDF, "lib", "python"))
from netcut import Via  # noqa: E402
import fdfram  # noqa: E402
FAILS = []
def check(ok, what):
    print(("PASS  " if ok else "FAIL  ") + what, flush=True)
    if not ok: FAILS.append(what)

def free():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p

T = tempfile.mkdtemp(prefix="rt-pages-")
os.makedirs(T + "/bin")
for b in ("ram", "rina-host", "fdf-client", "fdf-where", "fdf-events"): shutil.copy(os.path.join(B, b), T + "/bin/")
procs = []
def ram_host(name):
    port = free(); addr = "127.0.0.1:%d" % port
    procs.append(subprocess.Popen([T + "/bin/rina-host", "--listen", addr, "--api", "/FawcettInnovations.RINA.ram_interface.php"],
                                  env=dict(os.environ, RINA_REGION_FILE=T + "/%s.perm" % name, RINA_NET_CONFIG=T + "/none"),
                                  stdout=subprocess.DEVNULL, stderr=open(T + "/host-%s.log" % name, "w")))
    return addr
RAM = ram_host("region")          # the network RAM
RAM_B_LAN = ram_host("b-lan")     # the network RAM on B's side of a split
via = {"A": Via(RAM), "B": Via(RAM)}   # each post's one address for the network RAM, whatever is behind it
VL = os.path.join(FDF, "..", "..", "..", "..", "..", "var", "www", "html", "vendor", "leaflet")   # the node serves it from here (00845fb)
if not os.path.isdir(VL): sys.exit("no Leaflet at %s: the map page needs it" % VL)
posts = {}
import threading
up = {}
def member_env(post):
    R = T + "/" + post
    return dict(os.environ, RINA_RAM=via[post].addr, FDF_FACTS=R + "/facts.json", FDF_VAR=R, FDF_APPS=R + "/apps",
                FDF_BIN=T + "/bin", FDF_PY=os.path.join(FDF, "lib", "python"), FDF_NAME="post-" + post)
def session(post):                # the member's session as its unit runs it (fdf-join: Restart=always)
    up[post] = True
    def run():
        while up[post]:
            p = subprocess.Popen([T + "/bin/fdf-client"], env=member_env(post), stdout=open(T + "/%s/client.log" % post, "a"), stderr=subprocess.STDOUT)
            p.wait()
            if up[post]: time.sleep(0.5)
    threading.Thread(target=run, daemon=True).start()
for i, name in enumerate(("A", "B"), start=1):
    R = T + "/" + name; os.makedirs(R + "/www/dc")
    json.dump({"name": "post-" + name, "guid": "guid-" + name, "role": "node", "subnet": "127.0.%d.0/24" % i,
               "addrs": [{"ip": "127.0.%d.1" % i, "kind": "own"}],
               "capability": {"mem_total_kb": 8_000_000 if name == "A" else 4_000_000, "cores": 4}}, open(R + "/facts.json", "w"))
    subprocess.run([os.path.join(FDF, "bin", "fdf-app"), "config", "rangertrak", "general"], env=member_env(name), check=True, stdout=subprocess.DEVNULL)
    session(name)
    procs.append(subprocess.Popen([os.path.join(FDF, "bin", "fdf-app"), "serve"], env=member_env(name),
                                  stdout=open(R + "/app.log", "w"), stderr=subprocess.STDOUT))
    shutil.copytree(HERE, R + "/www/rt", ignore=shutil.ignore_patterns("*.py", "__pycache__"))
    shutil.copytree(VL, R + "/www/vendor/leaflet")
    shutil.copy(os.path.join(DC, "ram.php"), R + "/www/dc/")
    svc = ["rt-mission", "rt-log", "rt-handled", "rt-roster"]
    json.dump({"apps": [{"id": "radiolog", "title": "Radio log", "version": "1", "page": "../rt/log.html", "services": svc,
                         "app": "rangertrak", "rams": {x: "general" for x in svc}}]}, open(R + "/www/dc/apps.json", "w"))
    open(R + "/fdf.env", "w").write("export FDF_BIN=%s/bin FDF_VAR=%s RINA_RAM=%s\n" % (T, R, via[name].addr))
    p = free(); posts[name] = "http://127.0.0.1:%d/rt/log.html" % p
    # PHP_CLI_SERVER_WORKERS: the built-in server is one process otherwise, and a held read would hold every page
    procs.append(subprocess.Popen(["php", "-S", "127.0.0.1:%d" % p, "-t", R + "/www"], env=dict(os.environ, PHP_CLI_SERVER_WORKERS="16"),
                                  stdout=subprocess.DEVNULL, stderr=open(T + "/php-%s.log" % name, "w")))
def map_of(post):
    try: return json.load(open(T + "/%s/app-where.json" % post))
    except (OSError, ValueError): return {}
def wait_map(post, want, s=20):
    t0 = time.time()
    while time.time() - t0 < s:
        if map_of(post).get("rangertrak.general", "").startswith(want): return time.time() - t0
        time.sleep(0.02)
    return None
for name in ("A", "B"):           # the client connects first: nothing works until the app's RAM is served
    dt = wait_map(name, "127.0.1.1:")
    check(dt is not None, "0 post %s: the app's general RAM is A's droplet (%s), %.2f s after start" % (name, map_of(name), dt or -1))
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
        check(ok and r and r[0][3] == "W7ABC" and "47.6062" in r[0][5], "2 A's entry on B's Radio Log in %d ms: %s" % (dt * 1000, r[0] if r else None))
        # 3
        t0 = time.time(); log(ph, "K7XYZ", "urgent", "CN87uo", "needs water")
        ok = wait_rows(a, 2) and wait_rows(b, 2); dt = time.time() - t0
        rb = rows(b)
        check(ok and rb[1][0] == "F001" and rb[1][3] == "K7XYZ", "3 the phone's entry on A and B in %d ms, numbered as the phone's station: %s" % (dt * 1000, rb[1] if len(rb) > 1 else rb))
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
        # 7
        def mark_on(pg, i): return pg.eval_on_selector_all("#rows tr", "(rs, i) => rs[i].children[1].textContent", i)
        t0 = time.time(); b.click("#rows tr:nth-child(1) td.mk")
        ok1 = False
        while time.time() - t0 < 10:
            if mark_on(a, 0) == "✓": ok1 = True; break
            time.sleep(0.02)
        d1 = time.time() - t0
        while mark_on(b, 0) != "✓" and time.time() - t0 < 10: time.sleep(0.02)   # B cycles from what B shows
        b.click("#rows tr:nth-child(1) td.mk"); t0 = time.time(); ok2 = False
        while time.time() - t0 < 10:
            if mark_on(a, 0) == "→": ok2 = True; break
            time.sleep(0.02)
        check(ok1 and ok2, "7 B marked A's entry: ✓ on A in %d ms, then → in %d ms" % (d1 * 1000, (time.time() - t0) * 1000))
        # 8
        rg = b.context.new_page(); rg.goto(posts["B"].replace("log.html", "rangers.html"))
        rg.fill("#add input[name=callsign]", "w7abc"); rg.fill("#add input[name=fullName]", "Pat Lee"); rg.fill("#add input[name=team]", "Alpha"); rg.click("#add button")
        t0 = time.time(); ok = False
        while time.time() - t0 < 10:
            if a.eval_on_selector_all("#callsigns option", "os => os.some(o => o.value === 'W7ABC' && o.textContent === 'Pat Lee')"): ok = True; break
            time.sleep(0.02)
        d8 = time.time() - t0
        for _ in range(250):
            rr = rg.eval_on_selector_all("#rows tr", "rs => rs.map(r => [...r.children].map(c => c.textContent))")
            if rr and rr[0][5]: break
            time.sleep(0.02)
        check(ok and rr and rr[0][0] == "W7ABC" and rr[0][5] != "", "8 a ranger added at B is in A's callsign list in %d ms; B's roster: %s" % (d8 * 1000, rr))
        # 9
        pr = a.context.new_page(); pr.goto(posts["A"].replace("log.html", "print309.html")); time.sleep(1.0)
        p9 = pr.eval_on_selector_all("#rows tr", "rs => rs.map(r => [...r.children].map(c => c.textContent))")
        check(len(p9) == 54 and [x[1] for x in p9] == nos and p9[0][2] == "→" and "(Pat Lee)" in p9[0][3] and pr.text_content("#inc") == "26-0142",
              "9 the ICS-309 at A: %d entries in write order, the first %s" % (len(p9), p9[0] if p9 else None))
        # 10
        mp = b.context.new_page(); mp.goto(posts["B"].replace("log.html", "map.html"))
        def maprows(): return mp.eval_on_selector_all("#rows tr", "rs => rs.map(r => [...r.children].map(c => c.textContent))")
        for _ in range(250):
            m1 = maprows()
            if len(m1) == 2: break
            time.sleep(0.02)
        nmk = mp.evaluate("document.querySelectorAll('.leaflet-interactive').length")
        log(a, "W7ABC", "normal", "47.7, -122.4", "moved")
        t0 = time.time(); m2 = []
        while time.time() - t0 < 10:
            m2 = [x for x in maprows() if x[0] == "W7ABC"]
            if m2 and m2[0][1].startswith("47.70000"): break
            time.sleep(0.02)
        check(len(m1) == 2 and nmk == 2 and m2 and m2[0][1].startswith("47.70000") and m2[0][4] == "A028",
              "10 B's map: %d callsigns, %d markers; W7ABC moved on B in %d ms: %s" % (len(m1), nmk, (time.time() - t0) * 1000, m2[0] if m2 else None))
        # 11
        via["B"].to(RAM_B_LAN); dm = wait_map("B", "127.0.2.1:")
        check(dm is not None, "11 B cut off: the general RAM on B's side is B's droplet %.2f s after (%s)" % (dm or -1, map_of("B")))
        log(b, "N7CUT", notes="while cut 1")             # the write after the move: the page reads everything there first
        t0 = time.time(); ok = False
        while time.time() - t0 < 40:
            rb = rows(b)
            if any(x[3] == "N7CUT" for x in rb) and sum(1 for x in rb if x[7] == "scribe-b") >= 27: ok = True; break
            time.sleep(0.1)
        cut_rows = rows(b)
        log(b, "N7CUT", notes="while cut 2"); log(a, "W7AAA", notes="A during the cut")
        time.sleep(1.0)
        a_has_cut = any(x[3] == "N7CUT" for x in rows(a))
        check(ok and not a_has_cut, "11 B cut off: B logs on its own RAM and keeps its %d own entries (%d rows there); A does not see the cut-off entries yet"
              % (sum(1 for x in cut_rows if x[7] == "scribe-b"), len(cut_rows)))
        via["B"].to(RAM); dm = wait_map("B", "127.0.1.1:")
        check(dm is not None, "11 B back: the general RAM is A's again on B %.2f s after (%s)" % (dm or -1, map_of("B")))
        t0 = time.time(); ok = False
        while time.time() - t0 < 60:
            ra = rows(a)
            if sum(1 for x in ra if x[3] == "N7CUT") >= 2: ok = True; break
            time.sleep(0.1)
        dt = time.time() - t0
        rb = rows(b)
        check(ok and any(x[3] == "W7AAA" for x in rb), "11 B's uplink back: both cut-off entries on A %.1f s later; A's entry from the cut on B; %d rows on A, %d on B" % (dt, len(rows(a)), len(rb)))
        # 12
        bd = br.new_context().new_page(); bd.goto(posts["A"].replace("log.html", "board.html")); n12 = []
        for _ in range(250):
            n12 = bd.eval_on_selector_all("#rows tr", "rs => rs.map(r => r.children[0].textContent)")
            if len(n12) == len(rows(a)): break
            time.sleep(0.02)
        teams = bd.eval_on_selector_all("#teams tr", "rs => rs.map(r => r.children[0].textContent)")
        check(len(n12) == len(rows(a)) and n12[0] == rows(a)[-1][0] and "W7ABC" in teams and "N7CUT" in teams,
              "12 the board on a fresh phone at A: %d entries, newest first (%s), %d teams, read-only" % (len(n12), n12[0] if n12 else None, len(teams)))
        br.close()
finally:
    for k in up: up[k] = False
    subprocess.run(["pkill", "-f", T + "/bin/fdf-client"])
    for p in procs: p.terminate()          # fdf-app serve stops the droplets it started
    for p in procs:
        try: p.wait(5)
        except subprocess.TimeoutExpired: p.kill()
    print("logs in", T)
print("RESULT " + ("PASS" if not FAILS else "FAIL: %d" % len(FAILS)))
sys.exit(1 if FAILS else 0)
