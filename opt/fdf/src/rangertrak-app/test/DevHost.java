// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
// DevHost UI_DIR PORT DEV NAME DOOR [ROLE TEAM [LOCK]] -- [RT_APP_TEST_V1] the app's real core (AppCore) and its real page,
// for the tests only: the page is served to a browser here, and the native bridge the desktop and phone give it
// (rtNative.send / rtDeliver) is carried over HTTP -- POST /send for a line to the core, GET /recv?after=N held until
// the core has said something past N. Nothing else differs from the apps. -Drt.core=admin: the admin app's core
// (AdminCore) and page (ui-admin/admin.js) instead [ADMIN_APP_V1].
import com.sun.net.httpserver.*;
import net.frognet.rangertrak.AppCore;
import java.io.*;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.util.*;

public class DevHost {
    static final List<String> lines = new ArrayList<>();
    public static void main(String[] a) throws Exception {
        Path ui = Paths.get(a[0]); int port = Integer.parseInt(a[1]);
        String init = "{\"dev\":\"" + a[2] + "\",\"name\":\"" + a[3] + "\",\"door\":\"" + a[4] + "\",\"role\":\"" + (a.length > 5 ? a[5] : "team")
                + "\",\"team\":\"" + (a.length > 6 ? a[6] : "Alpha") + "\",\"platform\":\"devhost\""
                + (a.length > 7 ? ",\"lock\":\"" + a[7] + "\"" : "") + ",\"build\":\"" + net.frognet.rangertrak.AppCore.BUILD + "\"}";   // LOCK: the Team / Public app
        boolean admin = "admin".equals(System.getProperty("rt.core"));
        java.util.function.Consumer<String> core;
        if (admin) { net.frognet.rangertrak.admin.AdminCore c = new net.frognet.rangertrak.admin.AdminCore(l -> { synchronized (lines) { lines.add(l); lines.notifyAll(); } }); core = c::handle; }
        else { AppCore c = new AppCore(l -> { synchronized (lines) { lines.add(l); lines.notifyAll(); } }); core = c::handle; }
        String js = admin ? "admin.js" : "app.js";
        HttpServer s = HttpServer.create(new InetSocketAddress("127.0.0.1", port), 0);
        s.setExecutor(java.util.concurrent.Executors.newCachedThreadPool());
        s.createContext("/send", x -> { String l = new String(x.getRequestBody().readAllBytes(), StandardCharsets.UTF_8);
            new Thread(() -> core.accept(l)).start(); reply(x, 200, "ok", "text/plain"); });
        s.createContext("/recv", x -> {
            int after = Integer.parseInt(x.getRequestURI().getQuery().replace("after=", ""));
            List<String> out = new ArrayList<>(); long end = System.currentTimeMillis() + 25000;
            synchronized (lines) {
                while (lines.size() <= after && System.currentTimeMillis() < end) try { lines.wait(1000); } catch (InterruptedException e) { }
                out.addAll(lines.subList(Math.min(after, lines.size()), lines.size()));
            }
            reply(x, 200, "[" + String.join(",", out) + "]", "application/json"); });
        s.createContext("/", x -> {
            String p = x.getRequestURI().getPath(); if (p.equals("/")) p = "/index.html";
            Path f = ui.resolve(p.substring(1)).normalize();
            if (!f.startsWith(ui) || !Files.exists(f)) { reply(x, 404, "no", "text/plain"); return; }
            String type = p.endsWith(".js") ? "text/javascript" : p.endsWith(".css") ? "text/css" : p.endsWith(".png") ? "image/png" : "text/html";
            byte[] b = Files.readAllBytes(f);
            if (p.equals("/index.html")) b = new String(b, StandardCharsets.UTF_8).replace("<script src=\"" + js + "\"></script>",
                "<script src=\"" + js + "\"></script><script>window.rtNative={send:(l)=>fetch('/send',{method:'POST',body:l})};" +
                "(async()=>{let n=0;rtInit(" + init + ");for(;;){try{const r=await (await fetch('/recv?after='+n)).json();" +
                "for(const l of r){n++;rtDeliver(l)}}catch(e){await new Promise(z=>setTimeout(z,500))}}})()</script>").getBytes(StandardCharsets.UTF_8);
            x.getResponseHeaders().set("Content-Type", type); x.sendResponseHeaders(200, b.length); x.getResponseBody().write(b); x.close(); });
        s.start(); System.out.println("devhost on " + port); System.out.flush();
    }
    static void reply(HttpExchange x, int c, String t, String type) throws IOException {
        byte[] b = t.getBytes(StandardCharsets.UTF_8); x.getResponseHeaders().set("Content-Type", type); x.sendResponseHeaders(c, b.length);
        x.getResponseBody().write(b); x.close();
    }
}
