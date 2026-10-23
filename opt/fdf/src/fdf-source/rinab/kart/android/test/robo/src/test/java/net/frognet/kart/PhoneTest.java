package net.frognet.kart;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertNotNull;
import static org.junit.Assert.assertTrue;
import static org.robolectric.Shadows.shadowOf;

import android.app.AlertDialog;
import android.graphics.Bitmap;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.RectF;
import android.os.Looper;
import android.os.SystemClock;
import android.view.MotionEvent;
import android.view.View;
import android.view.ViewGroup;
import android.widget.Button;
import android.widget.EditText;
import android.widget.TextView;

import net.frognet.kart.core.Game;
import net.frognet.kart.core.Json;

import org.junit.Test;
import org.junit.runner.RunWith;
import org.robolectric.Robolectric;
import org.robolectric.RobolectricTestRunner;
import org.robolectric.android.controller.ActivityController;
import org.robolectric.annotation.Config;
import org.robolectric.annotation.GraphicsMode;
import org.robolectric.annotation.LooperMode;
import org.robolectric.shadows.ShadowAlertDialog;

import java.io.File;
import java.io.FileOutputStream;
import java.lang.reflect.Field;
import java.lang.reflect.Method;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;

/**
 * The app's own Activity and view code (Android 14 framework, Robolectric; the app targets 33 and runs from 26) against a live kart service. [KART_ANDROID_V1]
 * -Dkart=host:port is the kart memory; -Dshots=<dir> gets PNGs of what the screen draws. Everything is done the way a person
 * does it: type in the setup screen, tap Join, tap Ready on the game view, tap Autopilot, tap Chat and Send, tap Leave.
 */
@RunWith(RobolectricTestRunner.class)
@Config(sdk = 34)
@LooperMode(LooperMode.Mode.PAUSED)
@GraphicsMode(GraphicsMode.Mode.NATIVE)
public class PhoneTest {
    static final int SW = 2400, SH = 1080;                     // a Galaxy S21's screen, landscape

    static void say(String s) { System.out.println("[PHONE_TEST] " + s); System.out.flush(); }

    static <T extends View> List<T> all(View v, Class<T> k, List<T> out) {
        if (k.isInstance(v)) out.add(k.cast(v));
        if (v instanceof ViewGroup) for (int i = 0; i < ((ViewGroup) v).getChildCount(); i++) all(((ViewGroup) v).getChildAt(i), k, out);
        return out;
    }

    /** the main looper's posted work, its clock kept with real time (the Communicator's ticks are posted 500 ms ahead) */
    static long lastStep = 0;
    static void step() {
        long now = System.currentTimeMillis(), d = lastStep == 0 ? 0 : Math.min(200, now - lastStep); lastStep = now;
        shadowOf(Looper.getMainLooper()).idleFor(java.time.Duration.ofMillis(d));
    }

    /** the far end of a call: the Communicator's reference core (frognet-android's communicator-test/Probe) as its own program */
    static Process probe(String out, String... args) throws Exception {
        List<String> cmd = new ArrayList<String>(java.util.Arrays.asList("java", "-cp", System.getProperty("probe"), "Probe"));
        cmd.addAll(java.util.Arrays.asList(args));
        say("the far end: Probe " + String.join(" ", args));
        return new ProcessBuilder(cmd).redirectErrorStream(true).redirectOutput(new File(out)).start();
    }
    static String slurp(String f) throws Exception { return new String(java.nio.file.Files.readAllBytes(new File(f).toPath()), "UTF-8"); }
    static String lines(String text, String prefix) {
        StringBuilder b = new StringBuilder();
        for (String l : text.split("\n")) if (l.startsWith(prefix)) b.append(b.length() == 0 ? "" : " | ").append(l);
        return b.toString();
    }

    static void idle(long ms) throws Exception {
        long end = System.currentTimeMillis() + ms;
        while (System.currentTimeMillis() < end) { step(); Thread.sleep(20); }
    }

    interface Until { boolean now() throws Exception; }
    static boolean waitFor(long ms, Until u) throws Exception {
        long end = System.currentTimeMillis() + ms;
        while (System.currentTimeMillis() < end) { step(); if (u.now()) return true; Thread.sleep(50); }
        return false;
    }

    /** a tap at a layout point (kartdemo's 1180 x 720), mapped to the screen the way the view maps the screen to the layout */
    static void tap(GameView v, float lx, float ly) {
        float s = Math.min(SW / GameView.W, SH / GameView.H), x = (SW - GameView.W * s) / 2 + lx * s, y = (SH - GameView.H * s) / 2 + ly * s;
        long t = SystemClock.uptimeMillis();
        MotionEvent d = MotionEvent.obtain(t, t, MotionEvent.ACTION_DOWN, x, y, 0), u = MotionEvent.obtain(t, t + 60, MotionEvent.ACTION_UP, x, y, 0);
        v.onTouchEvent(d); v.onTouchEvent(u); d.recycle(); u.recycle();
    }

    /** what the view draws, at the phone's size (its own drawFrame with its own screen matrix) */
    static Bitmap shot(GameView v, String name) throws Exception {
        Bitmap b = Bitmap.createBitmap(SW, SH, Bitmap.Config.ARGB_8888);
        Canvas c = new Canvas(b);
        Field ts = GameView.class.getDeclaredField("toScreen"); ts.setAccessible(true);
        Method df = GameView.class.getDeclaredMethod("drawFrame", Canvas.class); df.setAccessible(true);
        c.drawColor(Color.BLACK); c.save(); c.concat((android.graphics.Matrix) ts.get(v)); df.invoke(v, c); c.restore();
        String dir = System.getProperty("shots");
        if (dir != null && !dir.isEmpty() && !dir.startsWith("$")) {
            new File(dir).mkdirs();
            try (FileOutputStream o = new FileOutputStream(new File(dir, name + ".png"))) { b.compress(Bitmap.CompressFormat.PNG, 100, o); }
            say("screenshot " + dir + "/" + name + ".png");
        }
        return b;
    }

    static int distinct(Bitmap b) {
        java.util.HashSet<Integer> s = new java.util.HashSet<Integer>();
        for (int y = 0; y < b.getHeight(); y += 7) for (int x = 0; x < b.getWidth(); x += 7) s.add(b.getPixel(x, y));
        return s.size();
    }

    @Test public void playsARace() throws Exception {
        String kart = System.getProperty("kart");
        assertNotNull("-Dkart=host:port", kart);
        ActivityController<MainActivity> ac = Robolectric.buildActivity(MainActivity.class).setup();
        MainActivity a = ac.get();
        View root = a.getWindow().getDecorView();

        // the setup screen
        List<EditText> fields = all(root, EditText.class, new ArrayList<EditText>());
        assertEquals("setup: name, kart memory, robots, laps, Communicator memory, media", 6, fields.size());
        say("setup screen: name '" + fields.get(0).getText() + "', kart memory '" + fields.get(1).getText() + "', robots " + fields.get(2).getText() + ", laps " + fields.get(3).getText());
        assertEquals("165.232.153.144:8799", fields.get(1).getText().toString());
        fields.get(0).setText("phone"); fields.get(1).setText(kart); fields.get(2).setText("1"); fields.get(3).setText("2");
        String[] cm = System.getProperty("comms").split(",");             // the Communicator's memory and media server ports
        fields.get(4).setText("127.0.0.1:" + cm[0]); fields.get(5).setText("127.0.0.1:" + cm[1]);
        String ivf = System.getProperty("ivf"), opus = System.getProperty("opus"), tmp = System.getProperty("tmp");
        Button join = null;
        for (Button b : all(root, Button.class, new ArrayList<Button>())) if (b.getText().toString().equals("Connect")) join = b;
        assertNotNull(join);
        fields.get(1).setText("no-port-here");
        join.performClick(); idle(50);
        String err = null;
        for (TextView t : all(root, TextView.class, new ArrayList<TextView>())) if (t.getCurrentTextColor() == Color.rgb(255, 120, 120)) err = t.getText().toString();
        say("a kart memory with no port: '" + err + "'");
        assertTrue(err != null && err.contains("host:port"));
        fields.get(1).setText(kart);
        join.performClick(); idle(100);

        // the game view
        Field gv = MainActivity.class.getDeclaredField("view"); gv.setAccessible(true);
        final GameView v = (GameView) gv.get(a);
        assertNotNull("Join shows the game view", v);
        Field gf = MainActivity.class.getDeclaredField("game"); gf.setAccessible(true);
        final Game g = (Game) gf.get(a);
        v.surfaceChanged(v.getHolder(), 0, SW, SH);
        Field cf = MainActivity.class.getDeclaredField("calls"); cf.setAccessible(true);
        final net.frognet.communicator.GameCalls k = (net.frognet.communicator.GameCalls) cf.get(a);
        // [KART_HOME_V1] connected: the kart memory and the Communicator's, and the home screen; not in the game's lobby
        assertTrue("connected to the kart memory and the Communicator", waitFor(10000, () -> g.memoryState() == null && k.inRoom() && g.agreed() != null));
        assertTrue("home: others seen", waitFor(8000, () -> g.tiles().containsKey("red") && g.tiles().containsKey("blue")));
        idle(1500);
        assertEquals("home", v.screen);
        assertTrue("connected is not playing: no lobby tuple", !g.lobby().containsKey("phone"));
        say("home: kart memory up, " + k.roomState() + "; tiles " + g.tiles().keySet() + "; lobby " + g.lobby().keySet() + " (the phone is not in it)");
        shot(v, "0-home");
        tap(v, GameView.H_GAME.centerX(), GameView.H_GAME.centerY());
        assertEquals("game", v.screen);
        assertTrue("in the lobby", waitFor(10000, () -> g.lobby().containsKey("phone")));
        say("in the lobby: players " + g.lobby().keySet() + ", agreed " + g.agreed().get("state"));
        idle(1500);
        Bitmap lobby = shot(v, "1-lobby");
        assertTrue("the lobby draws", distinct(lobby) > 8);

        // Chat: the dialog, typed, Send
        tap(v, GameView.B_CHAT.centerX(), GameView.B_CHAT.centerY());
        assertTrue("Chat opens the dialog", waitFor(3000, () -> ShadowAlertDialog.getLatestAlertDialog() != null));
        AlertDialog d = ShadowAlertDialog.getLatestAlertDialog();
        EditText box = all(d.getWindow().getDecorView(), EditText.class, new ArrayList<EditText>()).get(0);
        box.setText("hello from the phone");
        d.getButton(AlertDialog.BUTTON_POSITIVE).performClick();
        assertTrue("the chat line is in the shared memory", waitFor(8000, () -> {
            for (Map<String, Object> m : g.chats()) if ("hello from the phone".equals(m.get("text"))) return true;
            return false; }));
        say("chat: 'hello from the phone' is in the lobby's chat");

        // laps: - then + (2 -> 1 -> 2)
        tap(v, GameView.B_MINUS.centerX(), GameView.B_MINUS.centerY()); assertEquals(1, g.laps());
        tap(v, GameView.B_PLUS.centerX(), GameView.B_PLUS.centerY()); assertEquals(2, g.laps());

        // ---- [KART_CALLS_IN_THE_GAME_V1] the third memory: the Communicator's, held beside the kart memory
        assertTrue("in the Communicator room", k.inRoom());
        say("Communicator: " + k.roomState());

        // a call from the lobby: Home (the reference core, answering) is in the room; tap Call beside Home
        String homeOut = tmp + "/probe-home.txt";
        Process home = probe(homeOut, "answer", cm[0], "Home", "phone", ivf, opus, "8", tmp + "/home.opus");
        assertTrue("Home is in the lobby's Communicator list", waitFor(10000, () -> { for (String[] m : k.people()) if (m[0].equals("Home")) return true; return false; }));
        idle(700);
        shot(v, "2-lobby-communicator");
        Field cb = GameView.class.getDeclaredField("callButtons"); cb.setAccessible(true);
        RectF callHome = null;
        for (Object[] b : (List<Object[]>) cb.get(v)) if ("Home".equals(b[1])) callHome = (RectF) b[0];
        assertNotNull("a Call button beside Home", callHome);
        tap(v, callHome.centerX(), callHome.centerY());
        assertTrue("the call with Home is up", waitFor(10000, () -> k.connected()));
        assertTrue("media from Home", waitFor(8000, () -> k.mediaIn() > 100));
        say("in a call with " + k.inCallWith() + ": " + k.mediaLine());
        boolean tiled = waitFor(5000, () -> g.tiles().get("phone") != null && "calling Home".equals(g.tiles().get("phone").get("status")));
        say("the phone's tiles view: " + g.tiles());
        assertTrue("the phone's call tile says it", tiled);
        say("the phone's tile in the kart memory: " + g.tiles().get("phone"));
        shot(v, "3-lobby-call");
        home.waitFor();
        String ho = slurp(homeOut);
        say("Home: " + lines(ho, "ANSWERING") + " | " + lines(ho, "VIDEO") + " | " + lines(ho, "AUDIO"));
        assertTrue("Home answered the phone's call", ho.contains("ANSWERING phone"));
        tap(v, GameView.C_END_L.centerX(), GameView.C_END_L.centerY());
        assertTrue("End ends the call", waitFor(3000, () -> k.inCallWith().isEmpty()));
        assertTrue("the room stays", k.inRoom());
        say("call ended; still in the room: " + k.roomState());

        // Start (blue sits in the lobby not ready: only Start launches a race for the ready ones)
        tap(v, GameView.B_START.centerX(), GameView.B_START.centerY());
        assertTrue("Start", g.ready() && g.started());
        assertTrue("the race starts and the phone is placed on its grid", waitFor(40000, () -> g.state.equals("racing") && g.placed()));
        say("racing: race " + g.myRace() + " on " + g.myServer().get("host") + ", " + g.lapsTotal() + " lap(s)");
        // a call while racing: Julie (the reference core) calls the phone
        // blue calls (blue's Communicator, the reference core; blue's kart game sits in the lobby)
        String julieOut = tmp + "/probe-blue.txt";
        Process julie = probe(julieOut, "call", cm[0], cm[1], "blue", "phone", ivf, opus, "90", tmp + "/blue.opus");
        assertTrue("it rings while racing", waitFor(10000, () -> "blue".equals(k.ringingFrom())));
        say("ringing while racing: " + k.ringingFrom() + " is calling");
        shot(v, "4-race-ringing");
        tap(v, GameView.RING_ANSWER.centerX(), GameView.RING_ANSWER.centerY());
        assertTrue("answered while racing", waitFor(10000, () -> k.connected()));
        final String raceSession = k.session();
        assertTrue("media from blue while racing", waitFor(8000, () -> k.mediaIn() > 100));
        assertTrue("still racing", g.state.equals("racing"));
        say("in a call with " + k.inCallWith() + " while racing (session " + raceSession + "): " + k.mediaLine());
        tap(v, GameView.R_AUTO.centerX(), GameView.R_AUTO.centerY());
        assertTrue("Autopilot on", g.autodrive);
        assertTrue("the start", waitFor(15000, () -> System.currentTimeMillis() / 1000.0 > g.startAt() + 3));
        Bitmap race = shot(v, "5-race-call");
        assertTrue("the race draws", distinct(race) > 8);
        assertTrue("other karts are seen", waitFor(5000, () -> !g.others(System.currentTimeMillis() / 1000.0).isEmpty()));
        say("other karts seen: " + g.others(System.currentTimeMillis() / 1000.0).keySet() + "; me lap " + g.me.lap + " at (" + (int) g.me.x + "," + (int) g.me.y + ")");
        idle(4000);
        say("me lap " + g.me.lap + " at (" + (int) g.me.x + "," + (int) g.me.y + "), the call: " + k.mediaLine());
        assertTrue("the race ends and the phone is back in the lobby", waitFor(120000, () -> g.state.equals("lobby") && g.myRace() == null));
        say("race over: " + g.lastResult());
        // the race memory is gone; the Communicator's is not: the same call goes on
        long m0 = k.mediaIn(); idle(3000); long m1 = k.mediaIn();
        say("back in the lobby, the call: session " + k.session() + " with " + k.inCallWith() + ", media in " + m0 + " -> " + m1 + " in 3 s");
        assertEquals("the same call", raceSession, k.session());
        assertTrue("media still coming after the race", m1 > m0 + 50);
        shot(v, "6-lobby-call-after-race");
        // the Communicator's screen, still in the game; Race! races the call's people
        tap(v, GameView.B_COMMS.centerX(), GameView.B_COMMS.centerY());
        assertEquals("comms", v.screen);
        assertTrue("the call's people", waitFor(5000, () -> k.participants().contains("blue")));
        idle(1000);
        shot(v, "7-comms-call");
        say("Communicator screen: in a call with " + k.participants() + "; " + k.mediaLine());
        // [KART_RACE_INVITE_V1] Race!: blue (in the call, running FrogNet Kart) is invited; blue's game answers in the kart memory
        tap(v, GameView.K_RACE.centerX(), GameView.K_RACE.centerY());
        assertTrue("invited", waitFor(3000, () -> g.inviteState.startsWith("invited [blue]")));
        say("Race!: " + g.inviteState);
        idle(600);
        shot(v, "8-comms-invited");
        assertTrue("everyone said yes", waitFor(10000, () -> { for (String l : g.log()) if (l.contains("everyone said yes ([blue])")) return true; return false; }));
        for (String l : g.log()) if (l.contains("[KART_RACE_INVITE_V1]")) say(l);
        assertEquals("all yes: the phone launches into the game", "game", v.screen);
        assertTrue("a race for the call: blue's kart game is in it", waitFor(40000, () -> g.state.equals("racing") && g.myServer() != null
                && Json.arr(g.myServer().get("players")).contains("blue")));
        say("race " + g.myRace() + " players " + g.myServer().get("players") + "; the call goes on: " + k.inCallWith() + ", " + k.mediaLine());
        assertTrue("told in the call's chat", waitFor(5000, () -> { for (String l : k.chat()) if (l.contains("invites [blue] to a kart race")) return true; return false; }));
        idle(6000);
        shot(v, "9-invited-race-call");
        tap(v, GameView.R_LEAVE.centerX(), GameView.R_LEAVE.centerY());
        assertTrue("Leave: out of the race, in the lobby", waitFor(5000, () -> g.state.equals("lobby") && g.playing));
        assertTrue("the call goes on", !k.inCallWith().isEmpty());
        tap(v, GameView.C_END_L.centerX(), GameView.C_END_L.centerY());
        assertTrue("End ends the call", waitFor(3000, () -> k.inCallWith().isEmpty()));
        julie.destroy(); julie.waitFor();
        say("blue's Communicator: " + lines(slurp(julieOut), "CALLING"));
        idle(1500);
        idle(1500);
        shot(v, "10-lobby-after");
        assertTrue("a record for the phone", g.records().containsKey("phone"));
        say("phone's record: " + g.records().get("phone"));

        // Log, then Leave: back to the setup screen, out of the lobby
        tap(v, GameView.B_LOG.centerX(), GameView.B_LOG.centerY());
        shot(v, "11-log");
        tap(v, GameView.B_LOG.centerX(), GameView.B_LOG.centerY());
        tap(v, GameView.B_HOME.centerX(), GameView.B_HOME.centerY());
        assertEquals("home", v.screen);
        assertTrue("Home: out of the lobby", waitFor(5000, () -> !g.lobby().containsKey("phone")));
        tap(v, GameView.H_SETTINGS.centerX(), GameView.H_SETTINGS.centerY());
        idle(500);
        assertTrue("Leave shows the setup screen", gv.get(a) == null && all(a.getWindow().getDecorView(), EditText.class, new ArrayList<EditText>()).size() == 6);
        idle(500);
        say("left: setup screen again; the Communicator: " + (cf.get(a) == null ? "left the room" : "STILL HELD"));
        ac.pause().stop().destroy();
    }
}
