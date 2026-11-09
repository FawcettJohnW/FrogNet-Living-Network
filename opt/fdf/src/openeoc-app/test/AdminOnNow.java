// Copyright (C) 2016-2026 Fawcett Innovations LLC. SPDX-License-Identifier: GPL-2.0-only
// AdminOnNow DOOR APP -- [ADMIN_ON_NOW_V1] the admin core's who-is-on-now by itself (test_no_mission.py): its door and app
// set, startOnNow run, its events printed, one JSON line each
import java.lang.reflect.*;
public class AdminOnNow {
    public static void main(String[] a) throws Exception {
        net.frognet.openeoc.admin.AdminCore c = new net.frognet.openeoc.admin.AdminCore(new net.frognet.openeoc.admin.AdminCore.Ui() {
            public void deliver(String j) { System.out.println(j); System.out.flush(); } });
        for (String[] kv : new String[][]{{"door", a[0]}, {"app", a[1]}}) { Field f = c.getClass().getDeclaredField(kv[0]); f.setAccessible(true); f.set(c, kv[1]); }
        Method m = c.getClass().getDeclaredMethod("startOnNow"); m.setAccessible(true); m.invoke(c);
        Thread.sleep(Long.parseLong(a.length > 2 ? a[2] : "15000"));
        System.exit(0);
    }
}
