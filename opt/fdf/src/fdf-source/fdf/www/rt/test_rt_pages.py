#!/usr/bin/env python3
################################################################
#  Copyright (C) 2016-2026 Fawcett Innovations LLC             #
#  SPDX-License-Identifier: GPL-2.0-only                       #
################################################################
# test_rt_pages.py -- [RT_PAGES_V1] the radio log app's node-served pages, driven in a real browser (Chromium, through
# Playwright): two posts, each a member (its own session, fdf-client, and its own fdf-app serve, both reaching the
# network RAM at the post's one address for it) with a web server and ram.php; three devices -- the scribe at post A
# (laptop-a), the scribe at post B (laptop-b), a field phone on post A's Wi-Fi that sends no host name (shown and numbered by
# the name typed for it, "phone"). [APP_DROPLET_V1] the app's
# general RAM is served by a droplet the app's selection picks (A, the stronger); the pages reach it through ram.php,
# which follows the post's app-where.json.
#
#   python3 test_rt_pages.py RINAB_BUILD_STATIC    (rina-host, ram, fdf-client, fdf-where, fdf-events from it; ram.php
#                                                   from ../dc, fdf-app from ../../bin, beside this directory)
#
# Checks, each with what it saw:
#   1  a mission started at A is on B's list; B opens it
#   2  an entry typed at A is on B's Radio Log (time from A's "Log it" to the row at B)
#   3  the phone logs one; it is on A and B, numbered by its own name (phone-001), no packet, no merge
#   4  the message numbers: A001.., B001.., F001 -- one count per device, printed as on the forms
#   5  two scribes logging at once, 25 each: all 50 on both screens, every number distinct (no clash by construction)
#   6  B reloads: every entry is back from the RAM (nothing kept in the browser but its own id and station)
#   7  the handled mark: B marks A's first entry (click: ✓, again: →); A sees each mark
#   8  a ranger added on B's Rangers page: in A's callsign list, and B's roster shows when it was last heard
#   9  the ICS-309 at post A: every entry from every device in write order, with its number and mark
#   10 the map at B: a query over every device's own position (the scribes' and the phone's). The phone walks: its
#      marker moves on B's map, with nothing logged
#   11 post B cut off (behind B's address for the network RAM is now its own side's RAM): B's droplet serves the app's
#      general RAM on B's side, B's page follows it, writes its own entries there and keeps logging; A keeps A's.
#      B back: the general RAM is A's again, B's page follows it and writes its own again, and A has every one of B's
#      entries -- nothing merged, restored or sent. Positions: during the cut B's map has only B's own (written again on its side); back, every device again
#   13 [POST_309_V1] each post's 309 file (rt-post309; A's restarted before the cut): every entry once, by its id;
#      A's in A's RAM order, as its Radio Log shows them
#   14 [TEAM_RAM_V1] messages: A announces (on B and the phone); the phone sends B a direct message (on B, not in A's
#      direct list); B writes to its team, hospitals (on the phone, a hospitals member; the cell is in the hospitals
#      RAM, served by its own droplet, and not in the general RAM)
#   15 [RT_213_V1][RT_214_V1][RT_SUMMARY_V1] B logs an entry with an ICS-213: A prints it, its fields as RangerTrak fills
#      the form; A writes 214 lines and an after-action note: on B's After-action page, on A's ICS-214 in time order;
#      B's Summary: every entry, the urgent ones as needing attention, the note under its area
#   12 the board on a fresh phone at post A (no device set up, nothing typed): every entry, newest first, and the teams
import json, os, shutil, socket, subprocess, sys, tempfile, time
from playwright.sync_api import sync_playwright

B = sys.argv[1]
HERE = os.path.dirname(os.path.abspath(__file__)); DC = os.path.join(HERE, '..', 'dc'); FDF = os.path.join(HERE, '..', '..')
sys.path.insert(0, os.path.join(FDF, "..", "rinab", "tools")); sys.path.insert(0, os.path.join(FDF, "lib", "python"))
from netcut import Via, From  # noqa: E402
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
post309 = {}
def post309_start(post):
    p = subprocess.Popen([os.path.join(FDF, "bin", "rt-post309")], env=member_env(post), stdout=open(T + "/%s/post309.log" % post, "a"), stderr=subprocess.STDOUT)
    procs.append(p); return p
# [STATION_IS_HOSTNAME_V1] the leases each post's dnsmasq gave (in its facts, as fdf-facts writes them): the devices'
# host names (a station ID is the host name, hidden from users). The phone's lease has none: fdf-facts records the MAC.
# The fresh phone at A has a lease too; the board asks it nothing
LEASES = {"A": {"127.0.11.2": "laptop-a", "127.0.11.3": "02:00:00:00:00:33", "127.0.11.4": "phone-g", "127.0.11.5": "eoc-1", "127.0.11.6": "tablet-h"}, "B": {"127.0.12.2": "laptop-b"}}
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
               "addrs": [{"ip": "127.0.%d.1" % i, "kind": "own"}] + [{"ip": ip, "kind": "given", "value": h, "mac": "02:00:00:00:00:%02x" % k}
                                                                    for k, (ip, h) in enumerate(LEASES[name].items())],
               "capability": {"mem_total_kb": 8_000_000 if name == "A" else 4_000_000, "cores": 4}}, open(R + "/facts.json", "w"))
    subprocess.run([os.path.join(FDF, "bin", "fdf-app"), "config", "rangertrak", "general,hospitals",
                    "--role", "hospitals", "--role", "eoc:default", "--user", "kim:kim-pw=hospitals", "--user", "lee:lee-pw=hospitals"], env=member_env(name), check=True, stdout=subprocess.DEVNULL)
    session(name)
    procs.append(subprocess.Popen([os.path.join(FDF, "bin", "fdf-app"), "serve"], env=member_env(name),
                                  stdout=open(R + "/app.log", "w"), stderr=subprocess.STDOUT))
    post309[name] = post309_start(name)
    shutil.copytree(HERE, R + "/www/rt", ignore=shutil.ignore_patterns("*.py", "*.mjs", "__pycache__"))
    shutil.copytree(VL, R + "/www/vendor/leaflet")
    shutil.copy(os.path.join(DC, "ram.php"), R + "/www/dc/")
    svc = ["rt-mission", "rt-log", "rt-handled", "rt-roster", "rt-pos", "rt-who", "rt-msg", "rt-dm", "rt-team", "rt-notes"]
    json.dump({"apps": [{"id": "radiolog", "title": "Radio log", "version": "1", "page": "../rt/log.html", "services": svc,
                         "app": "rangertrak", "rams": {x: ("team" if x == "rt-team" else "general") for x in svc}}]}, open(R + "/www/dc/apps.json", "w"))
    open(R + "/fdf.env", "w").write("export FDF_BIN=%s/bin FDF_VAR=%s RINA_RAM=%s\n" % (T, R, via[name].addr))
    p = free(); posts[name] = "127.0.0.1:%d" % p
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
        fwd = {}
        def device(post, name, ip, at=None, team=""):        # ip: its lease at the post; at: where its own GPS says it is (None: it gives none)
            ctx = br.new_context(**({"geolocation": {"latitude": at[0], "longitude": at[1], "accuracy": 8}, "permissions": ["geolocation"]} if at else {}))
            fwd[ip] = From(ip, posts[post])
            pg = ctx.new_page()
            clog = open(T + "/console-%s.log" % name, "a")            # [DIAG-WATCH] the page's own account of every read and write
            ctx.on("console", lambda msg: (clog.write("%.3f %s %s\n" % (time.time(), msg.page.url.rsplit("/", 1)[-1] if msg.page else "", msg.text)), clog.flush()))
            pg.goto("http://127.0.0.1:%d/rt/log.html" % fwd[ip].port)
            pg.wait_for_function("document.body.dataset.connected === '1'")
            pg.fill("#dev input[name=name]", name); pg.click("#saveDev")
            if team:                                  # [APP_ACCESS_V1] the role is the user's, activated at their login
                user, pw = team
                pg.fill("#login input[name=user]", user); pg.fill("#login input[name=password]", pw); pg.click("#doLogin")
                pg.wait_for_function("document.querySelector('#devInfo').textContent.includes('role ')")
            return pg
        def page(pg, name): return pg.url.split("/rt/")[0] + "/rt/" + name      # another page of the same device
        a, b, ph = device("A", "scribe-a", "127.0.11.2", (47.65, -122.35)), device("B", "scribe-b", "127.0.12.2", (47.55, -122.25), ("kim", "kim-pw")), device("A", "phone", "127.0.11.3", (47.6062, -122.3321), ("lee", "lee-pw"))
        st = [pg.text_content("#devInfo") for pg in (a, b, ph)]
        # [APP_ACCESS_V1] login is a read at the device's own edge: the door checks the password against the access tuples
        import urllib.request, urllib.error
        def door_login(post, body):
            rq = urllib.request.Request("http://%s/dc/ram.php?op=login&app=rangertrak" % posts[post], data=json.dumps(body).encode(), method="POST")
            try:
                with urllib.request.urlopen(rq, timeout=10) as r: return json.loads(r.read())
            except urllib.error.HTTPError as e: return json.loads(e.read())
        lg = {"kim at B": door_login("B", {"user": "kim", "password": "kim-pw"}), "kim, wrong password": door_login("A", {"user": "kim", "password": "x"}),
              "eoc, a default role": door_login("A", {"role": "eoc"}), "hospitals, no login": door_login("A", {"role": "hospitals"})}
        check(lg["kim at B"].get("role") == "hospitals" and not lg["kim, wrong password"].get("ok") and lg["eoc, a default role"].get("role") == "eoc"
              and not lg["hospitals, no login"].get("ok"),
              "0 login at the door, a read of the access tuples: %s" % {k: v.get("role") if v.get("ok") else v.get("why") for k, v in lg.items()})
        check(st == ["laptop-a", "laptop-b, role hospitals", "phone, role hospitals"],
              "0 each device shown by its host name from its lease, or by its own name when it sent none (the phone); no station ID shown: %s" % st)
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
        check(ok and rb[1][0] == "phone-001" and rb[1][3] == "K7XYZ", "3 the phone's entry on A and B in %d ms, numbered with the phone's own name (it sent no host name): %s" % (dt * 1000, rb[1] if len(rb) > 1 else rb))
        # 4
        log(b, "N7QQQ"); wait_rows(a, 3); log(a, "W7ABC", notes="second"); wait_rows(b, 4)
        nos = [x[0] for x in rows(b)]
        check(nos == ["laptop-a-001", "phone-001", "laptop-b-001", "laptop-a-002"], "4 message numbers in write order, one count per station: %s" % nos)
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
        b.wait_for_function("document.body.dataset.connected === '1'")
        check(ok and [x[0] for x in rows(b)] == nos and b.text_content("#devInfo") == "laptop-b, role hospitals", "6 B reloaded: all %d entries back from the RAM; %s" % (len(rows(b)), b.text_content("#devInfo")))
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
        # [MARK_PER_HANDLER_V1] A marks the same entry: its own cell; the newest is shown everywhere
        a.click("#rows tr:nth-child(1) td.mk"); t0 = time.time(); ok3 = False
        while time.time() - t0 < 10:
            if mark_on(b, 0) == "!": ok3 = True; break
            time.sleep(0.02)
        e0 = a.get_attribute("#rows tr:nth-child(1)", "data-i")
        cells = [c for c in fdfram.Ram(map_of("A")["rangertrak.general"]).read("rt-handled", "26-0142") if (c.get("bag") or {}).get("entry") == e0]
        check(ok3 and len(cells) == 2 and len({c["bag"]["by"] for c in cells}) == 2,
              "7 A marked it too: ! on B in %d ms; the RAM holds one mark cell per handler: %s"
              % ((time.time() - t0) * 1000, sorted((c["bag"]["by"], c["bag"]["mark"]) for c in cells)))
        # 8
        rg = b.context.new_page(); rg.goto(page(b, "rangers.html"))
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
        pr = a.context.new_page(); pr.goto(page(a, "print309.html")); time.sleep(1.0)
        p9 = pr.eval_on_selector_all("#rows tr", "rs => rs.map(r => [...r.children].map(c => c.textContent))")
        check(len(p9) == 54 and [x[1] for x in p9] == nos and p9[0][2] == "!" and "(Pat Lee)" in p9[0][3] and pr.text_content("#inc") == "26-0142",
              "9 the ICS-309 at A: %d entries in write order, the first %s" % (len(p9), p9[0] if p9 else None))
        # 10
        mp = b.context.new_page(); mp.goto(page(b, "map.html"))
        def maprows(): return mp.eval_on_selector_all("#rows tr", "rs => rs.map(r => [...r.children].map(c => c.textContent))")
        for _ in range(250):
            m1 = maprows()
            if len(m1) == 3: break
            time.sleep(0.02)
        nmk = mp.evaluate("document.querySelectorAll('.leaflet-interactive').length")
        n_log = len(rows(a))
        ph.context.set_geolocation({"latitude": 47.7, "longitude": -122.4, "accuracy": 8})
        t0 = time.time(); m2 = []
        while time.time() - t0 < 10:
            m2 = [x for x in maprows() if x[0] == "phone"]
            if m2 and m2[0][1].startswith("47.70000"): break
            time.sleep(0.02)
        check(sorted(x[0] for x in m1) == ["phone", "scribe-a", "scribe-b"] and nmk == 3 and m2 and m2[0][1].startswith("47.70000") and len(rows(a)) == n_log,
              "10 B's map: %s, %d markers; the phone walked and moved on B's map in %d ms, nothing logged (%d entries): %s"
              % ([x[0] for x in m1], nmk, (time.time() - t0) * 1000, len(rows(a)), m2[0] if m2 else None))
        # 11
        post309["A"].terminate(); post309["A"].wait(); post309["A"] = post309_start("A")     # a lost and resumed read
        via["B"].to(RAM_B_LAN); dm = wait_map("B", "127.0.2.1:")
        check(dm is not None, "11 B cut off: the general RAM on B's side is B's droplet %.2f s after (%s)" % (dm or -1, map_of("B")))
        log(b, "N7CUT", notes="while cut 1")             # the write after the move: the page reads everything there first
        t0 = time.time(); ok = False
        while time.time() - t0 < 40:
            rb = rows(b)
            if any(x[3] == "N7CUT" for x in rb) and sum(1 for x in rb if x[7] == "scribe-b") >= 27: ok = True; break
            time.sleep(0.1)
        cut_rows = rows(b)
        t1 = time.time()
        while time.time() - t1 < 15:                     # B's map's held read lasts at most 8 s on the RAM it has left
            on_b_side = [x[0] for x in maprows()]
            if on_b_side == ["scribe-b"]: break
            time.sleep(0.1)
        log(b, "N7CUT", notes="while cut 2"); log(a, "W7AAA", notes="A during the cut")
        time.sleep(1.0)
        a_has_cut = any(x[3] == "N7CUT" for x in rows(a))
        check(ok and not a_has_cut, "11 B cut off: B logs on its own RAM and keeps its %d own entries (%d rows there); A does not see the cut-off entries yet"
              % (sum(1 for x in cut_rows if x[7] == "scribe-b"), len(cut_rows)))
        # [OWN_TRUTH_KEPT_V1] B's scribe reloads during the cut: its own entries are on the device, not only in memory
        for _ in range(250):
            if any(x[6] == "while cut 2" for x in rows(b)): break
            time.sleep(0.02)
        n_before = sum(1 for x in rows(b) if x[7] == "scribe-b")
        b.reload(); b.wait_for_function("document.body.dataset.connected === '1'")
        ok_r = wait_rows(b, n_before, 20); n_own = sum(1 for x in rows(b) if x[7] == "scribe-b")
        recs, t1 = [], time.time()
        while time.time() - t1 < 15:
            recs = [c["bag"] for c in fdfram.Ram(RAM_B_LAN).read("fdf-app", "device")]
            if any(r.get("station") == "laptop-b" and r.get("role") == "hospitals" for r in recs): break
            time.sleep(0.1)
        on_region = [c["bag"]["station"] for c in fdfram.Ram(RAM).read("fdf-app", "device")]
        check(any(r.get("station") == "laptop-b" and r.get("role") == "hospitals" for r in recs) and "phone" in on_region,
              "11 [APP_ACCESS_V1] the devices' own access records: B's written again on its side's network RAM (%s); the region's has %s"
              % ([(r["station"], r["role"]) for r in recs], sorted(set(on_region))))
        check(ok_r and n_own == n_before == 28, "11 B reloaded during the cut: its %d own entries back on its side (%d before the reload)" % (n_own, n_before))
        via["B"].to(RAM); dm = wait_map("B", "127.0.1.1:")
        check(dm is not None, "11 B back: the general RAM is A's again on B %.2f s after (%s)" % (dm or -1, map_of("B")))
        t0 = time.time(); ok = False
        while time.time() - t0 < 60:
            ra = rows(a)
            if sum(1 for x in ra if x[3] == "N7CUT") >= 2: ok = True; break
            time.sleep(0.1)
        dt = time.time() - t0
        rb = rows(b)
        t1 = time.time(); back = []
        while time.time() - t1 < 20:
            back = sorted(x[0] for x in maprows())
            if back == ["phone", "scribe-a", "scribe-b"]: break
            time.sleep(0.1)
        check(on_b_side == ["scribe-b"] and back == ["phone", "scribe-a", "scribe-b"],
              "11 positions: on B's side during the cut, B's map has %s (its own fix written again there; A's devices are not on that side); back, %s"
              % (on_b_side or "none", back))
        t1 = time.time()
        while time.time() - t1 < 20 and len(rows(b)) != len(rows(a)): time.sleep(0.1)
        rb = rows(b)
        check(ok and any(x[3] == "W7AAA" for x in rb) and len(rb) == len(rows(a)), "11 B's uplink back: both cut-off entries on A %.1f s later; A's entry from the cut on B; %d rows on A, %d on B" % (dt, len(rows(a)), len(rb)))
        # 12
        fwd["127.0.11.4"] = From("127.0.11.4", posts["A"])
        bd = br.new_context().new_page(); bd.goto("http://127.0.0.1:%d/rt/board.html" % fwd["127.0.11.4"].port); n12 = []
        for _ in range(250):
            n12 = bd.eval_on_selector_all("#rows tr", "rs => rs.map(r => r.children[0].textContent)")
            if len(n12) == len(rows(a)): break
            time.sleep(0.02)
        teams = bd.eval_on_selector_all("#teams tr", "rs => rs.map(r => r.children[0].textContent)")
        check(len(n12) == len(rows(a)) and n12[0] == rows(a)[-1][0] and "W7ABC" in teams and "N7CUT" in teams,
              "12 the board on a fresh phone at A: %d entries, newest first (%s), %d teams, read-only" % (len(n12), n12[0] if n12 else None, len(teams)))
        # 13
        def file309(post):
            try:
                with open(T + "/%s/rt/309/26-0142.2.csv" % post, newline="") as f: return list(__import__("csv").DictReader(f))
            except FileNotFoundError: return []
        want = [x[0] for x in rows(a)]
        t0 = time.time()
        while time.time() - t0 < 20:
            fa, fb = file309("A"), file309("B")
            if len(fa) == len(fb) == len(want): break
            time.sleep(0.1)
        ida, idb = [r["id"] for r in fa], [r["id"] for r in fb]
        check([r["msg_no"] for r in fa] == want and len(set(ida)) == len(ida) and set(idb) == set(ida) and len(set(idb)) == len(idb),
              "13 the 309 files: A %d rows, in A's order, each once; B %d rows, each once (B's own from the cut first: %s)"
              % (len(fa), len(fb), [r["msg_no"] for r in fb if "CUT" in r["callsign"]]))
        # 14
        ma, mb, mf = [pg.context.new_page() for pg in (a, b, ph)]
        for mp_, pg in ((ma, a), (mb, b), (mf, ph)): mp_.goto(page(pg, "messages.html"))
        def tab(pg, t): return pg.eval_on_selector_all("#" + t + " tr", "rs => rs.map(r => r.children[2].textContent)")
        def until(f, s=10):
            t0 = time.time()
            while time.time() - t0 < s:
                if f(): return time.time() - t0
                time.sleep(0.02)
            return None
        def send(pg, to, text):
            pg.wait_for_function("(v) => [...document.querySelector('#send select').options].some(o => o.value === v)", arg=to)
            pg.select_option("#send select", to); pg.fill("#send input[name=text]", text); pg.click("#send button")
        send(ma, "*", "shift change at 1800")
        t1 = until(lambda: "shift change at 1800" in tab(mb, "ann") and "shift change at 1800" in tab(mf, "ann"))
        send(mf, "laptop-b", "B: water at the trailhead?")
        t2 = until(lambda: "B: water at the trailhead?" in tab(mb, "dm"))
        a_dm = tab(ma, "dm")
        send(mb, "@", "hospitals: two beds at St. Mark")
        t3 = until(lambda: "hospitals: two beds at St. Mark" in tab(mf, "team"))
        hosp = fdfram.Ram(map_of("A")["rangertrak.hospitals"]).read("rt-team", "26-0142")
        gen = fdfram.Ram(map_of("A")["rangertrak.general"]).read("rt-team", "26-0142")
        check(t1 is not None and t2 is not None and "B: water at the trailhead?" not in a_dm and t3 is not None
              and [c["bag"]["text"] for c in hosp] == ["hospitals: two beds at St. Mark"] and gen == [],
              "14 messages: announcement on B and the phone in %s s; the phone's DM on B in %s s, not in A's direct list (%s); "
              "B's team message on the phone in %s s, in the hospitals RAM at %s (%d cell), none in the general RAM"
              % (t1 and round(t1, 2), t2 and round(t2, 2), a_dm, t3 and round(t3, 2), map_of("A")["rangertrak.hospitals"], len(hosp)))
        # 15
        b.check("#entry input[name=g213]"); b.fill("#entry input[name=to213]", "IC, EOC"); b.fill("#entry input[name=subject213]", "Water")
        b.fill("#entry input[name=message213]", "Need 20 L at the trailhead"); log(b, "W7ABC", "need-rest-food", "", "water low")
        until(lambda: any("213" in (x[6] or "") and x[3] == "W7ABC" for x in rows(a)))
        href = a.get_attribute("#rows tr:last-child a.f213", "href")
        p13 = a.context.new_page(); p13.goto(page(a, href))
        until(lambda: "Water" in (p13.text_content("#form") or ""))
        form = p13.eval_on_selector_all("#form td", "ts => ts.map(t => t.textContent)")
        aa = a.context.new_page(); aa.goto(page(a, "aar.html"))
        for txt, t in (("Opened the trailhead station", "08:15"), ("Relieved by B shift", "07:30")):
            aa.fill("#act input[name=text]", txt); aa.fill("#act input[name=at]", t); aa.click("#act button"); time.sleep(0.2)
        aa.fill("#aar input[name=text]", "Repeater coverage thin past the ridge"); aa.fill("#aar input[name=area]", "Communications"); aa.click("#aar button")
        ab = b.context.new_page(); ab.goto(page(b, "aar.html"))
        t4 = until(lambda: len(ab.eval_on_selector_all("#acts tr", "x => x")) == 2 and len(ab.eval_on_selector_all("#notes tr", "x => x")) == 1)
        p14 = a.context.new_page(); p14.goto(page(a, "print214.html"))
        until(lambda: len(p14.eval_on_selector_all("#rows tr", "x => x")) == 2)
        l214 = p14.eval_on_selector_all("#rows tr", "rs => rs.map(r => r.children[1].textContent)")
        sm = b.context.new_page(); sm.goto(page(b, "summary.html"))
        n_all = len(rows(a)); n_att = sum(1 for x in rows(a) if x[4] in ("urgent", "need-rest-food", "evidence-report"))
        until(lambda: (sm.text_content("#totals") or "").startswith("%d entries" % n_all) and "Communications" in (sm.text_content("#aar") or ""))
        tot, natt, aar = sm.text_content("#totals"), sm.text_content("#natt"), sm.text_content("#aar")
        e213 = a.get_attribute("#rows tr:last-child", "data-i")
        until(lambda: os.path.exists(T + "/A/rt/213/26-0142.2.%s.txt" % e213))
        f213 = open(T + "/A/rt/213/26-0142.2.%s.txt" % e213).read() if os.path.exists(T + "/A/rt/213/26-0142.2.%s.txt" % e213) else ""
        check("4. Subject: Water" in f213 and "laptop-b-" in f213 and "2. To (Name and Position): IC, EOC" in f213,
              "15 post A's 213 file for it: %s" % f213.splitlines()[:5])
        check(any("IC, EOC" in c for c in form) and any("Water" in c for c in form) and any("Need 20 L" in c for c in form)
              and any(c.endswith(":") is False and "/" in c and "Date" in c for c in form)
              and t4 is not None and l214 == ["Relieved by B shift", "Opened the trailhead station"]
              and tot.startswith("%d entries" % n_all) and natt == str(n_att) and "Repeater coverage thin" in aar,
              "15 the 213 at A: %s; 214 lines on B's After-action in %s s, on A's ICS-214 in time order %s; B's Summary: %s; %s need attention; notes: %s"
              % ([c for c in form if c][:6], t4 and round(t4, 2), l214, tot, natt, aar))
        # 16 [APP_FOCUS_V1]
        fresh = {}
        for ip, q in (("127.0.11.5", "?role=eoc"), ("127.0.11.6", "")):
            fwd[ip] = From(ip, posts["A"]); pg = br.new_context().new_page(); pg.goto("http://127.0.0.1:%d/rt/index.html%s" % (fwd[ip].port, q))
            pg.wait_for_url(lambda u: "index.html" not in u, timeout=10000); fresh[q or "none"] = pg.url.rsplit("/", 1)[-1]
        hp = b.context.new_page(); hp.goto(page(b, "index.html")); hp.wait_for_url("**/messages.html", timeout=10000)
        check(fresh == {"?role=eoc": "summary.html", "none": "log.html"} and hp.url.endswith("messages.html"),
              "16 the client opens on its role's screen: an EOC client (default role, no password) on %s, a client with no role on %s, "
              "B (kim, hospitals) on %s" % (fresh.get("?role=eoc"), fresh.get("none"), hp.url.rsplit("/", 1)[-1]))
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
