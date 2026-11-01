// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
package net.frognet.rangertrak.desktop;

import javafx.application.Application;
import javafx.application.Platform;
import javafx.concurrent.Worker;
import javafx.scene.Scene;
import javafx.scene.web.WebEngine;
import javafx.scene.web.WebView;
import javafx.stage.Stage;
import net.frognet.ram.Json;
import net.frognet.rangertrak.AppCore;
import netscape.javascript.JSObject;

import java.io.*;
import java.net.InetAddress;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.security.SecureRandom;
import java.util.Map;

/**
 * [RT_APP_DESKTOP_V1] RangerTrak on the desktop (Windows; Linux runs the same): the app's own page (ui/) in a window,
 * its core (AppCore) beside it, the bridge between them. Settings this computer keeps: its device id (made once), the
 * node it last used, the name, the view -- in ~/.rangertrak/settings.json. A desktop with no location source writes
 * no position (nothing is made up).
 *   RangerTrak [--ui=DIR] [--connect]
 */
public final class Main extends Application {
    private WebEngine web;
    private AppCore core;
    private final Bridge bridge = new Bridge();          // held here: the page holds it only weakly
    private static Path home() { return Paths.get(System.getProperty("user.home"), ".rangertrak"); }

    /** what the page calls: rtNative.send(line) */
    public final class Bridge {
        public void send(String line) {
            try {
                Map<String, Object> m = Json.obj(Json.parse(line));
                if ("connect".equals(Json.str(m.get("op")))) save(m);
            } catch (Exception ignored) { }
            new Thread(() -> core.handle(line), "core").start();
        }
    }

    @Override public void start(Stage stage) throws Exception {
        String ui = getParameters().getNamed().getOrDefault("ui", Paths.get(System.getProperty("rt.home", "."), "ui").toString());
        WebView v = new WebView(); web = v.getEngine();
        core = new AppCore(line -> Platform.runLater(() -> {
            try { web.executeScript("rtDeliver(" + Json.quote(line) + ")"); } catch (Exception e) { System.err.println("page: " + e); }
        }));
        web.getLoadWorker().stateProperty().addListener((o, was, now) -> {
            if (now != Worker.State.SUCCEEDED) return;
            ((JSObject) web.executeScript("window")).setMember("rtNative", bridge);
            web.executeScript("rtInit(" + Json.write(settings()) + ")");
            // --connect: straight in with this computer's saved node, name and view (Team and Public; the EOC logs in)
            if (getParameters().getRaw().contains("--connect"))
                web.executeScript("setTimeout(()=>document.querySelector('#connect button').click(),2000)");
        });
        web.setOnAlert(e -> System.out.println("page: " + e.getData()));
        web.load(Paths.get(ui, "index.html").toUri().toString());
        stage.setTitle("RangerTrak — powered by the FrogNet Living Network");
        stage.setScene(new Scene(v, 1280, 820));
        stage.setOnCloseRequest(e -> { core.close(); Platform.exit(); System.exit(0); });
        stage.show();
        System.out.println("RangerTrak desktop " + AppCore.BUILD + ", page " + ui);
    }

    // ------------------------------------------------------------------ this computer's settings
    private Map<String, Object> settings() {
        Map<String, Object> s;
        try { s = Json.obj(Json.parse(new String(Files.readAllBytes(home().resolve("settings.json")), StandardCharsets.UTF_8))); }
        catch (Exception e) { s = Json.o(); }
        if (Json.str(s.get("dev")).isEmpty()) {           // this device's own id, made once
            StringBuilder b = new StringBuilder("d"); SecureRandom r = new SecureRandom();
            for (int i = 0; i < 12; i++) b.append("0123456789abcdef".charAt(r.nextInt(16)));
            s.put("dev", b.toString()); write(s);
        }
        if (Json.str(s.get("name")).isEmpty()) try { s.put("name", InetAddress.getLocalHost().getHostName()); } catch (Exception ignored) { }
        if (Json.str(s.get("door")).isEmpty()) s.put("door", gateway());
        s.put("platform", "desktop");
        return s;
    }

    private void save(Map<String, Object> m) {
        Map<String, Object> s = settings();
        for (String k : new String[]{"door", "name", "role", "team"}) s.put(k, Json.str(m.get(k)));
        write(s);
    }

    private static void write(Map<String, Object> s) {
        try { Files.createDirectories(home()); Files.write(home().resolve("settings.json"), Json.write(s).getBytes(StandardCharsets.UTF_8)); }
        catch (IOException e) { System.err.println("settings not saved: " + e.getMessage()); }
    }

    /** the node this computer is on is its default gateway (a device on the node's Wi-Fi): shown in the Node box */
    private static String gateway() {
        try {
            if (System.getProperty("os.name").toLowerCase().contains("win")) {
                Process p = new ProcessBuilder("route", "print", "-4", "0.0.0.0").redirectErrorStream(true).start();
                BufferedReader r = new BufferedReader(new InputStreamReader(p.getInputStream()));
                for (String l; (l = r.readLine()) != null; ) {
                    String[] f = l.trim().split("\\s+");
                    if (f.length >= 3 && f[0].equals("0.0.0.0") && f[1].equals("0.0.0.0")) return f[2];
                }
            } else {
                for (String l : Files.readAllLines(Paths.get("/proc/net/route"))) {
                    String[] f = l.trim().split("\\s+");
                    if (f.length > 2 && f[1].equals("00000000")) {
                        long g = Long.parseLong(f[2], 16);
                        return (g & 255) + "." + ((g >> 8) & 255) + "." + ((g >> 16) & 255) + "." + ((g >> 24) & 255);
                    }
                }
            }
        } catch (Exception ignored) { }
        return "";
    }

    public static void main(String[] a) { launch(a); }
}
