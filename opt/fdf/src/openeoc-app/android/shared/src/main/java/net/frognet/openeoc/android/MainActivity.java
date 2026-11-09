// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
package net.frognet.openeoc.android;

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
import net.frognet.openeoc.AppCore;

import java.security.SecureRandom;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

/**
 * [RT_APP_ANDROID_V1] OpenEOC on the phone: the app's own page (assets/ui, the same page as the desktop) in a
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
            if (conn) {                                 // [APP_IS_SELF_CONTAINED_V1] FrogNet first: this app's membership, its tunnel, then the app
                String inv = "";
                try { inv = Json.str(Json.obj(Json.parse(line)).get("invite")).trim(); } catch (IllegalArgumentException ignored) { }
                if (!inv.isEmpty()) {
                    try {
                        Map<String, Object> c = Json.obj(Json.parse(line));
                        if (Member.keep(MainActivity.this, inv, Json.str(c.get("user")), Json.str(c.get("password")))) memberRestart = true;
                        note("[APP_IS_SELF_CONTAINED_V1] the FrogNet " + (inv.startsWith("fdfjoin1.") ? "join line (this app logs in with it)" : "invitation") + " is kept");
                    } catch (IllegalArgumentException e) { deliver(Json.o("ev", "error", "text", "FrogNet invitation: " + e.getMessage())); return; }
                }
                pendingConnect = line;
                runOnUiThread(new Runnable() { public void run() { startMember("connect", false); } });
                return;
            }
            new Thread(new Runnable() { public void run() { core.handle(line); } }, "core").start();
        }
        /** [RT_NO_GPS_V1] a street address, looked up by Android's Geocoder: this device's position when it has no GPS */
        /** [RT_MAP_SAYS_V1] the page's own account of its map, in Logcat (tag OpenEOC) */
        @JavascriptInterface public void log(final String line) { android.util.Log.i("OpenEOC", line); }
        /** [RT_DIAG_V1] the FrogNet tunnel as Diagnostics shows it: what this phone's network is, whether this app holds the
         *  tunnel (or another FrogNet app does), whether it has an invitation, and the tunnel decisions it made, in order */
        @JavascriptInterface public String tunnelState() {
            String lan = "", shape = "", nets = "";
            try { lan = nodeLan(); shape = lanShape(); nets = networks(); } catch (RuntimeException e) { nets = "networks not read: " + e; }
            List<String> notes; synchronized (tunnelNotes) { notes = new ArrayList<String>(tunnelNotes); }
            return Json.write(Json.o("invited", Member.has(MainActivity.this), "what", Member.has(MainActivity.this) ? Member.what(MainActivity.this) : "",
                    "running", Member.running(), "started", memberStarted, "vpnUp", vpnUp(), "otherTunnel", otherTunnel() != null,
                    "nodeLan", lan, "lanShape", shape, "gateways", new HashMap<String, Object>(gwIsNode), "networks", nets, "notes", notes,
                    "hosting", hostingNow, "hotspot", hotspotAddr(), "ifaces", otherIfaces()));
        }
        /** [RT_DIAG_V1] the phone's GPS as Diagnostics shows it: permission, the GPS switched on, fixes had, the last one */
        @JavascriptInterface public String gpsState() {
            LocationManager lm = (LocationManager) getSystemService(LOCATION_SERVICE);
            Location l = lastFix;
            return Json.write(Json.o("permission", checkSelfPermission(Manifest.permission.ACCESS_FINE_LOCATION) == PackageManager.PERMISSION_GRANTED,
                    "gpsOn", lm != null && lm.isProviderEnabled(LocationManager.GPS_PROVIDER), "fixes", fixes, "connected", connected,
                    "last", l == null ? null : Json.o("lat", l.getLatitude(), "lng", l.getLongitude(), "acc", (double) l.getAccuracy(),
                            "ageS", (System.currentTimeMillis() - l.getTime()) / 1000)));
        }
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
                        .putExtra("name", Json.str(m.get("name"))).putExtra("from", "OpenEOC").putExtra("pond", "")
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
        /** [TEAM_JOIN_V1] the team's password (one, the lead's): whoever has it and the team's QR joins with their own call
         *  sign; "" clears it (no one joins) */
        @JavascriptInterface public void setTeamPassword(String pw) {
            if (!Enrolled.of(MainActivity.this).lead) return;
            try { Enrolled.setTeamPassword(MainActivity.this, pw); note("[TEAM_JOIN_V1] the team's password " + (pw.isEmpty() ? "cleared: no one joins the team now" : "set: the team's QR and it let a member join"));
                  runOnUiThread(new Runnable() { public void run() { members(false); } }); }
            catch (Exception e) { deliver(Json.o("ev", "error", "text", "the team's password was not set: " + e.getMessage())); }
        }
        @JavascriptInterface public void removeMember(String name) { Enrolled.removeMember(MainActivity.this, name); runOnUiThread(new Runnable() { public void run() { members(false); } }); }
        /** [CHAT_NOTICE_V1] a message the user cannot see: a phone notification while the app is not in front */
        @JavascriptInterface public void notice(String from, String text) {
            if (inFront) return;
            android.app.NotificationManager nm = (android.app.NotificationManager) getSystemService(NOTIFICATION_SERVICE);
            if (nm == null) return;
            if (Build.VERSION.SDK_INT >= 26 && nm.getNotificationChannel("chat") == null)
                nm.createNotificationChannel(new android.app.NotificationChannel("chat", "OpenEOC chat", android.app.NotificationManager.IMPORTANCE_HIGH));
            android.content.Intent open = new android.content.Intent(MainActivity.this, MainActivity.class).addFlags(android.content.Intent.FLAG_ACTIVITY_SINGLE_TOP);
            android.app.PendingIntent pi = android.app.PendingIntent.getActivity(MainActivity.this, 0, open, android.app.PendingIntent.FLAG_IMMUTABLE);
            android.app.Notification n = new android.app.Notification.Builder(MainActivity.this, "chat").setSmallIcon(android.R.drawable.stat_notify_chat)
                    .setContentTitle(from).setContentText(text).setAutoCancel(true).setContentIntent(pi).build();
            nm.notify((int) (System.currentTimeMillis() & 0x7fffffff), n);
        }
    }
    static final String COMMS = "net.frognet.communicator.CommunicatorActivity";

    // ------------------------------------------------------------------ [APP_IS_SELF_CONTAINED_V1] this app is the member
    // frognet-android's MemberService, BridgeVpn and fdf-tun are compiled in (Member.java); nothing else is installed.
    // Connect starts the membership (the invitation or join line and the login, kept once), and with it the tunnel the
    // RAMs (10.x) are reached through; the app connects when the tunnel is up. Android asks its VPN question once, the
    // first time. The member's own log is Logcat tag FDF; what this app did about it, tag OpenEOC.
    static final int VPN_CONSENT = 7;
    private volatile boolean memberStarted = false, memberRestart = false;
    private volatile String pendingConnect = null;
    private void note(String l) {
        android.util.Log.i("OpenEOC", l); deliver(Json.o("ev", "status", "text", l));
        // [RT_DIAG_V1] the tunnel's decisions, kept for Diag (the status line shows only the last line of anything)
        if (l.startsWith("[")) { tunnelNotes.add(new java.text.SimpleDateFormat("HH:mm:ss").format(new java.util.Date()) + " " + l); while (tunnelNotes.size() > 12) tunnelNotes.remove(0); }
    }
    private final List<String> tunnelNotes = java.util.Collections.synchronizedList(new ArrayList<String>());
    /** [SHARED_TUNNEL_V1] a TUN on this phone that routes 10.0.0.0/8 while this app has no membership running: another
     *  FrogNet app's tunnel to the FrogNet host; null when there is none */
    private android.net.Network otherTunnel() {
        if (Member.running()) return null;
        android.net.ConnectivityManager cm = (android.net.ConnectivityManager) getSystemService(CONNECTIVITY_SERVICE);
        for (android.net.Network n : cm.getAllNetworks()) {
            android.net.NetworkCapabilities c = cm.getNetworkCapabilities(n); android.net.LinkProperties lp = cm.getLinkProperties(n);
            if (c == null || lp == null || !c.hasTransport(android.net.NetworkCapabilities.TRANSPORT_VPN)) continue;
            for (android.net.RouteInfo r : lp.getRoutes()) {
                android.net.IpPrefix d = r.getDestination();
                if (d.getPrefixLength() == 8 && d.getAddress().getHostAddress().equals("10.0.0.0")) return n;
            }
        }
        return null;
    }
    private volatile android.net.ConnectivityManager.NetworkCallback otherWatch = null;
    /** [SHARED_TUNNEL_V1] Android says when that tunnel is gone (an event, not a poll): this app brings up its own */
    private void watchOtherTunnel(final android.net.Network other) {
        final android.net.ConnectivityManager cm = (android.net.ConnectivityManager) getSystemService(CONNECTIVITY_SERVICE);
        if (otherWatch != null) { try { cm.unregisterNetworkCallback(otherWatch); } catch (RuntimeException ignored) { } }
        otherWatch = new android.net.ConnectivityManager.NetworkCallback() {
            @Override public void onLost(android.net.Network n) {
                if (!n.equals(other)) return;
                try { cm.unregisterNetworkCallback(this); } catch (RuntimeException ignored) { }
                otherWatch = null;
                if (memberStarted || !connected || !nodeLan().isEmpty()) return;
                note("[SHARED_TUNNEL_V1] the other FrogNet app's tunnel is gone: this app brings up its own");
                runOnUiThread(new Runnable() { public void run() { startMember("the other app's tunnel is gone", false); } });
            }
        };
        cm.registerNetworkCallback(new android.net.NetworkRequest.Builder().addTransportType(android.net.NetworkCapabilities.TRANSPORT_VPN)
                .removeCapability(android.net.NetworkCapabilities.NET_CAPABILITY_NOT_VPN).build(), otherWatch);
    }
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
     *  phone has, before anything is tried. "" : not on a node's LAN.
     *  [GATEWAY_IS_A_NODE_V1] (John 2026-11-04, a phone on a home router's 10.101.113.x: "it's not going through the
     *  Internet to get there") the address's shape is not enough -- any router may hand out 10.x with a .1 gateway: the
     *  gateway is a node when its door answers (X-FDF-Door). Until the gateway has answered or not, this is "". */
    private String nodeLan() {
        String lan = lanShape(); if (lan.isEmpty()) return "";
        Boolean n = gwIsNode.get(gwOf(lan));
        return n != null && n ? lan : "";
    }
    private static String gwOf(String lan) { int a = lan.indexOf(" on "), b = lan.indexOf("'s LAN"); return a > 0 && b > a ? lan.substring(a + 4, b) : ""; }
    private final Map<String, Boolean> gwIsNode = new java.util.concurrent.ConcurrentHashMap<String, Boolean>();
    /** [GATEWAY_IS_A_NODE_V1] whether the gateway of a node-shaped LAN is a node is not known yet: asked now (its door),
     *  then.run() when it has answered; false when it is known (or there is no such LAN) */
    private boolean gatewayUnknown(final Runnable then) {
        final String lan = lanShape(); if (lan.isEmpty()) return false;
        final String gw = gwOf(lan); if (gwIsNode.containsKey(gw)) return false;
        new Thread(new Runnable() { public void run() {
            boolean node = false; String said;
            try {
                java.net.HttpURLConnection c = (java.net.HttpURLConnection) new java.net.URL("http://" + gw + "/dc/ram.php?op=roles&app=" + AppCore.APP).openConnection();
                c.setConnectTimeout(3000); c.setReadTimeout(3000);
                int code = c.getResponseCode(); String d = c.getHeaderField("X-FDF-Door"); node = d != null;
                said = node ? "its door answered (" + d + ")" : "HTTP " + code + " from " + c.getHeaderField("Server") + ", not a FrogNet door";
                c.disconnect();
            } catch (java.io.IOException e) { said = "no door there (" + e.getMessage() + ")"; }
            gwIsNode.put(gw, node);
            note("[GATEWAY_IS_A_NODE_V1] " + gw + (node ? " is a FrogNet node: " : " is not a FrogNet node: ") + said + (node ? "" : "; this phone reaches FrogNet through its own tunnel"));
            runOnUiThread(then);
        } }, "gateway").start();
        return true;
    }
    /** a 10.x Wi-Fi address (not the overlay) whose gateway is its own x.y.z.1: what a node's LAN looks like */
    private String lanShape() {
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
    private void startMember(final String why, final boolean consented) {
        if (gatewayUnknown(new Runnable() { public void run() { startMember(why, consented); } })) return;   // [GATEWAY_IS_A_NODE_V1]
        String lan = nodeLan();
        String tr = ""; try { if (pendingConnect != null) tr = Json.str(Json.obj(Json.parse(pendingConnect)).get("teamRam")); } catch (IllegalArgumentException ignored) { }
        // [TEAM_SPLIT_V1] on its lead's hotspot a member goes the lead's way: the lead's card says whether that needs a
        // membership of its own (teamVia); the connect itself is to the lead's phone and the node, as on a node's LAN
        if (lan.isEmpty() && !tr.isEmpty()) lan = "on the team lead's hotspot (its RAM at " + tr + ")";
        // [TEAM_SPLIT_V1] the network this connect is made on: the one a later network change is measured against. It was
        // the first change's (lastLan null then: "the network the phone connected on"), so a phone connected on Seattle7's
        // LAN that then walked onto another network took that one for its own and brought up no tunnel (John 2026-11-05:
        // 10.101.113.1 "is not a FrogNet node ... through its own tunnel", then nothing; the door out of reach for 40 min)
        lastLan = nodeLan();
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
        // [SHARED_TUNNEL_V1] another FrogNet app on this phone has its tunnel to the FrogNet host up (its TUN routes
        // 10.0.0.0/8): routes are the phone's, so this app reaches every address and name through it; it brings up no
        // tunnel of its own (Android lets one app hold the TUN). When that tunnel goes, this app brings up its own.
        if (!memberStarted) {
            final android.net.Network other = otherTunnel();
            if (other != null) {
                final String line = pendingConnect; pendingConnect = null;
                note("[SHARED_TUNNEL_V1] " + why + ": another FrogNet app's tunnel is up on this phone: this app uses it" + networks());
                watchOtherTunnel(other);
                if (line == null) return;
                new Thread(new Runnable() { public void run() {
                    core.handle(line);
                    Location l = lastFix; if (l != null) core.handle(fixLine(l));
                } }, "core-connect").start();
                return;
            }
        }
        if (!memberStarted || memberRestart) {          // [APP_IS_SELF_CONTAINED_V1] this app joins (its VPN question, once)
            if (!Member.has(this)) {                    // [ADMITTED_BY_INVITE_V1] the region answers invited keys only
                deliver(Json.o("ev", "error", "text", "no FrogNet invitation: scan the EOC's QR code, or paste its line (or one from fdf-identity <name> --leaf) on this screen"));
                return;
            }
            Member.name(this, prefs.getString("name", Build.MODEL));
            android.content.Intent consent = consented ? null : android.net.VpnService.prepare(this);
            if (consent != null) {
                note("[APP_IS_SELF_CONTAINED_V1] " + why + ": asking Android for the VPN permission (asked once)");
                try { startActivityForResult(consent, VPN_CONSENT); }
                catch (android.content.ActivityNotFoundException e) { deliver(Json.o("ev", "error", "text", "Android gave no VPN permission screen: " + e.getMessage())); }
                return;
            }
            note("[APP_IS_SELF_CONTAINED_V1] " + why + ": joining FrogNet " + Member.what(this) + (memberRestart && Member.running() ? " (attaching again with what was given)" : ""));
            try { Member.start(this, memberRestart); memberStarted = true; memberRestart = false; }
            catch (RuntimeException e) { deliver(Json.o("ev", "error", "text", "the FrogNet membership did not start: " + e)); return; }
        }
        final String line = pendingConnect; pendingConnect = null;
        if (line == null) return;
        new Thread(new Runnable() { public void run() {
            // the tunnel: the RAMs are reached through it; the connect waits for it (Android says when a VPN network is up)
            if (!vpnUp()) {
                note("[APP_IS_SELF_CONTAINED_V1] waiting for the FrogNet tunnel");
                final java.util.concurrent.CountDownLatch up = new java.util.concurrent.CountDownLatch(1);
                android.net.ConnectivityManager cm = (android.net.ConnectivityManager) getSystemService(CONNECTIVITY_SERVICE);
                android.net.ConnectivityManager.NetworkCallback cb = new android.net.ConnectivityManager.NetworkCallback() {
                    @Override public void onAvailable(android.net.Network n) { up.countDown(); }
                };
                cm.registerNetworkCallback(new android.net.NetworkRequest.Builder().addTransportType(android.net.NetworkCapabilities.TRANSPORT_VPN)
                        .removeCapability(android.net.NetworkCapabilities.NET_CAPABILITY_NOT_VPN).build(), cb);
                try { if (!up.await(45, java.util.concurrent.TimeUnit.SECONDS)) note("[APP_IS_SELF_CONTAINED_V1] the FrogNet tunnel is not up after 45 s (Logcat tag FDF says why): connecting anyway"); }
                catch (InterruptedException e) { return; }
                finally { cm.unregisterNetworkCallback(cb); }
            }
            note("[APP_IS_SELF_CONTAINED_V1] FrogNet tunnel up" + networks());
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
        if (code != VPN_CONSENT) { super.onActivityResult(code, result, data); return; }
        if (result == RESULT_OK) startMember("VPN permission given", true);
        else deliver(Json.o("ev", "error", "text", "the VPN permission was refused: no FrogNet tunnel (Connect asks again)"));
    }
    private final java.util.List<String> early = new java.util.ArrayList<String>();   // said before the page was up
    private void deliver(Map<String, Object> ev) {
        final String line = Json.write(ev);
        android.util.Log.i("OpenEOC", line);      // [APP_LOGS_WHAT_IT_SAYS_V1] what the screen is told, in Logcat too
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
        android.util.Log.i("OpenEOC", "OpenEOC " + AppCore.BUILD + " " + getPackageName());   // [APP_SAYS_ITS_BUILD_V1]
        prefs = getSharedPreferences("openeoc", MODE_PRIVATE);
        web = new WebView(this);
        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true); s.setDomStorageEnabled(true);
        s.setAllowFileAccess(true); s.setMixedContentMode(WebSettings.MIXED_CONTENT_ALWAYS_ALLOW);
        // [PAGE_ERRORS_ARE_SAID_V1] the page's console in the phone's log (adb logcat -s OpenEOC) too
        web.setWebChromeClient(new WebChromeClient() {
            @Override public boolean onConsoleMessage(android.webkit.ConsoleMessage m) {
                android.util.Log.i("OpenEOC", m.message() + " (" + m.sourceId() + ":" + m.lineNumber() + ")"); return true;
            }
        });
        android.util.Log.i("OpenEOC", "[RT_LAYERS_V1] layers " + meta("net.frognet.openeoc.LAYERS"));
        core = new AppCore(new AppCore.Ui() {
            public void deliver(final String line) {
                if (line.contains("\"ev\":\"status\"") || line.contains("\"ev\":\"error\"")) android.util.Log.i("OpenEOC", line);   // [APP_LOGS_WHAT_IT_SAYS_V1]
                if (line.contains("\"ev\":\"teamVia\"")) { try { Map<String, Object> v = Json.obj(Json.parse(line)); teamVia(Json.str(v.get("via")), Json.str(v.get("join"))); } catch (IllegalArgumentException ignored) { } return; }
                web.post(new Runnable() { public void run() { web.evaluateJavascript("rtDeliver(" + Json.quote(line) + ")", null); } });
            }
        }, meta("net.frognet.openeoc.LAYERS"));   // [RT_LAYERS_V1] the core's layers: the page's
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
        gatewayUnknown(new Runnable() { public void run() { } });   // [GATEWAY_IS_A_NODE_V1] asked at once, so the first screen knows
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
        // [RT_TEAM_APP_V1] an APK built for one role (the Team app: meta-data net.frognet.openeoc.ROLE = team) opens as it
        String lock = meta("net.frognet.openeoc.ROLE");
        return Json.o("dev", dev, "name", name, "door", door, "role", lock.isEmpty() ? prefs.getString("role", "team") : lock,
                "team", prefs.getString("team", "Alpha"), "platform", "android", "lock", lock, "build", AppCore.BUILD, "layers", layerList(),
                "invited", invitedAs(), "lan", nodeLan(), "enrolled", enrolledCfg());
    }

    /** this app's manifest meta-data NAME ("" : none) */
    private String meta(String name) {
        try {
            Bundle md = getPackageManager().getApplicationInfo(getPackageName(), PackageManager.GET_META_DATA).metaData;
            Object v = md == null ? null : md.get(name);
            return v == null ? "" : String.valueOf(v);
        } catch (PackageManager.NameNotFoundException e) { return ""; }
    }
    /** [RT_LAYERS_V1] this app's layers (its manifest's LAYERS, from its build.gradle.kts): the page loads them, the core
     *  makes them */
    private java.util.List<Object> layerList() {
        java.util.List<Object> l = new java.util.ArrayList<Object>();
        for (String n : meta("net.frognet.openeoc.LAYERS").split(",")) if (!n.trim().isEmpty()) l.add(n.trim());
        return l;
    }

    /** [ENROLL_V1] who enrolling made this phone's user, for the page ({} : not enrolled) */
    private Map<String, Object> enrolledCfg() {
        Enrolled e = Enrolled.of(this);
        return e.any() ? Json.o("user", e.user, "role", e.role, "lead", e.lead, "door", e.door, "app", e.app) : Json.o();
    }
    /** [ADMITTED_BY_INVITE_V1] who this device is invited as, for the connect screen ("" : no invitation kept yet) */
    private String invitedAs() {
        return Member.invited(this);                     // [APP_IS_SELF_CONTAINED_V1] this app's own membership
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
    private static final java.util.regex.Pattern TETHER = java.util.regex.Pattern.compile("(swlan|ap|softap|wlan[1-9]|wigig|rndis|ncm|usb|bt-pan)\\d*");
    /** [TEAM_WIFI_V1] the interfaces Android does not list, for Diag: name, address, whether it counts as the hotspot */
    private List<Object> otherIfaces() {
        List<Object> out = new ArrayList<Object>();
        android.net.ConnectivityManager cm = (android.net.ConnectivityManager) getSystemService(CONNECTIVITY_SERVICE);
        java.util.Set<String> nets = new java.util.HashSet<String>();
        for (android.net.Network n : cm.getAllNetworks()) { android.net.LinkProperties lp = cm.getLinkProperties(n); if (lp != null && lp.getInterfaceName() != null) nets.add(lp.getInterfaceName()); }
        try {
            for (java.net.NetworkInterface ni : java.util.Collections.list(java.net.NetworkInterface.getNetworkInterfaces())) {
                if (!ni.isUp() || ni.isLoopback() || nets.contains(ni.getName())) continue;
                for (java.net.InetAddress a : java.util.Collections.list(ni.getInetAddresses()))
                    if (a instanceof java.net.Inet4Address) out.add(Json.o("name", ni.getName(), "addr", a.getHostAddress(), "hotspot", TETHER.matcher(ni.getName()).matches()));
            }
        } catch (java.net.SocketException ignored) { }
        return out;
    }
    private String hotspotAddr() {
        android.net.ConnectivityManager cm = (android.net.ConnectivityManager) getSystemService(CONNECTIVITY_SERVICE);
        java.util.Set<String> nets = new java.util.HashSet<String>();
        for (android.net.Network n : cm.getAllNetworks()) { android.net.LinkProperties lp = cm.getLinkProperties(n); if (lp != null && lp.getInterfaceName() != null) nets.add(lp.getInterfaceName()); }
        try {
            for (java.net.NetworkInterface ni : java.util.Collections.list(java.net.NetworkInterface.getNetworkInterfaces())) {
                if (!ni.isUp() || ni.isLoopback() || ni.getName().startsWith("tun") || nets.contains(ni.getName())) continue;
                // [TEAM_WIFI_V1] a tethering interface by its name (the hotspot's swlan0/ap0/wlan1/softap0, USB's rndis0/ncm0,
                // Bluetooth's bt-pan): any other interface Android does not list (rmnet, ccmni, dummy, p2p ...) is not the
                // team's Wi-Fi -- taking one for it said "hosting the team on this phone's hotspot" with the hotspot off
                if (!TETHER.matcher(ni.getName()).matches()) continue;
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
        return Member.edge(this);                        // [APP_IS_SELF_CONTAINED_V1] this app's edge
    }
    /** [TEAM_WIFI_V1] the Hotspot & tethering screen (an app cannot turn the hotspot on: Android keeps that to itself):
     *  its own screen where the phone has one by these names, the network settings where it does not */
    private void openTethering() {
        String[][] at = {{"com.android.settings", "com.android.settings.TetherSettings"}, {"com.android.settings", "com.android.settings.Settings$TetherSettingsActivity"},
                         {"com.android.settings", "com.android.settings.Settings$WifiTetherSettingsActivity"}};
        for (String[] c : at) {
            android.content.Intent i = new android.content.Intent().setClassName(c[0], c[1]).addFlags(android.content.Intent.FLAG_ACTIVITY_NEW_TASK);
            if (i.resolveActivity(getPackageManager()) != null) { try { startActivity(i); return; } catch (RuntimeException ignored) { } }
        }
        try { startActivity(new android.content.Intent(Settings.ACTION_WIRELESS_SETTINGS)); }
        catch (android.content.ActivityNotFoundException e) { deliver(Json.o("ev", "error", "text", "no settings screen to turn the hotspot on: " + e.getMessage())); }
    }
    private void hostTeam(String why) {
        if (!connected) { deliver(Json.o("ev", "error", "text", "connect first: the team host is a connected Team phone")); return; }
        String hs = hotspotAddr();
        if (hs.isEmpty()) {
            hostPending = true;
            note("[TEAM_WIFI_V1] the team's Wi-Fi is this phone's hotspot: turn it on (Hotspot & tethering), then come back to OpenEOC");
            openTethering();
            return;
        }
        hostPending = false;
        try { startForegroundService(new android.content.Intent(this, TeamHostService.class)); }
        catch (RuntimeException e) { deliver(Json.o("ev", "error", "text", "the team RAM did not start: " + e)); return; }
        final String jl = Member.join(this);             // the join line this app keeps, for the lead's card
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
            if (!Member.has(this) && !join.isEmpty())   // the lead's join line
                try { Member.keep(this, join, "", ""); } catch (IllegalArgumentException e) { deliver(Json.o("ev", "error", "text", "the lead's join line: " + e.getMessage())); return; }
            note("[TEAM_SPLIT_V1] the team lead reaches the node through its tunnel: this phone joins FrogNet itself");
            runOnUiThread(new Runnable() { public void run() { startMember("the team lead is on its tunnel", false); } });
        } else if (via.equals("lan") && memberStarted) {
            note("[TEAM_SPLIT_V1] the team lead is on the node's LAN: this phone is there through it; its own membership ends");
            Member.stop(this); memberStarted = false;
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
                @Override public void onLost(android.net.Network n) { gwIsNode.clear(); netChanged(); }   // a network gone: its gateway is asked again
            });
    }
    private synchronized void netChanged() {
        if (!connected || onTeamWifi) return;
        if (gatewayUnknown(new Runnable() { public void run() { new Thread(new Runnable() { public void run() { netChanged(); } }).start(); } })) return;
        String lan = nodeLan();
        if (lan.equals(lastLan)) return;
        boolean first = lastLan == null; lastLan = lan;
        if (first) return;                              // no connect made yet (startMember sets the network it connects on)
        if (!lan.isEmpty()) {
            int a = lan.indexOf(" on "), b = lan.indexOf("'s LAN");
            final String node = a > 0 && b > a ? lan.substring(a + 4, b) : "";
            note("[TEAM_SPLIT_V1] this phone is on " + node + "'s LAN now");
            if (!node.isEmpty()) new Thread(new Runnable() { public void run() { core.handle(Json.write(Json.o("op", "door", "door", node))); } }, "redoor").start();
        } else {
            if (!Member.has(this)) note("[TEAM_SPLIT_V1] off the node's LAN with no FrogNet invitation or join line: this phone (and its team) is an island");
            else if (!memberStarted) {
                note("[TEAM_SPLIT_V1] off the node's LAN: FrogNet through this phone's own tunnel now");
                runOnUiThread(new Runnable() { public void run() { startMember("off the node's LAN", false); } });
            }
        }
        if (hostingNow && hotspotAddr().isEmpty()) note("[TEAM_WIFI_V1] Team Wi-Fi is OFF: this phone hosts the team, and its members cannot reach it -- turn the hotspot on");
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
    // whether the user leads its team, and this phone's own WAN invitation. The invitation goes to this app's FrogNet
    // member, which joins with it: one tunnel, to the WAN hub; everything else is routed. Nothing else
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
        String inv = Json.str(a.get("invite")).trim();    // [APP_IS_SELF_CONTAINED_V1] this app joins with it, at Connect
        if (!inv.startsWith("fdfinv1.")) { deliver(Json.o("ev", "enrollFailed", "text", "the invitation it gave is not one: " + (inv.length() > 40 ? inv.substring(0, 40) : inv))); return; }
        try { if (Member.keep(this, inv, "", "")) memberRestart = true; }
        catch (IllegalArgumentException e) { deliver(Json.o("ev", "enrollFailed", "text", "the invitation it gave: " + e.getMessage())); return; }
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
        String e = Member.edge(this); if (!e.isEmpty()) addrs.add(Json.o("label", "FrogNet (the Internet)", "addr", e));
        java.util.List<Object> names = new java.util.ArrayList<Object>(Enrolled.members(this).keySet());
        String key;                                       // [SEALED_ENROLL_V1] the sponsor key's fingerprint: the team QR's fifth part
        try { key = net.frognet.ram.Enroll.Seal.fingerprint(net.frognet.ram.Enroll.Seal.publicOf(Enrolled.sponsorKey(this))); }
        catch (Exception x) { deliver(Json.o("ev", "error", "text", "the team's sponsor key: " + x)); return; }
        deliver(Json.o("ev", "members", "key", key, "app", me.app, "team", me.role, "members", names, "addrs", addrs, "port", TeamHostService.SPONSOR_PORT,
                "sponsoring", TeamHostService.sponsorState().isEmpty() || start, "teamPassword", Enrolled.teamVerifier(this) != null));   // [TEAM_JOIN_V1]
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
        } catch (Exception e) { android.util.Log.i("OpenEOC", "[TEAM_HOST_V1] no team card at " + gw + ": " + e); }
        return null;
    }
    private void scanned(com.google.zxing.integration.android.IntentResult r) {
        String q = r == null ? null : r.getContents();
        if (q == null) { deliver(Json.o("ev", "error", "text", "nothing was scanned")); return; }
        net.frognet.ram.Enroll.Line l = net.frognet.ram.Enroll.parse(q);
        if (l == null) { deliver(Json.o("ev", "error", "text", "that is not an enrollment code (it says " + (q.length() > 40 ? q.substring(0, 40) + "…" : q) + ")")); return; }
        note("[ENROLL_V1] scanned: " + l.user + " in " + l.app + " at " + l.url);
        deliver(Json.o("ev", "enrollScan", "line", q.trim(), "url", l.url, "app", l.app, "user", l.user, "team", l.team(), "key", l.key));   // [TEAM_JOIN_V1] [SEALED_ENROLL_V1]
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
                else noGpsWhenUp("OpenEOC was not allowed to use this device's location.");
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

    private volatile long fixes = 0;
    @Override public void onLocationChanged(Location l) {
        lastFix = l; fixes++;
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
