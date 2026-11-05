// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
package net.frognet.rangertrak.layer;

import net.frognet.ram.Follower;
import net.frognet.ram.Json;
import net.frognet.rangertrak.Layer;

import java.util.HashMap;
import java.util.Map;

/**
 * [RT_LAYERS_V1] [RT_FACILITY_V1] each hospital's own status (beds by type, diversion, power, water, oxygen, staff,
 * needs): (rt-facility, <mission>, <facility>), written by whoever runs that facility's desk, not own (it stays);
 * [RT_TRANSPORT_V1] each patient transport, (rt-transport, <mission>, <dev>.<ms>), the unit's record, and the hospital's
 * acknowledgement (rt-transport, <mission>.act, <id>~<dev>). The Hospital view (its desk: on the map as staff) and the
 * Ambulance view.
 */
public final class MedicalLayer extends Layer {
    private volatile Follower facF, trF;
    public String name() { return "medical"; }
    public Map<String, Role> roles() { return roles("hospital", new Role("unit", "staff", false), "ambulance", new Role("unit", "ambulance", false)); }

    public void start(final Here h) {
        facF = h.follow("facilities", "rt-facility", h.mission(), new Runnable() { public void run() {
            Follower f = facF; if (f != null) h.out("ev", "facilities", "list", new HashMap<String, Object>(f.family(h.mission())));
        } });
        trF = h.follow("transports", "rt-transport", null, new Runnable() { public void run() {
            Follower f = trF; if (f == null) return;
            h.out("ev", "transports", "items", new HashMap<String, Object>(f.family(h.mission())), "acts", new HashMap<String, Object>(f.family(h.mission() + ".act")));
        } });
    }

    public boolean handle(String op, Map<String, Object> m, Here h) throws Exception {
        String unit = h.team().isEmpty() ? h.name() : h.team();
        if (op.equals("facility")) {
            if (facF == null) throw new Exception("not connected");
            Map<String, Object> b = Json.obj(m.get("bag")); b.put("by", h.name()); b.put("at", System.currentTimeMillis() / 1000);
            if (!facF.write(h.mission(), Json.str(m.get("id")), b, false)) throw new Exception("not connected");
            h.say("published the status of " + Json.str(b.get("name")));
        } else if (op.equals("transport")) {
            if (trF == null) throw new Exception("not connected");
            long ms = System.currentTimeMillis();
            Map<String, Object> b = Json.obj(m.get("bag")); b.put("by", h.dev()); b.put("unit", unit); b.put("name", h.name()); b.put("at", ms / 1000);
            if (!trF.write(h.mission(), h.dev() + "." + ms, b, false)) throw new Exception("not connected");
            h.say("transport to " + Json.str(b.get("to")) + " sent: the hospital sees it now");
        } else if (op.equals("transportAction")) {
            if (trF == null) throw new Exception("not connected");
            Map<String, Object> b = Json.o("action", Json.str(m.get("action")), "unit", unit, "note", Json.str(m.get("note")), "at", System.currentTimeMillis() / 1000);
            if (!trF.write(h.mission() + ".act", Json.str(m.get("id")) + "~" + h.dev(), b, false)) throw new Exception("not connected");
        } else return false;
        return true;
    }
}
