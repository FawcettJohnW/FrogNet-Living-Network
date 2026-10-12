// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
package net.frognet.communicator;

import android.content.Context;
import android.media.AudioAttributes;
import android.media.AudioDeviceInfo;
import android.media.AudioFormat;
import android.media.AudioManager;
import android.media.AudioRecord;
import android.media.AudioTrack;
import android.media.MediaCodec;
import android.media.MediaFormat;
import android.media.MediaRecorder;
import android.media.audiofx.AcousticEchoCanceler;
import android.media.audiofx.NoiseSuppressor;
import android.os.Build;

import net.frognet.communicator.core.CallCore;
import net.frognet.communicator.core.Mixer;
import net.frognet.communicator.core.Plane;

import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.util.HashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;

/**
 * The call's audio on a phone. The wire is the Communicator's: 16 kHz mono, 20 ms blocks, Opus 24 kb/s
 * ([AUDIO_IS_OPUS_V1]), one decoder per remote source, the fnphone mixer with a 120 ms cushion.
 *   capture:  AudioRecord VOICE_COMMUNICATION at 16 kHz, the platform's echo canceller and noise suppressor when the
 *             device has them (a phone's speaker sits next to its microphone) -> MediaCodec Opus encoder -> the plane
 *   playout:  per-source MediaCodec Opus decoders -> 16 kHz -> Mixer -> AudioTrack (voice call), written blocking, so
 *             the device's own clock paces the mixer
 * A missing audio stack degrades the call to video ([AUDIO_DEGRADES_TO_VIDEO_V1]) and says what was missing.
 */
final class AudioEngine {
    static final int RATE = Mixer.RATE, BLOCK = Mixer.BLOCK_SAMPLES;
    private final Context ctx;
    private final AudioManager am;
    private final Mixer mixer = new Mixer(120);
    private volatile CallCore core;
    private volatile boolean stop = true, muted = false;
    private volatile float gain = 1.0f;
    private AudioRecord rec;
    private AcousticEchoCanceler aec;                     // held: an effect handle that is collected is released
    private NoiseSuppressor ns;
    private AudioTrack track;
    private MediaCodec enc;
    private Thread capT, playT;
    private final Map<String, Dec> decs = new HashMap<String, Dec>();
    private int savedMode = AudioManager.MODE_NORMAL;
    private volatile boolean speaker = true;
    // what happened, cumulative (each reader diffs): [AUDIO_DROP_IS_LOUD_V1] [THE_MIC_IS_NOT_THE_LINK_V1]
    volatile long micBlocks, micShort, encPackets, sendFailures, decPackets, decErrors, playBlocks;
    volatile String micState = "off", playState = "off";

    private static final class Dec { MediaCodec c; int outRate = 48000, outCh = 1; double pos = 0; short last = 0; boolean haveLast = false; }

    AudioEngine(Context ctx) {
        this.ctx = ctx;
        am = (AudioManager) ctx.getSystemService(Context.AUDIO_SERVICE);
    }

    Mixer mixer() { return mixer; }

    void setMuted(boolean m) { muted = m; Diag.log("[audio] microphone " + (m ? "muted" : "on")); }
    boolean muted() { return muted; }
    void setGain(float g) { gain = Math.max(0f, Math.min(1.5f, g)); }

    void start(CallCore c, boolean canRecord) {
        core = c;
        stop = false;
        savedMode = am.getMode();
        am.setMode(AudioManager.MODE_IN_COMMUNICATION);
        applySpeaker();
        startPlayout();
        if (canRecord) startCapture();
        else { micState = "no permission to record"; Diag.log("[audio] no RECORD_AUDIO permission -- the call goes on without sending audio"); }
    }

    void stop() {
        stop = true;
        try { if (capT != null) capT.join(1500); if (playT != null) playT.join(1500); } catch (InterruptedException ignored) { }
        capT = playT = null;
        if (aec != null) { aec.release(); aec = null; }
        if (ns != null) { ns.release(); ns = null; }
        if (rec != null) { try { rec.stop(); } catch (RuntimeException ignored) { } rec.release(); rec = null; }
        if (enc != null) { try { enc.stop(); } catch (RuntimeException ignored) { } enc.release(); enc = null; }
        if (track != null) { try { track.stop(); } catch (RuntimeException ignored) { } track.release(); track = null; }
        synchronized (decs) {
            for (Dec d : decs.values()) { try { d.c.stop(); } catch (RuntimeException ignored) { } d.c.release(); }
            decs.clear();
        }
        if (Build.VERSION.SDK_INT >= 31) am.clearCommunicationDevice(); else am.setSpeakerphoneOn(false);
        am.setMode(savedMode);
        core = null;
        micState = playState = "off";
    }

    // ---- speaker / earpiece --------------------------------------------------------------------------------------
    void setSpeaker(boolean on) { speaker = on; if (!stop) applySpeaker(); }
    boolean speaker() { return speaker; }

    private void applySpeaker() {
        if (Build.VERSION.SDK_INT >= 31) {
            int want = speaker ? AudioDeviceInfo.TYPE_BUILTIN_SPEAKER : AudioDeviceInfo.TYPE_BUILTIN_EARPIECE;
            List<AudioDeviceInfo> devs = am.getAvailableCommunicationDevices();
            AudioDeviceInfo pick = null;
            for (AudioDeviceInfo d : devs) {           // a headset or Bluetooth device, when present, wins over both
                int t = d.getType();
                if (t == AudioDeviceInfo.TYPE_WIRED_HEADSET || t == AudioDeviceInfo.TYPE_BLUETOOTH_SCO || t == AudioDeviceInfo.TYPE_USB_HEADSET
                        || t == AudioDeviceInfo.TYPE_BLE_HEADSET) { pick = d; break; }
            }
            if (pick == null) for (AudioDeviceInfo d : devs) if (d.getType() == want) { pick = d; break; }
            if (pick != null) {
                boolean ok = am.setCommunicationDevice(pick);
                Diag.log("[audio] output: " + typeName(pick.getType()) + (ok ? "" : " (REFUSED by the system)"));
            } else Diag.log("[audio] output: no " + typeName(want) + " among the communication devices");
        } else {
            am.setSpeakerphoneOn(speaker);
            Diag.log("[audio] output: " + (speaker ? "speaker" : "earpiece"));
        }
    }

    private static String typeName(int t) {
        switch (t) {
            case AudioDeviceInfo.TYPE_BUILTIN_SPEAKER: return "speaker";
            case AudioDeviceInfo.TYPE_BUILTIN_EARPIECE: return "earpiece";
            case AudioDeviceInfo.TYPE_WIRED_HEADSET: return "wired headset";
            case AudioDeviceInfo.TYPE_BLUETOOTH_SCO: return "Bluetooth headset";
            case AudioDeviceInfo.TYPE_USB_HEADSET: return "USB headset";
            default: return "device type " + t;
        }
    }

    // ---- capture -> Opus -> the plane -------------------------------------------------------------------------
    private void startCapture() {
        int min = AudioRecord.getMinBufferSize(RATE, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT);
        try {
            rec = new AudioRecord(MediaRecorder.AudioSource.VOICE_COMMUNICATION, RATE, AudioFormat.CHANNEL_IN_MONO,
                    AudioFormat.ENCODING_PCM_16BIT, Math.max(min, BLOCK * 2 * 8));
            if (rec.getState() != AudioRecord.STATE_INITIALIZED) throw new IllegalStateException("AudioRecord did not initialise");
        } catch (RuntimeException e) {
            micState = "microphone unavailable: " + e.getMessage();
            Diag.log("[audio] " + micState + " -- the call goes on without sending audio");
            if (rec != null) { rec.release(); rec = null; }
            return;
        }
        int sid = rec.getAudioSessionId();
        String fx = "";
        if (AcousticEchoCanceler.isAvailable()) { aec = AcousticEchoCanceler.create(sid); if (aec != null) { aec.setEnabled(true); fx += " echo-canceller"; } }
        if (NoiseSuppressor.isAvailable()) { ns = NoiseSuppressor.create(sid); if (ns != null) { ns.setEnabled(true); fx += " noise-suppressor"; } }
        try {
            MediaFormat f = MediaFormat.createAudioFormat(MediaFormat.MIMETYPE_AUDIO_OPUS, RATE, 1);
            f.setInteger(MediaFormat.KEY_BIT_RATE, 24000);
            f.setInteger(MediaFormat.KEY_MAX_INPUT_SIZE, BLOCK * 2 * 4);
            enc = MediaCodec.createEncoderByType(MediaFormat.MIMETYPE_AUDIO_OPUS);
            enc.configure(f, null, null, MediaCodec.CONFIGURE_FLAG_ENCODE);
            enc.start();
        } catch (Exception e) {
            micState = "no Opus encoder on this phone: " + e.getMessage();
            Diag.log("[audio] " + micState + " -- the call goes on without sending audio");
            if (enc != null) { enc.release(); enc = null; }
            rec.release(); rec = null;
            return;
        }
        Diag.log("[audio] microphone: VOICE_COMMUNICATION 16 kHz mono, Opus 24 kb/s by " + enc.getName() + "; effects:" + (fx.isEmpty() ? " none on this device" : fx));
        rec.startRecording();
        micState = "on";
        capT = new Thread(new Runnable() { public void run() { capture(); } }, "audio-capture");
        capT.setPriority(Thread.MAX_PRIORITY);
        capT.start();
    }

    private void capture() {
        short[] blk = new short[BLOCK];
        byte[] bytes = new byte[BLOCK * 2];
        long samples = 0;
        MediaCodec.BufferInfo info = new MediaCodec.BufferInfo();
        long winT = System.nanoTime(), winBlocks = 0;
        try {
            while (!stop) {
                int got = 0;
                while (got < BLOCK && !stop) {
                    int n = rec.read(blk, got, BLOCK - got);
                    if (n < 0) { micState = "read error " + n; Diag.log("[audio] microphone read error " + n); return; }
                    got += n;
                }
                if (stop) break;
                micBlocks++; winBlocks++;
                long now = System.nanoTime();
                if (now - winT >= 10_000_000_000L) {                 // the mic is not the link: what it really delivers
                    double bps = winBlocks / ((now - winT) / 1e9);
                    if (Math.abs(bps - 50.0) > 5.0) Diag.log(String.format(Locale.ROOT, "[audio] the microphone delivered %.1f blocks/s (50 expected)", bps));
                    winT = now; winBlocks = 0;
                }
                if (muted) { samples += BLOCK; continue; }
                for (int k = 0; k < BLOCK; k++) { bytes[2 * k] = (byte) blk[k]; bytes[2 * k + 1] = (byte) (blk[k] >> 8); }
                int off = 0;                                       // in as many input buffers as the codec's size takes
                while (off < bytes.length) {
                    int idx = enc.dequeueInputBuffer(20_000);
                    if (idx < 0) { micShort++; break; }
                    ByteBuffer in = enc.getInputBuffer(idx);
                    in.clear();
                    int n = Math.min(in.remaining(), bytes.length - off);
                    in.put(bytes, off, n);
                    enc.queueInputBuffer(idx, 0, n, (samples + off / 2) * 1_000_000L / RATE, 0);
                    off += n;
                }
                samples += BLOCK;
                int o;
                while ((o = enc.dequeueOutputBuffer(info, 0)) >= 0) {
                    if ((info.flags & MediaCodec.BUFFER_FLAG_CODEC_CONFIG) == 0 && info.size > 0) {
                        ByteBuffer ob = enc.getOutputBuffer(o);
                        byte[] pkt = new byte[info.size];
                        ob.position(info.offset); ob.get(pkt);
                        encPackets++;
                        CallCore c = core;
                        if (c != null) {
                            try { c.sendAudio(pkt); }
                            catch (Plane.Closed e) { sendFailures++; Diag.log("[audio] send: " + e.getMessage()); return; }
                        }
                    }
                    enc.releaseOutputBuffer(o, false);
                }
            }
        } catch (RuntimeException e) {
            micState = "capture failed: " + e;
            Diag.log("[audio] capture failed: " + e);
        }
    }

    // ---- the plane -> per-source Opus decoders -> the mixer -------------------------------------------------------
    /** Called on the call's audio receive thread for each Opus packet. One packet's problem is that packet's. */
    void onOpus(String src, byte[] pkt) {
        if (stop) return;
        Dec d;
        synchronized (decs) {
            d = decs.get(src);
            if (d == null) {
                d = new Dec();
                try {
                    MediaFormat f = MediaFormat.createAudioFormat(MediaFormat.MIMETYPE_AUDIO_OPUS, 48000, 1);
                    f.setByteBuffer("csd-0", opusHead());
                    f.setByteBuffer("csd-1", le64(0));                 // codec delay, ns
                    f.setByteBuffer("csd-2", le64(80_000_000L));       // seek pre-roll, ns
                    d.c = MediaCodec.createDecoderByType(MediaFormat.MIMETYPE_AUDIO_OPUS);
                    d.c.configure(f, null, null, 0);
                    d.c.start();
                    Diag.log("[audio] decoding " + src + " with " + d.c.getName());
                } catch (Exception e) {
                    decErrors++;
                    Diag.log("[audio] cannot decode Opus from " + src + ": " + e);
                    return;
                }
                decs.put(src, d);
            }
        }
        try {
            int idx = d.c.dequeueInputBuffer(5_000);
            if (idx < 0) { decErrors++; return; }
            ByteBuffer in = d.c.getInputBuffer(idx);
            in.clear();
            if (pkt.length > in.remaining()) {                   // refused, never cut: a cut packet is noise
                d.c.queueInputBuffer(idx, 0, 0, 0, 0); decErrors++;
                Diag.log("[audio] an Opus packet of " + pkt.length + " B from " + src + " exceeds the decoder's " + in.remaining() + " B input");
                return;
            }
            in.put(pkt);
            d.c.queueInputBuffer(idx, 0, pkt.length, System.nanoTime() / 1000, 0);
            decPackets++;
            MediaCodec.BufferInfo info = new MediaCodec.BufferInfo();
            int o;
            while ((o = d.c.dequeueOutputBuffer(info, 1_000)) != MediaCodec.INFO_TRY_AGAIN_LATER) {
                if (o == MediaCodec.INFO_OUTPUT_FORMAT_CHANGED) {
                    MediaFormat of = d.c.getOutputFormat();
                    d.outRate = of.getInteger(MediaFormat.KEY_SAMPLE_RATE);
                    d.outCh = of.getInteger(MediaFormat.KEY_CHANNEL_COUNT);
                    Diag.log("[audio] " + src + ": decoder output " + d.outRate + " Hz x" + d.outCh + " -> 16 kHz mono");
                    continue;
                }
                if (o < 0) continue;
                ByteBuffer ob = d.c.getOutputBuffer(o);
                ob.position(info.offset); ob.limit(info.offset + info.size);
                short[] s = new short[info.size / 2];
                ob.order(ByteOrder.LITTLE_ENDIAN).asShortBuffer().get(s);
                d.c.releaseOutputBuffer(o, false);
                short[] mono = s;
                if (d.outCh > 1) { mono = new short[s.length / d.outCh]; for (int k = 0; k < mono.length; k++) mono[k] = s[k * d.outCh]; }
                short[] r = resample(d, mono);
                mixer.feed(src, r, r.length);
            }
        } catch (RuntimeException e) {
            decErrors++;
        }
    }

    /** A streaming linear resampler (the C++ / Python Resampler), source rate -> 16 kHz. */
    private static short[] resample(Dec d, short[] x) {
        if (d.outRate == RATE) return x;
        double step = (double) d.outRate / RATE;
        short[] out = new short[(int) (x.length / step) + 2];
        int n = 0;
        for (short s : x) {
            if (!d.haveLast) { d.last = s; d.haveLast = true; continue; }
            while (d.pos < 1.0) {
                if (n == out.length) out = java.util.Arrays.copyOf(out, out.length * 2);
                out[n++] = (short) (d.last + (s - d.last) * d.pos);
                d.pos += step;
            }
            d.pos -= 1.0;
            d.last = s;
        }
        return java.util.Arrays.copyOf(out, n);
    }

    private static ByteBuffer opusHead() {
        ByteBuffer b = ByteBuffer.allocate(19).order(ByteOrder.LITTLE_ENDIAN);
        b.put("OpusHead".getBytes(java.nio.charset.StandardCharsets.US_ASCII));
        b.put((byte) 1).put((byte) 1).putShort((short) 0).putInt(RATE).putShort((short) 0).put((byte) 0);
        b.flip();
        return b;
    }

    private static ByteBuffer le64(long v) {
        ByteBuffer b = ByteBuffer.allocate(8).order(ByteOrder.nativeOrder());
        b.putLong(v); b.flip();
        return b;
    }

    // ---- the mixer -> the speaker -------------------------------------------------------------------------------
    private void startPlayout() {
        int min = AudioTrack.getMinBufferSize(RATE, AudioFormat.CHANNEL_OUT_MONO, AudioFormat.ENCODING_PCM_16BIT);
        try {
            track = new AudioTrack.Builder()
                    .setAudioAttributes(new AudioAttributes.Builder().setUsage(AudioAttributes.USAGE_VOICE_COMMUNICATION)
                            .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH).build())
                    .setAudioFormat(new AudioFormat.Builder().setSampleRate(RATE).setChannelMask(AudioFormat.CHANNEL_OUT_MONO)
                            .setEncoding(AudioFormat.ENCODING_PCM_16BIT).build())
                    .setBufferSizeInBytes(Math.max(min, BLOCK * 2 * 4))
                    .setTransferMode(AudioTrack.MODE_STREAM).build();
            track.play();
        } catch (RuntimeException e) {
            playState = "speaker unavailable: " + e.getMessage();
            Diag.log("[audio] " + playState + " -- the call goes on without playing audio");
            if (track != null) { track.release(); track = null; }
            return;
        }
        playState = "on";
        Diag.log("[audio] playout: voice-call stream 16 kHz mono, cushion 120 ms");
        playT = new Thread(new Runnable() { public void run() { playout(); } }, "audio-playout");
        playT.setPriority(Thread.MAX_PRIORITY);
        playT.start();
    }

    private void playout() {
        try {
            while (!stop) {
                short[] b = mixer.pullBlock();
                float g = gain;
                if (g != 1.0f) for (int k = 0; k < b.length; k++) b[k] = (short) Math.max(-32768, Math.min(32767, Math.round(b[k] * g)));
                int n = track.write(b, 0, b.length, AudioTrack.WRITE_BLOCKING);
                if (n < 0) { playState = "write error " + n; Diag.log("[audio] playout write error " + n); return; }
                playBlocks++;
            }
        } catch (RuntimeException e) {
            playState = "playout failed: " + e;
            Diag.log("[audio] playout failed: " + e);
        }
    }

}
