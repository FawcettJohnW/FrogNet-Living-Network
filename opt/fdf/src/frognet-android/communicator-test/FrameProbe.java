// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.io.PrintStream;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import net.frognet.communicator.core.ChannelFrame;

/**
 * FrameProbe -- the phone's frame (core/ChannelFrame.java) driven a line at a time, so test_android_frame.py can put it
 * against the C++ library (ribbit/cpp/src/channel_frame.cpp) with no socket between them. Bytes cross as hex.
 *
 *   open ID OPENER(0|1) FAR SENDER SECRETS        -> ok
 *   setting ID ROLE none | DRIVER NUMBER PROTECTION STEP   -> ok
 *   pack ID ROLE HEX                              -> unit HEX | unit -
 *   next ID                                       -> unit HEX | unit -
 *   undo ID                                       -> ok
 *   feed ID HEX                                   -> msgs N, then N lines "m ROLE HEX"
 *   said ID                                       -> said N, then N lines
 *   counts ID                                     -> counts sent received notOpened sentAgain again_sent
 *   check ROLE SECRETS k=v ...                    -> check HEX
 *   take ROLE SECRETS k=v ...                     -> take TAKEN(0|1) MOVED(0|1) NUMBER STEP DRIVER PROTECTION FINGERPRINT|- WHY...
 *   works PROTECTION                              -> works 0|1
 * Anything that throws answers "gone TEXT".
 */
public final class FrameProbe {
    static byte[] unhex(String s) {
        if (s.equals("-")) return new byte[0];
        byte[] b = new byte[s.length() / 2];
        for (int i = 0; i < b.length; i++) b[i] = (byte) Integer.parseInt(s.substring(2 * i, 2 * i + 2), 16);
        return b;
    }
    static String hex(byte[] b) {
        if (b.length == 0) return "-";
        StringBuilder s = new StringBuilder(b.length * 2);
        for (byte x : b) { s.append("0123456789abcdef".charAt((x >> 4) & 15)); s.append("0123456789abcdef".charAt(x & 15)); }
        return s.toString();
    }
    static Map<String, String> fields(String[] a, int from) {
        Map<String, String> f = new HashMap<String, String>();
        for (int i = from; i < a.length; i++) { int e = a[i].indexOf('='); f.put(a[i].substring(0, e), a[i].substring(e + 1)); }
        return f;
    }

    public static void main(String[] args) throws Exception {
        BufferedReader in = new BufferedReader(new InputStreamReader(System.in, "UTF-8"));
        PrintStream out = new PrintStream(System.out, false, "UTF-8");
        final Map<String, ChannelFrame.Codec> ends = new HashMap<String, ChannelFrame.Codec>();
        final Map<String, List<String>> said = new HashMap<String, List<String>>();
        out.println("build " + ChannelFrame.BUILD); out.flush();
        String line;
        while ((line = in.readLine()) != null) {
            String[] a = line.split(" ");
            try {
                if (a[0].equals("open")) {
                    final List<String> lines = new ArrayList<String>();
                    said.put(a[1], lines);
                    ends.put(a[1], new ChannelFrame.Codec(a[2].equals("1"), a[3], a[4].equals("-") ? "" : a[4], a[5],
                            new ChannelFrame.Say() { public void say(String t) { synchronized (lines) { lines.add(t); } } }));
                    out.println("ok");
                } else if (a[0].equals("setting")) {
                    ends.get(a[1]).setting(a[2], a[3].equals("none") ? null
                            : new ChannelFrame.Setting(a[3], Long.parseLong(a[4]), a[5], Long.parseLong(a[6])));
                    out.println("ok");
                } else if (a[0].equals("pack")) {
                    byte[] u = ends.get(a[1]).pack(a[2], unhex(a[3]));
                    out.println("unit " + (u == null ? "-" : hex(u)));
                } else if (a[0].equals("next")) {
                    byte[] u = ends.get(a[1]).next();
                    out.println("unit " + (u == null ? "-" : hex(u)));
                } else if (a[0].equals("undo")) {
                    ends.get(a[1]).undo();
                    out.println("ok");
                } else if (a[0].equals("feed")) {
                    List<ChannelFrame.Message> got = new ArrayList<ChannelFrame.Message>();
                    byte[] b = unhex(a[2]);
                    ends.get(a[1]).feed(b, 0, b.length, got);
                    out.println("msgs " + got.size());
                    for (ChannelFrame.Message m : got) out.println("m " + m.role + " " + hex(m.data));
                } else if (a[0].equals("said")) {
                    List<String> lines = said.get(a[1]);
                    synchronized (lines) { out.println("said " + lines.size()); for (String t : lines) out.println(t); lines.clear(); }
                } else if (a[0].equals("counts")) {
                    ChannelFrame.Counts c = ends.get(a[1]).counts();
                    out.println("counts " + c.sent + " " + c.received + " " + c.notOpened + " " + c.sentAgain + " " + c.again[0]);
                } else if (a[0].equals("check")) {
                    out.println("check " + ChannelFrame.checkOf(ChannelFrame.secretAt(a[1].toLowerCase(java.util.Locale.ROOT), -1, a[2]).value, a[1], fields(a, 3)));
                } else if (a[0].equals("take")) {
                    ChannelFrame.Taken t = ChannelFrame.takeSetting(a[1], fields(a, 3), a[2]);
                    out.println("take " + (t.taken ? 1 : 0) + " " + (t.moved ? 1 : 0) + " " + t.setting.number + " " + t.setting.step + " " + t.setting.driver + " "
                            + t.setting.protection + " " + (t.fingerprint.isEmpty() ? "-" : t.fingerprint) + " " + t.why);
                } else if (a[0].equals("works")) {
                    out.println("works " + (ChannelFrame.protectionWorks(a[1]) ? 1 : 0));
                } else out.println("gone unknown command " + a[0]);
            } catch (ChannelFrame.Gone e) {
                out.println("gone " + e.getMessage().replace('\n', ' '));
            }
            out.flush();
        }
    }
}
