// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
package net.frognet.kart;

import android.content.Context;
import android.graphics.Bitmap;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.Matrix;
import android.graphics.Paint;
import android.graphics.RectF;
import android.view.MotionEvent;
import android.view.SurfaceHolder;
import android.view.SurfaceView;

import net.frognet.communicator.GameCalls;
import net.frognet.kart.core.Game;
import net.frognet.kart.core.Json;
import net.frognet.kart.core.Kart;

import java.util.ArrayList;
import java.util.Collections;
import java.util.Comparator;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * The game on the phone: one thread runs the game (Game.tick) and draws it, 60 times a second. [KART_ANDROID_V1]
 * Landscape is drawn in kartdemo.py's layout (1180 x 720), portrait in 720 x 1180 (the track scaled across the top),
 * each scaled to the screen [KART_ROTATES_V1]; the track and every kart's shared x/y are the same numbers on every game. Touch: lobby buttons; racing, steer / gas / brake (multi-touch) and Auto.
 */
public final class GameView extends SurfaceView implements SurfaceHolder.Callback, Runnable {
    public interface Host { void chatDialog(boolean toTheCall); void leave();
        /** [KART_CALLS_IN_THE_GAME_V1] where the far end's picture and this camera's go on the screen; null: hidden */
        void placeVideo(RectF remote, RectF self);
        /** a VPN (the FrogNet app's tunnel) is up on this phone: the 10.x race memories are reached only through it */
        boolean vpnUp();
        /** what the game did about the tunnel (started the FrogNet membership, or could not): null when nothing to say */
        String tunnel(); }
    static final float TW = (float) Kart.TRACK_W, TH = (float) Kart.H;   // the track, in its own (shared) units

    private final Game game;
    /** [KART_HOME_V1] which screen: home, comms (the Communicator) or game (the lobby; racing is the game too) */
    public volatile String screen = "home";
    private final Host host;
    private final GameCalls calls;
    private RectF placedRemote = null, placedSelf = null;
    private final List<Object[]> callButtons = new ArrayList<Object[]>();     // {RectF, name}: the lobby's Call buttons, as drawn
    private volatile boolean running = true, haveSurface = false;
    private Thread loop;
    private final Matrix toScreen = new Matrix(), toLayout = new Matrix();
    private final Matrix trackM = new Matrix();                 // track units -> layout units (portrait: the track scaled to the width)
    private Bitmap trackBmp;
    private final Paint p = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Set<String> held = new HashSet<String>();     // racing buttons under a finger now
    private volatile boolean showLog = false;
    private double fps = 0;

    // [KART_ROTATES_V1] the layout: landscape 1180 x 720 (kartdemo.py's), portrait 720 x 1180; chosen by the surface's shape
    public volatile boolean portrait = false;
    float W = 1180, H = 720;
    RectF B_READY, B_START, B_MINUS, B_PLUS, B_CHAT, B_COMMS, B_LOG, B_HOME, LAPS_AT;
    RectF H_COMMS, H_GAME, H_SETTINGS;
    RectF K_GAME, K_HOME, K_MUTE, K_CAM, K_SPK, K_CHAT, K_RACE, K_END, V_COMMS, PEOPLE;
    RectF R_LEFT, R_RIGHT, R_AUTO, R_GAS, R_BRAKE, R_LEAVE;
    RectF RING, RING_ANSWER, RING_DECLINE, ASK, ASK_YES, ASK_NO;
    RectF C_MUTE_L, C_CAM_L, C_END_L, C_MUTE_R, C_CAM_R, C_END_R, V_LOBBY;
    /** the call's picture in a race: inside the infield, in track units (drawn through trackM) */
    final RectF V_RACE = new RectF(240, 253, 620, 467);
    private static RectF r(float l, float t, float rr, float b) { return new RectF(l, t, rr, b); }

    private synchronized void relayout(boolean port) {
        portrait = port;
        trackM.reset();
        if (!port) {
            W = 1180; H = 720;
            B_READY = r(40, 632, 200, 702); B_START = r(210, 632, 370, 702); B_MINUS = r(380, 632, 450, 702); B_PLUS = r(560, 632, 630, 702);
            LAPS_AT = r(458, 652, 550, 680);
            B_CHAT = r(640, 632, 750, 702); B_COMMS = r(760, 632, 900, 702); B_LOG = r(910, 632, 1010, 702); B_HOME = r(1020, 632, 1160, 702);
            H_COMMS = r(80, 300, 570, 600); H_GAME = r(610, 300, 1100, 600); H_SETTINGS = r(960, 30, 1150, 84);
            K_GAME = r(820, 22, 990, 72); K_HOME = r(1000, 22, 1160, 72);
            K_MUTE = r(450, 500, 560, 552); K_CAM = r(570, 500, 690, 552); K_SPK = r(700, 500, 840, 552);
            K_CHAT = r(850, 500, 940, 552); K_RACE = r(950, 500, 1060, 552); K_END = r(1070, 500, 1160, 552);
            V_COMMS = r(450, 96, 1160, 490); PEOPLE = r(24, 100, 430, 700);
            R_LEFT = r(16, 540, 146, 704); R_RIGHT = r(160, 540, 290, 704);
            R_AUTO = r(880, 150, 1160, 230); R_GAS = r(880, 410, 1160, 560); R_BRAKE = r(880, 576, 1160, 704); R_LEAVE = r(880, 60, 1160, 130);
            RING = r(250, 40, 870, 124); RING_ANSWER = r(600, 52, 726, 112); RING_DECLINE = r(734, 52, 860, 112);
            ASK = r(250, 132, 870, 216); ASK_YES = r(640, 144, 746, 204); ASK_NO = r(754, 144, 860, 204);
            C_MUTE_L = r(680, 286, 790, 330); C_CAM_L = r(800, 286, 910, 330); C_END_L = r(1010, 286, 1160, 330);
            C_MUTE_R = r(884, 290, 966, 350); C_CAM_R = r(972, 290, 1054, 350); C_END_R = r(1060, 290, 1160, 350);
            V_LOBBY = r(680, 340, 1160, 610);
        } else {
            W = 720; H = 1180;
            float ts = W / TW; trackM.setScale(ts, ts);          // the track across the top, 720 x 603
            B_READY = r(20, 1000, 190, 1070); B_START = r(200, 1000, 370, 1070); B_MINUS = r(390, 1000, 460, 1070); B_PLUS = r(630, 1000, 700, 1070);
            LAPS_AT = r(468, 1020, 620, 1050);
            B_CHAT = r(20, 1090, 180, 1160); B_COMMS = r(190, 1090, 370, 1160); B_LOG = r(380, 1090, 530, 1160); B_HOME = r(540, 1090, 700, 1160);
            H_COMMS = r(40, 400, 680, 670); H_GAME = r(40, 700, 680, 970); H_SETTINGS = r(500, 30, 690, 84);
            K_GAME = r(330, 22, 530, 72); K_HOME = r(540, 22, 700, 72);
            K_MUTE = r(20, 545, 180, 600); K_CAM = r(190, 545, 350, 600); K_SPK = r(360, 545, 530, 600); K_CHAT = r(540, 545, 700, 600);
            K_RACE = r(20, 610, 350, 665); K_END = r(370, 610, 700, 665);
            V_COMMS = r(20, 150, 700, 533); PEOPLE = r(20, 850, 700, 1170);
            R_LEAVE = r(20, 700, 340, 770); R_AUTO = r(380, 700, 700, 770);
            R_LEFT = r(20, 900, 200, 1160); R_RIGHT = r(210, 900, 390, 1160); R_GAS = r(420, 900, 700, 1040); R_BRAKE = r(420, 1050, 700, 1160);
            RING = r(20, 40, 700, 124); RING_ANSWER = r(400, 52, 540, 112); RING_DECLINE = r(550, 52, 690, 112);
            ASK = r(20, 132, 700, 216); ASK_YES = r(420, 144, 550, 204); ASK_NO = r(560, 144, 690, 204);
            C_MUTE_L = r(40, 640, 190, 686); C_CAM_L = r(200, 640, 350, 686); C_END_L = r(530, 640, 680, 686);
            C_MUTE_R = r(20, 790, 240, 850); C_CAM_R = r(250, 790, 470, 850); C_END_R = r(480, 790, 700, 850);
            V_LOBBY = r(100, 694, 620, 986);
        }
        placedRemote = placedSelf = null;                    // placed again for this shape
    }

    public GameView(Context c, Game game, GameCalls calls, Host host) {
        super(c);
        this.game = game; this.host = host; this.calls = calls;
        relayout(false);
        getHolder().addCallback(this);
        getHolder().setFormat(android.graphics.PixelFormat.RGBA_8888);       // not the SurfaceView default, RGB_565
        setFocusable(true);
        loop = new Thread(this, "kart-loop");
        loop.start();
    }

    public void toggleLog() { showLog = !showLog; }

    /** ends the loop; the Activity stops the game (its sessions) */
    public void end() { running = false; try { loop.join(1000); } catch (InterruptedException ignored) { } }

    @Override public void surfaceCreated(SurfaceHolder h) { haveSurface = true; }
    @Override public void surfaceChanged(SurfaceHolder h, int f, int w, int hh) {
        relayout(hh > w);
        float s = Math.min(w / W, hh / H);
        toScreen.reset(); toScreen.postScale(s, s); toScreen.postTranslate((w - W * s) / 2, (hh - H * s) / 2);
        toScreen.invert(toLayout);
    }
    @Override public void surfaceDestroyed(SurfaceHolder h) { haveSurface = false; }

    // ---- the loop: the game runs whether or not the screen is drawn
    @Override public void run() {
        long last = System.nanoTime(), fpsT = last; int frames = 0;
        while (running) {
            long t = System.nanoTime();
            double dt = Math.min(0.1, (t - last) / 1e9); last = t;
            Set<String> h; synchronized (held) { h = new HashSet<String>(held); }
            double steer = (h.contains("right") ? 1 : 0) - (h.contains("left") ? 1 : 0);
            double gas = (h.contains("gas") ? 1 : 0) - (h.contains("brake") ? 1 : 0);
            if (calls != null) {
                String w = calls.inCallWith(), cl = calls.calling();
                game.setCall(w.isEmpty() ? "" : (!cl.isEmpty() ? "calling " + cl : "in a call"), w.isEmpty() ? "" : cl, w.isEmpty() || cl.isEmpty() ? "" : calls.session());
            }
            game.tick(System.currentTimeMillis() / 1000.0, dt, steer, gas);
            if (game.takePartyFrom() != null) screen = "game";          // [KART_PARTY_V1] asked into a race from a call
            synchronized (this) { placeVideo(); }
            if (haveSurface) {
                Canvas c = null;
                try {
                    c = getHolder().lockHardwareCanvas();                  // the GPU draws it (and no per-frame "Locking canvas" log)
                    if (c != null) synchronized (this) { c.drawColor(Color.BLACK); c.save(); c.concat(toScreen); drawFrame(c); c.restore(); }
                } finally { if (c != null) getHolder().unlockCanvasAndPost(c); }
            }
            frames++;
            if (t - fpsT >= 1_000_000_000L) { fps = frames / ((t - fpsT) / 1e9); frames = 0; fpsT = t; }
            long spent = (System.nanoTime() - t) / 1_000_000L;
            if (spent < 16) try { Thread.sleep(16 - spent); } catch (InterruptedException e) { return; }
        }
    }

    // ---- touch
    private float[] layout(MotionEvent e, int i) { float[] q = {e.getX(i), e.getY(i)}; toLayout.mapPoints(q); return q; }

    /** the ring banner and the call's buttons come first, on either screen */
    private boolean callTouch(float[] q) {
        if (game.asked() != null && ASK.contains(q[0], q[1])) {                 // [KART_RACE_INVITE_V1] an invitation's answer
            if (ASK_YES.contains(q[0], q[1])) game.answer(true);
            else if (ASK_NO.contains(q[0], q[1])) game.answer(false);
            return true;
        }
        if (calls == null) return false;
        boolean racing = game.state.equals("racing");
        if (calls.ringingFrom() != null && RING.contains(q[0], q[1])) {
            if (RING_ANSWER.contains(q[0], q[1])) calls.answer();
            else if (RING_DECLINE.contains(q[0], q[1])) calls.decline();
            return true;
        }
        boolean comms = !racing && screen.equals("comms");
        if (!calls.inCallWith().isEmpty() && comms) {
            if (K_MUTE.contains(q[0], q[1])) { calls.toggleMute(); return true; }
            if (K_CAM.contains(q[0], q[1])) { calls.toggleCamera(); return true; }
            if (K_SPK.contains(q[0], q[1])) { calls.toggleSpeaker(); return true; }
            if (K_CHAT.contains(q[0], q[1])) { host.chatDialog(true); return true; }
            if (K_RACE.contains(q[0], q[1])) { raceThem(); return true; }
            if (K_END.contains(q[0], q[1])) { calls.end(); return true; }
        } else if (!calls.inCallWith().isEmpty() && (racing || screen.equals("game"))) {
            if ((racing ? C_MUTE_R : C_MUTE_L).contains(q[0], q[1])) { calls.toggleMute(); return true; }
            if ((racing ? C_CAM_R : C_CAM_L).contains(q[0], q[1])) { calls.toggleCamera(); return true; }
            if ((racing ? C_END_R : C_END_L).contains(q[0], q[1])) { calls.end(); return true; }
        } else if (!racing) {
            synchronized (callButtons) {
                for (Object[] b : callButtons) if (((RectF) b[0]).contains(q[0], q[1])) { calls.call((String) b[1]); return true; }
            }
        }
        return false;
    }

    /** [KART_RACE_INVITE_V1] from a call: invite the call's people who run FrogNet Kart (a tile in the kart memory) to a
     *  race; the call's chat says so to everyone in it. Each answers in the kart memory; all yes, each game launches itself. */
    private void raceThem() {
        List<String> ps = calls.participants(), karts = new ArrayList<String>(), others = new ArrayList<String>();
        Map<String, Map<String, Object>> tiles = game.tiles();
        for (String n : ps) (tiles.containsKey(n) ? karts : others).add(n);
        calls.sendChat(game.name + " invites " + (karts.isEmpty() ? "nobody (no one in this call runs FrogNet Kart)" : karts) + " to a kart race"
                + (others.isEmpty() ? "" : "; " + others + " not running FrogNet Kart"));
        game.invite(karts);
    }

    @Override public synchronized boolean onTouchEvent(MotionEvent e) {
        int a = e.getActionMasked();
        if (a == MotionEvent.ACTION_DOWN || a == MotionEvent.ACTION_POINTER_DOWN) {
            if (callTouch(layout(e, e.getActionIndex()))) return true;
        }
        if (game.state.equals("racing")) {
            Set<String> now = new HashSet<String>();
            for (int i = 0; i < e.getPointerCount(); i++) {
                boolean up = (a == MotionEvent.ACTION_UP || a == MotionEvent.ACTION_CANCEL) || (a == MotionEvent.ACTION_POINTER_UP && i == e.getActionIndex());
                if (up) continue;
                float[] q = layout(e, i);
                if (R_LEFT.contains(q[0], q[1])) now.add("left");
                if (R_RIGHT.contains(q[0], q[1])) now.add("right");
                if (R_GAS.contains(q[0], q[1])) now.add("gas");
                if (R_BRAKE.contains(q[0], q[1])) now.add("brake");
            }
            synchronized (held) { held.clear(); held.addAll(now); }
            if (a == MotionEvent.ACTION_DOWN || a == MotionEvent.ACTION_POINTER_DOWN) {
                float[] q = layout(e, e.getActionIndex());
                if (R_AUTO.contains(q[0], q[1])) game.autodrive = !game.autodrive;
                if (R_LEAVE.contains(q[0], q[1])) game.leaveRace();
            }
            return true;
        }
        if (!screen.equals("game")) {
            synchronized (held) { held.clear(); }
            if (a != MotionEvent.ACTION_DOWN) return true;
            float[] q = layout(e, 0);
            if (screen.equals("home")) {
                if (H_COMMS.contains(q[0], q[1])) { game.play(false); screen = "comms"; }
                else if (H_GAME.contains(q[0], q[1])) { game.play(true); screen = "game"; }
                else if (H_SETTINGS.contains(q[0], q[1])) host.leave();
            } else {
                if (K_GAME.contains(q[0], q[1])) { game.play(true); screen = "game"; }
                else if (K_HOME.contains(q[0], q[1])) { game.play(false); screen = "home"; }
            }
            return true;
        }
        synchronized (held) { held.clear(); }
        if (a == MotionEvent.ACTION_DOWN) {
            float[] q = layout(e, 0);
            if (B_READY.contains(q[0], q[1])) game.setReady(!game.ready());
            else if (B_START.contains(q[0], q[1])) game.pressStart();
            else if (B_MINUS.contains(q[0], q[1])) game.lapsDown();
            else if (B_PLUS.contains(q[0], q[1])) game.lapsUp();
            else if (B_CHAT.contains(q[0], q[1])) host.chatDialog(false);
            else if (B_COMMS.contains(q[0], q[1])) screen = "comms";             // still in the game: its lobby tuple stays
            else if (B_LOG.contains(q[0], q[1])) toggleLog();
            else if (B_HOME.contains(q[0], q[1])) { game.play(false); screen = "home"; }
        }
        return true;
    }

    // ---- drawing
    private void text(Canvas c, String s, float x, float y, float size, int color, boolean bold) {
        p.setStyle(Paint.Style.FILL); p.setColor(color); p.setTextSize(size); p.setFakeBoldText(bold);
        c.drawText(s, x, y + size, p);
    }
    /** text cut to a width (an ellipsis where it is cut) */
    private void fit(Canvas c, String s, float x, float y, float size, int color, boolean bold, float maxW) {
        p.setTextSize(size); p.setFakeBoldText(bold);
        if (p.measureText(s) > maxW) { int n = p.breakText(s, true, maxW - p.measureText("…"), null); s = s.substring(0, Math.max(0, n)) + "…"; }
        text(c, s, x, y, size, color, bold);
    }
    private void button(Canvas c, RectF r, String label, boolean lit, boolean on) { button(c, r, label, lit, on, 26); }
    private void button(Canvas c, RectF r, String label, boolean lit, boolean on, float size) {
        p.setStyle(Paint.Style.FILL);
        p.setColor(lit ? Color.rgb(36, 140, 72) : on ? Color.rgb(60, 100, 160) : Color.rgb(70, 70, 75));
        c.drawRoundRect(r, 12, 12, p);
        p.setColor(Color.WHITE); p.setTextSize(size); p.setFakeBoldText(true);
        float tw = p.measureText(label);
        c.drawText(label, r.centerX() - tw / 2, r.centerY() + 9, p);
    }
    private static int rgb(int[] c) { return Color.rgb(c[0], c[1], c[2]); }

    private void drawFrame(Canvas c) {
        if (game.state.equals("racing")) drawRace(c);
        else if (screen.equals("home")) drawHome(c);
        else if (screen.equals("comms")) drawComms(c);
        else drawLobby(c);
        String trouble = game.memoryState();
        if (trouble == null && (trouble = game.raceState()) != null) {
            Map<String, Object> srv = game.myServer();
            String addr = srv == null ? "" : Json.str(srv.get("addr"));
            if (addr.startsWith("10.") && !host.vpnUp()) {                  // [KART_SAYS_NO_TUNNEL_V1]
                String t = host.tunnel();
                trouble = "no FrogNet tunnel yet (the race is on " + addr + ")" + (t == null ? "" : ": " + t);
            }
        }
        if (trouble != null) {
            p.setStyle(Paint.Style.FILL); p.setColor(Color.argb(220, 120, 20, 20)); c.drawRect(0, 0, W, 34, p);
            fit(c, trouble, 12, 6, 20, Color.WHITE, true, W - 24);
        }
        drawCall(c);
        if (showLog) drawLog(c);
    }

    // ---- the call [KART_CALLS_IN_THE_GAME_V1]
    private void placeVideo() {
        RectF r = null, sf = null;
        boolean racing = game.state.equals("racing");
        if (calls != null && !calls.inCallWith().isEmpty() && (racing || !screen.equals("home"))) {
            RectF v = racing ? V_RACE : screen.equals("comms") ? V_COMMS : V_LOBBY;
            if (racing) { RectF t = new RectF(); trackM.mapRect(t, v); v = t; }
            r = new RectF(); toScreen.mapRect(r, v);
            float w = v.width() * 0.3f, h = w * 9 / 16;
            sf = new RectF(); toScreen.mapRect(sf, new RectF(v.right - w - 6, v.bottom - h - 6, v.right - 6, v.bottom - 6));
            if (!calls.cameraOn()) sf = null;
        }
        if (eq(r, placedRemote) && eq(sf, placedSelf)) return;
        placedRemote = r; placedSelf = sf;
        host.placeVideo(r, sf);
    }
    private static boolean eq(RectF a, RectF b) { return a == null ? b == null : a.equals(b); }

    private void drawCall(Canvas c) {
        if (calls == null) return;
        boolean racing = game.state.equals("racing");
        String w = calls.inCallWith();
        boolean lobbyCall = racing || screen.equals("game");                 // the comms screen draws its own; home has none
        if (!w.isEmpty() && lobbyCall) {
            RectF v = racing ? V_RACE : V_LOBBY;
            if (racing) { RectF t = new RectF(); trackM.mapRect(t, v); v = t; }
            p.setStyle(Paint.Style.FILL); p.setColor(Color.rgb(10, 10, 12)); c.drawRect(v, p);       // under the picture
            if (!calls.connected()) text(c, "connecting…", v.left + 12, v.top + 10, 20, Color.rgb(200, 200, 200), false);
            long t = calls.callSeconds();
            String head = (calls.calling().isEmpty() ? "call with " : "calling ") + w + (calls.connected() ? String.format(java.util.Locale.ROOT, "  %d:%02d", t / 60, t % 60) : "");
            if (racing) text(c, head, portrait ? 20 : 884, portrait ? 672 : 360, 17, Color.rgb(140, 230, 160), true);
            else text(c, head, portrait ? 40 : 680, portrait ? 610 : 258, 20, Color.rgb(140, 230, 160), true);
            button(c, racing ? C_MUTE_R : C_MUTE_L, calls.muted() ? "Unmute" : "Mute", calls.muted(), true);
            button(c, racing ? C_CAM_R : C_CAM_L, calls.cameraOn() ? "Cam off" : "Cam on", false, true);
            p.setStyle(Paint.Style.FILL); p.setColor(Color.rgb(190, 50, 50)); c.drawRoundRect(racing ? C_END_R : C_END_L, 12, 12, p);
            RectF e = racing ? C_END_R : C_END_L;
            p.setColor(Color.WHITE); p.setTextSize(24); p.setFakeBoldText(true);
            c.drawText("End", e.centerX() - p.measureText("End") / 2, e.centerY() + 9, p);
        }
        else if (racing) text(c, calls.inRoom() ? "Communicator: ready for calls" : "Communicator: not connected", portrait ? 20 : 884, portrait ? 672 : 300, 16,
                calls.inRoom() ? Color.rgb(110, 220, 130) : Color.rgb(240, 90, 80), false);
        Game.Invite iv = game.asked();
        if (iv != null) {
            p.setStyle(Paint.Style.FILL); p.setColor(Color.argb(240, 24, 44, 90)); c.drawRoundRect(ASK, 16, 16, p);
            List<String> with = new ArrayList<String>(iv.members); with.remove(game.name);
            fit(c, iv.from + " invites you to a race", ASK.left + 18, ASK.top + 12, portrait ? 21 : 24, Color.WHITE, true, ASK_YES.left - ASK.left - 26);
            fit(c, with.isEmpty() ? "" : "with " + String.join(", ", with), ASK.left + 18, ASK.top + 46, 18, Color.rgb(190, 205, 235), false, ASK_YES.left - ASK.left - 26);
            p.setColor(Color.rgb(36, 160, 72)); c.drawRoundRect(ASK_YES, 12, 12, p);
            p.setColor(Color.rgb(190, 50, 50)); c.drawRoundRect(ASK_NO, 12, 12, p);
            p.setColor(Color.WHITE); p.setTextSize(24); p.setFakeBoldText(true);
            c.drawText("Yes", ASK_YES.centerX() - p.measureText("Yes") / 2, ASK_YES.centerY() + 9, p);
            c.drawText("No", ASK_NO.centerX() - p.measureText("No") / 2, ASK_NO.centerY() + 9, p);
        }
        String from = calls.ringingFrom();
        if (from != null) {
            p.setStyle(Paint.Style.FILL); p.setColor(Color.argb(235, 20, 60, 30)); c.drawRoundRect(RING, 16, 16, p);
            fit(c, from + " is calling", RING.left + 18, RING.top + 26, portrait ? 24 : 28, Color.WHITE, true, RING_ANSWER.left - RING.left - 26);
            p.setColor(Color.rgb(36, 160, 72)); c.drawRoundRect(RING_ANSWER, 12, 12, p);
            p.setColor(Color.rgb(190, 50, 50)); c.drawRoundRect(RING_DECLINE, 12, 12, p);
            p.setColor(Color.WHITE); p.setTextSize(24); p.setFakeBoldText(true);
            c.drawText("Answer", RING_ANSWER.centerX() - p.measureText("Answer") / 2, RING_ANSWER.centerY() + 9, p);
            c.drawText("Decline", RING_DECLINE.centerX() - p.measureText("Decline") / 2, RING_DECLINE.centerY() + 9, p);
        }
    }

    // ---- [KART_HOME_V1] the home screen: the kart memory and the Communicator connected; choose
    static final int BG = Color.rgb(16, 20, 28), CARD = Color.rgb(30, 36, 48), FROG = Color.rgb(72, 190, 96), INK = Color.rgb(170, 178, 192);
    private void card(Canvas c, RectF r, int color) { p.setStyle(Paint.Style.FILL); p.setColor(color); c.drawRoundRect(r, 24, 24, p); }
    private void dot(Canvas c, float x, float y, float rr, int color) { p.setStyle(Paint.Style.FILL); p.setColor(color); c.drawCircle(x, y, rr, p); }
    private void avatar(Canvas c, float x, float y, String n) {
        int[] col = Game.color(n);
        if (col[0] == 200 && col[1] == 200 && col[2] == 200) { int h = Math.abs(n.hashCode()); col = new int[]{90 + h % 140, 90 + (h / 140) % 140, 90 + (h / 19600) % 140}; }
        dot(c, x, y, 20, rgb(col));
        text(c, n.isEmpty() ? "?" : n.substring(0, 1).toUpperCase(java.util.Locale.ROOT), x - 8, y - 14, 22, Color.WHITE, true);
    }
    /** everyone this game knows of: the kart games' tiles (kart memory) and the Communicator's room, name -> what they are doing */
    private java.util.TreeMap<String, String> everyone() {
        java.util.TreeMap<String, String> who = new java.util.TreeMap<String, String>();
        for (Map.Entry<String, Map<String, Object>> e : game.tiles().entrySet()) if (!e.getKey().equals(game.name)) who.put(e.getKey(), Json.str(e.getValue().get("status")));
        if (calls != null) for (String[] m : calls.people()) if (!who.containsKey(m[0])) who.put(m[0], m[1] + " · Communicator");
        return who;
    }

    private void drawHome(Canvas c) {
        c.drawColor(BG);
        p.setShader(new android.graphics.LinearGradient(0, 0, W, 0, Color.rgb(40, 120, 70), Color.rgb(30, 70, 140), android.graphics.Shader.TileMode.CLAMP));
        p.setStyle(Paint.Style.FILL); c.drawRect(0, 0, W, 110, p); p.setShader(null);
        text(c, "FrogNet", portrait ? 30 : 60, 22, 52, Color.WHITE, true);
        if (portrait) fit(c, game.name + " · " + Game.BUILD, 30, 84, 16, Color.rgb(220, 235, 225), false, 440);
        else text(c, game.name + " · " + Game.BUILD, 300, 46, 20, Color.rgb(220, 235, 225), false);
        button(c, H_SETTINGS, "Settings", false, true, 22);
        // the memories this game holds
        float x0 = portrait ? 30 : 60, tw = W - x0 - 40;
        boolean km = game.memoryState() == null, cm = calls != null && calls.inRoom();
        dot(c, x0 + 10, 146, 8, km ? FROG : Color.rgb(230, 80, 70));
        fit(c, "kart memory " + game.kartHost + ":" + game.kartPort + (km ? "  ·  " + game.lobby().size() + " in the lobby" : "  ·  " + game.memoryState()), x0 + 28, 132, 20, km ? Color.WHITE : Color.rgb(240, 150, 140), false, tw);
        dot(c, x0 + 10, 178, 8, cm ? FROG : Color.rgb(230, 80, 70));
        fit(c, calls == null ? "no Communicator" : calls.roomState() + (cm ? "  ·  " + calls.people().size() + " other(s) in the room" : ""), x0 + 28, 164, 20, cm ? Color.WHITE : Color.rgb(240, 150, 140), false, tw);
        // who is here
        java.util.TreeMap<String, String> who = everyone();
        text(c, who.isEmpty() ? "nobody else is here yet" : "here now:", x0, 206, 20, INK, false);
        float x = portrait ? x0 : 160, y = 250, step = portrait ? 225 : 230;
        for (Map.Entry<String, String> e : who.entrySet()) {
            if (x + step > W + 40) { if (!portrait || y > 330) break; x = x0; y += 64; }
            avatar(c, x + 20, y, e.getKey());
            fit(c, e.getKey(), x + 48, y - 20, 18, Color.WHITE, true, step - 60);
            fit(c, e.getValue(), x + 48, y + 2, 14, INK, false, step - 60);
            x += step;
        }
        // the two choices
        card(c, H_COMMS, CARD); card(c, H_GAME, CARD);
        p.setStyle(Paint.Style.FILL); p.setColor(Color.rgb(66, 122, 222)); c.drawRoundRect(new RectF(H_COMMS.left, H_COMMS.top, H_COMMS.right, H_COMMS.top + 10), 5, 5, p);
        p.setColor(FROG); c.drawRoundRect(new RectF(H_GAME.left, H_GAME.top, H_GAME.right, H_GAME.top + 10), 5, 5, p);
        text(c, "Communicator", H_COMMS.left + 36, H_COMMS.top + 46, 40, Color.WHITE, true);
        text(c, "Calls with video and audio, on the", H_COMMS.left + 36, H_COMMS.top + 116, 21, INK, false);
        text(c, "Communicator's own memory", H_COMMS.left + 36, H_COMMS.top + 146, 21, INK, false);
        text(c, "From a call: race the people in it", H_COMMS.left + 36, H_COMMS.top + 196, 21, Color.rgb(150, 190, 250), false);
        text(c, "Kart racing", H_GAME.left + 36, H_GAME.top + 46, 40, Color.WHITE, true);
        text(c, "The lobby, then each race on its own", H_GAME.left + 36, H_GAME.top + 116, 21, INK, false);
        text(c, "game server memory", H_GAME.left + 36, H_GAME.top + 146, 21, INK, false);
        text(c, "Calls ring here too, racing or not", H_GAME.left + 36, H_GAME.top + 196, 21, Color.rgb(150, 230, 170), false);
        if (calls != null && !calls.inCallWith().isEmpty())
            fit(c, "in a call with " + calls.inCallWith() + " — open the Communicator to see it", x0, portrait ? 1010 : 630, 22, Color.rgb(140, 230, 160), true, W - 2 * x0);
    }

    // ---- [KART_HOME_V1] the Communicator's screen (its room, its calls) -- the game is a button away and keeps its state
    private void drawComms(Canvas c) {
        c.drawColor(BG);
        p.setStyle(Paint.Style.FILL); p.setColor(Color.rgb(24, 40, 72)); c.drawRect(0, 0, W, 86, p);
        text(c, "Communicator", portrait ? 20 : 40, portrait ? 20 : 16, portrait ? 30 : 36, Color.WHITE, true);
        boolean in = calls != null && calls.inRoom();
        float sx = portrait ? 20 : 330, sy = portrait ? 92 : 24;
        dot(c, sx + 6, sy + 12, 7, in ? FROG : Color.rgb(230, 80, 70));
        fit(c, (calls == null ? "" : calls.roomState()) + (portrait ? "  ·  as " + game.name : ""), sx + 20, sy, 16, in ? Color.rgb(200, 230, 210) : Color.rgb(240, 150, 140), false, portrait ? W - 60 : 460);
        if (!portrait) text(c, "as " + game.name, 344, 48, 16, INK, false);
        button(c, K_GAME, game.playing ? "Back to game" : "Kart racing", false, true, 20);
        button(c, K_HOME, "Home", false, true, 22);
        // people
        card(c, PEOPLE, CARD);
        text(c, "People", PEOPLE.left + 20, PEOPLE.top + 12, 24, Color.rgb(200, 220, 255), true);
        List<Object[]> btns = new ArrayList<Object[]>();
        boolean free = in && calls.inCallWith().isEmpty();
        float y = PEOPLE.top + 60;
        java.util.TreeMap<String, String> who = everyone();
        for (Map.Entry<String, String> e : who.entrySet()) {
            if (y > PEOPLE.bottom - 50) break;
            avatar(c, PEOPLE.left + 40, y + 20, e.getKey());
            fit(c, e.getKey(), PEOPLE.left + 72, y, 20, Color.WHITE, true, PEOPLE.width() - 190);
            fit(c, e.getValue(), PEOPLE.left + 72, y + 24, 15, INK, false, PEOPLE.width() - 190);
            RectF b = new RectF(PEOPLE.right - 100, y + 2, PEOPLE.right - 16, y + 40);
            button(c, b, "Call", false, free, 20);
            if (free) btns.add(new Object[]{b, e.getKey()});
            y += 62;
        }
        if (who.isEmpty()) text(c, "nobody else yet", PEOPLE.left + 20, PEOPLE.top + 60, 18, INK, false);
        synchronized (callButtons) { callButtons.clear(); callButtons.addAll(btns); }
        // the call
        String w = calls == null ? "" : calls.inCallWith();
        float vx = V_COMMS.left;
        if (w.isEmpty()) {
            card(c, V_COMMS, CARD);
            text(c, "No call", vx + 40, V_COMMS.top + 40, 34, Color.WHITE, true);
            fit(c, "Tap Call beside someone. A call to " + game.name + " rings", vx + 40, V_COMMS.top + 100, 20, INK, false, V_COMMS.width() - 60);
            fit(c, "here, on the home screen, in the lobby and in a race.", vx + 40, V_COMMS.top + 128, 20, INK, false, V_COMMS.width() - 60);
            if (calls != null && !calls.lastWhy().isEmpty()) fit(c, "last call: " + calls.lastWhy(), vx + 40, V_COMMS.top + 180, 16, Color.rgb(150, 150, 160), false, V_COMMS.width() - 60);
            if (!game.inviteState.isEmpty()) fit(c, game.inviteState, vx + 40, V_COMMS.top + 210, 15, Color.rgb(240, 210, 120), false, V_COMMS.width() - 60);
            return;
        }
        p.setStyle(Paint.Style.FILL); p.setColor(Color.rgb(8, 8, 10)); c.drawRect(V_COMMS, p);
        if (!calls.connected()) fit(c, "connecting to " + calls.mediaAddr + " …", vx + 20, V_COMMS.top + 20, 20, INK, false, V_COMMS.width() - 40);
        long t = calls.callSeconds();
        List<String> ps = calls.participants();
        fit(c, (calls.calling().isEmpty() ? "with " : "calling ") + (ps.isEmpty() ? w : String.join(", ", ps))
                + (calls.connected() ? String.format(java.util.Locale.ROOT, "   %d:%02d", t / 60, t % 60) : ""), vx, V_COMMS.top - 36, 18, Color.rgb(140, 230, 160), true, V_COMMS.width());
        if (!game.inviteState.isEmpty()) fit(c, game.inviteState, vx, V_COMMS.top - 18, 15, Color.rgb(240, 210, 120), false, V_COMMS.width());
        button(c, K_MUTE, calls.muted() ? "Unmute" : "Mute", calls.muted(), true, 20);
        button(c, K_CAM, calls.cameraOn() ? "Cam off" : "Cam on", false, true, 20);
        button(c, K_SPK, calls.speaker() ? "Earpiece" : "Speaker", false, true, 20);
        button(c, K_CHAT, "Chat", false, true, 20);
        button(c, K_RACE, "Race!", true, true, 20);
        p.setColor(Color.rgb(190, 50, 50)); c.drawRoundRect(K_END, 12, 12, p);
        p.setColor(Color.WHITE); p.setTextSize(20); p.setFakeBoldText(true); c.drawText("End", K_END.centerX() - p.measureText("End") / 2, K_END.centerY() + 7, p);
        List<String> ch = calls.chat();
        float cy = (portrait ? K_END.bottom : K_END.bottom) + 14, cw = portrait ? W - 40 : W - vx - 20;
        for (String l : ch.subList(Math.max(0, ch.size() - 5), ch.size())) { fit(c, l, vx, cy, 18, Color.WHITE, false, cw); cy += 26; }
        fit(c, calls.mediaLine(), vx, portrait ? PEOPLE.top - 22 : 698, 13, Color.rgb(120, 126, 140), false, cw);
    }

    /** the lobby's Communicator section: the room's state and everyone to call, from the room and the kart games' tiles */
    private float drawPeople(Canvas c, float y, float maxY) {
        List<Object[]> btns = new ArrayList<Object[]>();
        float right = portrait ? W - 40 : 620;
        text(c, "Communicator", 40, y, 24, Color.rgb(200, 220, 255), true); y += 30;
        fit(c, calls.roomState(), 40, y, 16, calls.inRoom() ? Color.rgb(110, 220, 130) : Color.rgb(240, 90, 80), false, right - 40); y += 24;
        java.util.TreeMap<String, String> who = new java.util.TreeMap<String, String>();
        for (Map.Entry<String, Map<String, Object>> e : game.tiles().entrySet()) if (!e.getKey().equals(game.name)) who.put(e.getKey(), Json.str(e.getValue().get("status")));
        for (String[] m : calls.people()) who.put(m[0], m[1] + " (in the Communicator)");
        boolean free = calls.inRoom() && calls.inCallWith().isEmpty();
        for (Map.Entry<String, String> e : who.entrySet()) {
            if (y > maxY) break;
            fit(c, e.getKey() + "   " + e.getValue(), 40, y, 19, Color.WHITE, false, right - 150);
            RectF b = new RectF(right - 100, y - 4, right, y + 28);
            button(c, b, "Call", false, free);
            if (free) btns.add(new Object[]{b, e.getKey()});
            y += 36;
        }
        if (who.isEmpty()) { text(c, "nobody else yet", 40, y, 18, Color.rgb(150, 150, 155), false); y += 26; }
        synchronized (callButtons) { callButtons.clear(); callButtons.addAll(btns); }
        return y;
    }

    private void drawLobby(Canvas c) {
        c.drawColor(Color.rgb(30, 34, 40));
        text(c, "Lobby", 40, 22, 36, Color.WHITE, true);
        Map<String, Object> ag = game.agreed();
        fit(c, "kart memory " + game.kartHost + ":" + game.kartPort + "  -  " + (ag != null ? Json.str(ag.get("state")) : "the kart service has not spoken yet"),
                40, 66, 18, Color.rgb(240, 200, 120), false, W - 80);
        if (ag != null) {
            StringBuilder hs = new StringBuilder();
            for (Map.Entry<String, Object> e : Json.obj(ag.get("hosts")).entrySet()) hs.append(hs.length() == 0 ? "" : ", ").append(e.getKey()).append(" (").append(Json.str(e.getValue())).append(")");
            fit(c, "agreed: " + Json.num(ag.get("laps")) + " lap(s)    game-serving machines: " + (hs.length() == 0 ? "none" : hs), 40, 90, 18, Color.rgb(180, 200, 220), false, W - 80);
        }
        float y = 124, colW = portrait ? W - 80 : 560;
        if (!game.inviteState.isEmpty()) fit(c, game.inviteState, 40, 108, 15, Color.rgb(240, 210, 120), false, W - 80);
        text(c, "players", 40, y, 24, Color.rgb(200, 220, 255), true); y += 34;
        Map<String, Map<String, Object>> servers = game.servers();
        Set<String> busy = new HashSet<String>();
        for (Map<String, Object> b : servers.values()) for (Object pl : Json.arr(b.get("players"))) busy.add(Json.str(pl));
        Map<String, Map<String, Object>> lob = game.lobby();
        List<String> names = new ArrayList<String>(lob.keySet()); Collections.sort(names);
        for (String n : names) {
            Map<String, Object> b = lob.get(n);
            String st = busy.contains(n) ? "racing" : Json.bool(b.get("ready")) ? "ready" : "not ready";
            fit(c, n + "   robots " + Json.num(b.get("bots")) + "   wants " + Json.num(b.get("laps")) + "   " + st, 40, y, 20, n.equals(game.name) ? Color.rgb(255, 230, 140) : Color.WHITE, false, colW);
            y += 26;
        }
        y += 10;
        text(c, "races running", 40, y, 24, Color.rgb(200, 220, 255), true); y += 34;
        if (servers.isEmpty()) { text(c, "none", 40, y, 20, Color.rgb(150, 150, 155), false); y += 26; }
        for (Map.Entry<String, Map<String, Object>> e : servers.entrySet()) { fit(c, e.getKey() + " on " + Json.str(e.getValue().get("host")), 40, y, 20, Color.WHITE, false, colW); y += 26; }
        boolean inCall = calls != null && !calls.inCallWith().isEmpty();
        if (calls != null) y = drawPeople(c, y + 20, portrait ? (inCall ? 560 : 560) : 600);

        float x2 = portrait ? 40 : 640, y2 = portrait ? Math.max(y + 10, 600) : 124, cw = portrait ? W - 80 : W - x2 - 20;
        if (portrait && inCall) y2 = 2000;                                   // the call's picture and buttons take the chat's place
        text(c, "chat (tap Chat to say something)", x2, y2, 24, Color.rgb(200, 220, 255), true); y2 += 34;
        List<Map<String, Object>> chats = game.chats();
        int keep = inCall ? 3 : portrait ? Math.max(1, (int) ((880 - y2) / 25)) : 11;
        for (Map<String, Object> m : chats.subList(Math.max(0, chats.size() - keep), chats.size())) {
            String line = Json.str(m.get("who")) + ": " + Json.str(m.get("text"));
            fit(c, line, x2, y2, 19, Json.str(m.get("who")).equals("kart service") ? Color.rgb(200, 200, 170) : Color.WHITE, false, cw);
            y2 += 25;
        }
        y2 += 8;
        if (!inCall && y2 < (portrait ? 960 : 600)) {
            text(c, "records (permanent)", x2, y2, 24, Color.rgb(200, 220, 255), true); y2 += 32;
            List<Map.Entry<String, Map<String, Object>>> recs = new ArrayList<Map.Entry<String, Map<String, Object>>>(game.records().entrySet());
            Collections.sort(recs, new Comparator<Map.Entry<String, Map<String, Object>>>() {
                public int compare(Map.Entry<String, Map<String, Object>> a, Map.Entry<String, Map<String, Object>> b) {
                    return Long.compare(Json.num(b.getValue().get("wins")), Json.num(a.getValue().get("wins"))); }
            });
            for (Map.Entry<String, Map<String, Object>> e : recs.subList(0, Math.min(4, recs.size()))) {
                if (y2 > (portrait ? 975 : 610)) break;
                Object bl = e.getValue().get("best_lap");
                fit(c, e.getKey() + "  races " + Json.num(e.getValue().get("races")) + "  wins " + Json.num(e.getValue().get("wins")) + "  best lap " + (bl == null ? "-" : Json.str(bl)), x2, y2, 19, Color.WHITE, false, cw);
                y2 += 25;
            }
        }
        button(c, B_READY, game.ready() ? "Ready ✓" : "Ready", game.ready(), true);
        button(c, B_START, game.started() ? "Starting…" : "Start", game.started(), true);
        button(c, B_MINUS, "-", false, true); button(c, B_PLUS, "+", false, true);
        text(c, game.laps() + " lap(s)", LAPS_AT.left, LAPS_AT.top, 22, Color.WHITE, true);
        button(c, B_CHAT, "Chat", false, true); button(c, B_COMMS, "Comms", calls != null && !calls.inCallWith().isEmpty(), true);
        button(c, B_LOG, "Log", showLog, true); button(c, B_HOME, "Home", false, true);
    }

    private void buildTrack() {
        trackBmp = Bitmap.createBitmap((int) TW, (int) TH, Bitmap.Config.ARGB_8888);
        Canvas t = new Canvas(trackBmp);
        t.drawColor(Color.rgb(38, 124, 67));
        Paint q = new Paint(Paint.ANTI_ALIAS_FLAG);
        q.setColor(Color.rgb(44, 138, 76));
        for (int i = 0; i < TW; i += 48) if ((i / 48) % 2 == 0) t.drawRect(i, 0, i + 48, TH, q);
        q.setColor(Color.rgb(54, 56, 62));
        for (int i = 0; i < 240; i++) { double[] pt = Kart.trackPoint(2 * Math.PI * i / 240); t.drawCircle((float) pt[0], (float) pt[1], (float) Kart.ROAD, q); }
        q.setStrokeWidth(4);
        double[][] rr = {{Kart.RX - Kart.ROAD, Kart.RY - Kart.ROAD}, {Kart.RX + Kart.ROAD, Kart.RY + Kart.ROAD}};
        for (double[] r : rr) {
            float lx = 0, ly = 0;
            for (int i = 0; i <= 240; i++) {
                double tt = 2 * Math.PI * i / 240; float x = (float) (Kart.CX + r[0] * Math.cos(tt)), y = (float) (Kart.CY + r[1] * Math.sin(tt));
                if (i > 0) { q.setColor((i / 4) % 2 == 0 ? Color.rgb(214, 70, 70) : Color.rgb(238, 238, 238)); t.drawLine(lx, ly, x, y, q); }
                lx = x; ly = y;
            }
        }
        double[] s = Kart.trackPoint(-Math.PI / 2);
        for (int o = (int) -Kart.ROAD; o < Kart.ROAD; o += 10) {
            q.setColor(Math.floorMod(o / 10, 2) == 0 ? Color.rgb(240, 240, 240) : Color.rgb(40, 40, 40));
            t.drawRect((float) (s[0] + o - 3), (float) (s[1] - 5), (float) (s[0] + o + 3), (float) (s[1] + 5), q);
        }
    }

    private void kart(Canvas c, double x, double y, double h, int color, String label) {
        c.save(); c.translate((float) x, (float) y); c.rotate((float) Math.toDegrees(h));
        p.setStyle(Paint.Style.FILL);
        p.setColor(Color.rgb(20, 20, 24)); c.drawRoundRect(-17, -8, 17, 8, 5, 5, p);
        p.setColor(color); c.drawRoundRect(-13, -11, 13, 11, 6, 6, p);
        p.setColor(Color.rgb(245, 245, 250)); c.drawRoundRect(5, -6, 13, 6, 3, 3, p);
        p.setColor(Color.rgb(15, 15, 18));
        c.drawRect(-17, -11, -12, -5, p); c.drawRect(-17, 5, -12, 11, p); c.drawRect(12, -11, 17, -5, p); c.drawRect(12, 5, 17, 11, p);
        c.restore();
        if (label != null) text(c, label, (float) x - 14, (float) y - 34, 16, Color.WHITE, true);
    }

    private void drawRace(Canvas c) {
        if (trackBmp == null) buildTrack();
        c.drawColor(Color.rgb(24, 26, 30));
        double now = System.currentTimeMillis() / 1000.0;
        c.save(); c.concat(trackM);                                     // the track, in its own units
        c.drawBitmap(trackBmp, 0, 0, null);
        for (Map.Entry<String, double[]> e : game.others(now).entrySet()) {
            double[] k = e.getValue();
            kart(c, k[0], k[1], k[2], Color.rgb((int) k[3], (int) k[4], (int) k[5]), e.getKey());
        }
        for (Kart k : game.mine()) kart(c, k.x, k.y, k.h, rgb(k.color), k.robot ? k.name : null);
        int lt = game.lapsTotal(); Kart me = game.me;
        if (game.placed()) {
            text(c, me.lap >= lt ? "FINISHED" : "Lap " + Math.min(me.lap + 1, lt) + "/" + lt, 20, 40, 40, Color.WHITE, true);
            if (now < game.startAt()) text(c, String.valueOf((int) Math.ceil(game.startAt() - now)), TW / 2 - 14, TH / 2 - 30, 60, Color.rgb(255, 230, 120), true);
        } else text(c, "taking the grid…", 20, 40, 32, Color.WHITE, true);
        c.restore();
        // the panel: what is going on, and the controls
        Map<String, Object> srv = game.myServer();
        Map<String, Object> rec = game.myRecord();
        float ix = portrait ? 20 : 884, iy = portrait ? 620 : 250;
        text(c, "race on " + (srv == null ? "?" : Json.str(srv.get("host"))) + "   " + String.format(java.util.Locale.ROOT, "%.0f fps", fps), ix, iy, 18, Color.rgb(210, 210, 210), false);
        if (rec != null) text(c, "races " + Json.num(rec.get("races")) + "  wins " + Json.num(rec.get("wins")), ix, iy + 26, 18, Color.rgb(210, 210, 210), false);
        button(c, R_LEAVE, "Leave", false, true);
        button(c, R_AUTO, game.autodrive ? "Autopilot ON" : "Autopilot off", game.autodrive, true);
        boolean manual = !game.autodrive;
        Set<String> h; synchronized (held) { h = new HashSet<String>(held); }
        button(c, R_GAS, "GAS", manual && h.contains("gas"), manual);
        button(c, R_BRAKE, "BRAKE", manual && h.contains("brake"), manual);
        p.setStyle(Paint.Style.FILL);
        p.setColor(Color.argb(manual ? (h.contains("left") ? 200 : 110) : 40, 255, 255, 255)); c.drawRoundRect(R_LEFT, 18, 18, p);
        p.setColor(Color.argb(manual ? (h.contains("right") ? 200 : 110) : 40, 255, 255, 255)); c.drawRoundRect(R_RIGHT, 18, 18, p);
        text(c, "◀", R_LEFT.centerX() - 18, R_LEFT.centerY() - 26, 48, Color.BLACK, true);
        text(c, "▶", R_RIGHT.centerX() - 18, R_RIGHT.centerY() - 26, 48, Color.BLACK, true);
    }

    private void drawLog(Canvas c) {
        float bottom = portrait ? H - 200 : 620;
        p.setStyle(Paint.Style.FILL); p.setColor(Color.argb(235, 10, 10, 12)); c.drawRect(20, 40, W - 20, bottom, p);
        List<String> l = game.log();
        int n = (int) ((bottom - 60) / 25);
        float y = 50;
        for (String s : l.subList(Math.max(0, l.size() - n), l.size())) { fit(c, s, 30, y, 16, Color.rgb(200, 230, 200), false, W - 60); y += 25; }
    }
}
