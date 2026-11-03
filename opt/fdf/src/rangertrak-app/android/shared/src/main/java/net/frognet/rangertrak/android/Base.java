// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
package net.frognet.rangertrak.android;

import android.app.Activity;
import android.content.BroadcastReceiver;
import android.content.ComponentName;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.os.Build;

/**
 * [BASE_APP_V1] this device's FrogNet membership is the hidden base app's (com.fawcettinnovations.fdf.android,
 * "Disaster Area"; John 2026-11-03: "the base disaster area app is hidden from the user ... it's shared between all of
 * the various apps and ram regions a given machine needs"). This app never holds a membership of its own: it asks the
 * base to join (with the line and the login this app's user gave: the one login), to attach again with another line,
 * or to leave, and it hears the base's state (joined, edge, invited, join). No base installed: said, nothing else
 * tried.
 */
final class Base {
    static final String PKG = "com.fawcettinnovations.fdf.android";
    static final String ACTION_JOIN = "net.frognet.action.JOIN", ACTION_LEAVE = "net.frognet.action.LEAVE", ACTION_STATE = "net.frognet.action.MEMBER_STATE";
    static volatile boolean joined = false;
    static volatile String edge = "", invited = "", join = "";

    static boolean installed(Context c) {
        try { c.getPackageManager().getPackageInfo(PKG, 0); return true; } catch (Exception e) { return false; }
    }
    private static Intent to(String action) { return new Intent(action).setComponent(new ComponentName(PKG, PKG + ".JoinActivity")); }
    /** the base's JoinActivity: join with this line (an fdfjoin1 join line or an fdfinv1 invitation, "" for the one it keeps) */
    static Intent join(String line, String user, String password, String name) {
        Intent i = to(ACTION_JOIN);
        if (line != null && !line.isEmpty()) i.putExtra("line", line);
        if (user != null && !user.isEmpty()) i.putExtra("user", user).putExtra("password", password == null ? "" : password);
        if (name != null && !name.isEmpty()) i.putExtra("name", name);
        return i;
    }
    static Intent leave() { return to(ACTION_LEAVE); }

    /** the state in an answer or a broadcast */
    static void take(Intent i) {
        if (i == null) return;
        joined = i.getBooleanExtra("joined", joined);
        if (i.hasExtra("edge")) edge = str(i.getStringExtra("edge"));
        if (i.hasExtra("invited")) invited = str(i.getStringExtra("invited"));
        if (i.hasExtra("join")) join = str(i.getStringExtra("join"));
    }
    private static String str(String s) { return s == null ? "" : s; }

    /** hears the base's state from now on (it sends it to this app's package when it changes) */
    static void listen(Context c, final Runnable onChange) {
        BroadcastReceiver r = new BroadcastReceiver() { @Override public void onReceive(Context x, Intent i) { take(i); if (onChange != null) onChange.run(); } };
        IntentFilter f = new IntentFilter(ACTION_STATE);
        if (Build.VERSION.SDK_INT >= 33) c.registerReceiver(r, f, Context.RECEIVER_EXPORTED); else c.registerReceiver(r, f);
    }
}
