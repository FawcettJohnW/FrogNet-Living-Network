// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
package net.frognet.kart;

import android.app.Activity;
import android.Manifest;
import android.app.AlertDialog;
import android.content.DialogInterface;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.graphics.RectF;
import android.graphics.Color;
import android.os.Build;
import android.os.Bundle;
import android.text.InputType;
import android.util.Log;
import android.util.TypedValue;
import android.view.Gravity;
import android.view.TextureView;
import android.view.View;
import android.view.WindowManager;
import android.widget.Button;
import android.widget.CheckBox;
import android.widget.EditText;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;

import net.frognet.communicator.GameCalls;
import net.frognet.kart.core.Game;
import net.frognet.kart.core.Region;

/**
 * FrogNet Kart on Android: a player of the kart service's games. [KART_ANDROID_V1]
 * The setup screen asks for a name, where the kart memory is (the droplet's 165.232.153.144:8799), robots and the
 * autopilot; then the game view. A race memory is on a game-serving machine's region address (10.x): the phone reaches it
 * through the FrogNet app's tunnel, so that app must be running and joined. The phone never serves a race.
 * Everything the game writes goes with its sessions: Leave (or closing the app) takes it out of the lobby and the race.
 * [KART_CALLS_IN_THE_GAME_V1] The game holds three memories: the kart memory (the lobby, the other players), each race's
 * memory while it runs, and the Communicator's (comms-ram) from Join to Leave. Desktop games launch the Communicator as a
 * second program; here GameCalls runs the Communicator's own code inside the game, so a call can be made from the lobby
 * and answered at any time, racing too, and a race ending leaves the call as it is.
 */
public final class MainActivity extends Activity implements GameView.Host {
    static final String TAG = "FrogNetKart";
    private Game game;
    private GameView view;
    private GameCalls calls;
    private FrameLayout stage;
    private TextureView remoteTV, selfTV;
    private FrameLayout selfBox;
    private SharedPreferences prefs;

    @Override protected void onCreate(Bundle b) {
        super.onCreate(b);
        prefs = getSharedPreferences("kart", MODE_PRIVATE);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        showSetup();
    }

    private int dp(float v) { return (int) TypedValue.applyDimension(TypedValue.COMPLEX_UNIT_DIP, v, getResources().getDisplayMetrics()); }

    private static android.graphics.drawable.GradientDrawable round(int color, float r) {
        android.graphics.drawable.GradientDrawable d = new android.graphics.drawable.GradientDrawable(); d.setColor(color); d.setCornerRadius(r); return d;
    }
    private EditText field(LinearLayout col, String label, String value, int type) {
        TextView t = new TextView(this); t.setText(label); t.setTextColor(Color.rgb(170, 178, 192)); t.setTextSize(13);
        t.setPadding(dp(4), dp(12), 0, dp(4)); col.addView(t);
        EditText e = new EditText(this); e.setText(value); e.setInputType(type); e.setTextColor(Color.WHITE); e.setTextSize(17);
        e.setSingleLine(true); e.setBackground(round(Color.rgb(40, 48, 64), dp(10))); e.setPadding(dp(14), dp(10), dp(14), dp(10));
        col.addView(e);
        return e;
    }

    private void showSetup() {
        getWindow().getDecorView().setSystemUiVisibility(0);
        ScrollView sv = new ScrollView(this);
        sv.setBackgroundColor(Color.rgb(16, 20, 28));
        LinearLayout outer = new LinearLayout(this); outer.setOrientation(LinearLayout.VERTICAL);
        sv.addView(outer);
        LinearLayout head = new LinearLayout(this); head.setOrientation(LinearLayout.VERTICAL);
        head.setBackground(new android.graphics.drawable.GradientDrawable(android.graphics.drawable.GradientDrawable.Orientation.LEFT_RIGHT,
                new int[]{Color.rgb(40, 120, 70), Color.rgb(30, 70, 140)}));
        head.setPadding(dp(28), dp(18), dp(28), dp(18));
        TextView title = new TextView(this); title.setText("FrogNet"); title.setTextSize(34); title.setTextColor(Color.WHITE);
        title.setTypeface(android.graphics.Typeface.DEFAULT_BOLD); head.addView(title);
        TextView sub = new TextView(this);
        sub.setText("Kart racing and the Communicator, on shared memory  ·  " + Game.BUILD);
        sub.setTextColor(Color.rgb(220, 235, 225)); sub.setTextSize(14); head.addView(sub);
        outer.addView(head);
        LinearLayout col = new LinearLayout(this);
        col.setOrientation(LinearLayout.VERTICAL);
        col.setBackground(round(Color.rgb(30, 36, 48), dp(18)));
        col.setPadding(dp(22), dp(10), dp(22), dp(20));
        LinearLayout.LayoutParams clp = new LinearLayout.LayoutParams(-1, -2); clp.setMargins(dp(20), dp(16), dp(20), dp(20));
        outer.addView(col, clp);
        TextView how = new TextView(this);
        how.setText("Connect to the kart memory (the players), then choose the Communicator or the game. Races run on a game-serving machine on the FrogNet region: the FrogNet app must be running and joined so this phone reaches it.");
        how.setTextColor(Color.rgb(170, 178, 192)); how.setTextSize(13); how.setPadding(dp(4), dp(8), dp(4), 0);
        col.addView(how);
        final EditText name = field(col, "Your name", prefs.getString("name", "phone"), InputType.TYPE_CLASS_TEXT);
        final EditText mem = field(col, "Kart memory (host:port)", prefs.getString("kart", "165.232.153.144:8799"), InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_URI);
        final EditText bots = field(col, "Robot karts of yours in each race", String.valueOf(prefs.getInt("bots", 0)), InputType.TYPE_CLASS_NUMBER);
        final EditText laps = field(col, "Laps you want", String.valueOf(prefs.getInt("laps", 3)), InputType.TYPE_CLASS_NUMBER);
        final EditText cram = field(col, "Communicator memory (host:port; empty: the kart memory's host, port 8800)", prefs.getString("comms_ram", ""), InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_URI);
        final EditText cmed = field(col, "Communicator media server (host:port; empty: the kart memory's host, port 8994)", prefs.getString("comms_media", ""), InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_URI);
        final CheckBox auto = new CheckBox(this); auto.setText("Start on the autopilot (toggle it in the race)"); auto.setTextColor(Color.WHITE);
        auto.setChecked(prefs.getBoolean("auto", false)); auto.setPadding(dp(4), dp(10), 0, 0); col.addView(auto);
        final TextView err = new TextView(this); err.setTextColor(Color.rgb(255, 120, 120)); col.addView(err);
        Button go = new Button(this); go.setText("Connect"); go.setTextSize(20); go.setTextColor(Color.WHITE); go.setAllCaps(false);
        go.setBackground(round(Color.rgb(36, 150, 72), dp(14)));
        LinearLayout.LayoutParams glp = new LinearLayout.LayoutParams(-1, dp(56)); glp.setMargins(0, dp(14), 0, 0);
        col.addView(go, glp);
        Button ex = new Button(this); ex.setText("Exit"); ex.setTextSize(18); ex.setTextColor(Color.WHITE); ex.setAllCaps(false);
        ex.setBackground(round(Color.rgb(150, 40, 40), dp(14)));
        LinearLayout.LayoutParams elp = new LinearLayout.LayoutParams(-1, dp(48)); elp.setMargins(0, dp(10), 0, 0);
        col.addView(ex, elp);
        ex.setOnClickListener(new View.OnClickListener() { public void onClick(View v) { exitApp(); } });   // [APP_EXIT_V1]
        go.setOnClickListener(new View.OnClickListener() {
            public void onClick(View v) {
                String n = name.getText().toString().trim(), hp = mem.getText().toString().trim();
                int c = hp.lastIndexOf(':');
                if (n.isEmpty()) { err.setText("give yourself a name"); return; }
                if (c <= 0) { err.setText("the kart memory is host:port, e.g. 165.232.153.144:8799"); return; }
                int port, nb, nl;
                try { port = Integer.parseInt(hp.substring(c + 1)); nb = Integer.parseInt("0" + bots.getText().toString().trim()); nl = Integer.parseInt("0" + laps.getText().toString().trim()); }
                catch (NumberFormatException e) { err.setText("port, robots and laps are numbers"); return; }
                String cr = cram.getText().toString().trim(), cm = cmed.getText().toString().trim();
                if ((!cr.isEmpty() && !cr.matches(".+:\\d+")) || (!cm.isEmpty() && !cm.matches(".+:\\d+"))) { err.setText("the Communicator's addresses are host:port (or empty)"); return; }
                prefs.edit().putString("name", n).putString("kart", hp).putInt("bots", nb).putInt("laps", Math.max(1, nl)).putBoolean("auto", auto.isChecked())
                        .putString("comms_ram", cr).putString("comms_media", cm).apply();
                String kh = hp.substring(0, c);
                startGame(n, kh, port, nb, Math.max(1, nl), auto.isChecked(), cr.isEmpty() ? kh + ":8800" : cr, cm.isEmpty() ? kh + ":8994" : cm);
            }
        });
        setContentView(sv);
    }

    private void startGame(String name, String host, int port, int bots, int laps, boolean auto, String commsRam, String commsMedia) {
        game = new Game(name, Build.MODEL, host, port, bots, auto, laps, new Region.Log() { public void say(String l) { Log.i(TAG, l); } });
        final Game g = game;
        askedPermissions = checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED
                || checkSelfPermission(Manifest.permission.CAMERA) != PackageManager.PERMISSION_GRANTED
                || (checkSelfPermission(Manifest.permission.ACCESS_FINE_LOCATION) != PackageManager.PERMISSION_GRANTED && !getSharedPreferences("kart", MODE_PRIVATE).getBoolean("asked_location", false));
        getSharedPreferences("kart", MODE_PRIVATE).edit().putBoolean("asked_location", true).apply();   // asked once; the person's no stands
        if (askedPermissions)
            requestPermissions(Build.VERSION.SDK_INT >= 33 ? new String[]{Manifest.permission.RECORD_AUDIO, Manifest.permission.CAMERA, "android.permission.POST_NOTIFICATIONS",
                        Manifest.permission.ACCESS_FINE_LOCATION, Manifest.permission.ACCESS_COARSE_LOCATION}
                    : new String[]{Manifest.permission.RECORD_AUDIO, Manifest.permission.CAMERA, Manifest.permission.ACCESS_FINE_LOCATION, Manifest.permission.ACCESS_COARSE_LOCATION}, 1);   // now, not mid-race; location: [TELEMETRY_IS_A_TUPLE_V1]
        stage = new FrameLayout(this);
        remoteTV = new TextureView(this); selfTV = new TextureView(this);
        selfBox = new FrameLayout(this); selfBox.setPadding(3, 3, 3, 3); selfBox.setBackgroundColor(0xCCFFFFFF);
        selfBox.addView(selfTV, new FrameLayout.LayoutParams(-1, -1));
        calls = new GameCalls(this, name, commsRam, commsMedia, remoteTV, selfTV, new GameCalls.Log() { public void say(String l) { Log.i(TAG, l); g.note(l); } });
        view = new GameView(this, game, calls, this);
        stage.addView(view, new FrameLayout.LayoutParams(-1, -1));
        stage.addView(remoteTV, new FrameLayout.LayoutParams(1, 1)); remoteTV.setVisibility(View.GONE);
        stage.addView(selfBox, new FrameLayout.LayoutParams(1, 1)); selfBox.setVisibility(View.GONE);
        setContentView(stage);
        if (!askedPermissions) startMember("connected");      // else after the permission answer: one system question at a time
        getWindow().getDecorView().setSystemUiVisibility(View.SYSTEM_UI_FLAG_FULLSCREEN | View.SYSTEM_UI_FLAG_HIDE_NAVIGATION
                | View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY | View.SYSTEM_UI_FLAG_LAYOUT_STABLE);
    }

    private void stopGame() {
        final GameView v = view; final Game g = game; final GameCalls k = calls;
        stopMember();
        view = null; game = null; calls = null;
        if (v != null) v.end();
        if (k != null) k.stop();
        if (g != null) new Thread(new Runnable() { public void run() { g.stop(); } }, "kart-stop").start();
    }

    @Override public void chatDialog(final boolean toTheCall) {
        runOnUiThread(new Runnable() { public void run() {
            final Game g = game; final GameCalls k = calls; if (g == null) return;
            final EditText e = new EditText(MainActivity.this); e.setSingleLine(true); e.setHint(toTheCall ? "say something to the call" : "say something to the lobby");
            e.setGravity(Gravity.START);
            new AlertDialog.Builder(MainActivity.this).setTitle(toTheCall ? "Chat in the call" : "Chat").setView(e)
                    .setPositiveButton("Send", new DialogInterface.OnClickListener() { public void onClick(DialogInterface d, int w) {
                        final String t = e.getText().toString();
                        if (toTheCall) { if (k != null) k.sendChat(t); return; }                    // the Communicator's own thread
                        new Thread(new Runnable() { public void run() { g.chat(t); } }, "kart-chat").start();     // no network on the UI thread
                    } })
                    .setNegativeButton("Cancel", null).show();
        } });
    }

    @Override public boolean vpnUp() {
        android.net.ConnectivityManager cm = (android.net.ConnectivityManager) getSystemService(CONNECTIVITY_SERVICE);
        for (android.net.Network n : cm.getAllNetworks()) {
            android.net.NetworkCapabilities nc = cm.getNetworkCapabilities(n);
            if (nc != null && nc.hasTransport(android.net.NetworkCapabilities.TRANSPORT_VPN)) return true;
        }
        return false;
    }

    @Override public void placeVideo(final RectF r, final RectF sf) {
        runOnUiThread(new Runnable() { public void run() {
            if (remoteTV == null) return;
            place(remoteTV, r); place(selfBox, sf);
        } });
    }
    private static void place(View v, RectF r) {
        if (r == null) { v.setVisibility(View.GONE); return; }
        FrameLayout.LayoutParams lp = new FrameLayout.LayoutParams(Math.round(r.width()), Math.round(r.height()));
        lp.leftMargin = Math.round(r.left); lp.topMargin = Math.round(r.top);
        v.setLayoutParams(lp); v.setVisibility(View.VISIBLE);
    }

    @Override public void exitApp() {
        runOnUiThread(new Runnable() { public void run() {
            Log.i(TAG, "[APP_EXIT_V1] Exit: the race, the Communicator and the FrogNet membership end; the app closes");
            stopGame(); finishAndRemoveTask();
        } });
    }

    @Override public void leave() {
        runOnUiThread(new Runnable() { public void run() { stopGame(); showSetup(); } });
    }

    @Override public void onBackPressed() { if (game != null) leave(); else super.onBackPressed(); }

    @Override protected void onDestroy() { stopGame(); super.onDestroy(); }

    // ---- [KART_IS_A_MEMBER_V1] this app is the FrogNet member itself (frognet-android's MemberService, BridgeVpn and
    // fdf-tun, compiled in): Connect starts the membership, and with it the tunnel that reaches the 10.x race memories.
    // Android asks its VPN question once, the first time; after that nothing is asked. The member's own log is logcat
    // tag FDF (and files/fdf-member.log); what the game did about it is here, tag FrogNetKart, [KART_IS_A_MEMBER_V1].
    static final int VPN_CONSENT = 7;
    static final String MEMBER = "com.fawcettinnovations.fdf.android.MemberService", MEMBER_STOP = "com.fawcettinnovations.fdf.android.STOP";
    /** what the game knows of the tunnel, for the banner: null when it is up */
    public volatile String tunnel = null;
    @Override public String tunnel() { return vpnUp() ? null : tunnel; }
    private boolean memberStarted = false, askedPermissions = false;
    @Override public void onRequestPermissionsResult(int code, String[] perms, int[] res) {
        if (code == 1 && askedPermissions) { askedPermissions = false; startMember("connected"); }
    }

    private void startMember(String why) { startMember(why, false); }
    /** consented: Android just said OK to the VPN question (asked again, prepare could ask again: it is not asked twice) */
    private void startMember(String why, boolean consented) {
        if (game == null || memberStarted) return;
        // the member's name in the region: the player's (set once; the FrogNet member keeps its own afterwards)
        SharedPreferences id = getSharedPreferences("fdf-identity", MODE_PRIVATE);
        if (!id.contains("name")) id.edit().putString("name", game.name).apply();
        Intent consent = consented ? null : android.net.VpnService.prepare(this);
        if (consent != null) {
            tunnel = "waiting for Android's VPN permission (asked once)";
            note("[KART_IS_A_MEMBER_V1] " + why + ": this app has no VPN permission yet: asking Android, once");
            try { startActivityForResult(consent, VPN_CONSENT); } catch (android.content.ActivityNotFoundException e) { tunnel = "Android gave no VPN permission screen: " + e.getMessage(); note("[KART_IS_A_MEMBER_V1] " + tunnel); }
            return;
        }
        memberStarted = true;
        tunnel = "joining FrogNet (the membership is starting; the tunnel comes up when it is placed)";
        note("[KART_IS_A_MEMBER_V1] " + why + ": starting the FrogNet membership (" + MEMBER + ")");
        try { startForegroundService(new Intent().setClassName(this, MEMBER)); }
        catch (RuntimeException e) { memberStarted = false; tunnel = "the FrogNet membership did not start: " + e; note("[KART_IS_A_MEMBER_V1] " + tunnel); }
    }
    private void stopMember() {
        if (!memberStarted) return;
        memberStarted = false;
        note("[KART_IS_A_MEMBER_V1] stopping the FrogNet membership");
        try { startService(new Intent(MEMBER_STOP).setClassName(this, MEMBER)); } catch (RuntimeException e) { note("[KART_IS_A_MEMBER_V1] stop: " + e); }
    }
    private void note(String l) { Log.i(TAG, l); Game g = game; if (g != null) g.note(l); }

    @Override protected void onActivityResult(int code, int result, Intent data) {
        if (code != VPN_CONSENT) return;
        if (result == RESULT_OK) startMember("VPN permission given", true);
        else { tunnel = "the VPN permission was refused: no tunnel, so no 10.x race (Settings, then Connect, asks again)"; note("[KART_IS_A_MEMBER_V1] " + tunnel); }
    }
}
