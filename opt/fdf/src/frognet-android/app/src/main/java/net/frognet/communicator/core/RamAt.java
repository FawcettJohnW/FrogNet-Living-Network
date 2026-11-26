// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
package net.frognet.communicator.core;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * [THE_MEMORY_MOVES_V1] Where the Communicator's memory (comms-ram) is, for this app: src/comms/ram_at.hpp.
 *
 * The Communicator is opened with the memory's address. The Role's setting may name another one: its owner moved the
 * memory (comms-channel move, or a Security tab's "Change RAM host"). The read this app holds on the setting fires,
 * the address here changes, and everything that holds a session to the memory is told: it ends that session, attaches
 * to the new address, asserts its own tuples there and sets up its held reads again. A call's audio and video go to
 * the media server, not to the memory, so they are not touched.
 */
public final class RamAt {
    private static final Object mu = new Object();          // this app's copy of the address and its list of who to tell
    private static String host = "";
    private static int port = 0, next = 0;
    private static long moves = 0;
    private static final Map<Integer, Runnable> told = new LinkedHashMap<Integer, Runnable>();

    private RamAt() { }

    /** Where the Communicator was opened. Each opening says it again: the app may be opened with another pond's servers. */
    public static void start(String h, int p) { synchronized (mu) { host = h; port = p; } }
    public static String host() { synchronized (mu) { return host; } }
    public static int port() { synchronized (mu) { return port; } }
    public static String text() { synchronized (mu) { return host + ":" + port; } }
    public static long moves() { synchronized (mu) { return moves; } }

    /** The memory is at another address now. False when it is the address this app already has. */
    public static boolean move(String h, int p) {
        List<Runnable> tell;
        synchronized (mu) {
            if (h.equals(host) && p == port) return false;
            host = h; port = p; moves++;
            tell = new ArrayList<Runnable>(told.values());
        }
        for (Runnable r : tell) r.run();                    // each ends its own session; none is called under the guard
        return true;
    }

    public static int whenMoved(Runnable r) { synchronized (mu) { told.put(++next, r); return next; } }
    public static void forget(int id) { synchronized (mu) { told.remove(id); } }
}
