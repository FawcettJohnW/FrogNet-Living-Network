// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
package net.frognet.rangertrak.android;

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
 * [TEAM_HOST_V1] the team's RAM on the team lead's phone: libteamram.so (rangertrak-app/android/teamram, the Ribbit
 * platform's RAM host built for Android), run from this app's nativeLibraryDir as libfdftun.so is, listening on every
 * address on PORT -- the hotspot (the team's phones), loopback (this phone's own Team app) and the network (the EOC).
 * A foreground service so it lives while the app is not in front. Its log is Logcat tag TeamRam. Gone is gone: the
 * process ending is said, and nothing starts it again behind the user's back; Host team starts it again.
 */
public final class TeamHostService extends Service {
    public static final int PORT = 21800;
    public static final String STOP = "net.frognet.rangertrak.TEAM_HOST_STOP";
    private static volatile Process proc;
    private static volatile String said = "";

    /** "" when the team RAM runs; else why not */
    public static String state() { Process p = proc; return p != null && p.isAlive() ? "" : (said.isEmpty() ? "not started" : said); }

    @Override public IBinder onBind(Intent i) { return null; }

    @Override public int onStartCommand(Intent intent, int flags, int startId) {
        if (intent != null && STOP.equals(intent.getAction())) { stopRam("stopped by the user"); stopSelf(); return START_NOT_STICKY; }
        NotificationManager nm = getSystemService(NotificationManager.class);
        nm.createNotificationChannel(new NotificationChannel("teamhost", "RangerTrak team host", NotificationManager.IMPORTANCE_LOW));
        Notification n = new Notification.Builder(this, "teamhost").setContentTitle("RangerTrak: hosting the team")
                .setContentText("the team's RAM is on this phone (port " + PORT + ")").setSmallIcon(android.R.drawable.stat_sys_data_bluetooth).setOngoing(true).build();
        if (Build.VERSION.SDK_INT >= 34) startForeground(3, n, ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE); else startForeground(3, n);
        Process p = proc;
        if (p == null || !p.isAlive()) startRam();
        return START_NOT_STICKY;
    }

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

    @Override public void onDestroy() { stopRam("the service ended"); super.onDestroy(); }
}
