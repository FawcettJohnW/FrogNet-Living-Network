// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
package net.frognet.communicator;

import android.media.MediaCodec;
import android.media.MediaCodecInfo;
import android.media.MediaCodecList;
import android.media.MediaFormat;
import android.os.Bundle;
import android.view.Surface;

import net.frognet.communicator.core.Ladder;

import java.io.IOException;
import java.nio.ByteBuffer;

/**
 * VP8 at one ladder size, fed by a Surface (the GL renderer draws into it). VP8 because the Communicator's receivers
 * and the media server's transcoder decode VP8 (src/comms/call.cpp, src/media/transcode.cpp). The Android CDD requires a
 * VP8 encoder on every device; the platform's software one is preferred because it takes every ladder size (854x480
 * included) and holds a low bitrate. A keyframe is asked for at once on KEYREQ: a sender that ignores KEYREQ leaves a
 * new viewer unanchored and the media server scores it at ~44% delivered and transcodes it down (measured 2026-10-10).
 * The packet's key flag is read from the VP8 bitstream itself ([KEYFRAME_ON_REQUEST_V1]: never assume).
 */
final class VideoEncoder {
    interface Sink { void onPacket(byte[] pkt, boolean key, Ladder.Size s); }

    static final String VP8 = MediaFormat.MIMETYPE_VIDEO_VP8;
    final Ladder.Size size;
    final Surface input;
    private final MediaCodec codec;
    private volatile boolean run = true;
    private final Thread drain;
    volatile long packets, keys, bytes;

    static String pick(int w, int h) {
        String soft = null, hard = null;
        for (MediaCodecInfo ci : new MediaCodecList(MediaCodecList.REGULAR_CODECS).getCodecInfos()) {
            if (!ci.isEncoder()) continue;
            boolean vp8 = false;
            for (String t : ci.getSupportedTypes()) if (t.equalsIgnoreCase(VP8)) vp8 = true;
            if (!vp8) continue;
            MediaCodecInfo.VideoCapabilities vc = ci.getCapabilitiesForType(VP8).getVideoCapabilities();
            if (vc == null || !vc.isSizeSupported(w, h)) continue;
            if (ci.isSoftwareOnly()) { if (soft == null) soft = ci.getName(); }
            else if (hard == null) hard = ci.getName();
        }
        return soft != null ? soft : hard;
    }

    VideoEncoder(Ladder.Size s, int fps, final Sink sink) throws IOException {
        size = s;
        String name = pick(s.w, s.h);
        if (name == null) throw new IOException("no VP8 encoder on this phone takes " + s);
        codec = MediaCodec.createByCodecName(name);
        MediaFormat f = MediaFormat.createVideoFormat(VP8, s.w, s.h);
        f.setInteger(MediaFormat.KEY_COLOR_FORMAT, MediaCodecInfo.CodecCapabilities.COLOR_FormatSurface);
        f.setInteger(MediaFormat.KEY_BIT_RATE, s.bitrate);
        f.setInteger(MediaFormat.KEY_FRAME_RATE, fps);
        f.setInteger(MediaFormat.KEY_I_FRAME_INTERVAL, 2);        // [KEYFRAME_INTERVAL_IS_BOUNDED_V1] g = fps * 2
        MediaCodecInfo.EncoderCapabilities ec = codec.getCodecInfo().getCapabilitiesForType(VP8).getEncoderCapabilities();
        boolean cbr = ec.isBitrateModeSupported(MediaCodecInfo.EncoderCapabilities.BITRATE_MODE_CBR);
        if (cbr) f.setInteger(MediaFormat.KEY_BITRATE_MODE, MediaCodecInfo.EncoderCapabilities.BITRATE_MODE_CBR);   // the budget held per window
        f.setInteger(MediaFormat.KEY_PRIORITY, 0);                  // realtime
        codec.configure(f, null, null, MediaCodec.CONFIGURE_FLAG_ENCODE);
        input = codec.createInputSurface();
        codec.start();
        Diag.log("[video] encoder " + name + " " + s + " " + s.bitrate / 1000 + " kb/s " + fps + " fps" + (s.gray ? " gray" : "")
                + (cbr ? " CBR" : " (this encoder has no CBR mode: its own rate control)"));
        drain = new Thread(new Runnable() { public void run() { drain(sink); } }, "video-encoder");
        drain.start();
    }

    void requestKey() {
        Bundle b = new Bundle();
        b.putInt(MediaCodec.PARAMETER_KEY_REQUEST_SYNC_FRAME, 0);
        try { codec.setParameters(b); } catch (RuntimeException e) { Diag.log("[video] keyframe request refused: " + e); }
    }

    private void drain(Sink sink) {
        MediaCodec.BufferInfo info = new MediaCodec.BufferInfo();
        try {
            while (run) {
                int o = codec.dequeueOutputBuffer(info, 10_000);
                if (o < 0) continue;
                if ((info.flags & MediaCodec.BUFFER_FLAG_CODEC_CONFIG) == 0 && info.size > 0) {
                    ByteBuffer ob = codec.getOutputBuffer(o);
                    byte[] p = new byte[info.size];
                    ob.position(info.offset); ob.get(p);
                    boolean key = (p[0] & 1) == 0;                   // VP8 frame tag: bit 0 clear = keyframe
                    packets++; bytes += p.length; if (key) keys++;
                    sink.onPacket(p, key, size);
                }
                codec.releaseOutputBuffer(o, false);
            }
        } catch (RuntimeException e) {
            if (run) Diag.log("[video] encoder " + size + " stopped: " + e);
        }
    }

    void release() {
        run = false;
        try { drain.join(1000); } catch (InterruptedException ignored) { }
        try { codec.stop(); } catch (RuntimeException ignored) { }
        codec.release();
        input.release();
    }
}
