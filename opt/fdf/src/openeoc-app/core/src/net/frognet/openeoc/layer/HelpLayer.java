// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
package net.frognet.openeoc.layer;

import net.frognet.ram.Follower;
import net.frognet.ram.Json;
import net.frognet.openeoc.Layer;

import java.util.HashMap;
import java.util.Map;

/**
 * [RT_LAYERS_V1] [RT_HELP_V1] help requests, as DisasterComm's help page keeps them: (help, request, <rid>) the
 * registrant's own record (status open / safe / resolved: "safe" is how a person says they are safe); (help, action,
 * <rid>~<dev>) a responder's or the EOC's action on it. Not own: a request stays when the phone that made it is gone.
 * And the reports to the EOC: (openeoc-report, <mission>, <dev>.<ms>), read by every view but the public's.
 */
public final class HelpLayer extends Layer {
    private volatile Follower helpF, reports;
    public String name() { return "help"; }

    public void start(final Here h) {
        helpF = h.follow("help", "help", null, new Runnable() { public void run() { onHelp(h); } });
        reports = h.isPublic() ? null : h.follow("reports", "openeoc-report", h.mission(), new Runnable() { public void run() { onReports(h); } });
    }
    private void onHelp(Here h) {
        Follower f = helpF; if (f == null) return;
        h.out("ev", "help", "requests", new HashMap<String, Object>(f.family("request")), "actions", new HashMap<String, Object>(f.family("action")));
    }
    private void onReports(Here h) {
        Follower f = reports; if (f == null) return;
        h.out("ev", "reports", "list", new HashMap<String, Object>(f.family(h.mission())));
    }

    public boolean handle(String op, Map<String, Object> m, Here h) throws Exception {
        if (op.equals("help")) {
            String rid = Json.str(m.get("rid"));
            if (rid.isEmpty() || helpF == null) throw new Exception("not connected");
            Map<String, Object> b = Json.obj(m.get("bag")); b.put("by", h.dev()); b.put("at", System.currentTimeMillis() / 1000);
            if (!helpF.write("request", rid, b, false)) throw new Exception("not connected");
            h.say("published: " + Json.str(b.get("status")).replace("open", "a request for help") + " for " + Json.str(b.get("name")));
        } else if (op.equals("helpAction")) {
            if (h.isPublic() || helpF == null) throw new Exception("only responders and the EOC act on requests");
            String rid = Json.str(m.get("rid"));
            Map<String, Object> b = Json.o("action", Json.str(m.get("action")), "unit", h.name(), "note", Json.str(m.get("note")), "at", System.currentTimeMillis() / 1000);
            if (!helpF.write("action", rid + "~" + h.dev(), b, false)) throw new Exception("not connected");
        } else if (op.equals("report")) {
            long ms = System.currentTimeMillis();
            Map<String, Object> b = Json.o("from", h.dev(), "name", h.name(), "category", Json.str(m.get("category")), "text", Json.str(m.get("text")),
                    "at", ms / 1000);
            if (m.get("lat") instanceof Number) { b.put("lat", Json.dbl(m.get("lat"))); b.put("lng", Json.dbl(m.get("lng"))); }
            // the public has no reports follower (it reads none): the write is one of its own, to openeoc-report
            h.writeOnce("openeoc-report", h.mission(), h.dev() + "." + ms, b);
            h.say("your report is with the EOC");
        } else return false;
        return true;
    }
}
