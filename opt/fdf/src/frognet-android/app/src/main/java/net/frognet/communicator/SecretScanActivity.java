// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
package net.frognet.communicator;

import android.Manifest;
import android.app.Activity;
import android.content.Context;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.graphics.Color;
import android.graphics.ImageFormat;
import android.graphics.SurfaceTexture;
import android.hardware.camera2.CameraAccessException;
import android.hardware.camera2.CameraCaptureSession;
import android.hardware.camera2.CameraCharacteristics;
import android.hardware.camera2.CameraDevice;
import android.hardware.camera2.CameraManager;
import android.hardware.camera2.CaptureRequest;
import android.media.Image;
import android.media.ImageReader;
import android.os.Bundle;
import android.os.Handler;
import android.os.HandlerThread;
import android.view.Gravity;
import android.view.Surface;
import android.view.TextureView;
import android.view.View;
import android.widget.Button;
import android.widget.FrameLayout;
import android.widget.TextView;

import net.frognet.communicator.core.ChannelFrame;
import net.frognet.communicator.core.SecretQr;

import java.io.File;
import java.nio.ByteBuffer;
import java.util.Arrays;

/**
 * [A_SECRET_IS_SHOWN_TO_BE_SCANNED_V1] (John, 27 Nov 2026: "Import secret kinda sucks. Let's have the Windows app display a
 * QR code for the Android to import from.") The back camera looks at the QR code a machine that holds the Role's secret
 * shows (comms-app, Security tab). The code's text is read here, kept by this phone as that machine keeps it
 * (SecretQr.keep, in this app's private files), and dropped: it is not logged, not shown, and not handed to any other
 * screen or app. What goes back to the Security tab is what a screen may say: which secret, and 8 characters of its hash.
 */
public final class SecretScanActivity extends Activity {
    static final String X_SAID = "said";
    private static final int W = 1280, H = 720, ASK_CAMERA = 11;
    private TextureView view;
    private TextView hint;
    private HandlerThread thread;
    private Handler bg;
    private CameraDevice camera;
    private CameraCaptureSession session;
    private ImageReader reader;
    private volatile boolean done = false, reading = false;
    private long frames = 0;

    @Override
    protected void onCreate(Bundle b) {
        super.onCreate(b);
        FrameLayout f = new FrameLayout(this);
        f.setBackgroundColor(Color.BLACK);
        view = new TextureView(this);
        f.addView(view, new FrameLayout.LayoutParams(-1, -1, Gravity.CENTER));
        hint = new TextView(this);
        hint.setTextColor(Color.WHITE); hint.setTextSize(16); hint.setGravity(Gravity.CENTER);
        hint.setBackgroundColor(0xB0000000); hint.setPadding(32, 32, 32, 32);
        hint.setText("Point the camera at the QR code on the other machine's Security tab (Show the secret as a QR code).");
        f.addView(hint, new FrameLayout.LayoutParams(-1, -2, Gravity.TOP));
        Button cancel = new Button(this);
        cancel.setText("Cancel");
        cancel.setOnClickListener(new View.OnClickListener() { public void onClick(View v) { setResult(RESULT_CANCELED); finish(); } });
        FrameLayout.LayoutParams cp = new FrameLayout.LayoutParams(-2, -2, Gravity.BOTTOM | Gravity.CENTER_HORIZONTAL); cp.bottomMargin = 96;
        f.addView(cancel, cp);
        setContentView(f);
        view.setSurfaceTextureListener(new TextureView.SurfaceTextureListener() {
            public void onSurfaceTextureAvailable(SurfaceTexture s, int w, int h) { fit(w, h); open(); }
            public void onSurfaceTextureSizeChanged(SurfaceTexture s, int w, int h) { fit(w, h); }
            public boolean onSurfaceTextureDestroyed(SurfaceTexture s) { close(); return true; }
            public void onSurfaceTextureUpdated(SurfaceTexture s) { }
        });
    }

    /** The camera's 16:9 picture, upright on a phone held upright: the view keeps 9 wide to 16 high, centred. */
    private void fit(int w, int h) {
        android.graphics.Matrix m = new android.graphics.Matrix();
        float want = 16f / 9f, has = (float) h / w;                      // the picture's height over width, and the view's
        if (has > want) m.setScale(has / want, 1f, w / 2f, h / 2f); else m.setScale(1f, want / has, w / 2f, h / 2f);
        view.setTransform(m);
    }

    @Override protected void onResume() { super.onResume(); if (view.isAvailable()) open(); }
    @Override protected void onPause() { close(); super.onPause(); }

    @Override
    public void onRequestPermissionsResult(int code, String[] perms, int[] results) {
        if (code != ASK_CAMERA) return;
        if (results.length > 0 && results[0] == PackageManager.PERMISSION_GRANTED) open();
        else { hint.setText("The camera is not allowed: the code cannot be scanned. Import file… takes the same secret from a file."); }
    }

    private void open() {
        if (camera != null || done) return;
        if (checkSelfPermission(Manifest.permission.CAMERA) != PackageManager.PERMISSION_GRANTED) { requestPermissions(new String[]{Manifest.permission.CAMERA}, ASK_CAMERA); return; }
        thread = new HandlerThread("secret-scan"); thread.start(); bg = new Handler(thread.getLooper());
        try {
            CameraManager cm = (CameraManager) getSystemService(Context.CAMERA_SERVICE);
            String id = null;
            for (String c : cm.getCameraIdList()) {
                Integer facing = cm.getCameraCharacteristics(c).get(CameraCharacteristics.LENS_FACING);
                if (facing != null && facing == CameraCharacteristics.LENS_FACING_BACK) { id = c; break; }
                if (id == null) id = c;
            }
            if (id == null) { hint.setText("This phone has no camera."); return; }
            reader = ImageReader.newInstance(W, H, ImageFormat.YUV_420_888, 2);
            reader.setOnImageAvailableListener(new ImageReader.OnImageAvailableListener() { public void onImageAvailable(ImageReader r) { look(r); } }, bg);
            cm.openCamera(id, new CameraDevice.StateCallback() {
                public void onOpened(CameraDevice c) { camera = c; start(); }
                public void onDisconnected(CameraDevice c) { c.close(); camera = null; }
                public void onError(CameraDevice c, int e) { c.close(); camera = null; say("The camera could not be opened (error " + e + ")."); }
            }, bg);
        } catch (CameraAccessException | RuntimeException e) {
            Diag.log("[scan] the camera could not be opened: " + e);
            hint.setText("The camera could not be opened: " + e.getMessage());
        }
    }

    private void start() {
        try {
            SurfaceTexture st = view.getSurfaceTexture();
            if (st == null || camera == null) return;
            st.setDefaultBufferSize(W, H);
            final Surface shown = new Surface(st);
            final CaptureRequest.Builder rq = camera.createCaptureRequest(CameraDevice.TEMPLATE_PREVIEW);
            rq.addTarget(shown); rq.addTarget(reader.getSurface());
            rq.set(CaptureRequest.CONTROL_AF_MODE, CaptureRequest.CONTROL_AF_MODE_CONTINUOUS_PICTURE);
            camera.createCaptureSession(Arrays.asList(shown, reader.getSurface()), new CameraCaptureSession.StateCallback() {
                public void onConfigured(CameraCaptureSession s) {
                    session = s;
                    try { s.setRepeatingRequest(rq.build(), null, bg); Diag.log("[scan] the back camera is looking for a secret's QR code (" + W + "x" + H + ")"); }
                    catch (CameraAccessException | IllegalStateException e) { say("The camera stopped: " + e.getMessage()); }
                }
                public void onConfigureFailed(CameraCaptureSession s) { say("The camera could not be set up for scanning."); }
            }, bg);
        } catch (CameraAccessException | RuntimeException e) { say("The camera could not be started: " + e.getMessage()); }
    }

    /** One picture from the camera, on the scan thread: its grey plane is read for a code. */
    private void look(ImageReader r) {
        Image img = null;
        try {
            img = r.acquireLatestImage();
            if (img == null || done || reading) return;
            reading = true;
            Image.Plane y = img.getPlanes()[0];
            ByteBuffer buf = y.getBuffer();
            byte[] grey = new byte[buf.remaining()];
            buf.get(grey);
            int stride = y.getRowStride(), w = img.getWidth(), h = Math.min(img.getHeight(), grey.length / Math.max(1, stride));
            frames++;
            String text = QrRead.read(grey, stride, w, h);
            if (text == null) return;
            if (!text.startsWith(SecretQr.PREFIX)) { say("That QR code is not a FrogNet secret. Show the one on the Security tab of a machine that holds it."); return; }
            final String said;
            try { said = SecretQr.keep(text, new File(getFilesDir(), "channel_secrets").getPath()); }
            catch (ChannelFrame.Gone e) { say("That code was read and not kept: " + e.getMessage()); return; }
            done = true;
            Diag.log("[scan] a secret was read from a QR code after " + frames + " picture(s) and kept: " + said);
            runOnUiThread(new Runnable() { public void run() { setResult(RESULT_OK, new Intent().putExtra(X_SAID, said)); finish(); } });
        } catch (RuntimeException e) {
            Diag.log("[scan] a picture could not be read: " + e);
        } finally {
            reading = false;
            if (img != null) img.close();
        }
    }

    private void say(final String s) { runOnUiThread(new Runnable() { public void run() { hint.setText(s); } }); }

    private void close() {
        try { if (session != null) session.close(); } catch (RuntimeException ignored) { }
        session = null;
        try { if (camera != null) camera.close(); } catch (RuntimeException ignored) { }
        camera = null;
        if (reader != null) { reader.close(); reader = null; }
        if (thread != null) { thread.quitSafely(); thread = null; bg = null; }
    }
}
