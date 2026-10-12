// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// MainActivity.kt -- name; the ponds [PONDS_V1] (each: area, RAM host:port; * = forward; Forward / Remove; Add); Start /
// Stop; the log, live. No address anywhere: the RAM manager gives it. Start asks Android for the VPN consent once
// (VpnService.prepare), then starts MemberService. No layout resources: one screen, built here.
package com.fawcettinnovations.fdf.android

import android.Manifest
import android.app.Activity
import android.content.ClipData
import android.content.ClipboardManager
import android.content.Intent
import android.net.Uri
import android.net.VpnService
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.os.PowerManager
import android.provider.Settings
import android.text.InputType
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import android.widget.Toast
import com.fawcettinnovations.fdf.member.MEMBER_BUILD
import net.frognet.communicator.CommunicatorActivity

class MainActivity : Activity() {
    private lateinit var ident: Identity
    private lateinit var logView: TextView
    private val ui = Handler(Looper.getMainLooper())
    // [LOG_YOU_CAN_GRAB_V1] the screen log is redrawn only when it changed and never while text is selected (the 1 s
    // redraw used to wipe any selection); "Copy log" puts the whole log on the clipboard (the emulator shares it with
    // the host's clipboard), the same lines as logcat tag FDF and files/fdf-member.log
    private val refresh = object : Runnable { override fun run() {
        val t = AppLog.text(); if (!logView.hasSelection() && logView.text.toString() != t) logView.text = t
        ui.postDelayed(this, 1000) } }

    private fun field(root: LinearLayout, label: String, value: String): EditText {
        root.addView(TextView(this).apply { text = label })
        return EditText(this).apply { setText(value); inputType = InputType.TYPE_CLASS_TEXT; isSingleLine = true }.also { root.addView(it) }
    }

    override fun onCreate(b: Bundle?) {
        super.onCreate(b); AppLog.init(this); ident = Identity(this)
        val root = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; setPadding(32, 32, 32, 32) }
        root.addView(TextView(this).apply { text = "FrogNet member  $MEMBER_BUILD\nguid ${ident.guid}\nwg pubkey ${ident.keys.publicKey.toBase64()}"; setTextIsSelectable(true) })
        val name = field(root, "Name", ident.name)
        val pondsView = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        root.addView(TextView(this).apply { text = "Ponds (* = forward)" }); root.addView(pondsView)
        fun svc(action: String, area: String) = startService(Intent(this, MemberService::class.java).setAction(action).putExtra("area", area))
        fun drawPonds() {
            pondsView.removeAllViews()
            for (p in ident.ponds) pondsView.addView(LinearLayout(this).apply {
                orientation = LinearLayout.HORIZONTAL
                addView(TextView(this@MainActivity).apply { text = (if (p.area == ident.forward) "* " else "  ") + p.area + "  " + p.ram })
                addView(Button(this@MainActivity).apply { text = "Forward"; setOnClickListener { svc(MemberService.ACTION_FORWARD, p.area); ui.postDelayed({ drawPonds() }, 300) } })
                addView(Button(this@MainActivity).apply { text = "Remove"; setOnClickListener {
                    if (p.area == ident.forward) AppLog.add("${p.area} is the forward pond: bring another forward first")
                    else { ident.ponds = ident.ponds.filter { it.area != p.area }; AppLog.add("pond ${p.area} removed (its session ends at the next restart of the service)"); drawPonds() } } })
            })
        }
        drawPonds()
        val newArea = field(root, "Add a pond: area", ""); val newRam = field(root, "its RAM manager (host:port)", "")
        root.addView(Button(this).apply { text = "Add pond"; setOnClickListener {
            val a = newArea.text.toString().trim(); val r = newRam.text.toString().trim()
            if (a.isEmpty() || !r.contains(':') || ident.ponds.any { it.area == a }) { AppLog.add("add pond: need a new area and host:port"); return@setOnClickListener }
            ident.ponds = ident.ponds + Pond(a, r); newArea.setText(""); newRam.setText(""); drawPonds()
            start() } })
        val row = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL }
        row.addView(Button(this).apply { text = "Start"; setOnClickListener {
            ident.name = name.text.toString().trim()
            val consent = VpnService.prepare(this@MainActivity)
            if (consent != null) startActivityForResult(consent, VPN_CONSENT) else start() } })
        row.addView(Button(this).apply { text = "Stop"; setOnClickListener {
            startService(Intent(this@MainActivity, MemberService::class.java).setAction(MemberService.ACTION_STOP)) } })
        // [COMMS_FROM_THE_POND_V1] the Communicator: the one the forward pond's members run, its addresses from the pond
        row.addView(Button(this).apply { text = "Communicator"; setOnClickListener {
            Thread({
                val c = MemberService.instance?.communicator()
                if (MemberService.instance == null) AppLog.add("communicator: Start first (not a member of any pond)")
                if (c != null) ui.post { startActivity(Intent(this@MainActivity, CommunicatorActivity::class.java)
                    .putExtra(CommunicatorActivity.X_POND, c[0]).putExtra(CommunicatorActivity.X_FROM, c[1])
                    .putExtra(CommunicatorActivity.X_RAM, c[2]).putExtra(CommunicatorActivity.X_MEDIA, c[3])
                    .putExtra(CommunicatorActivity.X_NAME, c[4])) }
            }, "fdf-communicator").start() } })
        row.addView(Button(this).apply { text = "Copy log"; setOnClickListener {
            val t = AppLog.text()
            getSystemService(ClipboardManager::class.java).setPrimaryClip(ClipData.newPlainText("fdf-member log", t))
            Toast.makeText(this@MainActivity, "log copied (${t.lines().size} lines)", Toast.LENGTH_SHORT).show() } })
        // Share log: the log through Android's share sheet (Quick Share to the PC, email, Drive) -- no clipboard needed
        row.addView(Button(this).apply { text = "Share log"; setOnClickListener {
            val t = AppLog.text()
            startActivity(Intent.createChooser(Intent(Intent.ACTION_SEND).setType("text/plain")
                .putExtra(Intent.EXTRA_SUBJECT, "fdf-member log").putExtra(Intent.EXTRA_TEXT, t), "Share the fdf-member log")) } })
        root.addView(row)
        logView = TextView(this).apply { textSize = 11f; setTextIsSelectable(true) }
        root.addView(ScrollView(this).apply { addView(logView) }, LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, 0, 1f))
        setContentView(root)
        if (Build.VERSION.SDK_INT >= 33) requestPermissions(arrayOf(Manifest.permission.POST_NOTIFICATIONS), 2)
        // a held session needs the network while the screen is off: ask once to be left out of battery optimization
        val pm = getSystemService(PowerManager::class.java)
        if (!pm.isIgnoringBatteryOptimizations(packageName))
            startActivity(Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS, Uri.parse("package:$packageName")))
    }

    private fun start() { startForegroundService(Intent(this, MemberService::class.java)) }

    @Deprecated("platform API")
    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        if (requestCode == VPN_CONSENT) { if (resultCode == RESULT_OK) start() else AppLog.add("VPN consent refused: no tunnel, not joining") }
    }
    override fun onResume() { super.onResume(); ui.post(refresh) }
    override fun onPause() { ui.removeCallbacks(refresh); super.onPause() }

    companion object { const val VPN_CONSENT = 1 }
}
