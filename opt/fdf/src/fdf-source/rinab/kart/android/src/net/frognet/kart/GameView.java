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
 * Everything is drawn in kartdemo.py's layout (1180 x 720) and scaled to the screen, so the track and every kart's
 * shared x/y are the same numbers on every game. Touch: lobby buttons; racing, steer / gas / brake (multi-touch) and Auto.
 */
public final class GameView extends SurfaceView implements SurfaceHolder.Callback, Runnable {
    public interface Host { void chatDialog(); void leave();
        /** [KART_CALLS_IN_THE_GAME_V1] where the far end's picture and this camera's go on the screen; null: hidden */
        void placeVideo(RectF remote, RectF self); }
    static final float W = 1180, H = 720, TW = (float) Kart.TRACK_W;

    private final Game game;
    private final Host host;
    private final GameCalls calls;
    private RectF placedRemote = null, placedSelf = null;
    private final List<Object[]> callButtons = new ArrayList<Object[]>();     // {RectF, name}: the lobby's Call buttons, as drawn
    private volatile boolean running = true, haveSurface = false;
    private Thread loop;
    private final Matrix toScreen = new Matrix(), toLayout = new Matrix();
    private Bitmap trackBmp;
    private final Paint p = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Set<String> held = new HashSet<String>();     // racing buttons under a finger now
    private volatile boolean showLog = false;
    private double fps = 0;

    // buttons, in layout units
    static final RectF B_READY = new RectF(40, 632, 220, 702), B_START = new RectF(240, 632, 420, 702),
            B_MINUS = new RectF(450, 632, 530, 702), B_PLUS = new RectF(640, 632, 720, 702),
            B_CHAT = new RectF(760, 632, 900, 702), B_LOG = new RectF(920, 632, 1030, 702), B_LEAVE = new RectF(1050, 632, 1160, 702);
    static final RectF R_LEFT = new RectF(16, 540, 146, 704), R_RIGHT = new RectF(160, 540, 290, 704),
            R_AUTO = new RectF(880, 150, 1160, 230), R_GAS = new RectF(880, 410, 1160, 560), R_BRAKE = new RectF(880, 576, 1160, 704),
            R_LEAVE = new RectF(880, 60, 1160, 130);
    // [KART_CALLS_IN_THE_GAME_V1] the call: the ring banner (both screens), the call's buttons, where the pictures go
    static final RectF RING = new RectF(250, 40, 870, 124), RING_ANSWER = new RectF(600, 52, 726, 112), RING_DECLINE = new RectF(734, 52, 860, 112);
    static final RectF C_MUTE_L = new RectF(680, 286, 790, 330), C_CAM_L = new RectF(800, 286, 910, 330), C_END_L = new RectF(1010, 286, 1160, 330),
            C_MUTE_R = new RectF(884, 290, 966, 350), C_CAM_R = new RectF(972, 290, 1054, 350), C_END_R = new RectF(1060, 290, 1160, 350);
    static final RectF V_LOBBY = new RectF(680, 340, 1160, 610), V_RACE = new RectF(240, 253, 620, 467);   // V_RACE: inside the infield

    public GameView(Context c, Game game, GameCalls calls, Host host) {
        super(c);
        this.game = game; this.host = host; this.calls = calls;
        getHolder().addCallback(this);
        setFocusable(true);
        loop = new Thread(this, "kart-loop");
        loop.start();
    }

    public void toggleLog() { showLog = !showLog; }

    /** ends the loop; the Activity stops the game (its sessions) */
    public void end() { running = false; try { loop.join(1000); } catch (InterruptedException ignored) { } }

    @Override public void surfaceCreated(SurfaceHolder h) { haveSurface = true; }
    @Override public void surfaceChanged(SurfaceHolder h, int f, int w, int hh) {
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
            placeVideo();
            if (haveSurface) {
                Canvas c = null;
                try {
                    c = getHolder().lockCanvas();
                    if (c != null) { c.drawColor(Color.BLACK); c.save(); c.concat(toScreen); drawFrame(c); c.restore(); }
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
        if (calls == null) return false;
        boolean racing = game.state.equals("racing");
        if (calls.ringingFrom() != null && RING.contains(q[0], q[1])) {
            if (RING_ANSWER.contains(q[0], q[1])) calls.answer();
            else if (RING_DECLINE.contains(q[0], q[1])) calls.decline();
            return true;
        }
        if (!calls.inCallWith().isEmpty()) {
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

    @Override public boolean onTouchEvent(MotionEvent e) {
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
                if (R_LEAVE.contains(q[0], q[1])) host.leave();
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
            else if (B_CHAT.contains(q[0], q[1])) host.chatDialog();
            else if (B_LOG.contains(q[0], q[1])) toggleLog();
            else if (B_LEAVE.contains(q[0], q[1])) host.leave();
        }
        return true;
    }

    // ---- drawing
    private void text(Canvas c, String s, float x, float y, float size, int color, boolean bold) {
        p.setStyle(Paint.Style.FILL); p.setColor(color); p.setTextSize(size); p.setFakeBoldText(bold);
        c.drawText(s, x, y + size, p);
    }
    private void button(Canvas c, RectF r, String label, boolean lit, boolean on) {
        p.setStyle(Paint.Style.FILL);
        p.setColor(lit ? Color.rgb(36, 140, 72) : on ? Color.rgb(60, 100, 160) : Color.rgb(70, 70, 75));
        c.drawRoundRect(r, 12, 12, p);
        p.setColor(Color.WHITE); p.setTextSize(26); p.setFakeBoldText(true);
        float tw = p.measureText(label);
        c.drawText(label, r.centerX() - tw / 2, r.centerY() + 9, p);
    }
    private static int rgb(int[] c) { return Color.rgb(c[0], c[1], c[2]); }

    private void drawFrame(Canvas c) {
        if (game.state.equals("racing")) drawRace(c); else drawLobby(c);
        String trouble = game.memoryState();
        if (trouble == null) trouble = game.raceState();
        if (trouble != null) {
            p.setStyle(Paint.Style.FILL); p.setColor(Color.argb(220, 120, 20, 20)); c.drawRect(0, 0, W, 34, p);
            text(c, trouble.length() > 110 ? trouble.substring(0, 110) : trouble, 12, 6, 20, Color.WHITE, true);
        }
        drawCall(c);
        if (showLog) drawLog(c);
    }

    // ---- the call [KART_CALLS_IN_THE_GAME_V1]
    private void placeVideo() {
        RectF r = null, sf = null;
        if (calls != null && !calls.inCallWith().isEmpty()) {
            RectF v = game.state.equals("racing") ? V_RACE : V_LOBBY;
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
        if (!w.isEmpty()) {
            RectF v = racing ? V_RACE : V_LOBBY;
            p.setStyle(Paint.Style.FILL); p.setColor(Color.rgb(10, 10, 12)); c.drawRect(v, p);       // under the picture
            if (!calls.connected()) text(c, "connecting…", v.left + 12, v.top + 10, 20, Color.rgb(200, 200, 200), false);
            long t = calls.callSeconds();
            String head = (calls.calling().isEmpty() ? "call with " : "calling ") + w + (calls.connected() ? String.format(java.util.Locale.ROOT, "  %d:%02d", t / 60, t % 60) : "");
            if (racing) text(c, head, 884, 360, 17, Color.rgb(140, 230, 160), true);
            else text(c, head, 680, 258, 20, Color.rgb(140, 230, 160), true);
            button(c, racing ? C_MUTE_R : C_MUTE_L, calls.muted() ? "Unmute" : "Mute", calls.muted(), true);
            button(c, racing ? C_CAM_R : C_CAM_L, calls.cameraOn() ? "Cam off" : "Cam on", false, true);
            p.setStyle(Paint.Style.FILL); p.setColor(Color.rgb(190, 50, 50)); c.drawRoundRect(racing ? C_END_R : C_END_L, 12, 12, p);
            RectF e = racing ? C_END_R : C_END_L;
            p.setColor(Color.WHITE); p.setTextSize(24); p.setFakeBoldText(true);
            c.drawText("End", e.centerX() - p.measureText("End") / 2, e.centerY() + 9, p);
        }
        else if (racing) text(c, calls.inRoom() ? "Communicator: ready for calls" : "Communicator: not connected", 884, 300, 16,
                calls.inRoom() ? Color.rgb(110, 220, 130) : Color.rgb(240, 90, 80), false);
        String from = calls.ringingFrom();
        if (from != null) {
            p.setStyle(Paint.Style.FILL); p.setColor(Color.argb(235, 20, 60, 30)); c.drawRoundRect(RING, 16, 16, p);
            text(c, from + " is calling", RING.left + 18, RING.top + 26, 28, Color.WHITE, true);
            p.setColor(Color.rgb(36, 160, 72)); c.drawRoundRect(RING_ANSWER, 12, 12, p);
            p.setColor(Color.rgb(190, 50, 50)); c.drawRoundRect(RING_DECLINE, 12, 12, p);
            p.setColor(Color.WHITE); p.setTextSize(24); p.setFakeBoldText(true);
            c.drawText("Answer", RING_ANSWER.centerX() - p.measureText("Answer") / 2, RING_ANSWER.centerY() + 9, p);
            c.drawText("Decline", RING_DECLINE.centerX() - p.measureText("Decline") / 2, RING_DECLINE.centerY() + 9, p);
        }
    }

    /** the lobby's Communicator section: the room's state and everyone to call, from the room and the kart games' tiles */
    private float drawPeople(Canvas c, float y) {
        List<Object[]> btns = new ArrayList<Object[]>();
        text(c, "Communicator", 40, y, 24, Color.rgb(200, 220, 255), true); y += 30;
        text(c, calls.roomState(), 40, y, 16, calls.inRoom() ? Color.rgb(110, 220, 130) : Color.rgb(240, 90, 80), false); y += 24;
        java.util.TreeMap<String, String> who = new java.util.TreeMap<String, String>();
        for (Map.Entry<String, Map<String, Object>> e : game.tiles().entrySet()) if (!e.getKey().equals(game.name)) who.put(e.getKey(), Json.str(e.getValue().get("status")));
        for (String[] m : calls.people()) who.put(m[0], m[1] + " (in the Communicator)");
        boolean free = calls.inRoom() && calls.inCallWith().isEmpty();
        for (Map.Entry<String, String> e : who.entrySet()) {
            if (y > 600) break;
            text(c, e.getKey() + "   " + e.getValue(), 40, y, 19, Color.WHITE, false);
            RectF b = new RectF(520, y - 4, 620, y + 28);
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
        text(c, "kart memory " + game.kartHost + ":" + game.kartPort + "  -  " + (ag != null ? Json.str(ag.get("state")) : "the kart service has not spoken yet"),
                40, 66, 18, Color.rgb(240, 200, 120), false);
        if (ag != null) {
            StringBuilder hs = new StringBuilder();
            for (Map.Entry<String, Object> e : Json.obj(ag.get("hosts")).entrySet()) hs.append(hs.length() == 0 ? "" : ", ").append(e.getKey()).append(" (").append(Json.str(e.getValue())).append(")");
            text(c, "agreed: " + Json.num(ag.get("laps")) + " lap(s)    game-serving machines: " + (hs.length() == 0 ? "none" : hs), 40, 90, 18, Color.rgb(180, 200, 220), false);
        }
        float y = 124;
        text(c, "players", 40, y, 24, Color.rgb(200, 220, 255), true); y += 34;
        Map<String, Map<String, Object>> servers = game.servers();
        Set<String> busy = new HashSet<String>();
        for (Map<String, Object> b : servers.values()) for (Object pl : Json.arr(b.get("players"))) busy.add(Json.str(pl));
        Map<String, Map<String, Object>> lob = game.lobby();
        List<String> names = new ArrayList<String>(lob.keySet()); Collections.sort(names);
        for (String n : names) {
            Map<String, Object> b = lob.get(n);
            String st = busy.contains(n) ? "racing" : Json.bool(b.get("ready")) ? "ready" : "not ready";
            text(c, n + "   robots " + Json.num(b.get("bots")) + "   wants " + Json.num(b.get("laps")) + "   " + st, 40, y, 20, n.equals(game.name) ? Color.rgb(255, 230, 140) : Color.WHITE, false);
            y += 26;
        }
        y += 10;
        text(c, "races running", 40, y, 24, Color.rgb(200, 220, 255), true); y += 34;
        if (servers.isEmpty()) text(c, "none", 40, y, 20, Color.rgb(150, 150, 155), false);
        for (Map.Entry<String, Map<String, Object>> e : servers.entrySet()) { text(c, e.getKey() + " on " + Json.str(e.getValue().get("host")), 40, y, 20, Color.WHITE, false); y += 26; }
        if (calls != null) drawPeople(c, y + 34);
        boolean inCall = calls != null && !calls.inCallWith().isEmpty();

        float x2 = 640, y2 = 124;
        text(c, "chat (tap Chat to say something)", x2, y2, 24, Color.rgb(200, 220, 255), true); y2 += 34;
        List<Map<String, Object>> chats = game.chats();
        int keep = inCall ? 3 : 11;
        for (Map<String, Object> m : chats.subList(Math.max(0, chats.size() - keep), chats.size())) {
            String line = Json.str(m.get("who")) + ": " + Json.str(m.get("text"));
            text(c, line.length() > 44 ? line.substring(0, 44) : line, x2, y2, 19, Json.str(m.get("who")).equals("kart service") ? Color.rgb(200, 200, 170) : Color.WHITE, false);
            y2 += 25;
        }
        y2 += 8;
        if (!inCall) text(c, "records (permanent)", x2, y2, 24, Color.rgb(200, 220, 255), true); y2 += 32;
        List<Map.Entry<String, Map<String, Object>>> recs = new ArrayList<Map.Entry<String, Map<String, Object>>>(game.records().entrySet());
        Collections.sort(recs, new Comparator<Map.Entry<String, Map<String, Object>>>() {
            public int compare(Map.Entry<String, Map<String, Object>> a, Map.Entry<String, Map<String, Object>> b) {
                return Long.compare(Json.num(b.getValue().get("wins")), Json.num(a.getValue().get("wins"))); }
        });
        for (Map.Entry<String, Map<String, Object>> e : recs.subList(0, inCall ? 0 : Math.min(4, recs.size()))) {
            Object bl = e.getValue().get("best_lap");
            text(c, e.getKey() + "  races " + Json.num(e.getValue().get("races")) + "  wins " + Json.num(e.getValue().get("wins")) + "  best lap " + (bl == null ? "-" : Json.str(bl)), x2, y2, 19, Color.WHITE, false);
            y2 += 25;
        }
        button(c, B_READY, game.ready() ? "Ready ✓" : "Ready", game.ready(), true);
        button(c, B_START, game.started() ? "Starting…" : "Start", game.started(), true);
        button(c, B_MINUS, "-", false, true); button(c, B_PLUS, "+", false, true);
        text(c, game.laps() + " lap(s)", 540, 652, 22, Color.WHITE, true);
        button(c, B_CHAT, "Chat", false, true); button(c, B_LOG, "Log", showLog, true); button(c, B_LEAVE, "Leave", false, true);
    }

    private void buildTrack() {
        trackBmp = Bitmap.createBitmap((int) TW, (int) H, Bitmap.Config.ARGB_8888);
        Canvas t = new Canvas(trackBmp);
        t.drawColor(Color.rgb(38, 124, 67));
        Paint q = new Paint(Paint.ANTI_ALIAS_FLAG);
        q.setColor(Color.rgb(44, 138, 76));
        for (int i = 0; i < TW; i += 48) if ((i / 48) % 2 == 0) t.drawRect(i, 0, i + 48, H, q);
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
        c.drawBitmap(trackBmp, 0, 0, null);
        double now = System.currentTimeMillis() / 1000.0;
        for (Map.Entry<String, double[]> e : game.others(now).entrySet()) {
            double[] k = e.getValue();
            kart(c, k[0], k[1], k[2], Color.rgb((int) k[3], (int) k[4], (int) k[5]), e.getKey());
        }
        for (Kart k : game.mine()) kart(c, k.x, k.y, k.h, rgb(k.color), k.robot ? k.name : null);
        int lt = game.lapsTotal(); Kart me = game.me;
        if (game.placed()) {
            text(c, me.lap >= lt ? "FINISHED" : "Lap " + Math.min(me.lap + 1, lt) + "/" + lt, 20, 40, 40, Color.WHITE, true);
            if (now < game.startAt()) text(c, String.valueOf((int) Math.ceil(game.startAt() - now)), TW / 2 - 14, H / 2 - 30, 60, Color.rgb(255, 230, 120), true);
        } else text(c, "taking the grid…", 20, 40, 32, Color.WHITE, true);
        // the panel: what is going on, and the controls
        Map<String, Object> srv = game.myServer();
        Map<String, Object> rec = game.myRecord();
        text(c, "race on " + (srv == null ? "?" : Json.str(srv.get("host"))) + "   " + String.format(java.util.Locale.ROOT, "%.0f fps", fps), 884, 250, 18, Color.rgb(210, 210, 210), false);
        if (rec != null) text(c, "races " + Json.num(rec.get("races")) + "  wins " + Json.num(rec.get("wins")), 884, 276, 18, Color.rgb(210, 210, 210), false);
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
        p.setStyle(Paint.Style.FILL); p.setColor(Color.argb(235, 10, 10, 12)); c.drawRect(20, 40, W - 20, 620, p);
        List<String> l = game.log();
        float y = 50;
        for (String s : l.subList(Math.max(0, l.size() - 22), l.size())) { text(c, s.length() > 120 ? s.substring(0, 120) : s, 30, y, 16, Color.rgb(200, 230, 200), false); y += 25; }
    }
}
