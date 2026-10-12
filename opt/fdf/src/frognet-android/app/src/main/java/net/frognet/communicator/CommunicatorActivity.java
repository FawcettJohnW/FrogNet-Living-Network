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
import net.frognet.communicator.core.Fnav;
import net.frognet.communicator.core.Ladder;
import net.frognet.communicator.core.Plane;
import net.frognet.communicator.core.Room;

import java.util.HashSet;
import java.util.List;
import java.util.Locale;
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
    private View setupV, lobbyV, callV, diagV;
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
        final boolean have = readPrefs();                              // before the screens: the setup screen shows them
        setupV = buildSetup(); lobbyV = buildLobby(); callV = buildCall(); diagV = buildDiag();
        root.addView(setupV); root.addView(lobbyV); root.addView(callV); root.addView(diagV);
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
        ops.shutdown();
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
        ui.postDelayed(new Runnable() { public void run() { if (remote != null) remote.fit(); } }, 200);
    }

    @Override
    public void onBackPressed() {
        if (diagV.getVisibility() == View.VISIBLE) { show(core != null ? callV : lobbyV); return; }
        if (chatPanel != null && chatPanel.getVisibility() == View.VISIBLE) { chatPanel.setVisibility(View.GONE); return; }
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
        ramAddr = r; mediaAddr = m; pond = String.valueOf(i.getStringExtra(X_POND)); hostedBy = String.valueOf(i.getStringExtra(X_FROM));
        int c = mediaAddr.lastIndexOf(':');
        mediaHost = mediaAddr.substring(0, c);
        mediaPort = Integer.parseInt(mediaAddr.substring(c + 1));
        return true;
    }

    private void show(View v) {
        for (View x : new View[]{setupV, lobbyV, callV, diagV}) x.setVisibility(x == v ? View.VISIBLE : View.GONE);
        if (v == diagV) refreshDiag();
    }

    // ---- the room: joined once, rejoined when its session is gone -------------------------------------------------
    private void join() {
        final String name = myName, ram = ramAddr;
        roomState = "connecting to " + ram + " ...";
        ops.execute(new Runnable() { public void run() {
            int c = ram.lastIndexOf(':');
            while (!leaving && room == null) {
                try {
                    Room r = new Room(ram.substring(0, c), Integer.parseInt(ram.substring(c + 1)), name, "video,audio",
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
                    break;
                }
        }
        if (core != null) {
            if (changed) drawChat(r);
            drawStats(r);
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
        for (Room.Offer o : r.offers()) if (on.contains(o.session)) { startCall(o.session, o.mediaHost, o.mediaPort, "*" + feed); return; }
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
        startCall(o.session, o.mediaHost, o.mediaPort, o.offeredBy);
    }

    private void decline() {
        if (ringing != null) { answered.add(ringing.session); Diag.log("[app] declined " + ringing.offeredBy); }
        ringing = null;
        stopRinging();
    }

    private void startCall(final String s, final String host, final int port, final String who) {
        hangUp(null);
        final Room r = room;
        if (r == null) return;
        session = s; with = who;
        answered.add(s);
        peerT.setText(who + "  ·  connecting ...");
        chatT.setText(""); chatCount = -1;
        show(callV);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        final boolean cam = has(Manifest.permission.CAMERA), mic = has(Manifest.permission.RECORD_AUDIO);
        if (!cam || !mic) requestPermissions(new String[]{Manifest.permission.CAMERA, Manifest.permission.RECORD_AUDIO}, 1);
        remote = new RemoteVideo(remoteTV);
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
        statsT.setText(String.format(Locale.ROOT,
                "receiving  %s  %.1f fps  %d kb/s%s\nsending    %s  %.1f fps  %d kb/s%s\naudio      in %.0f/s  out %.0f/s%s\nlink       %s",
                in, rate[1], (long) (rate[2] * 8 / 1000), h.present ? (h.passthrough ? "  (as sent)" : h.w > 0 ? "  (server made " + h.w + "x" + h.h + ")" : "") : "",
                out, rate[0], (long) (rate[5] * 8 / 1000), rate[6] > 0 ? String.format(Locale.ROOT, "  shed %.0f/s", rate[6]) : "",
                rate[4], rate[3], audio.micState.equals("on") ? (audio.muted() ? "  (muted)" : "") : "  (mic: " + audio.micState + ")",
                linkBps == 0 ? "unlimited" : (linkBps / 1000) + " kb/s"));
        long quiet = now - lastMediaMs;
        peerT.setText(with + (quiet > 10000 ? String.format(Locale.ROOT, "  ·  nothing from the call for %d s", quiet / 1000)
                : "  ·  " + fmtTime(now - callStartMs)));
    }

    private static String fmtTime(long ms) { long s = ms / 1000; return String.format(Locale.ROOT, "%d:%02d", s / 60, s % 60); }

    private void refreshDiag() {
        StringBuilder b = new StringBuilder();
        b.append("build ").append(Diag.BUILD).append('\n');
        b.append("room: ").append(roomState).append('\n');
        b.append("media server: ").append(mediaAddr).append('\n');
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
        camB.setText(camera != null ? "Camera off" : "Camera on");
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
        remoteTV.setOnClickListener(new View.OnClickListener() { public void onClick(View v) {
            statsPanel.setVisibility(statsPanel.getVisibility() == View.VISIBLE ? View.GONE : View.VISIBLE);
        } });
        remoteTV.setOnLongClickListener(new View.OnLongClickListener() { public boolean onLongClick(View v) { if (remote != null) remote.cycle(); return true; } });
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
        selfTV.setOnClickListener(new View.OnClickListener() { public void onClick(View v) {
            if (camera != null) { camera.switchCamera(); prefs.edit().putBoolean("front", !camera.front()).apply(); }
        } });
        FrameLayout.LayoutParams sp = new FrameLayout.LayoutParams(dp(112), dp(150), Gravity.TOP | Gravity.END);
        sp.topMargin = dp(56); sp.rightMargin = dp(12);
        f.addView(selfTV, sp);
        peerT = text("", 18, Color.WHITE, true);
        peerT.setShadowLayer(6, 0, 0, Color.BLACK);
        peerT.setPadding(dp(16), dp(14), dp(16), dp(8));
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
        statsT = text("", 12, 0xFFD8F0E0, false);
        statsT.setTypeface(Typeface.MONOSPACE);
        statsPanel.addView(statsT);
        linkT = text("Link: unlimited", 12, 0xFFD8F0E0, false);
        statsPanel.addView(linkT, gap(8, 0));
        linkS = new SeekBar(this);
        linkS.setMax(K_STEPS);
        linkS.setProgress(K_STEPS);
        linkS.setOnSeekBarChangeListener(new SeekBar.OnSeekBarChangeListener() {
            public void onProgressChanged(SeekBar s, int v, boolean u) { if (u) applyLink(v); }
            public void onStartTrackingTouch(SeekBar s) { }
            public void onStopTrackingTouch(SeekBar s) { }
        });
        statsPanel.addView(linkS, new LinearLayout.LayoutParams(-1, dp(32)));
        Button diag = button("Log", CHIP, FG);
        diag.setOnClickListener(new View.OnClickListener() { public void onClick(View v) { show(diagV); } });
        statsPanel.addView(diag, new LinearLayout.LayoutParams(-2, dp(36)));
        FrameLayout.LayoutParams stp = new FrameLayout.LayoutParams(-1, -2, Gravity.TOP);
        stp.topMargin = dp(52); stp.leftMargin = dp(12); stp.rightMargin = dp(136);
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
        cp.bottomMargin = dp(84); cp.leftMargin = dp(12); cp.rightMargin = dp(12);
        f.addView(chatPanel, cp);
        // controls
        LinearLayout bar = new LinearLayout(this);
        bar.setGravity(Gravity.CENTER);
        bar.setPadding(dp(8), dp(12), dp(8), dp(20));
        micB = ctl("Mute", new View.OnClickListener() { public void onClick(View v) { audio.setMuted(!audio.muted()); updateButtons(); } });
        camB = ctl("Camera off", new View.OnClickListener() { public void onClick(View v) {
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
        } });
        Button end = button("End", RED, Color.WHITE);
        end.setOnClickListener(new View.OnClickListener() { public void onClick(View v) { hangUp(null); } });
        for (Button b : new Button[]{micB, camB, spkB, chat}) {
            LinearLayout.LayoutParams bp = new LinearLayout.LayoutParams(0, dp(48), 1); bp.rightMargin = dp(6);
            bar.addView(b, bp);
        }
        bar.addView(end, new LinearLayout.LayoutParams(0, dp(48), 1));
        f.addView(bar, new FrameLayout.LayoutParams(-1, -2, Gravity.BOTTOM));
        return f;
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
