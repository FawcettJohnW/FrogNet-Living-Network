// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
package net.frognet.rangertrak.android;

import android.content.Context;
import android.content.SharedPreferences;
import net.frognet.ram.Json;

import java.util.LinkedHashMap;
import java.util.Map;

/**
 * [ENROLL_V1] what enrolling left on this phone, in this app's private preferences ("rt-enroll"): who the user is in the
 * app (its user, its role, whether it leads its team), the door it enrolled through, and the user's password (the app
 * logs in with it, and a lead asks the door for its members' invitations with it). The invitation itself is the FrogNet
 * Disaster Area base's (Base: it keeps the invitation). A lead's members are kept here as verifiers only (Enroll.verifier), never their passwords.
 */
final class Enrolled {
    final String app, user, password, role, door;
    final boolean lead;
    private Enrolled(SharedPreferences p) {
        app = p.getString("app", ""); user = p.getString("user", ""); password = p.getString("password", "");
        role = p.getString("role", ""); door = p.getString("door", ""); lead = p.getBoolean("lead", false);
    }
    static SharedPreferences prefs(Context c) { return c.getSharedPreferences("rt-enroll", Context.MODE_PRIVATE); }
    static Enrolled of(Context c) { return new Enrolled(prefs(c)); }
    boolean any() { return !user.isEmpty(); }

    static void keep(Context c, String app, String user, String password, String role, boolean lead, String door) {
        prefs(c).edit().putString("app", app).putString("user", user).putString("password", password).putString("role", role)
                .putBoolean("lead", lead).putString("door", door).apply();
    }

    /** the lead's members: name -> {verifier} */
    static Map<String, Object> members(Context c) {
        try { return Json.obj(Json.parse(prefs(c).getString("members", "{}"))); } catch (IllegalArgumentException e) { return new LinkedHashMap<String, Object>(); }
    }
    static synchronized void putMember(Context c, String name, Map<String, Object> verifier) {
        Map<String, Object> m = new LinkedHashMap<String, Object>(members(c)); m.put(name, Json.o("verifier", verifier));
        prefs(c).edit().putString("members", Json.write(m)).apply();
    }
    static synchronized void removeMember(Context c, String name) {
        Map<String, Object> m = new LinkedHashMap<String, Object>(members(c)); m.remove(name);
        prefs(c).edit().putString("members", Json.write(m)).apply();
    }
}
