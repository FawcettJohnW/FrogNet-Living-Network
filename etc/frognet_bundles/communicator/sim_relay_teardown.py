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
sim_relay_teardown.py -- what closes a HEALTHY caller's sockets?

Field symptom, repeatedly: a call runs for a while, then BOTH of a caller's planes
end at once -- "peer closed" or "connection reset by peer" -- with nothing on the
relay side, because the relay logs no joins and no teardowns.

This drives the REAL fnav.Relay over loopback with real FNWP framing and asks the
question directly. Only Relay._read_caps is stubbed (it reaches a live tuple store);
everything else -- accept, _pump, plane declaration, _fan, exclusion, the finally
block -- is the shipped code.

Each scenario ends by reporting which sockets are still alive. A scenario that kills
a caller who did nothing wrong is the bug.

  A  BASELINE                two callers, both planes, media flowing. Nobody dies.
  B  PAIRED TEARDOWN         one plane of a caller is closed. Its OTHER plane must
                             close too ([TWO_PLANES_V1], by design) and the OTHER
                             CALLER must be untouched.
  C  NAME COLLISION          a caller reconnects with the SAME name while its old
                             sockets are still unwinding. The teardown collects
                             mates by NAME, so it can shut down the connection that
                             just arrived. The new caller must survive.
  E  KEYREQ STORM             a viewer under a MediaSpeed cap is permanently
                             unanchored. The relay must NOT ask the sender for a
                             keyframe on every fan -- asking for the largest frame
                             there is, because a smaller one would not fit, is how
                             half the wire became keyframes and the picture never
                             came back.

  F  FULL SOCKET               a viewer that stops reading fills its socket. The
                             relay must SHED frames to it and keep the connection.
                             It used to give a committed frame 0.25s, try to pad it
                             out, and close the connection when the padding would
                             not go either -- measured on hardware as "peer closed
                             MID-FRAME after 608 of 647 bytes" six minutes into a
                             call on a 93 kbps cap.

  G  STARVED VIEWER REPORTED  a viewer receiving a fraction of what is aimed at it
                             must be REPORTED to the sender, or the sender holds
                             720p at full rate forever while the far end sees one
                             frame a second. Measured on hardware: 1.2 fps
                             delivered against 23.5 fps sent, sender at L7, every
                             counter it owned reading perfect health.

  D  DEAD-DROP RESIDUE       a write to a viewer fails, so _fan closes that socket
                             from the SENDER'S thread. Does the closed conn stay in
                             _name_of, and does a later teardown of the same name
                             then reach across and shut down somebody live?

Run:  python3 sim_relay_teardown.py
Exit: 0 if every scenario behaves as documented, 1 otherwise.
"""

import socket
import sys
import threading
import time

sys.path.insert(0, ".")

import fnav                                          # noqa: E402


PORT = 19301
SETTLE = 0.5
FAILURES = []


def check(name, ok, detail=""):
    print("  %-5s %s%s" % ("PASS" if ok else "FAIL", name,
                           ("\n        " + detail) if (detail and not ok) else ""))
    if not ok:
        FAILURES.append(name)


def start_relay(port):
    r = fnav.Relay("127.0.0.1", port)
    r._read_caps = lambda: {}
    threading.Thread(target=r.serve, daemon=True).start()
    time.sleep(0.4)
    return r


class Plane:
    """One plane of one caller. Tracks whether the relay has closed it.

    [TEARDOWN_IS_PER_SESSION_V1] The declaration carries the plane byte followed by
    the CALLER'S SESSION TAG, exactly as Call._open_plane sends it. The sim must send
    one too, or every sim caller declares an empty tag, they all look like the same
    session, and scenario C tests nothing.
    """

    def __init__(self, name, port, plane, session=b""):
        self.name, self.plane = name, plane
        self.alive = True
        self.why = ""
        self.got = []
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.sock.connect(("127.0.0.1", port))
        fnav.send_frame(self.sock,
                        fnav.pack_typed(fnav.KIND_PLANE, name, plane + session))
        self.sock.setblocking(False)
        self._stop = threading.Event()
        threading.Thread(target=self._rx, daemon=True).start()

    def _rx(self):
        while not self._stop.is_set():
            try:
                f = fnav.recv_frame(self.sock)
            except fnav.AbortedFrame:
                continue
            except Exception as e:
                self.alive = False
                self.why = "%s: %s" % (type(e).__name__, e)
                return
            self.got.append(f)

    def send(self, payload=b"x" * 64):
        kind = (fnav.KIND_VIDEO if self.plane == fnav.PLANE_VIDEO
                else fnav.KIND_AUDIO)
        body = (fnav.pack_video(7, payload, 0, is_key=True)
                if kind == fnav.KIND_VIDEO else payload)
        try:
            fnav.send_frame(self.sock, fnav.pack_typed(kind, self.name, body))
            return True
        except Exception as e:
            self.alive = False
            self.why = "send: %s: %s" % (type(e).__name__, e)
            return False

    def kill(self):
        """Yank this plane the way a crashed client would."""
        self._stop.set()
        try:
            self.sock.close()
        except Exception:
            pass

    def close(self):
        self.kill()


class Caller:
    """One caller: two planes sharing one session tag, as a real client does."""

    _n = 0

    def __init__(self, name, port):
        self.name = name
        Caller._n += 1
        self.session = ("s%08d" % Caller._n).encode("ascii")
        self.a = Plane(name, port, fnav.PLANE_AUDIO, self.session)
        self.v = Plane(name, port, fnav.PLANE_VIDEO, self.session)

    def send_both(self):
        self.a.send()
        self.v.send()

    def alive(self):
        return self.a.alive and self.v.alive

    def state(self):
        return "audio=%s%s video=%s%s" % (
            "up" if self.a.alive else "DOWN",
            "" if self.a.alive else " (%s)" % self.a.why,
            "up" if self.v.alive else "DOWN",
            "" if self.v.alive else " (%s)" % self.v.why)

    def close(self):
        self.a.close()
        self.v.close()


def main():
    print(__doc__.strip().splitlines()[0])
    print()
    start_relay(PORT)

    # ---------------------------------------------------------------- A
    print("A -- baseline: two callers, media flowing, nobody dies")
    alice = Caller("Alice", PORT)
    bob = Caller("Bob", PORT)
    time.sleep(SETTLE)
    for _ in range(5):
        alice.send_both()
        bob.send_both()
        time.sleep(0.05)
    time.sleep(SETTLE)
    print("      Alice %s" % alice.state())
    print("      Bob   %s" % bob.state())
    check("baseline_both_live", alice.alive() and bob.alive(),
          "a caller died with nothing wrong")
    alice.close()
    bob.close()
    time.sleep(SETTLE)
    print()

    # ---------------------------------------------------------------- B
    print("B -- one plane yanked: its mate must go, the other caller must not")
    carol = Caller("Carol", PORT)
    dave = Caller("Dave", PORT)
    time.sleep(SETTLE)
    carol.send_both()
    dave.send_both()
    time.sleep(SETTLE)
    carol.v.kill()                       # video plane dies; audio should follow
    time.sleep(SETTLE + 0.5)
    dave.send_both()
    time.sleep(SETTLE)
    print("      Carol %s" % carol.state())
    print("      Dave  %s" % dave.state())
    check("paired_teardown_closes_mate", not carol.a.alive,
          "Carol's audio plane survived her video plane -- half a caller")
    check("other_caller_untouched", dave.alive(),
          "Dave was taken down by Carol's teardown: %s" % dave.state())
    carol.close()
    dave.close()
    time.sleep(SETTLE)
    print()

    # ---------------------------------------------------------------- C
    print("C -- reconnect with the SAME name while the old sockets unwind")
    old = Caller("Erin", PORT)
    time.sleep(SETTLE)
    old.send_both()
    time.sleep(SETTLE)
    # The client goes away hard, then comes straight back under the same name --
    # exactly what restarting `fnav.py --name Dave` does.
    old.a.kill()
    new = Caller("Erin", PORT)           # same name, brand new sockets
    old.v.kill()
    time.sleep(SETTLE + 0.8)
    new.send_both()
    time.sleep(SETTLE)
    print("      new Erin %s" % new.state())
    check("same_name_reconnect_survives", new.alive(),
          "the FRESH connection was torn down by the OLD one's teardown. "
          "_pump's finally collects mates by NAME "
          "(_mates = [c for c, n in self._name_of.items() if n == _nm]) and "
          "shutdown()s every socket carrying that name, including one that "
          "arrived after the dying socket. state: %s" % new.state())
    new.close()
    time.sleep(SETTLE)
    print()

    # ---------------------------------------------------------------- D
    print("D -- residue: does a dead-dropped conn stay in _name_of?")
    r2_port = PORT + 1
    r2 = start_relay(r2_port)
    frank = Caller("Frank", r2_port)
    gina = Caller("Gina", r2_port)
    time.sleep(SETTLE)
    frank.send_both()
    gina.send_both()
    time.sleep(SETTLE)
    # Force a write failure to Gina by closing her socket under the relay, then
    # having Frank fan a frame at it.
    gina.v.kill()
    time.sleep(0.2)
    for _ in range(3):
        frank.v.send()
        time.sleep(0.05)
    time.sleep(SETTLE)
    with r2.lock:
        names = dict(r2._name_of)
        peers = dict(r2.peers)
    residue = [c for c in names if c not in peers]
    print("      _name_of holds %d conn(s); peers holds %d; residue=%d"
          % (len(names), len(peers), len(residue)))
    check("no_name_of_residue", not residue,
          "%d connection(s) were removed from peers but LEFT in _name_of. "
          "_fan's dead-drop closes the socket and pops peers only, so the entry "
          "survives -- and the next teardown of that name calls shutdown() on a "
          "closed fd and, worse, keeps matching a name that may since have been "
          "reused by a live caller." % len(residue))
    frank.close()
    gina.close()
    print()

    # ---------------------------------------------------------------- E
    print("E -- a capped, permanently unanchored viewer must not cause a storm")
    r3_port = PORT + 2
    r3 = start_relay(r3_port)
    # A cap so tight nothing fits: the viewer can never anchor, which is exactly
    # the hardware case (67 KB keyframes against a MediaSpeed cap).
    r3._caps = {"127.0.0.1": 1000}
    hank = Caller("Hank", r3_port)
    iris = Caller("Iris", r3_port)
    time.sleep(SETTLE)
    big = b"K" * 40000
    t_end = time.time() + 3.0
    n_frames = 0
    while time.time() < t_end:
        hank.v.send(big)
        n_frames += 1
        time.sleep(0.04)
    time.sleep(SETTLE)
    asked = sum(1 for f in hank.v.got
                if len(f) > fnav._KIND.size
                and fnav._KIND.unpack_from(f, 0)[0] == fnav.KIND_KEYREQ)
    # ABSOLUTE ceiling, deliberately not derived from KEYREQ_MIN_S: a viewer who
    # cannot anchor is waiting on BUDGET, and the correct number of requests for
    # that is ZERO once a keyframe is being held for them. One is allowed for the
    # first fan, before anything is held.
    expected_max = 1
    print("      %d frames fanned over 3.0s -> %d keyframe request(s) "
          "(ceiling %d at KEYREQ_MIN_S=%.1fs)"
          % (n_frames, asked, expected_max, fnav.Relay.KEYREQ_MIN_S))
    check("no_keyreq_storm", asked <= expected_max,
          "%d requests in 3 seconds. A viewer that cannot anchor is waiting on "
          "BUDGET, not on the encoder: once a keyframe is held for them another "
          "one cannot help, it just overwrites the held one and spends the wire."
          % asked)
    hank.close()
    iris.close()
    print()

    # ---------------------------------------------------------------- F
    print("F -- a viewer that stops reading is shed, not hung up on")
    r4_port = PORT + 3
    r4 = start_relay(r4_port)
    jack = Caller("Jack", r4_port)
    kim = Caller("Kim", r4_port)
    time.sleep(SETTLE)
    # Kim stops reading entirely. Her socket buffer fills, and every write to it
    # starts coming up short -- the hardware case, without the hardware.
    kim.v._stop.set()
    time.sleep(0.2)
    payload = b"V" * 60000
    sent_n = 0
    t_end = time.time() + 6.0
    while time.time() < t_end:
        if not jack.v.send(payload):
            break
        sent_n += 1
        time.sleep(0.01)
    time.sleep(SETTLE)
    # NOTE: r4.peers holds the relay's ACCEPTED sockets, not the client's ends --
    # kim.v.sock is the client side and is never in it. Count what the relay holds.
    with r4.lock:
        n_peers = len(r4.peers)
        owed = sum(getattr(r4, "_owed", {}).values())
        shed = getattr(r4, "_shed_busy", 0) + getattr(r4, "_shed_partial", 0)
    still_peer = (n_peers == 4)          # two callers, two planes each
    print("      %d frames fanned at a viewer who is not reading; "
          "relay shed %d, owes %d byte(s), relay still holds %d/4 connection(s)"
          % (sent_n, shed, owed, n_peers))
    check("sender_survives_a_stalled_viewer", jack.alive(),
          "the SENDER was taken down because a VIEWER stopped reading: %s"
          % jack.state())
    check("stalled_viewer_not_hung_up_on", still_peer,
          "the relay closed a viewer whose socket was merely full. A full socket "
          "on a constrained leg is the normal state and the reason the ladder "
          "exists; shedding is the answer, hanging up is not.")
    check("relay_shed_something", shed > 0,
          "nothing was recorded as shed, so this scenario did not exercise the "
          "path it is testing")
    jack.close()
    kim.close()
    print()

    # ---------------------------------------------------------------- G
    print("G -- a starved viewer is reported back to the sender")
    r5_port = PORT + 4
    r5 = start_relay(r5_port)
    lee = Caller("Lee", r5_port)
    mia = Caller("Mia", r5_port)
    time.sleep(SETTLE)
    mia.v._stop.set()                  # Mia stops reading: her socket fills
    time.sleep(0.2)
    big = b"V" * 60000
    # getattr: an old build has no BP_WINDOW_S at all, which IS the finding. Run
    # the scenario anyway so it reports a failed check instead of a traceback.
    t_end = time.time() + max(6.0, getattr(fnav.Relay, "BP_WINDOW_S", 2.0) * 3)
    while time.time() < t_end:
        lee.v.send(big)
        time.sleep(0.01)
    time.sleep(SETTLE)
    reports = [f for f in lee.v.got
               if len(f) > fnav._KIND.size
               and fnav._KIND.unpack_from(f, 0)[0] == fnav.KIND_BACKPRESSURE]
    n_reported = 0
    if reports:
        _k, _src, _pl = fnav.unpack_typed(reports[-1])
        n_reported = fnav._LEN.unpack_from(_pl, 0)[0]
    print("      sender received %d backpressure report(s); last said %d viewer(s) "
          "starved" % (len(reports), n_reported))
    check("starved_viewer_reported", len(reports) > 0,
          "the sender was told NOTHING. Call._ladder_from_backlog() consumes "
          "self._kf_backlog, which only the KIND_BACKPRESSURE handler sets -- with "
          "no producer the ladder can never learn that its viewer is starving, and "
          "720p at full rate is held against a link delivering a slideshow.")
    check("report_names_a_count", n_reported >= 1,
          "the report carried no viewer count: %r" % (n_reported,))
    lee.close()
    mia.close()
    print()

    if FAILURES:
        print("FAIL (%d): %s" % (len(FAILURES), ", ".join(FAILURES)))
        return 1
    print("PASS -- teardown reaches only the caller it belongs to, a capped viewer "
          "does not trigger a keyframe storm, a full socket is shed not closed, and "
          "a starved viewer reaches the sender's ladder.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
