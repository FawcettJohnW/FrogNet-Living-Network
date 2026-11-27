// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
package net.frognet.communicator.core;

/**
 * [A_SECRET_IS_SHOWN_TO_BE_SCANNED_V1] What a machine's QR code of a Role's secret says (comms-app, Security tab, "Show the
 * secret as a QR code"; src/comms/secret_qr.cpp). The code is a FrogNet QR code ([FROG_QR_V1]: lily pads, eyes, the
 * logo's frog in the middle, as OpenEOC's are) and what it holds is a FrogNet line, short and plain as the enrollment
 * line (fdfenroll1 URL APP USER) is:
 *     fdfsec1 NAME STEP SECRET        (parts apart by one space; SECRET is base64url without padding)
 * NAME: "default" (the network's secret) or the Role's name (its own secret, at that step). The line is never logged,
 * shown or written to a tuple: it is parsed, kept by ChannelFrame.placeSecret, and dropped.
 */
public final class SecretQr {
    public static final String PREFIX = "fdfsec1 ";
    public String name; public long step; public byte[] value;

    public static SecretQr parse(String line) throws ChannelFrame.Gone {
        if (line == null || !line.startsWith(PREFIX)) throw new ChannelFrame.Gone("that code is not a FrogNet secret (it does not start fdfsec1)");
        String[] p = line.trim().split(" ", -1);
        if (p.length != 4) throw new ChannelFrame.Gone("that code is not a whole FrogNet secret");
        SecretQr s = new SecretQr();
        s.name = p[1];
        try { s.step = Long.parseLong(p[2]); } catch (NumberFormatException e) { throw new ChannelFrame.Gone("that code's step is not a number"); }
        if (!p[3].matches("[A-Za-z0-9_-]+")) throw new ChannelFrame.Gone("that code's secret is not base64url");
        try { s.value = java.util.Base64.getUrlDecoder().decode(p[3]); }
        catch (IllegalArgumentException e) { throw new ChannelFrame.Gone("that code's secret does not decode"); }
        return s;
    }

    /** Parses and keeps it in dir; returns what a screen may say: the name, the hash, the step. */
    public static String keep(String text, String dir) throws ChannelFrame.Gone {
        SecretQr s = parse(text);
        ChannelFrame.placeSecret(s.name, s.value, s.step, dir);
        return (s.name.equals("default") ? "the network's default secret" : "the secret of '" + s.name + "'") + ", hash " + ChannelFrame.secretFingerprint(s.value)
                + (s.step > 0 ? ", at step " + s.step : "");
    }
}
