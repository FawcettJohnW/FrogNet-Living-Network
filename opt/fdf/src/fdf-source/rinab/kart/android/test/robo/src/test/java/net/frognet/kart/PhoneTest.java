package net.frognet.kart;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertNotNull;
import static org.junit.Assert.assertNull;
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
    static int SW = 2400, SH = 1080;                           // a Galaxy S21's screen: landscape, or turned (portrait)

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
        float s = Math.min(SW / v.W, SH / v.H), x = (SW - v.W * s) / 2 + lx * s, y = (SH - v.H * s) / 2 + ly * s;
        long t = SystemClock.uptimeMillis();
        MotionEvent d = MotionEvent.obtain(t, t, MotionEvent.ACTION_DOWN, x, y, 0), u = MotionEvent.obtain(t, t + 60, MotionEvent.ACTION_UP, x, y, 0);
        v.onTouchEvent(d); v.onTouchEvent(u); d.recycle(); u.recycle();
    }

    /** [KART_ROTATES_V1] the phone turned: the surface's new shape, as Android reports it to the view */
    static void turn(GameView v, boolean portrait) throws Exception {
        SW = portrait ? 1080 : 2400; SH = portrait ? 2400 : 1080;
        v.surfaceChanged(v.getHolder(), 0, SW, SH);
        idle(300);
        assertEquals("the layout follows the turn", portrait, v.portrait);
        say("turned " + (portrait ? "portrait" : "landscape") + ": layout " + (int) v.W + " x " + (int) v.H);
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

        // [KART_BANNER_IN_THE_LOG_V1] a kart memory that is not there: the red banner, and the same words in the log
        Game g0 = new Game("nobody", "robolectric", "127.0.0.1", 1, 0, false, 1, null);
        GameView v0 = new GameView(a, g0, null, new GameView.Host() {
            public void chatDialog(boolean t) { } public void leave() { } public void settings() { } public void exitApp() { } public void placeVideo(RectF r, RectF sf) { }
            public boolean vpnUp() { return true; } public String tunnel() { return null; } });
        v0.surfaceChanged(v0.getHolder(), 0, SW, SH);
        assertTrue("the kart memory at port 1 fails", waitFor(5000, () -> g0.memoryState() != null && !g0.memoryState().startsWith("connecting")));
        shot(v0, "00-banner");
        String said = null; for (String l : g0.log()) if (l.startsWith("[KART_BANNER_IN_THE_LOG_V1] banner: ")) said = l;
        say("the banner, logged: " + said);
        assertTrue("the red banner is in the log, word for word", said != null && said.endsWith(g0.memoryState()));
        v0.end(); g0.stop();

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
        join.performClick();

        // the game view
        Field gv = MainActivity.class.getDeclaredField("view"); gv.setAccessible(true);
        final GameView v = (GameView) gv.get(a);
        assertNotNull("Join shows the game view", v);
        Field gf = MainActivity.class.getDeclaredField("game"); gf.setAccessible(true);
        final Game g = (Game) gf.get(a);
        v.surfaceChanged(v.getHolder(), 0, SW, SH);
        boolean early = g.memoryState() != null;                           // drawn before the kart memory answers: the banner says so
        shot(v, "00-connecting");
        say("drawn at once after Connect: kart memory " + (early ? "not answered yet" : "already answered"));
        idle(100);
        if (early) {
            boolean logged = false; for (String l : g.log()) if (l.startsWith("[KART_BANNER_IN_THE_LOG_V1] banner: ")) { logged = true; say("logged: " + l); }
            assertTrue("the red banner is in the log", logged);
        }
        Field cf = MainActivity.class.getDeclaredField("calls"); cf.setAccessible(true);
        final net.frognet.communicator.GameCalls k = (net.frognet.communicator.GameCalls) cf.get(a);
        // [KART_HOME_V1] connected: the kart memory and the Communicator's, and the home screen; not in the game's lobby
        assertTrue("connected to the kart memory and the Communicator", waitFor(10000, () -> g.memoryState() == null && k.inRoom() && g.agreed() != null));
        assertTrue("home: others seen", waitFor(8000, () -> g.tiles().containsKey("red") && g.tiles().containsKey("blue")));
        idle(1500);
        assertEquals("home", v.screen);
        assertTrue("connected is not playing: no lobby tuple", !g.lobby().containsKey("phone"));
        // [KART_IS_A_MEMBER_V1] the FrogNet membership is this app's own: started once the permission question is answered
        // (one system question at a time), and not again when the game comes back to the front
        a.onRequestPermissionsResult(1, new String[0], new int[0]);
        org.robolectric.shadows.ShadowActivity.IntentForResult vc = shadowOf(a).getNextStartedActivityForResult();
        assertNotNull("Android's VPN question, once", vc);
        say("asked: " + vc.intent.getComponent());
        shadowOf(a).receiveResult(vc.intent, android.app.Activity.RESULT_OK, null);   // the person says OK
        android.content.Intent ms = shadowOf(org.robolectric.RuntimeEnvironment.getApplication()).getNextStartedService();
        assertNotNull("the membership is started", ms);
        assertEquals("com.fawcettinnovations.fdf.android.MemberService", ms.getComponent().getClassName());
        assertEquals("this app's own", a.getPackageName(), ms.getComponent().getPackageName());
        for (int i = 0; i < 5; i++) { ac.pause(); ac.resume(); idle(50); }
        int starts = 0; for (String l : g.log()) if (l.contains("starting the FrogNet membership")) { starts++; say("FrogNet: " + l); }
        assertEquals("started once", 1, starts);
        assertTrue("and once only", shadowOf(org.robolectric.RuntimeEnvironment.getApplication()).getNextStartedService() == null);
        say("home: kart memory up, " + k.roomState() + "; tiles " + g.tiles().keySet() + "; lobby " + g.lobby().keySet() + " (the phone is not in it)");
        shot(v, "0-home");
        turn(v, true);
        shot(v, "0p-home");
        tap(v, v.H_GAME.centerX(), v.H_GAME.centerY());                   // the portrait layout's Kart racing card
        assertEquals("game", v.screen);
        assertTrue("in the lobby", waitFor(10000, () -> g.lobby().containsKey("phone")));
        say("in the lobby: players " + g.lobby().keySet() + ", agreed " + g.agreed().get("state"));
        idle(1500);
        Bitmap lobby = shot(v, "1p-lobby");
        assertTrue("the lobby draws", distinct(lobby) > 8);

        // Chat: the dialog, typed, Send
        tap(v, v.B_CHAT.centerX(), v.B_CHAT.centerY());
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
        tap(v, v.B_MINUS.centerX(), v.B_MINUS.centerY()); assertEquals(1, g.laps());
        tap(v, v.B_PLUS.centerX(), v.B_PLUS.centerY()); assertEquals(2, g.laps());

        // ---- [KART_CALLS_IN_THE_GAME_V1] the third memory: the Communicator's, held beside the kart memory
        assertTrue("in the Communicator room", k.inRoom());
        say("Communicator: " + k.roomState());

        // a call from the lobby: Home (the reference core, answering) is in the room; tap Call beside Home
        String homeOut = tmp + "/probe-home.txt";
        Process home = probe(homeOut, "answer", cm[0], "Home", "phone", ivf, opus, "8", tmp + "/home.opus");
        assertTrue("Home is in the lobby's Communicator list", waitFor(10000, () -> { for (String[] m : k.people()) if (m[0].equals("Home")) return true; return false; }));
        idle(700);
        shot(v, "2p-lobby-communicator");
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
        shot(v, "3p-lobby-call");
        home.waitFor();
        String ho = slurp(homeOut);
        say("Home: " + lines(ho, "ANSWERING") + " | " + lines(ho, "VIDEO") + " | " + lines(ho, "AUDIO"));
        assertTrue("Home answered the phone's call", ho.contains("ANSWERING phone"));
        tap(v, v.C_END_L.centerX(), v.C_END_L.centerY());
        assertTrue("End ends the call", waitFor(3000, () -> k.inCallWith().isEmpty()));
        assertTrue("the room stays", k.inRoom());
        turn(v, false);
        shot(v, "3-lobby");
        say("call ended; still in the room: " + k.roomState());

        // Start (blue sits in the lobby not ready: only Start launches a race for the ready ones)
        tap(v, v.B_START.centerX(), v.B_START.centerY());
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
        tap(v, v.RING_ANSWER.centerX(), v.RING_ANSWER.centerY());
        assertTrue("answered while racing", waitFor(10000, () -> k.connected()));
        final String raceSession = k.session();
        assertTrue("media from blue while racing", waitFor(8000, () -> k.mediaIn() > 100));
        assertTrue("still racing", g.state.equals("racing"));
        say("in a call with " + k.inCallWith() + " while racing (session " + raceSession + "): " + k.mediaLine());
        tap(v, v.R_AUTO.centerX(), v.R_AUTO.centerY());
        assertTrue("Autopilot on", g.autodrive);
        assertTrue("the start", waitFor(15000, () -> System.currentTimeMillis() / 1000.0 > g.startAt() + 3));
        Bitmap race = shot(v, "5-race-call");
        turn(v, true);                                                     // turned mid-race, in a call
        shot(v, "5p-race-call");
        Field pr = GameView.class.getDeclaredField("placedRemote"); pr.setAccessible(true);
        RectF pv = (RectF) pr.get(v);
        say("portrait race: the call's picture at " + pv + " on a " + SW + " x " + SH + " screen");
        assertTrue("the call's picture is on the portrait screen", pv != null && pv.right <= SW && pv.bottom <= SH);
        turn(v, false);
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
        tap(v, v.B_COMMS.centerX(), v.B_COMMS.centerY());
        assertEquals("comms", v.screen);
        turn(v, true);
        assertTrue("the call's people", waitFor(5000, () -> k.participants().contains("blue")));
        idle(1000);
        shot(v, "7p-comms-call");
        say("Communicator screen: in a call with " + k.participants() + "; " + k.mediaLine());
        // [KART_RACE_INVITE_V1] Race!: blue (in the call, running FrogNet Kart) is invited; blue's game answers in the kart memory
        tap(v, v.K_RACE.centerX(), v.K_RACE.centerY());
        assertTrue("invited", waitFor(3000, () -> g.inviteState.startsWith("invited [blue]")));
        say("Race!: " + g.inviteState);
        idle(600);
        shot(v, "8p-comms-invited");
        assertTrue("everyone said yes", waitFor(10000, () -> { for (String l : g.log()) if (l.contains("everyone said yes ([blue])")) return true; return false; }));
        for (String l : g.log()) if (l.contains("[KART_RACE_INVITE_V1]")) say(l);
        assertEquals("all yes: the phone launches into the game", "game", v.screen);
        assertTrue("a race for the call: blue's kart game is in it", waitFor(40000, () -> g.state.equals("racing") && g.myServer() != null
                && Json.arr(g.myServer().get("players")).contains("blue")));
        say("race " + g.myRace() + " players " + g.myServer().get("players") + "; the call goes on: " + k.inCallWith() + ", " + k.mediaLine());
        assertTrue("told in the call's chat", waitFor(5000, () -> { for (String l : k.chat()) if (l.contains("invites [blue] to a kart race")) return true; return false; }));
        idle(6000);
        shot(v, "9p-invited-race-call");
        tap(v, v.R_LEAVE.centerX(), v.R_LEAVE.centerY());
        assertTrue("Leave: out of the race, in the lobby", waitFor(5000, () -> g.state.equals("lobby") && g.playing));
        assertTrue("the call goes on", !k.inCallWith().isEmpty());
        tap(v, v.C_END_L.centerX(), v.C_END_L.centerY());
        assertTrue("End ends the call", waitFor(3000, () -> k.inCallWith().isEmpty()));
        julie.destroy(); julie.waitFor();
        say("blue's Communicator: " + lines(slurp(julieOut), "CALLING"));
        idle(1500);
        idle(1500);
        turn(v, false);
        shot(v, "10-lobby-after");
        assertTrue("a record for the phone", g.records().containsKey("phone"));
        say("phone's record: " + g.records().get("phone"));

        // Log, then Leave: back to the setup screen, out of the lobby
        tap(v, v.B_LOG.centerX(), v.B_LOG.centerY());
        shot(v, "11-log");
        tap(v, v.B_LOG.centerX(), v.B_LOG.centerY());
        tap(v, v.B_HOME.centerX(), v.B_HOME.centerY());
        assertEquals("home", v.screen);
        assertTrue("Home: out of the lobby", waitFor(5000, () -> !g.lobby().containsKey("phone")));
        // [SETTINGS_ARE_TUPLES_V1] Settings: pages over the home screen, each a tuple; not a member here (no MemberService
        // under Robolectric), so each page says so instead of showing values it does not have
        tap(v, v.H_SETTINGS.centerX(), v.H_SETTINGS.centerY());
        idle(1500);
        View set = a.getWindow().getDecorView().findViewWithTag("settings");
        assertNotNull("Settings opens its pages", set);
        String txt = ""; for (TextView t : all(set, TextView.class, new ArrayList<TextView>())) txt += t.getText() + " | ";
        say("Settings, Device page: " + txt.substring(0, Math.min(240, txt.length())));
        assertTrue("not joined: the page says the settings are tuples, shown once a member", txt.contains("Not joined to FrogNet yet"));
        set.findViewWithTag("tab:kart").performClick(); idle(1500);
        assertTrue("the Kart tab", txt.length() > 0 && set.findViewWithTag("tab:comms") != null);
        set.findViewWithTag("close").performClick(); idle(500);
        assertNull("Close: the home screen again", a.getWindow().getDecorView().findViewWithTag("settings"));
        assertEquals("home", v.screen);
        tap(v, v.H_SETTINGS.centerX(), v.H_SETTINGS.centerY()); idle(1000);
        a.getWindow().getDecorView().findViewWithTag("settings").findViewWithTag("disconnect").performClick();
        idle(500);
        assertTrue("Disconnect shows the setup screen", gv.get(a) == null && all(a.getWindow().getDecorView(), EditText.class, new ArrayList<EditText>()).size() == 6);
        idle(500);
        say("left: setup screen again; the Communicator: " + (cf.get(a) == null ? "left the room" : "STILL HELD"));
        // [APP_EXIT_V1] Exit (on the setup screen here; the home screen has one too): the app closes
        Button exit = null;
        for (Button b : all(a.getWindow().getDecorView(), Button.class, new ArrayList<Button>())) if (b.getText().toString().equals("Exit")) exit = b;
        assertNotNull("an Exit button", exit);
        exit.performClick(); idle(200);
        assertTrue("Exit closes the app", a.isFinishing());
        say("Exit: the app is finishing");
        ac.pause().stop().destroy();
    }
}
