// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
package net.frognet.rangertrak.android;

import android.Manifest;
import android.annotation.SuppressLint;
import android.app.Activity;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.location.Location;
import android.location.LocationListener;
import android.location.LocationManager;
import android.net.wifi.WifiManager;
import android.os.Build;
import android.os.Bundle;
import android.provider.Settings;
import android.webkit.JavascriptInterface;
import android.webkit.WebChromeClient;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;

import net.frognet.ram.Json;
import net.frognet.rangertrak.AppCore;

import java.security.SecureRandom;
import java.util.Map;

/**
 * [RT_APP_ANDROID_V1] RangerTrak on the phone: the app's own page (assets/ui, the same page as the desktop) in a
 * WebView, its core (AppCore) beside it, the bridge between them, and the phone's GPS: every fix is written as this
 * device's own position (own=true: it goes when the app does). The node is the Wi-Fi's gateway (DHCP), shown in the
 * Node box. Settings the phone keeps: its device id (made once), the node, the name, the view.
 */
public final class MainActivity extends Activity implements LocationListener {
    private WebView web;
    private AppCore core;
    private SharedPreferences prefs;
    private boolean connected = false;

    /** what the page calls: rtNative.send(line) */
    public final class Bridge {
        @JavascriptInterface public void send(final String line) {
            try {
                Map<String, Object> m = Json.obj(Json.parse(line));
                if ("connect".equals(Json.str(m.get("op")))) {
                    SharedPreferences.Editor e = prefs.edit();
                    for (String k : new String[]{"door", "name", "role", "team"}) e.putString(k, Json.str(m.get(k)));
                    e.apply();
                    connected = true;
                }
            } catch (IllegalArgumentException ignored) { }
            new Thread(new Runnable() { public void run() { core.handle(line); } }, "core").start();
        }
        /** [CHAT_NOTICE_V1] a message the user cannot see: a phone notification while the app is not in front */
        @JavascriptInterface public void notice(String from, String text) {
            if (inFront) return;
            android.app.NotificationManager nm = (android.app.NotificationManager) getSystemService(NOTIFICATION_SERVICE);
            if (nm == null) return;
            if (Build.VERSION.SDK_INT >= 26 && nm.getNotificationChannel("chat") == null)
                nm.createNotificationChannel(new android.app.NotificationChannel("chat", "RangerTrak chat", android.app.NotificationManager.IMPORTANCE_HIGH));
            android.content.Intent open = new android.content.Intent(MainActivity.this, MainActivity.class).addFlags(android.content.Intent.FLAG_ACTIVITY_SINGLE_TOP);
            android.app.PendingIntent pi = android.app.PendingIntent.getActivity(MainActivity.this, 0, open, android.app.PendingIntent.FLAG_IMMUTABLE);
            android.app.Notification n = new android.app.Notification.Builder(MainActivity.this, "chat").setSmallIcon(android.R.drawable.stat_notify_chat)
                    .setContentTitle(from).setContentText(text).setAutoCancel(true).setContentIntent(pi).build();
            nm.notify((int) (System.currentTimeMillis() & 0x7fffffff), n);
        }
    }
    private volatile boolean inFront = false;
    @Override protected void onResume() { super.onResume(); inFront = true; }
    @Override protected void onPause() { inFront = false; super.onPause(); }

    @SuppressLint({"SetJavaScriptEnabled", "AddJavascriptInterface"})
    @Override protected void onCreate(Bundle b) {
        super.onCreate(b);
        prefs = getSharedPreferences("rangertrak", MODE_PRIVATE);
        web = new WebView(this);
        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true); s.setDomStorageEnabled(true);
        s.setAllowFileAccess(true); s.setMixedContentMode(WebSettings.MIXED_CONTENT_ALWAYS_ALLOW);
        web.setWebChromeClient(new WebChromeClient());
        core = new AppCore(new AppCore.Ui() {
            public void deliver(final String line) {
                web.post(new Runnable() { public void run() { web.evaluateJavascript("rtDeliver(" + Json.quote(line) + ")", null); } });
            }
        });
        web.addJavascriptInterface(new Bridge(), "rtNative");
        web.setWebViewClient(new WebViewClient() {
            @Override public void onPageFinished(WebView v, String url) { v.evaluateJavascript("rtInit(" + Json.write(settings()) + ")", null); }
        });
        setContentView(web);
        web.loadUrl("file:///android_asset/ui/index.html");
        askForLocation();
    }

    private Map<String, Object> settings() {
        String dev = prefs.getString("dev", "");
        if (dev.isEmpty()) {                          // this device's own id, made once
            StringBuilder sb = new StringBuilder("p"); SecureRandom r = new SecureRandom();
            for (int i = 0; i < 12; i++) sb.append("0123456789abcdef".charAt(r.nextInt(16)));
            dev = sb.toString(); prefs.edit().putString("dev", dev).apply();
        }
        String name = prefs.getString("name", "");
        if (name.isEmpty()) {
            String n = Settings.Global.getString(getContentResolver(), "device_name");
            name = n != null ? n : Build.MODEL;
        }
        String door = prefs.getString("door", "");
        String gw = gateway();
        if (!gw.isEmpty()) door = gw;                 // on a node's Wi-Fi the node is the gateway: where this phone is now
        return Json.o("dev", dev, "name", name, "door", door, "role", prefs.getString("role", "team"),
                "team", prefs.getString("team", "Alpha"), "platform", "android");
    }

    @SuppressWarnings("deprecation")
    private String gateway() {
        WifiManager w = (WifiManager) getApplicationContext().getSystemService(WIFI_SERVICE);
        if (w == null || w.getDhcpInfo() == null || w.getDhcpInfo().gateway == 0) return "";
        int g = w.getDhcpInfo().gateway;
        return (g & 255) + "." + ((g >> 8) & 255) + "." + ((g >> 16) & 255) + "." + ((g >> 24) & 255);
    }

    // ------------------------------------------------------------------ GPS: every fix is this device's own position
    private void askForLocation() {
        if (Build.VERSION.SDK_INT >= 33 && checkSelfPermission("android.permission.POST_NOTIFICATIONS") != PackageManager.PERMISSION_GRANTED)
            requestPermissions(new String[]{"android.permission.POST_NOTIFICATIONS", Manifest.permission.ACCESS_FINE_LOCATION, Manifest.permission.ACCESS_COARSE_LOCATION}, 1);
        else if (checkSelfPermission(Manifest.permission.ACCESS_FINE_LOCATION) != PackageManager.PERMISSION_GRANTED)
            requestPermissions(new String[]{Manifest.permission.ACCESS_FINE_LOCATION, Manifest.permission.ACCESS_COARSE_LOCATION}, 1);
        else startLocation();
    }

    @Override public void onRequestPermissionsResult(int code, String[] p, int[] r) {
        for (int i = 0; i < p.length; i++)
            if (p[i].equals(Manifest.permission.ACCESS_FINE_LOCATION) && r[i] == PackageManager.PERMISSION_GRANTED) startLocation();
    }

    @SuppressLint("MissingPermission")
    private void startLocation() {
        LocationManager lm = (LocationManager) getSystemService(LOCATION_SERVICE);
        if (lm == null) return;
        if (lm.isProviderEnabled(LocationManager.GPS_PROVIDER)) lm.requestLocationUpdates(LocationManager.GPS_PROVIDER, 2000, 2f, this);
        if (lm.isProviderEnabled(LocationManager.NETWORK_PROVIDER)) lm.requestLocationUpdates(LocationManager.NETWORK_PROVIDER, 5000, 5f, this);
    }

    @Override public void onLocationChanged(Location l) {
        if (!connected) return;
        final String line = Json.write(Json.o("op", "fix", "lat", l.getLatitude(), "lng", l.getLongitude(), "acc", (double) l.getAccuracy()));
        new Thread(new Runnable() { public void run() { core.handle(line); } }, "fix").start();
    }
    @Override public void onStatusChanged(String p, int s, Bundle e) { }
    @Override public void onProviderEnabled(String p) { }
    @Override public void onProviderDisabled(String p) { }

    @Override protected void onDestroy() {
        LocationManager lm = (LocationManager) getSystemService(LOCATION_SERVICE);
        if (lm != null) lm.removeUpdates(this);
        core.close();                                 // its own cells go with its sessions
        super.onDestroy();
    }
}
