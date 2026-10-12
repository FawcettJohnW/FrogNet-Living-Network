// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// Probe.java -- the Android Communicator's core (net.frognet.communicator.core) on the JVM, in the roles the phone
// plays, against the real comms-ram / comms-media and the C++ and Python Communicators (test_android_core.py drives it).
//   room   RAM_PORT                                  lobby, chat, invitations, close time
//   watch  RAM_PORT FEED SECONDS DUMP                watch an unattended feed; VP8 sizes, counts, Opus packets dumped
//   call   RAM_PORT MEDIA_PORT ME CALLEE IVF OPUS SECONDS DUMP   offer + invite + join, send both, receive both
//   answer RAM_PORT ME FROM IVF OPUS SECONDS DUMP    wait for an invitation from FROM, join it, send both, receive both
//   feed   RAM_PORT MEDIA_PORT ME IVF OPUS SECONDS   an unattended feed ("*ME"), as comms-feed
import net.frognet.communicator.core.*;

import java.io.*;
import java.nio.file.Files;
import java.nio.file.Paths;
import java.util.*;

public class Probe {
    static final String H = System.getenv("PROBE_HOST") != null ? System.getenv("PROBE_HOST") : "127.0.0.1";   // the Communicator host (on the mesh: from the pond's where cell)

    public static void main(String[] a) throws Exception {
        switch (a[0]) {
            case "room": room(Integer.parseInt(a[1])); break;
            case "watch": watch(Integer.parseInt(a[1]), a[2], Integer.parseInt(a[3]), a[4]); break;
            case "call": call(Integer.parseInt(a[1]), Integer.parseInt(a[2]), a[3], a[4], a[5], a[6], Integer.parseInt(a[7]), a[8]); break;
            case "answer": answer(Integer.parseInt(a[1]), a[2], a[3], a[4], a[5], Integer.parseInt(a[6]), a[7]); break;
            case "feed": feed(Integer.parseInt(a[1]), Integer.parseInt(a[2]), a[3], a[4], a[5], Integer.parseInt(a[6])); break;
            default: throw new IllegalArgumentException(a[0]);
        }
        System.exit(0);
    }

    static Room.Logger LOG = new Room.Logger() { public void log(String l) { System.out.println(l); } };

    static void room(int port) throws Exception {
        Room r = new Room(H, port, "Phone", "video,audio", LOG);
        r.sendChat("S1", "hello from android");
        r.invite("Pythonia", "S2", H, 8994);
        long end = System.currentTimeMillis() + 8000;
        while (System.currentTimeMillis() < end) {
            boolean cplusChat = false, cplusInv = false, py = false, cp = false;
            for (Room.Line l : r.readChat("S1")) if (l.fromName.equals("Cplus") && l.text.equals("hello from c++")) cplusChat = true;
            for (Room.Offer o : r.invitations()) if (o.session.equals("S1") && o.offeredBy.equals("Cplus") && o.mediaPort == 8994) cplusInv = true;
            for (Room.Member m : r.roster()) { if (m.name.equals("Pythonia")) py = true; if (m.name.equals("Cplus")) cp = true; }
            if (cplusChat && cplusInv && py && cp) break;
            r.waitChange(r.version(), 500);
        }
        StringBuilder ros = new StringBuilder();
        for (Room.Member m : r.roster()) ros.append(m.name).append(",");
        System.out.println("ROSTER " + ros);
        for (Room.Line l : r.readChat("S1")) System.out.println("CHAT " + l.fromName + ": " + l.text);
        for (Room.Offer o : r.invitations()) System.out.println("INVITATION " + o.session + " from " + o.offeredBy + " at " + o.mediaHost + ":" + o.mediaPort);
        long t0 = System.nanoTime();
        r.close();
        System.out.printf("CLOSED %.3f%n", (System.nanoTime() - t0) / 1e9);
    }

    // ---- media helpers -------------------------------------------------------------------------------------------
    static final class Rx implements CallCore.Listener {
        final Map<String, int[]> dims = new HashMap<String, int[]>();
        final Map<String, Integer> vcount = new HashMap<String, Integer>(), acount = new HashMap<String, Integer>();
        final DataOutputStream dump; volatile String ended; int keyreqs;
        Rx(String path) throws IOException { dump = new DataOutputStream(new BufferedOutputStream(new FileOutputStream(path))); }
        public synchronized void onVideo(String src, Fnav.Video v) {
            vcount.put(src, vcount.containsKey(src) ? vcount.get(src) + 1 : 1);
            byte[] p = v.packet;                                  // VP8 keyframe: tag(3) 9d 01 2a w(2) h(2)
            if (v.codec == Fnav.CODEC_VP8 && p.length >= 10 && (p[0] & 1) == 0 && (p[3] & 0xFF) == 0x9d && (p[4] & 0xFF) == 0x01 && (p[5] & 0xFF) == 0x2a)
                dims.put(src, new int[]{((p[7] & 0x3F) << 8) | (p[6] & 0xFF), ((p[9] & 0x3F) << 8) | (p[8] & 0xFF)});
        }
        public synchronized void onAudio(String src, byte[] opus) {
            acount.put(src, acount.containsKey(src) ? acount.get(src) + 1 : 1);
            try { dump.writeUTF(src); dump.writeInt(opus.length); dump.write(opus); } catch (IOException e) { throw new RuntimeException(e); }
        }
        public void onKeyRequest() { keyreqs++; }
        public void onPlaneEnded(String plane, String why) { ended = plane + ": " + why; }
        synchronized void report() throws IOException {
            dump.flush();
            for (String s : vcount.keySet()) {
                int[] d = dims.get(s);
                System.out.println("VIDEO " + s + " frames=" + vcount.get(s) + " keyframe_size=" + (d == null ? "none" : d[0] + "x" + d[1]));
            }
            for (String s : acount.keySet()) System.out.println("AUDIO " + s + " packets=" + acount.get(s));
            if (ended != null) System.out.println("ENDED " + ended);
        }
    }

    /** IVF frames (VP8 from ffmpeg/libvpx) and length-prefixed Opus packets: what the phone's encoders would make. */
    static List<byte[]> ivf(String path) throws IOException {
        byte[] f = Files.readAllBytes(Paths.get(path));
        List<byte[]> out = new ArrayList<byte[]>();
        int o = 32;
        while (o + 12 <= f.length) {
            int n = (f[o] & 0xFF) | (f[o + 1] & 0xFF) << 8 | (f[o + 2] & 0xFF) << 16 | (f[o + 3] & 0xFF) << 24;
            out.add(Arrays.copyOfRange(f, o + 12, o + 12 + n)); o += 12 + n;
        }
        return out;
    }

    static List<byte[]> opus(String path) throws IOException {
        DataInputStream in = new DataInputStream(new BufferedInputStream(new FileInputStream(path)));
        List<byte[]> out = new ArrayList<byte[]>();
        while (in.available() > 0) { byte[] p = new byte[in.readInt()]; in.readFully(p); out.add(p); }
        return out;
    }

    /** Sends both media for `seconds` the way the phone does: one tick per camera frame, audio every 20 ms. */
    static void send(final CallCore c, String ivfPath, String opusPath, int seconds) throws Exception {
        final List<byte[]> v = ivf(ivfPath), au = opus(opusPath);
        final long end = System.currentTimeMillis() + seconds * 1000L;
        Thread at = new Thread(new Runnable() { public void run() {
            long next = System.nanoTime(); int k = 0;
            try {
                while (System.currentTimeMillis() < end) {
                    c.sendAudio(au.get(k++ % au.size()));
                    next += 20_000_000L;
                    long s = next - System.nanoTime(); if (s > 0) Thread.sleep(s / 1_000_000L, (int) (s % 1_000_000L));
                }
            } catch (Exception e) { System.out.println("AUDIO-TX ended: " + e.getMessage()); }
        } });
        at.start();
        c.startVideo(1280, 720);
        long next = System.nanoTime(); int k = 0, ticks = 0, keyAnswers = 0; Map<Integer, Integer> sizes = new TreeMap<Integer, Integer>();
        while (System.currentTimeMillis() < end) {
            int size = c.tick(); ticks++;
            sizes.put(size, sizes.containsKey(size) ? sizes.get(size) + 1 : 1);
            if (c.takeWantKey()) {                              // KEYREQ: as an encoder would, the next frame is a keyframe
                int j = k; while ((v.get(j % v.size())[0] & 1) != 0) j++;
                k = j; keyAnswers++;
            }
            byte[] f = v.get(k % v.size()); boolean key = (f[0] & 1) == 0;
            // the pre-encoded stream is 1280x720 only: it is sent at L7 whatever the size, as a phone at its ceiling
            c.sendEncoded(f, key, Ladder.SIZES[1]);
            k++;
            next += 1_000_000_000L / 24;
            long s = next - System.nanoTime(); if (s > 0) Thread.sleep(s / 1_000_000L, (int) (s % 1_000_000L));
        }
        at.join();
        System.out.println("SENT video=" + c.stats.vSent + " dropped=" + c.stats.vDropped + " keys=" + c.stats.keysSent
                + " audio=" + c.stats.aSent + " shed=" + c.stats.aShed + " ticks=" + ticks + " keyreqs_answered=" + keyAnswers + " sizes=" + sizes + " rung=" + c.stats.rung);
    }

    static void watch(int ram, String feedName, int seconds, String dump) throws Exception {
        Room r = new Room(H, ram, "Phone", "video,audio", LOG);
        Room.Offer off = null;
        long end = System.currentTimeMillis() + 5000;
        while (off == null && System.currentTimeMillis() < end) {
            List<String> on = r.callsOf(feedName);
            for (Room.Offer o : r.offers()) if (on.contains(o.session)) off = o;
            if (off == null) r.waitChange(r.version(), 500);
        }
        if (off == null) { System.out.println("NO-OFFER from " + feedName); r.close(); return; }
        System.out.println("WATCHING " + off.session + " at " + off.mediaHost + ":" + off.mediaPort);
        r.joinCall(off.session, off.mediaHost, off.mediaPort);
        Rx rx = new Rx(dump);
        CallCore c = new CallCore(off.mediaHost, off.mediaPort, "Phone", off.session, 24, rx);
        c.start();
        Thread.sleep(seconds * 1000L);
        c.stop(); rx.report(); r.close();
    }

    static void call(int ram, int media, String me, String callee, String ivf, String opus, int seconds, String dump) throws Exception {
        Room r = new Room(H, ram, me, "video,audio", LOG);
        String s = Room.newSession();
        r.offerCall(s, H, media); r.invite(callee, s, H, media); r.joinCall(s, H, media);
        r.announce("in a call");
        System.out.println("CALLING " + callee + " session " + s);
        Rx rx = new Rx(dump);
        CallCore c = new CallCore(H, media, me, s, 24, rx);
        c.start();
        r.sendChat(s, "hi from the phone");
        send(c, ivf, opus, seconds);
        for (Room.Line l : r.readChat(s)) System.out.println("CHAT " + l.fromName + ": " + l.text);
        c.stop(); rx.report(); r.leaveCall(s); r.close();
    }

    static void answer(int ram, String me, String from, String ivf, String opus, int seconds, String dump) throws Exception {
        Room r = new Room(H, ram, me, "video,audio", LOG);
        Room.Offer inv = null;
        long end = System.currentTimeMillis() + 20000;
        while (inv == null && System.currentTimeMillis() < end) {
            for (Room.Offer o : r.invitations()) if (o.offeredBy.equals(from)) inv = o;
            if (inv == null) r.waitChange(r.version(), 500);
        }
        if (inv == null) { System.out.println("NO-INVITATION"); r.close(); return; }
        System.out.println("ANSWERING " + inv.offeredBy + " session " + inv.session + " at " + inv.mediaHost + ":" + inv.mediaPort);
        r.joinCall(inv.session, inv.mediaHost, inv.mediaPort);
        r.announce("in a call");
        Rx rx = new Rx(dump);
        CallCore c = new CallCore(inv.mediaHost, inv.mediaPort, me, inv.session, 24, rx);
        c.start();
        send(c, ivf, opus, seconds);
        c.stop(); rx.report(); r.leaveCall(inv.session); r.close();
    }

    static void feed(int ram, int media, String me, String ivf, String opus, int seconds) throws Exception {
        Room r = new Room(H, ram, "*" + me, "cam=1,mic=1,unattended=1", LOG);
        String s = Room.newSession();
        r.offerCall(s, H, media); r.joinCall(s, H, media);
        Rx rx = new Rx("/dev/null");
        CallCore c = new CallCore(H, media, me, s, 24, rx);
        c.start();
        System.out.println("FEEDING session " + s);
        send(c, ivf, opus, seconds);
        c.stop(); r.close();
    }
}
