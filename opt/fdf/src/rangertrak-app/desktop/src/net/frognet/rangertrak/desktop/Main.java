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
        /** [RT_CALL_V1] the page's Call: what this computer's Communicator can use, then the Communicator itself */
        public void comms(String json) { Main.this.comms(json); }
        public void startComms(String json) { new Thread(() -> Main.this.startComms(json), "comms").start(); }
        /** [CHAT_NOTICE_V1] a message the user cannot see: a Windows notice when this window is not in front */
        public void notice(String from, String text) {
            if (stage != null && stage.isFocused()) return;      // the page's own notice is enough
            java.awt.EventQueue.invokeLater(() -> {
                try {
                    if (!java.awt.SystemTray.isSupported()) return;
                    if (tray == null) {
                        java.awt.image.BufferedImage img = new java.awt.image.BufferedImage(16, 16, java.awt.image.BufferedImage.TYPE_INT_ARGB);
                        java.awt.Graphics2D g = img.createGraphics(); g.setColor(new java.awt.Color(0x3f, 0xb3, 0x6b)); g.fillOval(0, 0, 16, 16); g.dispose();
                        tray = new java.awt.TrayIcon(img, "RangerTrak"); tray.setImageAutoSize(true);
                        java.awt.SystemTray.getSystemTray().add(tray);
                    }
                    tray.displayMessage("RangerTrak: " + from, text, java.awt.TrayIcon.MessageType.INFO);
                } catch (Exception e) { System.err.println("notice: " + e); }
            });
        }
    }
    private Stage stage;
    private java.awt.TrayIcon tray;

    // ------------------------------------------------------------------ [RT_CALL_V1] the Communicator (comms-app)
    /** rtNative.comms({peer, name, door}): what this computer has (comms-app --list-devices), to the page's picker */
    public void comms(String json) {
        Map<String, Object> m; try { m = Json.obj(Json.parse(json)); } catch (Exception e) { return; }
        Platform.runLater(() -> {
            String exe = commsExe();
            if (exe.isEmpty()) { deliver(Json.o("ev", "error", "text", "no Communicator chosen: Call needs comms-app")); return; }
            new Thread(() -> {
                Map<String, Object> s = settings(), ev = devices(exe);
                String door = Json.str(m.get("door"));
                Map<String, Object> saved = Json.obj(s.get("comms"));
                ev.put("ev", "devices"); ev.put("peer", Json.str(m.get("peer"))); ev.put("answer", Boolean.TRUE.equals(m.get("answer"))); ev.put("name", Json.str(m.get("name")));
                ev.put("ram", door + ":8800"); ev.put("media", door + ":8994"); ev.put("saved", saved);
                deliver(ev);
            }, "comms-devices").start();
        });
    }
    /** rtNative.startComms({peer, name, camera, mic, speaker, ram, media}): the Communicator, joined to the peer */
    public void startComms(String json) {
        try {
            Map<String, Object> m = Json.obj(Json.parse(json)), s = settings();
            s.put("comms", Json.o("camera", Json.str(m.get("camera")), "mic", Json.str(m.get("mic")), "speaker", Json.str(m.get("speaker"))));
            write(s);
            java.util.List<String> c = new java.util.ArrayList<>(java.util.Arrays.asList(commsExeSaved(),
                    "--ram", Json.str(m.get("ram")), "--media", Json.str(m.get("media")), "--name", Json.str(m.get("name"))));
            // calling: --auto-join places the call once the peer is in the room. Answering: the Communicator rings with the
            // caller's invitation and is answered there (--auto-join on both ends would have each call the other)
            boolean answering = Boolean.TRUE.equals(m.get("answer"));
            if (!answering && !Json.str(m.get("peer")).isEmpty()) { c.add("--auto-join"); c.add(Json.str(m.get("peer"))); }
            if (!Json.str(m.get("camera")).isEmpty()) { c.add("--camera"); c.add(Json.str(m.get("camera"))); }
            if (!Json.str(m.get("mic")).isEmpty()) { c.add("--mic"); c.add(Json.str(m.get("mic"))); }
            if (!Json.str(m.get("speaker")).isEmpty()) { c.add("--speaker"); c.add(Json.str(m.get("speaker"))); }
            File log = home().resolve("comms-app.log").toFile();
            ProcessBuilder pb = new ProcessBuilder(c).redirectErrorStream(true).redirectOutput(ProcessBuilder.Redirect.appendTo(log));
            File dir = new File(c.get(0)).getParentFile(); if (dir != null) pb.directory(dir);
            pb.start();
            deliver(Json.o("ev", "status", "text", (answering ? "the Communicator is opening: answer " + Json.str(m.get("peer")) + " there" : "the Communicator is opening and calls " + Json.str(m.get("peer"))) + " (its log: " + log + ")"));
        } catch (Exception e) { deliver(Json.o("ev", "error", "text", "the Communicator did not start: " + e.getMessage())); }
    }
    private void deliver(Map<String, Object> ev) {
        String line = Json.write(ev);
        Platform.runLater(() -> { try { web.executeScript("rtDeliver(" + Json.quote(line) + ")"); } catch (Exception e) { System.err.println("page: " + e); } });
    }
    private String commsExeSaved() { return Json.str(settings().get("commsApp")); }
    /** comms-app: the one this computer was told, else beside RangerTrak, else asked for once (FX thread) */
    private String commsExe() {
        String p = commsExeSaved();
        if (!p.isEmpty() && new File(p).canExecute()) return p;
        boolean win = System.getProperty("os.name").toLowerCase().contains("win");
        String n = win ? "comms-app.exe" : "comms-app";
        Path base = Paths.get(System.getProperty("rt.home", ".")).toAbsolutePath();
        for (Path c : new Path[]{base.resolve(n), base.resolve("comms").resolve(n), base.resolve("bin").resolve(n),
                                 base.getParent() == null ? base.resolve(n) : base.getParent().resolve("Communicator").resolve(n),
                                 base.getParent() == null ? base.resolve(n) : base.getParent().resolve("comms").resolve(n)})
            if (Files.isExecutable(c)) return remember(c.toString());
        javafx.stage.FileChooser fc = new javafx.stage.FileChooser();
        fc.setTitle("Where is the FrogNet Communicator (" + n + ")?");
        File f = fc.showOpenDialog(stage);
        return f == null ? "" : remember(f.getAbsolutePath());
    }
    private String remember(String exe) { Map<String, Object> s = settings(); s.put("commsApp", exe); write(s); return exe; }
    /** comms-app --list-devices, parsed: cameras by name, audio devices by number (mic: has inputs; speaker: outputs) */
    public static Map<String, Object> devices(String exe) {
        java.util.List<Object> cams = new java.util.ArrayList<>(), mics = new java.util.ArrayList<>(), spk = new java.util.ArrayList<>();
        String note = "";
        try {
            ProcessBuilder pb = new ProcessBuilder(exe, "--list-devices").redirectErrorStream(true);
            File dir = new File(exe).getParentFile(); if (dir != null) pb.directory(dir);
            Process p = pb.start();
            java.util.regex.Pattern cam = java.util.regex.Pattern.compile("^\\s*--camera \"(.*)\"\\s+\\((.*)\\)\\s*$");
            java.util.regex.Pattern aud = java.util.regex.Pattern.compile("^(\\d+): (.*?)\\s+\\(in (\\d+), out (\\d+), (\\d+) Hz\\)\\s*$");
            BufferedReader r = new BufferedReader(new InputStreamReader(p.getInputStream(), StandardCharsets.UTF_8));
            for (String l; (l = r.readLine()) != null; ) {
                java.util.regex.Matcher a = cam.matcher(l), b = aud.matcher(l);
                if (a.matches()) cams.add(Json.o("value", a.group(1), "label", a.group(2).isEmpty() ? a.group(1) : a.group(2)));
                else if (b.matches()) {
                    String lab = b.group(2) + " (" + b.group(5) + " Hz)";
                    if (Integer.parseInt(b.group(3)) > 0) mics.add(Json.o("n", b.group(1), "label", lab));
                    if (Integer.parseInt(b.group(4)) > 0) spk.add(Json.o("n", b.group(1), "label", lab));
                }
            }
            if (!p.waitFor(20, java.util.concurrent.TimeUnit.SECONDS)) { p.destroyForcibly(); note = "comms-app --list-devices did not finish"; }
        } catch (Exception e) { note = "comms-app --list-devices: " + e.getMessage(); }
        if (note.isEmpty() && cams.isEmpty()) note = "no camera found: the call is voice only unless one is chosen";
        return Json.o("cameras", cams, "mics", mics, "speakers", spk, "note", note);
    }

    @Override public void start(Stage stage) throws Exception {
        this.stage = stage;
        String ui = getParameters().getNamed().getOrDefault("ui", Paths.get(System.getProperty("rt.home", "."), "ui").toString());
        WebView v = new WebView(); web = v.getEngine();
        core = new AppCore(line -> Platform.runLater(() -> {
            try { web.executeScript("rtDeliver(" + Json.quote(line) + ")"); } catch (Exception e) { System.err.println("page: " + e); }
        }), layers());                                   // [RT_LAYERS_V1] the core's layers: the page's
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
        stage.setTitle(("eoc".equals(lock()) ? "RangerTrak EOC" : "RangerTrak") + " — powered by the FrogNet Living Network");   // [RT_EOC_APP_V1]
        stage.setScene(new Scene(v, 1280, 820));
        stage.setOnCloseRequest(e -> { core.close(); Platform.exit(); System.exit(0); });
        stage.show();
        System.out.println("RangerTrak desktop " + AppCore.BUILD + ", page " + ui + ", layers " + (layers().isEmpty() ? "all" : layers()));
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
        if (!lock().isEmpty()) { s.put("lock", lock()); s.put("role", lock()); }   // [RT_EOC_APP_V1] --lock=eoc: the EOC app
        java.util.List<Object> ls = new java.util.ArrayList<Object>();
        for (String n : layers().split(",")) if (!n.trim().isEmpty()) ls.add(n.trim());
        s.put("layers", ls);                              // [RT_LAYERS_V1] the page loads these (none named: every one)
        return s;
    }
    /** [RT_LAYERS_V1] this app's layers (--layers=help,field,eoc,medical: the EOC app's), or "" for every one */
    private String layers() { return getParameters() == null ? "" : getParameters().getNamed().getOrDefault("layers", ""); }
    /** [RT_EOC_APP_V1] the one view this app opens as (--lock=eoc: RangerTrak EOC), or "" for every view */
    private String lock() { return getParameters() == null ? "" : getParameters().getNamed().getOrDefault("lock", "");
    }

    private void save(Map<String, Object> m) {
        Map<String, Object> s = settings();
        for (String k : new String[]{"door", "name", "role", "team", "unit"}) s.put(k, Json.str(m.get(k)));
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
