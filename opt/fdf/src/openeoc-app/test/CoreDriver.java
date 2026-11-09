// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// CoreDriver -- one AppCore with no page: each line on stdin is handed to it (as the page would), each line it delivers
// is printed on stdout. The tests drive several devices this way (test_team_host.py).
import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.nio.charset.StandardCharsets;
import net.frognet.openeoc.AppCore;

public final class CoreDriver {
    public static void main(String[] a) throws Exception {
        final AppCore core = new AppCore(new AppCore.Ui() {
            public void deliver(String line) { synchronized (System.out) { System.out.println(line); System.out.flush(); } }
        });
        BufferedReader in = new BufferedReader(new InputStreamReader(System.in, StandardCharsets.UTF_8));
        for (String l; (l = in.readLine()) != null; ) { final String line = l; new Thread(new Runnable() { public void run() { core.handle(line); } }).start(); }
        core.close();
    }
}
