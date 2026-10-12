// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
package net.frognet.communicator;

import android.util.Log;

import java.text.SimpleDateFormat;
import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Date;
import java.util.List;
import java.util.Locale;

/** The app's own log: every line to logcat (tag FrogComms) AND to a ring the Diagnostics screen shows, so what the phone
 *  did is readable on the phone itself, no adb needed. The build is the first line. */
public final class Diag {
    public static final String BUILD = "comms-android-2026-10-11-r5";   // r5: r2's call screen (full screen, draggable inset, fading controls, fill/fit) on r4 (r4: logs which camera and its facing; r3: rings only while offered; here-clock)
    private static final ArrayDeque<String> RING = new ArrayDeque<String>();
    private static final int MAX = 600;
    private static final SimpleDateFormat TS = new SimpleDateFormat("HH:mm:ss.SSS", Locale.ROOT);

    private Diag() { }

    public static void log(String s) {
        Log.i("FrogComms", s);
        String line;
        synchronized (TS) { line = TS.format(new Date()) + " " + s; }
        synchronized (RING) {
            RING.addLast(line);
            while (RING.size() > MAX) RING.pollFirst();
        }
    }

    public static List<String> lines() {
        synchronized (RING) { return new ArrayList<String>(RING); }
    }
}
