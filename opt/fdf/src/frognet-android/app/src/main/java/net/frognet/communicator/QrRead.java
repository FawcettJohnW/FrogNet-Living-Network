// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
package net.frognet.communicator;

import com.google.zxing.BinaryBitmap;
import com.google.zxing.DecodeHintType;
import com.google.zxing.PlanarYUVLuminanceSource;
import com.google.zxing.ReaderException;
import com.google.zxing.common.HybridBinarizer;
import com.google.zxing.qrcode.QRCodeReader;

import java.util.EnumMap;
import java.util.Map;

/** A QR code read from a grey picture: the camera's Y plane, as the scanner hands it over (ZXing core, no Android). */
final class QrRead {
    private static final Map<DecodeHintType, Object> HINTS = new EnumMap<DecodeHintType, Object>(DecodeHintType.class);
    static { HINTS.put(DecodeHintType.TRY_HARDER, Boolean.TRUE); }

    /** The code's text, or null when this picture shows none that reads. grey: rows of rowStride bytes, w x h of them used. */
    static String read(byte[] grey, int rowStride, int w, int h) {
        try {
            PlanarYUVLuminanceSource src = new PlanarYUVLuminanceSource(grey, rowStride, h, 0, 0, w, h, false);
            return new QRCodeReader().decode(new BinaryBitmap(new HybridBinarizer(src)), HINTS).getText();
        } catch (ReaderException e) {
            return null;                                   // not in this picture: the next one
        }
    }
    private QrRead() { }
}
