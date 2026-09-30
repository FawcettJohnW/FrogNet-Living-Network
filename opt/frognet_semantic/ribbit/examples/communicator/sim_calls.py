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
sim_calls.py -- [FOUR_TUPLES_V1] two clients, one call, through the real store.

    CallsAnnounce/host:<ip>      what this host offers
    CallsInProgress/host:<ip>    what this media host sees carrying traffic
    CallsJoined/user:<name>      what this user says they are in
    CallsInvitations/user:<name> what this user has been asked to join

Real ControlPlane, real Codex, real Channel, real frognet_tuples.put/get over
HTTP to a stub answering exactly what api.php answers. The only fake is the
database. Every previous test of this path replaced put/get with a dict, which
is how two codex defects shipped without a single red assertion.

  C1  announcing lands ONE row, and it carries the calls map
  C2  a client that created nothing SEES the announced call
  C3  joining is one fact about oneself; membership is the union of joiners
  C4  ONE session, not two, when the second client joins
  C5  leaving is ceasing -- and it removes only the leaver
  C6  crashing (never refreshing) is the same as leaving, a little slower
  C7  an invitation lands in the INVITEE's row and names its maker
  C8  answering removes it -- join and decline leave the same trace
  C9  INPROGRESS is the media host's view and does not correct JOINED
  C10 a restart announces into the SAME key, leaving no ghost behind
  C11 no row anywhere carries a None in a declared field
"""
import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, "/home/claude/src2/opt/frognet_semantic")
sys.path.insert(0, ".")

FAIL = []


def ck(name, cond, got=None):
    if cond:
        print("  PASS  %s" % name)
    else:
        print("  FAIL  %s   (got %r)" % (name, got))
        FAIL.append(name)


ROWS, NEXT_ID, NOW = {}, [1], [int(time.time())]


class Api(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, o):
        b = json.dumps(o).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        q = json.loads(self.rfile.read(n).decode() or "{}")
        name = q.get("SensorName")
        row = ROWS.get(name)
        if row is None:
            row = ROWS[name] = {"SensorID": NEXT_ID[0], "SensorName": name}
            NEXT_ID[0] += 1
        row.update(SensorType=q.get("SensorType"),
                   SensorAddress=q.get("SensorAddress"),
                   jsonData=json.dumps(q.get("jsonData")),
                   UpdatedAtEpoch=NOW[0])
        self._send({"ok": True})

    def do_GET(self):
        from urllib.parse import urlparse, parse_qs
        p = parse_qs(urlparse(self.path).query)
        svc = (p.get("SensorType") or [None])[0]
        fresh = int((p.get("fresh_s") or [0])[0])
        out = [dict(r) for r in ROWS.values()
               if (not svc or r.get("SensorType") == svc)
               and not (fresh and (NOW[0] - r["UpdatedAtEpoch"]) > fresh)]
        self._send({"rows": out})


srv = HTTPServer(("127.0.0.1", 0), Api)
threading.Thread(target=srv.serve_forever, daemon=True).start()
DB = "127.0.0.1:%d" % srv.server_port

from core import frognet_tuples as T          # noqa: E402
import comms_control as CC                    # noqa: E402

CC.T = T
T.my_ip = lambda: "10.250.250.20"
RELAY = ("10.160.160.1", 9000)


def plane(name, ip="10.250.250.20"):
    cp = CC.ControlPlane(name.lower() + "-" + hex(id(name))[-4:], name,
                         dbhost=DB)
    cp._resolve_media = lambda: RELAY
    return cp


def rows_of(var):
    return [r for r in T.get("communicator", var, dbhost=DB, fresh_s=120)]


dave = plane("Dave")
john = plane("John")

# ---- C1 ---------------------------------------------------------------------
call = dave.start_call()
sid = call["session"]
ann = rows_of("CallsAnnounce")
ck("C1 announcing lands one row", len(ann) == 1, len(ann))
ck("C1 scoped to the host, not the session",
   ann and ann[0]["scope"] == "host:10.250.250.20", ann and ann[0]["scope"])
ck("C1 and the row carries the call",
   ann and sid in (ann[0]["value"].get("calls") or {}), ann and ann[0]["value"])
ck("C1 with where to connect",
   ann and ann[0]["value"]["calls"][sid]["host"] == RELAY[0], ann)

# ---- C2/C3 -------------------------------------------------------------------
seen = john.list_calls()
ck("C2 a client that created nothing SEES it",
   len(seen) == 1 and seen[0]["session"] == sid, seen)
ck("C3 with the originator listed on it",
   seen and seen[0]["members"] == ["Dave"], seen and seen[0]["members"])

joined = john.join_call(sid)
ck("C4 joining returns where to point fnav",
   joined and joined["host"] == RELAY[0], joined)
seen = john.list_calls()
ck("C4 ONE session, not two", len(seen) == 1, seen)
ck("C3 membership is the union of joiners",
   seen[0]["members"] == ["Dave", "John"], seen[0]["members"])
ck("C3 and Dave sees the same", dave.list_calls() == seen, dave.list_calls())
ck("C3 joined rows are per USER",
   sorted(r["scope"] for r in rows_of("CallsJoined"))
   == ["user:Dave", "user:John"],
   sorted(r["scope"] for r in rows_of("CallsJoined")))

# ---- C7 ----------------------------------------------------------------------
sue = plane("Sue")
dave.invite(sid, ["Sue"])
inv = sue.invitations_for_me()
ck("C7 the invitation reaches the invitee", len(inv) == 1, inv)
ck("C7 in the INVITEE's own row",
   any(r["scope"] == "user:Sue" for r in rows_of("CallsInvitations")),
   [r["scope"] for r in rows_of("CallsInvitations")])
ck("C7 and it names who asked", inv and inv[0]["from"] == "Dave", inv)
ck("C7 nobody else is invited", john.invitations_for_me() == [],
   john.invitations_for_me())
ck("C7 an invitation is not membership",
   "Sue" not in dave.list_calls()[0]["members"], dave.list_calls()[0]["members"])

# ---- C8 ----------------------------------------------------------------------
sue.decline(sid)
ck("C8 declining removes it", sue.invitations_for_me() == [],
   sue.invitations_for_me())
dave.invite(sid, ["Sue"])
ck("C8 a re-invite arrives again", len(sue.invitations_for_me()) == 1,
   sue.invitations_for_me())
sue.join_call(sid)
ck("C8 answering removes it too -- same trace as declining",
   sue.invitations_for_me() == [], sue.invitations_for_me())
ck("C8 and now she IS a member",
   "Sue" in dave.list_calls()[0]["members"], dave.list_calls()[0]["members"])

# ---- C9 ----------------------------------------------------------------------
mh = plane("MediaHost")
mh.report_in_progress({sid: {"viewers": 2, "since": NOW[0]}})
prog = john.calls_in_progress()
ck("C9 the media host's view is readable", sid in prog, prog)
ck("C9 and it says which host reported it",
   prog.get(sid, {}).get("mediahost") == "10.250.250.20", prog)
ck("C9 live_only keeps a call the host confirms",
   len(john.list_calls(live_only=True)) == 1, john.list_calls(live_only=True))
mh.report_in_progress({})
ck("C9 INPROGRESS does not correct JOINED",
   john.list_calls()[0]["members"] == ["Dave", "John", "Sue"],
   john.list_calls()[0]["members"])
ck("C9 but live_only now shows nothing carrying traffic",
   john.list_calls(live_only=True) == [], john.list_calls(live_only=True))

# ---- C5 ----------------------------------------------------------------------
john.leave_call(sid)
mem = dave.list_calls()[0]["members"]
ck("C5 leaving removes only the leaver", mem == ["Dave", "Sue"], mem)
ck("C5 and the call is still there", len(dave.list_calls()) == 1,
   dave.list_calls())

# ---- C6 ----------------------------------------------------------------------
for _ in range(3):
    NOW[0] += 60
    dave.refresh_calls()          # Sue does not
mem = dave.list_calls()[0]["members"]
ck("C6 a client that stopped refreshing is gone", mem == ["Dave"], mem)
ck("C6 and the one still asserting is not", len(dave.list_calls()) == 1,
   dave.list_calls())

# ---- C10 ---------------------------------------------------------------------
before = len(rows_of("CallsAnnounce"))
dave2 = plane("Dave")             # restart: new me_id, same name, same host
dave2.announce_call(sid, *RELAY)
ck("C10 a restart writes the SAME announce key, no ghost",
   len(rows_of("CallsAnnounce")) == before, len(rows_of("CallsAnnounce")))
dave2.join_call_at(sid, *RELAY)
ck("C10 and the same joined key -- identity is the name",
   [r["scope"] for r in rows_of("CallsJoined") if r["value"].get("user") == "Dave"]
   == ["user:Dave"],
   [r["scope"] for r in rows_of("CallsJoined")])

# ---- C12: [MEDIACONTROL_BELONGS_TO_THE_CALL_V1] --------------------------
# The call's media settings are the CALL's, in one row everybody reads. Move the
# slider and every picture follows, because the memory changed -- nobody sent a
# message to anybody. MediaSpeed was per viewer per session and MediaTreatment
# per producer per viewer: N x M opinions to reconcile, and a client on the
# wrong end of a pair read none of them.
ck("C12 no settings until somebody sets them",
   dave2.media_control(sid) is None, dave2.media_control(sid))

dave2.set_media_control(sid, speed_bps=408_000, geometry="640x360")
mc = john.media_control(sid)
ck("C12 a participant reads what another set",
   mc and mc["speed_bps"] == 408_000, mc)
ck("C12 including the geometry", mc and mc["geometry"] == "640x360", mc)
ck("C12 and who set it", mc and mc["set_by"] == "Dave", mc)
ck("C12 one row for the call, not one per pair",
   len(rows_of("CallsMediaControl")) == 1,
   [r["scope"] for r in rows_of("CallsMediaControl")])
ck("C12 scoped to the call id",
   rows_of("CallsMediaControl")[0]["scope"] == "call:%s" % sid,
   rows_of("CallsMediaControl")[0]["scope"])

# current value wins: the last writer is the setting, no reconciliation
john.set_media_control(sid, speed_bps=0, geometry="auto")
mc = dave2.media_control(sid)
ck("C12 current value wins -- the other end's change is simply the value",
   mc and mc["speed_bps"] == 0 and mc["geometry"] == "auto", mc)
ck("C12 and it names the new setter", mc and mc["set_by"] == "John", mc)

ck("C12 another call's settings are its own",
   dave2.media_control("no-such-session") is None,
   dave2.media_control("no-such-session"))


# ---- C13: [A_CHANNEL_BELONGS_TO_ONE_CODEX_V1] ------------------------------
# ONE plane writing BOTH host-scoped rows. The channel cache was keyed on scope
# alone, so the second codex reused the first's channel and its fields landed in
# a structure that did not declare them. What reached the store was the OTHER
# codex's fields, all null, with no error at either end. Read out of the live
# database 2026-08-11:
#   SD:CallsAnnounce.host:10.102.60.1
#     {"addr":null,"awaiting_keyframe":null,"cap_bps":null,"frames_aimed":null,
#      "frames_delivered":null,...,"reason":<ts>}
# Backpressure fields, in a call row. No plane in this sim wrote both, so it
# passed while production was writing rubbish.
solo = plane("Solo")
solo.announce_call("sess-both", *RELAY)
solo.report_in_progress({"sess-both": {"viewers": 1}})
solo.announce_call("sess-both2", *RELAY)

a = [r for r in rows_of("CallsAnnounce") if r["value"].get("host")]
p = rows_of("CallsInProgress")
ck("C13 one writer, both host rows: announce still carries its own fields",
   any("sess-both2" in (r["value"].get("calls") or {}) for r in a),
   [r["value"] for r in a])
ck("C13 and in-progress carries its own",
   any("sess-both" in (r["value"].get("calls") or {}) for r in p),
   [r["value"] for r in p])
ck("C13 neither row picked up the other's fields",
   all(set(r["value"]) <= {"host", "calls", "ts"} for r in a + p),
   [sorted(r["value"]) for r in a + p])

# ---- C14: [THE_KEY_IS_THE_NAME_NOT_THE_DECORATION_V1] ----------------------
# fnav's publisher passes "*" + name to mark an unattended source. me_name is a
# DISPLAY string; keying the tuples on it put the marker in the key --
# SD:CallsJoined.user:*Dave -- which no reader looking for "Dave" matches.
star = plane("*Dave")
star.join_call_at("sess-star", *RELAY)
scopes = [r["scope"] for r in rows_of("CallsJoined")]
ck("C14 the marker does not reach the key", "user:*Dave" not in scopes, scopes)
ck("C14 the plain name does", "user:Dave" in scopes, scopes)
ck("C14 but the display name keeps it", star.me_name == "*Dave", star.me_name)


# ---- C15: [THE_PIPE_IS_SHARED_V1] ------------------------------------------
# The call's speed is the WHOLE pipe. With both ends sending, each arming the
# full number puts twice the setting on the wire, and the two ladders then fight
# congestion they jointly caused, each blaming its own link.
share_sess = dave2.start_call()["session"]
john.join_call(share_sess)
dave2.set_media_control(share_sess, speed_bps=1_000_000)

d = dave2.media_share(share_sess)
j = john.media_share(share_sess)
ck("C15 both ends see the same pipe", d["total_bps"] == j["total_bps"] == 1_000_000,
   (d["total_bps"], j["total_bps"]))
ck("C15 and the same sender count", d["senders"] == j["senders"] == 2,
   (d["senders"], j["senders"]))
ck("C15 each takes half, computed independently",
   d["share_bps"] == j["share_bps"] == 500_000, (d["share_bps"], j["share_bps"]))
ck("C15 the shares sum to the pipe, not double it",
   d["share_bps"] + j["share_bps"] == d["total_bps"], None)

# a third joiner re-divides it, with nobody telling anybody
sue.join_call(share_sess)
d = dave2.media_share(share_sess)
ck("C15 a third sender re-divides it for everyone", d["senders"] == 3
   and d["share_bps"] == 333_333, (d["senders"], d["share_bps"]))

# and leaving gives it back
sue.leave_call(share_sess)
d = dave2.media_share(share_sess)
ck("C15 leaving returns the share to those left",
   d["senders"] == 2 and d["share_bps"] == 500_000,
   (d["senders"], d["share_bps"]))

dave2.set_media_control(share_sess, speed_bps=0)
d = dave2.media_share(share_sess)
ck("C15 unconstrained divided by anything is still unconstrained",
   d["total_bps"] == 0 and d["share_bps"] == 0, d)


# ---- C16: [PRODUCER_LEADS_CONSUMERS_REPORT_V1] ----------------------------
# Two roles. A producer sends and does not receive: no measurement, no vote,
# starts at what its device can do. A consumer reports what it is getting and
# whether that is good enough. Down on the worst, up only on unanimity.
#
# Treating them as peers let a headless publisher with no viewers cap itself on
# its own absent downlink -- "slowest consumer is Dave", measured 2026-08-11 --
# and walk a good link to 160x120 on evidence that did not exist.
pc = dave2.start_call()["session"]
john.join_call(pc)
sue.join_call(pc)

dave2.report_capability(pc, 1920, 1080, 24.0)
dave2.report_sending(pc, 1920, 1080, 24.0)
p = john.producer_sending(pc)
ck("C16 a consumer reads what the producer says it is sending",
   p and (p["w"], p["h"]) == (1920, 1080), p)
ck("C16 and who is producing", p and p["producer"] == "Dave", p)

fb = dave2.consumer_feedback(pc)
ck("C16 no reports yet -> no unanimity to climb on",
   fb["consumers"] == 0 and fb["all_happy"] is False, fb)

john.report_receiving(pc, 1920, 1080, 23.0, True, False)
sue.report_receiving(pc, 1920, 1080, 4.0, False, True)
fb = dave2.consumer_feedback(pc)
ck("C16 one unhappy consumer blocks the climb", fb["all_happy"] is False, fb)
ck("C16 and is named, so the producer knows who it steps down for",
   [u["user"] for u in fb["unhappy"]] == ["Sue"], fb["unhappy"])
ck("C16 the slowest is the slowest, not the latest",
   fb["slowest"]["user"] == "Sue", fb["slowest"])

sue.report_receiving(pc, 854, 480, 23.5, True, False)
fb = dave2.consumer_feedback(pc)
ck("C16 when every consumer is steady the network may climb",
   fb["all_happy"] is True and fb["consumers"] == 2, fb)
ck("C16 the producer does not report on itself",
   "Dave" not in [r["user"] for r in fb["reports"]],
   [r["user"] for r in fb["reports"]])

# a consumer that stops asserting stops constraining -- same rule as membership
john.leave_call(pc)
fb = dave2.consumer_feedback(pc)
ck("C16 a departed consumer has no vote",
   [r["user"] for r in fb["reports"]] == ["Sue"], fb["reports"])


# [NOT_YET_STABLE_IS_NOT_OVERLOADED_V1] A consumer that is keeping up but has
# not yet proven it steady must NOT read as a complaint. Measured 2026-08-11:
# "John is not keeping up (55.4 fps at 1920x1080)" -- fifty-five frames a second
# on a rate of twenty-four, and the network stepped down on it.
john.join_call(pc)          # he left in the departure test just above
john.report_receiving(pc, 1920, 1080, 55.4, False, False)   # fine, not yet steady
fb = dave2.consumer_feedback(pc)
ck("C16 keeping up but not yet steady is not a complaint",
   [u["user"] for u in fb["unhappy"]] == [], fb["unhappy"])
ck("C16 and it does not count as unanimity either",
   fb["all_happy"] is False, fb)
john.report_receiving(pc, 1920, 1080, 2.0, False, True)     # genuinely behind
fb = dave2.consumer_feedback(pc)
ck("C16 genuinely behind IS a complaint",
   [u["user"] for u in fb["unhappy"]] == ["John"], fb["unhappy"])

# ---- C17: [EVERYONE_PUBLISHES_THEIR_OWN_CAPABILITY_V1] --------------------
sue.report_capability(pc, 1280, 720, 30.0)
srow = [r for r in rows_of("CallsJoined") if r["value"].get("user") == "Sue"]
ck("C17 capability rides in the participant's own row",
   srow and (srow[0]["value"]["calls"][pc].get("can_do") or {}).get("w") == 1280,
   srow and srow[0]["value"]["calls"][pc])
ck("C17 alongside the report, not instead of it",
   srow and "getting" in srow[0]["value"]["calls"][pc],
   srow and sorted(srow[0]["value"]["calls"][pc]))


# ---- C18: [NO_SYNC_JUST_STATE_V1] -----------------------------------------
# Each end publishes only what it knows about ITSELF. Everyone reads all of it
# and derives the same rate on their own. No request, no reply, nobody waiting.
#
# A request tuple existed for one revision -- a consumer wrote "I want 640x360"
# and a sender complied. That is a negotiation with the word filed off.
st = dave2.start_call()["session"]
john.join_call(st)
sue.join_call(st)

ck("C18 there is no request tuple to write",
   not hasattr(dave2, "report_request"), None)
ck("C18 and none to read", not hasattr(dave2, "requested_rate"), None)

dave2.report_capability(st, 1920, 1080, 24.0)
dave2.report_sending(st, 640, 360, 24.0)
john.report_receiving(st, 640, 360, 23.5, True, False)
sue.report_receiving(st, 640, 360, 6.0, False, True)

# every participant reads the same two facts
_p = john.producer_sending(st)
_f = john.consumer_feedback(st)
ck("C18 the producer's own state is readable by all",
   _p and (_p["w"], _p["h"]) == (640, 360), _p)
ck("C18 and every consumer's",
   sorted(r["user"] for r in _f["reports"]) == ["John", "Sue"],
   [r["user"] for r in _f["reports"]])
ck("C18 including who is not keeping up",
   [u["user"] for u in _f["unhappy"]] == ["Sue"], _f["unhappy"])
ck("C18 sue's trouble does not make john's report disagree",
   john.consumer_feedback(st) == sue.consumer_feedback(st)
   == dave2.consumer_feedback(st), None)

# the derivation each end runs, from that state alone
_lad = [{"w": 1920, "h": 1080}, {"w": 1280, "h": 720}, {"w": 854, "h": 480},
        {"w": 640, "h": 360}, {"w": 480, "h": 360}]


def _derive(psend, fb, cur=None):
    """The rule: producer's rate bounds it, a struggling consumer lowers it."""
    def idx_of(g):
        px = g["w"] * g["h"]
        for n, x in enumerate(_lad):
            if x["w"] * x["h"] <= px:
                return n
        return len(_lad) - 1
    base = cur or (psend and {"w": psend["w"], "h": psend["h"]})
    if not base:
        return None
    i = idx_of(base)
    if psend:
        i = max(i, idx_of(psend))
    if fb["unhappy"]:
        i += 1
    elif fb["all_happy"] and i > 0:
        i -= 1
    return _lad[max(0, min(len(_lad) - 1, i))]


ck("C18 a struggling consumer lowers the rate, with nobody asking",
   _derive(_p, _f) == {"w": 480, "h": 360}, _derive(_p, _f))

sue.report_receiving(st, 640, 360, 23.8, True, False)
_f = john.consumer_feedback(st)
ck("C18 and everyone steady raises it, with nobody asking",
   _derive(_p, _f) == {"w": 854, "h": 480}, _derive(_p, _f))

ck("C18 every end derives the same answer from the same rows",
   _derive(john.producer_sending(st), john.consumer_feedback(st))
   == _derive(sue.producer_sending(st), sue.consumer_feedback(st)), None)


# ---- C20: [SEND_NO_FASTER_THAN_THE_SLOWEST_SENDER_V1] ---------------------
# producer_sending returned the FIRST row with role=producer, so only one end's
# rate bounded anything and a second sender could sit well above it. Measured
# 2026-08-11: one end at 640x360 while the other pushed 1920x1080 the opposite
# way on the same call.
#
# And the role stamp made a two-way end invisible: writing `getting` relabelled
# it "consumer", so its own send rate stopped counting. The presence of the FACT
# is the role now -- `sending` means sending, `getting` means receiving, both
# means both.
ss = dave2.start_call()["session"]
john.join_call(ss)
dave2.report_sending(ss, 1920, 1080, 24.0)
john.report_sending(ss, 640, 360, 24.0)
john.report_receiving(ss, 1920, 1080, 3.0, False, True)

_low = sue.producer_sending(ss)
ck("C20 the LOWEST sender bounds the call, not the first row found",
   _low and (_low["w"], _low["h"]) == (640, 360), _low)
ck("C20 and it names which end", _low and _low["producer"] == "John", _low)

_fb2 = sue.consumer_feedback(ss)
ck("C20 a two-way end is still counted as a consumer",
   [r["user"] for r in _fb2["reports"]] == ["John"], _fb2["reports"])
ck("C20 its send rate survives writing a receive report",
   sue.producer_sending(ss)["producer"] == "John", None)

dave2.report_sending(ss, 320, 240, 24.0)
_low = john.producer_sending(ss)
ck("C20 a sender dropping lower re-bounds everybody",
   (_low["w"], _low["h"]) == (320, 240), _low)

# ---- C11 ---------------------------------------------------------------------
bad = [(r["name"], k) for var in ("CallsAnnounce", "CallsJoined",
                                  "CallsInvitations", "CallsInProgress",
                                  "CallsMediaControl")
       for r in rows_of(var) for k, v in r["value"].items() if v is None]
ck("C11 no declared field converged to None", not bad, bad)

print()
if FAIL:
    print("FAILED %d: %s" % (len(FAIL), ", ".join(FAIL)))
    sys.exit(1)
print("ALL PASS")
