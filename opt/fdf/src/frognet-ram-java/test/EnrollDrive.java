// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
// EnrollDrive.java -- [ENROLL_V1] Enroll from a command line, for tests on real networks (rinab/tools/netns-enroll-test.sh):
//   enroll "fdfenroll1 URL APP USER" PASSWORD      what a device does after scanning: the answer, as JSON, on stdout
//   sponsor PORT APP ROLE DOOR LEAD LEADPW NAME:PW ...
//                                                  what a team lead's phone runs: a Sponsor whose members are NAME:PW
//                                                  (kept as verifiers), asking DOOR's op=identity as LEAD for each
import net.frognet.ram.Enroll;
import net.frognet.ram.Json;
import java.net.URLEncoder;
import java.util.*;

public class EnrollDrive {
    public static void main(String[] a) throws Exception {
        if (a.length == 3 && a[0].equals("enroll")) {
            Enroll.Line l = Enroll.parse(a[1]);
            if (l == null) { System.err.println("not an enrollment line: " + a[1]); System.exit(2); }
            try { System.out.println(Json.write(Enroll.enroll(l, a[2]))); }
            catch (Exception e) { System.out.println(Json.write(Json.o("ok", false, "why", e.getMessage()))); System.exit(3); }
            return;
        }
        if (a.length >= 7 && a[0].equals("sponsor")) {
            final String app = a[2], role = a[3], door = a[4], lead = a[5], leadPw = a[6];
            final Map<String, Map<String, Object>> members = new HashMap<String, Map<String, Object>>();
            for (int i = 7; i < a.length; i++) { String[] p = a[i].split(":", 2); members.put(p[0], Json.o("verifier", Enroll.verifier(p[1]))); }
            new Enroll.Sponsor(Integer.parseInt(a[1]), app, role, new Enroll.Team() {
                public Map<String, Object> member(String n) { return members.get(n); }
                public String identity(String n) throws Exception {
                    Map<String, Object> r = Enroll.post("http://" + door + "/dc/ram.php?op=identity&app=" + URLEncoder.encode(app, "UTF-8"),
                            Json.write(Json.o("user", lead, "password", leadPw, "name", n)), 90000);
                    return Json.str(r.get("invite"));
                }
                public Map<String, Object> card() { return Json.o("at", door, "team", role, "leader", lead); }
                public void log(String line) { System.out.println(line); System.out.flush(); }
            });
            Thread.sleep(Long.MAX_VALUE);
        }
        System.err.println("usage: EnrollDrive enroll LINE PASSWORD | sponsor PORT APP ROLE DOOR LEAD LEADPW NAME:PW ..."); System.exit(2);
    }
}
