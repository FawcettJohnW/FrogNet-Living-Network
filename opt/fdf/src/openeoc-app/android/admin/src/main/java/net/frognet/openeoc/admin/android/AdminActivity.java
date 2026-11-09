// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
package net.frognet.openeoc.admin.android;

import android.app.Activity;
import android.content.ContentValues;
import android.content.SharedPreferences;
import android.net.Uri;
import android.provider.MediaStore;
import android.util.Base64;
import android.os.Bundle;
import android.util.Log;
import android.webkit.JavascriptInterface;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import net.frognet.ram.Json;
import net.frognet.openeoc.admin.AdminCore;
import java.io.File;
import java.io.OutputStream;
import java.util.Map;

/**
 * [ADMIN_PHONE_APP_V1] the OpenEOC admin app on the phone (John 2026-11-04: "the phone Admin app"): the page in
 * ui-admin/ and AdminCore, as the Windows admin app (desktop/AdminMain) has them -- the page sends lines to the core
 * (rtNative.send), the core answers the page (rtDeliver). It keeps the node, the app and the admin's user name in its
 * preferences; never the password. Saved assignments go in the app's own files directory.
 */
public final class AdminActivity extends Activity {
    private WebView web;
    private AdminCore core;
    private SharedPreferences prefs;

    public final class Bridge {
        @JavascriptInterface public void send(final String line) {
            try {
                Map<String, Object> m = Json.obj(Json.parse(line));
                if ("connect".equals(Json.str(m.get("op"))))
                    prefs.edit().putString("door", Json.str(m.get("door"))).putString("app", Json.str(m.get("app")))
                         .putString("user", Json.str(m.get("user"))).apply();
            } catch (Exception ignored) { }
            new Thread(new Runnable() { public void run() { core.handle(line); } }, "admin-core").start();
        }
        // [QR_SAVE_V1] a user's enrollment QR as a PNG in Pictures/OpenEOC (the gallery: print it, share it)
        @JavascriptInterface public void saveFile(final String name, final String base64) {
            String say;
            try {
                ContentValues v = new ContentValues();
                v.put(MediaStore.Images.Media.DISPLAY_NAME, name);
                v.put(MediaStore.Images.Media.MIME_TYPE, "image/png");
                v.put(MediaStore.Images.Media.RELATIVE_PATH, "Pictures/OpenEOC");
                Uri u = getContentResolver().insert(MediaStore.Images.Media.EXTERNAL_CONTENT_URI, v);
                if (u == null) throw new Exception("the gallery gave no place for it");
                try (OutputStream o = getContentResolver().openOutputStream(u)) { o.write(Base64.decode(base64, Base64.DEFAULT)); }
                say = Json.write(Json.o("ev", "saved", "path", "Pictures/OpenEOC/" + name));
            } catch (Exception e) {
                say = Json.write(Json.o("ev", "error", "text", name + " not saved: " + e.getMessage()));
            }
            final String line = say;
            runOnUiThread(new Runnable() { public void run() { web.evaluateJavascript("rtDeliver(" + Json.quote(line) + ")", null); } });
        }
    }

    @Override protected void onCreate(Bundle b) {
        super.onCreate(b);
        prefs = getSharedPreferences("admin", MODE_PRIVATE);
        web = new WebView(this);
        WebSettings s = web.getSettings(); s.setJavaScriptEnabled(true); s.setDomStorageEnabled(true);
        core = new AdminCore(new AdminCore.Ui() { public void deliver(final String json) {
            runOnUiThread(new Runnable() { public void run() { web.evaluateJavascript("rtDeliver(" + Json.quote(json) + ")", null); } });
        } }, new File(getFilesDir(), "FrogNet-assignments"));
        web.addJavascriptInterface(new Bridge(), "rtNative");
        web.setWebViewClient(new WebViewClient() { @Override public void onPageFinished(WebView v, String url) {
            v.evaluateJavascript("rtInit(" + Json.write(Json.o("door", prefs.getString("door", ""), "app", prefs.getString("app", "openeoc"),
                    "user", prefs.getString("user", ""))) + ")", null);
        } });
        setContentView(web);
        web.loadUrl("file:///android_asset/ui-admin/index.html");
        Log.i("OpenEOCAdmin", "OpenEOC Admin " + AdminCore.BUILD + " " + getPackageName());   // [APP_SAYS_ITS_BUILD_V1]
    }

    @Override protected void onDestroy() { core.close(); super.onDestroy(); }
}
