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
                    connected = true; onTeamWifi = !Json.str(m.get("teamRam")).isEmpty();
                }
            } catch (IllegalArgumentException ignored) { }
            final boolean conn = line.contains("\"op\":\"connect\"");
            if (conn) {                                 // [BASE_APP_V1] FrogNet first: the base's membership, its tunnel, then the app
                String inv = "";
                try { inv = Json.str(Json.obj(Json.parse(line)).get("invite")).trim(); } catch (IllegalArgumentException ignored) { }
                if (!inv.isEmpty()) { prefs.edit().putString("base-line", inv).apply(); note("[BASE_APP_V1] the FrogNet " + (inv.startsWith("fdfjoin1.") ? "join line" : "invitation") + " goes to the Disaster Area base with the join"); }
                try { Map<String, Object> c = Json.obj(Json.parse(line)); joinUser = Json.str(c.get("user")); joinPassword = Json.str(c.get("password")); } catch (IllegalArgumentException ignored) { }
                pendingConnect = line;
                runOnUiThread(new Runnable() { public void run() { startMember("connect", false); } });
                return;
            }
            new Thread(new Runnable() { public void run() { core.handle(line); } }, "core").start();
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
            Map<String, Object> ev = devices();
            String door = Json.str(m.get("door"));
            ev.put("ev", "devices"); ev.put("peer", Json.str(m.get("peer"))); ev.put("name", Json.str(m.get("name")));
            ev.put("answer", Boolean.TRUE.equals(m.get("answer")));
            ev.put("ram", door + ":8800"); ev.put("media", door + ":8994");
            ev.put("saved", Json.o("camera", prefs.getString("c_camera", "front"), "mic", prefs.getString("c_mic", ""), "speaker", prefs.getString("c_speaker", "")));
            deliver(ev);
        }
        /** [RT_CALL_V1] the Communicator, compiled into this app, opened for this call with these devices */
        @JavascriptInterface public void startComms(String json) {
            try {
                Map<String, Object> m = Json.obj(Json.parse(json));
                String cam = Json.str(m.get("camera")), mic = Json.str(m.get("mic")), out = Json.str(m.get("speaker"));
                prefs.edit().putString("c_camera", cam).putString("c_mic", mic).putString("c_speaker", out).apply();
                android.content.Intent i = new android.content.Intent().setClassName(MainActivity.this, COMMS)
                        .putExtra("ram", Json.str(m.get("ram"))).putExtra("media", Json.str(m.get("media")))
                        .putExtra("name", Json.str(m.get("name"))).putExtra("from", "RangerTrak").putExtra("pond", "")
                        .putExtra(Boolean.TRUE.equals(m.get("answer")) ? "answer" : "call", Json.str(m.get("peer")))
                        .putExtra("video", !cam.isEmpty()).putExtra("front", !"back".equals(cam));
                if (!mic.isEmpty()) i.putExtra("mic", Integer.parseInt(mic));
                if (!out.isEmpty()) i.putExtra("out", Integer.parseInt(out));
                startActivity(i);
            } catch (Exception e) { deliver(Json.o("ev", "error", "text", "the Communicator did not open: " + e.getMessage())); }
        }
        /** [TEAM_HOST_V1] Host team: this phone's hotspot carries the team, its RAM is on this phone */
        @JavascriptInterface public void teamHost() { runOnUiThread(new Runnable() { public void run() { hostTeam("Host team"); } }); }
        /** [GROUP_PLACE_V1] the lead: the team's RAM on databasehost, not this phone */
        @JavascriptInterface public void teamPlace(final String at) { runOnUiThread(new Runnable() { public void run() { placeTeam(at); } }); }
        /** [TEAM_HOST_V1][ENROLL_V1] Scan to join: on the lead's hotspot already, its card; otherwise an enrollment QR */
        @JavascriptInterface public void scanTeam() { new Thread(new Runnable() { public void run() { scanTeamNow(); } }, "scan-team").start(); }
        /** [ENROLL_V1] the scanned line and the password the user typed: enroll at the line's sponsor */
        @JavascriptInterface public void enroll(final String json) { new Thread(new Runnable() { public void run() { enrollNow(json); } }, "enroll").start(); }
        /** [ENROLL_V1] a lead's Team members page: its members, this phone's addresses, and the sponsor started */
        @JavascriptInterface public void teamMembers() { runOnUiThread(new Runnable() { public void run() { members(true); } }); }
        @JavascriptInterface public void addMember(final String name, final String password) {
            new Thread(new Runnable() { public void run() {
                String n = name.trim();
                if (!n.matches("[A-Za-z0-9._-]{1,30}")) { deliver(Json.o("ev", "error", "text", "a member's name: letters, digits, . _ - (at most 30)")); return; }
                if (password.isEmpty()) { deliver(Json.o("ev", "error", "text", "a member needs a password")); return; }
                try { Enrolled.putMember(MainActivity.this, n, net.frognet.ram.Enroll.verifier(password)); }
                catch (Exception e) { deliver(Json.o("ev", "error", "text", "the verifier was not made: " + e)); return; }
                note("[ENROLL_V1] member " + n + " added (its verifier kept, not its password)");
                runOnUiThread(new Runnable() { public void run() { members(false); } });
            } }, "add-member").start();
        }
        @JavascriptInterface public void removeMember(String name) { Enrolled.removeMember(MainActivity.this, name); runOnUiThread(new Runnable() { public void run() { members(false); } }); }
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
    static final String COMMS = "net.frognet.communicator.CommunicatorActivity";

    // ------------------------------------------------------------------ [BASE_APP_V1] the membership is the base's
    // (It was [RT_IS_A_MEMBER_V1]: the member compiled into each RangerTrak app, so Team and Public on one phone were
    // two memberships.) The device's hidden Disaster Area base holds the one membership; Connect asks it to join, with
    // the invitation or join line and the login this app was given (the one login: the base logs in with them); the
    // app connects when the tunnel is up. The base asks Android its VPN question once, the first time. The base's log
    // is Logcat tag FDF; what this app did about it, tag RangerTrak.
    static final int BASE_JOIN = 7;
    private volatile boolean memberStarted = false;
    private volatile String joinUser = "", joinPassword = "";   // the connect's login, for the base's (the one login)
    private volatile String pendingConnect = null;
    private void note(String l) { android.util.Log.i("RangerTrak", l); deliver(Json.o("ev", "status", "text", l)); }
    private boolean vpnUp() {
        android.net.ConnectivityManager cm = (android.net.ConnectivityManager) getSystemService(CONNECTIVITY_SERVICE);
        for (android.net.Network n : cm.getAllNetworks()) {
            android.net.NetworkCapabilities c = cm.getNetworkCapabilities(n);
            if (c != null && c.hasTransport(android.net.NetworkCapabilities.TRANSPORT_VPN)) return true;
        }
        return false;
    }
    /** [ON_THE_LAN_NO_TUNNEL_V1] (John, 2026-11-02: "if the phone is on my network ... it should be able to see everything on
     *  that network without having to do anything") a phone on a FrogNet node's own LAN -- its Wi-Fi address a 10.x the
     *  node's DHCP gave, its gateway that node's x.y.z.1 -- is routed everywhere the node routes: no membership, no
     *  tunnel, no invitation. Off it (any other Wi-Fi, mobile data), this app is the member. The member library's own rule
     *  (Placement: a 10.x inside a /24 a node serves; never the 10.253/10.254 overlay), decided from the address the
     *  phone has, before anything is tried. "" : not on a node's LAN. */
    private String nodeLan() {
        android.net.ConnectivityManager cm = (android.net.ConnectivityManager) getSystemService(CONNECTIVITY_SERVICE);
        for (android.net.Network n : cm.getAllNetworks()) {
            android.net.NetworkCapabilities c = cm.getNetworkCapabilities(n); android.net.LinkProperties lp = cm.getLinkProperties(n);
            if (c == null || lp == null || c.hasTransport(android.net.NetworkCapabilities.TRANSPORT_VPN)) continue;
            String gw = "";
            for (android.net.RouteInfo r : lp.getRoutes()) if (r.isDefaultRoute() && r.getGateway() instanceof java.net.Inet4Address) gw = r.getGateway().getHostAddress();
            for (android.net.LinkAddress la : lp.getLinkAddresses()) {
                String ip = la.getAddress().getHostAddress();
                if (!(la.getAddress() instanceof java.net.Inet4Address) || !ip.startsWith("10.") || ip.startsWith("10.253.") || ip.startsWith("10.254.") || ip.endsWith(".1")) continue;
                String net = ip.substring(0, ip.lastIndexOf('.'));
                if (gw.equals(net + ".1")) return ip + " on " + gw + "'s LAN (" + lp.getInterfaceName() + ")";
            }
        }
        return "";
    }
    /** consented: Android just said OK to the VPN question (not asked twice) */
    private void startMember(String why, boolean consented) {
        String lan = nodeLan();
        String tr = ""; try { if (pendingConnect != null) tr = Json.str(Json.obj(Json.parse(pendingConnect)).get("teamRam")); } catch (IllegalArgumentException ignored) { }
        // [TEAM_SPLIT_V1] on its lead's hotspot a member goes the lead's way: the lead's card says whether that needs a
        // membership of its own (teamVia); the connect itself is to the lead's phone and the node, as on a node's LAN
        if (lan.isEmpty() && !tr.isEmpty()) lan = "on the team lead's hotspot (its RAM at " + tr + ")";
        if (!lan.isEmpty()) {                           // [ON_THE_LAN_NO_TUNNEL_V1] the node routes this phone already
            final String line = pendingConnect; pendingConnect = null;
            if (line == null) return;
            note("[ON_THE_LAN_NO_TUNNEL_V1] " + lan + ": no FrogNet tunnel needed for this" + networks());
            new Thread(new Runnable() { public void run() {
                core.handle(line);
                Location l = lastFix; if (l != null) core.handle(fixLine(l));
            } }, "core-connect").start();
            return;
        }
        if (!memberStarted) {                           // [BASE_APP_V1] the base joins (its VPN question, once, is its own)
            if (!Base.installed(this)) { deliver(Json.o("ev", "error", "text", "this phone has no Disaster Area base (" + Base.PKG + "): install it; the FrogNet membership is its, shared by every app")); return; }
            Enrolled me = Enrolled.of(this);
            String u = !joinUser.isEmpty() ? joinUser : me.user, pw = !joinUser.isEmpty() ? joinPassword : me.password;
            note("[BASE_APP_V1] " + why + ": asking the Disaster Area base to join" + (u.isEmpty() ? "" : " (logging in as " + u + ")"));
            try { startActivityForResult(Base.join(prefs.getString("base-line", ""), u, pw, prefs.getString("name", Build.MODEL)), BASE_JOIN); }
            catch (android.content.ActivityNotFoundException e) { deliver(Json.o("ev", "error", "text", "the Disaster Area base did not answer: " + e.getMessage())); }
            return;                                     // onActivityResult goes on
        }
        final String line = pendingConnect; pendingConnect = null;
        if (line == null) return;
        new Thread(new Runnable() { public void run() {
            // the tunnel: the RAMs are reached through it; the connect waits for it (Android says when a VPN network is up)
            if (!vpnUp()) {
                note("[BASE_APP_V1] waiting for the FrogNet tunnel");
                final java.util.concurrent.CountDownLatch up = new java.util.concurrent.CountDownLatch(1);
                android.net.ConnectivityManager cm = (android.net.ConnectivityManager) getSystemService(CONNECTIVITY_SERVICE);
                android.net.ConnectivityManager.NetworkCallback cb = new android.net.ConnectivityManager.NetworkCallback() {
                    @Override public void onAvailable(android.net.Network n) { up.countDown(); }
                };
                cm.registerNetworkCallback(new android.net.NetworkRequest.Builder().addTransportType(android.net.NetworkCapabilities.TRANSPORT_VPN)
                        .removeCapability(android.net.NetworkCapabilities.NET_CAPABILITY_NOT_VPN).build(), cb);
                try { if (!up.await(45, java.util.concurrent.TimeUnit.SECONDS)) note("[BASE_APP_V1] the FrogNet tunnel is not up after 45 s (Logcat tag FDF, the base's, says why): connecting anyway"); }
                catch (InterruptedException e) { return; }
                finally { cm.unregisterNetworkCallback(cb); }
            }
            note("[BASE_APP_V1] FrogNet tunnel up" + networks());
            core.handle(line);
            Location l = lastFix;                       // a fix that came before the connection is this device's position now
            if (l != null) core.handle(fixLine(l));
        } }, "core-connect").start();
    }
    /** the phone's networks, for the log: each interface, VPN or not, its addresses and routes */
    private String networks() {
        StringBuilder all = new StringBuilder();
        android.net.ConnectivityManager cm = (android.net.ConnectivityManager) getSystemService(CONNECTIVITY_SERVICE);
        for (android.net.Network n : cm.getAllNetworks()) {
            android.net.LinkProperties lp = cm.getLinkProperties(n); if (lp == null) continue;
            android.net.NetworkCapabilities nc = cm.getNetworkCapabilities(n);
            all.append("; ").append(lp.getInterfaceName()).append(nc != null && nc.hasTransport(android.net.NetworkCapabilities.TRANSPORT_VPN) ? " (VPN)" : "")
               .append(" ").append(lp.getLinkAddresses()).append(" routes ").append(lp.getRoutes());
        }
        return all.toString();
    }
    @Override protected void onActivityResult(int code, int result, android.content.Intent data) {
        if (code == com.google.zxing.integration.android.IntentIntegrator.REQUEST_CODE) { scanned(com.google.zxing.integration.android.IntentIntegrator.parseActivityResult(code, result, data)); return; }
        if (code != BASE_JOIN) { super.onActivityResult(code, result, data); return; }
        Base.take(data);
        String why = data == null ? "" : String.valueOf(data.getStringExtra("why"));
        if (result == RESULT_OK) { memberStarted = true; note("[BASE_APP_V1] the Disaster Area base: " + why + (Base.invited.isEmpty() ? "" : " (" + Base.invited + ")")); startMember("the base joined", true); }
        else deliver(Json.o("ev", "error", "text", "the Disaster Area base did not join: " + why + " (Connect asks again)"));
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
    @Override protected void onResume() { super.onResume(); inFront = true; if (hostPending && !hotspotAddr().isEmpty()) hostTeam("the hotspot is on"); }
    @Override protected void onPause() { inFront = false; super.onPause(); }

    @SuppressLint({"SetJavaScriptEnabled", "AddJavascriptInterface"})
    @Override protected void onCreate(Bundle b) {
        super.onCreate(b);
        Base.listen(this, null);                         // [BASE_APP_V1] the base's state, as it changes
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
                if (line.contains("\"ev\":\"teamVia\"")) { try { Map<String, Object> v = Json.obj(Json.parse(line)); teamVia(Json.str(v.get("via")), Json.str(v.get("join"))); } catch (IllegalArgumentException ignored) { } return; }
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
        watchNetworks();
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
        // on a node's Wi-Fi the node is the gateway: where this phone is now ([TEAM_HOST_V1] not on a team lead's hotspot,
        // whose gateway is the lead's phone: there the node is the one the lead's card names)
        if (!gw.isEmpty() && !nodeLan().isEmpty()) door = gw;
        // [RT_TEAM_APP_V1] an APK built for one role (the Team app: meta-data net.frognet.rangertrak.ROLE = team) opens as it
        String lock = "";
        try {
            Bundle md = getPackageManager().getApplicationInfo(getPackageName(), PackageManager.GET_META_DATA).metaData;
            if (md != null) lock = String.valueOf(md.getString("net.frognet.rangertrak.ROLE", ""));
        } catch (PackageManager.NameNotFoundException ignored) { }
        return Json.o("dev", dev, "name", name, "door", door, "role", lock.isEmpty() ? prefs.getString("role", "team") : lock,
                "team", prefs.getString("team", "Alpha"), "platform", "android", "lock", lock, "build", AppCore.BUILD,
                "invited", invitedAs(), "lan", nodeLan(), "enrolled", enrolledCfg());
    }

    /** [ENROLL_V1] who enrolling made this phone's user, for the page ({} : not enrolled) */
    private Map<String, Object> enrolledCfg() {
        Enrolled e = Enrolled.of(this);
        return e.any() ? Json.o("user", e.user, "role", e.role, "lead", e.lead, "door", e.door, "app", e.app) : Json.o();
    }
    /** [ADMITTED_BY_INVITE_V1] who this device is invited as, for the connect screen ("" : no invitation kept yet) */
    private String invitedAs() {
        if (!Base.invited.isEmpty()) return Base.invited;          // [BASE_APP_V1] as the base says it
        String l = prefs.getString("base-line", "");
        return l.isEmpty() ? "" : l.startsWith("fdfjoin1.") ? "the EOC's join line (the base logs in with it)" : "an invitation (the base joins with it)";
    }

    @SuppressWarnings("deprecation")
    private String gateway() {
        WifiManager w = (WifiManager) getApplicationContext().getSystemService(WIFI_SERVICE);
        if (w == null || w.getDhcpInfo() == null || w.getDhcpInfo().gateway == 0) return "";
        int g = w.getDhcpInfo().gateway;
        return (g & 255) + "." + ((g >> 8) & 255) + "." + ((g >> 16) & 255) + "." + ((g >> 24) & 255);
    }

    // ------------------------------------------------------------------ [TEAM_HOST_V1] the team lead's phone hosts its team
    // (John 2026-11-02: EOC > Team > Team Member.) The lead turns on the phone's hotspot (an app cannot: Android keeps that
    // to itself, so Host team opens the settings where it is, and continues when the user comes back with it on); the
    // team's RAM runs on this phone (TeamHostService: libteamram.so); the core writes the lead's card into it (the team,
    // the EOC's node, the EOC's join line) and tells the EOC where it is. A member's phone scans the hotspot's QR code
    // (Android's own, on its hotspot screen), joins it, and reads the card at its gateway: the lead's phone.
    private volatile boolean hostPending = false;
    /** the hotspot's address on this phone: an interface with an IPv4 that is none of Android's networks (Wi-Fi as a
     *  client, mobile data, the VPN) -- a tethering interface; "" when the hotspot is off */
    private String hotspotAddr() {
        android.net.ConnectivityManager cm = (android.net.ConnectivityManager) getSystemService(CONNECTIVITY_SERVICE);
        java.util.Set<String> nets = new java.util.HashSet<String>();
        for (android.net.Network n : cm.getAllNetworks()) { android.net.LinkProperties lp = cm.getLinkProperties(n); if (lp != null && lp.getInterfaceName() != null) nets.add(lp.getInterfaceName()); }
        try {
            for (java.net.NetworkInterface ni : java.util.Collections.list(java.net.NetworkInterface.getNetworkInterfaces())) {
                if (!ni.isUp() || ni.isLoopback() || ni.getName().startsWith("tun") || nets.contains(ni.getName())) continue;
                for (java.net.InetAddress a : java.util.Collections.list(ni.getInetAddresses()))
                    if (a instanceof java.net.Inet4Address) return a.getHostAddress();
            }
        } catch (java.net.SocketException ignored) { }
        return "";
    }
    /** where the EOC reaches this phone: its address on a node's LAN, else the edge its FrogNet membership has */
    private String advertise() {
        String lan = nodeLan();
        if (!lan.isEmpty()) return lan.substring(0, lan.indexOf(' '));
        return Base.edge;                                // [BASE_APP_V1] the base's edge
    }
    private void hostTeam(String why) {
        if (!connected) { deliver(Json.o("ev", "error", "text", "connect first: the team host is a connected Team phone")); return; }
        String hs = hotspotAddr();
        if (hs.isEmpty()) {
            hostPending = true;
            note("[TEAM_HOST_V1] turn on this phone's hotspot (Hotspot & tethering), then come back to RangerTrak");
            try { startActivity(new android.content.Intent(Settings.ACTION_WIRELESS_SETTINGS)); }
            catch (android.content.ActivityNotFoundException e) { deliver(Json.o("ev", "error", "text", "no settings screen to turn the hotspot on: " + e.getMessage())); }
            return;
        }
        hostPending = false;
        try { startForegroundService(new android.content.Intent(this, TeamHostService.class)); }
        catch (RuntimeException e) { deliver(Json.o("ev", "error", "text", "the team RAM did not start: " + e)); return; }
        final String jl = Base.join;                     // [BASE_APP_V1] the join line the base keeps, for the lead's card
        hostingNow = true;
        Enrolled me = Enrolled.of(this);                 // [GROUP_PLACE_V1] a lead places its team here, at the door
        final String line = Json.write(Json.o("op", "teamHost", "port", TeamHostService.PORT, "addr", advertise(),
                "card", Json.o("door", "", "join", jl, "via", nodeLan().isEmpty() ? "tunnel" : "lan"),
                "user", me.lead ? me.user : "", "password", me.lead ? me.password : ""));
        note("[TEAM_HOST_V1] " + why + ": hosting the team on this phone's hotspot (" + hs + "). Members: Scan team in the Team app, at this phone's hotspot QR code (Hotspot settings, QR code)");
        new Thread(new Runnable() { public void run() { core.handle(line); } }, "team-host").start();
    }

    /** [GROUP_PLACE_V1] the lead moves its team's RAM to databasehost: placed there at the door (the core), and this
     *  phone stops serving it (the team RAM's service is stopped; Host team brings it back here) */
    private void placeTeam(String at) {
        Enrolled me = Enrolled.of(this);
        if (!me.lead) { deliver(Json.o("ev", "error", "text", "only the team's lead says where the team lives")); return; }
        hostingNow = false;
        try { startService(new android.content.Intent(this, TeamHostService.class).setAction(TeamHostService.STOP_RAM)); } catch (RuntimeException ignored) { }
        final String line = Json.write(Json.o("op", "teamPlace", "at", at, "user", me.user, "password", me.password));
        new Thread(new Runnable() { public void run() { core.handle(line); } }, "team-place").start();
    }

    /** [TEAM_SPLIT_V1] a member: its lead reaches the node on a LAN (this phone is on it through the lead: no membership
     *  of its own) or through its tunnel (this phone needs its own: the EOC's join line, from the lead's card) */
    private void teamVia(String via, String join) {
        if (via.equals("tunnel")) {
            if (memberStarted) return;
            if (prefs.getString("base-line", "").isEmpty() && !join.isEmpty()) prefs.edit().putString("base-line", join).apply();   // the lead's join line
            note("[TEAM_SPLIT_V1] the team lead reaches the node through its tunnel: this phone joins FrogNet itself (the Disaster Area base)");
            runOnUiThread(new Runnable() { public void run() { startMember("the team lead is on its tunnel", false); } });
        } else if (via.equals("lan") && memberStarted) {
            note("[TEAM_SPLIT_V1] the team lead is on the node's LAN: this phone is there through it; the base's membership ends");
            try { startActivity(Base.leave()); } catch (RuntimeException ignored) { }
            memberStarted = false;
        }
    }

    // [TEAM_SPLIT_V1] the phone's own networks: when they change (Wi-Fi gone, another node's Wi-Fi, mobile data), a
    // connected phone that is not on a lead's hotspot goes where it is now -- on a node's LAN that node is its node; off
    // one, its membership (the tunnel) carries it; and a lead says where its team is now. The event, not a poll.
    private volatile boolean hostingNow = false; private volatile String lastLan = null; private volatile boolean onTeamWifi = false;
    private void watchNetworks() {
        android.net.ConnectivityManager cm = (android.net.ConnectivityManager) getSystemService(CONNECTIVITY_SERVICE);
        cm.registerNetworkCallback(new android.net.NetworkRequest.Builder().addCapability(android.net.NetworkCapabilities.NET_CAPABILITY_NOT_VPN).build(),
            new android.net.ConnectivityManager.NetworkCallback() {
                @Override public void onLinkPropertiesChanged(android.net.Network n, android.net.LinkProperties lp) { netChanged(); }
                @Override public void onLost(android.net.Network n) { netChanged(); }
            });
    }
    private synchronized void netChanged() {
        if (!connected || onTeamWifi) return;
        String lan = nodeLan();
        if (lan.equals(lastLan)) return;
        boolean first = lastLan == null; lastLan = lan;
        if (first) return;                              // the network the phone connected on
        if (!lan.isEmpty()) {
            int a = lan.indexOf(" on "), b = lan.indexOf("'s LAN");
            final String node = a > 0 && b > a ? lan.substring(a + 4, b) : "";
            note("[TEAM_SPLIT_V1] this phone is on " + node + "'s LAN now");
            if (!node.isEmpty()) new Thread(new Runnable() { public void run() { core.handle(Json.write(Json.o("op", "door", "door", node))); } }, "redoor").start();
        } else {
            if (prefs.getString("base-line", "").isEmpty() && Base.invited.isEmpty()) note("[TEAM_SPLIT_V1] off the node's LAN with no FrogNet invitation or join line: this phone (and its team) is an island");
            else if (!memberStarted) {
                note("[TEAM_SPLIT_V1] off the node's LAN: FrogNet through this phone's own tunnel now (the Disaster Area base)");
                runOnUiThread(new Runnable() { public void run() { startMember("off the node's LAN", false); } });
            }
        }
        if (hostingNow) runOnUiThread(new Runnable() { public void run() { hostTeam("the network changed"); } });
    }

    private void scanTeamNow() {
        String gw = gateway();
        Map<String, Object> card = gw.isEmpty() || Enrolled.of(this).any() ? null : teamCard(gw);
        if (card != null) { deliver(card); return; }
        runOnUiThread(new Runnable() { public void run() {
            new com.google.zxing.integration.android.IntentIntegrator(MainActivity.this)
                    .setDesiredBarcodeFormats(com.google.zxing.integration.android.IntentIntegrator.QR_CODE)
                    .setPrompt("The frog QR code your admin or team lead shows you").setBeepEnabled(false).setOrientationLocked(false).initiateScan();
        } });
    }

    // ------------------------------------------------------------------ [ENROLL_V1] scan, password, joined
    // The common way a device joins a region with an app (frognet-ram-java's Enroll): the QR is "fdfenroll1 URL APP USER";
    // the user types the password; the sponsor at URL (the app's door, or the team lead's phone) answers with the role,
    // whether the user leads its team, and this phone's own WAN invitation. The invitation goes to the FrogNet member
    // (the Disaster Area base), which joins with it: one tunnel, to the WAN hub; everything else is routed. Nothing else
    // is asked.
    private void enrollNow(String json) {
        Map<String, Object> m;
        try { m = Json.obj(Json.parse(json)); } catch (IllegalArgumentException e) { return; }
        net.frognet.ram.Enroll.Line l = net.frognet.ram.Enroll.parse(Json.str(m.get("line")));
        if (l == null) { deliver(Json.o("ev", "error", "text", "not an enrollment code")); return; }
        String pw = Json.str(m.get("password"));
        note("[ENROLL_V1] enrolling as " + l.user + " in " + l.app + " at " + l.url + " (" + net.frognet.ram.Enroll.BUILD + ")");
        Map<String, Object> a;
        try { a = net.frognet.ram.Enroll.enroll(l, pw); }
        catch (Exception e) { deliver(Json.o("ev", "enrollFailed", "text", e.getMessage() == null ? e.toString() : e.getMessage())); return; }
        String inv = Json.str(a.get("invite")).trim();    // [BASE_APP_V1] the base joins with it, at Connect
        if (!inv.startsWith("fdfinv1.")) { deliver(Json.o("ev", "enrollFailed", "text", "the invitation it gave is not one: " + (inv.length() > 40 ? inv.substring(0, 40) : inv))); return; }
        prefs.edit().putString("base-line", inv).apply();
        // [ENROLL_V1] the node the app connects to: "at" in a lead's answer (its door's address); else the host the QR named.
        // ("door" in a door's answer is the door's build, [DOOR_SAYS_ITS_BUILD_V1]: never an address -- found 2026-11-02,
        // the phone tried to connect to "dc-door-2026-11-02-r3")
        String door = Json.str(a.get("at"));
        if (door.isEmpty()) { try { java.net.URL u = new java.net.URL(l.url); door = u.getHost() + (u.getPort() > 0 && u.getPort() != 80 ? ":" + u.getPort() : ""); } catch (java.net.MalformedURLException ignored) { } }
        boolean lead = Json.bool(a.get("lead"));
        Enrolled.keep(this, l.app, l.user, pw, Json.str(a.get("role")), lead, door);
        note("[ENROLL_V1] enrolled: " + l.user + " is " + Json.str(a.get("role")) + (lead ? ", its team's lead" : "") + "; this phone's FrogNet invitation is kept (" + invitedAs() + ")");
        deliver(Json.o("ev", "enrolled", "user", l.user, "role", Json.str(a.get("role")), "lead", lead, "door", door, "invited", invitedAs()));
    }

    /** [ENROLL_V1] the lead's members page: who may enroll here, where a member can reach this phone, and the sponsor */
    private void members(boolean start) {
        Enrolled me = Enrolled.of(this);
        if (!me.lead) { deliver(Json.o("ev", "error", "text", "this phone's user does not lead a team")); return; }
        if (start && !TeamHostService.sponsorState().isEmpty()) {
            try { startForegroundService(new android.content.Intent(this, TeamHostService.class)); }
            catch (RuntimeException e) { deliver(Json.o("ev", "error", "text", "the team's sponsor did not start: " + e)); }
        }
        java.util.List<Object> addrs = new java.util.ArrayList<Object>();
        String hs = hotspotAddr(); if (!hs.isEmpty()) addrs.add(Json.o("label", "this phone's hotspot", "addr", hs));
        String lan = nodeLan(); if (!lan.isEmpty()) addrs.add(Json.o("label", "the node's Wi-Fi", "addr", lan.substring(0, lan.indexOf(' '))));
        else { String w = wifiAddr(); if (!w.isEmpty()) addrs.add(Json.o("label", "this Wi-Fi", "addr", w)); }
        String e = Base.edge; if (!e.isEmpty()) addrs.add(Json.o("label", "FrogNet (the Internet)", "addr", e));   // [BASE_APP_V1]
        java.util.List<Object> names = new java.util.ArrayList<Object>(Enrolled.members(this).keySet());
        deliver(Json.o("ev", "members", "app", me.app, "team", me.role, "members", names, "addrs", addrs, "port", TeamHostService.SPONSOR_PORT,
                "sponsoring", TeamHostService.sponsorState().isEmpty() || start));
    }
    /** this phone's address on the Wi-Fi it is a client of ("" : none) */
    private String wifiAddr() {
        android.net.ConnectivityManager cm = (android.net.ConnectivityManager) getSystemService(CONNECTIVITY_SERVICE);
        for (android.net.Network n : cm.getAllNetworks()) {
            android.net.NetworkCapabilities c = cm.getNetworkCapabilities(n); android.net.LinkProperties lp = cm.getLinkProperties(n);
            if (c == null || lp == null || !c.hasTransport(android.net.NetworkCapabilities.TRANSPORT_WIFI)) continue;
            for (android.net.LinkAddress la : lp.getLinkAddresses()) if (la.getAddress() instanceof java.net.Inet4Address) return la.getAddress().getHostAddress();
        }
        return "";
    }
    /** the lead's card in its team RAM at gw (the hotspot's gateway: the lead's phone), as the page's teamCard; null: none */
    private Map<String, Object> teamCard(String gw) {
        try {
            java.net.HttpURLConnection c = (java.net.HttpURLConnection) new java.net.URL("http://" + gw + ":" + TeamHostService.PORT + AppCore.API + "?op=read&service=rt-team&variable=card").openConnection();
            c.setConnectTimeout(3000); c.setReadTimeout(3000);
            java.io.InputStream in = c.getInputStream(); java.io.ByteArrayOutputStream b = new java.io.ByteArrayOutputStream();
            byte[] k = new byte[4096]; for (int n; (n = in.read(k)) > 0; ) b.write(k, 0, n);
            for (Object o : Json.arr(Json.obj(Json.parse(b.toString("UTF-8"))).get("cells"))) {
                Map<String, Object> bag = Json.obj(Json.obj(o).get("bag"));
                note("[TEAM_HOST_V1] team " + Json.str(bag.get("team")) + "'s lead (" + Json.str(bag.get("lead")) + ") is this Wi-Fi's gateway " + gw);
                return Json.o("ev", "teamCard", "team", Json.str(bag.get("team")), "door", Json.str(bag.get("door")), "join", Json.str(bag.get("join")),
                        "lead", Json.str(bag.get("lead")), "teamRam", gw + ":" + TeamHostService.PORT);
            }
        } catch (Exception e) { android.util.Log.i("RangerTrak", "[TEAM_HOST_V1] no team card at " + gw + ": " + e); }
        return null;
    }
    private void scanned(com.google.zxing.integration.android.IntentResult r) {
        String q = r == null ? null : r.getContents();
        if (q == null) { deliver(Json.o("ev", "error", "text", "nothing was scanned")); return; }
        net.frognet.ram.Enroll.Line l = net.frognet.ram.Enroll.parse(q);
        if (l == null) { deliver(Json.o("ev", "error", "text", "that is not an enrollment code (it says " + (q.length() > 40 ? q.substring(0, 40) + "…" : q) + ")")); return; }
        note("[ENROLL_V1] scanned: " + l.user + " in " + l.app + " at " + l.url);
        deliver(Json.o("ev", "enrollScan", "line", q.trim(), "url", l.url, "app", l.app, "user", l.user));
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
