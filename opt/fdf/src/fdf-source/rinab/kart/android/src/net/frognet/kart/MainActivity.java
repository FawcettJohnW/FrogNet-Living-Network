// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
package net.frognet.kart;

import android.app.Activity;
import android.Manifest;
import android.app.AlertDialog;
import android.content.DialogInterface;
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

    private EditText field(LinearLayout col, String label, String value, int type) {
        TextView t = new TextView(this); t.setText(label); t.setTextColor(Color.rgb(200, 220, 255)); t.setTextSize(16);
        t.setPadding(0, dp(10), 0, 0); col.addView(t);
        EditText e = new EditText(this); e.setText(value); e.setInputType(type); e.setTextColor(Color.WHITE); e.setTextSize(18);
        e.setSingleLine(true); col.addView(e);
        return e;
    }

    private void showSetup() {
        getWindow().getDecorView().setSystemUiVisibility(0);
        ScrollView sv = new ScrollView(this);
        sv.setBackgroundColor(Color.rgb(30, 34, 40));
        LinearLayout col = new LinearLayout(this);
        col.setOrientation(LinearLayout.VERTICAL);
        col.setPadding(dp(24), dp(20), dp(24), dp(20));
        sv.addView(col);
        TextView title = new TextView(this); title.setText("FrogNet Kart"); title.setTextSize(28); title.setTextColor(Color.WHITE);
        col.addView(title);
        TextView sub = new TextView(this);
        sub.setText(Game.BUILD + "\nThe lobby, the chat, the race and the records are all shared memory: this game writes its own truths and reads everyone else's.\nRaces run on a game-serving machine on the FrogNet region; the FrogNet app must be running and joined so this phone can reach it.");
        sub.setTextColor(Color.rgb(170, 175, 185)); sub.setTextSize(14); sub.setPadding(0, dp(6), 0, dp(6));
        col.addView(sub);
        final EditText name = field(col, "Your name", prefs.getString("name", "phone"), InputType.TYPE_CLASS_TEXT);
        final EditText mem = field(col, "Kart memory (host:port)", prefs.getString("kart", "165.232.153.144:8799"), InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_URI);
        final EditText bots = field(col, "Robot karts of yours in each race", String.valueOf(prefs.getInt("bots", 0)), InputType.TYPE_CLASS_NUMBER);
        final EditText laps = field(col, "Laps you want", String.valueOf(prefs.getInt("laps", 3)), InputType.TYPE_CLASS_NUMBER);
        final EditText cram = field(col, "Communicator memory (host:port; empty: the kart memory's host, port 8800)", prefs.getString("comms_ram", ""), InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_URI);
        final EditText cmed = field(col, "Communicator media server (host:port; empty: the kart memory's host, port 8994)", prefs.getString("comms_media", ""), InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_URI);
        final CheckBox auto = new CheckBox(this); auto.setText("Start on the autopilot (toggle it in the race)"); auto.setTextColor(Color.WHITE);
        auto.setChecked(prefs.getBoolean("auto", false)); col.addView(auto);
        final TextView err = new TextView(this); err.setTextColor(Color.rgb(255, 120, 120)); col.addView(err);
        Button go = new Button(this); go.setText("Join the lobby"); go.setTextSize(20);
        col.addView(go);
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
        if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED
                || checkSelfPermission(Manifest.permission.CAMERA) != PackageManager.PERMISSION_GRANTED)
            requestPermissions(new String[]{Manifest.permission.RECORD_AUDIO, Manifest.permission.CAMERA}, 1);   // now, not mid-race
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
        getWindow().getDecorView().setSystemUiVisibility(View.SYSTEM_UI_FLAG_FULLSCREEN | View.SYSTEM_UI_FLAG_HIDE_NAVIGATION
                | View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY | View.SYSTEM_UI_FLAG_LAYOUT_STABLE);
    }

    private void stopGame() {
        final GameView v = view; final Game g = game; final GameCalls k = calls;
        view = null; game = null; calls = null;
        if (v != null) v.end();
        if (k != null) k.stop();
        if (g != null) new Thread(new Runnable() { public void run() { g.stop(); } }, "kart-stop").start();
    }

    @Override public void chatDialog() {
        runOnUiThread(new Runnable() { public void run() {
            final Game g = game; if (g == null) return;
            final EditText e = new EditText(MainActivity.this); e.setSingleLine(true); e.setHint("say something to the lobby");
            e.setGravity(Gravity.START);
            new AlertDialog.Builder(MainActivity.this).setTitle("Chat").setView(e)
                    .setPositiveButton("Send", new DialogInterface.OnClickListener() { public void onClick(DialogInterface d, int w) {
                        final String t = e.getText().toString();
                        new Thread(new Runnable() { public void run() { g.chat(t); } }, "kart-chat").start();     // no network on the UI thread
                    } })
                    .setNegativeButton("Cancel", null).show();
        } });
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

    @Override public void leave() {
        runOnUiThread(new Runnable() { public void run() { stopGame(); showSetup(); } });
    }

    @Override public void onBackPressed() { if (game != null) leave(); else super.onBackPressed(); }

    @Override protected void onDestroy() { stopGame(); super.onDestroy(); }
}
