// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// JoinActivity.kt -- [JOIN_FROM_ANOTHER_APP_V1][BASE_APP_V1] another app starts, changes or ends this device's membership
// without the user opening this one: this app is the device's hidden base (the Disaster Area base, John 2026-11-03:
// "the base disaster area app is hidden from the user. They don't need to bring it up and make login a 2-step
// process" ... "it's shared between all of the various apps and ram regions a given machine needs"). One membership
// per device, whichever level apps (RangerTrak Team, Public, the kart ...) use it.
//   action net.frognet.action.JOIN, extras (all optional): "line" (an fdfjoin1 join line or an fdfinv1 invitation: kept
//     as this device's), "user" and "password" (the level's login, the one the level app asked for: the single login),
//     "name" (the member's name). No screen of its own (translucent, gone at once): it asks Android for the VPN consent
//     only if this app has never had it (the one question, once), starts MemberService, and returns RESULT_OK. A member
//     given a different line or login attaches again with it (the door upgrades an open level to the one logged in:
//     [ONE_LEVEL_PER_HOST_V1]). Consent refused, or nothing to join with: RESULT_CANCELED with "why".
//   action net.frognet.action.LEAVE: the membership ends (RESULT_OK).
// Every answer carries the state (BaseState: joined, edge, invited, join); the caller is sent it again whenever it
// changes (action net.frognet.action.MEMBER_STATE, to its package only).
package com.fawcettinnovations.fdf.android

import android.app.Activity
import android.content.Intent
import android.net.VpnService
import android.os.Bundle
import com.fawcettinnovations.fdf.member.JoinLine

class JoinActivity : Activity() {
    override fun onCreate(b: Bundle?) {
        super.onCreate(b); AppLog.init(this)
        val from = callingPackage ?: "an app"
        BaseState.subscribe(this, callingPackage)
        if (intent?.action == ACTION_LEAVE) {
            AppLog.add("[BASE_APP_V1] leave: asked by $from")
            if (MemberService.instance != null) startService(Intent(this, MemberService::class.java).setAction(MemberService.ACTION_STOP))
            done(RESULT_OK, "left"); return
        }
        val ident = Identity(this)
        val line = intent?.getStringExtra("line")?.trim().orEmpty()
        val user = intent?.getStringExtra("user").orEmpty(); val pw = intent?.getStringExtra("password").orEmpty()
        intent?.getStringExtra("name")?.takeIf { it.isNotBlank() && it != ident.name }?.let { ident.name = it }
        var changed = false
        if (line.isNotEmpty()) try {
            if (line.startsWith(JoinLine.PREFIX)) {
                changed = ident.join?.line != line || ident.joinUser != user || ident.joinPassword != pw || ident.invite != null
                if (changed) ident.keepJoin(line, user, pw)
            } else {
                changed = ident.invite?.line != line
                if (changed) ident.keepInvite(line)
            }
        } catch (e: IllegalArgumentException) { done(RESULT_CANCELED, "not a join line or an invitation: ${e.message}"); return }
        val now = Identity(this)
        if (now.invite == null && now.join == null) { done(RESULT_CANCELED, "no FrogNet invitation or join line yet: the app gives one (the EOC's QR code, or an invitation)"); return }
        if (MemberService.instance != null && !changed) { AppLog.add("join: asked by $from; already a member"); done(RESULT_OK, "already a member"); return }
        val consent = VpnService.prepare(this)
        if (consent == null) { join(from, changed); return }
        AppLog.add("join: asked by $from; this app has no VPN consent yet: asking Android, once")
        pendingRestart = changed
        startActivityForResult(consent, VPN_CONSENT)
    }
    private var pendingRestart = false

    private fun join(from: String, restart: Boolean) {
        if (restart && MemberService.instance != null) {
            AppLog.add("[BASE_APP_V1] join: asked by $from with another line or login: the membership attaches again with it")
            stopService(Intent(this, MemberService::class.java))       // the next one reads the identity as it is now
        } else AppLog.add("join: asked by $from: starting the membership")
        startForegroundService(Intent(this, MemberService::class.java))
        done(RESULT_OK, "joining")
    }

    private fun done(code: Int, why: String) {
        setResult(code, BaseState.extras(this, Intent().putExtra("why", why))); finish(); overridePendingTransition(0, 0)
    }

    @Deprecated("platform API")
    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        if (requestCode != VPN_CONSENT) return
        if (resultCode == RESULT_OK) join(callingPackage ?: "an app", pendingRestart)
        else { AppLog.add("join: VPN consent refused: no tunnel, not joining"); done(RESULT_CANCELED, "the VPN consent was refused") }
    }

    companion object {
        const val VPN_CONSENT = 1
        const val ACTION_JOIN = "net.frognet.action.JOIN"
        const val ACTION_LEAVE = "net.frognet.action.LEAVE"
    }
}
