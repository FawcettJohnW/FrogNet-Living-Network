// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
package net.frognet.openeoc.desktop;

import javafx.application.Application;
import javafx.application.Platform;
import javafx.concurrent.Worker;
import javafx.scene.Scene;
import javafx.scene.web.WebEngine;
import javafx.scene.web.WebView;
import javafx.stage.FileChooser;
import javafx.stage.Stage;
import net.frognet.ram.Json;
import net.frognet.openeoc.admin.AdminCore;
import netscape.javascript.JSObject;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.util.Base64;
import java.util.Map;

/**
 * [ADMIN_APP_V1] the OpenEOC admin app on the desktop, standalone: its own window, its own page (ui-admin/), its own
 * core (AdminCore). It keeps the node, the app and the admin's user name in ~/.openeoc/admin.json; never the password.
 *   OpenEOCAdmin [--ui=DIR]
 */
public final class AdminMain extends Application {
    private WebEngine web;
    private AdminCore core;
    private Stage stage;
    private final Bridge bridge = new Bridge();
    private static Path file() { return Paths.get(System.getProperty("user.home"), ".openeoc", "admin.json"); }

    public final class Bridge {
        public void send(String line) {
            try {
                Map<String, Object> m = Json.obj(Json.parse(line));
                if ("connect".equals(Json.str(m.get("op"))))
                    save(Json.o("door", Json.str(m.get("door")), "app", Json.str(m.get("app")), "user", Json.str(m.get("user"))));
            } catch (Exception ignored) { }
            new Thread(() -> core.handle(line), "admin-core").start();
        }
        // [QR_SAVE_V1] a user's enrollment QR as a PNG, where the admin says (a save dialog)
        public void saveFile(String name, String base64) {
            Platform.runLater(() -> {
                FileChooser fc = new FileChooser();
                fc.setTitle("Save " + name); fc.setInitialFileName(name);
                fc.getExtensionFilters().add(new FileChooser.ExtensionFilter("PNG image", "*.png"));
                Path d = Paths.get(System.getProperty("user.home"), "Pictures");
                if (Files.isDirectory(d)) fc.setInitialDirectory(d.toFile());
                java.io.File f = fc.showSaveDialog(stage);
                String line;
                if (f == null) line = Json.write(Json.o("ev", "status", "text", name + ": not saved"));
                else try {
                    Files.write(f.toPath(), Base64.getDecoder().decode(base64));
                    line = Json.write(Json.o("ev", "saved", "path", f.getAbsolutePath()));
                } catch (Exception e) { line = Json.write(Json.o("ev", "error", "text", name + " not saved: " + e.getMessage())); }
                web.executeScript("rtDeliver(" + Json.quote(line) + ")");
            });
        }
    }

    @Override public void start(Stage stage) {
        this.stage = stage;
        String ui = getParameters().getNamed().getOrDefault("ui", Paths.get(System.getProperty("rt.home", "."), "ui-admin").toString());
        WebView v = new WebView(); web = v.getEngine();
        core = new AdminCore(line -> Platform.runLater(() -> {
            try { web.executeScript("rtDeliver(" + Json.quote(line) + ")"); } catch (Exception e) { System.err.println("page: " + e); }
        }));
        web.getLoadWorker().stateProperty().addListener((o, was, now) -> {
            if (now != Worker.State.SUCCEEDED) return;
            ((JSObject) web.executeScript("window")).setMember("rtNative", bridge);
            web.executeScript("rtInit(" + Json.write(load()) + ")");
        });
        web.load(Paths.get(ui, "index.html").toUri().toString());
        stage.setTitle("OpenEOC Admin — powered by the FrogNet Living Network");
        stage.setScene(new Scene(v, 1200, 820));
        stage.setOnCloseRequest(e -> { core.close(); Platform.exit(); System.exit(0); });
        stage.show();
        System.out.println("OpenEOC Admin " + AdminCore.BUILD + ", page " + ui);
    }

    private static Map<String, Object> load() {
        try { return Json.obj(Json.parse(new String(Files.readAllBytes(file()), StandardCharsets.UTF_8))); }
        catch (Exception e) { return Json.o(); }
    }
    private static void save(Map<String, Object> s) {
        try { Files.createDirectories(file().getParent()); Files.write(file(), Json.write(s).getBytes(StandardCharsets.UTF_8)); }
        catch (IOException e) { System.err.println("admin settings not saved: " + e.getMessage()); }
    }

    public static void main(String[] a) { launch(a); }
}
