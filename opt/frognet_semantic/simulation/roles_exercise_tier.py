#!/usr/bin/env python3
################################################################
#  Copyright (C) 2016-2026 Fawcett Innovations LLC             #
#                                                              #
#  SPDX-License-Identifier: GPL-2.0-only                       #
#                                                              #
#  This program is free software; you can redistribute it      #
#  and/or modify it under the terms of the GNU General Public  #
#  License as published by the Free Software Foundation;       #
#  version 2 of the License, and no other version.             #
#                                                              #
#  This program is distributed in the hope that it will be     #
#  useful, but WITHOUT ANY WARRANTY; without even the implied  #
#  warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR     #
#  PURPOSE.  See the GNU General Public License for details.   #
#                                                              #
#  See COPYRIGHT and LICENSE at the root of this tree.         #
################################################################
"""
roles_exercise_tier.py -- [ROLE_MEMORIES_V1] the protocol layer demonstration, between machines.

The design is "CroakOS Protocol Layer Demonstration: Requirements and Detailed Design" (version 0.1, 21 Nov 2026).
Three sites, each a machine of wire_between_machines_tier (a network namespace, the real daemon and proxy, its own
api.php and database). Seven Roles, each with a memory of its own, found by its host name:

    site A  Seattle5     exerciseHost.frognet, warningHost.exercise
    site B  Seattle3B    assessmentHost.exercise, computeHost.exercise, instructorHost.exercise
    site C  New-York-1   commandHost.exercise, unitsHost.exercise

They are application memories: the exercise is one application and each Role a Team of it, and the tier runs the
existing application election (core/app_hosts.py) for them.

A memory's channel setting is the tuple (channel, setting, <the memory's host name>) in the ControlRam. Every end reads
it by that name ([THE_SETTING_IS_READ_BY_THE_MEMORYS_NAME_V1]), so two memories on one machine each have their own
connection under their own setting ([A_CONNECTION_IS_FOR_A_MEMORY_V1]).

What it runs (the design's build steps 1 to 7):

    --step memories   the seven memories are named in every hosts file; each has a setting of its own, and the two or
                      three on one machine have DIFFERENT ones; every machine writes into every memory by name and
                      another machine reads the value back; a read held at one site returns on a write from another.
    --step protocols  TCP, Chorus and OpenQASM at once on the daemon's one port, each known by its first bytes; a
                      circuit's bytes on each; a channel's driver, key and algorithm each changed; what is refused.
    --step pages      each Role's admin page and the door it reads a Role's memory through.
    --run 1|2|3|4|all the exercise itself, the design's 13.1: seven Roles' services (simulation/roles_exercise/) play
                      the scenario with every channel on TCP (1), on Chorus (2), on OpenQASM (3), and mixed, with two
                      channels changing protocol, key and algorithm while it runs (4). The exercise record of each
                      run is compared with run 1's byte for byte; the checks of 13.2; the performance table of 5.5.

  python3 simulation/roles_exercise_tier.py --run all [--links none|radio-4800] [--steps 12] [--out <file prefix>]

Passing on the fast link (21 Nov 2026). NOT passing at 4,800 baud yet: EXPLORATION-LOG.md, 21 Nov.
"""
import argparse
import json
import os
import re
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import wire_between_machines_tier as W  # noqa: E402

BUILD = "roles-exercise-tier-v4-20261121d"
PY, SEM = W.PY, W.SEM
START = "auto,databasehost_control.frognet chorus"       # every machine is told of chorus and listens on both
SITES = {"A": "Seattle5", "B": "Seattle3B", "C": "New-York-1"}
ROLES = (("warning", "A"), ("exercise", "A"), ("assessment", "B"), ("compute", "B"), ("instructor", "B"),
         ("command", "C"), ("units", "C"))


APP = "exercise"                                        # the exercise is one application; each Role is a Team of it
FDF_PY = os.path.join(os.path.dirname(os.path.dirname(SEM)), "opt", "fdf", "src", "fdf-source", "fdf", "lib", "python")


def memory(role):
    """A Role's memory is an application memory, which the network already elects (core/app_hosts.py): the
    application's general memory for Exercise, <app>Host.frognet, and a Team's for every other Role, <team>Host.<app>."""
    return "%sHost.frognet" % APP if role == APP else "%sHost.%s" % (role, APP)


def app_ram(role):
    return "general" if role == APP else role


def channel(role):
    """The name a Role's channel setting, its keys and the logs go by: the Role (the design's 10.1)."""
    return role


KEYS_IN_A_SET = 20
SEALED = lambda role, switches=0, protection="aes-256-gcm": {"seed": "the seed of %s" % role, "protection": protection, "switches": switches}


def elect_memories(machines, by, check):
    """[THE_ROLES_MEMORIES_ARE_ELECTED_V1] The existing application election, run as it is (the design's 7.2, item 2;
    nothing in core/app_hosts.py or the selection is changed). Each machine writes its own capability tuples into the
    SystemRam, (fdf-app, can, exercise.<memory>.<its name>), offering the memories its site was installed to serve;
    the application's configuration tuple, (fdf-app, config, exercise), goes into the ControlRam; then every machine
    runs app_hosts.commit on its own hosts file, as discovery/elect.py does on a node at every election, and the
    election's lines are what its hosts file holds."""
    rams = [app_ram(r) for r, _s in ROLES]
    for m in machines:
        m.env["FDF_PY"] = FDF_PY
        serve = [app_ram(r) for r, s in ROLES if SITES[s] == m.name]
        w = on(m, "sys.path.insert(0, %r)\nimport fdfram, os\n"
                  "mem = int(open('/proc/meminfo').readline().split()[1])\n"
                  "ram = fdfram.RoleRam(%r)\n"
                  "ids = [ram.write('fdf-app', 'can', '%s.' + r + '.%s', {'guid': %r, 'addr': %r, 'serve': %r, 'mem_total_kb': mem, 'cores': os.cpu_count()}) for r in %r]\n"
                  "print('@@ ' + json.dumps({'wrote': len(ids)}))" % (FDF_PY, m.system.ip, APP, m.name, m.name, m.ip, serve, serve))
        check(w.get("wrote") == len(serve), "%s wrote its capability tuples into the SystemRam, offering %s (%s)" % (m.name, ", ".join(serve), w))
    c = machines[0].control
    w = on(c, "sys.path.insert(0, %r)\nimport fdfram\n"
              "i = fdfram.RoleRam(%r).write('fdf-app', 'config', %r, {'config': json.dumps({'rams': %r}), 'seq': 1})\n"
              "print('@@ ' + json.dumps({'wrote': bool(i)}))" % (FDF_PY, c.ip, APP, rams))
    check(w.get("wrote"), "the application's configuration tuple is in the ControlRam: one application, %r, with %d memories (%s)" % (APP, len(rams), w))
    elected = {}
    for m in machines:
        r = on(m, "from core import app_hosts\n"
                  "said = []\n"
                  "etc = [l.rstrip('\\n') for l in open(%r)]\n"
                  "new = app_hosts.commit(etc, hosts_path=%r, logger=said.append)\n"
                  "print('@@ ' + json.dumps({'hosts': new, 'said': said}))" % (m.hosts, m.hosts))
        lines = [l for l in r.get("hosts") or [] if l.split()[1:2] and any(l.split()[1] == memory(role) for role, _s in ROLES)]
        elected[m.name] = {l.split()[1]: l.split()[0] for l in lines}
        if r.get("hosts"):
            W.write_hosts(m, "\n".join(r["hosts"]) + "\n")
        else:
            print("   %s: the election did not run: %s" % (m.name, r), flush=True)
    want = {memory(role): by[SITES[site]].ip for role, site in ROLES}
    check(all(e == want for e in elected.values()),
          "the application election, run on each machine, elected the seven Roles' memories, the same on every machine: %s"
          % ", ".join("%s on %s" % (memory(r), SITES[s]) for r, s in ROLES) if all(e == want for e in elected.values())
          else "the election gave %s, and the site table says %s" % (elected, want))
    W.MORE_MEMORIES.clear()                              # a later merge writes the hosts file again: with the lines the election gave
    W.MORE_MEMORIES.update({name: [m for m in machines if m.ip == ip][0] for name, ip in elected[machines[0].name].items()})


def key_bytes(role, i):
    """One key of a Role's set for the demonstration: 32 bytes made from the Role and its place in the set, the same
    on every machine, placed in each machine's key store before the run. It never crosses a link."""
    import hashlib
    return hashlib.sha256(("roles-exercise key %s %d" % (role, i)).encode()).digest()


def on(m, code, wait_s=120):
    """A program on machine m, as a service there would run: it prints one line '@@ <json>'."""
    r = m.exec([PY, "-c", "import sys, json, time; sys.path.insert(0, %r); sys.path.insert(0, %r)\n" % (SEM, os.path.join(SEM, "core")) + code], env=m.env)
    line = [l for l in r.stdout.splitlines() if l.startswith("@@ ")]
    if not line:
        return {"error": (r.stdout[-300:] + " " + r.stderr[-600:]).strip()}
    return json.loads(line[-1][3:])


def said(path, pattern, since=0.0):
    return [x for x in re.findall(pattern, open(path, errors="replace").read()) if float(x if isinstance(x, str) else x[0]) >= since]


def sites_up(a, check):
    """The three sites, their network software, the keys, the election, and the seven memories' role lines."""
    dbc = json.load(open(os.environ["FROGNET_DB_CONFIG"]))
    tmp = tempfile.mkdtemp(prefix="roles-exercise-")
    net = W.net_of("trial3")
    machines = [W.Machine(i, name, ip, roles, tmp, ifaces) for i, (name, ip, roles, ifaces) in enumerate(net["machines"])]
    by = {m.name: m for m in machines}
    W.tear_down(machines)
    W.MORE_MEMORIES.clear()
    print("== %s, links %s: %s" % (BUILD, a.links, ", ".join("%s %s" % (m.name, m.ip) for m in machines)), flush=True)
    W.bring_up(machines, dbc, net)
    for m in machines:
        kd = os.path.join(m.dir, "channel_keys"); os.makedirs(kd, exist_ok=True)
        # A machine holds a Role's set only if it carries that Role's traffic (R42). In this exercise every site's
        # admin pages read every Role's memory, so all three machines carry all seven and hold all seven sets.
        for role, _site in ROLES:
            os.makedirs(os.path.join(kd, role), exist_ok=True)
            for i in range(KEYS_IN_A_SET):
                with open(os.path.join(kd, role, "%02d" % i), "wb") as f:
                    f.write(key_bytes(role, i))
        m.env["FROGNET_CHANNEL_KEYS"] = kd
        cd = os.path.join(m.dir, "openqasm_circuits"); os.makedirs(cd, exist_ok=True)     # the circuit file, placed as keys are
        with open(os.path.join(cd, "01-track.qasm"), "w") as f:
            f.write(_rules().CIRCUIT % ("0.000", "0.000"))
        m.env["FROGNET_OPENQASM_CIRCUITS"] = cd
        m.env["FROGNET_WIRE_BYTES"] = "1"                               # [BYTES_ON_THE_CONNECTION_V1]
        hf = os.path.join(m.dir, "html", "held_fire.php")              # [A_FIRE_REACHES_ITS_HOLDER_V1], as the swap tier
        c = open(hf).read()
        if "const HELD_DIR = '/run/frognet/held';" not in c:
            raise RuntimeError("held_fire.php no longer names its directory as this tier expects")
        open(hf, "w").write(c.replace("const HELD_DIR = '/run/frognet/held';", "const HELD_DIR = '%s';" % m.env["FROGNET_HELD_DIR"]))
    if a.links != "none":
        W.shape_links(machines, a.links, check)
    W.the_merge(net, machines, machines, "the machines found each other; no election yet", roles=False, pause=0)
    time.sleep(1.5)
    for m in machines:
        m.start_wire(START)
    up = [m.ask(None, wait_s=60) for m in machines]

    def answering():
        return [m.ask("req", target=m.ip, method="GET", path=W.RPATH % ("none", 1)).get("status") == 200 for m in machines]
    t_end, ready = time.time() + 60, answering()
    while not all(ready) and time.time() < t_end:
        time.sleep(0.5); ready = answering()
    heard = W.wait_daemons(machines, START)
    check(all(u.get("ready") for u in up) and all(ready) and all(heard.values()), "every site's daemon and proxy are up, told %r" % START)
    W.the_merge(net, machines, machines, "the election ran")
    there = W.wait_for_listeners(machines)
    check(all(there.values()), "every site's daemon is listening on chorus as well as TCP (%s)" % there)
    elect_memories(machines, by, check)
    named = {m.name: all(m.exec(["getent", "hosts", memory(role)]).stdout.split()[:1] == [by[SITES[site]].ip] for role, site in ROLES)
             for m in machines}
    check(all(named.values()), "every site's hosts file names the seven memories where the site table puts them: %s"
          % ", ".join("%s on %s" % (memory(r), SITES[s]) for r, s in ROLES))
    return net, machines, by, tmp


def owner_sets(machines, role, value, wait_s=60):
    """A Role's owner makes one write of that Role's channel setting, from the Role's own site (rule 1: every value
    has one owner, and only the owner writes it), and holds a read on the ends' reports, (channel, at, *), until
    every daemon and proxy holds the setting and every one that carries the Role says it is sending that way.
    Returns (it was so, seconds from the write to the last report, what was seen)."""
    m = [x for x in machines if x.name == SITES[dict(ROLES)[role]]][0]
    r = on(m, "from core import channel_setting as C\n"
              "T, H = C._tuples()\n"
              "value = json.loads(%r)\n"
              "t0 = time.time()\n"
              "ok = C.write(value, ram=%r)\n"
              "pre, last = T.var_name('at', ''), {}\n"
              "def fits(run):\n"
              "    return (run.get('driver') == value.get('driver') and run.get('switches') == int(value.get('switches') or 0)\n"
              "            and bool(run.get('sealed')) == bool(value.get('seed'))\n"
              "            and (not value.get('seed') or run.get('protection') == (value.get('protection') or 'aes-256-gcm')))\n"
              "def done(rows):\n"
              "    at = [r.get('data') or {} for r in rows if str(r.get('SensorName') or '').startswith(pre)]\n"
              "    last['ends'] = len(at)\n"
              "    last['hold'] = sum(1 for d in at if fits((d.get('rams') or {}).get(%r) or {}))\n"
              "    last['carry'] = sum(1 for d in at if %r in (d.get('running') or {}))\n"
              "    last['run'] = sum(1 for d in at if %r in (d.get('running') or {}) and fits(d['running'][%r]))\n"
              "    return last['ends'] >= %d and last['hold'] == last['ends'] and last['run'] == last['carry']\n"
              "rows = H.holder().wait_until(C.SERVICE, C.CONTROL_RAM, done, name_like=T.var_name('at', '%%'), wait_s=%f)\n"
              "print('@@ ' + json.dumps(dict(last, ok=bool(ok), done=done(rows), took=time.time() - t0)))"
           % (json.dumps(value), role, role, role, role, role, 2 * len(machines), wait_s), wait_s=wait_s + 60)
    return bool(r.get("done")), r.get("took"), r


def set_channels(machines, owner, settings, check, why):
    """Each Role's owner writes its channel's setting and waits, by held read, for every end's report."""
    for role, value in settings.items():
        ran, took, saw = owner_sets(machines, role, value)
        check(ran, "%s: %s's owner set its channel to %s; every daemon and proxy held it %.0f ms after the write (%s)"
              % (why, role, json.dumps(value, sort_keys=True), (took or 0) * 1000, {k: saw.get(k) for k in ("ends", "hold", "carry", "run", "error") if k in saw}))
        if not ran:
            return False
    return True


def step_memories(a, check):
    """The design's build step 1 between machines: the Roles' memories elected, each Role's channel set by its owner,
    every memory written and read by its name from every site, a held read across three sites, and ONE connection
    from each site to each other site, carrying every Role there under that Role's own protocol and key."""
    net, machines, by, tmp = sites_up(a, check)
    owner = machines[0].control
    before = pids(machines)
    # two or three Roles on one machine, each under a different setting
    settings = {
        "warning":    {"driver": "tcp"},
        "exercise":   {"driver": "chorus"},
        "assessment": dict({"driver": "chorus"}, **SEALED("assessment")),
        "compute":    dict({"driver": "openqasm"}, **SEALED("compute")),
        "instructor": {"driver": "chorus"},
        "command":    dict({"driver": "tcp"}, **SEALED("command")),
        "units":      {"driver": "tcp"},
    }
    if not set_channels(machines, owner, settings, check, "at the start"):
        return machines
    wrong, n = [], 0
    for rnd in range(3):
        for i, m in enumerate(machines):
            reader = machines[(i + 1) % len(machines)]
            for role, site in ROLES:
                val = {"n": rnd * 100 + i, "from": m.name, "to": role}
                w = on(m, "import frognet_ram as R\nok = R.upsert(%r, 'probe', %r, json.loads(%r), R.DURABLE, dbhost=%r)\nprint('@@ ' + json.dumps({'ok': bool(ok)}))"
                       % (role, m.name, json.dumps(val), memory(role)))
                r = read_tuple(reader, role, "probe", m.name)
                got = [{k: r.get(k) for k in val}]
                n += 1
                if not w.get("ok") or got != [val]:
                    wrong.append((m.name, role, reader.name, w, r))
    check(not wrong, "%d writes, each from one site into a memory by its name and read back at another site: %d failed or wrong%s"
          % (n, len(wrong), "" if not wrong else " (first: %s)" % (wrong[0],)))
    # ---- a read held at one site returns on a write from another, through a memory on a third
    holder, writer = by[SITES["A"]], by[SITES["B"]]
    hp = holder.run([PY, "-c", "import sys, json, time; sys.path.insert(0, %r); sys.path.insert(0, %r)\n"
                     "import frognet_held as H\nt = time.time()\n"
                     "rows = H.holder().wait_until('units', %r, lambda rows: any((r.get('data') or {}).get('n') == 7 for r in rows), name_like='SD:held.%%', wait_s=60)\n"
                     "print('@@ ' + json.dumps({'n': [(r.get('data') or {}).get('n') for r in rows], 'after': time.time() - t}), flush=True)\n"
                     % (SEM, os.path.join(SEM, "core"), memory("units"))], env=holder.env, stdout=__import__("subprocess").PIPE, text=True)
    time.sleep(2.0)                                        # the hold is registered before the write (the tier's own pause; no program waits this way)
    t_w = time.time()
    w = on(writer, "import frognet_ram as R\nok = R.upsert('units', 'held', 'x', {'n': 7}, R.DURABLE, dbhost=%r)\nprint('@@ ' + json.dumps({'ok': bool(ok)}))" % memory("units"))
    out = hp.communicate(timeout=90)[0]
    line = [l for l in out.splitlines() if l.startswith("@@ ")]
    h = json.loads(line[-1][3:]) if line else {"error": out[-300:]}
    check(w.get("ok") and 7 in (h.get("n") or []) and 2.0 <= h.get("after", 0) < 30,
          "a read held on %s at site A (%s) returned %.1f s after it was held, when site B (%s) wrote the value into that memory at site C (%s)"
          % (memory("units"), holder.name, h.get("after", -1), writer.name, SITES["C"], ) if "after" in h else "the held read failed: %s %s" % (h, w))
    # ---- one connection to each destination, and on it each Role under its own setting
    lines = {m.name: open(m.proxy_log, errors="replace").read() for m in machines}
    opened = {m.name: sorted(set(re.findall(r"the connection to (\S+):\d+ is open; it is held open", lines[m.name]))) for m in machines}
    want = {m.name: sorted(o.ip for o in machines if o is not m) for m in machines}
    check(opened == want, "each site's proxy holds ONE connection to each other site and no more: %s" % opened)
    named, bad = {}, {}
    for m in machines:
        for ip, seq, role, how in re.findall(r"to (\S+):\d+, sequence (\d+) is '(\w*)': (\{[^}]*\})", lines[m.name]):
            if role:
                named.setdefault((m.name, ip), {})[role] = how
                st = settings[role]
                if ("'driver': '%s'" % st["driver"]) not in how or ("'sealed': %s" % bool(st.get("seed"))) not in how:
                    bad[(m.name, role)] = how
    shared = {k: sorted(v) for k, v in named.items() if len(v) > 1}
    check(shared and not bad, "on those connections each Role has a sequence of packet IDs of its own, under its own protocol and its own key or none "
          "(e.g. %s to %s carries %s on one connection)%s" % (next(iter(shared))[0] if shared else "?", next(iter(shared))[1] if shared else "?",
                                                              ", ".join(next(iter(shared.values()))) if shared else "?", "" if not bad else "; NOT so: %s" % bad))
    refused = [l for m in machines for f in (m.proxy_log, m.daemon_log) for l in re.findall(r"\[CHANNEL-(?:FRAME|SETTING)\][^\n]*(?:does not open|IGNORED|: refused)[^\n]*", open(f, errors="replace").read())]
    check(not refused, "no frame was refused and no setting ignored%s" % ("" if not refused else ": %s" % refused[:2]))
    after = pids(machines)
    check(before == after and all(p[1] is None and p[3] is None for p in after.values()), "no daemon or proxy restarted or ended")
    return machines


def pids(machines):
    return {m.name: (m.daemon.pid, m.daemon.poll(), m.proxy_main.pid, m.proxy_main.poll()) for m in machines}


def read_tuple(m, role, var, scope):
    """One tuple, read by its own name. (Not a read of the whole service filtered here: every proxy keeps a read cache
    for its own machine's api.php, proxy/local_read_cache.py, which a write of a NEW name does not empty for a broad
    read that was cached before it; its 10 s age limit is the backstop. A raw read handed to the memory's machine in
    that window answers without the new tuple. Found 21 Nov by this tier's first version. A read by name of a tuple
    that is not there is not cached, so this read is of the memory.)"""
    r = on(m, "import urllib.request, urllib.parse, frognet_tuples as T\n"
              "u = 'http://%%s/api.php?entity=sensors&action=values&parse=1&SensorType=%%s&SensorName=%%s' %% (%r, %r, urllib.parse.quote(T.var_name(%r, %r)))\n"
              "rows = json.loads(urllib.request.urlopen(u, timeout=60).read()).get('rows') or []\n"
              "print('@@ ' + json.dumps({'v': [x.get('data') for x in rows]}))" % (memory(role), role, var, str(scope)))
    v = r.get("v") or []
    return v[0] if len(v) == 1 and isinstance(v[0], dict) else r


def circuit(quality, sensors):
    """The design's circuit (10.3) for a track of this quality read by this many sensors: the services' own rule."""
    return _rules().circuit(quality, sensors)


def _rules():
    d = os.path.join(os.path.dirname(os.path.abspath(__file__)), "roles_exercise")
    if d not in sys.path:
        sys.path.insert(0, d)
    import rules
    return rules


def change(machines, owner, role, values, check, label):
    """One change of a Role's channel: ONE write by the Role's owner (the design's 6: a switch is one command).
    Returns the time from the write to every end that carries the Role saying it sends the new way."""
    ran, took, saw = owner_sets(machines, role, values[-1])
    check(ran and len(values) == 1, "%s: one write; every daemon and proxy held it, and the %s that carry %s were sending the new way, %.0f ms after the write"
          % (label, saw.get("carry"), role, (took or 0) * 1000) if ran else "%s: not every end said it runs %s (%s)" % (label, json.dumps(values[-1], sort_keys=True), saw))
    return took if ran else None


PAGES = os.path.join(os.path.dirname(os.path.dirname(SEM)), "opt", "fdf", "src", "fdf-source", "fdf", "www", "roles_exercise")
ADMIN = ("WarningAdmin", "AssessmentAdmin", "ComputeAdmin", "CommandAdmin", "UnitsAdmin", "ExerciseAdmin", "InstructorAdmin")


def place_pages(machines):
    """Each machine's web root is given the pages and their door, as a node's is; the door's copy is told the machine's
    own held directory, as its held_fire.php is."""
    import shutil
    for m in machines:
        d = os.path.join(m.dir, "html", "roles_exercise")
        shutil.copytree(PAGES, d)
        f = os.path.join(d, "roles_ram.php")
        c = open(f).read()
        if "const HELD_DIR = '/run/frognet/held';" not in c:
            raise RuntimeError("roles_ram.php no longer names its directory as this tier expects")
        open(f, "w").write(c.replace("const HELD_DIR = '/run/frognet/held';", "const HELD_DIR = '%s';" % m.env["FROGNET_HELD_DIR"]))


def step_pages(a, check):
    """The design's build step 7: each Role's admin page, and the door they read a Role's memory through."""
    net, machines, by, tmp = sites_up(a, check)
    place_pages(machines)
    owner = machines[0].control
    A, B, C = by[SITES["A"]], by[SITES["B"]], by[SITES["C"]]
    if not set_channels(machines, owner, {"warning": {"driver": "tcp"}, "units": dict({"driver": "chorus"}, **SEALED("units")),
                                          "compute": dict({"driver": "openqasm"}, **SEALED("compute"))}, check, "for the pages"):
        return machines
    door = "http://127.0.0.1:8080/roles_exercise/"
    get = lambda m, path, post=None: on(m, "import urllib.request\ntry:\n    r = urllib.request.urlopen(urllib.request.Request(%r, data=%r, method=%r), timeout=90)\n"
                                           "    b = r.read().decode(); print('@@ ' + json.dumps({'status': r.status, 'body': b}))\n"
                                           "except Exception as e:\n    print('@@ ' + json.dumps({'status': getattr(e, 'code', 0), 'body': str(e)}))"
                                        % (door + path, post.encode() if post else None, "POST" if post else "GET"))
    served = {p: get(B, p + ".html") for p in ADMIN + ("index",)}
    check(all(v.get("status") == 200 and ("Roles Exercise -- " + p in v.get("body", "") or p == "index") for p, v in served.items()),
          "each Role's admin page is served and is named <Role>Admin: %s" % ", ".join(ADMIN))
    words = ("tcp", "chorus", "openqasm") + tuple("%s-%s" % (r, n) for r, _s in ROLES for n in "12")
    named = ["%s: %s" % (f, w) for f in sorted(os.listdir(PAGES)) for w in words if w in open(os.path.join(PAGES, f), errors="replace").read()]
    check(not named, "no admin page, script or door names a protocol or a key%s" % ("" if not named else ": %s" % named))
    # ---- the door: a write from one site, read at another, through each memory's own channel
    for role, writer, reader in (("warning", C, B), ("units", A, B), ("compute", C, A)):
        w = json.loads(get(writer, "roles_ram.php?op=write&role=%s&var=console&scope=probe.1" % role, json.dumps({"n": 7, "from": writer.name})).get("body") or "{}")
        r = json.loads(get(reader, "roles_ram.php?op=read&role=%s&like=SD:console.probe.1" % role).get("body") or "{}")
        got = [t.get("value") for t in r.get("tuples", [])]
        check(w.get("ok") and len(got) == 1 and got[0].get("n") == 7 and got[0].get("from") == writer.name,
              "the door at %s wrote into %s and the door at %s read it back (%s)" % (writer.name, memory(role), reader.name, got))
    # ---- the door's watch is a held read: it answers when the tuple changes, not when its time is up
    first = json.loads(get(A, "roles_ram.php?op=watch&role=units&like=SD:console.probe.1&seen=&wait=5").get("body") or "{}")
    import subprocess
    code = ("import sys, json, time, urllib.request\nt = time.time()\n"
            "j = json.loads(urllib.request.urlopen(%r, timeout=90).read().decode())\n"
            "print('@@ ' + json.dumps({'after': time.time() - t, 'n': [x['value'].get('n') for x in j.get('tuples', [])], 'changed': j.get('seen') != %r}), flush=True)\n"
            % (door + "roles_ram.php?op=watch&role=units&like=SD:console.probe.1&seen=%s&wait=50" % first.get("seen"), first.get("seen")))
    hp = A.run([PY, "-c", code], env=A.env, stdout=subprocess.PIPE, text=True)
    time.sleep(3.0)                                         # the page's read is held before the write (the tier's own pause)
    json.loads(get(C, "roles_ram.php?op=write&role=units&var=console&scope=probe.1", json.dumps({"n": 8, "from": C.name})).get("body") or "{}")
    out = hp.communicate(timeout=120)[0]
    line = [l for l in out.splitlines() if l.startswith("@@ ")]
    h = json.loads(line[-1][3:]) if line else {"error": out[-300:]}
    check(h.get("changed") and 8 in (h.get("n") or []) and 3.0 <= h.get("after", 0) < 20,
          "a page's watch on %s at site A, held for up to 50 s, answered %.1f s after it was made, when site C wrote the tuple: a held read, not a poll (%s)"
          % (memory("units"), h.get("after", -1), h))
    # ---- an operator's input is honoured by the Role's service as an injection is
    sys.path.insert(0, RX)
    import role_service as RS
    r = RS.Role("warning")
    r.mine = {("console", "sensor.2"): {"status": "out", "ts": 1}, ("console", "sensor.3"): {"off": True}}
    check(r.injected("sensor", "2", {"status": "ok", "x": 1}) == {"status": "out", "x": 1} and r.injected("sensor", "3", {"status": "ok"}) == {"status": "ok"},
          "a Role's service takes its admin tool's input for a tuple, and drops it when the tool says off")
    return machines


# ------------------------------------------------------------------------------------------------- the four runs
RX = os.path.join(HERE, "roles_exercise")
SERVICES = (("A", "warning", ["warning.py"]), ("A", "exercise", ["exercise.py"]),
            ("B", "assessment", ["assessment.py"]), ("B", "compute", ["compute_planner.py"]), ("B", "processor1", ["compute_processor.py", "1"]),
            ("C", "command", ["command.py"]), ("C", "units", ["units.py"]), ("C", "processor2", ["compute_processor.py", "2"]))


def run_plan(run):
    """(each Role's channel at the start, the changes of run 4 as (step of 60, Role, label, [the owner's one write])).
    The design's 11.1 and 8: runs 1 to 3 have every channel sealed, so the three protocols are compared like for
    like; run 4 starts mixed and two owners make seven changes, a protocol and an encryption never in one write."""
    roles = [r for r, _s in ROLES]
    if run in (1, 2, 3):
        word = {1: "tcp", 2: "chorus", 3: "openqasm"}[run]
        return {r: dict({"driver": word}, **SEALED(r)) for r in roles}, []
    P = lambda word: {"driver": word}
    start = {"warning": P("tcp"), "exercise": P("chorus"), "assessment": dict(P("chorus"), **SEALED("assessment")),
             "compute": dict(P("openqasm"), **SEALED("compute")), "instructor": P("chorus"),
             "command": dict(P("chorus"), **SEALED("command")), "units": P("tcp")}
    U = lambda **kw: dict(P("chorus"), **kw)
    A = lambda **kw: dict(P("openqasm"), **kw)
    cha = "chacha20-poly1305"
    changes = [
        (15, "units", "Units: the protocol, TCP to Chorus", [U()]),
        (25, "assessment", "Assessment: the protocol, Chorus to OpenQASM", [A(**SEALED("assessment"))]),
        (30, "units", "Units: encryption turned on", [U(**SEALED("units", 1))]),
        (35, "assessment", "Assessment: its key is switched", [A(**SEALED("assessment", 1))]),
        (45, "units", "Units: its key is switched", [U(**SEALED("units", 2))]),
        (50, "assessment", "Assessment: its algorithm is replaced", [A(**SEALED("assessment", 2, cha))]),
        (55, "units", "Units: its algorithm is replaced", [U(**SEALED("units", 3, cha))]),
    ]
    return start, changes


def clock_at(m, step=None, state=None, wait_s=1800):
    """A program on m that holds a read on the exercise clock until it shows this step (or more) or this state."""
    code = ("import frognet_held as H\nfrom frognet_tuples import var_name\nbox = {}\n"
            "def done(rows):\n"
            "    for r in rows:\n"
            "        if r.get('SensorName') == var_name('clock', 'run') and isinstance(r.get('data'), dict):\n"
            "            box.update(r['data'])\n"
            "    return %s\n"
            "t_end = time.time() + %d\n"
            "while time.time() < t_end:\n"
            "    H.holder().wait_until('exercise', 'exerciseHost.frognet', done, name='SD:clock.run', wait_s=300, timeout=120)\n"
            "    if done([]):\n        break\n"
            "print('@@ ' + json.dumps(dict(box, at=time.time())))"
            % (("box.get('state') == %r" % state) if state else ("box.get('state') == 'ended' or (box.get('state') == 'running' and box.get('step', 0) >= %d)" % step), wait_s))
    return on(m, code)


def one_run(a, run, check):
    """One run of the exercise, the design's 13.1. Returns (machines, the exercise record's text, the report)."""
    import subprocess
    net, machines, by, tmp = sites_up(a, check)
    owner = machines[0].control
    site = {s: by[n] for s, n in SITES.items()}
    start, changes = run_plan(run)
    report = {"run": run, "links": a.links, "build": BUILD, "changes": [], "checks": []}
    try:
        if not set_channels(machines, owner, start, check, "run %d, at the start" % run):
            return machines, None, report
        before = pids(machines)
        sc = json.load(open(os.path.join(RX, "scenarios", "first.json")))
        full = sc["steps"]
        if a.steps and a.steps < full:
            sc["steps"] = a.steps
        at = lambda k: k if sc["steps"] == full else max(2, k * sc["steps"] // full)
        scen = os.path.join(tmp, "scenario.json")
        json.dump(sc, open(scen, "w"))
        procs = {}
        place_pages(machines)                                # each Role's admin page, at every site (http://<site>:8080/roles_exercise/)
        t_start = time.time()
        # One service at a time, the Instructor first, each at its initial conditions before the next is started: a
        # service's first reads open a session for each memory it reads, and on a 4,800-baud link nine services
        # opening theirs at once did not get them open (21 Nov: the TCP driver's "no daemon callback after 16s").
        order = (("B", "instructor", ["instructor.py", "serve", scen]),) + tuple(x for x in SERVICES if x[1].startswith("processor")) + tuple(x for x in SERVICES if not x[1].startswith("processor"))
        for s, name, argv in order:
            m = site[s]
            log = open(os.path.join(m.dir, "role-%s.log" % name), "w")
            procs[name] = (m, m.run([PY, os.path.join(RX, argv[0])] + argv[1:], env=m.env, stdout=log, stderr=subprocess.STDOUT), log.name)
            if name == "exercise":
                continue                                     # it is at initial conditions when every other Role is: the clock says so, below
            role, scope = ("compute", name) if name.startswith("processor") else (name, "run")
            g = on(m, "import frognet_held as H\nfrom frognet_tuples import var_name\n"
                      "t = time.time()\nrows = []\n"
                      "while time.time() - t < 600 and not rows:\n"
                      "    rows = H.holder().wait_until(%r, %r, lambda rows: bool(rows), name=var_name('ready', %r), wait_s=300, timeout=120)\n"
                      "print('@@ ' + json.dumps({'ready': bool(rows), 'after': time.time() - t}))" % (role, memory(role), scope))
            if not g.get("ready"):
                check(False, "%s's service is at initial conditions (%s; its log ends: %s)" % (name, g, (open(log.name, errors="replace").read().strip().splitlines() or [""])[-1][:200]))
                return machines, None, report
        c = clock_at(site["B"], state="setup", wait_s=300)
        check(c.get("state") == "setup", "every Role's service is up and at initial conditions (the exercise clock: %s)" % c)
        if c.get("state") != "setup":
            return machines, None, report
        g = on(site["B"], "import subprocess\nr = subprocess.run([sys.executable, %r, 'console', 'start'], capture_output=True, text=True)\nprint(r.stdout.strip().splitlines()[-1] if r.stdout.strip() else '@@ ' + json.dumps({'ok': False, 'err': r.stderr[-300:]}))"
               % os.path.join(RX, "instructor.py"))
        check(g.get("ok"), "the Instructor gave start")
        for k, role, label, writes in changes:
            c = clock_at(owner, step=at(k))
            if c.get("state") != "running":
                check(False, "%s: the exercise was not running at step %d (%s)" % (label, at(k), c)); break
            took = change(machines, owner, role, writes, check, "step %d, %s" % (c.get("step"), label))
            report["changes"].append({"step": c.get("step"), "role": role, "what": label, "writes": len(writes), "seconds": took, "at": c.get("at")})
            if took is None:
                break
        c = clock_at(site["B"], state="ended", wait_s=a.wait)
        t_end = time.time()
        check(c.get("state") == "ended" and c.get("step") == sc["steps"], "the exercise ran its %d steps and ended (%s) in %.0f s" % (sc["steps"], c, t_end - t_start))
        ended = {n: open(p, errors="replace").read() for n, (_m, _p, p) in procs.items()}
        said = {n: re.findall(r"ENDED with the reason: (.*)", t) for n, t in ended.items()}
        check(not any(said.values()), "no Role's service ended with a failure%s" % ("" if not any(said.values()) else ": %s" % {n: v[:1] for n, v in said.items() if v}))
        if c.get("state") != "ended":
            for n, t in ended.items():
                print("     %s: %s" % (n, (t.strip().splitlines() or ["(nothing)"])[-1][:200]))
            return machines, None, report
        # the last counters are written after the clock says ended: the record is read when every service has said so
        done = clock_at(site["B"], state="ended", wait_s=5)
        deadline = time.time() + 60
        while time.time() < deadline and not all("the exercise ended;" in open(p, errors="replace").read() for _m, _p, p in procs.values()):
            time.sleep(0.2)                                  # the tier reading its own machines' logs; no program waits this way
        parts = {}
        for st, m in sorted(site.items()):                    # each site reads the memories it holds: a whole memory crosses no link
            mine = ",".join(r for r, x in ROLES if x == st)
            part = os.path.join(tmp, "record-%s.json" % st)
            g = on(m, "import subprocess\nr = subprocess.run([sys.executable, %r, 'collect', %r, %r], capture_output=True, text=True)\nprint(r.stdout.strip().splitlines()[-1] if r.stdout.strip() else '@@ ' + json.dumps({'ok': False, 'err': r.stderr[-400:]}))"
                   % (os.path.join(RX, "record.py"), part, mine))
            check(g.get("ok"), "site %s (%s) read back the memories it holds: %s" % (st, m.name, g))
            if not g.get("ok"):
                return machines, None, report
            parts.update(json.load(open(part)))
        sys.path.insert(0, RX)
        import record as RECORD
        ex, en = RECORD.build(parts)
        text = json.dumps(ex, sort_keys=True, separators=(",", ":"))
        # ---- criterion 2: no failed request
        counters = {role: v for role, v in en["counters"].items()}
        calls = sum(x["calls"] for v in counters.values() for x in v.values())
        failed = sum(x["failed"] for v in counters.values() for x in v.values())
        check(len([1 for v in counters.values() for _x in v]) == len(procs) and failed == 0,
              "criterion 2: %d services counted %d reads and writes of the Roles' memories, %d failed" % (len(procs), calls, failed))
        # ---- criterion 3: every injected value present from its injection to its release, as the Roles recorded it step by step
        st = ex["steps"]
        inj = {int(k): v for k, v in en["injections"].items()}
        ok3, said3 = True, []
        for n, v in sorted(inj.items()):
            lo, hi = v["from_step"], min(v.get("released", sc["steps"] + 1), sc["steps"] + 1)
            if v["var"] == "sensor":
                good = [st[str(s)]["detection"]["sensors"][v["scope"]] == v["value"]["status"] for s in range(lo, hi)]
            elif v["var"] == "phantom":
                good = [any(t["track"] == 1000 + int(v["scope"]) for t in st[str(s)]["detection"]["tracks"]) for s in range(lo, hi)]
            elif v["var"] == "processor":
                good = [all(j["processor"] != int(v["scope"]) for j in en["assignments"][str(s)]["jobs"]) for s in range(lo, hi)]
            elif v["var"] == "unit":
                good = [st[str(s)]["action"]["units"][v["scope"]]["comms"] == v["value"]["comms"] for s in range(lo, hi)]
            else:
                good = [False]
            after = hi <= sc["steps"]
            ok3 = ok3 and bool(good) and all(good)
            said3.append("%d (%s %s.%s) steps %d to %d: %d of %d%s" % (n, v["role"], v["var"], v["scope"], lo, hi - 1, sum(good), len(good), ", released" if after else ", to the end"))
        check(ok3 and inj, "criterion 3: every injected value was in force at every step from its injection to its release: %s" % "; ".join(said3))
        # ---- criterion 4: nothing restarted
        after_p = pids(machines)
        alive = {n: p.poll() for n, (_m, p, _l) in procs.items()}
        check(before == after_p and all(v is None for v in alive.values()), "criterion 4: no daemon, proxy or Role service restarted or ended")
        # ---- bytes on each Role's channel, from the drivers' own counts, and the three protocols at once
        chan = {}
        stamps = {}
        for m in machines:
            for what, path in (("req", m.proxy_log), ("rep", m.daemon_log)):
                for t, d, ram, n in re.findall(r"\[WIRE-BYTES\] ([0-9.]+) (\S+) (\S+) %s (\d+)" % what, open(path, errors="replace").read()):
                    if float(t) < t_start:
                        continue
                    e = chan.setdefault(ram, {"req": 0, "rep": 0, "req_bytes": 0, "rep_bytes": 0, "drivers": set()})
                    e[what] += 1; e[what + "_bytes"] += int(n); e["drivers"].add(d)
                    if ram != "-" and what == "req":
                        stamps.setdefault(d, []).append(float(t))
        report["channels"] = {ram: dict(v, drivers=sorted(v["drivers"])) for ram, v in sorted(chan.items())}
        if run == 4:
            together = 0
            ts = sorted((t, d) for d, v in stamps.items() for t in v)
            j = 0
            for i in range(len(ts)):
                while ts[i][0] - ts[j][0] > 1.0:
                    j += 1
                if {d for _t, d in ts[j:i + 1]} >= {"tcp", "chorus", "openqasm"}:
                    together += 1
            check(together > 0 and len(report["changes"]) == len(changes),
                  "criterion 5: every end said it runs each of the %d changes, and TCP, Chorus and OpenQASM each carried Roles' requests within the same second (%d such moments)" % (len(changes), together))
        steps_done = [float(t) for t in re.findall(r"\[ROLE exercise\] ([0-9.]+) step \d+ done", ended["exercise"])]
        gaps = sorted(b - c for b, c in zip(steps_done[1:], steps_done))
        report.update({"steps": sc["steps"], "seconds": round(t_end - t_start, 1), "calls": calls, "failed": failed,
                       "step_ms": {"median": round(gaps[len(gaps) // 2] * 1000) if gaps else None, "p95": round(gaps[min(len(gaps) - 1, len(gaps) * 95 // 100)] * 1000) if gaps else None},
                       "took": {role: {k: x.get("took") for k, x in v.items()} for role, v in counters.items()},
                       "score": ex["final"], "record_bytes": len(text), "injections": said3,
                       "builds": sorted({x.get("build") for v in en["present"].values() for x in v.values()} | {BUILD})})
        with open(os.path.join(tmp, "engineering.json"), "w") as f:
            json.dump(en, f, sort_keys=True)
        return machines, text, report
    finally:
        if not a.keep:
            for _m, p, _l in list(locals().get("procs", {}).values()):
                try:
                    p.kill()
                except Exception:
                    pass


def step_runs(a, check):
    runs = [1, 2, 3, 4] if a.run == "all" else [int(a.run)]
    texts, reports, machines = {}, [], []
    base = open(a.baseline).read() if a.baseline else None
    for run in runs:
        print("== run %d: %s" % (run, {1: "every channel on TCP", 2: "every channel on Chorus", 3: "every channel on OpenQASM", 4: "mixed, with changes"}[run]), flush=True)
        if machines:
            W.tear_down(machines)
        machines, text, rep = one_run(a, run, check)
        reports.append(rep)
        if text is None:
            break
        texts[run] = text
        if a.out:
            with open("%s.run%d.record.json" % (a.out, run), "w") as f:
                f.write(text)
        ref = base if base is not None else texts.get(1)
        if ref is not None and (run != 1 or base is not None):
            check(text == ref, "criterion 1: the exercise record of run %d is byte for byte that of run 1 (%d bytes)" % (run, len(text)))
    print("== performance, side by side (%s links, %s steps)" % (a.links, reports[0].get("steps")))
    print("   %-28s %s" % ("", "  ".join("run %-9d" % r["run"] for r in reports)))
    print("   %-28s %s" % ("the run, seconds", "  ".join("%-13s" % r.get("seconds") for r in reports)))
    print("   %-28s %s" % ("a step, median ms", "  ".join("%-13s" % (r.get("step_ms") or {}).get("median") for r in reports)))
    print("   %-28s %s" % ("a step, 95th ms", "  ".join("%-13s" % (r.get("step_ms") or {}).get("p95") for r in reports)))
    print("   %-28s %s" % ("reads and writes / failed", "  ".join("%-13s" % ("%s / %s" % (r.get("calls"), r.get("failed"))) for r in reports)))
    for ram in [channel(r) for r, _s in ROLES] + ["-"]:
        print("   %-28s %s" % ((ram if ram != "-" else "(for no Role: fires)")[:28] + " bytes", "  ".join(
            "%-13s" % (lambda c: "%d %s" % (c["req_bytes"] + c["rep_bytes"], ",".join(c["drivers"])) if c else "-")((r.get("channels") or {}).get(ram)) for r in reports)))
    for r in reports:
        for c in r.get("changes", []):
            print("   run %d, step %s: %s: %d write(s), every end on it %.0f ms after the first" % (r["run"], c["step"], c["what"], c["writes"], (c["seconds"] or 0) * 1000))
    if a.out:
        with open(a.out + ".report.json", "w") as f:
            json.dump(reports, f, indent=1, sort_keys=True, default=str)
    # ---- R25 and R27
    named = []
    for root, _d, files in os.walk(RX):
        for fn in files:
            if fn.endswith((".py", ".json")):
                t = open(os.path.join(root, fn), errors="replace").read()
                named += ["%s: %s" % (fn, w) for w in ("tcp", "chorus", "openqasm", "the seed of", "channel_keys", "aes-256", "chacha20") if w in t]
    check(not named, "R25: nothing under simulation/roles_exercise names a protocol or a key%s" % ("" if not named else ": %s" % named))
    import subprocess
    d = subprocess.run(["git", "-C", SEM, "status", "--porcelain", "--", "discovery", "frognet_route", "../../usr/local/bin", "internet_tunnels_v3"], capture_output=True, text=True)
    check(d.returncode == 0 and not d.stdout.strip(), "R27: discovery and routing are as the starting commit has them (git status of those directories is empty)")
    return machines


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--step", default="memories", choices=["memories", "pages"])
    ap.add_argument("--run", default="", choices=["", "1", "2", "3", "4", "all"], help="the design's four runs: 1 TCP, 2 Chorus, 3 OpenQASM, 4 mixed with changes")
    ap.add_argument("--steps", type=int, default=0, help="run this many of the scenario's 60 steps (the changes of run 4 fall at the same fractions)")
    ap.add_argument("--out", default="", help="write <out>.run<N>.record.json and <out>.report.json")
    ap.add_argument("--baseline", default="", help="a run 1 record to compare with")
    ap.add_argument("--wait", type=int, default=3600)
    ap.add_argument("--links", default="none")
    ap.add_argument("--keep", action="store_true")
    a = ap.parse_args()
    ok = True

    def check(cond, msg):
        nonlocal ok
        ok = ok and bool(cond)
        print("  [%s] %s" % ("PASS" if cond else "FAIL", msg), flush=True)

    need = W.missing()
    if need:
        print("RESULT FAIL: this tier needs " + "; ".join(need)); return 1
    machines = []
    try:
        machines = step_runs(a, check) if a.run else {"memories": step_memories, "pages": step_pages}[a.step](a, check)
    finally:
        if machines and not a.keep:
            W.tear_down(machines)
    print("RESULT %s" % ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
