# Copyright (C) 2016-2026 Fawcett Innovations LLC
# SPDX-License-Identifier: GPL-2.0-only
"""[THE_CALL_IS_UNDER_THE_FRAME_V1] The Communicator's channel, from Python: src/comms/channel.hpp, the same rules.

A call's planes are for one Role (by default "mediahost"). The Role's setting is a tuple in the Communicator's memory,
(channel, setting, <Role>) = {driver, number, protection, step, ram_host, ram_port, check}, written by comms-channel or
a window's Security tab. [THE_MEMORY_MOVES_V1] ram_host and ram_port say where the memory is: a process that reads a
setting naming another address than the one it is attached to follows it (ram_at.py). This process holds
one Channel: it holds the read on that tuple, proves each setting it reads with the Role's secret on this machine
(a setting whose check does not verify is ignored and the one held is kept), and tells every plane's end of the frame,
which switches in its own stream. The frame itself is libchannel_frame (third_party/ribbit/cpp), the library the C++
Communicator's planes use, through channel_codec.py.
"""
import os
import random
import sys
import threading
import time
import weakref

_here = os.path.dirname(os.path.abspath(__file__))
for _d in (os.environ.get("FROGCOMMS_LIB", ""), os.path.join(_here, "..", "..", "build"), os.path.join(_here, "..", "..", "build-msvc"),
           os.path.join(_here, "..", "..", "dist"), _here):
    if _d and any(os.path.exists(os.path.join(_d, n)) for n in ("libchannel_frame.so", "channel_frame.dll", "libchannel_frame.dll")):
        os.environ.setdefault("CHANNEL_FRAME_LIB", os.path.abspath(_d))
        break
sys.path.insert(0, os.path.join(_here, "..", "..", "third_party", "ribbit", "cpp", "python"))
import channel_codec as _C   # noqa: E402

from . import memory         # noqa: E402
from . import ram_at         # noqa: E402

Gone = _C.Gone
BUILD = _C.BUILD
SERVICE, VAR, ROLE = "channel", "setting", "mediahost"
REPORT_S, REPORT_FRESH_S = 10.0, 25      # an end writes its report at least this often; older than this, it is gone
_process = None
_watching = None


def secrets_dir():
    return os.environ.get("FROGNET_CHANNEL_SECRETS") or "/etc/frognet/channel_secrets"


def sender_id(me):
    """This node's FNID (/etc/fnid); a machine that is no FrogNet node has none: "comms-<me>"."""
    try:
        with open(os.environ.get("FROGNET_GUID_FILE") or "/etc/fnid") as f:
            fnid = f.read().strip()
        if fnid:
            return fnid
    except OSError:
        pass
    return "comms-" + me


def _say(text):
    print("[channel] " + text, flush=True)


def _text(s):
    if s is None:
        return "no setting: tcp, not sealed"
    return "%s, %s%s" % (s["driver"], "sealed with " + s["protection"] if s["number"] else "not sealed",
                         ", the Role's own secret at step %d" % s["step"] if s.get("step", 0) > 0 else "")


class Channel:
    def __init__(self, role, sender, secrets, framed=True):
        self.role, self.sender, self.secrets, self.framed = role, sender, secrets, framed
        self.held, self.changes, self.ignored, self.fields = None, 0, "", {}
        self.end = "%s.%d" % (sender, os.getpid())
        self._planes = weakref.WeakSet()
        self._mu = threading.Lock()                        # this process's copy of the setting and its list of planes
        if framed:
            _say("%s: this program's planes are under the frame, for the Role %r, sending as %s, secrets in %s" % (BUILD, role, sender, secrets))
        else:
            _say("this program's planes are not under the frame (no --channel); it holds the read on %r's setting to follow the memory when it moves" % role)

    def under(self, plane, far):
        """Put a plane this end opened under the frame, holding the setting this process holds now."""
        codec = _C.Codec(True, far, self.sender, self.secrets)
        with self._mu:
            codec.setting(self.role, self.held)
            plane.set_channel(codec, self.role)
            self._planes.add(plane)

    def read(self, value):
        """The setting's tuple as just read (None: there is none). Proved, kept, and every plane told."""
        with self._mu:
            if value is None:
                if self.held is not None:
                    _say("the setting of %r is gone from the memory; this process keeps the one it holds (%s)" % (self.role, _text(self.held)))
                return
            took = _C.take_setting(self.role, {k: v for k, v in value.items() if k != "ts"}, self.secrets)
            self.ignored = "" if took["taken"] else took["why"]
            if not took["taken"]:
                _say("a setting of %r is IGNORED: %s. This process keeps %s" % (self.role, took["why"], _text(self.held)))
                return
            self.held = {k: took[k] for k in ("driver", "number", "protection", "step")}
            self.fields = {k: v for k, v in value.items() if k != "ts"}
            self.changes += 1
            _say("the setting of %r is now %s%s" % (self.role, _text(self.held), " (secret %s)" % took["fingerprint"] if took["fingerprint"]
                                                   else " (this machine holds no secret for it)"))
            for plane in list(self._planes):                # each switches in its own stream
                plane.codec.setting(self.role, self.held)
                plane.owes()
            to = (str(value.get("ram_host") or ""), int(value.get("ram_port") or 0))
        # [THE_MEMORY_MOVES_V1] the setting names where the memory is. Told outside the guard: each holder of a
        # session ends its own and attaches there.
        if to[0] and to[1] > 0 and ram_at.move(*to):
            _say("the memory of %r is at %s:%d now: this process follows it" % (self.role, to[0], to[1]))

    def report(self):
        """This process's report: the setting it is running, or the one it ignored and why. No key, no secret."""
        s = self.held or {"driver": "tcp", "number": 0, "protection": "", "step": 0}
        at = ram_at.at() or ("", 0)
        return {"role": self.role, "end": self.end, "driver": s["driver"], "number": str(s["number"]), "protection": s["protection"] if s["number"] else "",
                "step": max(0, s.get("step", 0)), "sealed": bool(s["number"]), "framed": self.framed, "ram": "%s:%d" % at,
                "ignored": self.ignored, "build": BUILD}

    def sealed(self):
        return bool(self.held and self.held["number"])


class Watch:
    """The read this process holds on its Role's setting, on a session of its own."""
    def __init__(self, channel, ram_host, ram_port):
        self.channel, self.read_once, self._stop = channel, threading.Event(), False
        ram_at.start(ram_host, ram_port)
        self._c = memory.Client(ram_host, ram_port)
        self._moved = False
        # [THE_MEMORY_MOVES_V1] this read is what moves the process, and it moves too: its session ends and the loop
        # attaches where the memory is now, reads the setting as it stands there and writes this end's report there.
        self._moved_id = ram_at.when_moved(self._on_move)
        threading.Thread(target=self._run, daemon=True, name="channel-watch").start()

    def _on_move(self):
        self._moved = True
        self._c.shutdown()

    def _run(self):
        after, fresh = -1, True
        while not self._stop:
            try:
                if self._moved:
                    self._moved = False
                    self._c.close()
                    self._c = memory.Client(*ram_at.at()); after, fresh = -1, True
                rows = self._c.get(0, 0.0 if after < 0 else REPORT_S, 0 if after < 0 else 1, VAR, after, service=SERVICE)
            except memory.MemoryError_ as e:
                if self._stop:
                    return
                if self._moved:                             # the move ended the read
                    continue
                print("[channel] the read of the setting failed: %s (the setting held is kept)" % e, file=sys.stderr, flush=True)
                time.sleep(1.0)                             # as the room's own watcher does, then a session of its own again
                try:
                    self._c = memory.Client(*ram_at.at()); after, fresh = -1, True
                except memory.MemoryError_:
                    pass
                continue
            mine = False
            for t in rows:
                after = max(after, int(t["id"]))
                if t.get("scope") == self.channel.role:
                    self.channel.read(t["value"]); mine = True
            if not self.read_once.is_set() and not mine:
                self.channel.read(None)
            if not self._moved:                             # this end's report: its own tuple, written again at least every REPORT_S
                try:
                    self._c.put("at", "%s:%s" % (self.channel.role, self.channel.end), self.channel.report(), True, service=SERVICE)
                except memory.MemoryError_ as e:
                    print("[channel] this end's report was not written: %s" % e, file=sys.stderr, flush=True)
            self.read_once.set()
            fresh = False
            after = max(after, 0)

    def close(self):
        self._stop = True
        ram_at.forget(self._moved_id)
        try:                                                # a clean stop takes this end's report with it, on a session of its own
            c = memory.Client(*ram_at.at())
            c.remove("at", "%s:%s" % (self.channel.role, self.channel.end), service=SERVICE)
            c.close()
        except memory.MemoryError_:
            pass
        self._c.shutdown()


def for_program(role, me, ram_host, ram_port):
    """A program's main. role: its --channel option ("" or None: its planes are as they were). Makes the process's
    Channel, holds the read on the Role's setting, and returns once the setting as it stands has been read, so no plane
    is opened before this process knows whether the Role is sealed."""
    global _process, _watching
    framed = bool(role)
    _watching = Channel(role or ROLE, sender_id(me), secrets_dir(), framed)
    watch = Watch(_watching, ram_host, ram_port)
    _watching.watch = watch
    if not framed:                                          # nothing of its planes waits on the setting
        return None
    _process = _watching
    if not watch.read_once.wait(10.0):
        raise memory.MemoryError_("the setting of %r could not be read from the memory at %s:%d in 10 s: no plane is opened before this "
                                  "program knows whether the Role is sealed" % (role, ram_host, ram_port))
    return _process


def of_process():
    """The process's channel when its planes are under the frame; None when they are as they were."""
    return _process


def watching():
    """The process's channel, framed or not (None before its main made it)."""
    return _watching


# ---------------------------------------------------------------------------------------------- the owner's work
# comms-channel's, from Python (src/comms/channel.cpp channel_show, channel_change): a window's Security tab calls these.
def _setting_at(c, role):
    for t in c.get(0, 0.0, 0, VAR, -1, service=SERVICE):
        if t.get("scope") == role:
            return {k: v for k, v in t["value"].items() if k != "ts"}
    return {}


def _ends_at(c, role):
    return {t["scope"][len(role) + 1:]: {k: v for k, v in t["value"].items() if k != "ts"}
            for t in c.get(REPORT_FRESH_S, 0.0, 0, "at", -1, service=SERVICE) if t.get("scope", "").startswith(role + ":")}


def show(ram_host, ram_port, role=ROLE):
    """{"setting": the Role's setting without its check ({}: none), "ends": {end: its report}}."""
    c = memory.Client(ram_host, ram_port)
    try:
        s = _setting_at(c, role)
        s.pop("check", None)
        return {"setting": s, "ends": _ends_at(c, role)}
    finally:
        c.close()


def secret_line(role=ROLE, secrets=None):
    """This machine's secret for the Role, as a screen may show it (raises Gone if it holds none)."""
    s = _C.secret_of(role, secrets or secrets_dir())
    return "%s, hash %s%s" % ("the Role's own" if s["own"] else "the network's default", s["fingerprint"], ", at step %d" % s["step"] if s["own"] else "")


def change(ram_host, ram_port, role=ROLE, protocol="", algorithm="", seal=False, unseal=False, new_key=False, move_to=None, wait_s=10.0, secrets=None):
    """One change by the owner; exactly one of protocol, an encryption (seal, unseal, new_key, algorithm), move_to
    ((host, port): the memory's new address). Writes the Role's setting and returns when every end's report shows it,
    or says it ignored it, or wait_s has passed:
    {"setting", "checked", "lines", "ends", "running", "ignoring", "behind", "chat_carried", "ms", "summary"}.
    Raises ValueError with the reason when the change cannot be made."""
    secrets = secrets or secrets_dir()
    encryption = bool(seal or unseal or new_key or algorithm)
    if bool(protocol) + encryption + bool(move_to) > 1:
        raise ValueError("a protocol, an encryption and the memory's address are not changed in one write: give one, then the next")
    if not protocol and not encryption and not move_to:
        raise ValueError("nothing to set: give a protocol, seal, new key, algorithm, unseal, or the memory's new address")
    c = memory.Client(ram_host, ram_port)
    n = None
    try:
        now = _setting_at(c, role)
        nxt = {k: v for k, v in now.items() if k != "check"}
        if protocol:
            if protocol not in ("tcp", "chorus", "openqasm"):
                raise ValueError("no protocol %r is registered here (there are: tcp, chorus, openqasm)" % protocol)
            nxt["driver"] = protocol
        elif unseal:
            nxt.pop("number", None); nxt.pop("protection", None)
        elif encryption:
            if algorithm and algorithm not in ("aes-256-gcm", "chacha20-poly1305"):
                raise ValueError("no algorithm %r is registered here (there are: aes-256-gcm, chacha20-poly1305)" % algorithm)
            was = str(now.get("number") or "")
            if new_key and was in ("", "0"):
                raise ValueError("%s is not sealed: seal turns its encryption on" % role)
            _C.secret_of(role, secrets)                     # this machine must hold a secret: it makes the setting's check
            number = was
            while number == was:
                number = str(random.SystemRandom().getrandbits(62) + 1)   # the owner's random number: the key is computed from it
            nxt["number"] = number
            nxt["protection"] = algorithm or now.get("protection") or "aes-256-gcm"
        else:
            host, port = move_to[0], int(move_to[1])
            if (host, port) == (ram_host, int(ram_port)):
                raise ValueError("the memory is at %s:%d already" % (host, port))
            nxt["ram_host"], nxt["ram_port"] = host, str(port)
        nxt.setdefault("driver", "tcp")
        nxt.pop("step", None)                               # [A_ROLES_OWN_SECRET_MOVES_FORWARD_V1]
        checked = True
        try:
            mine = _C.secret_of(role, secrets)
        except Gone:
            checked, mine = False, None
        if checked and mine["own"]:
            step = max(int(now.get("step") or 0), mine["step"])
            if nxt.get("number") and str(nxt.get("number")) != str(now.get("number") or ""):
                step += 1
            if step:
                nxt["step"] = step
        if checked:
            nxt["check"] = _C.check_of(role, nxt, secrets)
        done = {"setting": nxt, "checked": checked, "lines": [], "chat_carried": 0}
        were = {}
        if move_to:                                         # [THE_MEMORY_MOVES_V1]
            were = _ends_at(c, role)
            try:
                n = memory.Client(host, port)
            except memory.MemoryError_ as e:
                raise ValueError("nothing answers as a memory at %s:%d (%s): the memory is not moved" % (host, port, e))
            for t in c.get(0, 0.0, 0, "chat", -1):          # the chat has no owner to assert it there: the mover carries it
                n.put("chat", t["scope"], t["value"], False)
                done["chat_carried"] += 1
            n.put(VAR, role, nxt, False, service=SERVICE)   # first where everyone is going
        w = n if move_to else c
        after = max([int(t["id"]) for t in w.get(0, 0.0, 0, "at", -1, service=SERVICE)] or [0])
        wrote = c.put(VAR, role, nxt, False, service=SERVICE)
        if not move_to:
            after = int(wrote)
        here = "%s:%d" % (move_to[0], int(move_to[1])) if move_to else ""

        def matches(r):
            if move_to:
                return r.get("ram") == here
            want = str(nxt.get("number") or "0")
            return (r.get("driver") == nxt.get("driver") and str(r.get("number")) == want and (want == "0" or r.get("protection") == nxt.get("protection"))
                    and ("step" not in nxt or int(r.get("step") or 0) == int(nxt["step"])))
        t0 = time.monotonic()
        first = True
        while True:
            left = wait_s - (time.monotonic() - t0)
            if not first and left <= 0:
                break
            if not first:
                # An end that follows reports at the new address; one that ignores the move says so at the old one. So
                # for a move the read held at the new address is held half a second at a time, and the old one is
                # looked at between.
                for t in w.get(0, min(left, 0.5) if move_to else left, 1, "at", after, service=SERVICE):
                    after = max(after, int(t["id"]))
            first = False
            ends = {e: {} for e in were}                    # every end that was at the old address is expected at the new
            if move_to:
                ends.update({e: r for e, r in _ends_at(c, role).items() if r.get("ignored")})
            ends.update(_ends_at(w, role))
            if all(matches(r) or r.get("ignored") for r in ends.values()):
                break
        done["ms"] = (time.monotonic() - t0) * 1000.0
        done.update(ends=len(ends), running=0, ignoring=0, behind=0)
        for e, r in sorted(ends.items()):
            if matches(r):
                done["running"] += 1
            elif r.get("ignored"):
                done["ignoring"] += 1
                done["lines"].append("%s IGNORED it: %s" % (e, r["ignored"]))
            else:
                done["behind"] += 1
                done["lines"].append("%s %s %.0f s" % (e, "has not arrived at %s in" % here if move_to else "has not reported it in", wait_s))
        done["summary"] = "%d end(s) there: %d %s%s%s (%.0f ms after the write)" % (
            done["ends"], done["running"], "attached there" if move_to else "running it", ", %d ignored it" % done["ignoring"] if done["ignoring"] else "",
            ", %d not yet" % done["behind"] if done["behind"] else "", done["ms"])
        return done
    finally:
        c.close()
        if n is not None:
            n.close()
