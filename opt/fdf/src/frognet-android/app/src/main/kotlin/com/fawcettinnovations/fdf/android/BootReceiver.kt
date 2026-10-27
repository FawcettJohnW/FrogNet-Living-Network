// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// BootReceiver.kt -- [START_ON_BOOT_V1] the membership starts with the phone: once an invitation is kept and the VPN
// consent was given (VpnService.prepare says nothing is left to ask), boot starts MemberService as Start does. Without
// either, nothing starts and the log says which is missing (the app's screen asks for it).
package com.fawcettinnovations.fdf.android

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.net.VpnService

class BootReceiver : BroadcastReceiver() {
    override fun onReceive(ctx: Context, intent: Intent) {
        if (intent.action != Intent.ACTION_BOOT_COMPLETED && intent.action != Intent.ACTION_MY_PACKAGE_REPLACED) return
        AppLog.init(ctx)
        val id = Identity(ctx)
        when {
            id.invite == null -> AppLog.add("boot: no invitation kept: not starting (paste one in the app)")
            VpnService.prepare(ctx) != null -> AppLog.add("boot: the VPN consent is not given: not starting (Start in the app asks for it)")
            else -> { AppLog.add("boot: starting the membership (${id.invite.name} in ${id.invite.area})")
                      ctx.startForegroundService(Intent(ctx, MemberService::class.java)) }
        }
    }
}
