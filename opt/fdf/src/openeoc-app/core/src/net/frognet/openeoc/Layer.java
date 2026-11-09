// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
package net.frognet.openeoc;

import net.frognet.ram.Follower;

import java.util.HashMap;
import java.util.Map;

/**
 * [RT_LAYERS_V1] a layer of the app's core, as the page's layers are of its page (ui/layers/*.js; the same names): the
 * views it adds (how a device connects as each), the families it follows in the general RAM (started with every
 * mission), and its own ops. AppCore is the base (connect, presence and positions, chat, the team RAMs, the mission,
 * Call, the radio log, sensors); an app's core is the base and its layers (AppCore(ui, "eoc,help,field,...")).
 */
public abstract class Layer {
    /** how a device connects as a view: connect "login" (its user's role, at the door), "team" (a team), "unit" (a unit's
     *  name), "public" (reads only); kind: its presence on the map ("" for none); allTeams: it follows every team's RAM */
    public static final class Role {
        public final String connect, kind; public final boolean allTeams;
        public Role(String connect, String kind, boolean allTeams) { this.connect = connect; this.kind = kind; this.allTeams = allTeams; }
    }
    /** what a layer reaches of the core */
    public interface Here {
        String mission(); String dev(); String name(); String role(); String team(); boolean isPublic();
        void out(Object... kv); void say(String text);
        /** a follower of SERVICE (VARIABLE: one variable only; null: all) on the general RAM, stopped with the mission */
        Follower follow(String label, String service, String variable, Runnable onChange);
        /** one write on its own session to the general RAM (for a view that follows nothing there) */
        void writeOnce(String service, String variable, String instance, Map<String, Object> bag) throws Exception;
    }
    public abstract String name();
    public Map<String, Role> roles() { return new HashMap<String, Role>(); }
    public void start(Here h) { }
    /** an op of this layer's; false: not its */
    public boolean handle(String op, Map<String, Object> m, Here h) throws Exception { return false; }

    protected static Map<String, Role> roles(Object... nameRole) {
        Map<String, Role> r = new HashMap<String, Role>();
        for (int i = 0; i + 1 < nameRole.length; i += 2) r.put((String) nameRole[i], (Role) nameRole[i + 1]);
        return r;
    }
}
