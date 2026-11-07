#!/usr/bin/env python3
# fdf-monitor [--once | --json] [HOST:PORT] -- [FDF_MONITOR_V1] frognet_monitor for an FD&F network: the members of
# the shared space and everything the shared space holds about them.
#
# Per member (joined on the member's session):
#   ann     name, IPs it reports, its capability (fdf-client: cores/memory/os; the Linux agent: the full FrogNet probe:
#           CPU benchmark, disk, load, encoders ...)
#   node    alive = its session to the RAM manager is up; the session id
#   speer   the address the RAM manager sees that session come from (the public / NAT address)
#   sess    what is attached to the session;  proc / app: RINA processes and registered applications on that machine
#   term.hosts  it accepts browser logins (fdf-term-serve), since when;  term: browser sessions open to it now
#   telem       [TELEMETRY_IS_A_TUPLE_V1] what the device reports, (pond, telem, <its ns>): position, battery, environment,
#               network, memory -- the member's own tuple, gone with its session
#   settings    [SETTINGS_ARE_TUPLES_V1] (pond, cap, <ns>) the device's, (pond, cap, <ns>:<app>) each app's: permanent
#               tuples the device and its apps follow, whoever writes them (its settings pages, --set here)
# Network-wide: fdf/where (where the race and the Communicator are), the RAM manager's round trip from here.
#
# Interactive (curses): redrawn when something changes -- a member joins, leaves or announces (fdf-watch, held), a
# machine starts or stops advertising terminals (held read on term.hosts) -- or on a key. No timer.
#   Up/Down select   Enter details   Esc/Left back   r re-read now   q quit
#   --once   print the table and every member's details once, then exit (also: Windows, pipes, logs)
#   --json   everything gathered, as JSON
#   --set NAME|NS[:APP] KEY=VALUE ...   write that settings tuple (the keys given; the others kept), permanent; the
#            device or app follows it. VALUE is JSON (30, true, "x") or else a string. e.g. --set pixel telem_period_s=60
# The network is HOST:PORT, else RINA_RAM, else what fdf-join wrote.
import json, os, select, subprocess, sys, threading, time
BIN = os.path.dirname(os.path.realpath(__file__))
LIB = os.path.join(os.path.dirname(BIN), "lib", "python")
sys.path.insert(0, LIB if os.path.isdir(LIB) else BIN)
import fdfram


# ---------------------------------------------------------------- gathering (one pass = one consistent view)
def peek(ram, v):
    """Region records of kind v: {instance: value}; erased records (empty value) left out."""
    rows = ram._do("POST", {"op": "rina"}, {"op": "peek", "v": v})["cells"]
    return {r[1]: r[2].get("v", "") for r in rows if r[2].get("v", "")}


def kv(s):
    """'1;ns=w:x;type=normal;' -> {'_': '1', 'ns': 'w:x', 'type': 'normal'}"""
    out = {}
    for part in s.split(";"):
        if "=" in part:
            k, v = part.split("=", 1)
            out[k] = v
        elif part:
            out["_"] = part
    return out


def gather(ram):
    parts = ram.participants()
    node, speer, sess = peek(ram, "node"), peek(ram, "speer"), peek(ram, "sess")
    procs, apps = peek(ram, "proc"), peek(ram, "app")
    terms = {c["variable"]: c for c in ram.read("term.hosts")}
    open_s = {}
    for c in ram.read("term"):
        open_s.setdefault(c["variable"], set()).add(c["instance"].rsplit(".", 1)[0])
    where = {c["instance"]: c["bag"] for c in ram.read("fdf", "where")}
    telem = {}                                        # [TELEMETRY_IS_A_TUPLE_V1] the newest non-empty cell per ns
    for c in sorted(ram.read("pond", "telem"), key=lambda c: c["id"]):
        if c["bag"]:
            telem[c["instance"]] = c["bag"]
        else:
            telem.pop(c["instance"], None)
    caps = {}                                         # [SETTINGS_ARE_TUPLES_V1] the newest cell per instance
    for c in sorted(ram.read("pond", "cap"), key=lambda c: c["id"]):
        caps[c["instance"]] = c["bag"]
    t0 = time.time()                                  # the round trip: one small request, after the client is warm
    ram._do("GET", {"op": "version"})
    rtt_ms = (time.time() - t0) * 1000.0
    members = []
    for p in parts:
        ns, sid = p["ns"], kv(node.get(p["ns"], "")).get("epoch", "")
        m = {"name": p["name"], "ns": ns, "alive": p["alive"], "ips": p.get("ips", []), "cap": p.get("cap", {}),
             "session": sid, "seen_from": speer.get(sid, ""), "attached": sess.get(sid, ""),
             "procs": sorted(k for k, v in procs.items() if kv(v).get("ns") == ns),
             "apps": sorted(k for k, v in apps.items() if kv(v).get("ns") == ns),
             "term": None, "term_sessions": sorted(open_s.get(p["name"], ())), "telem": telem.get(ns),
             "settings": {i: b for i, b in sorted(caps.items()) if b and (i == ns or i.startswith(ns + ":"))}}
        t = terms.get(p["name"])
        if t:
            m["term"] = {"since": t["bag"].get("since"), "row": t["id"]}
        members.append(m)
    members.sort(key=lambda m: (not m["alive"], m["name"].lower()))
    return {"ram": ram.hostport, "rtt_ms": round(rtt_ms, 1), "at": time.time(), "where": where, "members": members}


# ---------------------------------------------------------------- formatting
def gb(kb):
    return "%.1fG" % (kb / 1048576.0) if kb else "-"


def ago(ts):
    if not ts:
        return "-"
    d = int(time.time() - ts)
    return "%ds" % d if d < 120 else "%dm" % (d // 60) if d < 7200 else "%dh" % (d // 3600) if d < 172800 else "%dd" % (d // 86400)


def load(cap):
    la = cap.get("loadavg")
    return ("%.2f" % la.get("1", 0)) if isinstance(la, dict) else "-"


COLS = [("NAME", 16), ("ALIVE", 5), ("SEEN FROM", 15), ("IPS", 22), ("OS", 7), ("CORES", 5), ("CPU", 8),
        ("MEM", 6), ("LOAD", 5), ("TERM", 6), ("SESS", 4), ("POSITION", 19), ("BATT", 5)]


def position(t):
    l = (t or {}).get("loc") or {}
    return "%.4f,%.4f" % (l["lat"], l["lon"]) if "lat" in l and "lon" in l else "-"


def battery(t):
    b = (t or {}).get("battery") or {}
    return ("%d%%%s" % (b["pct"], "+" if b.get("charging") else "")) if "pct" in b else "-"


def row(m):
    c = m["cap"]
    return [m["name"], "yes" if m["alive"] else "no", m["seen_from"] or "-", ",".join(m["ips"]) or "-",
            c.get("os") or ("linux" if c.get("arch") else "-"), str(c.get("cores", "-")),
            ("%.0f" % c["cpu_bench_total"]) if c.get("cpu_bench_total") else "-", gb(c.get("mem_total_kb", 0)),
            load(c), ("up " + ago(m["term"]["since"])) if m["term"] else "-", str(len(m["term_sessions"])) if m["term"] else "-",
            position(m["telem"]), battery(m["telem"])]


def fit(s, w):
    s = str(s)
    return s if len(s) <= w else s[:w - 1] + "~"


def line(cells):
    return "  ".join(fit(c, w).ljust(w) for c, (_, w) in zip(cells, COLS)).rstrip()


def header_lines(v):
    alive = sum(1 for m in v["members"] if m["alive"])
    out = ["FD&F monitor   network %s   RAM manager round trip %.1f ms   %d member(s), %d alive" %
           (v["ram"], v["rtt_ms"], len(v["members"]), alive)]
    if v["where"]:
        out.append("services: " + "   ".join("%s %s" % (k, " ".join("%s=%s" % kv_ for kv_ in sorted(b.items())))
                                             for k, b in sorted(v["where"].items())))
    return out


def detail_lines(m):
    out = ["%s   (%s)" % (m["name"], "alive" if m["alive"] else "NOT alive: no session to the RAM manager"), ""]
    out.append("identity      ns %s" % m["ns"])
    out.append("session       %s" % (m["session"] or "-"))
    out.append("seen from     %s   (the address the RAM manager sees this session come from)" % (m["seen_from"] or "-"))
    out.append("reports IPs   %s" % (", ".join(m["ips"]) or "-"))
    out.append("attached      %s" % (m["attached"] or "-"))
    if m["term"]:
        out.append("terminal      accepts browser logins (fdf-term-serve), advertised %s ago; %d session(s) open%s" %
                   (ago(m["term"]["since"]), len(m["term_sessions"]),
                    (": " + ", ".join(m["term_sessions"])) if m["term_sessions"] else ""))
    else:
        out.append("terminal      no (fdf-term-serve not running)")
    out.append("processes     %s" % (", ".join(m["procs"]) or "-"))
    out.append("applications  %s" % (", ".join(m["apps"]) or "-"))
    t = m["telem"]
    out.append("")
    if t:
        out.append("telemetry (reported %s ago, %s %s):" % (ago(t.get("at")), t.get("kind", "?"), t.get("model", "")))
        l = t.get("loc") or {}
        if l:
            out.append("  %-20s %s, %s  +-%.0f m%s%s  (%s, fix %s ago)  https://maps.google.com/?q=%s,%s" % (
                "position", l.get("lat"), l.get("lon"), l.get("acc_m") or 0,
                ("  alt %.0f m" % l["alt_m"]) if l.get("alt_m") is not None else "",
                ("  %.1f m/s" % l["speed_mps"]) if l.get("speed_mps") else "", l.get("provider", "?"), ago(l.get("fix_at")),
                l.get("lat"), l.get("lon")))
        else:
            out.append("  %-20s none reported (no permission, location off, or not asked)" % "position")
        for k in ("env", "battery", "net", "mem", "disk"):
            if t.get(k):
                out.append("  %-20s %s" % (k, "  ".join("%s=%s" % kv_ for kv_ in t[k].items())))
        for k in sorted(set(t) - {"loc", "env", "battery", "net", "mem", "disk", "name", "kind", "model", "at"}):
            out.append("  %-20s %s" % (k, json.dumps(t[k]) if isinstance(t[k], (dict, list)) else t[k]))
    else:
        out.append("telemetry     none (the device reports no (pond, telem) tuple)")
    out.append("")
    if m.get("settings"):
        for i, b in m["settings"].items():
            out.append("settings (pond, cap, %s)%s:" % (i, (", written %s ago" % ago(b["at"])) if b.get("at") else ""))
            for k in sorted(set(b) - {"at"}):
                out.append("  %-20s %s" % (k, json.dumps(b[k]) if isinstance(b[k], (dict, list, bool)) else b[k]))
    else:
        out.append("settings      none (no (pond, cap, <ns>) tuple: the device has not written its own)")
    out.append("")
    out.append("capability (as announced%s):" % ((", measured " + ago(m["cap"]["ts"]) + " ago") if m["cap"].get("ts") else ""))
    for k in sorted(m["cap"]):
        v = m["cap"][k]
        out.append("  %-20s %s" % (k, json.dumps(v) if isinstance(v, (dict, list)) else v))
    return out


# ---------------------------------------------------------------- --once / --json
def once(ram, as_json):
    v = gather(ram)
    if as_json:
        print(json.dumps(v, indent=1))
        return
    print("\n".join(header_lines(v)))
    print()
    print(line([c for c, _ in COLS]))
    for m in v["members"]:
        print(line(row(m)))
    for m in v["members"]:
        print()
        print("\n".join(detail_lines(m)))


# ---------------------------------------------------------------- interactive
class Events:
    """Wakes the curses loop through a pipe: select() on the keyboard and this pipe, never a timer."""
    def __init__(self):
        self.r, self.w = os.pipe()
        self.why, self.error = "start", None

    def poke(self, why):
        self.why = why
        os.write(self.w, b"x")


def watch_members(ram, ev):
    exe = os.path.join(BIN, "fdf-watch")
    p = subprocess.Popen([exe, ram.hostport], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    for line_ in p.stdout:
        ev.poke("membership changed (generation %s)" % json.loads(line_)["gen"])
    rc = p.wait()
    ev.error = "the membership watch ended (fdf-watch exit %s: %s); r re-reads, restart to watch again" % (rc, p.stderr.read().strip())
    ev.poke("watch ended")


def watch_terms(ram, ev):
    last = 0
    try:
        last = max([c["id"] for c in ram.read("term.hosts")] + [0])
        while True:
            cells = ram.read("term.hosts", after=last, wait_s=fdfram.HOLD_S)
            if cells:
                last = max(c["id"] for c in cells)
                ev.poke("terminal advertised")
    except fdfram.RamError as e:
        ev.error = "term.hosts read failed: %s" % e
        ev.poke("error")


def watch_telem(ram, ev):
    """[TELEMETRY_IS_A_TUPLE_V1] a held read on (pond, telem): a device reported, or its tuple went with it"""
    last = 0
    try:
        last = max([c["id"] for c in ram.read("pond", "telem")] + [0])
        while True:
            cells = ram.read("pond", "telem", after=last, wait_s=fdfram.HOLD_S)
            if cells:
                last = max(c["id"] for c in cells)
                ev.poke("telemetry")
    except fdfram.RamError as e:
        ev.error = "(pond, telem) read failed: %s" % e
        ev.poke("error")


def interactive(ram):
    import curses
    ev = Events()
    threading.Thread(target=watch_members, args=(ram, ev), daemon=True).start()
    threading.Thread(target=watch_terms, args=(ram, ev), daemon=True).start()
    threading.Thread(target=watch_telem, args=(ram, ev), daemon=True).start()

    def ui(scr):
        curses.curs_set(0)
        curses.use_default_colors()
        for i, c in enumerate((curses.COLOR_GREEN, curses.COLOR_RED, curses.COLOR_CYAN, curses.COLOR_YELLOW), 1):
            curses.init_pair(i, c, -1)
        GREEN, RED, CYAN, YEL = (curses.color_pair(i) for i in range(1, 5))
        sel, detail, top, v, err = 0, False, 0, None, None

        def put(y, x, s, a=0):
            h, w = scr.getmaxyx()
            if 0 <= y < h and x < w:
                scr.addnstr(y, x, s, w - x - 1, a)

        while True:
            if v is None:
                try:
                    v, err = gather(ram), None
                except fdfram.RamError as e:
                    err = str(e)
            scr.erase()
            h, w = scr.getmaxyx()
            y = 0
            if v:
                for i, l in enumerate(header_lines(v)):
                    put(y, 0, l, curses.A_BOLD if i == 0 else CYAN)
                    y += 1
                put(y, 0, "updated %s (%s)" % (time.strftime("%H:%M:%S", time.localtime(v["at"])), ev.why), curses.A_DIM)
                y += 2
                ms = v["members"]
                sel = max(0, min(sel, len(ms) - 1))
                if not detail:
                    put(y, 0, line([c for c, _ in COLS]), curses.A_UNDERLINE)
                    y += 1
                    for i, m in enumerate(ms):
                        a = (GREEN if m["alive"] else RED) | (curses.A_REVERSE if i == sel else 0)
                        put(y, 0, line(row(m)), a)
                        y += 1
                    if not ms:
                        put(y, 0, "no members", curses.A_DIM)
                elif ms:
                    dl = detail_lines(ms[sel])
                    top = max(0, min(top, len(dl) - (h - y - 2)))
                    for i, l in enumerate(dl[top:top + h - y - 2]):
                        put(y, 0, l, (GREEN if ms[sel]["alive"] else RED) | curses.A_BOLD if i + top == 0 else 0)
                        y += 1
            msg = err or ev.error
            if msg:
                put(h - 2, 0, msg, YEL)
            put(h - 1, 0, ("Up/Down scroll   Esc/Left back   r re-read   q quit" if detail else
                           "Up/Down select   Enter details   r re-read   q quit"), curses.A_DIM)
            scr.refresh()

            ready, _, _ = select.select([sys.stdin, ev.r], [], [])
            if ev.r in ready:
                os.read(ev.r, 4096)
                v = None
            if sys.stdin in ready:
                k = scr.getch()
                if k in (ord("q"), ord("Q")):
                    return
                if k in (ord("r"), ord("R")):
                    ev.why, v = "re-read by hand", None
                elif k == curses.KEY_UP:
                    if detail:
                        top = max(0, top - 1)
                    else:
                        sel -= 1
                elif k == curses.KEY_DOWN:
                    if detail:
                        top += 1
                    else:
                        sel += 1
                elif k in (10, 13, curses.KEY_ENTER, curses.KEY_RIGHT):
                    detail, top = True, 0
                elif k in (27, curses.KEY_LEFT, curses.KEY_BACKSPACE, 127):
                    detail = False

    os.environ.setdefault("ESCDELAY", "25")
    curses.wrapper(ui)


def set_cap(ram, who, pairs):
    """[SETTINGS_ARE_TUPLES_V1] --set: write (pond, cap, NS[:APP]) permanent, the given keys over what is there"""
    if who.startswith("w:"):
        parts = who.split(":")
        base, app = "w:" + parts[1], ":".join(parts[2:])
    else:
        base, _, app = who.partition(":")
    if not base.startswith("w:"):
        hits = [p["ns"] for p in ram.participants() if p["name"] == base]
        if len(hits) != 1:
            sys.exit("fdf-monitor --set: %d members named %s%s" % (len(hits), base, (": " + ", ".join(hits)) if hits else ""))
        base = hits[0]
    inst = base + (":" + app if app else "")
    bag = {}
    for c in sorted(ram.read("pond", "cap", inst), key=lambda c: c["id"]):
        if c["instance"] == inst:
            bag = dict(c["bag"])
    for kvp in pairs:
        if "=" not in kvp:
            sys.exit("fdf-monitor --set: %s is not KEY=VALUE" % kvp)
        k, v = kvp.split("=", 1)
        try:
            bag[k] = json.loads(v)
        except ValueError:
            bag[k] = v
    bag["at"] = time.time()
    import socket
    bag["by"] = "fdf-monitor on %s" % socket.gethostname()
    rid = ram.write("pond", "cap", inst, bag, permanent=True)
    print("(pond, cap, %s) #%s = %s" % (inst, rid, json.dumps(bag)))


def main():
    a = sys.argv[1:]
    if "--set" in a:
        i = a.index("--set")
        if len(a) < i + 3:
            sys.exit("usage: fdf-monitor [HOST:PORT] --set NAME|NS[:APP] KEY=VALUE ...")
        try:
            hp = [x for x in a[:i] if not x.startswith("--")]
            set_cap(fdfram.Ram(hp[0] if hp else None), a[i + 1], a[i + 2:])
        except fdfram.RamError as e:
            sys.exit("fdf-monitor: %s" % e)
        return
    rest = [x for x in a if not x.startswith("--")]
    try:
        ram = fdfram.Ram(rest[0] if rest else None)
        if "--json" in a or "--once" in a or not sys.stdout.isatty() or os.name == "nt":
            once(ram, "--json" in a)
        else:
            interactive(ram)
    except fdfram.RamError as e:
        sys.exit("fdf-monitor: %s" % e)
    except KeyboardInterrupt:
        pass
    except BrokenPipeError:                           # | head: the reader left
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())


main()
