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
            if (conn) {                                 // [RT_IS_A_MEMBER_V1] FrogNet first: the member, its tunnel, then the app
                String inv = "";
                try { inv = Json.str(Json.obj(Json.parse(line)).get("invite")).trim(); } catch (IllegalArgumentException ignored) { }
                if (!inv.isEmpty()) {
                    try {
                        com.fawcettinnovations.fdf.android.Identity ident = new com.fawcettinnovations.fdf.android.Identity(MainActivity.this);
                        if (inv.startsWith(com.fawcettinnovations.fdf.member.JoinLine.PREFIX)) {   // [LOGIN_IS_AN_INVITATION_V1] the EOC QR
                            Map<String, Object> c = Json.obj(Json.parse(line));
                            ident.keepJoin(inv, Json.str(c.get("user")), Json.str(c.get("password")));
                            note("[LOGIN_IS_AN_INVITATION_V1] the EOC's join line is kept: this phone logs in with it");
                        } else { ident.setInvite(inv); note("[ADMITTED_BY_INVITE_V1] the FrogNet invitation is kept"); }
                    }
                    catch (IllegalArgumentException e) { deliver(Json.o("ev", "error", "text", "FrogNet invitation: " + e.getMessage())); return; }
                }
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
        /** [RT_CALL_V1] the Communicator, compiled into this app ([RT_IS_A_MEMBER_V1]), opened for this call with these devices */
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
        /** [TEAM_HOST_V1] Scan team: on the lead's hotspot already, its card; otherwise the hotspot's QR code first */
        @JavascriptInterface public void scanTeam() { new Thread(new Runnable() { public void run() { scanTeamNow(); } }, "scan-team").start(); }
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

    // ------------------------------------------------------------------ [RT_IS_A_MEMBER_V1] this app is the FrogNet member
    // As the kart app ([KART_IS_A_MEMBER_V1]): frognet-android's MemberService, BridgeVpn and fdf-tun are compiled in.
    // Connect starts the membership (the invitation, pasted once, is kept), and with it the tunnel the RAMs (10.x) are
    // reached through; the app connects when the tunnel is up. Android asks its VPN question once, the first time;
    // nothing else is asked. The member's own log is Logcat tag FDF; what this app did about it, tag RangerTrak.
    static final int VPN_CONSENT = 7;
    static final String MEMBER = "com.fawcettinnovations.fdf.android.MemberService";
    private boolean memberStarted = false;
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
        com.fawcettinnovations.fdf.android.Identity ident = new com.fawcettinnovations.fdf.android.Identity(this);
        com.fawcettinnovations.fdf.member.Invite iv = ident.getInvite();
        com.fawcettinnovations.fdf.member.JoinLine jl = ident.getJoin();
        if (iv == null && jl == null) {                 // [ADMITTED_BY_INVITE_V1] the region answers invited keys only
            deliver(Json.o("ev", "error", "text", "no FrogNet invitation: scan the EOC's QR code, or paste its line (or one from fdf-identity <name> --leaf) on this screen"));
            return;
        }
        SharedPreferences id = getSharedPreferences("fdf-identity", MODE_PRIVATE);   // the member's name: the user's, set once
        if (!id.contains("name")) id.edit().putString("name", prefs.getString("name", Build.MODEL)).apply();
        if (!memberStarted) {
            android.content.Intent consent = consented ? null : android.net.VpnService.prepare(this);
            if (consent != null) {
                note("[RT_IS_A_MEMBER_V1] " + why + ": asking Android for the VPN permission (asked once)");
                try { startActivityForResult(consent, VPN_CONSENT); }
                catch (android.content.ActivityNotFoundException e) { deliver(Json.o("ev", "error", "text", "Android gave no VPN permission screen: " + e.getMessage())); }
                return;
            }
            note("[RT_IS_A_MEMBER_V1] joining FrogNet " + (iv != null ? "as " + iv.getName() + " (edge " + iv.getEdge() + ") in " + iv.getArea() + ", hub " + iv.getHub().getEndpoint()
                    : "by logging in to " + jl.getOrg() + " at level " + jl.getLevel() + " (" + jl.getManagers().get(0).getEndpoint() + ")"));
            try { startForegroundService(new android.content.Intent().setClassName(this, MEMBER)); memberStarted = true; }
            catch (RuntimeException e) { deliver(Json.o("ev", "error", "text", "the FrogNet membership did not start: " + e)); return; }
        }
        final String line = pendingConnect; pendingConnect = null;
        if (line == null) return;
        new Thread(new Runnable() { public void run() {
            // the tunnel: the RAMs are reached through it; the connect waits for it (Android says when a VPN network is up)
            if (!vpnUp()) {
                note("[RT_IS_A_MEMBER_V1] waiting for the FrogNet tunnel");
                final java.util.concurrent.CountDownLatch up = new java.util.concurrent.CountDownLatch(1);
                android.net.ConnectivityManager cm = (android.net.ConnectivityManager) getSystemService(CONNECTIVITY_SERVICE);
                android.net.ConnectivityManager.NetworkCallback cb = new android.net.ConnectivityManager.NetworkCallback() {
                    @Override public void onAvailable(android.net.Network n) { up.countDown(); }
                };
                cm.registerNetworkCallback(new android.net.NetworkRequest.Builder().addTransportType(android.net.NetworkCapabilities.TRANSPORT_VPN)
                        .removeCapability(android.net.NetworkCapabilities.NET_CAPABILITY_NOT_VPN).build(), cb);
                try { if (!up.await(45, java.util.concurrent.TimeUnit.SECONDS)) note("[RT_IS_A_MEMBER_V1] the FrogNet tunnel is not up after 45 s (Logcat tag FDF says why): connecting anyway"); }
                catch (InterruptedException e) { return; }
                finally { cm.unregisterNetworkCallback(cb); }
            }
            note("[RT_IS_A_MEMBER_V1] FrogNet tunnel up" + networks());
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
        if (code == WIFI_ADD) { if (result != RESULT_OK) deliver(Json.o("ev", "error", "text", "the lead's hotspot was not added (Android's question was declined)")); return; }
        if (code != VPN_CONSENT) { super.onActivityResult(code, result, data); return; }
        if (result == RESULT_OK) startMember("VPN permission given", true);
        else deliver(Json.o("ev", "error", "text", "the VPN permission was refused: no FrogNet tunnel (Connect asks again)"));
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
                "invited", invitedAs(), "lan", nodeLan());
    }

    /** [ADMITTED_BY_INVITE_V1] who this device is invited as, for the connect screen ("" : no invitation kept yet) */
    private String invitedAs() {
        try { com.fawcettinnovations.fdf.android.Identity ident = new com.fawcettinnovations.fdf.android.Identity(this);
              com.fawcettinnovations.fdf.member.Invite iv = ident.getInvite(); com.fawcettinnovations.fdf.member.JoinLine jl = ident.getJoin();
              return iv != null ? iv.getName() + " in " + iv.getArea() : jl != null ? "the EOC " + jl.getOrg() + " (level " + jl.getLevel() + ")" : ""; }
        catch (RuntimeException e) { return ""; }
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
    static final int WIFI_ADD = 9;
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
        com.fawcettinnovations.fdf.android.MemberService ms = com.fawcettinnovations.fdf.android.MemberService.Companion.getInstance();
        String e = ms == null ? null : ms.edge();
        return e == null ? "" : e;
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
        com.fawcettinnovations.fdf.member.JoinLine jl = new com.fawcettinnovations.fdf.android.Identity(this).getJoin();
        hostingNow = true;
        final String line = Json.write(Json.o("op", "teamHost", "port", TeamHostService.PORT, "addr", advertise(),
                "card", Json.o("door", "", "join", jl == null ? "" : jl.getLine(), "via", nodeLan().isEmpty() ? "tunnel" : "lan")));
        note("[TEAM_HOST_V1] " + why + ": hosting the team on this phone's hotspot (" + hs + "). Members: Scan team in the Team app, at this phone's hotspot QR code (Hotspot settings, QR code)");
        new Thread(new Runnable() { public void run() { core.handle(line); } }, "team-host").start();
    }

    /** [TEAM_SPLIT_V1] a member: its lead reaches the node on a LAN (this phone is on it through the lead: no membership
     *  of its own) or through its tunnel (this phone needs its own: the EOC's join line, from the lead's card) */
    private void teamVia(String via, String join) {
        if (via.equals("tunnel")) {
            if (memberStarted) return;
            com.fawcettinnovations.fdf.android.Identity id = new com.fawcettinnovations.fdf.android.Identity(this);
            if (id.getInvite() == null && id.getJoin() == null && !join.isEmpty())
                try { id.keepJoin(join, "", ""); } catch (IllegalArgumentException e) { deliver(Json.o("ev", "error", "text", "the lead's join line: " + e.getMessage())); return; }
            if (android.net.VpnService.prepare(this) != null) { runOnUiThread(new Runnable() { public void run() { startMember("the team lead is on its tunnel", false); } }); return; }
            note("[TEAM_SPLIT_V1] the team lead reaches the node through its tunnel: this phone joins FrogNet itself");
            try { startForegroundService(new android.content.Intent().setClassName(this, MEMBER)); memberStarted = true; }
            catch (RuntimeException e) { deliver(Json.o("ev", "error", "text", "the FrogNet membership did not start: " + e)); }
        } else if (via.equals("lan") && memberStarted) {
            note("[TEAM_SPLIT_V1] the team lead is on the node's LAN: this phone is there through it; its own membership ends");
            try { startService(new android.content.Intent().setClassName(this, MEMBER).setAction("com.fawcettinnovations.fdf.android.STOP")); } catch (RuntimeException ignored) { }
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
            com.fawcettinnovations.fdf.android.Identity id = new com.fawcettinnovations.fdf.android.Identity(this);
            if (id.getInvite() == null && id.getJoin() == null) note("[TEAM_SPLIT_V1] off the node's LAN with no FrogNet invitation or join line: this phone (and its team) is an island");
            else if (!memberStarted && android.net.VpnService.prepare(this) == null) {
                note("[TEAM_SPLIT_V1] off the node's LAN: FrogNet through this phone's own tunnel now");
                try { startForegroundService(new android.content.Intent().setClassName(this, MEMBER)); memberStarted = true; }
                catch (RuntimeException e) { deliver(Json.o("ev", "error", "text", "the FrogNet membership did not start: " + e)); }
            }
        }
        if (hostingNow) runOnUiThread(new Runnable() { public void run() { hostTeam("the network changed"); } });
    }

    private void scanTeamNow() {
        String gw = gateway();
        Map<String, Object> card = gw.isEmpty() ? null : teamCard(gw);
        if (card != null) { deliver(card); return; }
        runOnUiThread(new Runnable() { public void run() {
            new com.google.zxing.integration.android.IntentIntegrator(MainActivity.this)
                    .setDesiredBarcodeFormats(com.google.zxing.integration.android.IntentIntegrator.QR_CODE)
                    .setPrompt("Your team lead's hotspot QR code").setBeepEnabled(false).setOrientationLocked(false).initiateScan();
        } });
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
        if (!q.startsWith("WIFI:")) { deliver(Json.o("ev", "error", "text", "that is not a hotspot's QR code (it says " + (q.length() > 40 ? q.substring(0, 40) + "…" : q) + ")")); return; }
        Map<String, String> w = wifiQr(q.substring(5));
        String ssid = w.get("S"), pw = w.get("P"), t = w.get("T") == null ? "" : w.get("T");
        if (ssid == null || ssid.isEmpty()) { deliver(Json.o("ev", "error", "text", "the hotspot's QR code names no network")); return; }
        if (Build.VERSION.SDK_INT < 30) { deliver(Json.o("ev", "error", "text", "this Android cannot add a Wi-Fi network from an app: join \"" + ssid + "\" in Settings, then Scan team again")); return; }
        android.net.wifi.WifiNetworkSuggestion.Builder sb = new android.net.wifi.WifiNetworkSuggestion.Builder().setSsid(ssid);
        if (t.equalsIgnoreCase("SAE")) sb.setWpa3Passphrase(pw == null ? "" : pw); else if (pw != null && !pw.isEmpty()) sb.setWpa2Passphrase(pw);
        java.util.ArrayList<android.net.wifi.WifiNetworkSuggestion> list = new java.util.ArrayList<android.net.wifi.WifiNetworkSuggestion>(); list.add(sb.build());
        note("[TEAM_HOST_V1] joining the lead's hotspot \"" + ssid + "\"");
        awaitTeamWifi();
        startActivityForResult(new android.content.Intent(Settings.ACTION_WIFI_ADD_NETWORKS).putParcelableArrayListExtra(Settings.EXTRA_WIFI_NETWORK_LIST, list), WIFI_ADD);
    }
    /** WIFI:S:<ssid>;T:<WPA|SAE|nopass>;P:<password>;H:<hidden>;; with \ ; , : " escaped by a backslash */
    static Map<String, String> wifiQr(String s) {
        Map<String, String> out = new java.util.HashMap<String, String>(); StringBuilder v = new StringBuilder(); String k = null;
        for (int i = 0; i < s.length(); i++) {
            char c = s.charAt(i);
            if (c == '\\' && i + 1 < s.length()) { v.append(s.charAt(++i)); continue; }
            if (k == null && c == ':') { k = v.toString(); v.setLength(0); continue; }
            if (c == ';') { if (k != null) out.put(k, v.toString()); k = null; v.setLength(0); continue; }
            v.append(c);
        }
        return out;
    }
    /** the Wi-Fi this phone joins next: when its gateway has a team card, that is the team (the event, not a poll) */
    private void awaitTeamWifi() {
        final android.net.ConnectivityManager cm = (android.net.ConnectivityManager) getSystemService(CONNECTIVITY_SERVICE);
        final java.util.concurrent.atomic.AtomicBoolean done = new java.util.concurrent.atomic.AtomicBoolean(false);
        final android.net.ConnectivityManager.NetworkCallback cb = new android.net.ConnectivityManager.NetworkCallback() {
            @Override public void onLinkPropertiesChanged(android.net.Network n, android.net.LinkProperties lp) {
                String gw = "";
                for (android.net.RouteInfo r : lp.getRoutes()) if (r.isDefaultRoute() && r.getGateway() instanceof java.net.Inet4Address) gw = r.getGateway().getHostAddress();
                if (gw.isEmpty() || done.get()) return;
                Map<String, Object> card = teamCard(gw);
                if (card != null && done.compareAndSet(false, true)) { deliver(card); try { cm.unregisterNetworkCallback(this); } catch (Exception ignored) { } }
            }
        };
        cm.registerNetworkCallback(new android.net.NetworkRequest.Builder().addTransportType(android.net.NetworkCapabilities.TRANSPORT_WIFI).build(), cb);
        web.postDelayed(new Runnable() { public void run() {
            if (done.compareAndSet(false, true)) { try { cm.unregisterNetworkCallback(cb); } catch (Exception ignored) { }
                deliver(Json.o("ev", "error", "text", "no team found on the lead's hotspot in 60 s: is Host team on, on the lead's phone?")); }
        } }, 60000);
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
