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
                    for (String k : new String[]{"door", "name", "role", "team", "unit"}) e.putString(k, Json.str(m.get(k)));
                    e.apply();
                    connected = true;
                }
            } catch (IllegalArgumentException ignored) { }
            final boolean conn = line.contains("\"connect\"");
            String door0 = "";
            try { door0 = Json.str(Json.obj(Json.parse(line)).get("door")).trim(); } catch (IllegalArgumentException ignored) { }
            final String door = door0;
            new Thread(new Runnable() { public void run() {
                if (!door.isEmpty()) useNetworkFor(door);
                core.handle(line);
                Location l = lastFix;                   // a fix that came before the connection is this device's position now
                if (conn && l != null) core.handle(fixLine(l));
            } }, "core").start();
        }
        /** [RT_NO_GPS_V1] a street address, looked up by Android's Geocoder: this device's position when it has no GPS */
        @JavascriptInterface public void geocode(final String address) {
            new Thread(new Runnable() { public void run() {
                if (!android.location.Geocoder.isPresent()) {
                    deliver(Json.o("ev", "geocoded", "error", "this device cannot look up addresses: type \"latitude, longitude\"")); return; }
                try {
                    @SuppressWarnings("deprecation")
                    java.util.List<android.location.Address> r = new android.location.Geocoder(MainActivity.this).getFromLocationName(address, 1);
                    if (r == null || r.isEmpty()) { deliver(Json.o("ev", "geocoded", "error", "no such address found: try it with the city and state")); return; }
                    android.location.Address a = r.get(0);
                    deliver(Json.o("ev", "geocoded", "lat", a.getLatitude(), "lng", a.getLongitude(), "label", address));
                } catch (Exception e) {
                    deliver(Json.o("ev", "geocoded", "error", "the address lookup failed (" + e.getMessage() + "): it needs the Internet; or type \"latitude, longitude\""));
                }
            } }, "geocode").start();
        }
        @JavascriptInterface public void saveAddress(String json) { prefs.edit().putString("address", json).apply(); }
        /** [RT_CALL_V1] the page's Call: what this phone can use (cameras, microphones, outputs), to the page's picker */
        @JavascriptInterface public void comms(String json) {
            Map<String, Object> m; try { m = Json.obj(Json.parse(json)); } catch (IllegalArgumentException e) { return; }
            if (!communicatorInstalled()) {
                deliver(Json.o("ev", "error", "text", "Call opens the FrogNet Communicator, in the FrogNet Member app: it is not on this phone (or is older than its CALL screen)"));
                return;
            }
            Map<String, Object> ev = devices();
            String door = Json.str(m.get("door"));
            ev.put("ev", "devices"); ev.put("peer", Json.str(m.get("peer"))); ev.put("name", Json.str(m.get("name")));
            ev.put("answer", Boolean.TRUE.equals(m.get("answer")));
            ev.put("ram", door + ":8800"); ev.put("media", door + ":8994");
            ev.put("saved", Json.o("camera", prefs.getString("c_camera", "front"), "mic", prefs.getString("c_mic", ""), "speaker", prefs.getString("c_speaker", "")));
            deliver(ev);
        }
        /** [RT_CALL_V1] the Communicator (FrogNet Member's CommunicatorActivity), opened for this call with these devices */
        @JavascriptInterface public void startComms(String json) {
            try {
                Map<String, Object> m = Json.obj(Json.parse(json));
                String cam = Json.str(m.get("camera")), mic = Json.str(m.get("mic")), out = Json.str(m.get("speaker"));
                prefs.edit().putString("c_camera", cam).putString("c_mic", mic).putString("c_speaker", out).apply();
                android.content.Intent i = new android.content.Intent(COMMS_CALL).setPackage(COMMS_PKG)
                        .putExtra("ram", Json.str(m.get("ram"))).putExtra("media", Json.str(m.get("media")))
                        .putExtra("name", Json.str(m.get("name"))).putExtra("from", "RangerTrak").putExtra("pond", "")
                        .putExtra(Boolean.TRUE.equals(m.get("answer")) ? "answer" : "call", Json.str(m.get("peer")))
                        .putExtra("video", !cam.isEmpty()).putExtra("front", !"back".equals(cam));
                if (!mic.isEmpty()) i.putExtra("mic", Integer.parseInt(mic));
                if (!out.isEmpty()) i.putExtra("out", Integer.parseInt(out));
                startActivity(i);
            } catch (Exception e) { deliver(Json.o("ev", "error", "text", "the Communicator did not open: " + e.getMessage())); }
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
    static final String COMMS_PKG = "com.fawcettinnovations.fdf.android", COMMS_CALL = "net.frognet.communicator.CALL";
    private boolean communicatorInstalled() {
        android.content.Intent i = new android.content.Intent(COMMS_CALL).setPackage(COMMS_PKG);
        return !getPackageManager().queryIntentActivities(i, 0).isEmpty();
    }
    private final java.util.List<String> early = new java.util.ArrayList<String>();   // said before the page was up
    private void deliver(Map<String, Object> ev) {
        final String line = Json.write(ev);
        android.util.Log.i("RangerTrak", line);      // [APP_LOGS_WHAT_IT_SAYS_V1] what the screen is told, in Logcat too
        synchronized (early) { if (!pageUp) { early.add(line); return; } }
        web.post(new Runnable() { public void run() { web.evaluateJavascript("rtDeliver(" + Json.quote(line) + ")", null); } });
    }
    /** this phone's cameras (front / back), microphones, and call outputs (speaker, earpiece, headsets) */
    private Map<String, Object> devices() {
        java.util.List<Object> cams = new java.util.ArrayList<Object>(), mics = new java.util.ArrayList<Object>(), outs = new java.util.ArrayList<Object>();
        try {
            android.hardware.camera2.CameraManager cm = (android.hardware.camera2.CameraManager) getSystemService(CAMERA_SERVICE);
            boolean f = false, bk = false;
            for (String id : cm.getCameraIdList()) {
                Integer face = cm.getCameraCharacteristics(id).get(android.hardware.camera2.CameraCharacteristics.LENS_FACING);
                if (face != null && face == android.hardware.camera2.CameraCharacteristics.LENS_FACING_FRONT) f = true;
                if (face != null && face == android.hardware.camera2.CameraCharacteristics.LENS_FACING_BACK) bk = true;
            }
            if (f) cams.add(Json.o("value", "front", "label", "Front camera"));
            if (bk) cams.add(Json.o("value", "back", "label", "Back camera"));
        } catch (Exception ignored) { }
        android.media.AudioManager am = (android.media.AudioManager) getSystemService(AUDIO_SERVICE);
        mics.add(Json.o("n", "", "label", "Automatic (a headset when one is connected)"));
        for (android.media.AudioDeviceInfo d : am.getDevices(android.media.AudioManager.GET_DEVICES_INPUTS))
            mics.add(Json.o("n", String.valueOf(d.getId()), "label", audioName(d)));
        outs.add(Json.o("n", "", "label", "Automatic (speaker; a headset when one is connected)"));
        if (Build.VERSION.SDK_INT >= 31)
            for (android.media.AudioDeviceInfo d : am.getAvailableCommunicationDevices())
                outs.add(Json.o("n", String.valueOf(d.getId()), "label", audioName(d)));
        return Json.o("cameras", cams, "mics", mics, "speakers", outs, "note", Build.VERSION.SDK_INT >= 31 ? "" : "this Android chooses the call output itself");
    }
    private static String audioName(android.media.AudioDeviceInfo d) {
        switch (d.getType()) {
            case android.media.AudioDeviceInfo.TYPE_BUILTIN_MIC: return "Phone microphone";
            case android.media.AudioDeviceInfo.TYPE_BUILTIN_SPEAKER: return "Speaker";
            case android.media.AudioDeviceInfo.TYPE_BUILTIN_EARPIECE: return "Earpiece";
            case android.media.AudioDeviceInfo.TYPE_WIRED_HEADSET: return "Wired headset";
            case android.media.AudioDeviceInfo.TYPE_WIRED_HEADPHONES: return "Wired headphones";
            case android.media.AudioDeviceInfo.TYPE_BLUETOOTH_SCO: return "Bluetooth: " + d.getProductName();
            case android.media.AudioDeviceInfo.TYPE_USB_HEADSET: return "USB headset: " + d.getProductName();
            default: return String.valueOf(d.getProductName()) + " (type " + d.getType() + ")";
        }
    }
    /** [NODE_NETWORK_CHOSEN_V1] the network the node is on. A node's Wi-Fi has no Internet, and Android then sends an
     *  app's connections over mobile data instead (the phone's default network): the node is never reached -- seen on a
     *  Samsung phone, 2026-11-02, the connection to 10.170.170.1 timed out after 5 s. So the app's connections go over the
     *  network whose addresses or routes hold the node's address (the node's own Wi-Fi), whatever the default is. */
    private String boundFor = "";
    private void useNetworkFor(String door) {
        String host = door.contains(":") ? door.substring(0, door.lastIndexOf(':')) : door;
        if (host.equals(boundFor)) return;
        try {
            java.net.InetAddress a = java.net.InetAddress.getByName(host);
            android.net.ConnectivityManager cm = (android.net.ConnectivityManager) getSystemService(CONNECTIVITY_SERVICE);
            android.net.Network pick = null; String how = "";
            StringBuilder all = new StringBuilder();
            for (android.net.Network n : cm.getAllNetworks()) {
                android.net.LinkProperties lp = cm.getLinkProperties(n);
                if (lp == null) continue;
                android.net.NetworkCapabilities nc = cm.getNetworkCapabilities(n);
                all.append(all.length() > 0 ? "; " : "").append(lp.getInterfaceName()).append(nc != null && nc.hasTransport(android.net.NetworkCapabilities.TRANSPORT_VPN) ? " (VPN)" : "")
                   .append(" ").append(lp.getLinkAddresses()).append(" routes ").append(lp.getRoutes());
                for (android.net.LinkAddress la : lp.getLinkAddresses())
                    if (inPrefix(a, la.getAddress(), la.getPrefixLength())) { pick = n; how = lp.getInterfaceName() + " " + la; }
                if (pick == null) for (android.net.RouteInfo r : lp.getRoutes())
                    if (!r.isDefaultRoute() && r.matches(a)) { pick = n; how = lp.getInterfaceName() + " route " + r.getDestination(); }
                if (pick != null) break;
            }
            if (pick == null) {
                cm.bindProcessToNetwork(null);
                deliver(Json.o("ev", "status", "text", "no network on this phone holds " + host + " (its networks: " + all + ")"));
                return;
            }
            cm.bindProcessToNetwork(pick); boundFor = host;
            deliver(Json.o("ev", "status", "text", "reaching the node at " + host + " over " + how + " (its networks: " + all + ")"));
        } catch (Exception e) {
            deliver(Json.o("ev", "status", "text", "choosing the network for " + host + ": " + e.getMessage()));
        }
    }
    private static boolean inPrefix(java.net.InetAddress a, java.net.InetAddress net, int len) {
        byte[] x = a.getAddress(), y = net.getAddress();
        if (x.length != y.length) return false;
        for (int i = 0; i < len; i++) if (((x[i / 8] ^ y[i / 8]) & (0x80 >> (i % 8))) != 0) return false;
        return true;
    }
    private volatile Location lastFix;
    private static String fixLine(Location l) {
        return Json.write(Json.o("op", "fix", "lat", l.getLatitude(), "lng", l.getLongitude(), "acc", (double) l.getAccuracy(), "src", "gps"));
    }
    /** [RT_NO_GPS_V1] no GPS position on this device: no GPS receiver, or no permission to use it -- the page asks for
     *  the street address (or uses the one saved) */
    private void noGps(String why) {
        Map<String, Object> saved = null;
        try { String a = prefs.getString("address", ""); if (!a.isEmpty()) saved = Json.obj(Json.parse(a)); } catch (IllegalArgumentException ignored) { }
        deliver(Json.o("ev", "nogps", "why", why, "saved", saved));
    }
    private volatile boolean pageUp = false, gpsless = false; private String gpslessWhy = "";
    private void noGpsWhenUp(String why) { gpsless = true; gpslessWhy = why; if (pageUp) noGps(why); }
    private volatile boolean inFront = false;
    @Override protected void onResume() { super.onResume(); inFront = true; }
    @Override protected void onPause() { inFront = false; super.onPause(); }

    @SuppressLint({"SetJavaScriptEnabled", "AddJavascriptInterface"})
    @Override protected void onCreate(Bundle b) {
        super.onCreate(b);
        android.util.Log.i("RangerTrak", "RangerTrak " + AppCore.BUILD + " " + getPackageName());   // [APP_SAYS_ITS_BUILD_V1]
        prefs = getSharedPreferences("rangertrak", MODE_PRIVATE);
        web = new WebView(this);
        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true); s.setDomStorageEnabled(true);
        s.setAllowFileAccess(true); s.setMixedContentMode(WebSettings.MIXED_CONTENT_ALWAYS_ALLOW);
        // [PAGE_ERRORS_ARE_SAID_V1] the page's console in the phone's log (adb logcat -s RangerTrak) too
        web.setWebChromeClient(new WebChromeClient() {
            @Override public boolean onConsoleMessage(android.webkit.ConsoleMessage m) {
                android.util.Log.i("RangerTrak", m.message() + " (" + m.sourceId() + ":" + m.lineNumber() + ")"); return true;
            }
        });
        core = new AppCore(new AppCore.Ui() {
            public void deliver(final String line) {
                if (line.contains("\"ev\":\"status\"") || line.contains("\"ev\":\"error\"")) android.util.Log.i("RangerTrak", line);   // [APP_LOGS_WHAT_IT_SAYS_V1]
                web.post(new Runnable() { public void run() { web.evaluateJavascript("rtDeliver(" + Json.quote(line) + ")", null); } });
            }
        });
        web.addJavascriptInterface(new Bridge(), "rtNative");
        web.setWebViewClient(new WebViewClient() {
            @Override public void onPageFinished(WebView v, String url) {
                v.evaluateJavascript("rtInit(" + Json.write(settings()) + ")", null);
                synchronized (early) { pageUp = true; for (String l : early) v.evaluateJavascript("rtDeliver(" + Json.quote(l) + ")", null); early.clear(); }
                if (gpsless) noGps(gpslessWhy);
            }
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
        // [RT_TEAM_APP_V1] an APK built for one role (the Team app: meta-data net.frognet.rangertrak.ROLE = team) opens as it
        String lock = "";
        try {
            Bundle md = getPackageManager().getApplicationInfo(getPackageName(), PackageManager.GET_META_DATA).metaData;
            if (md != null) lock = String.valueOf(md.getString("net.frognet.rangertrak.ROLE", ""));
        } catch (PackageManager.NameNotFoundException ignored) { }
        return Json.o("dev", dev, "name", name, "door", door, "role", lock.isEmpty() ? prefs.getString("role", "team") : lock,
                "team", prefs.getString("team", "Alpha"), "platform", "android", "lock", lock, "build", AppCore.BUILD);
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
        if (!getPackageManager().hasSystemFeature(PackageManager.FEATURE_LOCATION_GPS)) {
            noGpsWhenUp("This device has no GPS.");
            if (Build.VERSION.SDK_INT >= 33 && checkSelfPermission("android.permission.POST_NOTIFICATIONS") != PackageManager.PERMISSION_GRANTED)
                requestPermissions(new String[]{"android.permission.POST_NOTIFICATIONS"}, 2);
            return;
        }
        if (Build.VERSION.SDK_INT >= 33 && checkSelfPermission("android.permission.POST_NOTIFICATIONS") != PackageManager.PERMISSION_GRANTED)
            requestPermissions(new String[]{"android.permission.POST_NOTIFICATIONS", Manifest.permission.ACCESS_FINE_LOCATION, Manifest.permission.ACCESS_COARSE_LOCATION}, 1);
        else if (checkSelfPermission(Manifest.permission.ACCESS_FINE_LOCATION) != PackageManager.PERMISSION_GRANTED)
            requestPermissions(new String[]{Manifest.permission.ACCESS_FINE_LOCATION, Manifest.permission.ACCESS_COARSE_LOCATION}, 1);
        else startLocation();
    }

    @Override public void onRequestPermissionsResult(int code, String[] p, int[] r) {
        for (int i = 0; i < p.length; i++)
            if (p[i].equals(Manifest.permission.ACCESS_FINE_LOCATION)) {
                if (r[i] == PackageManager.PERMISSION_GRANTED) startLocation();
                else noGpsWhenUp("RangerTrak was not allowed to use this device's location.");
            }
    }

    @SuppressLint("MissingPermission")
    private void startLocation() {
        LocationManager lm = (LocationManager) getSystemService(LOCATION_SERVICE);
        if (lm == null) return;
        // the GPS only: the position is the phone's own GPS (the provider asked for even when location is switched off now:
        // it delivers from the moment it is switched on)
        lm.requestLocationUpdates(LocationManager.GPS_PROVIDER, 2000, 2f, this);
        if (!lm.isProviderEnabled(LocationManager.GPS_PROVIDER))
            deliver(Json.o("ev", "error", "text", "Location is switched off on this phone: switch it on (Settings, Location) for your position on the map"));
    }

    @Override public void onLocationChanged(Location l) {
        lastFix = l;
        if (!connected) return;
        final String line = fixLine(l);
        new Thread(new Runnable() { public void run() { core.handle(line); } }, "fix").start();
    }
    @Override public void onStatusChanged(String p, int s, Bundle e) { }
    @Override public void onProviderEnabled(String p) { }
    @Override public void onProviderDisabled(String p) { }

    /** [BACK_CLOSES_FIRST_V1] Back closes what is open on the page first; with nothing open, the app goes to the background */
    @SuppressWarnings("deprecation")
    @Override public void onBackPressed() {
        web.evaluateJavascript("window.rtBack ? rtBack() : false", new android.webkit.ValueCallback<String>() {
            public void onReceiveValue(String v) { if (!"true".equals(v)) moveTaskToBack(true); }
        });
    }

    @Override protected void onDestroy() {
        LocationManager lm = (LocationManager) getSystemService(LOCATION_SERVICE);
        if (lm != null) lm.removeUpdates(this);
        core.close();                                 // its own cells go with its sessions
        super.onDestroy();
    }
}
