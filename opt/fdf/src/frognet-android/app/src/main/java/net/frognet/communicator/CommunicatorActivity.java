// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
package net.frognet.communicator;

import android.Manifest;
import android.app.Activity;
import android.content.Context;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.content.res.Configuration;
import android.graphics.Color;
import android.graphics.SurfaceTexture;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.media.Ringtone;
import android.media.RingtoneManager;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.text.InputType;
import android.util.TypedValue;
import android.view.Gravity;
import android.view.OrientationEventListener;
import android.view.Surface;
import android.view.TextureView;
import android.view.View;
import android.view.ViewGroup;
import android.view.WindowManager;
import android.view.inputmethod.EditorInfo;
import android.widget.Button;
import android.widget.EditText;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.SeekBar;
import android.widget.TextView;

import net.frognet.communicator.core.CallCore;
import net.frognet.communicator.core.Channel;
import net.frognet.communicator.core.ChannelFrame;
import net.frognet.communicator.core.Fnav;
import net.frognet.communicator.core.Ladder;
import net.frognet.communicator.core.Plane;
import net.frognet.communicator.core.RamAt;
import net.frognet.communicator.core.Room;

import java.util.HashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/**
 * The FrogNet Communicator on Android: the same lobby, calls and chat as the desktop Communicator (comms-app), in the
 * same Communicator memory (comms-ram) and through the same media server (comms-media). Nothing on the servers changes.
 */
public class CommunicatorActivity extends Activity {
    static final int FPS = 24;
    static final int K_STEPS = 1000; static final double K_MIN = 32000.0, K_MAX = 4000000.0;   // comms_app.py's slider
    // [COMMS_FROM_THE_POND_V1] no addresses here, no defaults: the FrogNet member opens this screen with the Communicator
    // its forward pond's members say they run -- (pond, where, <ns>) {"comms":{"ram","media"}} -- and the device's name.
    // Opened any other way (or with no Communicator in the pond) it says so and joins nothing.
    public static final String X_RAM = "ram", X_MEDIA = "media", X_NAME = "name", X_POND = "pond", X_FROM = "from";
    // [COMMS_OPENED_FOR_A_CALL_V1] another app (OpenEOC's Call) opens this screen with ACTION_CALL: the same servers
    // and name, and the call it is for -- X_CALL, place a call to that person once they are in the room; X_ANSWER,
    // answer that person's ring -- with the devices the user chose: X_FRONT (front or back camera), X_VIDEO (false: no
    // camera), X_MIC and X_OUT (an AudioDeviceInfo id: the microphone, and the speaker / earpiece / headset).
    public static final String ACTION_CALL = "net.frognet.communicator.CALL";
    public static final String X_CALL = "call", X_ANSWER = "answer", X_FRONT = "front", X_VIDEO = "video", X_MIC = "mic", X_OUT = "out";
    private volatile String autoCall = null, autoAnswer = null;
    private long autoUntil = 0;
    private boolean noVideo = false;
    private String pond = "", hostedBy = "";

    private final Handler ui = new Handler(Looper.getMainLooper());
    private final ExecutorService ops = Executors.newSingleThreadExecutor();
    private SharedPreferences prefs;
    private String myName, ramAddr, mediaAddr, mediaHost;
    private int mediaPort;

    private volatile Room room;
    private volatile String roomState = "not connected";
    private volatile boolean leaving = false;
    private long seenVersion = -1;

    // the call
    private volatile CallCore core;
    private String session = "", with = "";
    private CameraGl camera;
    private AudioEngine audio;
    private RemoteVideo remote;
    private final Set<String> answered = new HashSet<String>();
    private Room.Offer ringing;
    private Ringtone ringtone;
    private long linkBps = 0;
    private int chatCount = -1;
    private long callStartMs, lastMediaMs;
    private long[] prev = new long[8];
    private long prevT = 0;
    private OrientationEventListener orient;
    private int deviceQuarter = 0;

    // views
    private FrameLayout root;
    private View setupV, lobbyV, callV, diagV, securityV;
    // [THE_CALL_IS_UNDER_THE_FRAME_V1] the Security tab: the Role's protocol, cipher and key, and where the memory is
    private final ExecutorService sec = Executors.newSingleThreadExecutor();   // the owner's writes wait for every end's report
    private String secretsDir;
    private boolean speaksFrame = true;
    private TextView secNowT, secDeviceT, secResultT, secEndsT;
    private EditText ramToE;
    private Button frameB;
    private long secShownAt = 0, secChanges = -1;
    // [SWITCHED_FROM_THE_CALL_V1] the Channel panel of the call screen: the same owner's changes as the Security tab,
    // made while the call runs; the Stats panel over the picture shows each one take effect
    private ScrollView channelPanel;
    private TextView chanNowT, chanResultT;
    private EditText chanRamE;
    private int callsMade = 0;                       // calls this screen has been in since it opened
    private boolean otherOn = false;                 // the other person has been seen in this call
    private static final int PICK_SECRET = 7, SCAN_SECRET = 8;
    private EditText nameE, ramE, mediaE, sayE;
    private TextView lobbyStatus, ringText, statsT, peerT, diagT, chatT, linkT, emptyT, clockT;
    private LinearLayout people, ringBar, chatPanel, statsPanel;
    private ScrollView chatScroll, diagScroll;
    private TextureView remoteTV, selfTV;
    private Button micB, camB, spkB;
    private SeekBar linkS;

    // ---- life -----------------------------------------------------------------------------------------------------
    @Override
    protected void onCreate(Bundle b) {
        super.onCreate(b);
        Diag.log("FrogNet Communicator for Android build=" + Diag.BUILD + " device=" + Build.MANUFACTURER + " " + Build.MODEL
                + " android=" + Build.VERSION.RELEASE + " (API " + Build.VERSION.SDK_INT + ")");
        prefs = getSharedPreferences("comms", MODE_PRIVATE);
        audio = new AudioEngine(this);
        root = new FrameLayout(this);
        root.setBackgroundColor(BG);
        java.io.File sd = new java.io.File(getFilesDir(), "channel_secrets");   // the Roles' secrets: this app's private files
        sd.mkdirs();
        secretsDir = sd.getPath();
        // [THE_PHONE_SPEAKS_THE_FRAME_V1] (John, 27 Nov 2026, in the middle of a demonstration, on being told to turn the
        // frame back on: "Doing shit like that without reason isn't a good look.") There is no reason for a phone not to
        // speak the frame: every media server does, an unsealed Role is carried under it the same, and a sealed one lets
        // nothing else in. The phone speaks it, always; the switch that turned it off is gone, and a phone on which it
        // had been turned off speaks it again from this build on.
        speaksFrame = true;
        if (!prefs.getBoolean("frame", true)) { prefs.edit().putBoolean("frame", true).apply(); Diag.log("[app] this phone speaks the frame (it had been turned off; that switch is gone)"); }
        final boolean have = readPrefs();                              // before the screens: the setup screen shows them
        setupV = buildSetup(); lobbyV = buildLobby(); callV = buildCall(); diagV = buildDiag(); securityV = buildSecurity();
        root.addView(setupV); root.addView(lobbyV); root.addView(callV); root.addView(diagV); root.addView(securityV);
        setContentView(root);
        orient = new OrientationEventListener(this) {
            public void onOrientationChanged(int o) {
                if (o == ORIENTATION_UNKNOWN) return;                  // flat on a table: keep the last quarter
                int d = Math.abs(o - deviceQuarter); d = Math.min(d, 360 - d);
                if (d < 60) return;                                    // hysteresis: settle on a quarter turn
                deviceQuarter = ((o + 45) / 90 * 90) % 360;
                if (camera != null) camera.setDeviceOrientation(deviceQuarter);
            }
        };
        if (!have) { show(setupV); ui.post(tick); ui.post(clockTick); return; }
        Diag.log("[app] the Communicator of pond " + pond + " on " + hostedBy + ": memory " + ramAddr + ", media " + mediaAddr + " (from the pond's where cell)");
        show(lobbyV); join();
        ui.post(tick); ui.post(clockTick);
    }

    @Override
    protected void onDestroy() {
        leaving = true;
        ui.removeCallbacksAndMessages(null);
        hangUp("the app closed");
        final Room r = room; room = null;
        if (r != null) ops.execute(new Runnable() { public void run() { r.close(); } });
        ops.execute(new Runnable() { public void run() { Channel ch = Channel.watching(); if (ch != null) ch.close(); } });
        ops.shutdown();
        sec.shutdown();
        super.onDestroy();
    }

    @Override
    protected void onResume() {
        super.onResume();
        if (core != null && camera != null && !camera.cameraOpen()) camera.reopen();   // taken away in the background
    }

    @Override
    public void onConfigurationChanged(Configuration c) {
        super.onConfigurationChanged(c);
        if (camera != null) camera.setDisplayRotation(displayDeg());
        placeSelf();
        ui.postDelayed(new Runnable() { public void run() { if (remote != null) remote.fit(); } }, 200);
    }

    @Override
    public void onBackPressed() {
        if (diagV.getVisibility() == View.VISIBLE) { show(core != null ? callV : lobbyV); return; }
        if (chatPanel != null && chatPanel.getVisibility() == View.VISIBLE) { chatPanel.setVisibility(View.GONE); return; }
        if (channelPanel != null && channelPanel.getVisibility() == View.VISIBLE) { channelPanel.setVisibility(View.GONE); return; }
        if (setupV.getVisibility() == View.VISIBLE && room != null) { show(lobbyV); return; }
        super.onBackPressed();
    }

    private int displayDeg() {
        android.view.Display d = Build.VERSION.SDK_INT >= 30 ? getDisplay() : getWindowManager().getDefaultDisplay();
        int r = d != null ? d.getRotation() : Surface.ROTATION_0;
        return r == Surface.ROTATION_90 ? 90 : r == Surface.ROTATION_180 ? 180 : r == Surface.ROTATION_270 ? 270 : 0;
    }

    /** The servers and the name from the member that opened this screen. False (nothing to join) without them. */
    private boolean readPrefs() {
        android.content.Intent i = getIntent();
        String r = i == null ? null : i.getStringExtra(X_RAM), m = i == null ? null : i.getStringExtra(X_MEDIA);
        if (r == null || m == null || !r.matches(".+:\\d+") || !m.matches(".+:\\d+")) {
            Diag.log("[app] opened without the pond's Communicator (ram=" + r + " media=" + m + "): nothing to join"); return false; }
        myName = prefs.getString("name", "").isEmpty() ? (i.getStringExtra(X_NAME) != null ? i.getStringExtra(X_NAME) : Build.MODEL) : prefs.getString("name", "");
        RamAt.start(r.substring(0, r.lastIndexOf(':')), Integer.parseInt(r.substring(r.lastIndexOf(':') + 1)));   // [THE_MEMORY_MOVES_V1] where the memory is, until its owner moves it
        ramAddr = r; mediaAddr = m; pond = String.valueOf(i.getStringExtra(X_POND)); hostedBy = String.valueOf(i.getStringExtra(X_FROM));
        readCall(i);
        int c = mediaAddr.lastIndexOf(':');
        mediaHost = mediaAddr.substring(0, c);
        mediaPort = Integer.parseInt(mediaAddr.substring(c + 1));
        return true;
    }

    /** [COMMS_OPENED_FOR_A_CALL_V1] the call this screen was opened for, and the devices chosen for it */
    private void readCall(android.content.Intent i) {
        if (i == null) return;
        if (i.getStringExtra(X_CALL) != null && !i.getStringExtra(X_CALL).isEmpty()) autoCall = i.getStringExtra(X_CALL);
        if (i.getStringExtra(X_ANSWER) != null && !i.getStringExtra(X_ANSWER).isEmpty()) autoAnswer = i.getStringExtra(X_ANSWER);
        if (autoCall != null || autoAnswer != null) autoUntil = System.currentTimeMillis() + 90000;   // a ring older than this is not this call
        if (i.hasExtra(X_FRONT)) prefs.edit().putBoolean("front", i.getBooleanExtra(X_FRONT, true)).apply();
        noVideo = i.hasExtra(X_VIDEO) && !i.getBooleanExtra(X_VIDEO, true);
        if (audio != null) audio.prefer(i.getIntExtra(X_MIC, 0), i.getIntExtra(X_OUT, 0));
        if (autoCall != null || autoAnswer != null)
            Diag.log("[app] opened for a call: " + (autoCall != null ? "calling " + autoCall : "answering " + autoAnswer)
                    + " (camera " + (noVideo ? "off" : prefs.getBoolean("front", true) ? "front" : "back") + ", mic " + i.getIntExtra(X_MIC, 0) + ", out " + i.getIntExtra(X_OUT, 0) + ")");
    }

    @Override
    protected void onNewIntent(android.content.Intent i) {           // singleTask: a second Call while this screen is open
        super.onNewIntent(i);
        setIntent(i);
        readCall(i);
    }

    private void show(View v) {
        for (View x : new View[]{setupV, lobbyV, callV, diagV, securityV}) x.setVisibility(x == v ? View.VISIBLE : View.GONE);
        immersive(v == callV);
        if (v == callV) showChrome(); else ui.removeCallbacks(hideChrome);
        if (v == diagV) refreshDiag();
        if (v == securityV) { secShownAt = 0; refreshSecurity(); }
    }

    // ---- the room: joined once, rejoined when its session is gone -------------------------------------------------
    private void join() {
        final String name = myName;
        roomState = "connecting to " + RamAt.text() + " ...";
        ops.execute(new Runnable() { public void run() {
            while (!leaving && room == null) {
                final String ram = RamAt.text();                   // [THE_MEMORY_MOVES_V1] wherever the memory is now
                try {
                    // [THE_CALL_IS_UNDER_THE_FRAME_V1] the read on the Role's setting first: no plane is opened before
                    // the app knows whether the Role is sealed, and this read is what follows the memory when it moves
                    Channel ch = Channel.watching();
                    if (ch == null || !ch.sender.equals(Channel.senderId(name)) || ch.framed != speaksFrame)
                        Channel.forProgram(Channel.ROLE, speaksFrame, name, secretsDir, new Channel.Logger() { public void log(String l) { Diag.log(l); } });
                    Room r = new Room(RamAt.host(), RamAt.port(), name, "video,audio",
                            new Room.Logger() { public void log(String l) { Diag.log(l); } });
                    room = r;
                    roomState = "in the room at " + ram;
                } catch (Exception e) {
                    roomState = "cannot reach the Communicator at " + ram + ": " + e.getMessage() + " (trying again)";
                    Diag.log("[room] " + roomState);
                    try { Thread.sleep(3000); } catch (InterruptedException ie) { return; }
                }
            }
        } });
    }

    private void rejoinIfLost() {
        final Room r = room;
        if (r == null || r.fault() == null || leaving) return;
        Diag.log("[room] lost the Communicator memory: " + r.fault() + " -- rejoining");
        room = null;
        ops.execute(new Runnable() { public void run() { r.close(); } });
        join();
    }

    private final java.text.SimpleDateFormat hms = new java.text.SimpleDateFormat("HH:mm:ss.SSS", Locale.ROOT);
    private final Runnable clockTick = new Runnable() { public void run() {   // [STAMP_THE_PICTURE_V1] 30 times a second
        if (leaving) return;
        if (clockT != null) clockT.setText(core != null ? "here " + hms.format(new java.util.Date()) : "");
        ui.postDelayed(this, 33);
    } };

    // ---- the clock: twice a second -------------------------------------------------------------------------------
    private final Runnable tick = new Runnable() { public void run() {
        if (leaving) return;
        try { onTick(); } catch (RuntimeException e) { Diag.log("[ui] " + e); }
        ui.postDelayed(this, 500);
    } };

    private void onTick() {
        rejoinIfLost();
        if (ramAddr != null && RamAt.moves() > 0 && !RamAt.text().equals(ramAddr)) {     // [THE_MEMORY_MOVES_V1] its owner moved it
            ramAddr = RamAt.text();
            if (room != null) roomState = "in the room at " + ramAddr;
            Diag.log("[app] the Communicator's memory is at " + ramAddr + " now");
        }
        if (securityV.getVisibility() == View.VISIBLE) refreshSecurity();
        if (camera != null) camera.setDisplayRotation(displayDeg());   // a 90 -> 270 turn is no configuration change
        Room r = room;
        lobbyStatus.setText(myName + "  ·  " + roomState);
        if (r == null) return;
        boolean changed = r.version() != seenVersion;
        seenVersion = r.version();
        List<Room.Member> ros = r.roster();
        if (changed || people.getTag() == null || System.currentTimeMillis() - (Long) people.getTag() > 5000) {
            people.setTag(System.currentTimeMillis());
            drawLobby(ros);
        }
        // [RINGS_ONLY_WHILE_OFFERED_V1] an invitation rings only while it is at most 60 s old and its caller still offers
        // that session in CallsAnnounce; checked every tick, so the ring stops when the caller hangs up or goes away.
        if (core == null && autoCall != null) {                 // [COMMS_OPENED_FOR_A_CALL_V1] call them once they are here
            if (System.currentTimeMillis() > autoUntil) { Diag.log("[app] " + autoCall + " did not come into the room: not called"); autoCall = null; }
            else for (Room.Member mb : ros) if (mb.name != null && mb.name.equalsIgnoreCase(autoCall)) { String w = autoCall; autoCall = null; call(mb.name); break; }
        }
        if (core == null) {
            List<Room.Offer> inv = r.invitations(), off = r.offers();
            if (ringing != null) {
                boolean still = false;
                for (Room.Offer o : inv) if (o.session.equals(ringing.session) && offered(o, off)) still = true;
                if (!still) { Diag.log("[app] " + ringing.offeredBy + " is no longer calling"); answered.add(ringing.session); ringing = null; stopRinging(); }
            }
            if (ringing == null)
                for (Room.Offer o : inv) {
                    if (answered.contains(o.session) || !offered(o, off)) continue;
                    ring(o);
                    if (autoAnswer != null && o.offeredBy != null && o.offeredBy.equalsIgnoreCase(autoAnswer) && System.currentTimeMillis() <= autoUntil) {
                        autoAnswer = null; answer();                 // the user already answered, in the app that opened this
                    }
                    break;
                }
        }
        if (core != null) {
            if (changed) drawChat(r);
            // [THE_CALL_ENDS_WHEN_THE_OTHER_LEAVES_V1] a call with one other person ends here when that person leaves it
            if (!with.isEmpty() && !with.startsWith("*")) {
                if (r.callsOf(with).contains(session)) otherOn = true;
                else if (otherOn) { String who = with; Diag.log("[app] " + who + " left the call (session " + session + "): this end hangs up"); hangUp(who + " hung up"); }
            }
        }
        if (core != null) {
            drawStats(r);
            if (channelPanel.getVisibility() == View.VISIBLE) chanNowT.setText(channelNow());
        }
        if (diagV.getVisibility() == View.VISIBLE) refreshDiag();
    }

    // ---- calls -----------------------------------------------------------------------------------------------------
    private void call(final String name) {
        final Room r = room;
        if (r == null || core != null) return;
        final String s = Room.newSession();
        Diag.log("[app] calling " + name + " (session " + s + " at " + mediaHost + ":" + mediaPort + ")");
        ops.execute(new Runnable() { public void run() {
            try {
                r.offerCall(s, mediaHost, mediaPort);
                r.invite(name, s, mediaHost, mediaPort);
            } catch (Exception e) { toastLog("could not place the call: " + e.getMessage()); return; }
            ui.post(new Runnable() { public void run() { startCall(s, mediaHost, mediaPort, name); } });
        } });
    }

    private void watch(String feed) {
        Room r = room;
        if (r == null) return;
        List<String> on = r.callsOf(feed);
        for (Room.Offer o : r.offers()) if (on.contains(o.session)) { startCall(o.session, mediaHost, mediaPort, "*" + feed); return; }
        toastLog("*" + feed + " is in the room but offers no call yet");
    }

    private static boolean offered(Room.Offer inv, List<Room.Offer> offers) {
        if (inv.ageS > 60) return false;
        for (Room.Offer x : offers) if (x.session.equals(inv.session)) return true;
        return false;
    }

    private void ring(Room.Offer o) {
        ringing = o;
        ringText.setText(o.offeredBy + " is calling");
        ringBar.setVisibility(View.VISIBLE);
        show(lobbyV);
        Diag.log("[app] " + o.offeredBy + " is calling (session " + o.session + " at " + o.mediaHost + ":" + o.mediaPort + ")");
        try {
            ringtone = RingtoneManager.getRingtone(this, RingtoneManager.getDefaultUri(RingtoneManager.TYPE_RINGTONE));
            if (ringtone != null) ringtone.play();
        } catch (RuntimeException e) { Diag.log("[app] ringtone: " + e); }
    }

    private void stopRinging() {
        if (ringtone != null) { ringtone.stop(); ringtone = null; }
        ringBar.setVisibility(View.GONE);
    }

    private void answer() {
        Room.Offer o = ringing;
        ringing = null;
        stopRinging();
        if (o == null) return;
        answered.add(o.session);
        // [A_CALL_IS_ANSWERED_AT_THIS_ENDS_ADDRESS_V1] (John, 27 Nov 2026: "I cannot start a session from the Windows side to
        // Android. If I do that, android hangs.") An offer names the media server as the OFFERER reaches it (a Windows
        // machine said "databasehost.frognet:8994"); this phone may not resolve that, or may reach the same server at
        // another address, and it then sat connecting. There is one media server, the Communicator's, and this phone
        // already knows its own way to it (the pond's cell): the call is joined there, whatever the offer says.
        if (!String.valueOf(o.mediaHost).equals(mediaHost) || o.mediaPort != mediaPort)
            Diag.log("[app] the offer says the media server is at " + o.mediaHost + ":" + o.mediaPort + "; this phone reaches it at " + mediaAddr + " and joins there");
        startCall(o.session, mediaHost, mediaPort, o.offeredBy);
    }

    private void decline() {
        if (ringing != null) { answered.add(ringing.session); Diag.log("[app] declined " + ringing.offeredBy); }
        ringing = null;
        stopRinging();
    }

    private void startCall(final String s, final String host, final int port, final String who) {
        final Room r = room;
        if (r == null) return;
        // [A_REFUSED_CALL_SAYS_WHY_V1] (John, 27 Nov 2026: "Way too many times I try to call from Android to Windows and all
        // I get is a bounce.") The media server lets no end in that does not speak the frame while the Role is sealed
        // (server.cpp: "not let in"), and all this phone saw was its plane closed. It knows both facts before it dials:
        // the call is not made, and the screen and the log say why and what to press.
        final Channel chNow = Channel.watching();
        if (chNow != null && !chNow.framed && chNow.sealed()) {
            toastLog("Not called: '" + chNow.role + "' is sealed and this phone does not speak the frame, so the media server will not let it in. Security: turn the frame on.");
            return;
        }
        hangUp(null);
        session = s; with = who;
        otherOn = false; callsMade++;
        answered.add(s);
        peerT.setText(who + "  ·  connecting ...");
        chatT.setText(""); chatCount = -1;
        show(callV);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        final boolean cam = has(Manifest.permission.CAMERA), mic = has(Manifest.permission.RECORD_AUDIO);
        if (!cam || !mic) requestPermissions(new String[]{Manifest.permission.CAMERA, Manifest.permission.RECORD_AUDIO}, 1);
        remote = new RemoteVideo(remoteTV);
        remote.fill = prefs.getBoolean("fill", true);
        if (remoteTV.isAvailable()) remote.setSurface(remoteTV.getSurfaceTexture());
        remote.start(who.startsWith("*") ? who.substring(1) : who);
        ops.execute(new Runnable() { public void run() {
            final CallCore c;
            try {
                r.joinCall(s, host, port);
                c = new CallCore(host, port, myName, s, FPS, new CallCore.Listener() {
                    public void onVideo(String src, Fnav.Video v) { lastMediaMs = System.currentTimeMillis(); RemoteVideo rv = remote; if (rv != null) rv.onVideo(src, v); }
                    public void onAudio(String src, byte[] opus) { lastMediaMs = System.currentTimeMillis(); audio.onOpus(src, opus); }
                    public void onKeyRequest() { }
                    public void onPlaneEnded(final String plane, final String why) {
                        ui.post(new Runnable() { public void run() { hangUp("the media server ended the " + plane + " plane: " + why); } });
                    }
                });
                c.start();
                r.announce("in a call");
            } catch (final Exception e) {
                ui.post(new Runnable() { public void run() { hangUp("could not reach the media server " + host + ":" + port + ": " + e.getMessage()); } });
                return;
            }
            ui.post(new Runnable() { public void run() {
                if (!s.equals(session)) { c.stop(); return; }
                core = c;
                callStartMs = lastMediaMs = System.currentTimeMillis();
                prevT = 0;
                peerT.setText(who);
                Diag.log("[app] in call " + s + " with " + who + " at " + host + ":" + port);
                audio.start(c, has(Manifest.permission.RECORD_AUDIO));
                if (has(Manifest.permission.CAMERA)) startCamera(c);
                else Diag.log("[camera] no CAMERA permission -- the call goes on receiving only");
                applyLink(linkS.getProgress());
                updateButtons();
            } });
        } });
    }

    private void startCamera(final CallCore c) {
        c.startVideo(1280, 720);                       // the phone's ceiling: L7 (720p), what its VP8 encoder holds at 24 fps
        camera = new CameraGl(this, FPS, new CameraGl.Host() {
            public int tick() { return c.tick(); }
            public boolean takeWantKey() { return c.takeWantKey(); }
            public void onPacket(byte[] p, boolean key, Ladder.Size s) {
                try { c.sendEncoded(p, key, s); } catch (Plane.Closed e) { Diag.log("[video] send: " + e.getMessage()); }
            }
        });
        camera.setDisplayRotation(displayDeg());
        camera.setDeviceOrientation(deviceQuarter);
        if (orient.canDetectOrientation()) orient.enable();
        if (noVideo) { Diag.log("[video] no camera: chosen for this call"); return; }
        camera.start(prefs.getBoolean("front", true));
        if (selfTV.isAvailable()) camera.setSelfView(selfTV.getSurfaceTexture(), selfTV.getWidth(), selfTV.getHeight());
        camera.setSending(true);
    }

    /** Ends the call. The teardown (threads joined, devices closed) runs on the worker thread, never on the UI's. */
    private void hangUp(String why) {
        final CallCore c = core;
        core = null;
        if (why != null && c != null) toastLog(why);
        final CameraGl cam = camera; camera = null;
        final RemoteVideo rv = remote; remote = null;
        orient.disable();
        final boolean wasInCall = c != null || !session.isEmpty();
        final String s = session;
        final Room r = room;
        if (wasInCall || cam != null || rv != null) {
            ops.execute(new Runnable() { public void run() {
                if (cam != null) cam.stop();
                if (rv != null) rv.stop();
                if (wasInCall) audio.stop();
                if (c != null) c.stop();
                if (wasInCall && r != null) {
                    try { r.leaveCall(s); r.announce("online"); } catch (Exception e) { Diag.log("[room] after the call: " + e.getMessage()); }
                }
            } });
        }
        if (wasInCall) Diag.log("[app] call " + s + " with " + with + " ended" + (why != null ? ": " + why : ""));
        session = ""; with = "";
        getWindow().clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        if (callV.getVisibility() == View.VISIBLE) show(lobbyV);
    }

    private boolean has(String p) { return checkSelfPermission(p) == PackageManager.PERMISSION_GRANTED; }

    @Override
    public void onRequestPermissionsResult(int code, String[] perms, int[] res) {
        CallCore c = core;
        if (c == null) return;
        if (has(Manifest.permission.CAMERA) && camera == null) startCamera(c);
        if (has(Manifest.permission.RECORD_AUDIO) && "no permission to record".equals(audio.micState)) {
            final CallCore cc = c;
            ops.execute(new Runnable() { public void run() {
                audio.stop();
                ui.post(new Runnable() { public void run() { if (core == cc) audio.start(cc, true); } });
            } });
        }
        updateButtons();
    }

    private void applyLink(int v) {
        linkBps = v >= K_STEPS ? 0 : (long) (K_MIN * Math.pow(K_MAX / K_MIN, v / (double) (K_STEPS - 1)));
        linkT.setText("Link: " + (linkBps == 0 ? "unlimited" : linkBps >= 1000000 ? String.format(Locale.ROOT, "%.2f Mb/s", linkBps / 1e6) : (linkBps / 1000) + " kb/s"));
        final CallCore c = core;
        final Room r = room;
        if (c == null || r == null) return;
        c.setThrottle(linkBps);                       // the link: both directions ([THE_LINK_IS_SYMMETRIC_V1])
        final long b = linkBps; final String s = session;
        ops.execute(new Runnable() { public void run() {
            try { r.setMediaSpeed(s, b); } catch (Exception e) { Diag.log("[room] MediaSpeed: " + e.getMessage()); }
        } });
    }

    private void sendChat() {
        final String text = sayE.getText().toString().trim();
        final Room r = room;
        if (text.isEmpty() || r == null || core == null) return;
        sayE.setText("");
        final String s = session;
        ops.execute(new Runnable() { public void run() {
            try { r.sendChat(s, text); } catch (Exception e) { toastLog("chat not sent: " + e.getMessage()); }
        } });
    }

    private void toastLog(final String s) {
        Diag.log("[app] " + s);
        ui.post(new Runnable() { public void run() { android.widget.Toast.makeText(CommunicatorActivity.this, s, android.widget.Toast.LENGTH_LONG).show(); } });
    }

    // ---- drawing -----------------------------------------------------------------------------------------------------
    private void drawLobby(List<Room.Member> ros) {
        people.removeAllViews();
        int n = 0;
        for (final Room.Member m : ros) {
            if (m.meId.equals(room == null ? "" : room.meId)) continue;
            n++;
            LinearLayout row = new LinearLayout(this);
            row.setOrientation(LinearLayout.HORIZONTAL);
            row.setGravity(Gravity.CENTER_VERTICAL);
            row.setPadding(dp(16), dp(12), dp(12), dp(12));
            row.setBackground(round(CARD, 14));
            LinearLayout.LayoutParams rp = new LinearLayout.LayoutParams(-1, -2);
            rp.bottomMargin = dp(8);
            LinearLayout txt = new LinearLayout(this);
            txt.setOrientation(LinearLayout.VERTICAL);
            TextView nm = text(m.name, 17, FG, true);
            boolean feed = m.name.startsWith("*");
            TextView st = text(feed ? "unattended camera" : m.status, 13, "in a call".equals(m.status) ? AMBER : MUTED, false);
            txt.addView(nm); txt.addView(st);
            row.addView(txt, new LinearLayout.LayoutParams(0, -2, 1));
            Button b = button(feed ? "Watch" : "Call", GREEN, Color.WHITE);
            b.setOnClickListener(new View.OnClickListener() { public void onClick(View v) {
                if (m.name.startsWith("*")) watch(m.name.substring(1)); else call(m.name);
            } });
            row.addView(b, new LinearLayout.LayoutParams(-2, dp(44)));
            people.addView(row, rp);
        }
        emptyT.setVisibility(n == 0 ? View.VISIBLE : View.GONE);
    }

    private void drawChat(Room r) {
        List<Room.Line> lines = r.readChat(session);
        if (lines.size() == chatCount) return;
        StringBuilder b = new StringBuilder();
        for (Room.Line l : lines) b.append(l.fromName.equals(myName) ? "You" : l.fromName).append(": ").append(l.text).append('\n');
        boolean news = chatCount >= 0 && lines.size() > chatCount && !lines.get(lines.size() - 1).fromName.equals(myName);
        chatCount = lines.size();
        chatT.setText(b.toString());
        chatScroll.post(new Runnable() { public void run() { chatScroll.fullScroll(View.FOCUS_DOWN); } });
        if (news && chatPanel.getVisibility() != View.VISIBLE) {
            chatPanel.setVisibility(View.VISIBLE);
            showChrome();
            Diag.log("[chat] " + lines.get(lines.size() - 1).fromName + ": " + lines.get(lines.size() - 1).text);
        }
    }

    private void drawStats(Room r) {
        CallCore c = core;
        if (c == null) return;
        long now = System.currentTimeMillis();
        CallCore.Stats s = c.stats;
        long[] cur = {s.vSent, s.vRecv, s.vRxBytes, s.aSent, s.aRecv, s.vBytes, s.vDropped, s.aShed};
        double dt = prevT == 0 ? 0 : (now - prevT) / 1000.0;
        double[] rate = new double[8];
        if (dt > 0) for (int k = 0; k < 8; k++) rate[k] = (cur[k] - prev[k]) / dt;
        prev = cur; prevT = now;
        RemoteVideo rv = remote;
        Room.Hold h = r.myHold(session);
        String in = rv != null && rv.picW > 0 ? rv.picW + "x" + rv.picH : "no picture yet";
        String out = s.sendW > 0 ? "L" + s.rung + " " + s.sendW + "x" + s.sendH : (camera == null ? "camera off" : s.rung < 5 && s.sendW == 0 && s.vSent > 0 ? "audio only" : "starting");
        // [THE_OVERLAY_SHOWS_THE_CHANNEL_V1] the call, the link and the channel, as the desktop's overlay shows them
        statsT.setText(String.format(Locale.ROOT,
                "with %s  call #%d  %s\nreceiving  %s  %.1f fps  %d kb/s%s\nsending    %s  %.1f fps  %d kb/s (ceiling L%d)\naudio      in %.0f/s  out %.0f/s%s\nshed       video %.0f/s  audio %.0f/s  key requests %d\nlink       %s\n%s",
                with, callsMade, fmtTime(now - callStartMs),
                in, rate[1], (long) (rate[2] * 8 / 1000), h.present ? (h.audioOnly ? "  (server: audio only)" : h.passthrough ? "  (as sent)" : h.w > 0 ? "  (server made " + h.w + "x" + h.h + ")" : "") : "",
                out, rate[0], (long) (rate[5] * 8 / 1000), s.ceiling,
                rate[4], rate[3], audio.micState.equals("on") ? (audio.muted() ? "  (muted)" : "") : "  (mic: " + audio.micState + ")",
                rate[6], rate[7], s.keyreqs,
                linkBps == 0 ? "unlimited" : linkBps >= 1000000 ? String.format(Locale.ROOT, "%.2f Mb/s", linkBps / 1e6) : (linkBps / 1000) + " kb/s",
                channelLines()));
        long quiet = now - lastMediaMs;
        peerT.setText(with + (quiet > 10000 ? String.format(Locale.ROOT, "  ·  nothing from the call for %d s", quiet / 1000)
                : "  ·  " + fmtTime(now - callStartMs)));
    }

    /** The channel's lines of the overlay: protocol, cipher, 8 characters of a hash of the key, where the memory is,
     *  what this phone's planes have done under the frame, and the last change with how long ago. */
    private String channelLines() {
        Channel ch = Channel.watching();
        if (ch == null) return "channel    none";
        Map<String, String> f = ch.fields();
        String number = f.get("number");
        boolean sealed = !(number == null || number.isEmpty() || number.equals("0"));
        ChannelFrame.Counts n = ch.totals();
        StringBuilder b = new StringBuilder();
        b.append("channel    ").append(ch.role).append(ch.framed ? "" : " (planes NOT under the frame)")
                .append("\nprotocol   ").append(f.get("driver") == null ? "tcp" : f.get("driver"))
                .append("\ncipher     ").append(sealed ? f.get("protection") + "  key " + ch.keyTag() : "not sealed")
                .append("\nmemory     ").append(RamAt.text())
                .append(String.format(Locale.ROOT, "\nframed     %d kB out  %d kB in", n.sent / 1000, n.received / 1000))
                .append(String.format(Locale.ROOT, "\nswitches   sent: protocol %d  key/cipher %d\n           received: protocol %d  key/cipher %d", n.protocol[0], n.encryption[0], n.protocol[1], n.encryption[1]))
                .append(String.format(Locale.ROOT, "\nsent again %d  not opened %d", n.sentAgain, n.notOpened));
        String[] lc = ch.lastChange();
        if (!lc[0].isEmpty()) {
            long age = Long.parseLong(lc[2]);
            b.append("\nlast       ").append(lc[0]).append("\n           at ").append(lc[1]).append(" (").append(age < 90 ? age + " s" : (age / 60) + " min").append(" ago)  ")
                    .append(ch.changes()).append(" taken");
        }
        if (!ch.ignored().isEmpty()) b.append("\nIGNORED    ").append(ch.ignored());
        return b.toString();
    }

    /** The Channel panel's first line: what the Role's setting is now. */
    private String channelNow() {
        Channel ch = Channel.watching();
        if (ch == null) return "No channel";
        Map<String, String> f = ch.fields();
        String number = f.get("number");
        boolean sealed = !(number == null || number.isEmpty() || number.equals("0"));
        return "Now: " + (f.get("driver") == null ? "tcp" : f.get("driver")) + "  ·  " + (sealed ? f.get("protection") + "  ·  key " + ch.keyTag() : "not sealed") + "  ·  memory " + RamAt.text();
    }

    private static String fmtTime(long ms) { long s = ms / 1000; return String.format(Locale.ROOT, "%d:%02d", s / 60, s % 60); }

    private void refreshDiag() {
        StringBuilder b = new StringBuilder();
        b.append("build ").append(Diag.BUILD).append('\n');
        b.append("room: ").append(roomState).append('\n');
        b.append("media server: ").append(mediaAddr).append('\n');
        Channel ch = Channel.watching();
        if (ch != null) b.append("channel '").append(ch.role).append("': ").append(ch.held()).append(ch.framed ? "; this phone's planes are under the frame" : "; this phone does not speak the frame")
                .append(ch.ignored().isEmpty() ? "" : "; IGNORED a setting: " + ch.ignored()).append("; memory at ").append(RamAt.text()).append(" (").append(ChannelFrame.BUILD).append(")\n");
        CallCore c = core;
        if (c != null) {
            CallCore.Stats s = c.stats;
            b.append(String.format(Locale.ROOT, "call %s with %s: video sent %d dropped %d keys %d (keyreqs %d) recv %d; audio sent %d shed %d recv %d; rung L%d ceiling L%d %s%n",
                    session, with, s.vSent, s.vDropped, s.keysSent, s.keyreqs, s.vRecv, s.aSent, s.aShed, s.aRecv, s.rung, s.ceiling, s.reason));
            b.append(String.format(Locale.ROOT, "audio: mic %s (%d blocks, %d encoded), playout %s (%d blocks), decoded %d, errors %d; mixer pads %d trims %d%n",
                    audio.micState, audio.micBlocks, audio.encPackets, audio.playState, audio.playBlocks, audio.decPackets, audio.decErrors,
                    audio.mixer().pads, audio.mixer().trims));
            CameraGl g = camera;
            if (g != null) b.append("camera: ").append(g.state).append(", frames ").append(g.frames).append(", encoded ").append(g.encoded).append('\n');
            RemoteVideo rv = remote;
            if (rv != null) b.append(String.format(Locale.ROOT, "picture: %s %dx%d by %s, decoded %d, dropped back to a keyframe %d, errors %d%n",
                    rv.shown, rv.picW, rv.picH, rv.decoderName, rv.decoded, rv.dropped, rv.errors));
        }
        b.append('\n');
        List<String> l = Diag.lines();
        for (int k = l.size() - 1; k >= 0; k--) b.append(l.get(k)).append('\n');
        diagT.setText(b.toString());
    }

    private void updateButtons() {
        micB.setText(audio.muted() ? "Unmute" : "Mute");
        camB.setText(camera != null ? "Cam off" : "Cam on");
        spkB.setText(audio.speaker() ? "Earpiece" : "Speaker");
    }

    // ---- building the screens ---------------------------------------------------------------------------------------
    static final int BG = 0xFF0F1512, CARD = 0xFF1A231E, FG = 0xFFEAF2EC, MUTED = 0xFF8FA396, GREEN = 0xFF2E9E5B, RED = 0xFFD9443C,
            AMBER = 0xFFE0A93B, CHIP = 0xFF2A3530;

    private View buildSetup() {
        ScrollView sv = new ScrollView(this);
        LinearLayout l = column(dp(24));
        l.addView(text("FrogNet Communicator", 26, FG, true));
        l.addView(text("Calls, chat and video with everyone in the Communicator's room — the same room as the desktop app.", 14, MUTED, false), gap(8, 24));
        l.addView(text("Your name", 13, MUTED, false));
        nameE = edit(prefs.getString("name", Build.MODEL), InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_FLAG_CAP_WORDS);
        l.addView(nameE, gap(4, 16));
        l.addView(text(ramAddr == null ? "No Communicator: open it from the FrogNet member, in a pond whose members run one."
                : "The Communicator of pond " + pond + ", on " + hostedBy + ": memory " + ramAddr + ", media " + mediaAddr, 13, MUTED, false), gap(4, 24));
        Button go = button("Save", GREEN, Color.WHITE);
        go.setOnClickListener(new View.OnClickListener() { public void onClick(View v) {
            String n = nameE.getText().toString().trim();
            if (n.isEmpty()) { toastLog("a name"); return; }
            if (ramAddr == null) { toastLog("no Communicator in this pond: open it from the FrogNet member"); return; }
            boolean moved = !n.equals(myName);
            prefs.edit().putString("name", n).apply();
            myName = n;
            show(lobbyV);
            if (moved) {
                final Room old = room; room = null;
                if (old != null) ops.execute(new Runnable() { public void run() { old.close(); } });
                join();
            }
        } });
        l.addView(go, new LinearLayout.LayoutParams(-1, dp(52)));
        l.addView(text("build " + Diag.BUILD, 12, MUTED, false), gap(24, 0));
        sv.addView(l);
        return sv;
    }

    private View buildLobby() {
        LinearLayout l = column(dp(16));
        LinearLayout top = new LinearLayout(this);
        top.setGravity(Gravity.CENTER_VERTICAL);
        top.addView(text("In the room", 24, FG, true), new LinearLayout.LayoutParams(0, -2, 1));
        Button diag = button("Log", CHIP, FG);
        diag.setOnClickListener(new View.OnClickListener() { public void onClick(View v) { show(diagV); } });
        Button set = button("Settings", CHIP, FG);
        set.setOnClickListener(new View.OnClickListener() { public void onClick(View v) { show(setupV); } });
        top.addView(diag, new LinearLayout.LayoutParams(-2, dp(40)));
        LinearLayout.LayoutParams sp = new LinearLayout.LayoutParams(-2, dp(40)); sp.leftMargin = dp(8);
        top.addView(set, sp);
        l.addView(top);
        l.addView(tabs(false), gap(8, 4));
        lobbyStatus = text("", 13, MUTED, false);
        l.addView(lobbyStatus, gap(4, 12));
        ringBar = new LinearLayout(this);
        ringBar.setOrientation(LinearLayout.HORIZONTAL);
        ringBar.setGravity(Gravity.CENTER_VERTICAL);
        ringBar.setPadding(dp(16), dp(14), dp(12), dp(14));
        ringBar.setBackground(round(0xFF1F3B2B, 14));
        ringText = text("", 18, FG, true);
        ringBar.addView(ringText, new LinearLayout.LayoutParams(0, -2, 1));
        Button no = button("Decline", RED, Color.WHITE);
        no.setOnClickListener(new View.OnClickListener() { public void onClick(View v) { decline(); } });
        Button yes = button("Answer", GREEN, Color.WHITE);
        yes.setOnClickListener(new View.OnClickListener() { public void onClick(View v) { answer(); } });
        ringBar.addView(no, new LinearLayout.LayoutParams(-2, dp(44)));
        LinearLayout.LayoutParams yp = new LinearLayout.LayoutParams(-2, dp(44)); yp.leftMargin = dp(8);
        ringBar.addView(yes, yp);
        ringBar.setVisibility(View.GONE);
        l.addView(ringBar, gap(0, 12));
        ScrollView sv = new ScrollView(this);
        LinearLayout inner = new LinearLayout(this);
        inner.setOrientation(LinearLayout.VERTICAL);
        people = new LinearLayout(this);
        people.setOrientation(LinearLayout.VERTICAL);
        inner.addView(people);
        emptyT = text("Nobody else is in the room yet. Anyone running the Communicator against the same memory appears here.", 14, MUTED, false);
        inner.addView(emptyT, gap(12, 0));
        sv.addView(inner);
        l.addView(sv, new LinearLayout.LayoutParams(-1, 0, 1));
        return l;
    }

    /** The two tabs of the room's screen: Room and Security. */
    private View tabs(boolean security) {
        LinearLayout t = new LinearLayout(this);
        t.setOrientation(LinearLayout.HORIZONTAL);
        Button room = button("Room", security ? CHIP : GREEN, security ? FG : Color.WHITE);
        Button secb = button("Security", security ? GREEN : CHIP, security ? Color.WHITE : FG);
        room.setOnClickListener(new View.OnClickListener() { public void onClick(View v) { show(lobbyV); } });
        secb.setOnClickListener(new View.OnClickListener() { public void onClick(View v) { show(securityV); } });
        t.addView(room, new LinearLayout.LayoutParams(0, dp(40), 1));
        LinearLayout.LayoutParams p = new LinearLayout.LayoutParams(0, dp(40), 1); p.leftMargin = dp(8);
        t.addView(secb, p);
        return t;
    }

    private View row(String label, final String[] names, final Runnable[] acts) {
        LinearLayout box = new LinearLayout(this);
        box.setOrientation(LinearLayout.VERTICAL);
        box.addView(text(label, 13, MUTED, false));
        LinearLayout r = new LinearLayout(this);
        r.setOrientation(LinearLayout.HORIZONTAL);
        for (int k = 0; k < names.length; k++) {
            Button b = button(names[k], CHIP, FG);
            b.setAllCaps(false);
            final Runnable act = acts[k];
            b.setOnClickListener(new View.OnClickListener() { public void onClick(View v) { act.run(); } });
            LinearLayout.LayoutParams p = new LinearLayout.LayoutParams(0, dp(44), 1); if (k > 0) p.leftMargin = dp(6);
            r.addView(b, p);
        }
        box.addView(r, gap(4, 0));
        return box;
    }

    private Runnable change(final String what, final String protocol, final String algorithm, final boolean unseal, final boolean newKey) {
        return new Runnable() { public void run() {
            Channel.Change c = new Channel.Change();
            c.protocol = protocol; c.algorithm = algorithm; c.unseal = unseal; c.newKey = newKey;
            ownerChange(what, c);
        } };
    }

    /**
     * [THE_CALL_IS_UNDER_THE_FRAME_V1] The Security tab. What the Role's setting is now, what this phone holds, and the
     * owner's changes: the cipher, a new key, the protocol, and where the memory is. Each is one write of the Role's
     * setting (core/Channel.java change, as comms-channel does on a desktop); it carries a check made with this
     * phone's secret, so a phone with no secret cannot change a Role whose other machines hold one.
     */
    private View buildSecurity() {
        LinearLayout l = column(dp(16));
        l.addView(text("Security", 24, FG, true));
        l.addView(tabs(true), gap(8, 8));
        ScrollView sv = new ScrollView(this);
        LinearLayout in = new LinearLayout(this);
        in.setOrientation(LinearLayout.VERTICAL);
        secNowT = text("", 15, FG, false);
        secNowT.setTextIsSelectable(true);
        in.addView(secNowT, gap(4, 8));
        secDeviceT = text("", 13, MUTED, false);
        in.addView(secDeviceT, gap(0, 12));
        in.addView(row("Cipher", new String[]{"Not sealed", "AES-256-GCM", "ChaCha20-Poly1305"}, new Runnable[]{
                change("not sealed", "", "", true, false), change("sealed with aes-256-gcm", "", "aes-256-gcm", false, false),
                change("sealed with chacha20-poly1305", "", "chacha20-poly1305", false, false)}), gap(0, 12));
        in.addView(row("Key", new String[]{"New key"}, new Runnable[]{change("a new key", "", "", false, true)}), gap(0, 12));
        // [A_SECRET_IS_SHOWN_TO_BE_SCANNED_V1] the Role's secret comes to this phone from a machine that holds it: its QR
        // code read by the camera, or the file itself
        in.addView(row("This phone's secret", new String[]{"Scan secret…", "Import file…"}, new Runnable[]{
                new Runnable() { public void run() {
                    if (core != null) { toastLog("hang up first: the call has the camera"); return; }
                    startActivityForResult(new android.content.Intent(CommunicatorActivity.this, SecretScanActivity.class), SCAN_SECRET);
                } },
                new Runnable() { public void run() {
                    android.content.Intent i = new android.content.Intent(android.content.Intent.ACTION_OPEN_DOCUMENT);
                    i.addCategory(android.content.Intent.CATEGORY_OPENABLE); i.setType("*/*");
                    try { startActivityForResult(i, PICK_SECRET); } catch (RuntimeException e) { toastLog("no file chooser on this device: " + e.getMessage()); }
                } }}), gap(0, 12));
        // [THE_PHONE_IS_RESET_V1] back to how the Communicator is on a phone that has never run it: for a demonstration
        in.addView(row("Start over", new String[]{"Reset this phone"}, new Runnable[]{new Runnable() { public void run() {
            if (core != null) { toastLog("hang up first"); return; }
            new android.app.AlertDialog.Builder(CommunicatorActivity.this)
                    .setTitle("Reset this phone's Communicator?")
                    .setMessage("Its secret is erased and its Communicator settings go back to what they were at first (it speaks the frame, the picture fills the screen, front camera). "
                            + "The phone stays joined to FrogNet, and the Role's setting in the memory is not changed.")
                    .setNegativeButton("Cancel", null)
                    .setPositiveButton("Reset", new android.content.DialogInterface.OnClickListener() { public void onClick(android.content.DialogInterface d, int w) { resetPhone(); } })
                    .show();
        } }}), gap(0, 12));
        in.addView(row("Protocol", new String[]{"tcp", "chorus", "openqasm"}, new Runnable[]{
                change("protocol tcp", "tcp", "", false, false), change("protocol chorus", "chorus", "", false, false), change("protocol openqasm", "openqasm", "", false, false)}), gap(0, 12));
        in.addView(text("RAM host: another comms-ram that is running, as host:port", 13, MUTED, false));
        ramToE = edit("", InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_URI);
        ramToE.setHint("host:port");
        in.addView(ramToE, gap(4, 6));
        Button move = button("Change RAM host", AMBER, Color.BLACK);
        move.setAllCaps(false);
        move.setOnClickListener(new View.OnClickListener() { public void onClick(View v) {
            String to = ramToE.getText().toString().trim();
            if (!to.matches(".+:\\d+")) { toastLog("the new RAM host as host:port"); return; }
            Channel.Change c = new Channel.Change();
            c.moveHost = to.substring(0, to.lastIndexOf(':')); c.movePort = Integer.parseInt(to.substring(to.lastIndexOf(':') + 1));
            ownerChange("the memory to " + to, c);
        } });
        in.addView(move, new LinearLayout.LayoutParams(-1, dp(48)));
        frameB = button("", CHIP, FG);
        frameB.setAllCaps(false);
        frameB.setOnClickListener(new View.OnClickListener() { public void onClick(View v) {
            if (core != null) { toastLog("hang up first: a call's planes are what they were opened as"); return; }
            speaksFrame = !speaksFrame;
            prefs.edit().putBoolean("frame", speaksFrame).apply();
            Diag.log("[app] this phone " + (speaksFrame ? "speaks the frame from now on" : "does not speak the frame from now on"));
            rejoinChannel();
        } });
        frameB.setVisibility(View.GONE);                          // [THE_PHONE_SPEAKS_THE_FRAME_V1] no switch: it always does,
        in.addView(text("This phone speaks the frame", 14, FG, false), gap(12, 8));   // and the page says so
        secResultT = text("", 14, AMBER, false);
        secResultT.setTextIsSelectable(true);
        in.addView(secResultT, gap(8, 8));
        in.addView(text("Every end and what it is running", 13, MUTED, false));
        secEndsT = text("", 12, FG, false);
        secEndsT.setTypeface(Typeface.MONOSPACE);
        secEndsT.setTextIsSelectable(true);
        in.addView(secEndsT, gap(4, 24));
        sv.addView(in);
        l.addView(sv, new LinearLayout.LayoutParams(-1, 0, 1));
        return l;
    }

    /** The app's channel again (another secret, or the frame turned on or off): the room is rejoined with it. */
    /** [THE_PHONE_IS_RESET_V1] The secrets written over and removed, the Communicator's own settings cleared (the name
     *  it joined under is kept), and the channel joined again as a phone that holds no secret. */
    private void resetPhone() {
        int n = ChannelFrame.forgetSecrets(secretsDir);
        String name = prefs.getString("name", "");
        prefs.edit().clear().apply();
        if (!name.isEmpty()) prefs.edit().putString("name", name).apply();
        speaksFrame = true; selfCorner = Gravity.TOP | Gravity.END;
        toastLog("this phone is reset: " + n + " secret(s) erased, settings as at first (it speaks the frame)");
        rejoinChannel();
        secShownAt = 0;
        if (securityV.getVisibility() == View.VISIBLE) refreshSecurity();
    }

    private void rejoinChannel() {
        final Room old = room; room = null;
        ops.execute(new Runnable() { public void run() {
            if (old != null) old.close();
            Channel ch = Channel.watching();
            if (ch != null) ch.close();
        } });
        join();
        secShownAt = 0;
    }

    private void ownerChange(final String what, final Channel.Change c) {
        if (ramAddr == null) { toastLog("no Communicator in this pond"); return; }
        secResultT.setText("Setting " + what + " …"); chanResultT.setText("Setting " + what + " …");
        Diag.log("[security] the owner sets " + what);
        sec.execute(new Runnable() { public void run() {
            String out;
            try {
                Channel.Done d = Channel.change(RamAt.host(), RamAt.port(), Channel.ROLE, c, 10.0, secretsDir);
                StringBuilder b = new StringBuilder(what + ": " + d.summary());
                for (String line : d.lines) b.append('\n').append(line);
                if (d.moved) b.append("\n").append(d.chatCarried).append(" line(s) of chat carried there");
                if (!d.checked) b.append("\nThis phone holds no secret: the setting carries no check, and an end that holds one ignores it");
                out = b.toString();
            } catch (IllegalArgumentException e) { out = "Not set: " + e.getMessage(); }
            catch (Exception e) { out = "Not set: " + e.getMessage(); }
            final String said = out;
            Diag.log("[security] " + said.replace('\n', ' '));
            ui.post(new Runnable() { public void run() {
                secResultT.setText(said); chanResultT.setText(said); chanNowT.setText(channelNow());
                secShownAt = 0;
                if (securityV.getVisibility() == View.VISIBLE) refreshSecurity();
            } });
        } });
    }

    private void refreshSecurity() {
        Channel ch = Channel.watching();
        long now = System.currentTimeMillis();
        if (ch == null) { secNowT.setText(ramAddr == null ? "No Communicator in this pond." : "Reading the Role's setting from " + RamAt.text() + " …"); return; }
        Map<String, String> f = ch.fields();
        String number = f.get("number"), cipher = number == null || number.equals("0") || number.isEmpty() ? "not sealed" : String.valueOf(f.get("protection"));
        String secret;
        try { secret = Channel.secretLine(Channel.ROLE, secretsDir); } catch (ChannelFrame.Gone e) { secret = "this phone holds no secret (Scan secret… or Import file…)"; }
        secNowT.setText("Role " + ch.role + "\nCipher: " + cipher + "\nKey: computed from the secret — " + secret
                + "\nProtocol: " + (f.get("driver") == null ? "tcp" : f.get("driver")) + "\nRAM host: " + RamAt.text());
        secDeviceT.setText("This phone sends as " + ch.sender + (ch.ignored().isEmpty() ? "" : "\nIt IGNORED the last setting: " + ch.ignored())
                + (ChannelFrame.protectionWorks("chacha20-poly1305") ? "" : "\nThis version of Android has no ChaCha20-Poly1305 (it needs Android 9)"));
        frameB.setText(speaksFrame ? "This phone speaks the frame: tap to turn off" : "This phone does not speak the frame: tap to turn on");
        if (now - secShownAt < 3000 && ch.changes() == secChanges) return;      // the ends: read again when a setting was taken, or every 3 s
        secShownAt = now; secChanges = ch.changes();
        final String role = ch.role;
        sec.execute(new Runnable() { public void run() {
            final StringBuilder b = new StringBuilder();
            try {
                Channel.Now n = Channel.show(RamAt.host(), RamAt.port(), role);
                for (Map.Entry<String, Map<String, String>> e : n.ends.entrySet()) {
                    Map<String, String> r = e.getValue();
                    boolean sealed = "true".equals(r.get("sealed"));
                    b.append(e.getKey()).append("\n    ").append(r.get("driver")).append(sealed ? ", sealed with " + r.get("protection") : ", not sealed")
                            .append("false".equals(r.get("framed")) ? ", not under the frame" : "").append("\n    memory at ").append(r.get("ram"))
                            .append(r.get("ignored") == null || r.get("ignored").isEmpty() ? "" : "\n    IGNORED: " + r.get("ignored")).append('\n');
                }
                if (n.ends.isEmpty()) b.append("(no end reports)");
            } catch (Exception e) { b.append("could not read the ends: ").append(e.getMessage()); }
            ui.post(new Runnable() { public void run() { secEndsT.setText(b.toString()); } });
        } });
    }

    /** Import secret…: the chosen file's bytes become this phone's secret for every Role that has none of its own. */
    @Override
    protected void onActivityResult(int code, int result, android.content.Intent data) {
        super.onActivityResult(code, result, data);
        if (code == SCAN_SECRET) {                                    // the scanner kept it; this is what a screen may say
            if (result == RESULT_OK && data != null) {
                toastLog("secret scanned: " + data.getStringExtra(SecretScanActivity.X_SAID));
                if (core == null) rejoinChannel();
                secShownAt = 0;
            }
            return;
        }
        if (code != PICK_SECRET || result != RESULT_OK || data == null || data.getData() == null) return;
        try {
            java.io.InputStream in = getContentResolver().openInputStream(data.getData());
            java.io.ByteArrayOutputStream b = new java.io.ByteArrayOutputStream();
            byte[] buf = new byte[4096]; int n;
            try { while ((n = in.read(buf)) > 0 && b.size() <= 65536) b.write(buf, 0, n); } finally { in.close(); }
            if (b.size() < 32 || b.size() > 65536) { toastLog("a secret is a file of 32 bytes to 64 KiB; this one is " + b.size() + " bytes: not imported"); return; }
            java.io.File f = new java.io.File(secretsDir, "default"), tmp = new java.io.File(secretsDir, "default.new");
            java.io.FileOutputStream o = new java.io.FileOutputStream(tmp);
            try { o.write(b.toByteArray()); o.getFD().sync(); } finally { o.close(); }
            if (!tmp.renameTo(f)) { toastLog("the secret could not be written"); return; }
            toastLog("secret imported: " + Channel.secretLine(Channel.ROLE, secretsDir) + (core != null ? " (a call in progress keeps the one it started with)" : ""));
            if (core == null) rejoinChannel();
        } catch (Exception e) { toastLog("the secret was not imported: " + e.getMessage()); }
    }

    private View buildCall() {
        FrameLayout f = new FrameLayout(this);
        f.setBackgroundColor(Color.BLACK);
        remoteTV = new TextureView(this);
        remoteTV.setSurfaceTextureListener(new TextureView.SurfaceTextureListener() {
            public void onSurfaceTextureAvailable(SurfaceTexture s, int w, int h) { if (remote != null) remote.setSurface(s); }
            public void onSurfaceTextureSizeChanged(SurfaceTexture s, int w, int h) { if (remote != null) remote.fit(); }
            public boolean onSurfaceTextureDestroyed(SurfaceTexture s) {   // released by whoever draws into it, once it stops
                if (remote != null) remote.dropSurface(s); else s.release();
                return false;
            }
            public void onSurfaceTextureUpdated(SurfaceTexture s) { }
        });
        // tap: the controls; double tap: fill the screen <-> the whole picture; long press: the next source (a group call)
        final android.view.GestureDetector gd = new android.view.GestureDetector(this, new android.view.GestureDetector.SimpleOnGestureListener() {
            public boolean onDown(android.view.MotionEvent e) { return true; }
            public boolean onSingleTapConfirmed(android.view.MotionEvent e) { toggleChrome(); return true; }
            public boolean onDoubleTap(android.view.MotionEvent e) {
                RemoteVideo rv = remote;
                if (rv != null) { rv.fill = !rv.fill; rv.fit(); prefs.edit().putBoolean("fill", rv.fill).apply(); }
                return true;
            }
            public void onLongPress(android.view.MotionEvent e) { if (remote != null) remote.cycle(); }
        });
        remoteTV.setOnTouchListener(new View.OnTouchListener() { public boolean onTouch(View v, android.view.MotionEvent e) { return gd.onTouchEvent(e); } });
        f.addView(remoteTV, new FrameLayout.LayoutParams(-1, -1));
        selfTV = new TextureView(this);
        selfTV.setSurfaceTextureListener(new TextureView.SurfaceTextureListener() {
            public void onSurfaceTextureAvailable(SurfaceTexture s, int w, int h) { if (camera != null) camera.setSelfView(s, w, h); }
            public void onSurfaceTextureSizeChanged(SurfaceTexture s, int w, int h) { if (camera != null) camera.setSelfView(s, w, h); }
            public boolean onSurfaceTextureDestroyed(SurfaceTexture s) {
                if (camera != null) camera.dropSelfView(s); else s.release();
                return false;
            }
            public void onSurfaceTextureUpdated(SurfaceTexture s) { }
        });
        selfBox = new FrameLayout(this);
        selfBox.setPadding(dp(2), dp(2), dp(2), dp(2));
        selfBox.setBackground(round(0xCCFFFFFF, 10));
        selfBox.addView(selfTV, new FrameLayout.LayoutParams(-1, -1));
        selfBox.setOnTouchListener(new View.OnTouchListener() {
            float x0, y0; boolean moved;
            public boolean onTouch(View v, android.view.MotionEvent e) {
                switch (e.getActionMasked()) {
                    case android.view.MotionEvent.ACTION_DOWN: x0 = e.getRawX(); y0 = e.getRawY(); moved = false; return true;
                    case android.view.MotionEvent.ACTION_MOVE:
                        float dx = e.getRawX() - x0, dy = e.getRawY() - y0;
                        if (Math.abs(dx) + Math.abs(dy) > dp(8)) moved = true;
                        v.setTranslationX(dx); v.setTranslationY(dy);
                        return true;
                    case android.view.MotionEvent.ACTION_UP:
                        if (!moved) {                                   // a tap: the other camera
                            if (camera != null) { camera.switchCamera(); prefs.edit().putBoolean("front", !camera.front()).apply(); }
                        } else {                                        // a drag: to the nearest corner
                            View parent = (View) v.getParent();
                            float cx = v.getLeft() + v.getTranslationX() + v.getWidth() / 2f, cy = v.getTop() + v.getTranslationY() + v.getHeight() / 2f;
                            selfCorner = (cx < parent.getWidth() / 2f ? Gravity.START : Gravity.END) | (cy < parent.getHeight() / 2f ? Gravity.TOP : Gravity.BOTTOM);
                            prefs.edit().putInt("corner", selfCorner).apply();
                        }
                        v.setTranslationX(0); v.setTranslationY(0);
                        placeSelf();
                        return true;
                    case android.view.MotionEvent.ACTION_CANCEL:
                        v.setTranslationX(0); v.setTranslationY(0); return true;
                }
                return false;
            }
        });
        selfCorner = prefs.getInt("corner", Gravity.TOP | Gravity.END);
        f.addView(selfBox, new FrameLayout.LayoutParams(dp(112), dp(150)));
        placeSelf();
        peerT = text("", 18, Color.WHITE, true);
        peerT.setShadowLayer(6, 0, 0, Color.BLACK);
        peerT.setPadding(dp(16), dp(14), dp(16), dp(8));
        peerT.setBackground(new android.graphics.drawable.GradientDrawable(android.graphics.drawable.GradientDrawable.Orientation.TOP_BOTTOM,
                new int[]{0x99000000, 0x00000000}));
        f.addView(peerT, new FrameLayout.LayoutParams(-1, -2, Gravity.TOP));
        // [STAMP_THE_PICTURE_V1] this phone's clock, to the millisecond: a desktop sender burns its frame number and
        // clock into the picture's bottom-left corner, so the lag is this minus that, read off the screen
        clockT = text("", 13, Color.WHITE, false);
        clockT.setTypeface(Typeface.MONOSPACE);
        clockT.setShadowLayer(6, 0, 0, Color.BLACK);
        clockT.setPadding(dp(16), 0, dp(16), 0);
        FrameLayout.LayoutParams clp = new FrameLayout.LayoutParams(-2, -2, Gravity.TOP | Gravity.START);
        clp.topMargin = dp(46);
        f.addView(clockT, clp);
        statsPanel = new LinearLayout(this);
        statsPanel.setOrientation(LinearLayout.VERTICAL);
        statsPanel.setPadding(dp(12), dp(10), dp(12), dp(10));
        statsPanel.setBackground(round(0xB0000000, 10));
        statsT = text("", 11, 0xFFD8F0E0, false);
        statsT.setTypeface(Typeface.MONOSPACE);
        statsPanel.addView(statsT);
        linkT = text("Link: unlimited", 12, 0xFFD8F0E0, false);
        LinearLayout linkRow = new LinearLayout(this);
        linkRow.setGravity(Gravity.CENTER_VERTICAL);
        linkRow.addView(linkT, new LinearLayout.LayoutParams(0, -2, 1));
        Button diag = button("Log", CHIP, FG);
        diag.setOnClickListener(new View.OnClickListener() { public void onClick(View v) { show(diagV); } });
        linkRow.addView(diag, new LinearLayout.LayoutParams(-2, dp(34)));
        statsPanel.addView(linkRow, gap(6, 0));
        linkS = new SeekBar(this);
        linkS.setMax(K_STEPS);
        linkS.setProgress(K_STEPS);
        linkS.setOnSeekBarChangeListener(new SeekBar.OnSeekBarChangeListener() {
            public void onProgressChanged(SeekBar s, int v, boolean u) { if (u) applyLink(v); }
            public void onStartTrackingTouch(SeekBar s) { }
            public void onStopTrackingTouch(SeekBar s) { }
        });
        statsPanel.addView(linkS, new LinearLayout.LayoutParams(-1, dp(32)));
        FrameLayout.LayoutParams stp = new FrameLayout.LayoutParams(-1, -2, Gravity.TOP);
        stp.topMargin = dp(52); stp.leftMargin = dp(12); stp.rightMargin = dp(12);
        statsPanel.setVisibility(View.GONE);
        f.addView(statsPanel, stp);
        // chat
        chatPanel = new LinearLayout(this);
        chatPanel.setOrientation(LinearLayout.VERTICAL);
        chatPanel.setPadding(dp(12), dp(10), dp(12), dp(10));
        chatPanel.setBackground(round(0xE0121A16, 14));
        chatScroll = new ScrollView(this);
        chatT = text("", 15, FG, false);
        chatScroll.addView(chatT);
        chatPanel.addView(chatScroll, new LinearLayout.LayoutParams(-1, dp(150)));
        LinearLayout say = new LinearLayout(this);
        say.setGravity(Gravity.CENTER_VERTICAL);
        sayE = edit("", InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_FLAG_CAP_SENTENCES);
        sayE.setHint("Message");
        sayE.setImeOptions(EditorInfo.IME_ACTION_SEND);
        sayE.setOnEditorActionListener(new TextView.OnEditorActionListener() {
            public boolean onEditorAction(TextView v, int a, android.view.KeyEvent e) { sendChat(); return true; }
        });
        say.addView(sayE, new LinearLayout.LayoutParams(0, -2, 1));
        Button send = button("Send", GREEN, Color.WHITE);
        send.setOnClickListener(new View.OnClickListener() { public void onClick(View v) { sendChat(); } });
        LinearLayout.LayoutParams snp = new LinearLayout.LayoutParams(-2, dp(44)); snp.leftMargin = dp(8);
        say.addView(send, snp);
        chatPanel.addView(say, gap(8, 0));
        chatPanel.setVisibility(View.GONE);
        FrameLayout.LayoutParams cp = new FrameLayout.LayoutParams(-1, -2, Gravity.BOTTOM);
        cp.bottomMargin = dp(140); cp.leftMargin = dp(12); cp.rightMargin = dp(12);
        f.addView(chatPanel, cp);
        // [SWITCHED_FROM_THE_CALL_V1] the Channel panel: cipher, key, protocol and where the memory is, changed while the call runs
        channelPanel = new ScrollView(this);
        channelPanel.setBackground(round(0xE0121A16, 14));
        LinearLayout chn = new LinearLayout(this);
        chn.setOrientation(LinearLayout.VERTICAL);
        chn.setPadding(dp(12), dp(10), dp(12), dp(10));
        chanNowT = text("", 13, FG, false);
        chn.addView(chanNowT);
        chn.addView(row("Cipher", new String[]{"Not sealed", "AES-256-GCM", "ChaCha20"}, new Runnable[]{
                change("not sealed", "", "", true, false), change("sealed with aes-256-gcm", "", "aes-256-gcm", false, false),
                change("sealed with chacha20-poly1305", "", "chacha20-poly1305", false, false)}), gap(8, 0));
        chn.addView(row("Protocol", new String[]{"tcp", "chorus", "openqasm"}, new Runnable[]{
                change("protocol tcp", "tcp", "", false, false), change("protocol chorus", "chorus", "", false, false), change("protocol openqasm", "openqasm", "", false, false)}), gap(8, 0));
        chn.addView(text("Key, and the RAM host (another comms-ram that is running)", 13, MUTED, false), gap(8, 0));
        LinearLayout kr = new LinearLayout(this);
        kr.setGravity(Gravity.CENTER_VERTICAL);
        Button newKey = button("New key", CHIP, FG);
        final Runnable nk = change("a new key", "", "", false, true);
        newKey.setOnClickListener(new View.OnClickListener() { public void onClick(View v) { nk.run(); } });
        kr.addView(newKey, new LinearLayout.LayoutParams(-2, dp(44)));
        chanRamE = edit("", InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_URI);
        chanRamE.setHint("host:port");
        LinearLayout.LayoutParams rep = new LinearLayout.LayoutParams(0, -2, 1); rep.leftMargin = dp(8);
        kr.addView(chanRamE, rep);
        Button moveB = button("Move", AMBER, Color.BLACK);
        moveB.setOnClickListener(new View.OnClickListener() { public void onClick(View v) {
            String to = chanRamE.getText().toString().trim();
            if (!to.matches(".+:\\d+")) { toastLog("the new RAM host as host:port"); return; }
            Channel.Change c = new Channel.Change();
            c.moveHost = to.substring(0, to.lastIndexOf(':')); c.movePort = Integer.parseInt(to.substring(to.lastIndexOf(':') + 1));
            ownerChange("the memory to " + to, c);
        } });
        LinearLayout.LayoutParams mvp = new LinearLayout.LayoutParams(-2, dp(44)); mvp.leftMargin = dp(8);
        kr.addView(moveB, mvp);
        chn.addView(kr, gap(4, 0));
        chanResultT = text("", 13, AMBER, false);
        chn.addView(chanResultT, gap(8, 0));
        channelPanel.addView(chn);
        channelPanel.setVisibility(View.GONE);
        FrameLayout.LayoutParams chp = new FrameLayout.LayoutParams(-1, -2, Gravity.BOTTOM);
        chp.bottomMargin = dp(140); chp.leftMargin = dp(12); chp.rightMargin = dp(12);
        f.addView(channelPanel, chp);
        // controls: two rows -- what to look at, then the call itself
        LinearLayout bar = new LinearLayout(this);
        callBar = bar;
        bar.setOrientation(LinearLayout.VERTICAL);
        bar.setPadding(dp(8), dp(20), dp(8), dp(20));
        bar.setBackground(new android.graphics.drawable.GradientDrawable(android.graphics.drawable.GradientDrawable.Orientation.BOTTOM_TOP,
                new int[]{0x99000000, 0x00000000}));
        micB = ctl("Mute", new View.OnClickListener() { public void onClick(View v) { audio.setMuted(!audio.muted()); updateButtons(); } });
        camB = ctl("Cam off", new View.OnClickListener() { public void onClick(View v) {
            CallCore c = core;
            if (camera != null) {
                final CameraGl g = camera; camera = null; orient.disable(); Diag.log("[camera] off");
                ops.execute(new Runnable() { public void run() { g.stop(); } });
            }
            else if (c != null && has(Manifest.permission.CAMERA)) startCamera(c);
            updateButtons();
        } });
        spkB = ctl("Earpiece", new View.OnClickListener() { public void onClick(View v) { audio.setSpeaker(!audio.speaker()); updateButtons(); } });
        Button chat = ctl("Chat", new View.OnClickListener() { public void onClick(View v) {
            chatPanel.setVisibility(chatPanel.getVisibility() == View.VISIBLE ? View.GONE : View.VISIBLE);
            if (chatPanel.getVisibility() == View.VISIBLE) channelPanel.setVisibility(View.GONE);      // they share the place
            showChrome();
        } });
        Button chan = ctl("Channel", new View.OnClickListener() { public void onClick(View v) {
            boolean open = channelPanel.getVisibility() != View.VISIBLE;
            channelPanel.setVisibility(open ? View.VISIBLE : View.GONE);
            if (open) { chatPanel.setVisibility(View.GONE); statsPanel.setVisibility(View.VISIBLE); chanNowT.setText(channelNow()); }   // a change is watched in the Stats
            showChrome();
        } });
        Button stats = ctl("Stats", new View.OnClickListener() { public void onClick(View v) {
            statsPanel.setVisibility(statsPanel.getVisibility() == View.VISIBLE ? View.GONE : View.VISIBLE);
            showChrome();
        } });
        Button end = button("End", RED, Color.WHITE);
        end.setOnClickListener(new View.OnClickListener() { public void onClick(View v) { hangUp(null); } });
        LinearLayout look = new LinearLayout(this), doing = new LinearLayout(this);
        Button[] lookB = {chat, stats, chan}, doingB = {micB, camB, spkB, end};
        for (int k = 0; k < lookB.length; k++) {
            LinearLayout.LayoutParams bp = new LinearLayout.LayoutParams(0, dp(48), 1); if (k > 0) bp.leftMargin = dp(6);
            look.addView(lookB[k], bp);
        }
        for (int k = 0; k < doingB.length; k++) {
            LinearLayout.LayoutParams bp = new LinearLayout.LayoutParams(0, dp(48), 1); if (k > 0) bp.leftMargin = dp(6);
            doing.addView(doingB[k], bp);
        }
        bar.addView(look, new LinearLayout.LayoutParams(-1, -2));
        bar.addView(doing, gap(8, 0));
        f.addView(bar, new FrameLayout.LayoutParams(-1, -2, Gravity.BOTTOM));
        return f;
    }

    // ---- the call screen: the far end fills it; the controls come and go; your camera is an inset --------------------
    private FrameLayout selfBox;
    private LinearLayout callBar;
    private int selfCorner;
    private final Runnable hideChrome = new Runnable() { public void run() {
        RemoteVideo rv = remote;
        boolean busy = chatPanel.getVisibility() == View.VISIBLE || statsPanel.getVisibility() == View.VISIBLE || channelPanel.getVisibility() == View.VISIBLE;
        if (busy || rv == null || rv.picW == 0) { ui.postDelayed(this, 4000); return; }   // nothing to look at yet: keep them
        fadeChrome(false);
    } };

    private void fadeChrome(boolean on) {
        for (View v : new View[]{peerT, callBar}) {
            v.animate().cancel();
            if (on) { v.setVisibility(View.VISIBLE); v.animate().alpha(1f).setDuration(150).start(); }
            else {
                final View w = v;
                v.animate().alpha(0f).setDuration(300).withEndAction(new Runnable() { public void run() { w.setVisibility(View.GONE); } }).start();
            }
        }
    }

    private void showChrome() {
        fadeChrome(true);
        ui.removeCallbacks(hideChrome);
        ui.postDelayed(hideChrome, 4000);
    }

    private void toggleChrome() {
        if (callBar.getVisibility() == View.VISIBLE && callBar.getAlpha() > 0.5f) {
            ui.removeCallbacks(hideChrome);
            chatPanel.setVisibility(View.GONE); statsPanel.setVisibility(View.GONE); channelPanel.setVisibility(View.GONE);
            fadeChrome(false);
        } else showChrome();
    }

    /** The inset: a 3:4 window (4:3 held sideways), in its corner, clear of the system's gesture edges. */
    private void placeSelf() {
        boolean land = getResources().getConfiguration().orientation == Configuration.ORIENTATION_LANDSCAPE;
        FrameLayout.LayoutParams p = (FrameLayout.LayoutParams) selfBox.getLayoutParams();
        p.width = dp(land ? 150 : 112); p.height = dp(land ? 112 : 150);
        p.gravity = selfCorner;
        p.leftMargin = p.rightMargin = dp(14);
        p.topMargin = p.bottomMargin = dp(18);
        selfBox.setLayoutParams(p);
    }

    /** During a call the far end gets the whole display: no status or navigation bar (a swipe from the edge shows
     *  them for a moment), into the camera cutout. */
    private void immersive(boolean on) {
        android.view.Window w = getWindow();
        WindowManager.LayoutParams lp = w.getAttributes();
        lp.layoutInDisplayCutoutMode = on ? WindowManager.LayoutParams.LAYOUT_IN_DISPLAY_CUTOUT_MODE_SHORT_EDGES
                : WindowManager.LayoutParams.LAYOUT_IN_DISPLAY_CUTOUT_MODE_DEFAULT;
        w.setAttributes(lp);
        if (Build.VERSION.SDK_INT >= 30) {
            w.setDecorFitsSystemWindows(!on);
            android.view.WindowInsetsController c = w.getInsetsController();
            if (c != null) {
                if (on) {
                    c.setSystemBarsBehavior(android.view.WindowInsetsController.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE);
                    c.hide(android.view.WindowInsets.Type.systemBars());
                } else c.show(android.view.WindowInsets.Type.systemBars());
            }
        } else {
            w.getDecorView().setSystemUiVisibility(on ? (View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY | View.SYSTEM_UI_FLAG_FULLSCREEN
                    | View.SYSTEM_UI_FLAG_HIDE_NAVIGATION | View.SYSTEM_UI_FLAG_LAYOUT_FULLSCREEN
                    | View.SYSTEM_UI_FLAG_LAYOUT_HIDE_NAVIGATION | View.SYSTEM_UI_FLAG_LAYOUT_STABLE) : 0);
        }
    }

    private View buildDiag() {
        LinearLayout l = column(dp(12));
        LinearLayout top = new LinearLayout(this);
        top.setGravity(Gravity.CENTER_VERTICAL);
        top.addView(text("Log", 22, FG, true), new LinearLayout.LayoutParams(0, -2, 1));
        Button back = button("Back", CHIP, FG);
        back.setOnClickListener(new View.OnClickListener() { public void onClick(View v) { show(core != null ? callV : lobbyV); } });
        top.addView(back, new LinearLayout.LayoutParams(-2, dp(40)));
        l.addView(top);
        diagScroll = new ScrollView(this);
        diagT = text("", 11, 0xFFCFE3D6, false);
        diagT.setTypeface(Typeface.MONOSPACE);
        diagT.setTextIsSelectable(true);
        diagScroll.addView(diagT);
        l.addView(diagScroll, new LinearLayout.LayoutParams(-1, 0, 1));
        return l;
    }

    // ---- little widgets -------------------------------------------------------------------------------------------
    private int dp(int v) { return (int) TypedValue.applyDimension(TypedValue.COMPLEX_UNIT_DIP, v, getResources().getDisplayMetrics()); }

    private LinearLayout column(int pad) {
        LinearLayout l = new LinearLayout(this);
        l.setOrientation(LinearLayout.VERTICAL);
        l.setPadding(pad, pad + dp(8), pad, pad);
        l.setFitsSystemWindows(true);
        return l;
    }

    private LinearLayout.LayoutParams gap(int top, int bottom) {
        LinearLayout.LayoutParams p = new LinearLayout.LayoutParams(-1, -2);
        p.topMargin = dp(top); p.bottomMargin = dp(bottom);
        return p;
    }

    private TextView text(String s, int sp, int color, boolean bold) {
        TextView t = new TextView(this);
        t.setText(s);
        t.setTextSize(TypedValue.COMPLEX_UNIT_SP, sp);
        t.setTextColor(color);
        if (bold) t.setTypeface(Typeface.DEFAULT_BOLD);
        return t;
    }

    private EditText edit(String v, int type) {
        EditText e = new EditText(this);
        e.setText(v);
        e.setInputType(type);
        e.setSingleLine(true);
        e.setTextColor(FG);
        e.setHintTextColor(MUTED);
        e.setBackground(round(CARD, 10));
        e.setPadding(dp(14), dp(12), dp(14), dp(12));
        return e;
    }

    private GradientDrawable round(int color, int radiusDp) {
        GradientDrawable g = new GradientDrawable();
        g.setColor(color);
        g.setCornerRadius(dp(radiusDp));
        return g;
    }

    private Button button(String s, int bg, int fg) {
        Button b = new Button(this);
        b.setText(s);
        b.setAllCaps(false);
        b.setTextColor(fg);
        b.setTextSize(TypedValue.COMPLEX_UNIT_SP, 15);
        b.setBackground(round(bg, 12));
        b.setPadding(dp(16), 0, dp(16), 0);
        b.setStateListAnimator(null);
        return b;
    }

    private Button ctl(String s, View.OnClickListener l) {
        Button b = button(s, 0xCC26302B, Color.WHITE);
        b.setTextSize(TypedValue.COMPLEX_UNIT_SP, 13);
        b.setPadding(dp(4), 0, dp(4), 0);
        b.setOnClickListener(l);
        return b;
    }

    @SuppressWarnings("unused")
    private static ViewGroup.LayoutParams match() { return new ViewGroup.LayoutParams(-1, -1); }

    @SuppressWarnings("unused")
    private Context ctx() { return this; }
}
