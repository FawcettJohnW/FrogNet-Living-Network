// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
package net.frognet.communicator;

import android.annotation.SuppressLint;
import android.content.Context;
import android.graphics.SurfaceTexture;
import android.hardware.camera2.CameraAccessException;
import android.hardware.camera2.CameraCaptureSession;
import android.hardware.camera2.CameraCharacteristics;
import android.hardware.camera2.CameraDevice;
import android.hardware.camera2.CameraManager;
import android.hardware.camera2.CaptureRequest;
import android.hardware.camera2.params.StreamConfigurationMap;
import android.opengl.EGL14;
import android.opengl.EGLConfig;
import android.opengl.EGLContext;
import android.opengl.EGLDisplay;
import android.opengl.EGLExt;
import android.opengl.EGLSurface;
import android.opengl.GLES11Ext;
import android.opengl.GLES20;
import android.os.Handler;
import android.os.HandlerThread;
import android.util.Range;
import android.util.Size;
import android.view.Surface;

import net.frognet.communicator.core.Ladder;

import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.nio.FloatBuffer;
import java.util.Collections;

/**
 * The camera, drawn by GL into the encoder at the size the bearer chose, and into the self-view.
 *
 * Camera2 -> SurfaceTexture (an OES texture). The SurfaceTexture's transform makes the picture upright in the phone's
 * natural (portrait) orientation, mirrored for the front camera (what a selfie preview shows). From there:
 *  - the picture is turned upright for how the phone is held: by the physical orientation for what is SENT (so the far
 *    end sees it upright even with the screen's rotation locked), by the screen's rotation for the self-view;
 *  - what is sent is always a landscape ladder size (the media server scales an upload to the ladder's fixed landscape
 *    sizes, src/media/transcode.cpp, so a portrait upload would reach a transcoded viewer stretched): held sideways the
 *    picture fills it, held upright it is pillarboxed -- nothing cut, nothing distorted;
 *  - the front camera is un-mirrored for the far end; the self-view stays a mirror;
 *  - the bottom sizes are drawn gray ([SMALLEST_WIRE_WINS_V1]);
 *  - one frame per 1/fps goes to the encoder; each is one tick of the call's send loop (CallCore.tick()), which
 *    returns the size for that frame; a new size is a new encoder, which opens on a keyframe.
 * Everything runs on one thread ("camera-gl").
 */
final class CameraGl implements SurfaceTexture.OnFrameAvailableListener {
    interface Host {
        int tick();                                   // the size index for this frame, -1 = audio only
        boolean takeWantKey();
        void onPacket(byte[] p, boolean key, Ladder.Size s);
    }

    private final Context ctx;
    private final Host host;
    private final int fps;
    private HandlerThread thread;
    private Handler h;
    private EGLDisplay dpy = EGL14.EGL_NO_DISPLAY;
    private EGLContext ectx = EGL14.EGL_NO_CONTEXT;
    private EGLConfig cfg;
    private EGLSurface pbuf = EGL14.EGL_NO_SURFACE, encSurf = EGL14.EGL_NO_SURFACE, selfSurf = EGL14.EGL_NO_SURFACE;
    private int prog, tex, aPos, aTex, uTex, uPos, uGray;
    private SurfaceTexture st;
    private Surface camSurface;
    private CameraDevice cam;
    private CameraCaptureSession session;
    private boolean front = true;
    private int sensorOrientation = 90, natW = 720, natH = 1280;
    private VideoEncoder enc;
    private int encSize = -2;
    private long lastEncodeNs = 0, encFailNs = 0;
    private volatile int displayDeg = 0, deviceDeg = 0;
    private volatile boolean sending = false;
    private SurfaceTexture selfTarget;
    private int errorsLogged = 0;
    private int selfW, selfH;
    private final float[] stm = new float[16];
    private FloatBuffer quadPos, quadTex;
    volatile String state = "off";
    volatile int frames, encoded;

    CameraGl(Context ctx, int fps, Host host) { this.ctx = ctx; this.fps = fps; this.host = host; }

    // ---- control (any thread) --------------------------------------------------------------------------------------
    void start(final boolean useFront) {
        thread = new HandlerThread("camera-gl");
        thread.start();
        h = new Handler(thread.getLooper());
        h.post(new Runnable() { public void run() { front = useFront; initGl(); openCamera(); } });
    }

    void setSending(boolean on) { sending = on; }

    void setDisplayRotation(int deg) { displayDeg = deg; }

    /** OrientationEventListener's degrees (clockwise from natural), already settled on a quarter turn. */
    void setDeviceOrientation(int clockwiseDeg) { deviceDeg = (360 - clockwiseDeg) % 360; }

    void setSelfView(final SurfaceTexture t, final int w, final int hh) {
        if (h == null) return;
        h.post(new Runnable() { public void run() {
            releaseSelf();
            selfTarget = t; selfW = w; selfH = hh;
            if (t != null && dpy != EGL14.EGL_NO_DISPLAY && cfg != null) {
                selfSurf = EGL14.eglCreateWindowSurface(dpy, cfg, t, new int[]{EGL14.EGL_NONE}, 0);
                if (selfSurf == EGL14.EGL_NO_SURFACE) Diag.log("[camera] self-view surface refused: 0x" + Integer.toHexString(EGL14.eglGetError()));
            }
        } });
    }

    /** The TextureView let go of its SurfaceTexture: stop drawing into it, then release it (TextureView contract). */
    void dropSelfView(final SurfaceTexture t) {
        if (h == null) { t.release(); return; }
        h.post(new Runnable() { public void run() {
            if (selfTarget == t) { releaseSelf(); selfTarget = null; selfW = selfH = 0; }
            t.release();
        } });
    }

    /** The camera was taken away (the app left the foreground): open it again. */
    void reopen() {
        if (h == null) return;
        h.post(new Runnable() { public void run() {
            if (cam != null) return;
            Diag.log("[camera] reopening");
            closeCamera(); openCamera();
            if (enc != null) enc.requestKey();
        } });
    }

    boolean cameraOpen() { return cam != null; }

    void switchCamera() {
        if (h == null) return;
        h.post(new Runnable() { public void run() {
            closeCamera(); front = !front; openCamera();
            if (enc != null) enc.requestKey();
        } });
    }

    boolean front() { return front; }

    void stop() {
        if (h == null) return;
        final java.util.concurrent.CountDownLatch done = new java.util.concurrent.CountDownLatch(1);
        h.post(new Runnable() { public void run() {
            closeCamera();
            releaseEncoder();
            releaseSelf();
            if (st != null) { st.release(); st = null; }
            if (camSurface != null) { camSurface.release(); camSurface = null; }
            if (dpy != EGL14.EGL_NO_DISPLAY) {
                EGL14.eglMakeCurrent(dpy, EGL14.EGL_NO_SURFACE, EGL14.EGL_NO_SURFACE, EGL14.EGL_NO_CONTEXT);
                if (pbuf != EGL14.EGL_NO_SURFACE) EGL14.eglDestroySurface(dpy, pbuf);
                EGL14.eglDestroyContext(dpy, ectx);
                EGL14.eglTerminate(dpy);
                dpy = EGL14.EGL_NO_DISPLAY;
            }
            done.countDown();
        } });
        try { done.await(3, java.util.concurrent.TimeUnit.SECONDS); } catch (InterruptedException ignored) { }
        thread.quitSafely();
        h = null;
        state = "off";
    }

    // ---- GL ---------------------------------------------------------------------------------------------------------
    private void initGl() {
        dpy = EGL14.eglGetDisplay(EGL14.EGL_DEFAULT_DISPLAY);
        int[] v = new int[2];
        EGL14.eglInitialize(dpy, v, 0, v, 1);
        int[] attr = {EGL14.EGL_RED_SIZE, 8, EGL14.EGL_GREEN_SIZE, 8, EGL14.EGL_BLUE_SIZE, 8, EGL14.EGL_ALPHA_SIZE, 8,
                EGL14.EGL_RENDERABLE_TYPE, EGL14.EGL_OPENGL_ES2_BIT, 0x3142 /* EGL_RECORDABLE_ANDROID */, 1, EGL14.EGL_NONE};
        EGLConfig[] cs = new EGLConfig[1]; int[] n = new int[1];
        EGL14.eglChooseConfig(dpy, attr, 0, cs, 0, 1, n, 0);
        if (n[0] < 1 || cs[0] == null) {
            state = "no recordable EGL config on this phone: no video can be sent";
            Diag.log("[camera] " + state);
            EGL14.eglTerminate(dpy); dpy = EGL14.EGL_NO_DISPLAY;
            return;
        }
        cfg = cs[0];
        ectx = EGL14.eglCreateContext(dpy, cfg, EGL14.EGL_NO_CONTEXT, new int[]{EGL14.EGL_CONTEXT_CLIENT_VERSION, 2, EGL14.EGL_NONE}, 0);
        // made current with no surface where the driver allows it (EGL_KHR_surfaceless_context), else on a 1x1 pbuffer
        String ext = EGL14.eglQueryString(dpy, EGL14.EGL_EXTENSIONS);
        if (ext == null || !ext.contains("EGL_KHR_surfaceless_context"))
            pbuf = EGL14.eglCreatePbufferSurface(dpy, cfg, new int[]{EGL14.EGL_WIDTH, 1, EGL14.EGL_HEIGHT, 1, EGL14.EGL_NONE}, 0);
        if (!EGL14.eglMakeCurrent(dpy, pbuf, pbuf, ectx)) {
            state = "EGL context could not be made current: 0x" + Integer.toHexString(EGL14.eglGetError());
            Diag.log("[camera] " + state);
            EGL14.eglDestroyContext(dpy, ectx); EGL14.eglTerminate(dpy); dpy = EGL14.EGL_NO_DISPLAY;
            return;
        }
        Diag.log("[camera] GL: " + GLES20.glGetString(GLES20.GL_RENDERER) + (pbuf == EGL14.EGL_NO_SURFACE ? ", surfaceless" : ", pbuffer"));
        String vs = "uniform mat4 uPos; uniform mat4 uTex; attribute vec4 aPos; attribute vec4 aTex; varying vec2 vTex;\n"
                + "void main() { gl_Position = uPos * aPos; vTex = (uTex * aTex).xy; }\n";
        String fs = "#extension GL_OES_EGL_image_external : require\nprecision mediump float; varying vec2 vTex;\n"
                + "uniform samplerExternalOES sTex; uniform float uGray;\n"
                + "void main() { vec4 c = texture2D(sTex, vTex); float y = dot(c.rgb, vec3(0.299, 0.587, 0.114));\n"
                + "  gl_FragColor = mix(c, vec4(y, y, y, 1.0), uGray); }\n";
        prog = GLES20.glCreateProgram();
        GLES20.glAttachShader(prog, shader(GLES20.GL_VERTEX_SHADER, vs));
        GLES20.glAttachShader(prog, shader(GLES20.GL_FRAGMENT_SHADER, fs));
        GLES20.glLinkProgram(prog);
        int[] ok = new int[1];
        GLES20.glGetProgramiv(prog, GLES20.GL_LINK_STATUS, ok, 0);
        if (ok[0] == 0) Diag.log("[camera] GL program: " + GLES20.glGetProgramInfoLog(prog));
        aPos = GLES20.glGetAttribLocation(prog, "aPos"); aTex = GLES20.glGetAttribLocation(prog, "aTex");
        uPos = GLES20.glGetUniformLocation(prog, "uPos"); uTex = GLES20.glGetUniformLocation(prog, "uTex");
        uGray = GLES20.glGetUniformLocation(prog, "uGray");
        quadPos = fb(new float[]{-1, -1, 1, -1, -1, 1, 1, 1});
        quadTex = fb(new float[]{0, 0, 1, 0, 0, 1, 1, 1});
        int[] t = new int[1];
        GLES20.glGenTextures(1, t, 0);
        tex = t[0];
        GLES20.glBindTexture(GLES11Ext.GL_TEXTURE_EXTERNAL_OES, tex);
        GLES20.glTexParameteri(GLES11Ext.GL_TEXTURE_EXTERNAL_OES, GLES20.GL_TEXTURE_MIN_FILTER, GLES20.GL_LINEAR);
        GLES20.glTexParameteri(GLES11Ext.GL_TEXTURE_EXTERNAL_OES, GLES20.GL_TEXTURE_MAG_FILTER, GLES20.GL_LINEAR);
        GLES20.glTexParameteri(GLES11Ext.GL_TEXTURE_EXTERNAL_OES, GLES20.GL_TEXTURE_WRAP_S, GLES20.GL_CLAMP_TO_EDGE);
        GLES20.glTexParameteri(GLES11Ext.GL_TEXTURE_EXTERNAL_OES, GLES20.GL_TEXTURE_WRAP_T, GLES20.GL_CLAMP_TO_EDGE);
        st = new SurfaceTexture(tex);
        st.setOnFrameAvailableListener(this, h);
        if (selfTarget != null) selfSurf = EGL14.eglCreateWindowSurface(dpy, cfg, selfTarget, new int[]{EGL14.EGL_NONE}, 0);
    }

    /** A window that never waits for its consumer: the newest frame replaces a queued one. A hidden self-view (or an
     *  encoder that falls behind) must never stall the one thread that feeds both. */
    private void current(EGLSurface sf) {
        EGL14.eglMakeCurrent(dpy, sf, sf, ectx);
        EGL14.eglSwapInterval(dpy, 0);
    }

    private static int shader(int type, String src) {
        int s = GLES20.glCreateShader(type);
        GLES20.glShaderSource(s, src);
        GLES20.glCompileShader(s);
        int[] ok = new int[1];
        GLES20.glGetShaderiv(s, GLES20.GL_COMPILE_STATUS, ok, 0);
        if (ok[0] == 0) Diag.log("[camera] shader: " + GLES20.glGetShaderInfoLog(s));
        return s;
    }

    private static FloatBuffer fb(float[] a) {
        FloatBuffer b = ByteBuffer.allocateDirect(a.length * 4).order(ByteOrder.nativeOrder()).asFloatBuffer();
        b.put(a).position(0);
        return b;
    }

    /** Draws the camera picture upright for `deg` (the quarter turn the phone is held at, counter-clockwise), fitted
     *  into W x H without distortion (black bars where the shapes differ), optionally mirrored and gray. */
    private void draw(int w, int hh, int deg, boolean flip, boolean gray) {
        GLES20.glViewport(0, 0, w, hh);
        GLES20.glClearColor(0f, 0f, 0f, 1f);
        GLES20.glClear(GLES20.GL_COLOR_BUFFER_BIT);
        double th = Math.toRadians(deg), cs = Math.cos(th), sn = Math.sin(th);
        double rw = Math.abs(cs) * natW + Math.abs(sn) * natH, rh = Math.abs(sn) * natW + Math.abs(cs) * natH;
        double k = Math.min(w / rw, hh / rh), fx = flip ? -1 : 1;
        float[] m = new float[16];
        m[0] = (float) (2 * k / w * fx * cs * natW / 2);   m[4] = (float) (2 * k / w * fx * -sn * natH / 2);
        m[1] = (float) (2 * k / hh * sn * natW / 2);       m[5] = (float) (2 * k / hh * cs * natH / 2);
        m[10] = 1; m[15] = 1;
        GLES20.glUseProgram(prog);
        GLES20.glActiveTexture(GLES20.GL_TEXTURE0);
        GLES20.glBindTexture(GLES11Ext.GL_TEXTURE_EXTERNAL_OES, tex);
        GLES20.glUniformMatrix4fv(uPos, 1, false, m, 0);
        GLES20.glUniformMatrix4fv(uTex, 1, false, stm, 0);
        GLES20.glUniform1f(uGray, gray ? 1f : 0f);
        GLES20.glEnableVertexAttribArray(aPos);
        GLES20.glVertexAttribPointer(aPos, 2, GLES20.GL_FLOAT, false, 0, quadPos);
        GLES20.glEnableVertexAttribArray(aTex);
        GLES20.glVertexAttribPointer(aTex, 2, GLES20.GL_FLOAT, false, 0, quadTex);
        GLES20.glDrawArrays(GLES20.GL_TRIANGLE_STRIP, 0, 4);
    }

    @Override
    public void onFrameAvailable(SurfaceTexture t) {
        if (st == null || dpy == EGL14.EGL_NO_DISPLAY) return;
        try { frame(); }
        catch (RuntimeException e) {                                   // one frame's problem, logged, never the app's
            if (errorsLogged++ < 20) Diag.log("[camera] frame failed: " + e);
        }
    }

    private void frame() {
        EGL14.eglMakeCurrent(dpy, pbuf, pbuf, ectx);
        st.updateTexImage();
        st.getTransformMatrix(stm);
        frames++;
        if (selfSurf != EGL14.EGL_NO_SURFACE && selfW > 0 && selfH > 0) {
            current(selfSurf);
            draw(selfW, selfH, displayDeg, false, false);           // a mirror for the front camera, as the camera gives it
            EGL14.eglSwapBuffers(dpy, selfSurf);
        }
        long now = System.nanoTime(), period = 1_000_000_000L / fps;
        if (!sending || now - lastEncodeNs < period - 4_000_000L) return;
        lastEncodeNs = (now - lastEncodeNs > 2 * period) ? now : lastEncodeNs + period;   // [NO_CATCH_UP_BURST_V1]
        int sz = host.tick();
        if (sz != encSize || (sz >= 0 && enc == null)) reconfigure(sz, now);
        if (enc == null || encSurf == EGL14.EGL_NO_SURFACE) return;
        if (host.takeWantKey()) enc.requestKey();
        current(encSurf);
        draw(enc.size.w, enc.size.h, deviceDeg, front, enc.size.gray);  // the far end sees the true picture, upright
        EGLExt.eglPresentationTimeANDROID(dpy, encSurf, now);
        EGL14.eglSwapBuffers(dpy, encSurf);
        encoded++;
    }

    private void reconfigure(int sz, long now) {
        if (sz == encSize && enc != null) return;
        if (sz >= 0 && enc == null && encSize == sz && now - encFailNs < 2_000_000_000L) return;   // retried, not replaced
        releaseEncoder();
        encSize = sz;
        if (sz < 0) { Diag.log("[video] audio only: the bearer is below the bottom size"); return; }
        try {
            enc = new VideoEncoder(Ladder.SIZES[sz], fps, new VideoEncoder.Sink() {
                public void onPacket(byte[] p, boolean key, Ladder.Size s) { host.onPacket(p, key, s); }
            });
            encSurf = EGL14.eglCreateWindowSurface(dpy, cfg, enc.input, new int[]{EGL14.EGL_NONE}, 0);
            if (encSurf == EGL14.EGL_NO_SURFACE) throw new RuntimeException("EGL refused the encoder's surface: 0x" + Integer.toHexString(EGL14.eglGetError()));
        } catch (Exception e) {
            Diag.log("[video] encoder for " + Ladder.SIZES[sz] + " failed: " + e.getMessage() + " -- retried in 2 s");
            releaseEncoder();
            encSize = sz; encFailNs = now;
        }
    }

    private void releaseEncoder() {
        if (encSurf != EGL14.EGL_NO_SURFACE) {
            EGL14.eglMakeCurrent(dpy, pbuf, pbuf, ectx);
            EGL14.eglDestroySurface(dpy, encSurf);
            encSurf = EGL14.EGL_NO_SURFACE;
        }
        if (enc != null) { enc.release(); enc = null; }
        encSize = -2;
    }

    private void releaseSelf() {
        if (selfSurf != EGL14.EGL_NO_SURFACE) {
            EGL14.eglMakeCurrent(dpy, pbuf, pbuf, ectx);
            EGL14.eglDestroySurface(dpy, selfSurf);
            selfSurf = EGL14.EGL_NO_SURFACE;
        }
    }

    // ---- the camera ----------------------------------------------------------------------------------------------
    @SuppressLint("MissingPermission")
    private void openCamera() {
        if (st == null) return;
        CameraManager cm = (CameraManager) ctx.getSystemService(Context.CAMERA_SERVICE);
        try {
            String id = null, any = null;
            for (String c : cm.getCameraIdList()) {
                Integer f = cm.getCameraCharacteristics(c).get(CameraCharacteristics.LENS_FACING);
                if (f == null) continue;
                if (any == null) any = c;
                if ((front && f == CameraCharacteristics.LENS_FACING_FRONT) || (!front && f == CameraCharacteristics.LENS_FACING_BACK)) { id = c; break; }
            }
            if (id == null) {
                if (any == null) { state = "no camera"; Diag.log("[camera] this phone has no camera"); return; }
                Diag.log("[camera] no " + (front ? "front" : "back") + " camera; using camera " + any);
                id = any;
            }
            CameraCharacteristics cc = cm.getCameraCharacteristics(id);
            Integer so = cc.get(CameraCharacteristics.SENSOR_ORIENTATION);
            sensorOrientation = so == null ? 0 : so;
            Integer f = cc.get(CameraCharacteristics.LENS_FACING);
            front = f != null && f == CameraCharacteristics.LENS_FACING_FRONT;
            StreamConfigurationMap map = cc.get(CameraCharacteristics.SCALER_STREAM_CONFIGURATION_MAP);
            Size best = choose(map.getOutputSizes(SurfaceTexture.class));
            st.setDefaultBufferSize(best.getWidth(), best.getHeight());
            // [CAMERA_SAYS_WHICH_V1] which camera, which way it faces, and so whether the far end gets it un-mirrored
            Diag.log("[camera] opened camera " + id + ": facing " + (f == null ? "unknown" : f == CameraCharacteristics.LENS_FACING_FRONT ? "front"
                    : f == CameraCharacteristics.LENS_FACING_BACK ? "back" : "external") + ", sensor " + sensorOrientation + " deg, "
                    + best.getWidth() + "x" + best.getHeight() + (front ? "; un-mirrored for the far end" : "; sent as the camera gives it"));
            if (sensorOrientation % 180 != 0) { natW = best.getHeight(); natH = best.getWidth(); }
            else { natW = best.getWidth(); natH = best.getHeight(); }
            final Range<Integer> fpsRange = chooseFps(cc.get(CameraCharacteristics.CONTROL_AE_AVAILABLE_TARGET_FPS_RANGES));
            if (camSurface != null) camSurface.release();
            camSurface = new Surface(st);
            Diag.log("[camera] " + (front ? "front" : "back") + " camera " + id + ": " + best.getWidth() + "x" + best.getHeight()
                    + " sensor " + sensorOrientation + " deg, fps range " + fpsRange);
            final String camId = id;
            cm.openCamera(id, new CameraDevice.StateCallback() {
                public void onOpened(CameraDevice d) {
                    cam = d;
                    try {
                        d.createCaptureSession(Collections.singletonList(camSurface), new CameraCaptureSession.StateCallback() {
                            public void onConfigured(CameraCaptureSession s) {
                                if (cam == null) return;
                                session = s;
                                try {
                                    CaptureRequest.Builder b = cam.createCaptureRequest(CameraDevice.TEMPLATE_RECORD);
                                    b.addTarget(camSurface);
                                    if (fpsRange != null) b.set(CaptureRequest.CONTROL_AE_TARGET_FPS_RANGE, fpsRange);
                                    s.setRepeatingRequest(b.build(), null, h);
                                    state = "on (" + (front ? "front" : "back") + ")";
                                } catch (CameraAccessException | RuntimeException e) {
                                    state = "camera request failed: " + e.getMessage();
                                    Diag.log("[camera] " + state);
                                }
                            }
                            public void onConfigureFailed(CameraCaptureSession s) {
                                state = "camera session could not be configured";
                                Diag.log("[camera] " + state);
                            }
                        }, h);
                    } catch (CameraAccessException e) {
                        state = "camera session: " + e.getMessage();
                        Diag.log("[camera] " + state);
                    }
                }
                public void onDisconnected(CameraDevice d) {
                    state = "camera " + camId + " taken away (another app, or the app left the foreground)";
                    Diag.log("[camera] " + state);
                    d.close(); if (cam == d) cam = null;
                }
                public void onError(CameraDevice d, int err) {
                    state = "camera " + camId + " error " + err;
                    Diag.log("[camera] " + state);
                    d.close(); if (cam == d) cam = null;
                }
            }, h);
        } catch (CameraAccessException | SecurityException | IllegalArgumentException e) {
            state = "camera unavailable: " + e.getMessage();
            Diag.log("[camera] " + state + " -- the call goes on receiving only");
        }
    }

    private void closeCamera() {
        if (session != null) { try { session.close(); } catch (RuntimeException ignored) { } session = null; }
        if (cam != null) { cam.close(); cam = null; }
    }

    /** 1280x720 when the camera gives it; else the 16:9 size nearest 1280 wide; else the size nearest 1280 wide. */
    private static Size choose(Size[] sizes) {
        Size best = null, best169 = null;
        for (Size s : sizes) {
            if (s.getWidth() == 1280 && s.getHeight() == 720) return s;
            if (s.getWidth() > 1920) continue;
            if (Math.abs(s.getWidth() * 9 - s.getHeight() * 16) < 32 && (best169 == null || Math.abs(s.getWidth() - 1280) < Math.abs(best169.getWidth() - 1280))) best169 = s;
            if (best == null || Math.abs(s.getWidth() - 1280) < Math.abs(best.getWidth() - 1280)) best = s;
        }
        return best169 != null ? best169 : best != null ? best : sizes[0];
    }

    /** An AE range reaching 30 fps (spare frames for the 24 fps gate to choose from); if the camera has none, one
     *  reaching the call's frame rate. Narrowest first. */
    private Range<Integer> chooseFps(Range<Integer>[] rs) {
        if (rs == null) return null;
        Range<Integer> r = narrowest(rs, fps + 6);
        return r != null ? r : narrowest(rs, fps);
    }

    private static Range<Integer> narrowest(Range<Integer>[] rs, int need) {
        Range<Integer> best = null;
        for (Range<Integer> r : rs) {
            if (r.getUpper() < need) continue;
            if (best == null || r.getUpper() < best.getUpper() || (r.getUpper().equals(best.getUpper()) && r.getLower() > best.getLower())) best = r;
        }
        return best;
    }

    VideoEncoder encoder() { return enc; }
}
