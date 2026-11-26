// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
package net.frognet.communicator.core;

import java.io.IOException;
import java.net.InetSocketAddress;
import java.net.StandardSocketOptions;
import java.nio.ByteBuffer;
import java.nio.channels.SelectionKey;
import java.nio.channels.Selector;
import java.nio.channels.SocketChannel;
import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.List;

/**
 * One fnav plane: a non-blocking TCP connection carrying audio or video (frogcomms/fnav.py Plane, src/fnav/plane.*).
 * Whole frames only: nothing written = dropped cleanly; a part-written frame is finished within 250 ms or completed
 * with abort padding. Audio is retried twice 8 ms apart, a keyframe three times ([ONE_EWOULDBLOCK_IS_NOT_A_VERDICT_V1]).
 * The room check before the first byte needs the kernel's queue size (SIOCOUTQ), which Java cannot read: as on
 * Windows ([THE_GUARD_MUST_WORK_WHERE_THE_CLIENT_RUNS_V1]) the socket's "would block" decides, and the send buffer is
 * kept small (64 KiB) because the buffer is the latency ([THE_BUFFER_IS_THE_LATENCY_V1]).
 *
 * [ONE_WRITER_PER_PLANE_V1] The connection's wire has one owner: this plane's writer thread. send() hands it the body
 * and waits for what became of it; nothing else writes the socket, so nothing locks it. The only guarded thing is the
 * list of bodies handed over.
 *
 * [THE_CALL_IS_UNDER_THE_FRAME_V1] With setChannel(), every body goes inside the protocol layer's frame
 * (ChannelFrame): sealed under the Role's key, packed by the Role's protocol, and switched in the stream when the
 * Role's setting changes. A unit that did not go whole is taken back from the frame (undo), so the far end's frame
 * state is fed exactly the units that arrived whole. [A_WHOLE_UNIT_SAYS_SO_V1] Every unit under the frame ends in one
 * byte, 0, which abort padding never ends in. The writer also sends what the frame owes (a switch, a request to send
 * again, a repeat) when no body is being sent.
 */
public final class Plane {
    public static final int SNDBUF = 1 << 16;
    public static final int AUDIO = 0, KEYFRAME = 1, VIDEO = 2, CONTROL = 3;
    public static final int WHOLE = 0, DROPPED = 1, ABORTED = 2;
    public static final int FRAME = 0, ABORTED_IN = 1, CLOSED = 2, TIMEOUT = 3;
    static final int AUDIO_RETRIES = 2, KEY_RETRIES = 3, RETRY_WAIT_MS = 8, FINISH_MS = 250;

    public static final class Closed extends Exception { public Closed(String m) { super(m); } }

    private final SocketChannel ch;
    private final Selector wsel, rsel;
    static final byte WHOLE_UNIT = 0;
    private static final class Job { final byte[] body; final int kind; int result = DROPPED; boolean done; String error; Job(byte[] b, int k) { body = b; kind = k; } }
    private final Object hand = new Object();                 // the bodies handed to the writer; never held while writing
    private final ArrayDeque<Job> jobs = new ArrayDeque<Job>();
    private boolean owedPending = false;
    private volatile boolean stopWriter = false;
    private volatile String broken = null;                    // why the writer could not go on, once it cannot
    private Thread writer;
    private volatile ChannelFrame.Codec codec;                // this plane's end of the frame; null: bodies go as they are
    private String role = "";
    private final ArrayDeque<byte[]> ready = new ArrayDeque<byte[]>();   // the reader's: messages opened and not yet returned
    public final Fnav.RateMeter rate = new Fnav.RateMeter();
    private int vsegId = 0;
    public volatile long framesSent, bytesSent, dropped, abortedOut, framesRecv, bytesRecv, abortedIn, framesShed;
    private byte[] lastBody = new byte[0];

    private Plane(SocketChannel ch) throws IOException {
        this.ch = ch;
        ch.setOption(StandardSocketOptions.SO_SNDBUF, SNDBUF);
        ch.setOption(StandardSocketOptions.TCP_NODELAY, true);
        ch.setOption(StandardSocketOptions.SO_KEEPALIVE, true);
        ch.configureBlocking(false);
        wsel = Selector.open(); rsel = Selector.open();
        ch.register(wsel, SelectionKey.OP_WRITE);
        ch.register(rsel, SelectionKey.OP_READ);
        writer = new Thread(new Runnable() { public void run() { writer(); } }, "plane-writer");
        writer.setDaemon(true);
        writer.start();
    }

    /**
     * This plane's end of the frame, and the Role its bodies are for. Before the first send or recv. The codec tells
     * the writer when the reader made it owe the far end something.
     */
    public void setChannel(ChannelFrame.Codec codec, String role) {
        this.role = role;
        codec.whenOwing(new Runnable() { public void run() { synchronized (hand) { owedPending = true; hand.notifyAll(); } } });
        this.codec = codec;
    }
    public boolean framed() { return codec != null; }

    /** [A_HANG_IS_THE_WORST_REPORT_V1] connecting is bounded. */
    public static Plane connect(String host, int port, int timeoutMs) throws Closed {
        try {
            SocketChannel ch = SocketChannel.open();
            ch.socket().connect(new InetSocketAddress(host, port), timeoutMs);
            return new Plane(ch);
        } catch (IOException e) {
            throw new Closed("connect " + host + ":" + port + ": " + e.getMessage());
        }
    }

    public void shutdown() {
        try { ch.shutdownInput(); } catch (IOException ignored) { }
        try { ch.shutdownOutput(); } catch (IOException ignored) { }
    }

    public void close() {
        stopWriter = true;
        synchronized (hand) { hand.notifyAll(); }
        shutdown();
        try { ch.close(); } catch (IOException ignored) { }
        try { wsel.close(); } catch (IOException ignored) { }
        try { rsel.close(); } catch (IOException ignored) { }
    }

    private boolean writable(long ms) throws IOException {
        synchronized (wsel) {
            wsel.selectedKeys().clear();
            return (ms <= 0 ? wsel.selectNow() : wsel.select(ms)) > 0;
        }
    }

    private int trySend(ByteBuffer b) throws Closed {
        try { return ch.write(b); }
        catch (IOException e) { throw new Closed("send: " + e.getMessage()); }
    }

    private static byte[] withLength(byte[] body) {
        byte[] b = new byte[4 + body.length];
        RamSession.putU32(b, 0, body.length);
        System.arraycopy(body, 0, b, 4, body.length);
        return b;
    }

    /** One whole frame (its typed body), or nothing. Returns WHOLE, DROPPED or ABORTED. The writer thread does the writing. */
    public int send(byte[] body, int kind) throws Closed {
        Job j = new Job(body, kind);
        synchronized (hand) {
            if (broken != null) throw new Closed(broken);
            jobs.addLast(j);
            hand.notifyAll();
        }
        synchronized (j) {
            while (!j.done) { try { j.wait(); } catch (InterruptedException e) { Thread.currentThread().interrupt(); throw new Closed("interrupted"); } }
        }
        if (j.error != null) throw new Closed(j.error);
        return j.result;
    }

    // ---- the writer's: only the plane's one writer thread runs these or writes the socket ---------------------------
    private void writer() {
        boolean refused = false;
        while (!stopWriter) {
            Job j;
            synchronized (hand) {
                while (jobs.isEmpty() && !owedPending && !stopWriter) {
                    // something owed that the socket would not take: wait a moment for room, then try again
                    try { if (refused) { hand.wait(RETRY_WAIT_MS); break; } hand.wait(); } catch (InterruptedException e) { return; }
                }
                if (stopWriter) break;
                j = jobs.pollFirst();
                owedPending = false;
            }
            String error = null;
            try {
                refused = !writeOwed();
                if (j != null) j.result = writeBody(j.body, j.kind);
            } catch (Closed e) { error = e.getMessage(); }
            catch (ChannelFrame.Gone e) { error = "the frame: " + e.getMessage(); }
            catch (java.nio.channels.ClosedSelectorException e) { error = "closed"; }
            if (error != null) { breakPlane(error); if (j != null) j.error = error; }
            if (j != null) synchronized (j) { j.done = true; j.notifyAll(); }
            if (error != null) break;
        }
        String why = broken != null ? broken : "closed";
        synchronized (hand) {
            if (broken == null) broken = why;
            for (Job j : jobs) synchronized (j) { j.error = why; j.done = true; j.notifyAll(); }
            jobs.clear();
        }
    }

    /** [A_BROKEN_PLANE_IS_CLOSED_V1] the writer cannot go on, so the connection is over and both ends learn it now. */
    private void breakPlane(String why) {
        synchronized (hand) { if (broken == null) broken = why; }
        shutdown();
    }

    private static byte[] withTrailer(byte[] unit) {
        byte[] u = new byte[unit.length + 1];
        System.arraycopy(unit, 0, u, 0, unit.length);
        u[unit.length] = WHOLE_UNIT;
        return u;
    }

    /** What this end's frame owes the far end: calls and repeats. False: one did not go, and is taken back for later. */
    private boolean writeOwed() throws Closed, ChannelFrame.Gone {
        ChannelFrame.Codec cd = codec;
        if (cd == null) return true;
        for (;;) {
            byte[] unit = cd.next();
            if (unit == null) return true;
            if (sendUnit(withTrailer(unit), AUDIO) != WHOLE) { cd.undo(); return false; }
        }
    }

    private int writeBody(byte[] body, int kind) throws Closed, ChannelFrame.Gone {
        ChannelFrame.Codec cd = codec;
        if (cd == null) return sendUnit(body, kind);
        byte[] unit = cd.pack(role, body);
        if (unit == null) { dropped++; return DROPPED; }          // messages are being sent again: they go first
        int r = sendUnit(withTrailer(unit), kind);
        if (r != WHOLE) cd.undo();                                // the far end never sees it, so this end's frame forgets it
        return r;
    }

    /** What goes on the wire: one length, one unit. Whole, or nothing, or abort padding to the declared length. */
    private int sendUnit(byte[] body, int kind) throws Closed {
        byte[] blob = withLength(body);
        int tries = kind == AUDIO ? 1 + AUDIO_RETRIES : kind == KEYFRAME ? KEY_RETRIES : 1;
        ByteBuffer b = ByteBuffer.wrap(blob);
        try {
            for (int t = 0; t < tries && b.position() == 0; t++) {
                if (t > 0 && !writable(RETRY_WAIT_MS)) continue;
                trySend(b);
            }
            if (b.position() == 0) { dropped++; return DROPPED; }
            long t0 = System.nanoTime();                       // [WHOLE_FRAME_SEND_V1] committed: finish, bounded
            while (b.hasRemaining() && (System.nanoTime() - t0) / 1e6 < FINISH_MS)
                if (writable(10)) trySend(b);
            if (!b.hasRemaining()) {
                framesSent++; bytesSent += blob.length;
                rate.onAccepted(blob.length, System.nanoTime() / 1e9);
                return WHOLE;
            }
            // [ABORT_SENTINEL_V1] [THE_LENGTH_IS_NEVER_PADDED_V1] with fewer than the 4 length bytes gone, the rest of
            // the length goes as it is and only the body is padding
            int sent = b.position();
            byte[] rest;
            if (sent >= 4) rest = Fnav.abortPad(b.remaining());
            else {
                byte[] bodyPad = Fnav.abortPad(blob.length - 4);
                rest = new byte[(4 - sent) + bodyPad.length];
                System.arraycopy(blob, sent, rest, 0, 4 - sent);
                System.arraycopy(bodyPad, 0, rest, 4 - sent, bodyPad.length);
            }
            ByteBuffer pad = ByteBuffer.wrap(rest);
            long t1 = System.nanoTime();
            while (pad.hasRemaining()) {
                if ((System.nanoTime() - t1) / 1e6 >= FINISH_MS)
                    throw new Closed("a " + body.length + " byte frame: " + sent + "/" + blob.length
                            + " written and the abort padding would not go either; the stream is desynced");
                if (writable(10)) trySend(pad);
            }
            abortedOut++;
            return ABORTED;
        } catch (IOException e) {
            throw new Closed("send: " + e.getMessage());
        }
    }

    /** A VIDEO payload: whole if it fits wholeFrameMax() at the accepted rate, else VSEG pieces; true if it all went. */
    public boolean sendVideo(String src, byte[] vp, boolean key) throws Closed {
        int kind = key ? KEYFRAME : VIDEO;
        try {
            if (vp.length <= Fnav.wholeFrameMax(rate.rate, SNDBUF * 2)) {
                if (send(Fnav.packTyped(Fnav.KIND_VIDEO, src, vp), kind) == WHOLE) return true;
                framesShed++;
                return false;
            }
            vsegId = (vsegId + 1) & 0xFFFF;
            int fid = vsegId, idx = 0, off = 0;
            while (off < vp.length) {
                int n = Math.min(Fnav.VSEG_BYTES, vp.length - off);
                off += n;
                int flags = off >= vp.length ? Fnav.VSEG_LAST : 0;
                if (send(Fnav.packTyped(Fnav.KIND_VSEG, src, Fnav.packSeg(fid, idx, flags, vp, off - n, n)), kind) != WHOLE) {
                    send(Fnav.packTyped(Fnav.KIND_VSEG, src, Fnav.packSeg(fid, idx, Fnav.VSEG_ABORT, vp, 0, 0)), AUDIO);  // one shed
                    framesShed++;
                    return false;
                }
                idx++;
            }
            return true;
        } catch (Fnav.FrameError e) {
            throw new Closed(e.getMessage());
        }
    }

    private void readExact(ByteBuffer b, boolean midFrame) throws Closed {
        try {
            while (b.hasRemaining()) {
                int n = ch.read(b);
                if (n < 0) {                                         // [RX_SAYS_WHERE_V1]
                    if (!midFrame && b.position() == 0) throw new Closed("closed");
                    throw new Closed("peer closed mid-frame: " + b.position() + "/" + b.capacity() + " bytes (truncation, not a hang-up)");
                }
                if (n == 0) synchronized (rsel) { rsel.selectedKeys().clear(); rsel.select(100); }
            }
        } catch (IOException e) {
            throw new Closed("recv: " + e.getMessage());
        }
    }

    /** The next frame's typed body: FRAME (body() has it), TIMEOUT, ABORTED_IN or CLOSED. Under the frame, the next message that opened. */
    public int recv(long timeoutMs) throws Closed {
        ChannelFrame.Codec cd = codec;
        if (cd == null) return recvUnit(timeoutMs);
        long until = System.nanoTime() + timeoutMs * 1000000L;
        for (;;) {
            byte[] m = ready.pollFirst();
            if (m != null) { lastBody = m; return FRAME; }
            long left = (until - System.nanoTime()) / 1000000L;
            int st = recvUnit(Math.max(0, left));
            if (st != FRAME) return st;
            byte[] unit = lastBody;
            // [A_WHOLE_UNIT_SAYS_SO_V1] a unit that does not end in the byte that says "whole" was abandoned part-way
            if (unit.length == 0 || unit[unit.length - 1] != WHOLE_UNIT) { abortedIn++; return ABORTED_IN; }
            List<ChannelFrame.Message> got = new ArrayList<ChannelFrame.Message>();
            try { cd.feed(unit, 0, unit.length - 1, got); }
            catch (ChannelFrame.Gone e) { throw new Closed("the frame: " + e.getMessage()); }
            for (ChannelFrame.Message x : got) ready.addLast(x.data);
            if (ready.isEmpty() && left <= 0) return TIMEOUT;     // a unit of calls only
        }
    }

    private int recvUnit(long timeoutMs) throws Closed {
        try {
            synchronized (rsel) {
                rsel.selectedKeys().clear();
                if ((timeoutMs <= 0 ? rsel.selectNow() : rsel.select(timeoutMs)) == 0) return TIMEOUT;
            }
        } catch (IOException e) {
            throw new Closed("recv: " + e.getMessage());
        }
        ByteBuffer h = ByteBuffer.allocate(4);
        try { readExact(h, false); }
        catch (Closed c) { if ("closed".equals(c.getMessage())) return CLOSED; throw c; }
        long n = RamSession.u32(h.array(), 0);
        if (n > (64 << 20)) throw new Closed("a frame of " + n + " bytes: the stream is desynced");
        ByteBuffer body = ByteBuffer.allocate((int) n);
        readExact(body, true);                                       // the declared length is ALWAYS consumed
        byte[] bb = body.array();
        if (Fnav.isAbort(bb)) { abortedIn++; return ABORTED_IN; }
        framesRecv++; bytesRecv += 4 + n;
        lastBody = bb;
        return FRAME;
    }

    /** The body of the frame recv() just returned FRAME for. */
    public byte[] body() { return lastBody; }
}
