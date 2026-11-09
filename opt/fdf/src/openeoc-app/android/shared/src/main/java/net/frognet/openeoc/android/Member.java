// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
package net.frognet.openeoc.android;

import android.content.Context;
import android.content.Intent;

import com.fawcettinnovations.fdf.android.Identity;
import com.fawcettinnovations.fdf.android.MemberService;
import com.fawcettinnovations.fdf.member.Invite;
import com.fawcettinnovations.fdf.member.JoinLine;

/**
 * [APP_IS_SELF_CONTAINED_V1] (John 2026-11-04: "we aren't going to have the user install 2 things, on any platform. All
 * apps are fully self contained entities") this app is the FrogNet member: frognet-android's member, MemberService,
 * BridgeVpn and fdf-tun are compiled into it (as [RT_IS_A_MEMBER_V1], [KART_IS_A_MEMBER_V1]); no other app is asked or
 * needed. (It replaces [BASE_APP_V1], the hidden Disaster Area base.) The membership is this app's: its key, its
 * invitation or join line and the login, in this app's private storage (Identity, "fdf-identity").
 */
final class Member {
    static final String SERVICE = "com.fawcettinnovations.fdf.android.MemberService";

    /** keeps the line this app was given (an fdfjoin1 join line, with the login, or an fdfinv1 invitation); true when
     *  it is not what was kept. Throws IllegalArgumentException with the reason. */
    static boolean keep(Context c, String line, String user, String password) {
        Identity id = new Identity(c);
        line = line == null ? "" : line.trim();
        if (line.isEmpty()) return false;
        if (line.startsWith(JoinLine.PREFIX)) {
            JoinLine j = id.getJoin();
            boolean changed = j == null || !j.getLine().equals(line) || !id.getJoinUser().equals(user) || !id.getJoinPassword().equals(password) || id.getInvite() != null;
            if (changed) id.keepJoin(line, user == null ? "" : user, password == null ? "" : password);
            return changed;
        }
        Invite iv = id.getInvite();
        boolean changed = iv == null || !iv.getLine().equals(line);
        if (changed) id.keepInvite(line);
        return changed;
    }
    /** an invitation or a join line is kept */
    static boolean has(Context c) { Identity id = new Identity(c); return id.getInvite() != null || id.getJoin() != null; }
    static void name(Context c, String name) {
        if (name == null || name.isEmpty()) return;
        android.content.SharedPreferences p = c.getSharedPreferences("fdf-identity", Context.MODE_PRIVATE);
        if (!p.contains("name")) p.edit().putString("name", name).apply();   // the member's name: the user's, set once
    }
    static boolean running() { return MemberService.Companion.getInstance() != null; }
    /** this device's address in its forward pond, off the mesh ("" : none) */
    static String edge(Context c) {
        MemberService ms = MemberService.Companion.getInstance();
        String e = ms == null ? null : ms.edge();
        if (e == null) { Invite iv = new Identity(c).getInvite(); e = iv == null ? null : iv.getEdge(); }
        return e == null ? "" : e;
    }
    /** who this device is invited or logged in as ("" : neither kept) */
    static String invited(Context c) {
        try {
            Identity id = new Identity(c); Invite iv = id.getInvite(); JoinLine jl = id.getJoin();
            return iv != null ? iv.getName() + " in " + iv.getArea() : jl != null ? "the Command Center " + jl.getOrg() + " (level " + jl.getLevel() + ")" : "";
        } catch (RuntimeException e) { return ""; }
    }
    /** the join line kept, for a team lead's card ("" : none) */
    static String join(Context c) { JoinLine jl = new Identity(c).getJoin(); return jl == null ? "" : jl.getLine(); }
    /** what joining is, for the log */
    static String what(Context c) {
        Identity id = new Identity(c); Invite iv = id.getInvite(); JoinLine jl = id.getJoin();
        return iv != null ? "as " + iv.getName() + " (edge " + iv.getEdge() + ") in " + iv.getArea() + ", hub " + iv.getHub().getEndpoint()
                : jl != null ? "by logging in to " + jl.getOrg() + " at level " + jl.getLevel() + " (" + jl.getManagers().get(0).getEndpoint() + ")" : "";
    }
    /** starts the membership; restart: the kept line or login changed, so the running one attaches again with it */
    static void start(Context c, boolean restart) {
        if (restart && running()) c.stopService(new Intent().setClassName(c, SERVICE));
        c.startForegroundService(new Intent().setClassName(c, SERVICE));
    }
    static void stop(Context c) {
        try { c.startService(new Intent().setClassName(c, SERVICE).setAction(MemberService.ACTION_STOP)); } catch (RuntimeException ignored) { }
    }
}
