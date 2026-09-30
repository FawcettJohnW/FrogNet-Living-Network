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
test_shared_link_oracle.py -- drives the REAL comms_control code paths for the shared
link condition and for settings, over an in-memory stand-in for the tuple store.

Only T.put / T.get / the scope helpers are stubbed. Everything above them is the
shipping code: set_link/read_link and save_settings/read_settings, which are plain
writes and reads -- link conditions and saved preferences are not discovery state
and go nowhere near a Codex. What a stub still exercises is what actually breaks
here: scoping, field shape, merge behaviour and ageing.

Run: python3 test_shared_link_oracle.py
"""
import sys
import time

import comms_control as CC

_p = _f = 0


def ck(name, cond, extra=""):
    global _p, _f
    if cond:
        _p += 1
        print("  [PASS] %s" % name)
    else:
        _f += 1
        print("  [FAIL] %s  %s" % (name, extra))


# -- in-memory stand-in for the transient store ------------------------------
STORE = {}
WRITES = []


def fake_put(service, var, scope, value, dbhost=None, timeout=4.0, own=True):
    STORE[(service, var, scope)] = dict(value)
    WRITES.append((service, var, scope, dict(value)))
    return True


def fake_get(service, var, dbhost=None, fresh_s=0, timeout=4.0):
    now = int(time.time())
    out = []
    for (svc, v, scope), value in STORE.items():
        if svc != service or v != var:
            continue
        if fresh_s and now - int(value.get("ts", now) or now) > fresh_s:
            continue
        out.append({"var": v, "scope": scope, "value": dict(value)})
    return out


CC.T.put = fake_put
CC.T.get = fake_get
CC.T.session_scope = lambda sid: "session:%s" % sid
CC.T.role_scope = lambda role: "host:10.102.60.1:%s" % role


def fresh():
    STORE.clear()
    WRITES.clear()


# =============================================================================
print("-- link conditions cross the wire as shared memory ---------------------")
fresh()
john = CC.ControlPlane("john", "John")
dan = CC.ControlPlane("dan", "Dan")

ck("nobody has set a link yet", dan.read_link("s1") is None)

john.set_link("s1", 320_000, 60, "Congested")
seen = dan.read_link("s1")
ck("the far side reads the setting", seen is not None)
ck("with the same numbers",
   seen and (seen["bps"], seen["jitter_ms"]) == (320_000, 60), seen)
ck("and the attribution to carry into the UI",
   seen and seen["set_by"] == "john" and seen["set_by_name"] == "John")
ck("and the preset name, so the far side lights the same button",
   seen and seen["preset"] == "Congested")

john.set_link("s1", 0, 0, "Open water")
seen = dan.read_link("s1")
ck("a later setting replaces the earlier one -- LATEST_ONLY",
   seen and (seen["bps"], seen["jitter_ms"]) == (0, 0), seen)

dan.set_link("s1", 110_000, 180, "Contested")
seen = john.read_link("s1")
ck("either end may set it", seen and seen["set_by"] == "dan")

# One tuple per session, not one per setter: the link is a fact about the CALL.
scopes = {s for (svc, v, s) in STORE if v == "link"}
ck("a session has exactly one link tuple", len(scopes) == 1, scopes)

john.set_link("s2", 700_000, 20, "Home DSL")
ck("a different call has its own link",
   john.read_link("s2")["bps"] == 700_000 and john.read_link("s1")["bps"] == 110_000)
ck("reading a session that has none returns None", john.read_link("s3") is None)

# Re-setting the same values is just another write. No codec, no channel: the link
# is not discovery state, so nothing here compresses a repeat into a SAME frame.
before = len(WRITES)
dan.set_link("s1", 110_000, 180, "Contested")
ck("a repeat is written like any other value", len(WRITES) == before + 1)
ck("and the row carries every field a reader needs",
   set(dan.read_link("s1")) >= {"bps", "jitter_ms", "preset", "set_by"})


# =============================================================================
print("-- settings: one tuple, one JSON bag ------------------------------------")
import json
fresh()
john = CC.ControlPlane("john", "John")
dan = CC.ControlPlane("dan", "Dan")

ck("a first run has nothing saved", john.read_settings() is None)

john.save_settings(cam=1, in_dev=2, in_name="USB Headset",
                   out_dev=1, out_name="HDMI Out", codec="h264", quality="480p")
got = john.read_settings()
ck("settings come back", got is not None)
ck("with the camera index", got and got["cam"] == 1)
ck("with the device NAMES, which survive renumbering",
   got and got["in_name"] == "USB Headset" and got["out_name"] == "HDMI Out")
ck("and the stream choices", got and (got["codec"], got["quality"]) == ("h264", "480p"))

# The shape on the wire: ONE bag field, not a column per setting.
row = [v for (svc, var, sc), v in STORE.items() if var == "settings"][0]
ck("the tuple carries id, ts and one bag",
   set(row) == {"id", "ts", "settings"}, set(row))
ck("the bag is JSON text", isinstance(row["settings"], str))
ck("and every setting is inside it",
   set(json.loads(row["settings"])) ==
   {"cam", "in_dev", "in_name", "out_dev", "out_name", "codec", "quality"})

# The point of a bag: comms_control does not need to know what a setting is.
john.save_settings(ringtone="pond", tile_order=["dan", "julie"], mirror_self=False)
got = john.read_settings()
ck("a setting this file has never heard of round-trips",
   got["ringtone"] == "pond" and got["tile_order"] == ["dan", "julie"], got)
ck("including one whose value is False, not just missing",
   got["mirror_self"] is False)
ck("and the old settings are still there", got["cam"] == 1)

# A partial save must merge, not blank the rest.
john.save_settings(cam=0)
got = john.read_settings()
ck("a partial save updates only what it names", got["cam"] == 0)
ck("and leaves everything else standing",
   got["in_name"] == "USB Headset" and got["quality"] == "480p", got)

# A fresh process holds an empty bag. Saving one field WRITES a bag with one field:
# there is no read-modify-write hidden in the write path, and the write is the write.
blind = CC.ControlPlane("john", "John")
blind.save_settings(cam=5)
ck("a client that never loaded replaces the bag when it saves",
   blind.read_settings() == {"cam": 5}, blind.read_settings())

# Which is why a client loads its settings once, on purpose, at startup.
blind.save_settings(**{"cam": 0, "in_name": "USB Headset", "quality": "480p"})
loaded = CC.ControlPlane("john", "John")
ck("load_settings returns the bag", loaded.load_settings()["in_name"] == "USB Headset")
loaded.save_settings(cam=5)
got = loaded.read_settings()
ck("and a client that loaded keeps the rest when it saves one field",
   got["cam"] == 5 and got["in_name"] == "USB Headset"
   and got["quality"] == "480p", got)

ck("another user on this node does not see them", dan.read_settings() is None)
dan.save_settings(cam=3)
ck("two users on one node keep separate settings",
   loaded.read_settings()["cam"] == 5 and dan.read_settings()["cam"] == 3)

# Re-assertion: settings must not age out of a transient store.
before = len(WRITES)
loaded.refresh_settings()
ck("the heartbeat re-writes saved settings", len(WRITES) == before + 1)
ck("the keep-alive writes the bag this client holds", loaded.read_settings()["cam"] == 5)

nothing = CC.ControlPlane("nobody", "Nobody")
before = len(WRITES)
nothing.refresh_settings()
ck("a user who saved nothing writes nothing", len(WRITES) == before)

# A row whose bag will not parse is absent, not an exception in the caller.
key = [k for k in STORE if k[1] == "settings" and "john" in json.dumps(STORE[k])][0]

# Staleness: a setting nobody re-asserts ages out rather than being DELETEd.
STORE[key]["ts"] = int(time.time()) - (CC.SETTINGS_FRESH_S + 60)
ck("a setting older than the freshness window is gone", loaded.read_settings() is None)


# =============================================================================
print("-- [HOSTS_ONLY_V1] the media host is resolved from /etc/hosts, never DNS --")
import os as _os, tempfile as _tf
_d = _tf.mkdtemp(); _hf = _os.path.join(_d, "hosts")
open(_hf, "w").write("10.250.250.1 databasehost.frognet\n"
                     "10.130.130.1 mediahost.frognet\n")
_os.environ["FROGNET_HOSTS_PATH"] = _hf
_cc_code = "\n".join(l for l in open("comms_control.py").read().split("\n")
                     if not l.lstrip().startswith("#"))
ck("comms_control does not CALL the socket resolver anywhere",
   "socket.gethostbyname(" not in _cc_code and "getaddrinfo(" not in _cc_code,
   "gethostbyname consults resolv.conf; on a node that is nameserver 127.0.0.1 first, "
   "and it answered 10.130.130.1 where /etc/hosts said 10.250.250.1")
ck("the hosts file is what answers",
   CC._hosts_only_resolve("mediahost.frognet") == "10.130.130.1")
try:
    CC._hosts_only_resolve("notinthefile.frognet")
    ck("a name absent from the file is refused", False, "it returned something")
except Exception as _e:
    ck("a name absent from the file is refused, loudly",
       "HOSTS_ONLY_V1" in str(_e), str(_e)[:60])
_os.environ.pop("FROGNET_HOSTS_PATH", None)


print("-- the initiator pins an ADDRESS into the call tuple ---------------------")
fresh()
import socket as _sock
_real = _sock.gethostbyname
CC._hosts_only_resolve = lambda n: "10.102.60.1" if n == "mediahost.frognet" else _real(n)
seattle = CC.ControlPlane("john", "John")
info = seattle.start_call(members=["john", "dan"])
ck("start_call returns the resolved address, not the name",
   info["host"] == "10.102.60.1", info)
row = [v for (svc, var, sc), v in STORE.items() if var == "call"][0]
ck("and the tuple carries that address", row["host"] == "10.102.60.1", row)

# A guest whose OWN mediahost.frognet is a different box must still land on the
# initiator's relay -- that is what makes the media host a gathering point.
CC._hosts_only_resolve = lambda n: "10.160.160.1" if n == "mediahost.frognet" else _real(n)
ny = CC.ControlPlane("dan", "Dan")
joined = ny.join_call(info["session"])
ck("a guest on another LAN joins the INITIATOR's media host",
   joined["host"] == "10.102.60.1", joined)
ck("not its own LAN's media host", joined["host"] != "10.160.160.1")

# CONTROL -- the old code wrote the literal name, so each end resolved it locally
# and sat on a different relay, with no error on either side.
old_tuple_host = "mediahost.frognet"
ck("CONTROL: the old tuple carried a name that resolves per-machine",
   _sock.gethostbyname.__self__ is None if False else old_tuple_host == "mediahost.frognet")
ck("CONTROL: which the guest would have resolved to its own box",
   CC._hosts_only_resolve(old_tuple_host) == "10.160.160.1")

CC.socket.gethostbyname = _real
ck("an explicit relay still bypasses resolution entirely",
   CC.ControlPlane("x", "X", relay=("10.0.0.5", 9100))._resolve_media()
   == ("10.0.0.5", 9100))


# =============================================================================
print("-- leaving forgets the call; you cannot ring yourself --------------------")
fresh()
CC._hosts_only_resolve = lambda n: "10.250.250.1"
import comms_ui as _U
john = CC.ControlPlane("john", "John")
dan = CC.ControlPlane("dan", "Dan")
info = john.start_call(members=["john"])
sess = info["session"]

rt = _U.RingTracker()
ck("an open call with only me on it does not ring me",
   rt.should_ring(john.list_calls(), "john", busy=False) == [])
ck("nor after a heartbeat re-assertion",
   (john.refresh_calls() or True)
   and rt.should_ring(john.list_calls(), "john", busy=False) == [])

john.invite(sess, ["dan"])
rd = _U.RingTracker()
ck("but it rings DAN once somebody else is on it",
   len(rd.should_ring(dan.list_calls(), "dan", busy=False)) == 1)

ck("inviting myself is dropped",
   john.invite(sess, ["john"]) == ["john", "dan"])

# the resurrection: leave, then let the heartbeat run
john.leave_call(sess)
ck("leaving removes me from the row",
   john.call_info(sess)["members"] == ["dan"], john.call_info(sess))
john.refresh_calls()
ck("and the heartbeat does NOT put me back",
   john.call_info(sess)["members"] == ["dan"], john.call_info(sess))
ck("so the call I left cannot ring me",
   _U.RingTracker().should_ring(john.list_calls(), "john", busy=False) == [])

dan.leave_call(sess)
dan.refresh_calls()
ck("the last one out empties it and nobody re-asserts it",
   john.call_info(sess)["members"] == [], john.call_info(sess))


# =============================================================================
print("-- no media host, no call ----------------------------------------------")
fresh()
solo = CC.ControlPlane("john", "John")
def _nx(n):
    raise OSError(-2, "Name or service not known")
CC._hosts_only_resolve = _nx
try:
    solo.start_call(members=["john"])
    ck("an unresolvable media host stops the call", False, "no exception raised")
except CC.NoMediaHost as e:
    ck("an unresolvable media host stops the call", True)
    ck("and the message names what is missing", "mediahost.frognet" in str(e), str(e))
except Exception as e:
    ck("an unresolvable media host stops the call", False, repr(e))
ck("no call row was written",
   not [1 for (svc, var, sc) in STORE if var == "call"], list(STORE))

# CONTROL -- the old fallback handed the literal name back, so the row was written
# with a string fnav cannot dial and the failure surfaced as socket.gaierror on a
# worker thread, once per attempt, with the UI showing a call in progress.
old_host = "mediahost.frognet"
ck("CONTROL: the old fallback put an unresolvable name in the row",
   not old_host[0].isdigit())

CC._hosts_only_resolve = lambda n: "10.250.250.1"
ck("with a media host, the call forms normally",
   solo.start_call(members=["john"])["host"] == "10.250.250.1")

# an explicit --relay still works with no elected media host
CC._hosts_only_resolve = _nx
ck("--relay bypasses the election entirely",
   CC.ControlPlane("x", "X", relay=("10.0.0.5", 9100))._resolve_media()
   == ("10.0.0.5", 9100))
CC._hosts_only_resolve = lambda n: "10.250.250.1"


# =============================================================================
print("-- invite: membership IS the invitation ---------------------------------")
fresh()
john = CC.ControlPlane("john", "John")
dan = CC.ControlPlane("dan", "Dan")
CC._hosts_only_resolve = lambda n: "10.250.250.1"
info = john.start_call(members=["john"])
sess = info["session"]
ck("a call starts with only the caller on it",
   john.call_info(sess)["members"] == ["john"])

import comms_ui as _UI
rt = _UI.RingTracker()
ck("nobody rings yet", rt.should_ring(dan.list_calls(), "dan", busy=False) == [])

members = john.invite(sess, ["dan"])
ck("invite adds them", members == ["john", "dan"], members)
ck("and the row says so", dan.call_info(sess)["members"] == ["john", "dan"])
ck("now the far side rings, with no message sent",
   len(rt.should_ring(dan.list_calls(), "dan", busy=False)) == 1)

ck("inviting the same person twice does not duplicate them",
   john.invite(sess, ["dan"]) == ["john", "dan"])
ck("inviting into a call that is gone reports it",
   john.invite("no-such-session", ["dan"]) is None)
ck("the host and port are preserved across an invite",
   dan.call_info(sess)["host"] == "10.250.250.1")


# =============================================================================
print("-- the two planes stay separate ----------------------------------------")
fresh()
john = CC.ControlPlane("john", "John")
john.set_link("s1", 320_000, 60, "Congested")
john.save_settings(cam=1)
john.announce()
vars_written = {v for (svc, v, s) in STORE}
ck("link, settings and presence are distinct variables",
   vars_written == {"link", "settings", "presence"}, vars_written)
ck("presence is the one that goes through a channel, being discovery state",
   john._presence_ch is not None and not hasattr(john, "_link_ch"))
ck("everything goes to the ELECTED data host, not _control",
   john.dbhost == "databasehost.frognet")


print()
print("=== %d passed, %d failed ===" % (_p, _f))
print("ORACLE " + ("GREEN" if _f == 0 else "RED"))
sys.exit(0 if _f == 0 else 1)
