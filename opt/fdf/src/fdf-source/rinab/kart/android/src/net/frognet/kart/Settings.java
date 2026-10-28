// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
package net.frognet.kart;

import android.app.Activity;
import android.content.SharedPreferences;
import android.graphics.Color;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.text.InputType;
import android.util.Log;
import android.util.TypedValue;
import android.view.View;
import android.widget.Button;
import android.widget.CheckBox;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;

import com.fawcettinnovations.fdf.android.MemberService;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.TreeMap;

import net.frognet.kart.core.Json;

/**
 * [SETTINGS_ARE_TUPLES_V1] The settings pages keep nothing of their own: each shows tuples of the forward pond's
 * memory and Save writes one tuple there, permanent, on this device's member session:
 *     Device        (pond, cap, w:<guid>)        {name, telem_on, telem_gps, telem_period_s, ...}  followed by the member (Telemetry)
 *     Kart          (pond, cap, w:<guid>:kart)   {laps, auto, bots, kart}                           followed by this app (follow())
 *     Communicator  (pond, cap, w:<guid>:comms)  {comms_ram, comms_media}                           followed by this app
 * Whoever writes one of them -- this page, fdf-monitor, any program on the pond -- the device or app follows it with a
 * held read. Keys a page does not show are kept as they are. The Device page also shows (pond, telem, w:<guid>), what
 * this device reports, exactly as every monitor reads it.
 */
final class Settings {
    static final String TAG = MainActivity.TAG;
    interface Host { void closeSettings(); void disconnect(); }

    private final Activity a;
    private final Host host;
    final ScrollView root;
    private final LinearLayout page;
    String shown = "device";

    Settings(Activity a, Host host) {
        this.a = a; this.host = host;
        root = new ScrollView(a); root.setBackgroundColor(Color.rgb(16, 20, 28)); root.setTag("settings");
        LinearLayout outer = new LinearLayout(a); outer.setOrientation(LinearLayout.VERTICAL); root.addView(outer);
        LinearLayout head = new LinearLayout(a); head.setOrientation(LinearLayout.VERTICAL);
        head.setBackground(new GradientDrawable(GradientDrawable.Orientation.LEFT_RIGHT, new int[]{Color.rgb(40, 120, 70), Color.rgb(30, 70, 140)}));
        head.setPadding(dp(24), dp(14), dp(24), dp(14));
        TextView t = new TextView(a); t.setText("Settings"); t.setTextSize(28); t.setTextColor(Color.WHITE); t.setTypeface(Typeface.DEFAULT_BOLD); head.addView(t);
        TextView s = new TextView(a); s.setText("Each page is a tuple in your pond's memory; Save writes it, and the device or the app follows it");
        s.setTextColor(Color.rgb(220, 235, 225)); s.setTextSize(13); head.addView(s);
        outer.addView(head);
        LinearLayout tabs = new LinearLayout(a); tabs.setOrientation(LinearLayout.HORIZONTAL); tabs.setPadding(dp(16), dp(12), dp(16), 0);
        tabs.addView(tab("Device", "device"), new LinearLayout.LayoutParams(0, dp(44), 1));
        tabs.addView(tab("Kart", "kart"), new LinearLayout.LayoutParams(0, dp(44), 1));
        tabs.addView(tab("Communicator", "comms"), new LinearLayout.LayoutParams(0, dp(44), 1));
        outer.addView(tabs);
        page = new LinearLayout(a); page.setOrientation(LinearLayout.VERTICAL);
        page.setBackground(round(Color.rgb(30, 36, 48), dp(18))); page.setPadding(dp(20), dp(10), dp(20), dp(18));
        LinearLayout.LayoutParams plp = new LinearLayout.LayoutParams(-1, -2); plp.setMargins(dp(16), dp(12), dp(16), dp(10));
        outer.addView(page, plp);
        LinearLayout bottom = new LinearLayout(a); bottom.setOrientation(LinearLayout.HORIZONTAL); bottom.setPadding(dp(16), 0, dp(16), dp(20));
        bottom.addView(button("Close", Color.rgb(60, 70, 90), "close", new Runnable() { public void run() { host.closeSettings(); } }), new LinearLayout.LayoutParams(0, dp(50), 1));
        View gap = new View(a); bottom.addView(gap, new LinearLayout.LayoutParams(dp(10), 1));
        bottom.addView(button("Disconnect", Color.rgb(150, 40, 40), "disconnect", new Runnable() { public void run() { host.disconnect(); } }), new LinearLayout.LayoutParams(0, dp(50), 1));
        outer.addView(bottom);
    }

    // ---- what each page is
    static String instance(String ns, String which) { return which.equals("device") ? ns : ns + ":" + which; }

    /** fields: key, label, kind ("text", "num", "bool", "addr") */
    private static String[][] fields(String which) {
        if (which.equals("device")) return new String[][]{{"name", "This device's name in FrogNet (announced from its next session)", "text"},
                {"telem_on", "Report telemetry (pond, telem): position, environment, battery, network", "bool"},
                {"telem_gps", "Include the GPS position", "bool"},
                {"telem_period_s", "Look at what has no event (memory, storage, signal) every N seconds", "num"}};
        if (which.equals("kart")) return new String[][]{{"laps", "Laps you want", "num"}, {"auto", "Start races on the autopilot", "bool"},
                {"bots", "Robot karts of yours in each race (from the next Connect)", "num"},
                {"kart", "Kart memory, host:port (from the next Connect)", "addr"}};
        return new String[][]{{"comms_ram", "Communicator memory, host:port; empty: the kart memory's host, port 8800 (from the next Connect)", "addr"},
                {"comms_media", "Communicator media server, host:port; empty: the kart memory's host, port 8994 (from the next Connect)", "addr"}};
    }

    void show(final String which) {
        shown = which;
        page.removeAllViews();
        final TextView where = text("reading...", 13, Color.rgb(170, 178, 192)); page.addView(where);
        new Thread(new Runnable() { public void run() {
            MemberService s = MemberService.Companion.getInstance();
            final String ns = s == null ? null : s.ns();
            final String area = s == null ? "" : s.forwardArea();
            final String cap = ns == null ? null : s.tuple("cap", instance(ns, which));
            final String telem = ns == null || !which.equals("device") ? null : s.tuple("telem", ns);
            a.runOnUiThread(new Runnable() { public void run() { if (shown.equals(which)) fill(which, ns, area, cap, telem, where); } });
        } }, "settings-read").start();
    }

    private void fill(final String which, final String ns, final String area, String cap, String telem, TextView where) {
        if (ns == null) { where.setText("Not joined to FrogNet yet: the settings are tuples in your pond's memory, so they are shown once this device is a member."); return; }
        if (cap == null) { where.setText("Reading (pond, cap, " + instance(ns, which) + ") from pond " + area + " failed (the log says why)."); where.setTextColor(Color.rgb(255, 120, 120)); return; }
        where.setText("(pond, cap, " + instance(ns, which) + ")  in pond " + area);
        final Map<String, Object> bag = Json.obj(Json.parse(cap));
        final String[][] fs = fields(which);
        final List<View> inputs = new ArrayList<View>();
        for (String[] f : fs) {
            Object v = bag.get(f[0]);
            if (f[2].equals("bool")) {
                CheckBox c = new CheckBox(a); c.setText(f[1]); c.setTextColor(Color.WHITE); c.setChecked(Json.bool(v)); c.setTag("set:" + f[0]);
                c.setPadding(dp(4), dp(10), 0, 0); page.addView(c); inputs.add(c);
            } else {
                page.addView(text(f[1], 13, Color.rgb(170, 178, 192)));
                EditText e = new EditText(a); e.setSingleLine(true); e.setTextColor(Color.WHITE); e.setTextSize(17); e.setTag("set:" + f[0]);
                e.setText(v == null ? "" : f[2].equals("num") ? String.valueOf(Json.num(v)) : Json.str(v));
                e.setInputType(f[2].equals("num") ? InputType.TYPE_CLASS_NUMBER : InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_URI);
                e.setBackground(round(Color.rgb(40, 48, 64), dp(10))); e.setPadding(dp(14), dp(10), dp(14), dp(10));
                page.addView(e); inputs.add(e);
            }
        }
        final TextView said = text("", 13, Color.rgb(170, 230, 180)); said.setTag("said");
        Button save = button("Save", Color.rgb(36, 150, 72), "save", null);
        save.setOnClickListener(new View.OnClickListener() { public void onClick(View v) {
            final Map<String, Object> nb = new LinkedHashMap<String, Object>(bag);      // what the page does not show stays as it is
            for (int i = 0; i < fs.length; i++) {
                String k = fs[i][0], kind = fs[i][2]; View in = inputs.get(i);
                if (kind.equals("bool")) { nb.put(k, ((CheckBox) in).isChecked()); continue; }
                String t = ((EditText) in).getText().toString().trim();
                if (kind.equals("num")) { if (t.isEmpty()) continue; try { nb.put(k, Long.parseLong(t)); } catch (NumberFormatException e) { said.setText(k + " is a number"); said.setTextColor(Color.rgb(255, 120, 120)); return; } }
                else if (kind.equals("addr") && !t.isEmpty() && !t.matches(".+:\\d+")) { said.setText(k + " is host:port (or empty)"); said.setTextColor(Color.rgb(255, 120, 120)); return; }
                else nb.put(k, t);
            }
            nb.put("at", System.currentTimeMillis() / 1000.0); nb.put("by", "settings page on " + android.os.Build.MODEL);
            final String inst = instance(ns, which), body = Json.write(nb);
            said.setText("writing (pond, cap, " + inst + ")..."); said.setTextColor(Color.rgb(170, 178, 192));
            new Thread(new Runnable() { public void run() {
                MemberService s = MemberService.Companion.getInstance();
                final boolean ok = s != null && s.setCap(inst, body);
                Log.i(TAG, "[SETTINGS_ARE_TUPLES_V1] Save: (pond, cap, " + inst + ") = " + body + (ok ? " written" : " NOT written (the FDF log says why)"));
                a.runOnUiThread(new Runnable() { public void run() {
                    said.setText(ok ? "written: (pond, cap, " + inst + ")" : "not written (not joined, or the memory refused it: the log says why)");
                    said.setTextColor(ok ? Color.rgb(170, 230, 180) : Color.rgb(255, 120, 120));
                } });
            } }, "settings-write").start();
        } });
        LinearLayout.LayoutParams slp = new LinearLayout.LayoutParams(-1, dp(50)); slp.setMargins(0, dp(14), 0, 0);
        page.addView(save, slp); page.addView(said);
        if (telem != null) {
            page.addView(text("What this device reports: (pond, telem, " + ns + "), as every monitor reads it", 15, Color.WHITE));
            Map<String, String> flat = new TreeMap<String, String>(); flatten("", Json.parse(telem), flat);
            StringBuilder b = new StringBuilder();
            for (Map.Entry<String, String> e : flat.entrySet()) b.append(e.getKey()).append("  ").append(e.getValue()).append('\n');
            TextView tv = text(flat.isEmpty() ? "nothing reported (telemetry off, or not written yet)" : b.toString().trim(), 13, Color.rgb(200, 210, 220));
            tv.setTypeface(Typeface.MONOSPACE); tv.setTag("telem"); page.addView(tv);
        }
    }

    @SuppressWarnings("unchecked")
    private static void flatten(String p, Object v, Map<String, String> out) {
        if (v instanceof Map) { for (Map.Entry<String, Object> e : ((Map<String, Object>) v).entrySet()) flatten(p.isEmpty() ? e.getKey() : p + "." + e.getKey(), e.getValue(), out); return; }
        out.put(p, v == null ? "-" : Json.str(v));
    }

    // ---- [SETTINGS_ARE_TUPLES_V1] this app follows its tuples: a held read on (pond, cap, w:<guid>:<app>), applied as
    // it changes, whoever wrote it. None yet: written once from what this app has (its prefs), so the monitor shows it.
    interface Apply { void apply(Map<String, Object> bag); }
    static Thread follow(final String app, final SharedPreferences prefs, final String[] keys, final Apply apply, final MainActivity.Alive alive) {
        Thread t = new Thread(new Runnable() { public void run() {
            long after = -1; String ns = null;
            try {
                while (alive.alive()) {
                    MemberService s = MemberService.Companion.getInstance();
                    String now = s == null ? null : s.ns();
                    if (now == null) {                              // not joined (yet, or the session ended): the member says when
                        synchronized (MemberService.JOINED) { if ((s = MemberService.Companion.getInstance()) == null || s.ns() == null) MemberService.JOINED.wait(30000); }
                        continue;
                    }
                    if (!now.equals(ns)) { ns = now; after = -1; }
                    String inst = ns + ":" + app;
                    String[] r = s.held("cap", inst, after, after < 0 ? 0.0 : 600.0);
                    if (r == null) { synchronized (MemberService.JOINED) { MemberService.JOINED.wait(5000); } continue; }   // logged by the member; the session is usually ending
                    long id = Long.parseLong(r[0]);
                    if (after < 0 && id < 0) {
                        Map<String, Object> b = new LinkedHashMap<String, Object>();
                        for (String k : keys) { Object v = prefs.getAll().get(k); if (v instanceof Integer) v = ((Integer) v).longValue(); if (v != null) b.put(k, v); }
                        b.put("at", System.currentTimeMillis() / 1000.0); b.put("by", "this app, from what it had");
                        boolean ok = s.setCap(inst, Json.write(b));
                        Log.i(TAG, "[SETTINGS_ARE_TUPLES_V1] (pond, cap, " + inst + ") did not exist: " + (ok ? "written from this app's own: " + Json.write(b) : "writing it failed"));
                        after = 0; continue;
                    }
                    if (id <= after) continue;                       // the hold ran out: nothing new
                    after = id;
                    Map<String, Object> bag = Json.obj(Json.parse(r[1]));
                    if (bag.isEmpty()) continue;
                    Log.i(TAG, "[SETTINGS_ARE_TUPLES_V1] (pond, cap, " + inst + ") #" + id + ": " + r[1]);
                    apply.apply(bag);
                }
            } catch (InterruptedException e) { /* the app is going */ }
            Log.i(TAG, "[SETTINGS_ARE_TUPLES_V1] no longer following (pond, cap, *:" + app + ")");
        } }, "settings-" + app);
        t.setDaemon(true); t.start();
        return t;
    }

    // ---- looks
    private int dp(float v) { return (int) TypedValue.applyDimension(TypedValue.COMPLEX_UNIT_DIP, v, a.getResources().getDisplayMetrics()); }
    private static GradientDrawable round(int color, float r) { GradientDrawable d = new GradientDrawable(); d.setColor(color); d.setCornerRadius(r); return d; }
    private TextView text(String s, int size, int color) {
        TextView t = new TextView(a); t.setText(s); t.setTextSize(size); t.setTextColor(color); t.setPadding(dp(4), dp(10), dp(4), dp(4)); return t;
    }
    private Button button(String label, int color, String tag, final Runnable r) {
        Button b = new Button(a); b.setText(label); b.setTextSize(17); b.setTextColor(Color.WHITE); b.setAllCaps(false); b.setTag(tag);
        b.setBackground(round(color, dp(12)));
        if (r != null) b.setOnClickListener(new View.OnClickListener() { public void onClick(View v) { r.run(); } });
        return b;
    }
    private Button tab(String label, final String which) {
        return button(label, Color.rgb(44, 56, 80), "tab:" + which, new Runnable() { public void run() { show(which); } });
    }
}
