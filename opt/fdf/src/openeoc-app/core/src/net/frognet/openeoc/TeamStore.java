// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
package net.frognet.openeoc;

import net.frognet.ram.Follower;
import net.frognet.ram.Json;

import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.Semaphore;

/**
 * [TEAM_ON_SYSTEMRAM_V1] where a team's tuples are, for the core: its own RAM (a Follower of openeoc-team on it), or,
 * when its lead said Use SystemRam (John 2026-11-09: "the role does not have its own RAM and runs everything through
 * databasehost.frognet, using the default api.php"), the SystemRam's api.php, through the node's door: op=team (a held
 * read there: the door holds at api.php, api.php fires the door's held socket) and op=teamwrite.
 */
abstract class TeamStore {
    abstract Map<String, Map<String, Object>> family(String variable);
    abstract boolean write(String variable, String instance, Map<String, Object> bag, boolean own);
    abstract void again();
    abstract void stop();
    /** the Follower, for the diagnostics that list sessions (null for one on the SystemRam) */
    Follower follower() { return null; }

    static TeamStore of(final Follower f) {
        return new TeamStore() {
            Map<String, Map<String, Object>> family(String v) { return f.family(v); }
            boolean write(String v, String i, Map<String, Object> b, boolean own) { return f.write(v, i, b, own); }
            void again() { f.again(); }
            void stop() { f.stop(); }
            Follower follower() { return f; }
        };
    }

    /** the door's call: the query after ram.php?, a JSON body or null, the read timeout; its JSON answer when ok */
    interface Door { Map<String, Object> call(String q, String body, int readMs) throws Exception; }

    /** a team on the SystemRam: one held read at a time through the door; a failed one is said and waits for the next event */
    static final class OnSystemRam extends TeamStore {
        static final int HOLD_S = 20;
        private final String app, service, team; private final Door door; private final Follower.Log log; private final Runnable onChange;
        private final Map<String, Map<String, Object>> view = new HashMap<String, Map<String, Object>>();   // "variable\0instance" -> bag
        private final Semaphore kick = new Semaphore(0);
        private volatile boolean stopped; private volatile String bad;
        OnSystemRam(String app, String service, String team, Door door, Follower.Log log, Runnable onChange) {
            this.app = app; this.service = service; this.team = team; this.door = door; this.log = log; this.onChange = onChange;
            Thread t = new Thread(new Runnable() { public void run() { follow(); } }, "systemram-" + service + "@" + team);
            t.setDaemon(true); t.start();
        }
        private String q() { return "op=team&app=" + enc(app) + "&s=" + enc(service) + "&team=" + enc(team); }
        private void follow() {
            long last = 0; boolean first = true;
            while (!stopped) {
                try {
                    Map<String, Object> o = door.call(q() + (first ? "" : "&after=" + last + "&wait=" + HOLD_S), null, (HOLD_S + 15) * 1000);
                    long sig = (long) Json.num(o.get("last"));
                    boolean changed = first || sig != last;
                    last = sig;
                    if (changed) {
                        Map<String, Map<String, Object>> v = new HashMap<String, Map<String, Object>>();
                        List<Object> rows = Json.arr(o.get("rows"));          // the newest first: the first of each stands
                        for (Object r0 : rows) {
                            Map<String, Object> r = Json.obj(r0), bag = Json.obj(r.get("bag"));
                            String k = Json.str(r.get("variable")) + "\u0000" + Json.str(r.get("instance"));
                            if (!v.containsKey(k) && !bag.isEmpty()) v.put(k, bag);
                        }
                        synchronized (view) { view.clear(); view.putAll(v); }
                    }
                    if (first) { log.say("team " + team + " on the SystemRam (" + Json.str(o.get("at")) + "'s api.php, through the door): " + view.size() + " tuple(s)"); first = false; }
                    if (bad != null) { log.say("team " + team + " on the SystemRam: working again"); bad = null; }
                    if (changed && onChange != null) onChange.run();
                } catch (Exception e) {
                    if (stopped) break;
                    String why = e.getMessage() == null ? e.toString() : e.getMessage();
                    if (!why.equals(bad)) log.say("team " + team + " on the SystemRam: " + why + " (read again on the next event)");
                    bad = why; first = true;
                    try { kick.acquire(); kick.drainPermits(); } catch (InterruptedException x) { Thread.currentThread().interrupt(); return; }
                }
            }
        }
        Map<String, Map<String, Object>> family(String variable) {
            Map<String, Map<String, Object>> out = new HashMap<String, Map<String, Object>>();
            String pre = variable + "\u0000";
            synchronized (view) { for (Map.Entry<String, Map<String, Object>> e : view.entrySet()) if (e.getKey().startsWith(pre)) out.put(e.getKey().substring(pre.length()), e.getValue()); }
            return out;
        }
        boolean write(String variable, String instance, Map<String, Object> bag, boolean own) {
            try {
                door.call("op=teamwrite&app=" + enc(app), Json.write(Json.o("s", service, "team", team, "v", variable, "i", instance, "bag", bag)), 15000);
                return true;
            } catch (Exception e) { log.say("team " + team + " on the SystemRam: not written: " + e.getMessage()); return false; }
        }
        void again() { kick.release(); }
        void stop() { stopped = true; kick.release(); }
        private static String enc(String s) { try { return java.net.URLEncoder.encode(s, "UTF-8"); } catch (java.io.UnsupportedEncodingException e) { return s; } }
    }
}
