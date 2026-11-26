#!/usr/bin/env python3
################################################################
#  Copyright (C) 2016-2026 Fawcett Innovations LLC             #
#  SPDX-License-Identifier: GPL-2.0-only                       #
################################################################
"""core/channel_chat.py -- two clients talking through a Role's memory, to watch while the Role's protocol, key and
algorithm are changed under them.

John, 23 Nov 2026: "I need a script that sends chat back and forth between two clients so I can show it working as
we switch protocols, etc."

On one node:      /opt/frognet_semantic/venv/bin/python3 /opt/frognet_semantic/core/channel_chat.py --start
On another node:  /opt/frognet_semantic/venv/bin/python3 /opt/frognet_semantic/core/channel_chat.py

The one given --start asks the network's application election for a memory of the Role (units unless --role),
`<role>Host.protocolchat`, on a THIRD node, so that what both clients say crosses the wire as that Role's traffic, and
then says a line once a second. The other answers each line as it arrives. Each prints what it hears, how long the
round trip took, and how the Role's traffic is going at that moment: the protocol, sealed or not and the algorithm,
and this node's sending key (its fingerprint). Anything typed, then Enter, is said in place of the next line.
q and Enter, or Ctrl-C, ends it; the one that started it gives the memory back.

Change the Role while they talk, from any node that holds its secret:
    channel_admin.py set units --protocol chorus      (or tcp, openqasm)
    channel_admin.py set units --seal | --new-key | --algorithm chacha20-poly1305 | --unseal
or with the keys of channel_test.py's part L.

How it works (the way anything on this system does): a line is a tuple a client writes into the memory,
(chat, line, <its address>); each client holds a read on that family there and is woken by the other's write.
Nothing is polled and neither client connects to the other. What a line says crosses sealed whenever the Role is.
(A held read's fire names the tuple that changed, not what it holds, and is not sealed.)
"""
import argparse
import contextlib
import hashlib
import io
import json
import os
import queue
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

BUILD = "channel-chat-v2-20261126a"
APP = "protocolchat"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Two clients talking through a Role's memory while its channel is changed.")
    ap.add_argument("--start", action="store_true", help="make the memory and begin the conversation (the other client is run without this)")
    ap.add_argument("--role", default="units")
    ap.add_argument("--memory-on", default="", help="with --start: the address of the node that is to serve the memory (default: one that is not this node)")
    ap.add_argument("--every", type=float, default=1.0, help="with --start: seconds between one line being answered and the next being said")
    ap.add_argument("--count", type=int, default=0, help="end after this many lines have been answered (default: until q or Ctrl-C)")
    ap.add_argument("--no-keys", action="store_true", help="do not read the keyboard (for the simulator)")
    ap.add_argument("--verbose", action="store_true")
    a = ap.parse_args(argv)
    role = a.role.lower()

    if not a.verbose:
        try:
            from core import frognet_diag
            frognet_diag._ON = False
        except Exception:
            pass
        class _OnlyTheConversation:
            """This process holds the Role's setting as every end does, and that code says what it does in a
            daemon's journal's words. Here only the conversation is shown: those lines are dropped."""
            def __init__(self, out):
                self._out, self._part = out, ""

            def write(self, text):
                self._part += text
                while "\n" in self._part:
                    line, self._part = self._part.split("\n", 1)
                    if not (line.startswith("[") and not line.startswith("[CHANNEL-CHAT]")):
                        self._out.write(line + "\n")
                return len(text)

            def flush(self):
                self._out.flush()

            def isatty(self):
                return self._out.isatty()
        sys.stdout = _OnlyTheConversation(sys.stdout)
    from core import app_hosts
    from core import channel_frame as F
    from core import channel_setting as C
    with contextlib.redirect_stdout(io.StringIO() if not a.verbose else sys.stdout), \
            contextlib.redirect_stderr(io.StringIO() if not a.verbose else sys.stderr):
        T, H = C._tuples()
        H.holder()
        C.start()                                    # this process holds the Role's setting, as every end does
    fdfram, _sel = app_hosts._lib()
    me = T.my_ip()
    memory = app_hosts.host_name(APP, role)

    def hosts():
        try:
            return [l.rstrip("\n") for l in open(os.environ.get("FROGNET_HOSTS") or os.environ.get("FDF_HOSTS") or "/etc/hosts")]
        except OSError:
            return []

    def node_name(ip):
        for l in hosts():
            w = l.split()
            if w and w[0] == ip:
                for n in w[1:]:
                    if n.startswith("FrogNetHost.") and len(n) > 12:
                        return n[12:]
        return ip

    def how():
        """How the Role's traffic is going now, as this node holds its setting."""
        s = C.setting_of(role) or {}
        if s.get("number"):
            try:
                key = F.key_for(C.secret_in_use(role), F.fnid_here(), role, int(s["number"]))
                fp = hashlib.sha256(key).hexdigest()[:8]
            except Exception as e:
                fp = "not made here (%s)" % type(e).__name__
            return "%-8s | SEALED %-17s | key %s" % (s.get("driver") or "tcp", s.get("protection") or "aes-256-gcm", fp)
        return "%-8s | not sealed               |" % (s.get("driver") or "tcp")

    print("[CHANNEL-CHAT] %s on %s (%s), the Role %r" % (BUILD, node_name(me), me, role), flush=True)
    made, where = [], None
    stop = threading.Event()
    try:
        ctl, sysr = app_hosts.ip_of(hosts(), app_hosts.CONTROL), app_hosts.ip_of(hosts(), app_hosts.SYSTEM)
        if a.start:
            alive = sorted(ip for ip in app_hosts.alive_ones(hosts()) if ip != me)
            on = a.memory_on or (alive[-1] if alive else "")
            if not on:
                print("there is no other node to serve the memory: --memory-on <address>")
                return 1
            made.append((sysr, fdfram.RoleRam(sysr).write(app_hosts.SVC, "can", "%s.%s.%s" % (APP, role, on.replace(".", "-")),
                                                         {"guid": on.replace(".", "-"), "addr": on, "serve": [role], "mem_total_kb": 1, "cores": 1})))
            made.append((ctl, fdfram.RoleRam(ctl).write(app_hosts.SVC, "config", APP, {"config": json.dumps({"rams": [role]}), "seq": 1})))
            print("asked the network for the memory %s on %s" % (memory, node_name(on)), flush=True)
        t0, said = time.time(), 0.0
        while not app_hosts.ip_of(hosts(), memory):
            # (a held read returns when the hosts file is rewritten: that is the event waited for)
            H.holder().wait_until(app_hosts.SVC, ctl, lambda got: bool(app_hosts.ip_of(hosts(), memory)), name_like="config.%", wait_s=10)
            if not app_hosts.ip_of(hosts(), memory) and time.time() - said >= 10:
                said = time.time()
                print("   ... %.0f s: %s is not in this node's hosts file yet%s" % (time.time() - t0, memory,
                      "" if a.start else " (run the other client with --start first)"), flush=True)
        where = app_hosts.ip_of(hosts(), memory)
        print("the memory %s is on %s%s" % (memory, node_name(where),
              "" if where != me else ". THAT IS THIS NODE: what this client says does not cross the wire. Start with --memory-on a third node."), flush=True)
        ram = fdfram.RoleRam(memory)
        mine = "%s.%s" % (me, os.getpid())
        print("   %-8s  %-12s  %-34s  %-9s  %s" % ("time", "who", "said", "round trip", "the Role's traffic now"))

        state = {"n": 0, "sent_at": {}, "heard": {}, "answered": 0}
        typed = queue.Queue()
        got_one = threading.Event()

        def say(text, re=0):
            state["n"] += 1
            n = state["n"]
            state["sent_at"][n] = time.time()
            try:
                made_cell = ram.write("chat", "line", mine, {"n": n, "from": node_name(me), "text": text, "re": re})
                if not any(c == made_cell for _h, c in made):
                    made.append((memory, made_cell))
            except Exception as e:
                print("   %s  %-12s  %-34s  %-9s  %s" % (time.strftime("%H:%M:%S"), node_name(me), "NOT SAID: %s" % str(e), "", how()), flush=True)
                return 0
            return n

        def next_text(n):
            try:
                return typed.get_nowait()
            except queue.Empty:
                return "line %d from %s" % (n, node_name(me))

        def hear():
            """The held read on the conversation: woken by the other client's write."""
            def news(got):
                fresh = False
                for r in got:
                    name = str(r.get("SensorName") or "")
                    d = r.get("data") or {}
                    who = name[len("line."):]
                    if not name.startswith("line.") or who == mine or not isinstance(d, dict):
                        continue
                    n = int(d.get("n") or 0)
                    if n <= state["heard"].get(who, 0):
                        continue
                    state["heard"][who] = n
                    fresh = True
                    rt = ""
                    t = state["sent_at"].pop(int(d.get("re") or 0), None)
                    if t is not None:
                        rt = "%.0f ms" % ((time.time() - t) * 1000)
                        state["answered"] += 1
                    print("   %s  %-12s  %-34s  %-9s  %s" % (time.strftime("%H:%M:%S"), str(d.get("from") or who)[:12], str(d.get("text") or "")[:34], rt, how()), flush=True)
                    if not a.start and not int(d.get("re") or 0) and not str(d.get("text") or "").endswith(" is here"):
                        say(next_text(state["n"] + 1), re=n)       # the answer, at once
                    else:
                        got_one.set()
                return fresh
            first = True
            while not stop.is_set():
                try:
                    if first:                         # what is there when this client starts is not news
                        for r in H.holder().wait_until("chat", memory, lambda got: True, name_like="line.%", wait_s=5):
                            who = str(r.get("SensorName") or "")[len("line."):]
                            state["heard"][who] = int(((r.get("data") or {}).get("n")) or 0)
                        first = False
                    H.holder().wait_until("chat", memory, news, name_like="line.%", wait_s=C.HOLD_S)
                except Exception as e:
                    if not app_hosts.ip_of(hosts(), memory):
                        print("   %s  the memory %s has been given back: the client that started the conversation ended it" % (time.strftime("%H:%M:%S"), memory), flush=True)
                        stop.set()
                        got_one.set()
                        return
                    print("   %s  (the read held on the conversation ended: %s; held again)" % (time.strftime("%H:%M:%S"), str(e)), flush=True)
                    stop.wait(2)
        threading.Thread(target=hear, daemon=True, name="chat-hear").start()

        def keyboard():
            for line in sys.stdin:
                line = line.strip()
                if line.lower() == "q":
                    stop.set()
                    got_one.set()
                    return
                if line:
                    typed.put(line)
        if not a.no_keys and sys.stdin.isatty():
            threading.Thread(target=keyboard, daemon=True, name="chat-keys").start()

        if not a.start:
            say("%s is here" % node_name(me))            # the client that started waits for this
        if a.start:
            waited = time.time()
            while not got_one.wait(10) and not stop.is_set():
                print("   ... %.0f s: waiting for the other client. On another node: channel_chat.py" % (time.time() - waited), flush=True)
            while not stop.is_set():
                got_one.clear()
                n = say(next_text(state["n"] + 1))
                if n and not got_one.wait(5) and not stop.is_set():
                    print("   %s  %-12s  %-34s  %-9s  %s" % (time.strftime("%H:%M:%S"), "(nobody)", "no answer to line %d in 5 s" % n, "", how()), flush=True)
                    state["sent_at"].pop(n, None)
                if a.count and state["answered"] >= a.count:
                    break
                stop.wait(a.every)
        else:
            while not stop.is_set():
                stop.wait(1)
                if a.count and state["n"] >= a.count:
                    stop.wait(2)
                    break
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        import signal
        try:
            signal.signal(signal.SIGINT, signal.SIG_IGN)
        except ValueError:
            pass
        gone = 0
        for host, cell in reversed(made):
            try:
                gone += fdfram.RoleRam(host).remove(None, row_id=cell)
            except Exception:
                pass
        if a.start:
            print("\nthe memory %s is given back (%d tuples removed)" % (memory, gone), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
