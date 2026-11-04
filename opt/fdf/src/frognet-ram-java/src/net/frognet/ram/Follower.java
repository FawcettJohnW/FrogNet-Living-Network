// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
package net.frognet.ram;

import java.util.HashMap;
import java.util.List;
import java.util.Map;

/**
 * One service of one memory, followed -- [RAM_FOLLOWER_V1] abstracted from the kart app (its Region) into this library, for every app.
 * One session; the whole service is read once, then held (after=last, wait_s=30): the memory answers when something is
 * written, and every tuple written comes back once ({} = gone, its writer's session ended). A broken session is said
 * (FAILING) and a new one opened at once; a session that cannot be opened waits until the owner says the RAM is
 * there again: again() -- [EVENTS_NOT_TIMERS_V1] the
 * owner holds the event (its door says the RAM is up, or where the RAMs are now), the follower does not retry on a
 * timer. A 409 ESTALE ([NOT_THIS_MEMORY_V1]: the RAM started again) is the RAM answering: a new session at once.
 * onUp runs on every new session, before onChange: the owner writes its own truths there again. Writes are posted on the current
 * session and never waited for [KART_ON_THE_SESSION_V1]; with own=true they go with this session [OWNED_CELL_GOES_WITH_THE_SESSION_V1].
 */
public final class Follower {
    public interface Log { void say(String line); }
    public interface Ends { boolean ended(Map<String, Map<String, Object>> view); }

    public static final double HOLD_S = 30.0;
    public final String name, host, service, api, variable;   // variable "": the whole service
    public final int port;
    private final Log log;
    private final Ends ends;
    private final Runnable onChange;
    private final Map<String, Object[]> view = new HashMap<String, Object[]>();     // "variable\u0000instance" -> {bag, t}
    private volatile RamSession mem = null;
    private volatile boolean stopped = false;
    private volatile int gen = 0;
    private volatile long writes = 0;
    private String bad = null;
    private final Thread follower;
    private final java.util.concurrent.Semaphore kick = new java.util.concurrent.Semaphore(0);   // again(): the owner's event
    private volatile Runnable onUp = null;

    public Follower(String name, String host, int port, String api, String service, Log log, Ends ends, Runnable onChange) {
        this(name, host, port, api, service, "", log, ends, onChange);
    }

    /** [RAM_FOLLOWER_V1] one variable of a service only (a reader of its own variable: an addressee's messages) */
    public Follower(String name, String host, int port, String api, String service, String variable, Log log, Ends ends, Runnable onChange) {
        this.name = name; this.host = host; this.port = port; this.api = api; this.service = service; this.variable = variable == null ? "" : variable;
        this.log = log; this.ends = ends; this.onChange = onChange;
        follower = new Thread(new Runnable() { public void run() { follow(); } }, "follow-" + service + "@" + host + ":" + port);
        follower.setDaemon(true);
        follower.start();
    }

    public int gen() { return gen; }
    public long writes() { return writes; }
    public boolean up() { return mem != null; }
    public String failing() { return bad; }
    /** [EVENTS_NOT_TIMERS_V1] the RAM is there again (the owner's event): a failed follower opens its session now */
    public void again() { if (mem == null) kick.release(); }
    /** runs on every new session of this follower (and now, if it is up): the owner's own truths are written there */
    public Follower onUp(Runnable r) { onUp = r; if (up()) r.run(); return this; }

    private void failing(String e) {
        if (bad == null || !bad.equals(e)) { log.say(name + " FAILING: " + e); bad = e; }
    }

    /** Posted: goes out, never waited for. False if there is no session right now (said by the follower). */
    public boolean write(String variable, String instance, Map<String, Object> bag, boolean own) {
        RamSession m = mem;
        if (m == null || stopped) return false;
        Map<String, Object> body = Json.o("service", service, "variable", variable, "instance", instance, "bag", bag);
        if (own) body.put("own", Boolean.TRUE);
        try { m.post("POST", api + "?op=write", Json.write(body)); writes++; return true; }
        catch (RamSession.Unreachable e) { failing(e.getMessage()); return false; }
    }

    private static String key(String v, String i) { return v + "\u0000" + i; }

    /** instance -> bag, for one variable */
    public Map<String, Map<String, Object>> family(String variable) {
        Map<String, Map<String, Object>> out = new HashMap<String, Map<String, Object>>();
        String pre = variable + "\u0000";
        synchronized (view) {
            for (Map.Entry<String, Object[]> e : view.entrySet())
                if (e.getKey().startsWith(pre)) out.put(e.getKey().substring(pre.length()), Json.obj(e.getValue()[0]));
        }
        return out;
    }

    /** instance -> when this game saw that tuple last written (System.currentTimeMillis()/1000.0) */
    public Map<String, Double> familyT(String variable) {
        Map<String, Double> out = new HashMap<String, Double>();
        String pre = variable + "\u0000";
        synchronized (view) {
            for (Map.Entry<String, Object[]> e : view.entrySet())
                if (e.getKey().startsWith(pre)) out.put(e.getKey().substring(pre.length()), (Double) e.getValue()[1]);
        }
        return out;
    }

    private Map<String, Map<String, Object>> snapshot() {
        Map<String, Map<String, Object>> out = new HashMap<String, Map<String, Object>>();
        synchronized (view) { for (Map.Entry<String, Object[]> e : view.entrySet()) out.put(e.getKey(), Json.obj(e.getValue()[0])); }
        return out;
    }

    private static List<Object> cells(Map<String, Object> o) { return Json.arr(o.containsKey("rows") ? o.get("rows") : o.get("cells")); }

    private void follow() {
        while (!stopped) {
            RamSession m;
            try { m = new RamSession(host, port, 5000); }
            catch (RamSession.Unreachable e) { failing("cannot open the session: " + e.getMessage()); held(); continue; }
            boolean first = true;
            long last = 0;
            try {
                while (!stopped) {
                    String q = api + "?op=read&service=" + RamSession.urlenc(service) + (variable.isEmpty() ? "" : "&variable=" + RamSession.urlenc(variable)) + (first ? "" : "&after=" + last + "&wait_s=" + HOLD_S);
                    Map<String, Object> o = m.call("GET", q, "", 15.0 + (first ? 0 : HOLD_S));
                    List<Object> cs = cells(o);
                    if (!first && cs.isEmpty()) continue;                      // a hold that ended with nothing written: held again
                    double now = System.currentTimeMillis() / 1000.0;
                    synchronized (view) {
                        if (first) view.clear();
                        for (Object c0 : cs) {
                            Map<String, Object> c = Json.obj(c0);
                            last = Math.max(last, Json.num(c.get("id")));
                            Map<String, Object> bag = Json.obj(c.get("bag"));
                            String k = key(Json.str(c.get("variable")), Json.str(c.get("instance")));
                            if (bag.isEmpty()) view.remove(k); else view.put(k, new Object[]{bag, now});
                        }
                    }
                    if (ends != null && ends.ended(snapshot())) { stop(); break; }
                    if (first) {
                        mem = m; gen++; first = false; kick.drainPermits();
                        if (bad != null) { log.say(name + " working again (a new session)"); bad = null; }
                        Runnable u = onUp; if (u != null) u.run();
                    }
                    if (onChange != null) onChange.run();
                }
            } catch (RamSession.Unreachable e) {
                if (!stopped) failing(e.getMessage());
                // a session that was open and ended: the RAM's listening socket outlives its host
                // ([LISTENING_SOCKET_OUTLIVES_THE_HOST_V1]), so a new session now waits in its backlog; a connect that
                // fails is what waits for the owner's event (held)
                if (!first) { if (mem == m) mem = null; m.close(); continue; }
            } catch (RamSession.Refused e) {
                if (!stopped) failing("refused: " + e.getMessage());
                if (String.valueOf(e.getMessage()).contains("ESTALE")) { if (mem == m) mem = null; m.close(); continue; }   // up: at once
            }
            if (mem == m) mem = null;
            m.close();
            if (!stopped) held();
        }
    }

    /** Ends this follower and its session: everything written with own=true goes with it. */
    public void stop() {
        stopped = true; kick.release();
        RamSession m = mem; mem = null;
        if (m != null) m.close();
    }

    private void held() { try { kick.acquire(); kick.drainPermits(); } catch (InterruptedException e) { Thread.currentThread().interrupt(); stopped = true; } }
}
