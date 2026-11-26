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
worldshepherd_acceptance.py - [WS_ACCEPTANCE_V1] the acceptance study Brandon Davis (WorldShepherd R&D) asked for on
2026-11-25, in the simulator: continuity of SARA's read-ticket ledger on the memory when the memory is replaced, a
link splits, an issuer is absent or a writer is late, with what he asked to have measured; and the access-control
negative controls, made through the real RAM API.

What is real: the app's RAM host (ribbit/examples/worldshepherd/bin/worldshepherd-ram), RibbitClaimStore, and SARA's
own contract (Sara_pro, the SharedClaimStore API: register, claim, status). What is simulated is the network
(slowlink.py): every SARA is its own process on its own shaped link, arriving at the RAM from its own address.

  the memory is replaced   The RAM host is killed and a new one, with an EMPTY memory, is started on the listening
                           socket the study holds -- what fdf-app does when the AppHost moves or its RAM host dies
                           ([LISTENING_SOCKET_OUTLIVES_THE_HOST_V1]). The election itself, with fdf-app on three
                           machines, is harness/test_the_apphost_moves.py.
  a split                  One SARA's link goes down: its session ends, and its connections are not answered until
                           the link is back.
  two memories             A second RAM host with an empty memory; some SARAs' links lead to it (an election on
                           each side of a partition), and later back.

The cases (each ticket id is used once; K tickets a case, R repetitions of each injection):
  baseline                    no injection: what a claim costs on this link
  restart:before-claims         the memory is replaced between issue and claim
  restart:claims-in-flight    ... while claims are in flight
  restart:reassertion         ... and replaced again while the SARAs are writing their own truth into the new one
  restart:completion          ... after every ticket is consumed; every SARA then claims every ticket again
  split:issuer                the issuer's link is down between issue and claim
  split:claimant-in-flight    a claimant's link goes down with its claims in flight, and comes back
  split:winner+restart        the winner's link goes down, the others see it leave, and then the memory is
                              replaced; it returns 3 s after
  access:*                    negative controls on the real API from another address and another session: the
                              ticket's operations called in another SARA's name; a claim of another digest, past
                              expiry, or on a consumed ticket; an issue of a ticket already issued; the ticket's own
                              cell written from the network; a call with no session; a malformed call. Each passes
                              when the ticket stands afterwards exactly as it did before
  access:restored-winner(*)   in a NEW memory, before the winner returns, a member says it held the ticket before.
                              issuer-saw: the issuer saw the win on its held read and says so when it issues
                              the ticket again -- must PASS. issuer-did-not-see: its link was down while the
                              ticket was won; nobody left can say, reported OPEN
  storage                     cells and the RAM host's memory per ticket and per claim, and what a replaced memory holds
  long (310 s; --skip-long)   two memories for the contract's whole MAX_TTL_SECONDS: the issuer is absent from one;
                              a writer's link is down past the ticket's life and its held claim arrives after

It measures, for every case: claims answered and raised, tickets won twice (duplicate wins), tickets issued and
claimed that nobody won (lost), how long a call was held (the longest, and the claim's p50/p95/p99), and recovery:
for each call in flight at the injection (or, for a SARA with none, its first call after), the time from the
injection until it was answered -- p50/p95/p99/max. One row a case in --out's JSONL; one line a case here.
It fails when any ticket is won twice, any call raises, or a negative control is accepted. A lost ticket fails too.

usage (from opt/frognet_semantic):
  python3 simulation/worldshepherd_acceptance.py --sara DIR [--link lan wan-100ms] [--reps 3] [--skip-long] [--only NAME...] [--out DIR]
  DIR (--sara): Sara_pro at worldshepherd/connectors-distributed-claim-contract-v1-20260913. The example must be built.
"""
import argparse
import hashlib
import json
import os
import random
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
import slowlink as SL                         # noqa: E402

BUILD = "worldshepherd-acceptance 2026-11-26e [WS_ACCEPTANCE_V1] for worldshepherd-ribbit 0.11"
EXAMPLE = os.path.normpath(os.path.join(_HERE, "..", "ribbit", "examples", "worldshepherd"))
FDF_PY = os.path.normpath(os.path.join(_HERE, "..", "..", "fdf", "src", "fdf-source", "fdf", "lib", "python"))
os.environ.setdefault("FDF_PY", FDF_PY)
os.environ["KARTRAM_LIB"] = os.path.join(EXAMPLE, "lib")
sys.path.insert(0, os.path.join(EXAMPLE, "lib"))
RAM = os.environ.get("WS_RAM_HOST") or os.path.join(EXAMPLE, "bin", "worldshepherd-ram")
API = "/FawcettInnovations.RINA.ram_interface.php"
S, SVC = "ws.accept", "worldshepherd"
NAMES = ["issuer", "a", "b", "c", "d"]        # d: the late writer of the long case; idle before it
CLAIMANTS = ["a", "b", "c"]


def sha_of(t):
    return hashlib.sha256(t.encode()).hexdigest()


def pct(xs, p):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, (p * len(xs)) // 100)] if xs else None


def ms(x):
    return None if x is None else round(1000 * x, 1)


def listening(addr):
    s = socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind((addr, 0)); s.listen(1024); s.set_inheritable(True)
    return s


class Study:
    def __init__(self, link, sara, out, tag=""):
        import ribbit
        self.ribbit, self.link, self.out = ribbit, link, out
        self.tag = link + tag                                   # in every log's name: a second study of a link keeps the first's logs
        os.makedirs(os.path.join(out, "blob_cache"), exist_ok=True)   # where the wire keeps a large body; a node has /opt/frognet_semantic/blob_cache
        self.env = dict(os.environ, FROGNET_BLOB_ROOT=os.path.join(out, "blob_cache"), PYTHONPATH=os.pathsep.join([os.path.join(EXAMPLE, "harness"), EXAMPLE, os.path.join(EXAMPLE, "lib"), FDF_PY, sara]
                                                                    + [p for p in os.environ.get("PYTHONPATH", "").split(os.pathsep) if p]))   # the caller's own, kept: where SARA's packages are
        self.ls = {"A": listening("127.0.0.1"), "B": listening("127.0.0.1")}       # the sockets that outlive their RAM hosts
        self.at = {k: ("127.0.0.1", s.getsockname()[1]) for k, s in self.ls.items()}
        self.rams, self.starts = {}, 0
        self.start_ram("A")
        tl = listening("127.0.0.1"); self.test_at = tl.getsockname()                # the study's own memory: never stopped
        self.test_ram = subprocess.Popen([RAM, "--listen", "%s:%d" % self.test_at, "--api", API], env=dict(self.env, RIBBIT_LISTEN_FD=str(tl.fileno())),
                                         pass_fds=(tl.fileno(),), stdout=subprocess.DEVNULL, stderr=open(os.path.join(out, "test-ram-%s.log" % self.tag), "w"))
        self.ctl = ribbit.Memory(self.test_at[0], self.test_at[1], API)
        self.last, self.n, self.done = 0, 0, {}                  # done: answers read before they were waited for
        self.relays = {n: SL.Relay(self.at["A"], SL.profile(link), seed=i, name=n, source="127.0.1.%d" % (10 + i)) for i, n in enumerate(NAMES)}
        self.side = {n: "A" for n in NAMES}
        self.kids = {}
        for n in NAMES:
            log = open(os.path.join(out, "sara-%s-%s.log" % (n, self.tag)), "w")
            self.kids[n] = subprocess.Popen([sys.executable, os.path.join(EXAMPLE, "harness", "acceptance_sara.py"), "%s:%d" % self.test_at,
                                             "--store-ram", self.relays[n].where, "--name", n], env=self.env, stdout=log, stderr=subprocess.STDOUT)
        self.who = {}
        deadline = time.time() + 60
        while len(self.who) < len(NAMES):                                             # held: each SARA says it is here
            for c in self.ctl.read(S, "can", "", after=self.last, wait_s=5.0):
                self.last = max(self.last, c["id"])
                if c["bag"]: self.who[c["instance"]] = c["bag"]["who"]
            self.alive()
            if time.time() > deadline:
                raise RuntimeError("the SARAs did not all say they are here: %s (logs in %s)" % (sorted(self.who), out))

    # ---- the RAM hosts
    def start_ram(self, k):
        self.starts += 1
        log = open(os.path.join(self.out, "worldshepherd-ram-%s-%s.log" % (k, self.tag)), "a")
        ls = self.ls[k]
        self.rams[k] = subprocess.Popen([RAM, "--listen", "%s:%d" % self.at[k], "--api", API], env=dict(self.env, RIBBIT_LISTEN_FD=str(ls.fileno())),
                                        pass_fds=(ls.fileno(),), stdout=log, stderr=subprocess.STDOUT)

    def replace(self, k="A"):
        """the memory is replaced: the RAM host is killed and a new one, empty, is on its listening socket. When."""
        t = time.time()
        self.rams[k].kill(); self.rams[k].wait()
        self.start_ram(k)
        return t

    def rss_kb(self, k="A"):
        for ln in open("/proc/%d/status" % self.rams[k].pid):
            if ln.startswith("VmRSS:"): return int(ln.split()[1])

    def cells(self, k="A"):
        m = self.ribbit.Memory(self.at[k][0], self.at[k][1], API)
        try:
            return [c for c in m.read(SVC) if c["bag"]]
        finally:
            m.close()

    def to_side(self, name, k):
        """NAME's link now leads to memory K: its session over the old way ends, and its next one arrives there"""
        r = self.relays[name]; r.target = self.at[k]; self.side[name] = k
        r.cut(); r.restore()

    # ---- what the SARAs are asked, and what they answer (cells in the study's own memory; held, never polled)
    def alive(self):
        for n, k in self.kids.items():
            if k.poll() is not None:
                raise RuntimeError("SARA %s ended (exit %s): %s" % (n, k.returncode, open(os.path.join(self.out, "sara-%s-%s.log" % (n, self.tag))).read()[-600:]))

    def ask(self, name, calls, threads=4):
        self.n += 1
        self.ctl.write(S, "do." + name, str(self.n), {"calls": calls, "threads": threads})
        return self.n

    def answers(self, asked, limit=120.0):
        """{name: rows} for {name: n} asked"""
        got, deadline = {}, time.time() + limit
        for name, n in asked.items():
            if (name, n) in self.done: got[name] = self.done.pop((name, n))
        while len(got) < len(asked):
            left = deadline - time.time()
            if left <= 0:
                silent = sorted(set(asked) - set(got))
                for n in silent:                                  # where each of its threads is, into its own log
                    self.kids[n].send_signal(signal.SIGUSR1)
                time.sleep(0.5)
                tails = "; ".join("%s: %s" % (n, " | ".join(ln.strip() for ln in open(os.path.join(self.out, "sara-%s-%s.log" % (n, self.tag))).read().splitlines()
                                                           if ("File" in ln and "worldshepherd" in ln) or "WS-ATTACH" in ln)[-700:]) for n in silent)
                raise RuntimeError("no answer within %d s from %s (logs in %s). %s" % (limit, silent, self.out, tails))
            for c in self.ctl.read(S, "", "", after=self.last, wait_s=min(5.0, left)):
                self.last = max(self.last, c["id"])
                v = c.get("variable", "")
                if v.startswith("done.") and c["bag"]:
                    if asked.get(v[5:]) == int(c["instance"]): got[v[5:]] = c["bag"]["r"]
                    else: self.done[(v[5:], int(c["instance"]))] = c["bag"]["r"]
            self.alive()
        return got

    def issue(self, tickets, life=120.0, by="issuer"):
        exp = time.time() + life
        return self.answers({by: self.ask(by, [["issue", t, sha_of(t), exp] for t in tickets])})[by]

    def claims(self, tickets, who=CLAIMANTS, threads=4):
        asked = {}
        for n in who:
            mine = list(tickets); random.Random("%s%s" % (n, tickets[0])).shuffle(mine)
            asked[n] = self.ask(n, [["claim", t, sha_of(t)] for t in mine], threads)
        return asked

    def close(self):
        for n in NAMES:
            try: self.ctl.write(S, "do." + n, str(10 ** 9), {"end": 1})
            except Exception: pass
        time.sleep(0.3)
        for k in list(self.kids.values()) + list(self.rams.values()) + [self.test_ram]:
            if k.poll() is None: k.kill()
        for r in self.relays.values(): r.close()


# ---------------------------------------------------------------- what a case measured
def measure(rows_by, tickets, injected=None, healed=None, expect_lost=()):
    """rows_by: {sara: [[op, ticket, ok, reason, t0, t1, err], ...]} for the whole case"""
    rows = [(n, r) for n, rs in rows_by.items() for r in rs]
    claims = [(n, r) for n, r in rows if r[0] == "claim"]
    wins = {t: [n for n, r in claims if r[1] == t and r[2]] for t in tickets}
    claimed = {r[1] for _, r in claims}
    raised = [(n, r[0], r[1], r[6]) for n, r in rows if r[6]]
    dur = [r[5] - r[4] for _, r in claims]
    reasons = {}
    for _, r in claims:
        k = "won" if r[2] else (r[3] or r[6].split(":")[0] or "?")
        reasons[k] = reasons.get(k, 0) + 1
    rec = []
    if injected is not None:
        for n in rows_by:
            mine = sorted((r for r in rows_by[n] if r[0] == "claim"), key=lambda r: r[4])
            flight = [r for r in mine if r[4] <= injected <= r[5]]
            after = [r for r in mine if r[4] > injected][:1]
            rec += [r[5] - injected for r in (flight or after)]
    return {"tickets": len(tickets), "claims": len(claims), "answered": len(claims) - sum(1 for _, r in claims if r[6]), "raised": len(raised),
            "raised_first": ["%s %s %s: %s" % x for x in raised[:2]],
            "won_once": sum(1 for t in tickets if len(wins[t]) == 1), "duplicate_wins": sum(1 for t in tickets if len(wins[t]) > 1),
            "lost_tickets": sum(1 for t in tickets if t in claimed and not wins[t] and t not in expect_lost), "reasons": reasons,
            "claim_ms_p50": ms(pct(dur, 50)), "claim_ms_p95": ms(pct(dur, 95)), "claim_ms_p99": ms(pct(dur, 99)), "hold_ms_max": ms(max(dur) if dur else None),
            "recovery_n": len(rec), "recovery_ms_p50": ms(pct(rec, 50)), "recovery_ms_p95": ms(pct(rec, 95)), "recovery_ms_p99": ms(pct(rec, 99)),
            "recovery_ms_max": ms(max(rec) if rec else None), "recovery_ms_each": sorted(ms(x) for x in rec),
            "outage_ms": ms(healed - injected) if healed and injected else None}


def merge(a, b):
    out = {n: list(rs) for n, rs in a.items()}
    for n, rs in b.items(): out.setdefault(n, []).extend(rs)
    return out


class Report:
    def __init__(self, path, pins):
        self.f, self.fails, self.opens, self.pins = open(path, "a"), [], [], pins

    def row(self, link, case, verdict, m, note=""):
        self.f.write(json.dumps(dict(m, link=link, case=case, verdict=verdict, note=note, at=round(time.time(), 1), **self.pins)) + "\n"); self.f.flush()
        if verdict == "FAIL": self.fails.append("%s %s" % (link, case))
        if verdict in ("OPEN", "FINDING"): self.opens.append("%s %s" % (link, case))
        keys = ("tickets", "claims", "answered", "raised", "won_once", "duplicate_wins", "lost_tickets")
        head = " ".join("%s %s" % (k.replace("_", " "), m[k]) for k in keys if k in m)
        tail = ""
        if m.get("claim_ms_p50") is not None:
            tail = "; claim p50/p95/p99 %s/%s/%s ms, longest held %s ms" % (m["claim_ms_p50"], m["claim_ms_p95"], m["claim_ms_p99"], m["hold_ms_max"])
        if m.get("recovery_n"):
            tail += "; recovery (%d calls) p50/p95/p99/max %s/%s/%s/%s ms" % (m["recovery_n"], m["recovery_ms_p50"], m["recovery_ms_p95"], m["recovery_ms_p99"], m["recovery_ms_max"])
        print("%-7s %-9s %-26s %s%s%s" % (verdict, link, case, head, tail, ("; " + note) if note else ""), flush=True)


def verdict_of(m, more_ok=True):
    return "PASS" if (m["duplicate_wins"] == 0 and m["raised"] == 0 and m["lost_tickets"] == 0 and more_ok) else "FAIL"


# ---------------------------------------------------------------- the cases
def continuity(st, rp, K, reps, only):
    link = st.link
    seq = [0]
    def tickets(tag, n=K):
        seq[0] += 1
        return ["%s-%s-%d-%04d" % (link, tag, seq[0], k) for k in range(n)]
    def want(name): return not only or any(name.startswith(o) for o in only)

    # baseline: what a claim costs here, and how long a round of claims takes (the injections are timed from it)
    T = tickets("base"); st.issue(T)
    t0 = time.time(); rows = st.answers(st.claims(T)); round_s = time.time() - t0
    m = measure(rows, T); rp.row(link, "baseline", verdict_of(m), m, "a round of claims took %.0f ms" % (1000 * round_s))
    into = max(0.02, 0.35 * round_s)

    def repeated(name, one):
        if not want(name): return
        allrows, ms_ = {}, []
        for r in range(reps):
            T, rows, injected, healed, lost = one(r)
            ms_.append(measure(rows, T, injected, healed, lost))
            allrows = merge(allrows, {"%s#%d" % (n, r): rs for n, rs in rows.items()})
        tot = {k: sum(x[k] for x in ms_) for k in ("tickets", "claims", "answered", "raised", "won_once", "duplicate_wins", "lost_tickets", "recovery_n")}
        dur = [r[5] - r[4] for rs in allrows.values() for r in rs if r[0] == "claim"]      # the percentiles are over every repetition's calls together
        rec = sorted(x for m1 in ms_ for x in m1["recovery_ms_each"])
        tot.update(claim_ms_p50=ms(pct(dur, 50)), claim_ms_p95=ms(pct(dur, 95)), claim_ms_p99=ms(pct(dur, 99)), hold_ms_max=ms(max(dur)),
                   recovery_ms_p50=pct(rec, 50), recovery_ms_p95=pct(rec, 95), recovery_ms_p99=pct(rec, 99), recovery_ms_max=rec[-1] if rec else None,
                   recovery_ms_each=rec, outage_ms=ms_[0]["outage_ms"], reps=reps, raised_first=[x for m1 in ms_ for x in m1["raised_first"]][:3],
                   reasons={k: sum(m1["reasons"].get(k, 0) for m1 in ms_) for m1 in ms_ for k in m1["reasons"]})
        rp.row(link, name, verdict_of(tot), tot, (tot["raised_first"][0] if tot["raised_first"] else ""))

    def restart_issue_claim(r):
        T = tickets("ric"); st.issue(T)
        inj = st.replace()
        return T, st.answers(st.claims(T)), inj, inj, ()
    repeated("restart:before-claims", restart_issue_claim)

    def restart_in_flight(r):
        T = tickets("rif"); st.issue(T)
        asked = st.claims(T); time.sleep(into)
        inj = st.replace()
        return T, st.answers(asked), inj, inj, ()
    repeated("restart:claims-in-flight", restart_in_flight)

    def restart_reassertion(r):
        T = tickets("rre"); st.issue(T)
        first = st.answers(st.claims(T[:len(T) // 2]))                     # winners exist: they have truth to write again
        inj = st.replace()
        time.sleep(max(0.01, 1.5 * 2 * SL.profile(link).latency_ms / 1000.0))   # the SARAs are attaching and writing
        again = st.replace()
        rows = merge(first, st.answers(st.claims(T)))
        return T, rows, inj, again, ()
    repeated("restart:reassertion", restart_reassertion)

    def restart_completion(r):
        T = tickets("rco"); st.issue(T)
        first = st.answers(st.claims(T))
        inj = st.replace()
        rows = merge(first, st.answers(st.claims(T)))                      # every SARA claims every ticket again
        return T, rows, inj, inj, ()
    repeated("restart:completion", restart_completion)

    def split_issuer(r):
        T = tickets("sis"); st.issue(T)
        inj = time.time(); st.relays["issuer"].cut(waiting=True)
        asked = st.claims(T)
        threading.Timer(2.0, st.relays["issuer"].restore).start()
        rows = st.answers(asked); time.sleep(max(0, inj + 2.3 - time.time()))
        return T, rows, inj, inj + 2.0, ()
    repeated("split:issuer", split_issuer)

    def split_claimant(r):
        T = tickets("scl"); st.issue(T)
        asked = st.claims(T); time.sleep(into)
        inj = time.time(); st.relays["a"].cut(waiting=True)
        time.sleep(2.0); st.relays["a"].restore()
        return T, st.answers(asked), inj, inj + 2.0, ()
    repeated("split:claimant-in-flight", split_claimant)

    def split_winner_restart(r):
        T = tickets("swr"); st.issue(T)
        first = st.answers(st.claims(T, who=["a"]))                        # a wins every ticket
        st.relays["a"].cut(waiting=True)
        time.sleep(0.2)                                                    # the others see a leave the memory, and then:
        inj = st.replace()
        asked = st.claims(T, who=["b", "c"])                               # they wait for a: it left less than a ticket's life ago
        time.sleep(3.0); st.relays["a"].restore()
        rows = merge(first, st.answers(asked))
        return T, rows, inj, inj + 3.0, ()
    repeated("split:winner+restart", split_winner_restart)


def access(st, rp):
    """the negative controls, through the real API of the app's RAM host, from another address and another session"""
    link, ribbit = st.link, st.ribbit
    X = ["%s-acc-%d" % (link, k) for k in range(8)]
    st.issue(X); now = time.time()
    st.answers({"a": st.ask("a", [["claim", X[1], sha_of(X[1])]])})          # X1: consumed by a
    victim = st.who["a"]
    other = SL.Relay(st.at["A"], SL.profile("lan"), seed=77, name="other", source="127.0.1.99")
    same = SL.Relay(st.at["A"], SL.profile("lan"), seed=78, name="same", source=st.relays["a"].source)
    host, port = st.at["A"]

    def landed(m, family, instance, bag):
        try:
            m.write_landed(SVC, family, instance, bag); return ""
        except ribbit.RibbitError as e:
            return str(e).split("HTTP 403: ", 1)[-1][:150]
    def there(family, instance):
        return any(c["variable"] == family and c["instance"] == instance for c in st.cells())
    def stands(ticket):                                                       # the ticket's own cell: who issued it, who won it
        cells = st.cells()                                                    # its two cells: that it was issued, that it was won
        for c in cells:
            if c["variable"] == "ticket" and c["instance"] == ticket and c["bag"]:
                won = [w["bag"]["won"] for w in cells if w["variable"].startswith("won.") and w["instance"] == ticket and w["bag"]]
                return (c["bag"]["sha"], c["bag"]["by"], won[0] if won else "")
    def control(case, refused_why, present, more=""):
        ok = bool(refused_why) and not present
        rp.row(link, case, "PASS" if ok else "FAIL", {}, ("refused: " + refused_why if refused_why else "ACCEPTED") + ("; the cell is in the memory" if present else "") + more)
    def called(case, m, op, ticket, **body):
        """one of the ticket's operations, tried by someone it must not serve: PASS when the ticket stands as it did"""
        was = stands(ticket)
        try:
            r = m.call("POST", API + "?op=" + op, json.dumps(dict(body, ticket=ticket)))
            said = "answered " + str(r.get("outcome") or ("ok" if r.get("ok") else r.get("error", r)))[:140]
            took = r.get("outcome") == "claimed" or (op == "issue" and (r.get("state") or {}).get("by") == body.get("who"))
        except ribbit.RibbitError as e:
            said, took = "refused: " + str(e).split(": ", 2)[-1][:150], False
        now_ = stands(ticket)
        rp.row(link, case, "PASS" if (now_ == was and not took) else "FAIL", {}, "%s; the ticket stands as it did" % said if now_ == was else "%s; THE TICKET CHANGED: %r -> %r" % (said, was, now_))

    session = lambda relay: ribbit.Memory(relay.where.rsplit(":", 1)[0], int(relay.where.rsplit(":", 1)[1]), API)
    o = session(other)
    imp = "impostor-0001"
    # in another SARA's name, from another machine: the ticket's operation, and the one cell a SARA writes
    called("access:other-name(claim)", o, "claim", X[2], who=victim, sha=sha_of(X[2]), at=now, n=9001)
    called("access:other-name(held-claim)", o, "claim", X[2], who=victim, sha=sha_of(X[2]), at=now, n=9002, held=1)
    control("access:other-name(here)", landed(o, "sara", victim, {"x": 1}), False)
    # the ticket's own cell, and any other cell in the service, written from the network
    control("access:ticket-cell", landed(o, "ticket", X[2], {"sha": sha_of(X[2]), "exp": now + 250, "by": imp, "bn": 1}), stands(X[2])[1] == imp)
    control("access:won-cell", landed(o, "won." + st.who["issuer"], X[2], {"won": imp, "wn": 1, "at": now}), stands(X[2])[2] == imp)
    control("access:not-a-family", landed(o, "junk", "x", {"a": 1}), there("junk", "x"))
    # in its own name: a claim the ticket does not take
    called("access:claim-other-digest", o, "claim", X[3], who=imp, sha="00" * 32, at=now, n=1)
    called("access:claim-past-expiry", o, "claim", X[3], who=imp, sha=sha_of(X[3]), at=now + 10 ** 6, n=2)
    called("access:claim-on-consumed", o, "claim", X[1], who=imp, sha=sha_of(X[1]), at=now, n=3)
    called("access:held-claim-on-consumed", o, "claim", X[1], who=imp, sha=sha_of(X[1]), at=now, n=4, held=1)
    called("access:malformed-claim", o, "claim", X[4], who=imp, sha=sha_of(X[4]), n=5)
    called("access:issue-of-an-issued-ticket", o, "issue", X[4], who=imp, sha="11" * 32, exp=now + 10 ** 6, n=6)
    # the same machine as a, another session, while a's is up
    s2 = session(same)
    called("access:other-session", s2, "claim", X[2], who=victim, sha=sha_of(X[2]), at=now, n=9003)
    # no session at all: plain HTTP
    was, why = stands(X[2]), ""
    try:
        urllib.request.urlopen(urllib.request.Request("http://%s:%d%s?op=claim" % (host, port, API), method="POST", headers={"Content-Type": "application/json"},
                               data=json.dumps({"who": victim, "ticket": X[2], "sha": sha_of(X[2]), "at": now, "n": 9004}).encode()), timeout=5).read()
    except urllib.error.HTTPError as e:
        why = "HTTP %d: %s" % (e.code, e.read().decode("utf-8", "replace")[:120])
    control("access:no-session", why, stands(X[2]) != was)
    # and the tickets the controls aimed at are still claimable, once each, by the SARAs
    rows = st.answers(st.claims(X[2:5], who=["b", "c"]))
    m = measure(rows, X[2:5]); rp.row(link, "access:tickets-after", verdict_of(m), m)

    # restored-winner provenance: a NEW memory; a member says it held the ticket before the winner is back.
    # [THE_ISSUER_SAYS_WHO_WON_V1] seen: the issuer saw the win on its held read before the memory was replaced.
    # unseen: the issuer's link was down while the ticket was won, so it cannot say -- the case that stays open.
    lat = SL.profile(link).latency_ms / 1000.0
    o2 = session(other)
    for seen in (True, False):
        P = ["%s-prov-%s-%d" % (link, "seen" if seen else "unseen", k) for k in range(4)]
        st.issue(P)
        if not seen: st.relays["issuer"].cut(waiting=True)
        first = st.answers(st.claims(P, who=["a"]))
        if seen: time.sleep(0.5 + 6 * lat)                                    # the ticket's write has reached the issuer's held read
        st.relays["a"].cut(waiting=True)
        st.replace()
        if not seen: st.relays["issuer"].restore()
        time.sleep(1.0 + 6 * lat)                                             # the issuer has written its tickets again
        o2.close(); o2 = session(other)
        took = []
        for k in range(len(P)):                                               # a member, in its own name, says it held each ticket before
            try:
                r = o2.call("POST", API + "?op=claim", json.dumps({"who": imp, "ticket": P[k], "sha": sha_of(P[k]), "at": time.time(), "n": (50 if seen else 60) + k, "held": 1}))
            except ribbit.RibbitError as e:
                r = {"outcome": "refused: " + str(e)[-80:]}
            if r.get("outcome") == "claimed": took.append(P[k])
        st.relays["a"].restore(); time.sleep(1.0 + 6 * lat)
        won_by = {t: (stands(t) or ("", "", "nobody: the ticket is not in the new memory"))[2] for t in P}
        stolen = [t for t in P if won_by[t] == imp]; kept = [t for t in P if won_by[t] == victim]
        if seen:
            verdict = "PASS" if (len(kept) == len(P) and not took) else "FAIL"
            what = "the issuer had seen each ticket won before the memory was replaced, and said so when it issued them again"
        else:
            verdict = "OPEN" if stolen else ("PASS" if len(kept) == len(P) else "FAIL")
            what = "the issuer's link was down while the tickets were won, so it could not say who won. The ticket cannot tell: proof is evidence from before"
        rp.row(link, "access:restored-winner(%s)" % ("issuer-saw" if seen else "issuer-did-not-see"), verdict, {"tickets": len(P)},
               "a member that said it held a ticket, in a new memory before the winner returned, was answered claimed for %d of %d tickets; afterwards %d stand won "
               "by it and %d by the true winner%s. %s" % (len(took), len(P), len(stolen), len(kept),
               "" if len(stolen) + len(kept) == len(P) else "; the rest: %s" % sorted(set(won_by.values()) - {imp, victim}), what))
    for m_ in (o, o2, s2):
        try: m_.close()
        except Exception: pass
    other.close(); same.close()


def storage(st, rp, n=200):
    """in a study of its own: SARAs that have issued and won nothing before, so what is counted is these tickets"""
    link = st.link
    T = ["%s-sto-%04d" % (link, k) for k in range(n)]
    tick = lambda: [c for c in st.cells() if c["variable"] == "ticket" or c["variable"].startswith("won.")]
    time.sleep(0.5); r0 = st.rss_kb(); st.issue(T, life=250.0); time.sleep(0.5); c1 = len(tick()); r1 = st.rss_kb()
    st.answers(st.claims(T), limit=600); time.sleep(0.5); c2 = len(tick()); r2 = st.rss_kb()
    st.answers(st.claims(T), limit=600); time.sleep(0.5); c3 = len(tick()); r3 = st.rss_kb()
    st.replace(); time.sleep(2.0 + 10 * SL.profile(link).latency_ms / 1000.0); c4 = len(tick())
    per_claim = (c3 - c1) / float(6 * n)
    # a ticket is one cell when issued and one more when won; a claim made again adds none
    rp.row(link, "storage", "PASS" if c1 == n and c2 <= 2 * n and c3 == c2 else "FINDING",
           {"tickets": n, "cells_after_issue": c1, "cells_after_one_round": c2, "cells_after_two_rounds": c3, "cells_in_replaced_memory": c4,
            "ram_host_rss_kb": [r0, r1, r2, r3], "ram_host_kb_per_ticket": round((r3 - r0) / float(n), 1), "cells_per_claim_call": round(per_claim, 3)},
           "%d tickets: %d cell(s) after issue, %d after 3 SARAs claimed each, %d after they claimed again (%.3f a claim call: a ticket is one cell when issued and one more "
           "when won, however often it is claimed); the RAM host grew %d kB over the %d tickets and their %d claim calls (%.1f kB a ticket); nothing removes a ticket while its "
           "memory lives. A replaced memory held %d (each ticket its issuer made again)"
           % (n, c1, c2, c3, per_claim, r3 - r0, n, 6 * n, (r3 - r0) / float(n), c4))


def long_case(st, rp, life=300.0, extra=10.0):
    """two memories for the contract's whole MAX_TTL_SECONDS. Side A: the issuer and a. Side B (empty): b and c -- the
    issuer is absent there. d: its link is down past the tickets' life, with a claim in flight."""
    link = st.link
    L = ["%s-long-%d" % (link, k) for k in range(8)]
    st.issue(L, life=life); t_issue = time.time()
    first = st.answers({"d": st.ask("d", [["claim", L[0], sha_of(L[0])]])})  # d consumed L0 before
    t0 = time.time(); st.relays["d"].cut(waiting=True)
    held_d = st.ask("d", [["claim", L[7], sha_of(L[7])], ["claim", L[6], sha_of(L[6])]], threads=2)   # made with d's link down: held until it is back
    st.start_ram("B")
    for n in ("b", "c"): st.to_side(n, "B")
    side_a = st.answers({"a": st.ask("a", [["claim", t, sha_of(t)] for t in L[:6]])})       # A: a claims L0..L5 now
    asked_b = st.claims(L[:6], who=["b", "c"])                                              # B: held for the issuer
    print("        %-9s long: two memories and d's link down since %s; waiting %.0f s (the contract's MAX_TTL_SECONDS and %.0f more)" % (link, time.strftime("%H:%M:%S"), life, extra), flush=True)
    side_b = st.answers(asked_b, limit=life + 120)
    t_b = time.time()
    time.sleep(max(0, t_issue + life + extra - time.time()))
    for n in ("b", "c"): st.to_side(n, "A")                                                 # the partition heals
    st.relays["d"].restore(); t_heal = time.time()
    late = st.answers({"d": held_d}, limit=120)
    after = st.answers(st.claims(L, who=["b", "c", "d"]), limit=120)
    status = st.answers({"issuer": st.ask("issuer", [["status", t] for t in L])})["issuer"]
    rows = merge(merge(merge(first, side_a), merge(side_b, late)), after)
    m = measure(rows, L, t0, t_heal, expect_lost=L[6:])
    b_reasons = {}
    for rs in side_b.values():
        for r in rs: b_reasons[r[3] or r[6][:40]] = b_reasons.get(r[3] or r[6][:40], 0) + 1
    b_hold = [r[5] - r[4] for rs in side_b.values() for r in rs]
    late_won = [r[1] for r in late["d"] if r[2]]
    m.update(side_b_reasons=b_reasons, side_b_hold_s_min=round(min(b_hold), 1), side_b_hold_s_max=round(max(b_hold), 1),
             late_writer_won_after_expiry=late_won, late_writer_answers=[[r[1][-6:], r[2], r[3] or r[6][:60], round(r[5] - r[4], 1)] for r in late["d"]],
             status_known=sum(1 for r in status if r[2]), status_consumed=sum(1 for r in status if r[3] == "consumed"))
    ok = not late_won and all(not r[2] for rs in side_b.values() for r in rs)
    rp.row(link, "long:two-memories", verdict_of(m, ok), m,
           "side B (issuer absent) answered %s after holding %.0f to %.0f s; side A won %d of 6 at once; d's claims, held %.0f s past the ticket's life, were answered %s"
           % (b_reasons, min(b_hold), max(b_hold), sum(1 for r in side_a["a"] if r[2]), max(r[5] - r[4] for r in late["d"]),
              [(r[2], r[3] or r[6][:50]) for r in late["d"]]))


def pins(sara):
    def git(d, *a):
        return subprocess.run(["git", "-C", d] + list(a), capture_output=True, text=True).stdout.strip()
    tree = os.path.normpath(os.path.join(_HERE, "..", "..", ".."))
    ram = subprocess.run([RAM, "--version"], capture_output=True, text=True)
    app = [ln for ln in open(os.path.join(tree, "opt", "fdf", "src", "fdf-source", "fdf", "bin", "fdf-app")) if ln.startswith("BUILD_TAG")]
    return {"frognet_commit": git(tree, "rev-parse", "--short", "HEAD") + ("+uncommitted" if git(tree, "status", "--porcelain", "--untracked-files=no") else ""),
            "frognet_branch": git(tree, "rev-parse", "--abbrev-ref", "HEAD"), "sara_commit": git(sara, "rev-parse", "--short", "HEAD"),
            "ram_host": (ram.stdout + ram.stderr).strip().splitlines()[0][:200] if (ram.stdout + ram.stderr).strip() else "?",
            "fdf_app": app[0].split('"')[1] if app else "?", "study": BUILD}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--sara", required=True); p.add_argument("--link", nargs="+", default=["lan", "wan-100ms"])
    p.add_argument("--reps", type=int, default=3); p.add_argument("--tickets", type=int, default=0)
    p.add_argument("--skip-long", action="store_true"); p.add_argument("--only", nargs="*", default=[]); p.add_argument("--out")
    a = p.parse_args()
    for f in (RAM, os.path.join(EXAMPLE, "lib", "libkartram.so"), os.path.join(a.sara, "worldshepherd_sara", "connector_ticket_shared.py")):
        if not os.path.exists(f):
            sys.exit("worldshepherd_acceptance: %s is not there (ribbit/examples/worldshepherd/README.md: Build)" % f)
    out = a.out or tempfile.mkdtemp(prefix="ws-acceptance-")
    os.makedirs(out, exist_ok=True)
    pn = pins(a.sara)
    print("%s\n  FrogNet %s (%s); SARA %s; %s; %s\n  rows and logs in %s" % (BUILD, pn["frognet_commit"], pn["frognet_branch"], pn["sara_commit"], pn["ram_host"], pn["fdf_app"], out), flush=True)
    rp = Report(os.path.join(out, "worldshepherd-acceptance.jsonl"), pn)
    want = lambda name: not a.only or any(name.startswith(o) or o.startswith(name) for o in a.only)
    for link in a.link:
        K = a.tickets or (240 if SL.profile(link).latency_ms < 5 else 24)
        st = Study(link, a.sara, out)
        try:
            continuity(st, rp, K, a.reps, a.only)
            if want("access"): access(st, rp)
            if want("long") and not a.skip_long: long_case(st, rp)
        except Exception as e:
            rp.row(link, "the study itself", "FAIL", {}, "%s: %s" % (type(e).__name__, str(e)[:500]))
        finally:
            st.close()
        if want("storage"):
            st = Study(link, a.sara, out, "-storage")
            try:
                storage(st, rp)
            except Exception as e:
                rp.row(link, "the study itself (storage)", "FAIL", {}, "%s: %s" % (type(e).__name__, str(e)[:500]))
            finally:
                st.close()
    skipped = " (the long case was not run: --skip-long)" if a.skip_long else ""
    print("RESULT %s%s%s%s" % ("PASS" if not rp.fails else "FAIL: " + "; ".join(rp.fails), (", and to read: " + "; ".join(rp.opens)) if rp.opens else "", skipped, ""), flush=True)
    return 1 if rp.fails else 0


if __name__ == "__main__":
    sys.exit(main())
