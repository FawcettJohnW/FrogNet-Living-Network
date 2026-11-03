// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
// OwnedCells HOST PORT API -- [RAM_LIBRARY_JAVA_V1] against a running fdf-ram: a Follower sees a tuple a session wrote
// with own=true, and sees it gone ({}) when that session ends; one written without own stays.
import net.frognet.ram.*;
import java.util.*;
public class OwnedCells {
    static int fails = 0;
    static void check(boolean ok, String what) { System.out.println((ok ? "PASS  " : "FAIL  ") + what); if (!ok) fails++; }
    public static void main(String[] a) throws Exception {
        String h = a[0], api = a[2]; int p = Integer.parseInt(a[1]);
        final Object lock = new Object();
        Follower r = new Follower("reader", h, p, api, "t", s -> {}, null, () -> { synchronized (lock) { lock.notifyAll(); } });
        Follower w = new Follower("writer", h, p, api, "t", s -> System.out.println("writer: " + s), null, null);
        long t0 = System.currentTimeMillis(); while (!w.up() || !r.up()) { Thread.sleep(50); if (System.currentTimeMillis() - t0 > 5000) break; }
        w.write("k", "mine", Json.o("x", 1), true); w.write("k", "kept", Json.o("x", 2), false);
        t0 = System.currentTimeMillis(); while (r.family("k").size() < 2 && System.currentTimeMillis() - t0 < 3000) Thread.sleep(20);
        check(r.family("k").containsKey("mine") && r.family("k").containsKey("kept"), "1 the reader holds both tuples: " + r.family("k"));
        long t1 = System.currentTimeMillis(); w.stop();
        while (r.family("k").containsKey("mine") && System.currentTimeMillis() - t1 < 3000) Thread.sleep(10);
        check(!r.family("k").containsKey("mine"), "2 the owned tuple went with its writer's session, " + (System.currentTimeMillis() - t1) + " ms after it ended");
        check(r.family("k").containsKey("kept"), "3 the tuple written without own stays: " + r.family("k"));
        r.stop(); System.out.println(fails == 0 ? "RESULT PASS" : "RESULT FAIL: " + fails); System.exit(fails);
    }
}
