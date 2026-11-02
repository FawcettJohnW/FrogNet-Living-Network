// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
// EnrollTest.java -- [ENROLL_V1] Enroll on a JVM: the line there and back (the same text frogqr.js writes), and a lead's
// Sponsor on a loopback port: a member enrolls and gets the invitation the lead asked the door for, plus the card; a
// wrong password, a stranger and another app are refused with their why; the door saying no is the sponsor's 502.
import net.frognet.ram.Enroll;
import net.frognet.ram.Json;
import java.util.*;

public class EnrollTest {
    static int fails = 0;
    static void check(boolean ok, String what) { System.out.println((ok ? "PASS " : "FAIL ") + what); if (!ok) fails++; }
    static String why(Exception e) { return e.getMessage() == null ? e.toString() : e.getMessage(); }

    public static void main(String[] a) throws Exception {
        Enroll.Line l = Enroll.line("http://192.168.43.1:21801/enroll", "rangertrak", "Équipe Ñandú 100%");
        Enroll.Line b = Enroll.parse(l.toString());
        check(l.toString().equals("fdfenroll1 http://192.168.43.1:21801/enroll rangertrak Équipe%20Ñandú%20100%25") && b != null
              && b.url.equals(l.url) && b.app.equals("rangertrak") && b.user.equals("Équipe Ñandú 100%"), "the line there and back: " + l);
        check(Enroll.parse("fdfinv1.abc") == null && Enroll.parse("fdfenroll1 x y") == null, "not a line: null");

        final Map<String, Map<String, Object>> members = new HashMap<String, Map<String, Object>>();
        members.put("KJ7ABC", Json.o("verifier", Enroll.verifier("frog-2026")));
        members.put("N2DH", Json.o("verifier", Enroll.verifier("ham")));
        final List<String> asked = new ArrayList<String>();
        Enroll.Sponsor sp = new Enroll.Sponsor(0, "rangertrak", "alpha", new Enroll.Team() {
            public Map<String, Object> member(String n) { return members.get(n); }
            public String identity(String n) throws Exception {
                asked.add(n);
                if (n.equals("N2DH")) throw new Exception("the region's hub is not answering");
                return "fdfinv1.member-" + n;
            }
            public Map<String, Object> card() { return Json.o("door", "10.170.170.1", "team", "alpha", "invite", "the card cannot set this"); }
            public void log(String line) { System.out.println("  sponsor: " + line); }
        });
        String url = "http://127.0.0.1:" + sp.port() + "/enroll";

        Map<String, Object> ok = Enroll.enroll(Enroll.line(url, "rangertrak", "KJ7ABC"), "frog-2026");
        check(Json.str(ok.get("invite")).equals("fdfinv1.member-KJ7ABC") && Json.str(ok.get("role")).equals("alpha") && !Json.bool(ok.get("lead"))
              && Json.str(ok.get("door")).equals("10.170.170.1") && asked.equals(Arrays.asList("KJ7ABC")), "a member enrolls: " + Json.write(ok));
        String w1 = "", w2 = "", w3 = "", w4 = "";
        try { Enroll.enroll(Enroll.line(url, "rangertrak", "KJ7ABC"), "wrong"); } catch (Exception e) { w1 = why(e); }
        try { Enroll.enroll(Enroll.line(url, "rangertrak", "stranger"), "x"); } catch (Exception e) { w2 = why(e); }
        try { Enroll.enroll(Enroll.line(url, "otherapp", "KJ7ABC"), "frog-2026"); } catch (Exception e) { w3 = why(e); }
        try { Enroll.enroll(Enroll.line(url, "rangertrak", "N2DH"), "ham"); } catch (Exception e) { w4 = why(e); }
        check(w1.contains("not a member of alpha"), "a wrong password: " + w1);
        check(w2.contains("not a member of alpha"), "a stranger: " + w2);
        check(w3.contains("not that app"), "another app: " + w3);
        check(w4.contains("did not give N2DH an invitation") && w4.contains("not answering"), "the door saying no: " + w4);
        check(asked.equals(Arrays.asList("KJ7ABC", "N2DH")), "the door was asked only for members who gave their password: " + asked);
        sp.close();
        System.out.println(fails == 0 ? "ALL PASS" : fails + " failed"); System.exit(fails == 0 ? 0 : 1);
    }
}
