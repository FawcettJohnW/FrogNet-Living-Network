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
            public Map<String, Object> card() { return Json.o("at", "10.170.170.1", "team", "alpha", "invite", "the card cannot set this"); }
            public void log(String line) { System.out.println("  sponsor: " + line); }
            public void keyUsed(String n) { Map<String, Object> r = new HashMap<String, Object>(members.get(n)); r.remove("key"); members.put(n, r); }
        });
        String url = "http://127.0.0.1:" + sp.port() + "/enroll";

        Map<String, Object> ok = Enroll.enroll(Enroll.line(url, "rangertrak", "KJ7ABC"), "frog-2026");
        check(Json.str(ok.get("invite")).equals("fdfinv1.member-KJ7ABC") && Json.str(ok.get("role")).equals("alpha") && !Json.bool(ok.get("lead"))
              && Json.str(ok.get("at")).equals("10.170.170.1") && asked.equals(Arrays.asList("KJ7ABC")), "a member enrolls: " + Json.write(ok));
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
        // [ENROLL_KEY_V1] the lead's one-time sign-in code: the key in the line (there and back), the member signed in with
        // no password; the same code again refused; an expired one refused; the password still works
        String key = Enroll.newKey();
        Map<String, Object> rec = new HashMap<String, Object>(members.get("KJ7ABC"));
        rec.put("key", Json.o("verifier", Enroll.verifier(key), "until", System.currentTimeMillis() + 15 * 60 * 1000L)); members.put("KJ7ABC", rec);
        Enroll.Line kl = Enroll.line(url, "rangertrak", "KJ7ABC", key), kb = Enroll.parse(kl.toString());
        check(kb != null && kb.key.equals(key) && kl.toString().endsWith(" " + key) && key.length() == 22, "a sign-in line there and back: " + kl);
        Map<String, Object> byKey = Enroll.enroll(kb, "");
        String again = "", old = "";
        try { Enroll.enroll(kb, ""); } catch (Exception e) { again = why(e); }
        String key2 = Enroll.newKey(); rec = new HashMap<String, Object>(members.get("KJ7ABC"));
        rec.put("key", Json.o("verifier", Enroll.verifier(key2), "until", System.currentTimeMillis() - 1000)); members.put("KJ7ABC", rec);
        try { Enroll.enroll(Enroll.line(url, "rangertrak", "KJ7ABC", key2), ""); } catch (Exception e) { old = why(e); }
        Map<String, Object> pw = Enroll.enroll(Enroll.line(url, "rangertrak", "KJ7ABC"), "frog-2026");
        check(Json.str(byKey.get("invite")).equals("fdfinv1.member-KJ7ABC") && again.contains("used or expired") && old.contains("used or expired")
              && Json.str(pw.get("invite")).equals("fdfinv1.member-KJ7ABC") && !Enroll.parse(kl.toString()).key.isEmpty() && Enroll.parse(Enroll.line(url, "rangertrak", "x").toString()).key.isEmpty(),
              "signed in by the code, no password (" + Json.str(byKey.get("invite")) + "); again: " + again + "; expired: " + old + "; the password still: " + Json.str(pw.get("invite")));
        sp.close();
        System.out.println(fails == 0 ? "ALL PASS" : fails + " failed"); System.exit(fails == 0 ? 0 : 1);
    }
}
