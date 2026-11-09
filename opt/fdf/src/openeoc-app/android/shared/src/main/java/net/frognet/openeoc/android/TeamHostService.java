// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
package net.frognet.openeoc.android;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.Service;
import android.content.Intent;
import android.content.pm.ServiceInfo;
import android.os.Build;
import android.os.IBinder;
import android.util.Log;

import java.io.BufferedReader;
import java.io.File;
import java.io.InputStreamReader;

/**
 * [TEAM_HOST_V1] the team's RAM on the team lead's phone: libteamram.so (openeoc-app/android/teamram, the Ribbit
 * platform's RAM host built for Android), run from this app's nativeLibraryDir as libfdftun.so is, listening on every
 * address on PORT -- the hotspot (the team's phones), loopback (this phone's own Team app) and the network (the EOC).
 * A foreground service so it lives while the app is not in front. Its log is Logcat tag TeamRam. Gone is gone: the
 * process ending is said, and nothing starts it again behind the user's back; Host team starts it again.
 *
 * [ENROLL_V1] on a lead's phone (enrolled with "lead": true) it also runs the team's sponsor (Enroll.Sponsor, port
 * SPONSOR_PORT): a member who scans the lead's QR and types its password gets its own WAN invitation, which this phone
 * asks the app's door for as the lead. The members are the lead's (Team members page): verifiers in this app's
 * preferences, never passwords. Logcat tag Enroll.
 */
public final class TeamHostService extends Service {
    public static final int PORT = 21800, SPONSOR_PORT = 21801;
    private static volatile net.frognet.ram.Enroll.Sponsor sponsor;
    public static final String STOP = "net.frognet.openeoc.TEAM_HOST_STOP";
    /** [GROUP_PLACE_V1] the team lives on databasehost now: the RAM stops here, the lead's sponsor stays */
    public static final String STOP_RAM = "net.frognet.openeoc.TEAM_HOST_STOP_RAM";
    private static volatile Process proc;
    private static volatile String said = "";

    /** "" when the team RAM runs; else why not */
    public static String state() { Process p = proc; return p != null && p.isAlive() ? "" : (said.isEmpty() ? "not started" : said); }

    @Override public IBinder onBind(Intent i) { return null; }

    @Override public int onStartCommand(Intent intent, int flags, int startId) {
        if (intent != null && STOP.equals(intent.getAction())) { stopRam("stopped by the user"); stopSelf(); return START_NOT_STICKY; }
        if (intent != null && STOP_RAM.equals(intent.getAction())) {
            stopRam("the team lives on databasehost now");
            if (sponsor == null) stopSelf();
            return START_NOT_STICKY;
        }
        NotificationManager nm = getSystemService(NotificationManager.class);
        nm.createNotificationChannel(new NotificationChannel("teamhost", "OpenEOC team host", NotificationManager.IMPORTANCE_LOW));
        Notification n = new Notification.Builder(this, "teamhost").setContentTitle("OpenEOC: hosting the team")
                .setContentText("the team's RAM is on this phone (port " + PORT + ")").setSmallIcon(android.R.drawable.stat_sys_data_bluetooth).setOngoing(true).build();
        if (Build.VERSION.SDK_INT >= 34) startForeground(3, n, ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE); else startForeground(3, n);
        Process p = proc;
        if (p == null || !p.isAlive()) startRam();
        if (sponsor == null && Enrolled.of(this).lead) startSponsor();
        return START_NOT_STICKY;
    }

    /** [ENROLL_V1] the lead's sponsor: its members from the preferences each time (the page changes them), the door asked as the lead */
    private void startSponsor() {
        final Enrolled me = Enrolled.of(this);
        final android.content.Context ctx = getApplicationContext();
        try {
            sponsor = new net.frognet.ram.Enroll.Sponsor(SPONSOR_PORT, me.app, me.role, new net.frognet.ram.Enroll.Team() {
                public java.util.Map<String, Object> member(String n) { return Enrolled.members(ctx).get(n) == null ? null : net.frognet.ram.Json.obj(Enrolled.members(ctx).get(n)); }
                public String identity(String n) throws Exception {
                    Enrolled e = Enrolled.of(ctx);
                    java.util.Map<String, Object> r = net.frognet.ram.Enroll.post("http://" + e.door + "/dc/ram.php?op=identity&app=" + java.net.URLEncoder.encode(e.app, "UTF-8"),
                            net.frognet.ram.Json.write(net.frognet.ram.Json.o("user", e.user, "password", e.password, "name", n)), 90000);
                    return net.frognet.ram.Json.str(r.get("invite"));
                }
                public java.util.Map<String, Object> card() { Enrolled e = Enrolled.of(ctx); return net.frognet.ram.Json.o("at", e.door, "team", e.role, "leader", e.user); }   // [ENROLL_V1] "at": the door's address
                public void log(String line) { Log.i("Enroll", line); }
                public java.util.Map<String, Object> teamVerifier() { return Enrolled.teamVerifier(ctx); }    // [TEAM_JOIN_V1]
                public void joined(String n) { Enrolled.joined(ctx, n); }
            }, Enrolled.sponsorKey(ctx));                     // [SEALED_ENROLL_V1] the lead's kept key: its QR stays good
        } catch (Exception e) { Log.i("Enroll", "[ENROLL_V1] the sponsor did not start on port " + SPONSOR_PORT + ": " + e); }
    }
    /** "" when this phone sponsors its team; else why not */
    public static String sponsorState() { return sponsor != null ? "" : "not sponsoring"; }

    private void startRam() {
        File bin = new File(getApplicationInfo().nativeLibraryDir, "libteamram.so");
        try {
            final Process p = new ProcessBuilder(bin.getPath(), "--listen", "0.0.0.0:" + PORT).redirectErrorStream(true).start();
            proc = p; said = "";
            Log.i("TeamRam", "[TEAM_HOST_V1] started " + bin + " on 0.0.0.0:" + PORT);
            new Thread(new Runnable() { public void run() {
                try (BufferedReader r = new BufferedReader(new InputStreamReader(p.getInputStream()))) {
                    for (String l; (l = r.readLine()) != null; ) Log.i("TeamRam", l);
                } catch (Exception ignored) { }
                try { said = "the team RAM ended (exit " + p.waitFor() + "): Logcat tag TeamRam says why"; } catch (InterruptedException e) { said = "ended"; }
                Log.i("TeamRam", "[TEAM_HOST_V1] " + said);
            } }, "teamram-log").start();
        } catch (Exception e) { said = "the team RAM did not start (" + bin + "): " + e.getMessage(); Log.i("TeamRam", "[TEAM_HOST_V1] " + said); }
    }

    private static void stopRam(String why) {
        Process p = proc; proc = null;
        if (p != null) { p.destroy(); said = why; Log.i("TeamRam", "[TEAM_HOST_V1] the team RAM stopped: " + why); }
    }

    @Override public void onDestroy() {
        stopRam("the service ended");
        net.frognet.ram.Enroll.Sponsor sp = sponsor; sponsor = null; if (sp != null) sp.close();
        super.onDestroy();
    }
}
