// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
package net.frognet.openeoc.desktop;

import javafx.application.Application;
import javafx.application.Platform;
import javafx.concurrent.Worker;
import javafx.scene.Scene;
import javafx.scene.web.WebEngine;
import javafx.scene.web.WebView;
import javafx.stage.Stage;
import net.frognet.ram.Json;
import net.frognet.openeoc.AppCore;
import netscape.javascript.JSObject;

import java.io.*;
import java.net.InetAddress;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.security.SecureRandom;
import java.util.Map;

/**
 * [RT_APP_DESKTOP_V1] OpenEOC on the desktop (Windows; Linux runs the same): the app's own page (ui/) in a window,
 * its core (AppCore) beside it, the bridge between them. Settings this computer keeps: its device id (made once), the
 * node it last used, the name, the view -- in ~/.openeoc/settings.json. A desktop with no location source writes
 * no position (nothing is made up).
 *   OpenEOC [--ui=DIR] [--connect]
 */
public final class Main extends Application {
    private WebEngine web;
    private AppCore core;
    private final Bridge bridge = new Bridge();          // held here: the page holds it only weakly
    private static Path home() { return Paths.get(System.getProperty("user.home"), ".openeoc"); }

    /** what the page calls: rtNative.send(line) */
    public final class Bridge {
        public void send(String line) {
            try {
                Map<String, Object> m = Json.obj(Json.parse(line));
                if ("connect".equals(Json.str(m.get("op")))) save(m);
            } catch (Exception ignored) { }
            new Thread(() -> core.handle(line), "core").start();
        }
        /** [RT_NO_GPS_V1] the address the user gave: this computer's position from now on */
        public void saveAddress(String json) {
            try { Map<String, Object> s = settings(); s.put("addr", Json.obj(Json.parse(json))); write(s, settingsFile()); } catch (Exception e) { System.err.println("address: " + e); }
        }
        /** [RT_CALL_V1] the page's Call: what this computer's Communicator can use, then the Communicator itself */
        public void comms(String json) { Main.this.comms(json); }
        /** [FEMA_EOC_V1] the page's Print (EOCAdmin's forms, the IAP, the after-action report): this computer's printer
         *  dialog, then the page as it prints (its print rules show the sheet only) */
        public void print(String title) {
            Platform.runLater(() -> {
                javafx.print.PrinterJob job = javafx.print.PrinterJob.createPrinterJob();
                if (job == null) { pageSay("⚠ no printer on this computer"); return; }
                job.getJobSettings().setJobName(title);
                if (!job.showPrintDialog(stage)) { job.cancelJob(); return; }
                web.print(job);
                if (!job.endJob()) pageSay("⚠ the printer did not take " + title + ": " + job.getJobStatus());
            });
        }
        /** [RT_MAP_SAYS_V1] the page's own account of its map, in this window's log */
        public void log(String line) { System.out.println(line); }
        public void startComms(String json) { new Thread(() -> Main.this.startComms(json), "comms").start(); }
        /** [CHAT_NOTICE_V1] a message the user cannot see: a Windows notice when this window is not in front */
        public void notice(String from, String text) {
            if (stage != null && stage.isFocused()) return;      // the page's own notice is enough
            java.awt.EventQueue.invokeLater(() -> {
                try {
                    if (!java.awt.SystemTray.isSupported()) return;
                    if (tray == null) {
                        java.awt.Image img = javax.imageio.ImageIO.read(Paths.get(uiDir, "openeoc-mark-256.png").toFile());   // [OPENEOC_BRAND_V1]
                        tray = new java.awt.TrayIcon(img, "OpenEOC"); tray.setImageAutoSize(true);
                        java.awt.SystemTray.getSystemTray().add(tray);
                    }
                    tray.displayMessage("OpenEOC: " + from, text, java.awt.TrayIcon.MessageType.INFO);
                } catch (Exception e) { System.err.println("notice: " + e); }
            });
        }
    }
    private Stage stage;
    private java.awt.TrayIcon tray;
    private volatile String uiDir = "ui";   // [OPENEOC_BRAND_V1] where the page and the mark are

    // ------------------------------------------------------------------ [RT_CALL_V1] the Communicator (comms-app)
    /** rtNative.comms({peer, name, door}): what this computer has (comms-app --list-devices), to the page's picker */
    public void comms(String json) {
        Map<String, Object> m; try { m = Json.obj(Json.parse(json)); } catch (Exception e) { return; }
        Platform.runLater(() -> {
            String exe = commsExe();
            if (exe.isEmpty()) return;                  // said by commsExe
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
            write(s, settingsFile());
            String exe = commsExe(false); if (exe.isEmpty()) { deliver(Json.o("ev", "error", "text", "no Communicator on this computer: Call again to choose it")); return; }
            java.util.List<String> c = new java.util.ArrayList<>(java.util.Arrays.asList(exe,
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
    /** [ONE_COMMUNICATOR_V1] (John 2026-11-10: "the comms-app is part of the bundle thrown down, but also such that there is
     *  only one on the device") one Communicator per computer, for every OpenEOC app on it: the bundle's comms\comms-app.exe
     *  when the bundle carries one; else the one this computer was told (~/.openeoc/communicator.json, one file for every
     *  app; the path an app saved before, its commsApp, is taken into it); else asked for once, on the FX thread, and kept
     *  there. r73 to r75 used the bundle's only and broke the calls of a computer whose Communicator was elsewhere (John:
     *  "It has been working"). "" : none (asked: none chosen, said). */
    private String commsExe() { return commsExe(true); }
    private String commsExe(boolean ask) {
        boolean win = System.getProperty("os.name").toLowerCase().contains("win");
        String n = win ? "comms-app.exe" : "comms-app";
        Path exe = bundle().resolve("comms").resolve(n);
        if (Files.isRegularFile(exe) && Files.isExecutable(exe)) return exe.toString();
        String known = knownComms();
        if (!known.isEmpty() && Files.isRegularFile(Paths.get(known))) return known;
        if (!ask) return "";
        javafx.stage.FileChooser fc = new javafx.stage.FileChooser();
        fc.setTitle("Where is the FrogNet Communicator (" + n + ")? Asked once for every OpenEOC app on this computer");
        File f = fc.showOpenDialog(stage);
        if (f == null) { deliver(Json.o("ev", "error", "text", "no Communicator chosen: Call needs " + n)); return ""; }
        keepComms(f.getAbsolutePath()); return f.getAbsolutePath();
    }
    private static Path commsFile() { return home().resolve("communicator.json"); }
    private String knownComms() {
        try { String p = Json.str(Json.obj(Json.parse(new String(Files.readAllBytes(commsFile()), StandardCharsets.UTF_8))).get("exe")); if (!p.isEmpty()) return p; }
        catch (Exception ignored) { }
        String old = Json.str(settings().get("commsApp"));            // the path this app saved before r73
        if (!old.isEmpty() && Files.isRegularFile(Paths.get(old))) { keepComms(old); return old; }
        return "";
    }
    private static void keepComms(String exe) {
        try { Files.createDirectories(home()); Files.write(commsFile(), Json.write(Json.o("exe", exe)).getBytes(StandardCharsets.UTF_8)); }
        catch (IOException e) { System.err.println("communicator.json not saved: " + e.getMessage()); }
    }
    /** the bundle's folder: the one the ui folder (--ui) is in */
    private Path bundle() {
        String ui = getParameters().getNamed().getOrDefault("ui", Paths.get(System.getProperty("rt.home", "."), "ui").toString());
        Path p = Paths.get(ui).toAbsolutePath().getParent();
        return p == null ? Paths.get(".").toAbsolutePath() : p;
    }
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

    /** a line on the page's status line (on the FX thread) */
    private void pageSay(String t) { try { web.executeScript("status(" + Json.quote(t) + ")"); } catch (Exception e) { System.err.println("page: " + e); } }

    @Override public void start(Stage stage) throws Exception {
        this.stage = stage;
        String ui = getParameters().getNamed().getOrDefault("ui", Paths.get(System.getProperty("rt.home", "."), "ui").toString());
        uiDir = ui;
        WebView v = new WebView(); web = v.getEngine();
        core = new AppCore(line -> Platform.runLater(() -> {
            try { web.executeScript("rtDeliver(" + Json.quote(line) + ")"); } catch (Exception e) { System.err.println("page: " + e); }
        }), layers());                                   // [RT_LAYERS_V1] the core's layers: the page's
        web.getLoadWorker().stateProperty().addListener((o, was, now) -> {
            if (now != Worker.State.SUCCEEDED) return;
            ((JSObject) web.executeScript("window")).setMember("rtNative", bridge);
            web.executeScript("rtInit(" + Json.write(settings()) + ")");
            // [RT_NO_GPS_V1] a computer has no GPS: the page uses the address saved here, or the node's own position
            // ([RT_AT_ITS_NODE_V1]) until one is given. Before r44 the desktop never said so: it had no position at all,
            // and nobody else's map drew it (John 2026-11-05: a Team on Windows, on no one's map but its own)
            Object addr = settings().get("addr");
            web.executeScript("rtDeliver(" + Json.quote(Json.write(Json.o("ev", "nogps", "why", "This computer has no GPS of its own.", "saved", addr instanceof Map ? addr : null))) + ")");
            // --connect: straight in with this computer's saved node, name and view (Team and Public; the EOC logs in)
            if (getParameters().getRaw().contains("--connect"))
                web.executeScript("setTimeout(()=>document.querySelector('#connect button').click(),2000)");
        });
        web.setOnAlert(e -> System.out.println("page: " + e.getData()));
        web.load(Paths.get(ui, "index.html").toUri().toString());
        // [WINDOW_SAYS_ITS_VIEW_V1] (John 2026-11-09: "The Windows tools need to say what UI is running in the title bar") the
        // window's title is the page's: the view it opens as, or the one it connected as (OpenEOC EOCAdmin, OpenEOC Team · Bigdogs ...)
        String first = lock().isEmpty() ? "OpenEOC" : "OpenEOC -- " + ("eocadmin".equals(lock()) ? "EOCAdmin" : "eoc".equals(lock()) ? "CommandCenter" : lock());   // [NAMES_ARE_THE_CONVENTION_V1]
        stage.setTitle(first + " — powered by the FrogNet Living Network");
        web.titleProperty().addListener((o, was, now) -> { if (now != null && !now.trim().isEmpty()) stage.setTitle(now.trim() + " — powered by the FrogNet Living Network"); });
        // [OPENEOC_BRAND_V1] the OpenEOC mark as the window's icon (taskbar, title bar), from the page's own folder
        java.nio.file.Path mark = Paths.get(ui, "openeoc-mark-256.png");
        if (java.nio.file.Files.exists(mark)) stage.getIcons().add(new javafx.scene.image.Image(mark.toUri().toString()));
        else System.err.println("no window icon: " + mark + " is missing");
        stage.setScene(new Scene(v, 1280, 820));
        stage.setOnCloseRequest(e -> { core.close(); Platform.exit(); System.exit(0); });
        stage.show();
        System.out.println("OpenEOC desktop " + AppCore.BUILD + ", page " + ui + ", layers " + (layers().isEmpty() ? "all" : layers()));
    }

    // ------------------------------------------------------------------ this computer's settings
    private Map<String, Object> settings() {
        Map<String, Object> s;
        try { s = Json.obj(Json.parse(new String(Files.readAllBytes(home().resolve(settingsFile())), StandardCharsets.UTF_8))); }
        catch (Exception e) { s = Json.o(); }
        // [RT_APP_SETTINGS_APART_V1] what this computer remembers, only: which app this is (its lock, its layers) is the
        // app's own, never read from the file (r31 and before wrote the EOC app's lock into the one settings.json, and the
        // OpenEOC app then opened as the EOC)
        Map<String, Object> kept = Json.o();
        for (String k : KEPT) if (s.containsKey(k)) kept.put(k, s.get(k));
        s = kept;
        if (Json.str(s.get("dev")).isEmpty()) {           // this device's own id, made once
            StringBuilder b = new StringBuilder("d"); SecureRandom r = new SecureRandom();
            for (int i = 0; i < 12; i++) b.append("0123456789abcdef".charAt(r.nextInt(16)));
            s.put("dev", b.toString()); write(s, settingsFile());
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
    /** [RT_EOC_APP_V1] the one view this app opens as (--lock=eoc: OpenEOC EOC), or "" for every view */
    private String lock() { return getParameters() == null ? "" : getParameters().getNamed().getOrDefault("lock", "");
    }

    private void save(Map<String, Object> m) {
        Map<String, Object> s = settings();
        for (String k : new String[]{"door", "name", "role", "team", "unit"}) s.put(k, Json.str(m.get(k)));
        write(s, settingsFile());
    }
    private static final String[] KEPT = {"dev", "door", "name", "role", "team", "unit", "comms", "commsApp", "addr"};   // [ONE_COMMUNICATOR_V1] commsApp read once into communicator.json
    /** [RT_APP_SETTINGS_APART_V1] each app its own file: settings.json the OpenEOC app's, settings-eoc.json the EOC app's
     *  (its own device id too: both may run on one computer) */
    private String settingsFile() { return lock().isEmpty() ? "settings.json" : "settings-" + lock().replaceAll("[^a-z0-9_-]", "") + ".json"; }

    private static void write(Map<String, Object> s0, String file) {
        Map<String, Object> s = Json.o(); for (String k : KEPT) if (s0.containsKey(k)) s.put(k, s0.get(k));
        try { Files.createDirectories(home()); Files.write(home().resolve(file), Json.write(s).getBytes(StandardCharsets.UTF_8)); }
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
