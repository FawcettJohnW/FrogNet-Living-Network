// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
package net.frognet.communicator;

import android.Manifest;
import android.content.Context;
import android.content.pm.PackageManager;
import android.graphics.SurfaceTexture;
import android.media.Ringtone;
import android.media.RingtoneManager;
import android.os.Handler;
import android.os.Looper;
import android.view.OrientationEventListener;
import android.view.Surface;
import android.view.TextureView;
import android.view.WindowManager;

import net.frognet.communicator.core.CallCore;
import net.frognet.communicator.core.Fnav;
import net.frognet.communicator.core.Ladder;
import net.frognet.communicator.core.Plane;
import net.frognet.communicator.core.Room;

import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Set;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/**
 * The Communicator inside the kart game: the second memory the game holds. [KART_CALLS_IN_THE_GAME_V1]
 * On Windows and Linux the kart game launches the Communicator (comms-app) as a second program; a phone has one app, so
 * here it is the Communicator's own code -- core/Room, core/CallCore, AudioEngine, CameraGl, RemoteVideo, unchanged, from
 * frognet-android -- run by the game. The calls are the reference Communicator's: the same Communicator memory (comms-ram),
 * the same media server (comms-media), the same invitations and offers, so a call goes to and from comms-app, the phone's
 * FrogNet Communicator, and other phones alike.
 * The room is joined when the game joins the lobby and held until the game leaves; races come and go beside it (the race
 * memory is a third session), so a race ending does not touch a call, and a call can be answered at any time, racing too.
 * Everything here runs on the UI thread except the network, which runs on one worker thread (ops), as in the Activity.
 */
public final class GameCalls {
    public interface Log { void say(String line); }
    static final int FPS = 24;

    private final Context ctx;
    private final Handler ui = new Handler(Looper.getMainLooper());
    private final ExecutorService ops = Executors.newSingleThreadExecutor();
    private final Log log;
    public final String name, ramAddr, mediaAddr;
    private final String mediaHost;
    private final int mediaPort;
    private final TextureView remoteTV, selfTV;

    private volatile Room room;
    private volatile String roomState = "not connected";
    private volatile boolean leaving = false;

    private volatile CallCore core;
    private volatile String session = "", with = "", calling = "";
    private CameraGl camera;
    private final AudioEngine audio;
    private RemoteVideo remote;
    private final Set<String> answered = new HashSet<String>();
    private volatile Room.Offer ringing;
    private Ringtone ringtone;
    private volatile long callStartMs = 0, lastMediaMs = 0;
    private volatile List<Room.Member> roster = new ArrayList<Room.Member>();
    private volatile String lastWhy = "";
    private final OrientationEventListener orient;
    private int deviceQuarter = 0;

    public GameCalls(Context ctx, String name, String ram, String media, TextureView remoteTV, TextureView selfTV, Log log) {
        this.ctx = ctx; this.name = name; this.ramAddr = ram; this.mediaAddr = media; this.log = log;
        this.remoteTV = remoteTV; this.selfTV = selfTV;
        int c = media.lastIndexOf(':');
        mediaHost = media.substring(0, c); mediaPort = Integer.parseInt(media.substring(c + 1));
        audio = new AudioEngine(ctx);
        orient = new OrientationEventListener(ctx) {                   // the Activity's: which way the camera's picture is up
            public void onOrientationChanged(int o) {
                if (o == ORIENTATION_UNKNOWN) return;
                int d = Math.abs(o - deviceQuarter); d = Math.min(d, 360 - d);
                if (d < 60) return;
                deviceQuarter = ((o + 45) / 90 * 90) % 360;
                if (camera != null) camera.setDeviceOrientation(deviceQuarter);
            }
        };
        remoteTV.setSurfaceTextureListener(new TextureView.SurfaceTextureListener() {
            public void onSurfaceTextureAvailable(SurfaceTexture s, int w, int h) { if (remote != null) remote.setSurface(s); }
            public void onSurfaceTextureSizeChanged(SurfaceTexture s, int w, int h) { if (remote != null) remote.fit(); }
            public boolean onSurfaceTextureDestroyed(SurfaceTexture s) { if (remote != null) remote.dropSurface(s); else s.release(); return false; }
            public void onSurfaceTextureUpdated(SurfaceTexture s) { }
        });
        selfTV.setSurfaceTextureListener(new TextureView.SurfaceTextureListener() {
            public void onSurfaceTextureAvailable(SurfaceTexture s, int w, int h) { if (camera != null) camera.setSelfView(s, w, h); }
            public void onSurfaceTextureSizeChanged(SurfaceTexture s, int w, int h) { if (camera != null) camera.setSelfView(s, w, h); }
            public boolean onSurfaceTextureDestroyed(SurfaceTexture s) { if (camera != null) camera.dropSelfView(s); else s.release(); return false; }
            public void onSurfaceTextureUpdated(SurfaceTexture s) { }
        });
        say("[KART_CALLS_IN_THE_GAME_V1] the Communicator (" + Diag.BUILD + " code): memory " + ram + ", media " + media + ", as " + name);
        join();
        ui.post(tick);
    }

    private void say(String l) { Diag.log(l); if (log != null) log.say(l); }

    // ---- what the game shows
    public String roomState() { return roomState; }
    public boolean inRoom() { return room != null; }
    public String ringingFrom() { Room.Offer o = ringing; return o == null ? null : o.offeredBy; }
    /** who the call is with, "" when there is none (a call being set up counts) */
    public String inCallWith() { return session.isEmpty() ? "" : with; }
    public boolean connected() { return core != null; }
    public String calling() { return calling; }
    public String session() { return session; }
    public long callSeconds() { return callStartMs == 0 ? 0 : (System.currentTimeMillis() - callStartMs) / 1000; }
    public boolean muted() { return audio.muted(); }
    public boolean cameraOn() { return camera != null; }
    public String lastWhy() { return lastWhy; }
    /** the room's people other than this game, name -> status */
    public List<String[]> people() {
        List<String[]> out = new ArrayList<String[]>();
        for (Room.Member m : roster) if (!m.name.equals(name)) out.add(new String[]{m.name, m.status});
        return out;
    }
    /** media from the far end: video frames and audio packets received so far, and ms since the last */
    public String mediaLine() {
        CallCore c = core;
        if (c == null) return session.isEmpty() ? "" : "connecting to the media server " + mediaAddr;
        return "video " + c.stats.vRecv + " in / " + c.stats.vSent + " out, audio " + c.stats.aRecv + " in / " + c.stats.aSent + " out, mic " + audio.micState;
    }
    public long mediaIn() { CallCore c = core; return c == null ? 0 : c.stats.vRecv + c.stats.aRecv; }
    public long mediaOut() { CallCore c = core; return c == null ? 0 : c.stats.vSent + c.stats.aSent; }

    /** the others in this call (the room's CallsJoined rows that name its session) */
    public List<String> participants() {
        List<String> out = new ArrayList<String>();
        Room r = room; String s = session;
        if (r == null || s.isEmpty()) return out;
        for (Room.Member m : roster) if (!m.name.equals(name) && r.callsOf(m.name).contains(s)) out.add(m.name);
        if (out.isEmpty() && !with.isEmpty()) out.add(with);
        return out;
    }
    /** the call's chat, oldest first: "who: text" */
    public List<String> chat() {
        List<String> out = new ArrayList<String>();
        Room r = room; String s = session;
        if (r == null || s.isEmpty()) return out;
        for (Room.Line l : r.readChat(s)) out.add(l.fromName + ": " + l.text);
        return out;
    }
    public void sendChat(final String text) {
        final Room r = room; final String s = session;
        if (r == null || s.isEmpty() || text.trim().isEmpty()) return;
        ops.execute(new Runnable() { public void run() {
            try { r.sendChat(s, text.trim()); } catch (Exception e) { say("[calls] chat not sent: " + e.getMessage()); }
        } });
    }
    public boolean speaker() { return audio.speaker(); }
    public void toggleSpeaker() { ui.post(new Runnable() { public void run() { audio.setSpeaker(!audio.speaker()); } }); }

    // ---- the room: joined once, rejoined when its session is gone (the Activity's join and rejoinIfLost)
    private void join() {
        final String ram = ramAddr;
        roomState = "connecting to the Communicator at " + ram;
        ops.execute(new Runnable() { public void run() {
            int c = ram.lastIndexOf(':');
            while (!leaving && room == null) {
                try {
                    Room r = new Room(ram.substring(0, c), Integer.parseInt(ram.substring(c + 1)), name, "video,audio",
                            new Room.Logger() { public void log(String l) { Diag.log(l); } });
                    room = r;
                    roomState = "in the Communicator room at " + ram;
                    say("[room] " + roomState);
                } catch (Exception e) {
                    String s = "cannot reach the Communicator at " + ram + ": " + e.getMessage() + " (trying again)";
                    if (!s.equals(roomState)) say("[room] " + s);
                    roomState = s;
                    try { Thread.sleep(3000); } catch (InterruptedException ie) { return; }
                }
            }
        } });
    }

    private void rejoinIfLost() {
        final Room r = room;
        if (r == null || r.fault() == null || leaving) return;
        say("[room] lost the Communicator memory: " + r.fault() + " -- rejoining");
        room = null;
        ops.execute(new Runnable() { public void run() { r.close(); } });
        join();
    }

    private final Runnable tick = new Runnable() { public void run() {
        if (leaving) return;
        try { onTick(); } catch (RuntimeException e) { say("[calls] " + e); }
        ui.postDelayed(this, 500);
    } };

    private void onTick() {
        rejoinIfLost();
        if (camera != null) camera.setDisplayRotation(displayDeg());
        Room r = room;
        if (r == null) return;
        roster = r.roster();
        // [RINGS_ONLY_WHILE_OFFERED_V1] as the Communicator: an invitation rings only while at most 60 s old and offered
        if (session.isEmpty()) {
            List<Room.Offer> inv = r.invitations(), off = r.offers();
            Room.Offer o0 = ringing;
            if (o0 != null) {
                boolean still = false;
                for (Room.Offer o : inv) if (o.session.equals(o0.session) && offered(o, off)) still = true;
                if (!still) { say("[calls] " + o0.offeredBy + " is no longer calling"); answered.add(o0.session); ringing = null; stopRinging(); }
            }
            if (ringing == null)
                for (Room.Offer o : inv) {
                    if (answered.contains(o.session) || !offered(o, off)) continue;
                    ring(o);
                    break;
                }
        }
        CallCore c = core;
        if (c != null && c.stopped()) hangUp("the call's media stopped");
    }

    private static boolean offered(Room.Offer inv, List<Room.Offer> offers) {
        if (inv.ageS > 60) return false;
        for (Room.Offer x : offers) if (x.session.equals(inv.session)) return true;
        return false;
    }

    private void ring(Room.Offer o) {
        ringing = o;
        say("[calls] " + o.offeredBy + " is calling (session " + o.session + " at " + o.mediaHost + ":" + o.mediaPort + ")");
        try {
            ringtone = RingtoneManager.getRingtone(ctx, RingtoneManager.getDefaultUri(RingtoneManager.TYPE_RINGTONE));
            if (ringtone != null) ringtone.play();
        } catch (RuntimeException e) { say("[calls] ringtone: " + e); }
    }

    private void stopRinging() { if (ringtone != null) { try { ringtone.stop(); } catch (RuntimeException ignored) { } ringtone = null; } }

    // ---- the player's hands (from any thread: each runs on the UI thread)
    public void call(final String who) { ui.post(new Runnable() { public void run() { doCall(who); } }); }
    public void answer() { ui.post(new Runnable() { public void run() { doAnswer(); } }); }
    public void decline() { ui.post(new Runnable() { public void run() {
        Room.Offer o = ringing;
        if (o != null) { answered.add(o.session); say("[calls] declined " + o.offeredBy); }
        ringing = null; stopRinging();
    } }); }
    public void end() { ui.post(new Runnable() { public void run() { hangUp("hung up here"); } }); }
    public void toggleMute() { ui.post(new Runnable() { public void run() { audio.setMuted(!audio.muted()); } }); }
    public void toggleCamera() { ui.post(new Runnable() { public void run() {
        CallCore c = core;
        if (camera != null) { final CameraGl g = camera; camera = null; say("[camera] off"); ops.execute(new Runnable() { public void run() { g.stop(); } }); }
        else if (c != null && has(Manifest.permission.CAMERA)) startCamera(c);
    } }); }

    private void doCall(final String who) {
        final Room r = room;
        if (r == null || !session.isEmpty()) return;
        final String s = Room.newSession();
        calling = who;
        say("[calls] calling " + who + " (session " + s + " at " + mediaHost + ":" + mediaPort + ")");
        ops.execute(new Runnable() { public void run() {
            try { r.offerCall(s, mediaHost, mediaPort); r.invite(who, s, mediaHost, mediaPort); }
            catch (Exception e) { say("[calls] could not place the call: " + e.getMessage()); calling = ""; return; }
            ui.post(new Runnable() { public void run() { startCall(s, mediaHost, mediaPort, who, true); } });
        } });
    }

    private void doAnswer() {
        Room.Offer o = ringing;
        ringing = null; stopRinging();
        if (o == null) return;
        answered.add(o.session);
        say("[calls] answered " + o.offeredBy);
        startCall(o.session, o.mediaHost, o.mediaPort, o.offeredBy, false);
    }

    private int displayDeg() {
        android.view.Display d = ((WindowManager) ctx.getSystemService(Context.WINDOW_SERVICE)).getDefaultDisplay();
        int r = d != null ? d.getRotation() : Surface.ROTATION_0;
        return r == Surface.ROTATION_90 ? 90 : r == Surface.ROTATION_180 ? 180 : r == Surface.ROTATION_270 ? 270 : 0;
    }

    private boolean has(String p) { return ctx.checkSelfPermission(p) == PackageManager.PERMISSION_GRANTED; }

    /** the Activity's startCall, the call screen being the game's */
    private void startCall(final String s, final String host, final int port, final String who, boolean outgoing) {
        hangUp(null);
        calling = outgoing ? who : "";                // kartdemo's tile: "calling X" for the whole of a call this game placed
        final Room r = room;
        if (r == null) return;
        session = s; with = who;
        answered.add(s);
        remote = new RemoteVideo(remoteTV);
        remote.fill = true;
        if (remoteTV.isAvailable()) remote.setSurface(remoteTV.getSurfaceTexture());
        remote.start(who);
        ops.execute(new Runnable() { public void run() {
            final CallCore c;
            try {
                r.joinCall(s, host, port);
                c = new CallCore(host, port, name, s, FPS, new CallCore.Listener() {
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
                say("[calls] in call " + s + " with " + who + " at " + host + ":" + port);
                try { audio.start(c, has(Manifest.permission.RECORD_AUDIO)); }
                catch (RuntimeException e) { say("[audio] could not start: " + e); }
                if (has(Manifest.permission.CAMERA)) startCamera(c);
                else say("[camera] no CAMERA permission -- the call goes on receiving only");
            } });
        } });
    }

    private void startCamera(final CallCore c) {
        c.startVideo(1280, 720);
        camera = new CameraGl(ctx, FPS, new CameraGl.Host() {
            public int tick() { return c.tick(); }
            public boolean takeWantKey() { return c.takeWantKey(); }
            public void onPacket(byte[] p, boolean key, Ladder.Size sz) {
                try { c.sendEncoded(p, key, sz); } catch (Plane.Closed e) { Diag.log("[video] send: " + e.getMessage()); }
            }
        });
        camera.setDisplayRotation(displayDeg());
        camera.setDeviceOrientation(deviceQuarter);
        if (orient.canDetectOrientation()) orient.enable();
        try { camera.start(true); } catch (RuntimeException e) { say("[camera] could not start: " + e); camera = null; return; }
        if (selfTV.isAvailable()) camera.setSelfView(selfTV.getSurfaceTexture(), selfTV.getWidth(), selfTV.getHeight());
        camera.setSending(true);
    }

    /** Ends the call (not the room). The teardown runs on the worker thread, never on the UI's. */
    private void hangUp(String why) {
        final CallCore c = core;
        core = null;
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
                if (wasInCall) { try { audio.stop(); } catch (RuntimeException ignored) { } }
                if (c != null) c.stop();
                if (wasInCall && r != null) {
                    try { r.leaveCall(s); r.announce("online"); } catch (Exception e) { Diag.log("[room] after the call: " + e.getMessage()); }
                }
            } });
        }
        if (wasInCall) { say("[calls] call " + s + " with " + with + " ended" + (why != null ? ": " + why : "")); if (why != null) lastWhy = why; }
        session = ""; with = ""; calling = ""; callStartMs = 0;
    }

    /** The game left: the call, then the room. */
    public void stop() {
        ui.post(new Runnable() { public void run() {
            if (leaving) return;
            leaving = true;
            ui.removeCallbacks(tick);
            ringing = null; stopRinging();
            hangUp("the game left");
            final Room r = room; room = null;
            ops.execute(new Runnable() { public void run() { if (r != null) r.close(); } });
            ops.shutdown();
            say("[room] left the Communicator room");
        } });
    }
}
