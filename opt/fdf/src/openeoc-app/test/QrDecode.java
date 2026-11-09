// Copyright (C) 2016-2026 Fawcett Innovations LLC. SPDX-License-Identifier: GPL-2.0-only
// QrDecode.java -- [FROG_QR_V1] reads QR codes from images with zxing core 3.4.1, the library the phone's scanner
// (zxing-android-embedded 4.3.0) decodes with, and its default hints (no TRY_HARDER): one line per image, the text or FAIL.
import com.google.zxing.*;
import com.google.zxing.common.HybridBinarizer;
import com.google.zxing.qrcode.QRCodeReader;
import javax.imageio.ImageIO;
import java.awt.image.BufferedImage;
import java.io.File;
import java.util.EnumMap;
import java.util.Map;

public class QrDecode {
    public static void main(String[] a) throws Exception {
        for (String p : a) {
            BufferedImage im = ImageIO.read(new File(p));
            int w = im.getWidth(), h = im.getHeight();
            int[] px = im.getRGB(0, 0, w, h, null, 0, w);
            Map<DecodeHintType, Object> hints = new EnumMap<>(DecodeHintType.class);
            hints.put(DecodeHintType.CHARACTER_SET, "UTF-8");
            try {
                Result r = new QRCodeReader().decode(new BinaryBitmap(new HybridBinarizer(new RGBLuminanceSource(w, h, px))), hints);
                System.out.println(p + "\t" + r.getText());
            } catch (NotFoundException | ChecksumException | FormatException e) {
                System.out.println(p + "\tFAIL " + e.getClass().getSimpleName());
            }
        }
    }
}
