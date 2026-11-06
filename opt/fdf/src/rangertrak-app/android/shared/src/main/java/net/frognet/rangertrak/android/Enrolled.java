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
 * logs in with it, and a lead asks the door for its members' invitations with it). The invitation itself is the
 * FrogNet member's, compiled into this app (Member: Identity keeps it). A lead's members are kept here as verifiers only (Enroll.verifier), never their passwords.
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
    /** [TEAM_JOIN_V1] the team's password, set by the lead: its verifier, never the password ("" clears it: no one joins) */
    static synchronized void setTeamPassword(Context c, String pw) throws Exception {
        prefs(c).edit().putString("teamVerifier", pw.isEmpty() ? "" : Json.write(net.frognet.ram.Enroll.verifier(pw))).apply();
    }
    static Map<String, Object> teamVerifier(Context c) {
        String v = prefs(c).getString("teamVerifier", ""); if (v.isEmpty()) return null;
        try { return Json.obj(Json.parse(v)); } catch (IllegalArgumentException e) { return null; }
    }
    /** [TEAM_JOIN_V1] joined by the team's password: a member, with no password of its own */
    static synchronized void joined(Context c, String name) {
        Map<String, Object> m = new LinkedHashMap<String, Object>(members(c)); if (m.containsKey(name)) return;
        m.put(name, Json.o("joined", "team password", "at", System.currentTimeMillis() / 1000)); prefs(c).edit().putString("members", Json.write(m)).apply();
    }
    /** [SEALED_ENROLL_V1] the lead's sponsor key pair (P-256): made the first time it sponsors and kept here (the private
     *  key as PKCS8), so a team QR already shown stays good; a new lead (cleared preferences) makes a new one */
    static synchronized java.security.KeyPair sponsorKey(Context c) throws Exception {
        SharedPreferences p = prefs(c);
        String pub = p.getString("sponsorPub", ""), priv = p.getString("sponsorPriv", "");
        if (!pub.isEmpty() && !priv.isEmpty()) {
            try { return net.frognet.ram.Enroll.Seal.pair(pub, priv); } catch (Exception e) { android.util.Log.i("Enroll", "[SEALED_ENROLL_V1] the kept sponsor key does not read (" + e + "): a new one"); }
        }
        java.security.KeyPair kp = net.frognet.ram.Enroll.Seal.newPair();
        p.edit().putString("sponsorPub", net.frognet.ram.Enroll.Seal.publicOf(kp)).putString("sponsorPriv", net.frognet.ram.Enroll.Seal.privateOf(kp)).commit();
        return kp;
    }
    static synchronized void removeMember(Context c, String name) {
        Map<String, Object> m = new LinkedHashMap<String, Object>(members(c)); m.remove(name);
        prefs(c).edit().putString("members", Json.write(m)).apply();
    }
}
