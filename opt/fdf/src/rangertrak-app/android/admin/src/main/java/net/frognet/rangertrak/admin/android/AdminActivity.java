// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
package net.frognet.rangertrak.admin.android;

import android.app.Activity;
import android.content.SharedPreferences;
import android.os.Bundle;
import android.util.Log;
import android.webkit.JavascriptInterface;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import net.frognet.ram.Json;
import net.frognet.rangertrak.admin.AdminCore;
import java.io.File;
import java.util.Map;

/**
 * [ADMIN_PHONE_APP_V1] the RangerTrak admin app on the phone (John 2026-11-04: "the phone Admin app"): the page in
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
            v.evaluateJavascript("rtInit(" + Json.write(Json.o("door", prefs.getString("door", ""), "app", prefs.getString("app", "rangertrak"),
                    "user", prefs.getString("user", ""))) + ")", null);
        } });
        setContentView(web);
        web.loadUrl("file:///android_asset/ui-admin/index.html");
        Log.i("RangerTrakAdmin", "RangerTrak Admin " + AdminCore.BUILD + " " + getPackageName());   // [APP_SAYS_ITS_BUILD_V1]
    }

    @Override protected void onDestroy() { core.close(); super.onDestroy(); }
}
