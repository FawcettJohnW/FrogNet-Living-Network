# Copyright (C) 2016-2026 Fawcett Innovations LLC
# SPDX-License-Identifier: GPL-2.0-only
"""The room: presence, the lobby, chat, call offers and invitations, MediaSpeed, MediaHold.

Every row is the same row the C++ room writes and reads (src/comms/room.cpp), so a Python participant and a C++ one
share one lobby. [HOLD_THE_READ_V1] Everything that READS is answered from this participant's copy of the region,
kept by ONE held read ("wake me when anything newer than write-order id N exists"); a full read only after 15 s of
quiet, because a removal or an expiry writes nothing newer."""
import random, threading, time
from dataclasses import dataclass

from .memory import Client, MemoryError_

HEARTBEAT_S, PRESENCE_FRESH_S, HOLD_S = 5, 15, 15.0


@dataclass
class Member:
    me_id: str
    name: str
    status: str
    caps: str
    addr: str
    age_s: float


@dataclass
class Line:
    from_id: str
    from_name: str
    text: str
    ts_ms: int


@dataclass
class Offer:
    session: str
    media_host: str
    offered_by: str
    media_port: int


@dataclass
class Hold:
    present: bool = False
    unanchored: bool = False
    key_held: bool = False
    passthrough: bool = False
    audio_only: bool = False
    aimed: int = 0
    delivered: int = 0
    inters_shed: int = 0
    keys_held: int = 0
    w: int = 0
    h: int = 0


def _hex(n):
    return "".join(random.choice("0123456789abcdef") for _ in range(n))


class Room:
    def __init__(self, host, port, name, caps="video,audio"):
        self.name, self.caps = name, caps
        self.me_key = name.lstrip("*")             # comms_control: the name without the '*' of an unattended source
        self.me_id = self.me_key
        self._c = Client(host, port)               # writes
        self._w = Client(host, port)               # the watcher's own session: its held read never delays a write
        self._ip = self._c.my_ip()
        self._mu = threading.Lock()
        self._cv = threading.Condition(self._mu)
        self._rows = {}                            # (var, scope) -> (tuple, monotonic time it arrived)
        self._last_id = -1
        self.version = 0
        self._status = "online"
        self._offered, self._ever_offered, self._joined = {}, set(), {}
        self._stop = False
        self.announce()
        self._absorb(self._w.get(), True)           # the starting copy
        self._watch = threading.Thread(target=self._watcher, daemon=True)
        self._watch.start()
        self._beat = threading.Thread(target=self._heartbeat, daemon=True)
        self._beat.start()

    def close(self):
        self._stop = True
        self._w.shutdown()                         # [A_HELD_READ_ENDS_AT_ONCE_V1] the held read ends now
        with self._cv:
            self._cv.notify_all()
        self._beat.join()
        self._watch.join()
        self._w.close()
        self._c.close()                            # removes this participant's own (own=True) rows

    # -- the copy of the region ------------------------------------------------------------------------------
    def _watcher(self):
        while not self._stop:
            try:
                with self._mu:
                    after = self._last_id
                ts = self._w.get(wait_s=HOLD_S, min_rows=1, after=after)
                if self._stop:
                    break
                if not ts:
                    self._absorb(self._w.get(), True)        # quiet: resync removals and ages
                else:
                    self._absorb(ts, False)
            except MemoryError_ as e:
                if self._stop:
                    break
                print("[room] watch failed: %s (retrying)" % e, flush=True)   # logged, never silent
                time.sleep(1.0)

    def _absorb(self, ts, full):
        now = time.monotonic()
        with self._cv:
            if full:
                self._rows.clear()
            for t in ts:
                self._last_id = max(self._last_id, int(t.get("id", 0)))
                self._rows[(t["var"], t["scope"])] = (t, now)
            self.version += 1
            self._cv.notify_all()

    def _write(self, var, scope, value, own):
        """[READ_YOUR_OWN_WRITES_V1] to memory AND into this participant's own copy at once (the held read's position
        is not advanced, so others' earlier writes are still delivered)."""
        i = self._c.put(var, scope, value, own=own)
        with self._cv:
            self._rows[(var, scope)] = ({"var": var, "scope": scope, "value": value, "addr": "", "age_s": 0.0, "id": i},
                                        time.monotonic())
            self.version += 1
            self._cv.notify_all()

    def wait_change(self, seen, timeout_s):
        with self._cv:
            return self._cv.wait_for(lambda: self.version != seen or self._stop, timeout_s)

    def _cached(self, var, fresh_s):
        out, now = [], time.monotonic()
        with self._mu:
            for (v, _), (t, got) in self._rows.items():
                if v != var:
                    continue
                age = float(t.get("age_s", 0)) + (now - got)
                if fresh_s > 0 and age > fresh_s:
                    continue
                t = dict(t); t["age_s"] = age
                out.append(t)
        return out

    def _heartbeat(self):
        while not self._stop:
            for _ in range(HEARTBEAT_S * 10):
                if self._stop:
                    return
                time.sleep(0.1)
            try:
                self.announce(self._status)
                self.refresh_calls()
            except MemoryError_ as e:
                print("[room] heartbeat failed: %s" % e, flush=True)   # logged, never silent

    # -- presence and the lobby --------------------------------------------------------------------------------
    def announce(self, status="online"):
        self._status = status
        self._write("presence", "host:%s:presence:%s" % (self._ip, self.me_id),
                    {"name": self.name, "status": status, "caps": self.caps, "ts": int(time.time())}, own=True)

    def roster(self):
        out = []
        for t in self._cached("presence", PRESENCE_FRESH_S):
            scope, v = t["scope"], t.get("value", {})
            i = scope.find(":presence:")
            out.append(Member(scope if i < 0 else scope[i + 10:], str(v.get("name", "")), str(v.get("status", "")),
                              str(v.get("caps", "")), t.get("addr", ""), t["age_s"]))
        return out

    # -- chat ------------------------------------------------------------------------------------------------
    def send_chat(self, session, text):
        self._write("chat", "session:chat:%s:%s" % (session, _hex(10)),
                    {"from_id": self.me_id, "from_name": self.name, "text": text, "ts": int(time.time() * 1000)},
                    own=False)                       # lossless: outlives the writer

    def read_chat(self, session):
        pre = "session:chat:%s:" % session
        out = [Line(str(t["value"].get("from_id", "")), str(t["value"].get("from_name", "")), str(t["value"].get("text", "")),
                    int(t["value"].get("ts", 0)))
               for t in self._cached("chat", 3600) if t["scope"].startswith(pre)]
        return sorted(out, key=lambda l: l.ts_ms)

    # -- the viewer's link (MediaSpeed) and what the media server does for it (MediaHold) -----------------------
    def set_media_speed(self, session, bps):
        self._write("MediaSpeed", "session:%s:viewer:%s" % (session, self.me_id),
                    {"session": session, "viewer": self.me_id, "bps": int(bps), "addr": self._ip, "ts": int(time.time())},
                    own=False)

    def my_hold(self, session):
        h = Hold()
        for t in self._cached("MediaHold", 10):
            v = t.get("value", {})
            if v.get("session") != session or v.get("viewer") != self.me_id:
                continue
            h = Hold(True, bool(v.get("unanchored")), bool(v.get("key_held")), bool(v.get("passthrough")),
                     bool(v.get("audio_only")), int(v.get("aimed", 0)), int(v.get("delivered", 0)),
                     int(v.get("inters_shed", 0)), int(v.get("keys_held", 0)), int(v.get("w", 0)), int(v.get("h", 0)))
        return h

    # -- calls -----------------------------------------------------------------------------------------------
    @staticmethod
    def _calls(m, sender=""):
        return {sid: dict({"host": h, "port": p}, **({"from": sender} if sender else {})) for sid, (h, p) in m.items()}

    def _write_announce(self):
        # [ONE_ANNOUNCEMENT_PER_PARTICIPANT_V1] [FOUR_TUPLES_V1]: keyed to the participant who offers, never to an
        # address (on the Internet two people behind home routers can both be 192.168.1.10)
        scope = "user:" + self.me_key
        current = {}
        for t in self._cached("CallsAnnounce", 30):
            if t["scope"] == scope:
                for sid, c in (t["value"].get("calls") or {}).items():
                    current[sid] = (c.get("host", ""), int(c.get("port", 0)))
                break
        with self._mu:
            for sid in self._ever_offered:
                if sid not in self._offered:
                    current.pop(sid, None)
            current.update(self._offered)
        self._write("CallsAnnounce", scope, {"user": self.me_key, "host": self._ip, "calls": self._calls(current)}, own=False)

    def _write_joined(self):
        with self._mu:
            joined = dict(self._joined)
        self._write("CallsJoined", "user:" + self.me_key, {"user": self.me_key, "calls": self._calls(joined)}, own=False)

    def offer_call(self, session, host, port):
        with self._mu:
            self._offered[session] = (host, int(port)); self._ever_offered.add(session)
        self._write_announce()

    def join_call(self, session, host, port):
        with self._mu:
            self._joined[session] = (host, int(port))
        self._write_joined()

    def invite(self, who, session, host, port):
        self._write("CallsInvitations", "user:" + who,
                    {"user": who, "calls": self._calls({session: (host, int(port))}, self.name)}, own=False)

    def refresh_calls(self):
        with self._mu:
            any_offer, any_join = bool(self._offered), bool(self._joined)
        if any_offer:
            self._write_announce()
        if any_join:
            self._write_joined()

    def offers(self):
        return [Offer(sid, c.get("host", ""), t["value"].get("user", ""), int(c.get("port", 0)))   # who offers it
                for t in self._cached("CallsAnnounce", 30) for sid, c in (t["value"].get("calls") or {}).items()]

    def calls_of(self, user):
        return [sid for t in self._cached("CallsJoined", 30) if t["scope"] == "user:" + user
                for sid in (t["value"].get("calls") or {})]

    def invitations(self):
        return [Offer(sid, c.get("host", ""), c.get("from", ""), int(c.get("port", 0)))
                for t in self._cached("CallsInvitations", 120) if t["scope"] == "user:" + self.me_key
                for sid, c in (t["value"].get("calls") or {}).items()]
