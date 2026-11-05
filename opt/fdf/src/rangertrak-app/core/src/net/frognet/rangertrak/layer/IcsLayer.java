// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
package net.frognet.rangertrak.layer;

import net.frognet.ram.Follower;
import net.frognet.ram.Json;
import net.frognet.rangertrak.Layer;

import java.util.Arrays;
import java.util.HashMap;
import java.util.HashSet;
import java.util.Map;
import java.util.Set;

/**
 * [RT_LAYERS_V1] [RT_ICS_BOARDS_V1] the EOC's boards, with the fields WebEOC's standard boards carry (the county and
 * state WebEOC guides: Palm Beach, Broward, Miami-Dade, Tampa, San Diego, New Hampshire, Washington EMD, Texas STAR, and
 * Juvare's own): the incident, the activity log and significant events, resource requests (ICS-213RR), situation
 * reports, shelters, road closures, damage assessment, position logs (ICS-214), press releases, sign-in/out.
 * One family: (rt-ics, <mission>.<board>, <record id>) is a record as its writer filed it, not own (it stays: a record
 * is never deleted, as on WebEOC); (rt-ics, <mission>.<board>.act, <record id>~<dev>~<ms>) each update to it -- a
 * status, a comment, an approval, a deployment -- so its history is every update, in order.
 */
public final class IcsLayer extends Layer {
    public static final Set<String> BOARDS = new HashSet<String>(Arrays.asList(
            "incident", "events", "rr", "sitrep", "shelters", "roads", "damage", "ics214", "press", "signin"));
    private volatile Follower f;
    public String name() { return "ics"; }

    public void start(final Here h) {
        f = h.follow("ics", "rt-ics", null, new Runnable() { public void run() {
            Follower x = f; if (x == null) return;
            Map<String, Object> rec = new HashMap<String, Object>(), act = new HashMap<String, Object>();
            for (String b : BOARDS) {
                rec.put(b, new HashMap<String, Object>(x.family(h.mission() + "." + b)));
                act.put(b, new HashMap<String, Object>(x.family(h.mission() + "." + b + ".act")));
            }
            h.out("ev", "ics", "rec", rec, "act", act);
        } });
    }

    public boolean handle(String op, Map<String, Object> m, Here h) throws Exception {
        if (!op.equals("icsWrite") && !op.equals("icsAct")) return false;
        if (h.isPublic() || f == null) throw new Exception("the boards are the responders' and the EOC's");
        String board = Json.str(m.get("board")), id = Json.str(m.get("id"));
        if (!BOARDS.contains(board)) throw new Exception("no board " + board);
        if (id.isEmpty() || !id.matches("[A-Za-z0-9_.:-]+")) throw new Exception("a record needs an id");
        long ms = System.currentTimeMillis();
        Map<String, Object> b = Json.obj(m.get("bag"));
        b.put("by", h.dev()); b.put("name", h.name()); b.put("position", h.role() + (h.team().isEmpty() ? "" : " " + h.team())); b.put("at", ms / 1000);
        boolean ok = op.equals("icsWrite") ? f.write(h.mission() + "." + board, id, b, false)
                                           : f.write(h.mission() + "." + board + ".act", id + "~" + h.dev() + "~" + ms, b, false);
        if (!ok) throw new Exception("not connected");
        h.say(op.equals("icsWrite") ? "filed on " + board : "updated " + board + " " + id);
        return true;
    }
}
