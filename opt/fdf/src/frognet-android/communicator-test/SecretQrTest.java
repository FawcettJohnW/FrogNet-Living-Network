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
 * SecretQrTest DIALOG.png HASH [DIALOG.png HASH ...] -- [A_SECRET_IS_SHOWN_TO_BE_SCANNED_V1][FROG_QR_V1] each picture is the
 * desktop Communicator's own dialog showing a secret as a FrogNet QR code (comms-app --security "qr FILE"), each hash
 * what that machine says of the same secret (comms-channel secret). The phone's own reader (QrRead, the scanner's)
 * reads the code as the screen shows it, then out of a 1280x720 grey camera frame the picture is set into, off centre,
 * dimmer, with noise, upright and a quarter turn round; the phone keeps what it read (SecretQr.keep) and must then say
 * the same hash. Then the phone is reset and must hold nothing.
 */
public final class SecretQrTest {
    static int fails = 0;
    static void check(boolean ok, String what) { System.out.println((ok ? "PASS  " : "FAIL  ") + what); if (!ok) fails++; }

    public static void main(String[] a) throws Exception {
        Random r = new Random(7);
        for (int k = 0; k + 1 < a.length; k += 2) {
            if (a[k].endsWith(".png")) {                  // a picture of the desktop's own dialog (comms-app --security "qr FILE"): the FrogNet QR code as a screen shows it
                java.awt.image.BufferedImage im = javax.imageio.ImageIO.read(new File(a[k]));
                int w = im.getWidth(), h = im.getHeight();
                byte[] grey = new byte[w * h];
                for (int y = 0; y < h; y++) for (int x = 0; x < w; x++) { int c = im.getRGB(x, y); grey[y * w + x] = (byte) ((((c >> 16) & 255) * 30 + ((c >> 8) & 255) * 59 + (c & 255) * 11) / 100); }
                File dir = Files.createTempDirectory("phone-secrets").toFile();
                // as the dialog is; then as the phone's camera has it: in a 1280x720 frame off centre with noise, upright and a quarter turn round
                String[] how = {"as the screen shows it", "in a 1280x720 camera frame with noise", "in a camera frame, a quarter turn round, with noise"};
                for (int v = 0; v < 3; v++) {
                    byte[] f = grey; int fw = w, fh = h;
                    if (v > 0) {
                        boolean turned = v == 2; int pw = turned ? h : w, ph = turned ? w : h;
                        fw = 1280; fh = 720; f = new byte[fw * fh];
                        for (int i = 0; i < f.length; i++) f[i] = (byte) (150 + r.nextInt(25) - 12);
                        int x0 = (fw - pw) / 2 + 60, y0 = Math.max(0, (fh - ph) / 2);
                        for (int y = 0; y < ph && y0 + y < fh; y++) for (int x = 0; x < pw; x++) {
                            int g = grey[(turned ? x : y) * w + (turned ? w - 1 - y : x)] & 0xFF;
                            f[(y0 + y) * fw + x0 + x] = (byte) Math.max(0, Math.min(255, g * 3 / 4 + 30 + r.nextInt(25) - 12));
                        }
                    }
                    String text = QrRead.read(f, fw, fw, fh);
                    check(text != null && text.startsWith(SecretQr.PREFIX), a[k] + ": the phone's reader read the FrogNet QR code " + how[v]);
                    if (text == null) continue;
                    ChannelFrame.forgetSecrets(dir.getPath());
                    String kept = SecretQr.keep(text, dir.getPath());
                    String line = Channel.secretLine("mediahost", dir.getPath());
                    check(line.contains("hash " + a[k + 1]), "the phone kept " + kept + ": the hash the dialog shows and the desktop says (" + a[k + 1] + ")");
                }
                int n = ChannelFrame.forgetSecrets(dir.getPath());
                boolean none = false;
                try { Channel.secretLine("mediahost", dir.getPath()); } catch (ChannelFrame.Gone e) { none = true; }
                check(n == 1 && none && dir.list().length == 0, "the phone reset: " + n + " secret written over and removed, and it holds none");
                continue;
            }
            check(false, a[k] + " is not a picture of the dialog (.png)");
        }
        byte[] grey = new byte[1280 * 720];
        check(QrRead.read(grey, 1280, 1280, 720) == null, "a frame with no code in it reads as nothing");
        boolean refused = false;
        try { SecretQr.keep("fdfenroll1 http://10.170.170.1/dc/ram.php openeoc Ranger7", Files.createTempDirectory("x").toString()); } catch (ChannelFrame.Gone e) { refused = true; }
        check(refused, "a FrogNet enrollment code scanned as a secret is refused");
        System.out.println("RESULT " + (fails == 0 ? "PASS" : "FAIL") + " the phone reads the desktop's QR code of a secret: " + fails + " fail");
        System.exit(fails == 0 ? 0 : 1);
    }
}
