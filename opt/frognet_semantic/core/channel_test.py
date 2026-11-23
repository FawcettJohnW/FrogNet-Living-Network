#!/usr/bin/env python3
################################################################
#  Copyright (C) 2016-2026 Fawcett Innovations LLC             #
#  SPDX-License-Identifier: GPL-2.0-only                       #
################################################################
"""core/channel_test.py -- the protocol layer's full test for a REAL network: what must happen, and what must not,
with traffic running the whole time.

John, 23 Nov 2026: "a test that proves it works, both positive and negative"; "a summary of the tests that were run
and Pass or Fail for each, as a table"; "Make this a full test. I should be able to change the protocol and force a
change of encryption during the run."

Run it on any node that holds the test Role's own secret:

    /opt/frognet_semantic/venv/bin/python3 /opt/frognet_semantic/core/channel_test.py            (the Role `units`)

It works the way a distributed test on this system must (John, 2026-10-22). Everything goes through the shared
memory. Each participant advertises itself: every daemon and proxy writes its own report tuple, (channel, at, <its
address>:<its process>), and this command picks those up on a held read. The instructions are one tuple this command
owns, the Role's channel setting, which every participant picks up on its own held read. The results come back the
same way, in the reports. Nothing is sent to a node, nothing is polled, and no node is logged into.

WHAT IT DOES, IN ORDER

  A. Who is there. Every end that reports, by node; daemon and proxy on each; every end on this node's build.
  M. The Role's memories. A Role's traffic is traffic to a memory named <role>Host.<app>, which the network's own
     application election places (core/app_hosts.py). This command asks that election for one memory of the Role on
     EVERY OTHER node (one small application each, `protocoltest-<the node's address>`), so the Role's traffic
     crosses the wire from this node to each of them. They are removed at the end.
  T. Traffic. From here on a value is written into each of those memories and read back, about five times a second
     each, until the end. Every request that fails or reads back the wrong value is counted and shown.
  B. POSITIVE: sealing on, a new key, a new key again, another algorithm, another protocol. Each is ONE write; every
     end holds it within the limit (--within, 5 s); traffic goes on under it with nothing lost; and this node's
     proxy and every memory's daemon report that they are sending the Role's traffic that way (sealed, that count).
  C. POSITIVE: the Role's own secret moves one step at each key switch on every end that holds it.
  D. NEGATIVE: a forged setting (a check nobody could have made) is ignored by every end; traffic goes on.
  E. NEGATIVE: a valid setting with one field changed and its old check is ignored.
  F. NEGATIVE: a setting that was valid at a step already passed, written again, is ignored.
  L. YOURS. While traffic runs, press a key in this terminal:
         p  the other protocol        k  a new key        a  the other algorithm
         s  sealing on / off          q  finish
     Each one is one write, timed and checked like the ones above. A change made anywhere else (channel_admin.py on
     another node, an Admin page) is seen here on a held read and timed and checked the same way.
  G. After everything, sealing is turned off by one write. This is what the test leaves: the Role unsealed, its
     secret at the step it reached, the test's memories gone.

It ends with a SUMMARY table, one row per test: Part, Test, Measured, Result. Exit 0 when every check passed.

--no-keys leaves part L out. --keys is for the simulator (the same keys from a list; `e` waits for one change made
elsewhere). --verbose puts the tuple layer's own diagnostic lines back in the output.
"""
import argparse
import contextlib
import io
import json
import os
import queue
import secrets
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

BUILD = "channel-test-v4-20261123d"
passed = failed = 0
rows = []          # the summary table: one row for each test, [part, what was tested, measured, passed]


def test(part, name):
    """Start a row of the summary table: every check until the next row counts toward this one."""
    rows.append([part, name, "", True])


def measured(text):
    if rows:
        rows[-1][2] = (rows[-1][2] + "; " if rows[-1][2] else "") + text


def check(ok, text, detail=""):
    global passed, failed
    passed, failed = passed + bool(ok), failed + (not ok)
    if rows and not ok:
        rows[-1][3] = False
    print("  [%s] %s%s" % ("PASS" if ok else "FAIL", text, "" if ok or not detail else "\n         " + str(detail)[:600]), flush=True)
    return bool(ok)


def summary(role):
    w = max(len(r[1]) for r in rows)
    m = max([len(r[2]) for r in rows] + [8])
    line = "+------+-" + "-" * w + "-+-" + "-" * m + "-+--------+"
    print("\nSUMMARY  %s, the Role %r" % (BUILD, role))
    print(line)
    print("| Part | %s | %s | Result |" % ("Test".ljust(w), "Measured".ljust(m)))
    print(line)
    for part, name, meas, ok in rows:
        print("| %s | %s | %s | %s |" % (part.ljust(4), name.ljust(w), meas.ljust(m), {True: "PASS  ", False: "FAIL  ", None: "SKIP  "}[ok]))
    print(line)
    done = [r for r in rows if r[3] is not None]
    print("%d test(s): %d passed, %d failed%s.  (%d checks: %d passed, %d failed.)"
          % (len(done), sum(1 for r in done if r[3]), sum(1 for r in done if not r[3]),
             ", %d skipped" % (len(rows) - len(done)) if len(rows) != len(done) else "", passed + failed, passed, failed))


class Traffic:
    """The Role's traffic: one thread for each memory, writing a value into it and reading it back, until stopped.
    This is the workload, not a wait: nothing in the test waits by asking again."""

    def __init__(self, fdfram, memories, me):
        self.fdfram, self.memories, self.me = fdfram, memories, me
        self.lock = threading.Condition()
        self.ok = {m: 0 for m in memories}
        self.bad = {m: 0 for m in memories}
        self.slowest = 0.0
        self.said = []                       # the first failures, as they were
        self.cells = {}                      # memory -> the id of this test's cell there (removed at the end)
        self.stop = threading.Event()
        self.threads = [threading.Thread(target=self._run, args=(m,), name="traffic-" + m, daemon=True) for m in memories]

    def start(self):
        for t in self.threads:
            t.start()

    def _run(self, memory):
        ram, n = self.fdfram.RoleRam(memory), 0
        while not self.stop.is_set():
            n += 1
            t0, why = time.time(), ""
            try:
                cell = ram.write("channel-test", "probe", self.me, {"n": n})
                back = [c["bag"].get("n") for c in ram.read("channel-test", "probe", self.me)]
                if back != [n]:
                    why = "wrote %d and read back %s" % (n, back)
                self.cells[memory] = cell
            except Exception as e:
                why = "%s: %s" % (type(e).__name__, e)
            with self.lock:
                self.slowest = max(self.slowest, time.time() - t0)
                if why:
                    self.bad[memory] += 1
                    if len(self.said) < 6:
                        self.said.append("%s %s: %s" % (time.strftime("%H:%M:%S"), memory, why[:200]))
                else:
                    self.ok[memory] += 1
                self.lock.notify_all()
            self.stop.wait(0.2)

    def now(self):
        with self.lock:
            return dict(self.ok), dict(self.bad)

    def more(self, each, wait_s):
        """Wait (woken by the traffic itself) until every memory has `each` more good round trips. Whether it did."""
        with self.lock:
            want = {m: n + each for m, n in self.ok.items()}
            return self.lock.wait_for(lambda: all(self.ok[m] >= want[m] for m in want), wait_s)

    def since(self, mark):
        ok, bad = self.now()
        return sum(ok.values()) - sum(mark[0].values()), sum(bad.values()) - sum(mark[1].values())

    def end(self):
        self.stop.set()
        for t in self.threads:
            t.join(15)
        for memory, cell in self.cells.items():
            try:
                self.fdfram.RoleRam(memory).remove(None, row_id=cell)
            except Exception:
                pass


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="The protocol layer's full test for a real network: positive and negative, with traffic running.")
    ap.add_argument("--role", default="units", help="the Role whose setting is the test's instructions (its own secret must be on this node)")
    ap.add_argument("--within", type=float, default=5.0, help="seconds: every end must act on a write within this (default 5)")
    ap.add_argument("--no-keys", action="store_true", help="leave out the part where you change things from the keyboard")
    ap.add_argument("--keys", default="", help="for the simulator: the keys of part L from this list instead of the keyboard")
    ap.add_argument("--verbose", action="store_true", help="also print the tuple layer's own diagnostic lines ([DIAG-STORE], [HELD])")
    a = ap.parse_args(argv)
    role, within = a.role.lower(), a.within

    if not a.verbose:
        # the tuple layer prints every read and write it makes; that is for a daemon's journal, not for this report
        try:
            from core import frognet_diag
            frognet_diag._ON = False
        except Exception:
            pass
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            from core import channel_setting as _C
            _C._tuples()[1].holder()                 # (its one line saying which socket it holds on)
    from core import app_hosts
    from core import channel_admin as ADM
    from core import channel_frame as F
    from core import channel_setting as C
    T, H = C._tuples()
    pre = T.var_name("at", "")
    mine = C.builds()
    me = T.my_ip()
    print("[CHANNEL-TEST] %s on %s; this node's build: %s" % (BUILD, me, mine))

    try:
        _s, _at0, own = F.read_secret(role)
    except F.DoesNotOpen as e:
        print("RESULT FAIL  this node holds no secret for %r (%s). Make it here with\n"
              "    channel_admin.py secret %s\nand place the same file on the other nodes that are to take part." % (role, e, role))
        return 1
    if not own:
        print("RESULT FAIL  this node has no secret of %r's own (only the network's default, which never moves). Make it "
              "here with\n    channel_admin.py secret %s\nand place the same file on the other nodes that are to take part." % (role, role))
        return 1

    def ends_of(got):
        return {str(r.get("SensorName") or "")[len(pre):]: (r.get("data") or {}) for r in got
                if str(r.get("SensorName") or "").startswith(pre)}

    def about(d):
        return (d.get("rams") or {}).get(role) or {}

    def hold_reports(done, wait_s):
        """One held read on every end's report, until done(ends) or wait_s. Returns (ends, seconds, whether done)."""
        t0, state = time.time(), {}

        def _done(got):
            state["ends"] = ends_of(got)
            return bool(done(state["ends"]))
        until = time.monotonic() + wait_s
        while True:                                  # (held again after a hosts-file event: that is not the answer)
            H.holder().wait_until(C.SERVICE, C.CONTROL_RAM, _done, name_like=T.var_name("at", "%"), wait_s=max(0.0, until - time.monotonic()))
            if done(state.get("ends") or {}) or time.monotonic() >= until:
                break
        return state.get("ends") or {}, time.time() - t0, bool(done(state.get("ends") or {}))

    def hosts():
        try:
            return [l.rstrip("\n") for l in open(os.environ.get("FROGNET_HOSTS") or os.environ.get("FDF_HOSTS") or "/etc/hosts")]
        except OSError:
            return []

    traffic, made, watching, step_left = None, [], threading.Event(), None
    try:
        # -------------------------------------------------------------- A. who is there
        print("\nA. Who is there")
        test("A", "Every node reports: daemon, proxy, one build")
        ends = ADM.reports()
        nodes = {}
        for name in ends:
            ip, _, proc = name.partition(":")
            nodes.setdefault(ip, []).append(proc.rsplit(".", 1)[0])
        check(len(nodes) >= 2, "%d end(s) report, on %d node(s): %s" % (len(ends), len(nodes), ", ".join("%s (%s)" % (ip, "+".join(sorted(p))) for ip, p in sorted(nodes.items()))),
              "a test of a network needs at least two nodes reporting")
        measured("%d ends, %d nodes" % (len(ends), len(nodes)))
        short = sorted(ip for ip, p in nodes.items() if not ({"daemon_main.py", "proxy_main.py"} <= set(p)))
        check(not short, "every node has both its daemon and its proxy reporting", "not both on: %s" % ", ".join(short))
        other = sorted("%s (%s)" % (n, d.get("build") or "says no build: older than this test") for n, d in ends.items() if d.get("build") != mine)
        check(not other, "every end runs this node's build", "; ".join(other))

        # -------------------------------------------------------------- M. the Role's memories, on every other node
        print("\nM. A memory for %s on every other node, placed by the network's own application election" % role)
        test("M", "The election places a memory for the Role on every other node")
        fdfram, _sel = app_hosts._lib()
        ctl, sysr = app_hosts.ip_of(hosts(), app_hosts.CONTROL), app_hosts.ip_of(hosts(), app_hosts.SYSTEM)
        want = {}                                    # memory name -> the node it is to be on
        for ip in sorted(n for n in nodes if n != me):
            app = "protocoltest-" + ip.replace(".", "-")
            # the same two tuples an installed application has (core/app_hosts.py): its configuration in the
            # ControlRam and a machine's offer in the SystemRam. This command writes the offer FOR each node: any
            # node's api.php serves a memory, and the test owns these and removes them.
            made.append((sysr, fdfram.RoleRam(sysr).write(app_hosts.SVC, "can", "%s.%s.%s" % (app, role, ip.replace(".", "-")),
                                                         {"guid": ip.replace(".", "-"), "addr": ip, "serve": [role], "mem_total_kb": 1, "cores": 1})))
            made.append((ctl, fdfram.RoleRam(ctl).write(app_hosts.SVC, "config", app, {"config": json.dumps({"rams": [role]}), "seq": 1})))
            want[app_hosts.host_name(app, role)] = ip

        def placed():
            etc = hosts()
            return {m: app_hosts.ip_of(etc, m) for m in want}
        t0, limit = time.time(), time.time() + 120
        while placed() != want and time.time() < limit:
            # (a held read returns when the hosts file is rewritten: that is the event waited for, not a clock)
            H.holder().wait_until(app_hosts.SVC, ctl, lambda got: placed() == want, name_like="config.%", wait_s=limit - time.time())
        got = placed()
        check(got == want, "this node's hosts file names %d memories for %s, one on each other node, %.1f s after they were asked for"
              % (len(want), role, time.time() - t0),
              "not placed within 120 s: %s. The tunnel daemon's journal says why, on lines with APP_HOST_ELECT."
              % ", ".join("%s (wanted on %s, is %s)" % (m, want[m], got.get(m) or "nowhere") for m in sorted(want) if got.get(m) != want[m]))
        measured("%d memories, %.1f s" % (len(want), time.time() - t0))
        memories = sorted(m for m in want if got.get(m) == want[m])
        if not memories:
            raise RuntimeError("no memory for %s was placed: there is no traffic to test with" % role)

        # -------------------------------------------------------------- T. traffic
        print("\nT. Traffic: a value written into each memory and read back, about five times a second each, until the end")
        test("T", "Traffic to every memory before any change")
        traffic = Traffic(fdfram, memories, me)
        traffic.start()
        flowing = traffic.more(3, 30)
        ok, bad = traffic.now()
        check(flowing and not sum(bad.values()), "every memory answers: %s" % ", ".join("%s %d" % (want[m], ok[m]) for m in memories),
              "; ".join(traffic.said) or "no answer from: %s" % ", ".join(want[m] for m in memories if ok[m] < 3))
        measured("%d round trips, %d failed" % (sum(ok.values()), sum(bad.values())))

        # -------------------------------------------------------------- the instructions
        now = dict(ADM.settings().get(role) or {})
        for k in ("check", "ts"):
            now.pop(k, None)
        driver0 = now.get("driver") or "tcp"
        holders = set()          # ends that hold the Role's own secret: they report a step
        part = ["B,C"]           # the part of the test the next row of the summary belongs to
        ours = set()             # (driver, number, protection) of every setting this command wrote

        def key_of(v):
            return (v.get("driver") or "tcp", int(v.get("number") or 0), (v.get("protection") or "") if v.get("number") else "")

        def carried(value, mark, label):
            """After a setting is held: traffic went on under it, nothing was lost, and the ends that carry the Role's
            traffic (this node's proxy, each memory's daemon) say they are sending it that way."""
            went = traffic.more(2, max(within, 10))
            n, lost = traffic.since(mark)
            check(went and not lost, "%s: traffic to all %d memories went on under it: %d round trips, %d failed" % (label, len(memories), n, lost),
                  "; ".join(traffic.said[-3:]) or "a memory did not answer twice within %.0f s" % max(within, 10))
            measured("traffic %d ok, %d failed" % (n, lost))

            def all_say_so(e):
                carry = [d["running"][role] for d in e.values() if role in (d.get("running") or {})]
                return len(carry) > len(memories) and all(ADM._fits(r, value) for r in carry)
            reps, _secs, said_so = hold_reports(all_say_so, within)
            carry = sorted(n_ for n_, d in reps.items() if role in (d.get("running") or {}))
            wrong = sorted(n_ for n_ in carry if not ADM._fits(reps[n_]["running"][role], value))
            check(said_so, "%s: the %d ends carrying %s's traffic say they are sending it %s"
                  % (label, len(carry), role, ("SEALED, %s, on %s" % (value.get("protection") or "aes-256-gcm", value.get("driver"))) if value.get("number")
                     else "unsealed, on %s" % value.get("driver")),
                  "sending another way: %s" % ", ".join(wrong) if wrong else "only %d end(s) say they carry it; this node's proxy and %d daemons should: %s"
                  % (len(carry), len(memories), ", ".join(carry)))

        def one_write(label, value, key_switch):
            """A valid setting, one write. Every end must hold it within the limit; the secret's holders at the new step."""
            nonlocal now
            test(part[0], "%s: reaches every end, %s" % (label[:1].upper() + label[1:], "secret moves a step" if key_switch else "secret stays"))
            ours.add(key_of(value))
            mark = traffic.now()
            saw = ADM.write_and_wait(role, value, ends=1, wait_s=within)
            measured("%.0f ms, %d ends" % (saw.get("took", 0) * 1000, saw.get("hold", 0)))
            wrote = saw.get("wrote") or value
            late = saw.get("behind") or []
            check(saw.get("ok") and saw.get("done"), "%s: one write, and every end holds it %.0f ms after it (%d hold it%s)"
                  % (label, saw.get("took", 0) * 1000, saw.get("hold", 0),
                     ", %d ignored it for want of this Role's secret" % len(saw.get("ignored") or []) if saw.get("ignored") else ""),
                  "not within %.0f s: %s" % (within, ", ".join(late) or "the write was not taken"))
            reps = ADM.reports()
            step = int(wrote.get("step") or 0)
            at = {n: about(d).get("step") for n, d in reps.items() if about(d).get("step") is not None}
            holders.update(at)
            wrong = sorted("%s at step %s" % (n, s) for n, s in at.items() if int(s) != step)
            check(not wrong and at, "%s: every end that holds %s's secret is at step %d (%d end(s))"
                  % ("the key switch moved the secret" if key_switch else "no key switch, the secret did not move", role, step, len(at)),
                  "; ".join(wrong) or "no end reports a step: no end but this command holds the Role's own secret")
            # an end that holds the Role's own secret (it reports a step) and yet IGNORED a valid setting holds another
            # secret than this node's, or read its own wrongly: either way it is no longer with the network
            deaf = sorted(n for n in (saw.get("ignored") or []) if n in at)
            check(not deaf, "%s: no end that holds %s's secret ignored this valid setting" % (label, role),
                  "IGNORED A VALID SETTING: %s. Its journal says why, on a line with [A_SETTING_PROVES_ITSELF_V1]." % ", ".join(deaf))
            now = {k: v for k, v in wrote.items() if k not in ("check", "ts")}
            carried(now, mark, label)
            return wrote

        def refused(label, forged, why):
            """A setting that must NOT be taken: written as it is. Every holder must say it ignored it and stay put."""
            test(part[0], "NEGATIVE %s: refused by every end"
                 % {"forged": "forged setting", "changed": "setting with a field changed",
                    "taken back": "setting from a step already passed"}.get(label, label))
            ours.add(key_of(forged))
            mark = traffic.now()
            before = {n: about(d).get("step") for n, d in ADM.reports().items() if n in holders}
            ok = T.put(C.SERVICE, C.VAR, role, dict(forged), dbhost=C.CONTROL_RAM, own=False)

            def said(d):
                i = about(d).get("ignored") or {}
                return bool(i) and int(i.get("number") or 0) == int(forged.get("number") or 0) and int(i.get("step") or 0) == int(forged.get("step") or 0)

            def took(d):
                x = about(d)
                return int(x.get("number") or 0) == int(forged.get("number") or 0) and not said(d)
            reps, secs, done = hold_reports(lambda e: all(said(e.get(n) or {}) for n in holders), within)
            measured("%.0f ms, %d ends" % (secs * 1000, len(holders)))
            quiet = sorted(n for n in holders if not said(reps.get(n) or {}))
            check(ok and done, "%s: every end that holds the secret says it IGNORED it, %.0f ms after the write (%d end(s))"
                  % (label, secs * 1000, len(holders)), "did not say so within %.0f s: %s" % (within, ", ".join(quiet)))
            fooled = sorted(n for n in holders if took(reps.get(n) or {}))
            check(not fooled, "%s: no end that holds the secret took it (%s)" % (label, why), "TOOK IT: %s" % ", ".join(fooled))
            moved = sorted("%s %s -> %s" % (n, before.get(n), about(reps.get(n) or {}).get("step")) for n in holders
                           if about(reps.get(n) or {}).get("step") != before.get(n))
            check(not moved, "%s: no end's secret moved" % label, "; ".join(moved))
            went = traffic.more(2, max(within, 10))
            n, lost = traffic.since(mark)
            check(went and not lost, "%s: sealed traffic went on under the setting that stands: %d round trips, %d failed" % (label, n, lost),
                  "; ".join(traffic.said[-3:]))
            measured("traffic %d ok, %d failed" % (n, lost))
            blind = sorted(n for n, d in reps.items() if n not in holders and took(d))
            if blind:
                print("         (%d end(s) hold no secret for %s and took it, having nothing to verify it with: %s)" % (len(blind), role, ", ".join(blind)))

        def number():
            return secrets.randbits(62) + 1

        def other_driver():
            return "chorus" if (now.get("driver") or "tcp") != "chorus" else "tcp"

        def other_algorithm():
            return "chacha20-poly1305" if (now.get("protection") or "aes-256-gcm") != "chacha20-poly1305" else "aes-256-gcm"

        # -------------------------------------------------------------- B and C. positive
        print("\nB, C. Positive: one write reaches every end by its held read, traffic goes on, a key switch moves the secret")
        first = one_write("sealing on (aes-256-gcm)", dict(now, driver=driver0, number=number(), protection="aes-256-gcm"), True)
        one_write("a new key", dict(now, number=number()), True)
        one_write("a new key again", dict(now, number=number()), True)
        one_write("another algorithm (chacha20-poly1305)", dict(now, number=number(), protection="chacha20-poly1305"), True)
        last_valid = one_write("another protocol (%s to %s)" % (now.get("driver"), other_driver()), dict(now, driver=other_driver()), False)
        test("C", "Other nodes hold the Role's own secret")
        others = sorted(n for n in holders if not n.startswith(me + ":"))
        measured("%d ends, %d nodes" % (len(others), len({n.split(":")[0] for n in others})))
        check(others, "ends on OTHER nodes hold %s's secret, so the refusals below are tested on them (%d end(s) on %d node(s))"
              % (role, len(others), len({n.split(":")[0] for n in others})),
              "only this node holds the Role's own secret. Place /etc/frognet/channel_secrets/%s on other nodes and run again." % role)

        # -------------------------------------------------------------- D, E, F. negative
        part[0] = "D"
        print("\nD. Negative: a forged setting (a check nobody could have made), with sealed traffic running")
        refused("forged", dict(now, number=number(), step=int(now.get("step") or 0) + 1, check="0" * 96),
                "its check does not verify under the secret at its step")
        part[0] = "E"
        print("\nE. Negative: a valid setting with one field changed and its old check (sealing taken off)")
        tampered = {k: v for k, v in last_valid.items() if k not in ("number", "protection", "ts")}
        tampered["number"] = 0
        refused("changed", tampered, "the check covers every field")
        part[0] = "F"
        print("\nF. Negative: a setting that WAS valid, at a step the machines have passed, written again with its valid check")
        refused("taken back", {k: v for k, v in first.items() if k != "ts"},
                "its step, %d, is lower than the step the secret is at" % int(first.get("step") or 0))

        # -------------------------------------------------------------- L. yours
        part[0] = "L"
        events = queue.Queue()                       # what happens in part L: ("key", k) and ("elsewhere", setting)
        script = [k for k in a.keys if k.strip() and k != ","]
        if a.no_keys or (not script and not sys.stdin.isatty()):
            print("\nL. (left out: %s)" % ("--no-keys" if a.no_keys else "this is not a terminal, so there are no keys to press"))
        else:
            print("\nL. Yours. Traffic is running to %d nodes. Press:" % len(memories))
            print("       p  the other protocol      k  a new key      a  the other algorithm      s  sealing on / off      q  finish")
            print("   A change made anywhere else (channel_admin.py on another node) is seen here and checked the same way.", flush=True)

            def elsewhere():
                """A held read on the Role's setting: it returns when the setting is one this command did not write."""
                seen = [key_of(now)]

                def changed(got):
                    v = next((r.get("data") or {} for r in reversed(C.oldest_first(got))      # (the newest cell is the setting)
                              if str(r.get("SensorName") or "") == T.var_name(C.VAR, role)), None)
                    if v is None or key_of(v) in ours or key_of(v) == seen[0]:
                        return False
                    seen[0] = key_of(v)
                    events.put(("elsewhere", {k: x for k, x in v.items() if k not in ("check", "ts")}))
                    return True
                while watching.is_set():
                    try:
                        H.holder().wait_until(C.SERVICE, C.CONTROL_RAM, changed, name_like=T.var_name(C.VAR, "%"), wait_s=C.HOLD_S)   # (the hold every end has)
                    except Exception as e:           # said, and held again at the next event (the hosts file, a write)
                        print("   (the read held on %s's setting ended: %s: %s)" % (role, type(e).__name__, e), flush=True)
                        time.sleep(1)
            watching.set()
            threading.Thread(target=elsewhere, name="setting-elsewhere", daemon=True).start()

            def keyboard():
                import termios
                import tty
                fd = sys.stdin.fileno()
                was = termios.tcgetattr(fd)
                try:
                    tty.setcbreak(fd)
                    while watching.is_set():
                        ch = (sys.stdin.read(1) or "q").lower()
                        events.put(("key", ch))
                        if ch == "q":
                            return
                finally:
                    termios.tcsetattr(fd, termios.TCSADRAIN, was)
            if not script:
                threading.Thread(target=keyboard, name="keys", daemon=True).start()
            awaited = False                          # (the simulator's `e`: the next thing is a change made elsewhere)
            while True:
                what, x = ("key", script.pop(0)) if script and not awaited else events.get()
                if what == "elsewhere":
                    awaited = False
                    was = dict(now)
                    said = ", ".join(s for s in (
                        "protocol %s to %s" % (was.get("driver"), x.get("driver")) if was.get("driver") != x.get("driver") else "",
                        ("sealing off" if not x.get("number") else "sealing on" if not was.get("number") else "a new key")
                        if int(was.get("number") or 0) != int(x.get("number") or 0) else "",
                        "algorithm %s" % x.get("protection") if x.get("number") and was.get("number") and was.get("protection") != x.get("protection") else "") if s)
                    test("L", "Changed elsewhere (%s): reaches every end" % (said or "written again"))
                    mark = traffic.now()
                    reps, secs, done = hold_reports(lambda e: all(ADM._fits(about(d), x) or (about(d).get("ignored") or {}).get("number") == x.get("number")
                                                                  for d in e.values()), within)
                    behind = sorted(n for n, d in reps.items() if not ADM._fits(about(d), x))
                    check(done, "changed elsewhere (%s): every end holds it %.0f ms after this node saw it (%d ends)" % (said, secs * 1000, len(reps)),
                          "not within %.0f s: %s" % (within, ", ".join(behind)))
                    measured("%.0f ms, %d ends" % (secs * 1000, len(reps)))
                    now = dict(x)
                    carried(now, mark, "changed elsewhere")
                    continue
                if x == "e":
                    awaited = True
                    T.put("channel-test", "waiting", me, {"for": "a change made elsewhere"}, dbhost=C.CONTROL_RAM)
                    continue
                if x == "q":
                    break
                sealed = bool(now.get("number"))
                if x == "p":
                    one_write("another protocol (%s to %s)" % (now.get("driver"), other_driver()), dict(now, driver=other_driver()), False)
                elif x == "k":
                    one_write("a new key" if sealed else "sealing on (%s)" % (now.get("protection") or "aes-256-gcm"),
                              dict(now, number=number(), protection=now.get("protection") or "aes-256-gcm"), True)
                elif x == "a":
                    one_write("another algorithm (%s)" % other_algorithm(), dict(now, number=number(), protection=other_algorithm()), True)
                elif x == "s":
                    if sealed:
                        one_write("sealing off", {"driver": now.get("driver"), "step": now.get("step")}, False)
                    else:
                        one_write("sealing on (aes-256-gcm)", dict(now, number=number(), protection="aes-256-gcm"), True)
                else:
                    print("   (%r is not one of p, k, a, s, q)" % x, flush=True)
            watching.clear()

        # -------------------------------------------------------------- G. it still works, and what is left
        part[0] = "G"
        print("\nG. Positive: after everything, one valid write (sealing off) reaches every end, and traffic goes on")
        one_write("sealing off, back on %s" % driver0, {"driver": driver0, "step": now.get("step")}, False)

        test("T", "Traffic through the whole run")
        ok, bad = traffic.now()
        check(not sum(bad.values()), "through the whole run: %d round trips to %d nodes, %d failed, the slowest %.0f ms"
              % (sum(ok.values()), len(memories), sum(bad.values()), traffic.slowest * 1000), "; ".join(traffic.said))
        measured("%d round trips, %d failed, slowest %.0f ms" % (sum(ok.values()), sum(bad.values()), traffic.slowest * 1000))
        step_left = now.get("step")
    except KeyboardInterrupt:
        test("-", "The test itself")
        check(False, "the test ran to its end", "stopped from the keyboard: %s's setting is left as it was at that moment" % role)
    except Exception as e:
        test("-", "The test itself")
        check(False, "the test ran to its end", "%s: %s" % (type(e).__name__, e))
    finally:
        watching.clear()
        if traffic is not None:
            traffic.end()
        gone = 0
        for host, cell in made:                      # the test's memories: their offers and their configurations
            try:
                gone += app_hosts._lib()[0].RoleRam(host).remove(None, row_id=cell)
            except Exception as e:
                print("   (a test tuple was not removed from %s: %s)" % (host, e))
        if made:
            test("M", "The test's memories are given back")
            ctl_ = app_hosts.ip_of(hosts(), app_hosts.CONTROL)

            def named():
                return [l.split()[1] for l in hosts() if len(l.split()) > 1 and "Host.protocoltest-" in l.split()[1]]
            t0, limit = time.time(), time.time() + 120
            while named() and time.time() < limit:       # (the event waited for is the hosts file being rewritten)
                H.holder().wait_until(app_hosts.SVC, ctl_, lambda got_: not named(), name_like="config.%", wait_s=limit - time.time())
            print("\nM. The test's memories")
            check(gone == len(made) and not named(), "the test's %d tuples are removed and the election has taken its %d names out of this node's hosts file, %.1f s after"
                  % (len(made), len(made) // 2, time.time() - t0),
                  "%d of %d tuples removed; still named here: %s" % (gone, len(made), ", ".join(named()) or "none"))
            measured("%d tuples, %.1f s" % (gone, time.time() - t0))

    summary(role)
    print("\nRESULT %s  %d passed, %d failed.  %s%s"
          % ("PASS" if not failed else "FAIL", passed, failed,
             "%s's setting is left unsealed, at step %s." % (role, step_left) if step_left is not None else "The test did not reach its end.",
             "" if not failed else "  Each FAIL line above names the ends it is about."))
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
