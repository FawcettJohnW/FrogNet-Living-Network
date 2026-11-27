// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
package net.frognet.communicator;

import net.frognet.communicator.core.Channel;
import net.frognet.communicator.core.ChannelFrame;
import net.frognet.communicator.core.SecretQr;

import java.io.File;
import java.nio.file.Files;
import java.util.Random;

/**
 * SecretQrTest PICTURE.pgm HASH [PICTURE.pgm HASH ...] -- [A_SECRET_IS_SHOWN_TO_BE_SCANNED_V1] each picture is the QR code
 * the desktop Communicator draws for a secret (tests/test_secret_qr, C++), each hash what that machine says of the same
 * secret (comms-channel secret). The phone's own reader (QrRead, the scanner's) reads the code out of a 1280x720 grey
 * camera frame the picture is set into, off centre, with noise, and as a camera held a quarter turn round; the phone
 * keeps what it read (SecretQr.keep) and must then say the same hash. Then the phone is reset and must hold nothing.
 */
public final class SecretQrTest {
    static int fails = 0;
    static void check(boolean ok, String what) { System.out.println((ok ? "PASS  " : "FAIL  ") + what); if (!ok) fails++; }

    /** The picture set into a camera frame: W x H grey, the code at (x0, y0), every pixel off by up to `noise`. */
    static byte[] frame(byte[] pic, int side, int W, int H, int x0, int y0, int noise, boolean turned, Random r) {
        byte[] f = new byte[W * H];
        for (int i = 0; i < f.length; i++) f[i] = (byte) (150 + r.nextInt(2 * noise + 1) - noise);
        for (int y = 0; y < side; y++) for (int x = 0; x < side; x++) {
            int v = (pic[(turned ? x : y) * side + (turned ? side - 1 - y : x)] & 0xFF) == 0 ? 40 : 215;
            f[(y0 + y) * W + x0 + x] = (byte) Math.max(0, Math.min(255, v + r.nextInt(2 * noise + 1) - noise));
        }
        return f;
    }

    public static void main(String[] a) throws Exception {
        Random r = new Random(7);
        for (int k = 0; k + 1 < a.length; k += 2) {
            if (a[k].endsWith(".png")) {                  // a picture of the desktop's own dialog (comms-app --security "qr FILE"): the whole of it, as a camera sees a screen
                java.awt.image.BufferedImage im = javax.imageio.ImageIO.read(new File(a[k]));
                int w = im.getWidth(), h = im.getHeight();
                byte[] grey = new byte[w * h];
                for (int y = 0; y < h; y++) for (int x = 0; x < w; x++) { int c = im.getRGB(x, y); grey[y * w + x] = (byte) ((((c >> 16) & 255) * 30 + ((c >> 8) & 255) * 59 + (c & 255) * 11) / 100); }
                File dir = Files.createTempDirectory("phone-secrets").toFile();
                String text = QrRead.read(grey, w, w, h);
                check(text != null && text.startsWith(SecretQr.PREFIX), a[k] + ": the phone's reader read the code out of a picture of the desktop's dialog (" + w + "x" + h + ")");
                if (text != null) {
                    String kept = SecretQr.keep(text, dir.getPath());
                    String line = Channel.secretLine("mediahost", dir.getPath());
                    check(line.contains("hash " + a[k + 1]), "the phone kept " + kept + ": the hash the dialog shows and the desktop says (" + a[k + 1] + ")");
                }
                continue;
            }
            byte[] raw = Files.readAllBytes(new File(a[k]).toPath());
            String head = new String(raw, 0, 32, "ISO-8859-1");
            String[] h = head.split("\\s+");
            int side = Integer.parseInt(h[1]), at = raw.length - side * side;
            byte[] pic = java.util.Arrays.copyOfRange(raw, at, raw.length);
            File dir = Files.createTempDirectory("phone-secrets").toFile();
            for (int turn = 0; turn < 2; turn++) {
                byte[] f = frame(pic, side, 1280, 720, 300 + 40 * turn, 150 - 30 * turn, 18, turn == 1, r);
                String text = QrRead.read(f, 1280, 1280, 720);
                check(text != null && text.startsWith(SecretQr.PREFIX), a[k] + (turn == 1 ? ", the camera a quarter turn round" : "") + ": the phone's reader read the code out of a 1280x720 frame with noise");
                if (text == null) continue;
                ChannelFrame.forgetSecrets(dir.getPath());
                String kept = SecretQr.keep(text, dir.getPath());
                String line = Channel.secretLine("mediahost", dir.getPath());
                check(line.contains("hash " + a[k + 1]), "the phone kept " + kept + " and says of its secret for mediahost what the desktop says: " + line + " (the desktop: hash " + a[k + 1] + ")");
            }
            int n = ChannelFrame.forgetSecrets(dir.getPath());
            boolean none = false;
            try { Channel.secretLine("mediahost", dir.getPath()); } catch (ChannelFrame.Gone e) { none = true; }
            check(n == 1 && none && dir.list().length == 0, "the phone reset: " + n + " secret written over and removed, and it holds none");
        }
        byte[] grey = new byte[1280 * 720];
        check(QrRead.read(grey, 1280, 1280, 720) == null, "a frame with no code in it reads as nothing");
        boolean refused = false;
        try { SecretQr.keep("fdfinv1.eyJ2IjoxfQ", Files.createTempDirectory("x").toString()); } catch (ChannelFrame.Gone e) { refused = true; }
        check(refused, "a FrogNet invitation scanned as a secret is refused");
        System.out.println("RESULT " + (fails == 0 ? "PASS" : "FAIL") + " the phone reads the desktop's QR code of a secret: " + fails + " fail");
        System.exit(fails == 0 ? 0 : 1);
    }
}
