// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
// EnrollTest.java -- [ENROLL_V1] Enroll on a JVM: the line there and back (the same text frogqr.js writes), and a lead's
// Sponsor on a loopback port: a member enrolls and gets the invitation the lead asked the door for, plus the card; a
// wrong password, a stranger and another app are refused with their why; the door saying no is the sponsor's 502.
// [SEALED_ENROLL_V1] the lead's line carries its key: what goes between device and lead (caught by a relay between them,
// both ways) has neither the password nor the invitation; an unsealed request, a QR with another key, a request sent
// again, and a line whose fifth part is not a key are refused; a door's line (four parts) is as it was.
import net.frognet.ram.Enroll;
import net.frognet.ram.Json;
import java.util.*;

public class EnrollTest {
    static int fails = 0;
    static void check(boolean ok, String what) { System.out.println((ok ? "PASS " : "FAIL ") + what); if (!ok) fails++; }
    static String why(Exception e) { return e.getMessage() == null ? e.toString() : e.getMessage(); }

    static void pipe(final java.io.InputStream in, final java.io.OutputStream out, final java.io.ByteArrayOutputStream copy) {
        Thread t = new Thread(new Runnable() { public void run() {
            byte[] b = new byte[4096]; int n;
            try { while ((n = in.read(b)) > 0) { synchronized (copy) { copy.write(b, 0, n); } out.write(b, 0, n); out.flush(); } out.close(); } catch (Exception e) { }
        } }); t.setDaemon(true); t.start();
    }
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
        @SuppressWarnings("unchecked") final Map<String, Object>[] teamPw = new Map[1];
        Enroll.Sponsor sp = new Enroll.Sponsor(0, "rangertrak", "alpha", new Enroll.Team() {
            public Map<String, Object> member(String n) { return members.get(n); }
            public String identity(String n) throws Exception {
                asked.add(n);
                if (n.equals("N2DH")) throw new Exception("the region's hub is not answering");
                return "fdfinv1.member-" + n;
            }
            public Map<String, Object> card() { return Json.o("at", "10.170.170.1", "team", "alpha", "invite", "the card cannot set this"); }
            public void log(String line) { System.out.println("  sponsor: " + line); }
            public Map<String, Object> teamVerifier() { return teamPw[0]; }
            public void joined(String n) { members.put(n, Json.o("joined", "team password")); }
        });
        String url = "http://127.0.0.1:" + sp.port() + "/enroll";
        final String K = sp.fingerprint();

        Map<String, Object> ok = Enroll.enroll(Enroll.line(url, "rangertrak", "KJ7ABC", K), "frog-2026");
        check(Json.str(ok.get("invite")).equals("fdfinv1.member-KJ7ABC") && Json.str(ok.get("role")).equals("alpha") && !Json.bool(ok.get("lead"))
              && Json.str(ok.get("at")).equals("10.170.170.1") && asked.equals(Arrays.asList("KJ7ABC")), "a member enrolls: " + Json.write(ok));
        String w1 = "", w2 = "", w3 = "", w4 = "";
        try { Enroll.enroll(Enroll.line(url, "rangertrak", "KJ7ABC", K), "wrong"); } catch (Exception e) { w1 = why(e); }
        try { Enroll.enroll(Enroll.line(url, "rangertrak", "stranger", K), "x"); } catch (Exception e) { w2 = why(e); }
        try { Enroll.enroll(Enroll.line(url, "otherapp", "KJ7ABC", K), "frog-2026"); } catch (Exception e) { w3 = why(e); }
        try { Enroll.enroll(Enroll.line(url, "rangertrak", "N2DH", K), "ham"); } catch (Exception e) { w4 = why(e); }
        check(w1.contains("not a member of alpha"), "a wrong password: " + w1);
        check(w2.contains("not a member of alpha"), "a stranger: " + w2);
        check(w3.contains("not that app"), "another app: " + w3);
        check(w4.contains("did not give N2DH an invitation") && w4.contains("not answering"), "the door saying no: " + w4);
        check(asked.equals(Arrays.asList("KJ7ABC", "N2DH")), "the door was asked only for members who gave their password: " + asked);
        // [TEAM_JOIN_V1] one QR for the team ("*"), one password: a member gives their own call sign; never a member the lead made
        Enroll.Line tq = Enroll.parse(Enroll.line(url, "rangertrak", Enroll.TEAM, K).toString());
        String asTeam = ""; try { Enroll.enroll(tq, "alpha-team"); } catch (Exception e) { asTeam = why(e); }
        check(tq != null && tq.team() && tq.as("W7NEW").user.equals("W7NEW") && !tq.as("W7NEW").team() && asTeam.contains("call sign"),
              "the team's QR there and back (" + Enroll.line(url, "rangertrak", Enroll.TEAM, K) + "); it takes a call sign: " + asTeam);
        String noTeam = ""; try { Enroll.enroll(Enroll.line(url, "rangertrak", "W7NEW", K), "alpha-team"); } catch (Exception e) { noTeam = why(e); }
        teamPw[0] = Enroll.verifier("alpha-team");
        Map<String, Object> j = Enroll.enroll(tq.as("W7NEW"), "alpha-team");
        Map<String, Object> j2 = Enroll.enroll(tq.as("W7NEW"), "alpha-team");
        String bad = ""; try { Enroll.enroll(tq.as("W7 NEW ONE"), "alpha-team"); } catch (Exception e) { bad = why(e); }
        String taken = "", wrongTeam = "";
        try { Enroll.enroll(Enroll.line(url, "rangertrak", "KJ7ABC", K), "alpha-team"); } catch (Exception e) { taken = why(e); }
        try { Enroll.enroll(Enroll.line(url, "rangertrak", "W7OTHER", K), "not-it"); } catch (Exception e) { wrongTeam = why(e); }
        check(noTeam.contains("not a member") && Json.str(j.get("invite")).equals("fdfinv1.member-W7NEW") && members.containsKey("W7NEW") && Json.str(j2.get("invite")).equals("fdfinv1.member-W7NEW")
              && taken.contains("not a member") && wrongTeam.contains("not a member") && !members.containsKey("W7OTHER") && bad.contains("not a member"),
              "the team's password: none set, refused; set, W7NEW joins (and again); not as KJ7ABC (" + taken + "); a wrong one, and a call sign with spaces, refused");
        // [SEALED_ENROLL_V1]
        Enroll.Line kl = Enroll.parse(Enroll.line(url, "rangertrak", Enroll.TEAM, K).toString());
        check(kl != null && kl.key.equals(K) && kl.as("W7NEW").key.equals(K) && kl.toString().split(" ").length == 5 && K.length() == 22 && Enroll.Seal.fingerprint(sp.key()).equals(K)
              && Enroll.parse("fdfenroll1 http://x/enroll rangertrak * notakey") == null && Enroll.parse("fdfenroll1 http://x/dc/ram.php rangertrak *").key.isEmpty(),
              "the lead's line has its key's fingerprint (" + K.length() + " chars) there and back; a fifth part that is not a key: not a line; a door's line: no key");
        final java.io.ByteArrayOutputStream up = new java.io.ByteArrayOutputStream(), down = new java.io.ByteArrayOutputStream();
        final java.net.ServerSocket relay = new java.net.ServerSocket(0); final int sport = sp.port();
        Thread rt = new Thread(new Runnable() { public void run() {
            try { while (true) { final java.net.Socket c = relay.accept(); final java.net.Socket t = new java.net.Socket("127.0.0.1", sport);
                pipe(c.getInputStream(), t.getOutputStream(), up); pipe(t.getInputStream(), c.getOutputStream(), down); } } catch (Exception e) { }
        } }); rt.setDaemon(true); rt.start();
        String rurl = "http://127.0.0.1:" + relay.getLocalPort() + "/enroll";
        Map<String, Object> viaRelay = Enroll.enroll(Enroll.line(rurl, "rangertrak", "KJ7ABC", K), "frog-2026");
        Thread.sleep(300);
        String caught = new String(up.toByteArray(), "UTF-8") + new String(down.toByteArray(), "UTF-8");
        check(Json.str(viaRelay.get("invite")).equals("fdfinv1.member-KJ7ABC") && caught.contains("\"ct\"") && !caught.contains("frog-2026") && !caught.contains("fdfinv1") && !caught.contains("KJ7ABC"),
              "sealed: KJ7ABC enrolls through a relay; the relay caught " + caught.length() + " bytes with no password, no invitation, no call sign");
        String plain = ""; try { Enroll.post(url + "?op=enroll&app=rangertrak", Json.write(Json.o("user", "KJ7ABC", "password", "frog-2026")), 10000); } catch (Exception e) { plain = why(e); }
        check(plain.contains("sealed enrollments only"), "an unsealed request is refused: " + plain);
        String other = ""; try { Enroll.enroll(Enroll.line(url, "rangertrak", "KJ7ABC", Enroll.Seal.fingerprint(Enroll.Seal.publicOf(Enroll.Seal.newPair()))), "frog-2026"); } catch (Exception e) { other = why(e); }
        check(other.contains("not the one in its QR"), "a QR with another key's fingerprint: " + other);
        String req = new String(up.toByteArray(), "UTF-8"); req = req.substring(req.lastIndexOf("\r\n\r\n") + 4);
        int before = asked.size();
        Map<String, Object> again = Enroll.postRaw(url + "?op=enroll&app=rangertrak", req, 10000);
        check(asked.size() == before && again.get("ct") != null && again.get("why") == null, "a request sent again: the door not asked (" + asked.size() + "), the answer sealed (no why to read)");
        relay.close();
        sp.close();
        System.out.println(fails == 0 ? "ALL PASS" : fails + " failed"); System.exit(fails == 0 ? 0 : 1);
    }
}
