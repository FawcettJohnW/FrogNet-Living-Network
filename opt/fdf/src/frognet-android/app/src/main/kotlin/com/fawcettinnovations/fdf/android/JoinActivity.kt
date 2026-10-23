// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// JoinActivity.kt -- [JOIN_FROM_ANOTHER_APP_V1] another app (FrogNet Kart) starts this device's membership without
// the user opening this app: action net.frognet.action.JOIN. No screen of its own (translucent, gone at once): it
// asks Android for the VPN consent only if this app has never had it (VpnService.prepare -- the one question, once),
// starts MemberService (the same as Start), and returns RESULT_OK. Already a member: RESULT_OK, nothing else done.
// Consent refused: RESULT_CANCELED with "why". The caller watches for the VPN itself (the tunnel is up when it is).
package com.fawcettinnovations.fdf.android

import android.app.Activity
import android.content.Intent
import android.net.VpnService
import android.os.Bundle

class JoinActivity : Activity() {
    override fun onCreate(b: Bundle?) {
        super.onCreate(b); AppLog.init(this)
        val from = callingPackage ?: "an app"
        if (MemberService.instance != null) { AppLog.add("join: asked by $from; already a member"); done(RESULT_OK, "already a member"); return }
        val consent = VpnService.prepare(this)
        if (consent == null) { join(from); return }
        AppLog.add("join: asked by $from; this app has no VPN consent yet: asking Android, once")
        startActivityForResult(consent, VPN_CONSENT)
    }

    private fun join(from: String) {
        AppLog.add("join: asked by $from: starting the membership")
        startForegroundService(Intent(this, MemberService::class.java))
        done(RESULT_OK, "joining")
    }

    private fun done(code: Int, why: String) { setResult(code, Intent().putExtra("why", why)); finish(); overridePendingTransition(0, 0) }

    @Deprecated("platform API")
    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        if (requestCode != VPN_CONSENT) return
        if (resultCode == RESULT_OK) join(callingPackage ?: "an app")
        else { AppLog.add("join: VPN consent refused: no tunnel, not joining"); done(RESULT_CANCELED, "the VPN consent was refused") }
    }

    companion object { const val VPN_CONSENT = 1; const val ACTION_JOIN = "net.frognet.action.JOIN" }
}
