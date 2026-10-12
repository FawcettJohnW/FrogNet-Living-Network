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

/**
 * One fnav plane: a non-blocking TCP connection carrying audio or video (frogcomms/fnav.py Plane, src/fnav/plane.*).
 * Whole frames only: nothing written = dropped cleanly; a part-written frame is finished within 250 ms or completed
 * with abort padding. Audio is retried twice 8 ms apart, a keyframe three times ([ONE_EWOULDBLOCK_IS_NOT_A_VERDICT_V1]).
 * The room check before the first byte needs the kernel's queue size (SIOCOUTQ), which Java cannot read: as on
 * Windows ([THE_GUARD_MUST_WORK_WHERE_THE_CLIENT_RUNS_V1]) the socket's "would block" decides, and the send buffer is
 * kept small (64 KiB) because the buffer is the latency ([THE_BUFFER_IS_THE_LATENCY_V1]).
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
    private final Object wlock = new Object();
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
    }

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

    /** One whole frame (its typed body), or nothing. Returns WHOLE, DROPPED or ABORTED. */
    public int send(byte[] body, int kind) throws Closed {
        byte[] blob = withLength(body);
        int tries = kind == AUDIO ? 1 + AUDIO_RETRIES : kind == KEYFRAME ? KEY_RETRIES : 1;
        synchronized (wlock) {
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
                ByteBuffer pad = ByteBuffer.wrap(Fnav.abortPad(b.remaining()));   // [ABORT_SENTINEL_V1]
                long t1 = System.nanoTime();
                while (pad.hasRemaining()) {
                    if ((System.nanoTime() - t1) / 1e6 >= FINISH_MS)
                        throw new Closed("a " + body.length + " byte frame: " + b.position() + "/" + blob.length
                                + " written and the abort padding would not go either; the stream is desynced");
                    if (writable(10)) trySend(pad);
                }
                abortedOut++;
                return ABORTED;
            } catch (IOException e) {
                throw new Closed("send: " + e.getMessage());
            }
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

    /** The next frame's typed body, or null on TIMEOUT / an aborted frame (state() says which). CLOSED is thrown. */
    public int recv(long timeoutMs) throws Closed {
        try {
            synchronized (rsel) {
                rsel.selectedKeys().clear();
                if (rsel.select(timeoutMs) == 0) return TIMEOUT;
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
