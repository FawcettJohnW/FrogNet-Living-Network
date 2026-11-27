// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
package net.frognet.communicator.core;

/**
 * [A_SECRET_IS_SHOWN_TO_BE_SCANNED_V1] What a machine's QR code of a Role's secret says (comms-app, Security tab, "Show the
 * secret as a QR code"; src/comms/secret_qr.cpp). It is a FrogNet line, as an invitation (fdfinv1.) and a join line
 * (fdfjoin1.) are (John, 27 Nov 2026: "The QR code should be a FrogNet QR code"):
 *     fdfsec1.[base64url, no padding, of the JSON {"v":1,"name":NAME,"step":STEP,"secret":BASE64URL}]
 * name: "default" (the network's secret) or the Role's name (its own secret, at that step). The line is never logged,
 * shown or written to a tuple: it is parsed, kept by ChannelFrame.placeSecret, and dropped.
 */
public final class SecretQr {
    public static final String PREFIX = "fdfsec1.";
    public String name; public long step; public byte[] value;

    public static SecretQr parse(String line) throws ChannelFrame.Gone {
        if (line == null || !line.startsWith(PREFIX)) throw new ChannelFrame.Gone("that code is not a FrogNet secret (it does not start " + PREFIX + ")");
        java.util.Map<String, Object> j;
        try { j = Json.obj(Json.parse(new String(java.util.Base64.getUrlDecoder().decode(line.substring(PREFIX.length()).trim()), java.nio.charset.StandardCharsets.UTF_8))); }
        catch (RuntimeException e) { throw new ChannelFrame.Gone("that code is not a whole FrogNet secret"); }
        if (Json.num(j.get("v")) != 1) throw new ChannelFrame.Gone("that code is a FrogNet secret of a version this app does not read (v " + Json.str(j.get("v")) + ")");
        SecretQr s = new SecretQr();
        s.name = Json.str(j.get("name"));
        s.step = Json.num(j.get("step"));
        String b = Json.str(j.get("secret"));
        if (!b.matches("[A-Za-z0-9_-]+")) throw new ChannelFrame.Gone("that code's secret is not base64url");
        try { s.value = java.util.Base64.getUrlDecoder().decode(b); }
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
