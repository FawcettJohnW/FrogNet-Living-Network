#!/usr/bin/env python3
# Copyright (C) 2016-2026 Fawcett Innovations LLC
# SPDX-License-Identifier: GPL-2.0-only
"""test_the_memory_moves.py BUILD_DIR -- [THE_MEMORY_MOVES_V1] the owner changes the RAM host under a running call.

  Two comms-ram run, A and B. The media server, the C++ comms-feed (the stock 720p video and a 440 Hz tone) and a
  Python viewer are all started on A, under the frame, sealed. The viewer says a line of chat. Then:

  1  6 s with nothing changed: the baseline (frames and blocks per second, longest time without one)
  2  the owner moves the memory to B (comms-channel move): every end reports from B; the call's numbers across the
     move; the viewer's room at B shows the feed, the feed's call and the chat
  3  A is killed. The call goes on, a new key is set at B and every end takes it, and a C++ viewer started with B
     finds the feed's call there and watches it
  4  the owner moves the memory back to a new A (the Python owner's functions this time): every end follows again
  5  a participant with another secret does not follow a move it cannot verify: it stays where it was
"""
import os, subprocess, sys, tempfile, threading, time
B = os.path.abspath(sys.argv[1]); ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
sys.path.insert(0, os.path.join(ROOT, "python")); os.environ.setdefault("FROGCOMMS_LIB", B)
T = tempfile.mkdtemp(prefix="comms-moves-")
SECRETS, OTHER = os.path.join(T, "secrets"), os.path.join(T, "other")
for d in (SECRETS, OTHER):
    os.mkdir(d); open(os.path.join(d, "default"), "wb").write(os.urandom(48))
os.environ["FROGNET_CHANNEL_SECRETS"] = SECRETS
os.environ["FROGNET_GUID_FILE"] = os.path.join(T, "no-fnid")
from frogcomms import channel, ram_at  # noqa: E402
from frogcomms.room import Room  # noqa: E402
from frogcomms.call import Call  # noqa: E402

A, RB, MEDIA, ROLE = 21500, 21501, 21520, "mediahost"
logs = {n: open(os.path.join(T, n + ".log"), "w") for n in ("ramA", "ramB", "ramA2", "media", "feed")}
def ram(port, log):
    return subprocess.Popen([os.path.join(B, "comms-ram"), "--listen", "127.0.0.1:%d" % port], stdout=logs[log], stderr=subprocess.STDOUT)
ram_a, ram_b = ram(A, "ramA"), ram(RB, "ramB")
procs = [ram_a, ram_b]
time.sleep(1)
fail = 0
def check(ok, what):
    global fail
    print(("PASS  " if ok else "FAIL  ") + what, flush=True)
    fail += 0 if ok else 1
def owner(port, *args):
    r = subprocess.run([os.path.join(B, "comms-channel"), "--ram", "127.0.0.1:%d" % port] + list(args), capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError("comms-channel %s: %s" % (" ".join(args), r.stdout + r.stderr))
    return r.stdout.strip()

try:
    owner(A, "set", ROLE, "--seal")
    procs.append(subprocess.Popen([os.path.join(B, "comms-media"), "--listen", "127.0.0.1:%d" % MEDIA, "--ram", "127.0.0.1:%d" % A], stdout=logs["media"], stderr=subprocess.STDOUT))
    time.sleep(1)
    procs.append(subprocess.Popen([os.path.join(B, "comms-feed"), "--ram", "127.0.0.1:%d" % A, "--media", "127.0.0.1:%d" % MEDIA, "--name", "Chapel camera",
                                   "--video", os.path.join(ROOT, "media", "stock-720p.mp4"), "--tone", "440", "--channel", ROLE],
                                  stdout=logs["feed"], stderr=subprocess.STDOUT))
    time.sleep(3)
    ch = channel.for_program(ROLE, "Pythonia", "127.0.0.1", A)
    py = Room("127.0.0.1", A, "Pythonia")
    offer = next((o for o in py.offers() if o.session in py.calls_of("Chapel camera")), None)
    if not offer:
        check(False, "the viewer did not find the feed's call"); raise SystemExit(1)
    py.join_call(offer.session, offer.media_host, offer.media_port)
    py.set_media_speed(offer.session, 0)
    c = Call(offer.media_host, offer.media_port, "Pythonia", offer.session)
    c.start(); time.sleep(2.5)
    py.send_chat(offer.session, "said before the memory moved")

    samples, stop = [], False
    def sample():
        while not stop:
            samples.append((time.monotonic(), c.stats.v_recv, c.stats.a_recv)); time.sleep(0.01)
    threading.Thread(target=sample, daemon=True).start()
    def measure(t0, t1):
        s = [x for x in samples if t0 <= x[0] <= t1]
        def longest(k):
            worst, last = 0.0, s[0][0]
            for x, y in zip(s, s[1:]):
                if y[k] != x[k]:
                    worst, last = max(worst, y[0] - last), y[0]
            return max(worst, s[-1][0] - last) * 1000
        d = s[-1][0] - s[0][0]
        return (s[-1][1] - s[0][1]) / d, (s[-1][2] - s[0][2]) / d, longest(1), longest(2)

    t0 = time.monotonic(); time.sleep(6.0); t1 = time.monotonic()
    base = measure(t0, t1)
    check(base[0] > 20 and 45 < base[1] < 55, "1 nothing changed, 6 s, the memory at A: %.1f video frames/s and %.1f audio blocks/s; the longest time with no new "
          "frame %.0f ms, with no new block %.0f ms" % base)

    # -- 2 the owner moves the memory to B ----------------------------------------------------------------------
    t2 = time.monotonic()
    said = owner(A, "move", ROLE, "127.0.0.1:%d" % RB)
    took = time.monotonic() - t2
    time.sleep(4.0); t3 = time.monotonic()
    during = measure(t2 - 1.0, t3)
    last = said.splitlines()[-1].strip()
    roster = sorted(m.name for m in py.roster())
    chat = [l.text for l in py.read_chat(offer.session)]
    at_b = channel.show("127.0.0.1", RB, ROLE)
    there = sorted(e for e, r in at_b["ends"].items() if r.get("ram") == "127.0.0.1:%d" % RB)
    check("3 end(s) there: 3 attached there" in last and ram_at.at() == ("127.0.0.1", RB) and py.attached == 1
          and during[0] > 0.9 * base[0] and during[1] > 0.95 * base[1] and during[2] <= base[2] + 150 and during[3] <= base[3] + 150
          and "Chapel camera" in " ".join(roster) and offer.session in py.calls_of("Chapel camera") and chat == ["said before the memory moved"] and len(there) == 3,
          "2 the owner moves the memory from A to B under the call (the command returned in %.2f s: \"%s\"). Across the move: %.1f video frames/s and %.1f audio "
          "blocks/s; the longest time with no new frame %.0f ms, with no new block %.0f ms. At B the viewer's room shows %s, the feed's call, and %d line(s) "
          "of chat; the ends reporting from B: %s" % ((took, last) + during + (roster, len(chat), ", ".join(there))))

    # -- 3 A is killed; the call and the Role go on at B ------------------------------------------------------------
    ram_a.terminate(); ram_a.wait()
    t4 = time.monotonic(); time.sleep(3.0)
    seen = ch.changes
    said = owner(RB, "set", ROLE, "--new-key")
    time.sleep(2.0); t5 = time.monotonic()
    after = measure(t4, t5)
    out = subprocess.run([os.path.join(B, "test_feed_view"), str(RB), "Chapel camera"], env=dict(os.environ, COMMS_CHANNEL=ROLE), capture_output=True, text=True, timeout=40).stdout
    view = [l for l in out.strip().splitlines() if not l.startswith("[")]
    check(after[0] > 0.9 * base[0] and after[1] > 0.95 * base[1] and after[2] <= base[2] + 150 and ch.changes == seen + 1
          and "3 running it" in said.splitlines()[-1] and "RESULT PASS" in out,
          "3 A is killed: for the next %.0f s %.1f video frames/s and %.1f audio blocks/s, longest time with no new frame %.0f ms; a new key set at B: \"%s\"; "
          "a C++ viewer started with B finds the call there: %s" % (t5 - t4, after[0], after[1], after[2], said.splitlines()[-1].strip(), " | ".join(view[-2:])[:230]))

    # -- 5 (before the move back) a participant with another secret is at B and must not follow --------------------
    code = ("import os,sys,time; sys.path.insert(0,%r); from frogcomms import channel, ram_at; from frogcomms.room import Room\n"
            "channel.for_program(%r,'Eve','127.0.0.1',%d); r=Room('127.0.0.1',%d,'Eve'); print('EVE up', flush=True); time.sleep(9)\n"
            "print('EVE at %%s:%%d attached %%d ignored %%r' %% (ram_at.at() + (r.attached, channel.watching().ignored[:40])), flush=True); os._exit(0)\n"
            % (os.path.join(ROOT, "python"), ROLE, RB, RB))
    eve = subprocess.Popen([sys.executable, "-c", code], env=dict(os.environ, FROGNET_CHANNEL_SECRETS=OTHER), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    while "EVE up" not in eve.stdout.readline():
        pass

    # -- 4 the memory moves again, to a new comms-ram where A was, by the Python owner's functions ---------------------
    ram_a2 = ram(A, "ramA2"); procs.append(ram_a2); time.sleep(1.0)
    t6 = time.monotonic()
    done = channel.change("127.0.0.1", RB, ROLE, move_to=("127.0.0.1", A), wait_s=6.0)
    time.sleep(3.0); t7 = time.monotonic()
    again = measure(t6 - 1.0, t7)
    eve_said = [l for l in eve.communicate(timeout=30)[0].splitlines() if l.startswith("EVE at")]
    mine = [e for e in channel.show("127.0.0.1", A, ROLE)["ends"] if "Eve" not in e]
    check(done["running"] == 3 and done["behind"] == 0 and done["ignoring"] == 1 and len(mine) == 3 and py.attached == 2 and ram_at.at() == ("127.0.0.1", A) and again[0] > 0.9 * base[0] and again[1] > 0.95 * base[1]
          and again[2] <= base[2] + 150 and [l.text for l in py.read_chat(offer.session)] == ["said before the memory moved"],
          "4 the memory moves again, from B to a new comms-ram at A's address (frogcomms.channel.change): %s; %s. Across it: %.1f video frames/s and %.1f audio "
          "blocks/s, longest time with no new frame %.0f ms; the chat is there"
          % (done["summary"], "; ".join(done["lines"]) or "no end left behind but the one with another secret", again[0], again[1], again[2]))
    check(bool(eve_said) and ("at 127.0.0.1:%d attached 0" % RB) in eve_said[0] and any("Eve" in l and "IGNORED" in l for l in done["lines"]),
          "5 a participant whose machine holds another secret was at B: it ignored the move it could not verify and stayed (%s); the owner was told: %s"
          % (eve_said[0][4:] if eve_said else "?", "; ".join(l for l in done["lines"] if "Eve" in l)[:150]))
    stop = True
    c.stop(); py.close()
finally:
    for p in reversed(procs):
        p.terminate()
    print("logs in", T)
print("RESULT %s the memory moves under a call: %d fail" % ("FAIL" if fail else "PASS", fail))
os._exit(1 if fail else 0)
