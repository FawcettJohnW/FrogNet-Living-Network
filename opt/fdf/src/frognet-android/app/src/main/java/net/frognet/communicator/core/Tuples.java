// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
package net.frognet.communicator.core;

import java.util.ArrayList;
import java.util.List;
import java.util.Locale;
import java.util.Map;

/**
 * The tuples API (defaults/api/tuples.cpp) for any service of the Communicator's memory, on a session the caller
 * holds. Room has its own copy of these for the "communicator" service; this one is for the "channel" service.
 */
final class Tuples {
    static final String API = "/comms-api";

    static final class Row { String var, scope; Map<String, Object> value; long id; double ageS; }

    private Tuples() { }

    /** Writes (service, var, scope) = value, stamping value.ts if absent. Returns the memory's write-order id. */
    static long put(RamSession s, String service, String var, String scope, Map<String, Object> value) throws RamSession.Unreachable, RamSession.Refused {
        if (!value.containsKey("ts")) value.put("ts", System.currentTimeMillis() / 1000L);
        Map<String, Object> bag = Json.o("value", value, "addr", s.localAddress());
        String body = Json.write(Json.o("service", service, "variable", var, "instance", scope, "bag", bag));
        return Json.num(s.call("POST", API + "?op=write", body, 15.0).get("id"));
    }

    /** Every tuple of a service's var written within freshS (0: any age); waitS > 0 holds the read; after >= 0: only newer. */
    static List<Row> get(RamSession s, String service, String var, int freshS, double waitS, int minRows, long after)
            throws RamSession.Unreachable, RamSession.Refused {
        StringBuilder q = new StringBuilder(API + "?op=read&service=" + RamSession.urlenc(service));
        if (!var.isEmpty()) q.append("&variable=").append(RamSession.urlenc(var));
        if (freshS > 0) q.append("&fresh_s=").append(freshS);
        if (after >= 0) q.append("&after=").append(after);
        if (waitS > 0) q.append(String.format(Locale.ROOT, "&wait_s=%.3f&min_rows=%d", waitS, minRows > 0 ? minRows : 1));
        Map<String, Object> r = s.call("GET", q.toString(), "", 15.0 + waitS);
        List<Row> out = new ArrayList<Row>();
        double now = System.currentTimeMillis() / 1000.0;
        for (Object o : Json.arr(r.get("rows"))) {
            Map<String, Object> row = Json.obj(o);
            Map<String, Object> bag = Json.obj(row.get("bag"));
            Row t = new Row();
            t.var = Json.str(row.get("variable")); t.scope = Json.str(row.get("instance"));
            t.value = Json.obj(bag.get("value"));
            t.id = Json.num(row.get("id")); t.ageS = now - Json.dbl(row.get("updated_epoch"));
            out.add(t);
        }
        return out;
    }

    static void remove(RamSession s, String service, String var, String scope) throws RamSession.Unreachable, RamSession.Refused {
        String q = API + "?op=read&service=" + RamSession.urlenc(service) + "&variable=" + RamSession.urlenc(var) + "&instance=" + RamSession.urlenc(scope);
        for (Object o : Json.arr(s.call("GET", q, "", 15.0).get("rows")))
            s.call("DELETE", API + "?op=remove&service=" + RamSession.urlenc(service) + "&variable=" + RamSession.urlenc(var)
                   + "&id=" + Json.num(Json.obj(o).get("id")), "", 15.0);
    }
}
