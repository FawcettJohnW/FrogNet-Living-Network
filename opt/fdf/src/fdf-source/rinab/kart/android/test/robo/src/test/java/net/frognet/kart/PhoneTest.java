package net.frognet.kart;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertNotNull;
import static org.junit.Assert.assertTrue;
import static org.robolectric.Shadows.shadowOf;

import android.app.AlertDialog;
import android.graphics.Bitmap;
import android.graphics.Canvas;
import android.graphics.Color;
import android.os.Looper;
import android.os.SystemClock;
import android.view.MotionEvent;
import android.view.View;
import android.view.ViewGroup;
import android.widget.Button;
import android.widget.EditText;
import android.widget.TextView;

import net.frognet.kart.core.Game;

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

    static void idle(long ms) throws Exception {
        long end = System.currentTimeMillis() + ms;
        while (System.currentTimeMillis() < end) { shadowOf(Looper.getMainLooper()).idle(); Thread.sleep(20); }
    }

    interface Until { boolean now() throws Exception; }
    static boolean waitFor(long ms, Until u) throws Exception {
        long end = System.currentTimeMillis() + ms;
        while (System.currentTimeMillis() < end) { shadowOf(Looper.getMainLooper()).idle(); if (u.now()) return true; Thread.sleep(50); }
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
        assertEquals("setup: name, kart memory, robots, laps", 4, fields.size());
        say("setup screen: name '" + fields.get(0).getText() + "', kart memory '" + fields.get(1).getText() + "', robots " + fields.get(2).getText() + ", laps " + fields.get(3).getText());
        assertEquals("165.232.153.144:8799", fields.get(1).getText().toString());
        fields.get(0).setText("phone"); fields.get(1).setText(kart); fields.get(2).setText("1"); fields.get(3).setText("2");
        Button join = null;
        for (Button b : all(root, Button.class, new ArrayList<Button>())) if (b.getText().toString().equals("Join the lobby")) join = b;
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
        assertTrue("in the lobby", waitFor(10000, () -> g.memoryState() == null && g.lobby().containsKey("phone") && g.agreed() != null));
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

        // Ready
        tap(v, GameView.B_READY.centerX(), GameView.B_READY.centerY());
        assertTrue("Ready", g.ready());
        idle(800);
        shot(v, "2-after-ready");
        assertTrue("the race starts and the phone is placed on its grid", waitFor(40000, () -> g.state.equals("racing") && g.placed()));
        say("racing: race " + g.myRace() + " on " + g.myServer().get("host") + ", " + g.lapsTotal() + " lap(s)");
        tap(v, GameView.R_AUTO.centerX(), GameView.R_AUTO.centerY());
        assertTrue("Autopilot on", g.autodrive);
        assertTrue("the start", waitFor(15000, () -> System.currentTimeMillis() / 1000.0 > g.startAt() + 3));
        Bitmap race = shot(v, "3-race");
        assertTrue("the race draws", distinct(race) > 8);
        assertTrue("other karts are seen", waitFor(5000, () -> !g.others(System.currentTimeMillis() / 1000.0).isEmpty()));
        say("other karts seen: " + g.others(System.currentTimeMillis() / 1000.0).keySet() + "; me lap " + g.me.lap + " at (" + (int) g.me.x + "," + (int) g.me.y + ")");
        idle(6000);
        shot(v, "4-race-later");
        say("me lap " + g.me.lap + " at (" + (int) g.me.x + "," + (int) g.me.y + ")");
        assertTrue("the race ends and the phone is back in the lobby", waitFor(120000, () -> g.state.equals("lobby") && g.myRace() == null));
        say("race over: " + g.lastResult());
        idle(1500);
        shot(v, "5-lobby-after");
        assertTrue("a record for the phone", g.records().containsKey("phone"));
        say("phone's record: " + g.records().get("phone"));

        // Log, then Leave: back to the setup screen, out of the lobby
        tap(v, GameView.B_LOG.centerX(), GameView.B_LOG.centerY());
        shot(v, "6-log");
        tap(v, GameView.B_LOG.centerX(), GameView.B_LOG.centerY());
        tap(v, GameView.B_LEAVE.centerX(), GameView.B_LEAVE.centerY());
        idle(500);
        assertTrue("Leave shows the setup screen", gv.get(a) == null && all(a.getWindow().getDecorView(), EditText.class, new ArrayList<EditText>()).size() == 4);
        say("left: setup screen again");
        ac.pause().stop().destroy();
    }
}
