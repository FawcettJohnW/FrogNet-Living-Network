// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// Vectors.java IN OUT -- runs the Java Bearer / abortPad / Reassembler over the vectors gen_vectors.py wrote from the
// Python implementation (frogcomms/fnav.py, itself proven equal to fnav.py), and writes what Java decided.
import net.frognet.communicator.core.*;
import java.io.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.util.*;

public class Vectors {
    public static void main(String[] a) throws Exception {
        List<String> in = Files.readAllLines(Paths.get(a[0]), StandardCharsets.UTF_8);
        PrintWriter out = new PrintWriter(new FileWriter(a[1]));
        Bearer b = null;
        Fnav.Reassembler r = new Fnav.Reassembler();
        for (String line : in) {
            String[] f = line.split(" ");
            switch (f[0]) {
                case "B": b = new Bearer(Integer.parseInt(f[1]), Integer.parseInt(f[2]), Double.parseDouble(f[3])); break;
                case "S": {
                    Double fps = f[4].equals("None") ? null : Double.valueOf(f[4]);
                    int idx = b.sample(Double.parseDouble(f[1]), Integer.parseInt(f[2]), Integer.parseInt(f[3]), fps,
                                       Double.parseDouble(f[5]), Integer.parseInt(f[6]));
                    out.println(idx);
                    break;
                }
                case "R": b.resumeAt(Integer.parseInt(f[1]), Double.parseDouble(f[2])); out.println(b.idx); break;
                case "P": {
                    byte[] p = Fnav.abortPad(Integer.parseInt(f[1]));
                    StringBuilder h = new StringBuilder();
                    for (byte x : p) h.append(String.format("%02x", x & 0xFF));
                    out.println(h);
                    break;
                }
                case "G": {                                   // G src hexpayload
                    byte[] p = hex(f.length > 2 ? f[2] : "");
                    byte[] done = r.feed(f[1], p, 0, p.length);
                    out.println(done == null ? "-" : hexs(done));
                    break;
                }
                case "C": out.println(r.done + " " + r.aborted + " " + r.partial + " " + r.gap + " " + r.superseded + " " + r.bad); break;
            }
        }
        out.close();
    }
    static byte[] hex(String s) { byte[] o = new byte[s.length() / 2]; for (int i = 0; i < o.length; i++) o[i] = (byte) Integer.parseInt(s.substring(2 * i, 2 * i + 2), 16); return o; }
    static String hexs(byte[] b) { StringBuilder h = new StringBuilder(); for (byte x : b) h.append(String.format("%02x", x & 0xFF)); return h.length() == 0 ? "=" : h.toString(); }
}
