// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
package net.frognet.openeoc.layer;

import net.frognet.ram.Follower;
import net.frognet.ram.Json;
import net.frognet.openeoc.Layer;

import java.util.HashMap;
import java.util.Map;

/**
 * [RT_LAYERS_V1] [RT_FIELD_V1] what teams do in the field: (rt-field, <mission>, <dev>.<ms>) a deployed sensor platform,
 * a request for services, a hazard, a searched area -- its writer's record, not own (it stays when the phone goes);
 * (rt-field, <mission>.act, <item>~<dev>) the EOC's or a team's action on one (acknowledged, dispatched, done).
 */
public final class FieldLayer extends Layer {
    private volatile Follower fieldF;
    public String name() { return "field"; }

    public void start(final Here h) {
        fieldF = h.follow("field", "rt-field", null, new Runnable() { public void run() {
            Follower f = fieldF; if (f == null) return;
            h.out("ev", "field", "items", new HashMap<String, Object>(f.family(h.mission())), "acts", new HashMap<String, Object>(f.family(h.mission() + ".act")));
        } });
    }

    public boolean handle(String op, Map<String, Object> m, Here h) throws Exception {
        if (op.equals("field")) {
            if (h.isPublic() || fieldF == null) throw new Exception("field actions are the teams' and the EOC's");
            long ms = System.currentTimeMillis();
            Map<String, Object> b = Json.obj(m.get("bag"));
            b.put("by", h.dev()); b.put("name", h.name()); b.put("team", h.team()); b.put("at", ms / 1000);
            if (!fieldF.write(h.mission(), h.dev() + "." + ms, b, false)) throw new Exception("not connected");
            h.say("sent: " + Json.str(b.get("what")) + " " + Json.str(b.get("type")));
        } else if (op.equals("fieldAction")) {
            if (h.isPublic() || fieldF == null) throw new Exception("not connected");
            Map<String, Object> b = Json.o("action", Json.str(m.get("action")), "unit", h.name(), "note", Json.str(m.get("note")), "at", System.currentTimeMillis() / 1000);
            if (!fieldF.write(h.mission() + ".act", Json.str(m.get("id")) + "~" + h.dev(), b, false)) throw new Exception("not connected");
        } else return false;
        return true;
    }
}
