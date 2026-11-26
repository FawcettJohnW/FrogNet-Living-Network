// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// AppCycleTest -- [SWITCHED_FROM_THE_CALL_V1] the app's own CommunicatorActivity (Robolectric, SDK 33) in sealed calls
// with "Home" (the Python Communicator, test_app_cycle.py) through a live comms-media, two comms-ram. What it proves,
// through the real widgets: the phone answers Home's call; with the call running it opens Channel and makes eight
// changes as the Role's owner -- cipher (ChaCha20, not sealed, AES-256-GCM), a new key, protocol (chorus, openqasm,
// tcp), and the memory moved to the other comms-ram -- each answered by all three ends, each shown in the Stats over
// the picture, with Home's audio still arriving afterwards; Home hangs up and the phone's call ends by itself; then
// the phone calls Home, changes the cipher and the key in call #2, and hangs up.
import android.app.Application;
import android.Manifest;
import android.view.View;
import android.view.ViewGroup;
import android.widget.Button;
import android.widget.EditText;
import android.widget.TextView;

import net.frognet.communicator.CommunicatorActivity;
import net.frognet.communicator.Diag;

import org.junit.Test;
import org.junit.runner.JUnitCore;
import org.junit.runner.Result;
import org.junit.runner.RunWith;
import org.junit.runner.notification.Failure;
import org.robolectric.Robolectric;
import org.robolectric.RobolectricTestRunner;
import org.robolectric.RuntimeEnvironment;
import org.robolectric.Shadows;
import org.robolectric.android.controller.ActivityController;
import org.robolectric.annotation.Config;
import org.robolectric.annotation.LooperMode;
import org.robolectric.shadows.ShadowLooper;

import java.io.File;
import java.nio.file.Files;
import java.util.ArrayList;
import java.util.List;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

import static org.junit.Assert.assertTrue;

@RunWith(RobolectricTestRunner.class)
@Config(sdk = 33, manifest = Config.NONE)
@LooperMode(LooperMode.Mode.PAUSED)
public class AppCycleTest {
    static final String RAM = System.getProperty("ram"), RAM_B = System.getProperty("ramb"), MEDIA = System.getProperty("media"), SECRET = System.getProperty("secret");
    static int fails = 0;

    static void say(String s) { System.out.println("[robo] " + s); System.out.flush(); }
    static void check(boolean ok, String what) { System.out.println((ok ? "PASS  " : "FAIL  ") + what); System.out.flush(); if (!ok) fails++; }

    static void run(long ms) {
        long end = System.currentTimeMillis() + ms;
        while (System.currentTimeMillis() < end) {
            ShadowLooper.idleMainLooper(100, java.util.concurrent.TimeUnit.MILLISECONDS);
            try { Thread.sleep(100); } catch (InterruptedException e) { return; }
        }
    }

    static void texts(View v, List<String> out) {
        if (v.getVisibility() != View.VISIBLE) return;
        if (v instanceof TextView) out.add(((TextView) v).getText().toString());
        if (v instanceof ViewGroup) for (int i = 0; i < ((ViewGroup) v).getChildCount(); i++) texts(((ViewGroup) v).getChildAt(i), out);
    }

    static String screen(CommunicatorActivity a) {
        List<String> t = new ArrayList<String>();
        texts(a.getWindow().getDecorView(), t);
        return String.join(" | ", t);
    }

    static <T extends View> T find(View v, Class<T> c, String text) {
        if (v.getVisibility() != View.VISIBLE) return null;
        if (c.isInstance(v) && (text == null || (v instanceof TextView && ((TextView) v).getText().toString().equals(text)))) return c.cast(v);
        if (v instanceof ViewGroup) for (int i = 0; i < ((ViewGroup) v).getChildCount(); i++) {
            T r = find(((ViewGroup) v).getChildAt(i), c, text); if (r != null) return r;
        }
        return null;
    }

    static boolean waitFor(CommunicatorActivity a, String what, long ms) {
        long end = System.currentTimeMillis() + ms;
        while (System.currentTimeMillis() < end) { if (screen(a).contains(what)) return true; run(200); }
        return false;
    }

    static boolean press(CommunicatorActivity a, String button) {
        Button b = find(a.getWindow().getDecorView(), Button.class, button);
        if (b == null) { say("no button '" + button + "' on: " + screen(a)); return false; }
        b.performClick();
        return true;
    }

    static String group(String s, String re) { Matcher m = Pattern.compile(re).matcher(s); return m.find() ? m.group(1) : ""; }

    /** One change from the Channel panel: the button, then every end's answer under the panel and the Stats' line. */
    static void change(CommunicatorActivity a, String button, String answer, String statsLine) {
        long t0 = System.currentTimeMillis();
        boolean pressed = press(a, button);
        boolean answered = pressed && waitFor(a, answer, 15000);
        boolean shown = answered && waitFor(a, statsLine, 5000);
        check(pressed && answered && shown, "'" + button + "' during the call: \"" + answer + "\", the Stats show \"" + statsLine.replaceAll("\\s+", " ") + "\" ("
                + (System.currentTimeMillis() - t0) + " ms from the tap)" + (pressed && answered && shown ? "" : "  SCREEN: " + screen(a)));
    }

    @Test
    public void cycle() throws Exception {
        Application app = RuntimeEnvironment.getApplication();
        // the microphone is granted: Robolectric's stand-in for it never waits, so the phone sends audio as fast as the
        // machine runs (thousands of packets a second, not 50) under every setting -- more than a real phone would
        Shadows.shadowOf(app).grantPermissions(Manifest.permission.RECORD_AUDIO);
        File sd = new File(app.getFilesDir(), "channel_secrets");               // the phone's secret, as Import secret... leaves it
        sd.mkdirs();
        Files.copy(new File(SECRET).toPath(), new File(sd, "default").toPath());
        android.content.Intent in = new android.content.Intent(app, CommunicatorActivity.class)
            .putExtra(CommunicatorActivity.X_RAM, RAM).putExtra(CommunicatorActivity.X_MEDIA, MEDIA)
            .putExtra(CommunicatorActivity.X_NAME, "Phone").putExtra(CommunicatorActivity.X_POND, "p").putExtra(CommunicatorActivity.X_FROM, "z");
        ActivityController<CommunicatorActivity> c = Robolectric.buildActivity(CommunicatorActivity.class, in).setup();
        CommunicatorActivity a = c.get();
        check(waitFor(a, "Home", 20000), "the phone sees who is there: the room lists Home");
        check(waitFor(a, "Home is calling", 30000), "Home's call rings on the phone");
        press(a, "Answer");
        check(waitFor(a, "End", 10000), "the phone answers: the call screen is up");
        press(a, "Channel");                                                    // opens the Channel panel and the Stats
        check(waitFor(a, "with Home  call #1", 10000) && waitFor(a, "cipher     aes-256-gcm  key ", 10000), "the Stats show call #1 with Home, sealed with aes-256-gcm, and the key's hash");
        run(4000);
        String k1 = group(screen(a), "aes-256-gcm  key ([0-9a-f]{8})");
        change(a, "ChaCha20", "sealed with chacha20-poly1305: 3 end(s) there: 3 running it", "cipher     chacha20-poly1305  key ");
        String k2 = group(screen(a), "chacha20-poly1305  key ([0-9a-f]{8})");
        change(a, "New key", "a new key: 3 end(s) there: 3 running it", "last       new key");
        run(600);
        String k3 = group(screen(a), "chacha20-poly1305  key ([0-9a-f]{8})");
        check(k1.length() == 8 && k2.length() == 8 && k3.length() == 8 && !k2.equals(k3) && !k1.equals(k2), "the key's hash on the screen changes with the key: " + k1 + " -> " + k2 + " -> " + k3);
        change(a, "chorus", "protocol chorus: 3 end(s) there: 3 running it", "protocol   chorus");
        change(a, "Not sealed", "not sealed: 3 end(s) there: 3 running it", "cipher     not sealed");
        change(a, "openqasm", "protocol openqasm: 3 end(s) there: 3 running it", "protocol   openqasm");
        change(a, "AES-256-GCM", "sealed with aes-256-gcm: 3 end(s) there: 3 running it", "cipher     aes-256-gcm  key ");
        change(a, "tcp", "protocol tcp: 3 end(s) there: 3 running it", "protocol   tcp");
        EditText to = find(a.getWindow().getDecorView(), EditText.class, null);
        to.setText(RAM_B);
        change(a, "Move", "the memory to " + RAM_B + ": 3 end(s) there: 3 attached there", "memory     " + RAM_B);
        run(5000);
        String s = screen(a);
        say("after the eight changes: " + s);
        int ain = -1; try { ain = Integer.parseInt(group(s, "audio\\s+in (\\d+)/s")); } catch (NumberFormatException e) { }
        check(ain >= 40 && ain <= 60, "after the eight changes Home's audio still arrives at about 50 packets a second: " + ain);
        // a frame that arrives before its switch call does not open; it is asked for again and sent again, not lost
        say("retries in call #1: " + group(s, "(sent again \\d+  not opened \\d+)"));
        int sp = -1, sk = -1; try { sp = Integer.parseInt(group(s, "switches   sent: protocol (\\d+)")); sk = Integer.parseInt(group(s, "sent: protocol \\d+  key/cipher (\\d+)")); } catch (NumberFormatException e) { }
        check(sp >= 3 && sk >= 4, "the Stats count the switch calls this phone's planes sent: protocol " + sp + ", key/cipher " + sk);
        System.out.println("[robo] READY-FOR-HOME-TO-HANG-UP"); System.out.flush();
        check(waitFor(a, "In the room", 30000) && find(a.getWindow().getDecorView(), Button.class, "End") == null, "Home hangs up: the phone's call ends by itself and it is back in the room");
        run(3000);
        // again: the phone calls Home
        check(press(a, "Call") && waitFor(a, "End", 10000), "again: the phone calls Home and the call screen is up");
        check(waitFor(a, "with Home  call #2", 20000), "the Stats show call #2 with Home");
        run(4000);
        change(a, "ChaCha20", "sealed with chacha20-poly1305: 3 end(s) there: 3 running it", "cipher     chacha20-poly1305  key ");
        change(a, "New key", "a new key: 3 end(s) there: 3 running it", "last       new key");
        run(4000);
        s = screen(a);
        say("in call #2: " + s);
        ain = -1; try { ain = Integer.parseInt(group(s, "audio\\s+in (\\d+)/s")); } catch (NumberFormatException e) { }
        check(ain >= 40 && ain <= 60, "in call #2, after two more changes, Home's audio arrives at about 50 packets a second: " + ain);
        press(a, "End");
        run(2000);
        check(screen(a).contains("In the room"), "the phone hangs up: back in the room");
        say("---- the app's own log ----");
        for (String l : Diag.lines()) say(l);
        c.pause().stop().destroy();
        run(1500);
        assertTrue(fails + " check(s) failed", fails == 0);
    }

    public static void main(String[] args) {
        Result r = JUnitCore.runClasses(AppCycleTest.class);
        for (Failure f : r.getFailures()) { System.out.println("FAILURE " + f.getMessage()); System.out.println(f.getTrace()); }
        System.out.println("ROBO " + (r.wasSuccessful() ? "PASS" : "FAIL") + " run=" + r.getRunCount() + " failed=" + r.getFailureCount());
        System.exit(r.wasSuccessful() ? 0 : 1);
    }
}
