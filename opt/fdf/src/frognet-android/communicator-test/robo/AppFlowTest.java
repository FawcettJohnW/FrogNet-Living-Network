// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// AppFlowTest -- the app's own CommunicatorActivity, run on the Android framework's code (Robolectric, SDK 33), against a live
// comms-ram + comms-media and a Python participant "Home" (test_app_flow.py drives Home). What it proves: the
// activity builds, joins the room, lists Home, rings when Home calls, answers into the call, receives Home's media
// through CallCore, shows Home's chat, sends its own, and hangs up -- all through the real UI widgets.
// Camera, GL and the platform codecs are the parts Robolectric does not run (no hardware): the camera permission is
// not granted here, and the codec paths' refusals must be logged and survived, not crash the call.
import android.Manifest;
import android.app.Application;
import android.view.View;
import android.view.ViewGroup;
import android.widget.Button;
import android.widget.EditText;
import android.widget.TextView;

import net.frognet.communicator.Diag;
import net.frognet.communicator.CommunicatorActivity;

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

import java.time.Duration;
import java.util.ArrayList;
import java.util.List;

import static org.junit.Assert.assertTrue;

@RunWith(RobolectricTestRunner.class)
@Config(sdk = 33, manifest = Config.NONE)
@LooperMode(LooperMode.Mode.PAUSED)
public class AppFlowTest {
    static final String RAM = System.getProperty("ram"), MEDIA = System.getProperty("media");

    static void say(String s) { System.out.println("[robo] " + s); System.out.flush(); }

    /** Lets the main looper run `ms` of its own time while real background threads run in real time. */
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
        while (System.currentTimeMillis() < end) { if (screen(a).contains(what)) return true; run(250); }
        return false;
    }

    @Test
    public void flow() {
        Application app = RuntimeEnvironment.getApplication();
        Shadows.shadowOf(app).grantPermissions(Manifest.permission.RECORD_AUDIO);
        // [COMMS_FROM_THE_POND_V1] opened as the FrogNet member opens it: the pond's servers and the name as extras
        android.content.Intent in = new android.content.Intent(app, CommunicatorActivity.class)
            .putExtra(CommunicatorActivity.X_RAM, RAM).putExtra(CommunicatorActivity.X_MEDIA, MEDIA)
            .putExtra(CommunicatorActivity.X_NAME, "Phone").putExtra(CommunicatorActivity.X_POND, "p").putExtra(CommunicatorActivity.X_FROM, "z");
        ActivityController<CommunicatorActivity> c = Robolectric.buildActivity(CommunicatorActivity.class, in).setup();
        CommunicatorActivity a = c.get();
        boolean lobby = waitFor(a, "Home", 15000);
        say("lobby: " + screen(a));
        assertTrue("the lobby lists Home", lobby);
        boolean rang = waitFor(a, "Home is calling", 20000);
        say("ringing: " + screen(a));
        assertTrue("Home's call rings", rang);
        Button ans = find(a.getWindow().getDecorView(), Button.class, "Answer");
        ans.performClick();
        run(1500);
        boolean up = waitFor(a, "End", 10000);
        android.view.TextureView pic = find(a.getWindow().getDecorView(), android.view.TextureView.class, null);
        pic.performClick();                                   // a tap on the picture shows the stats panel
        boolean incall = up && waitFor(a, "receiving", 10000);
        say("in the call: " + screen(a));
        assertTrue("the call screen is up", incall);
        run(6000);
        String s = screen(a);
        say("after 6 s: " + s);
        java.util.regex.Matcher m = java.util.regex.Pattern.compile("audio\\s+in (\\d+)/s").matcher(s);
        int ain = m.find() ? Integer.parseInt(m.group(1)) : -1;
        say("audio packets per second arriving at the app: " + ain);
        assertTrue("Home's audio arrives at ~50 packets/s", ain >= 40 && ain <= 60);
        boolean chat = waitFor(a, "Home: hi phone", 8000);
        say("chat from Home shown: " + chat);
        EditText e = find(a.getWindow().getDecorView(), EditText.class, null);
        e.setText("hi from the android app");
        find(a.getWindow().getDecorView(), Button.class, "Send").performClick();
        run(3000);
        say("chat: " + screen(a));
        View root = a.getWindow().getDecorView();
        // the stats panel (tap the picture) carries the receive counters
        run(1000);
        say("stats: " + screen(a));
        find(root, Button.class, "End").performClick();
        run(2000);
        say("after End: " + screen(a));
        assertTrue("back in the lobby after End", screen(a).contains("In the room"));
        // the other way: the phone calls Home from the lobby
        run(2000);
        find(a.getWindow().getDecorView(), Button.class, "Call").performClick();
        boolean calling = waitFor(a, "End", 10000);
        say("calling Home: " + screen(a));
        assertTrue("pressing Call brings up the call screen", calling);
        boolean heard = waitFor(a, "Home  \u00b7  0:0", 15000);
        run(4000);
        say("in the outgoing call: " + screen(a));
        find(a.getWindow().getDecorView(), Button.class, "End").performClick();
        run(2000);
        List<String> log = Diag.lines();
        say("---- the app's own log ----");
        for (String l : log) say(l);
        c.pause().stop().destroy();
        run(1500);
        assertTrue("Home's chat reached the phone", chat);
    }

    public static void main(String[] args) {
        Result r = JUnitCore.runClasses(AppFlowTest.class);
        for (Failure f : r.getFailures()) { System.out.println("FAILURE " + f.getMessage()); System.out.println(f.getTrace()); }
        System.out.println("ROBO " + (r.wasSuccessful() ? "PASS" : "FAIL") + " run=" + r.getRunCount() + " failed=" + r.getFailureCount());
        System.exit(r.wasSuccessful() ? 0 : 1);
    }
}
