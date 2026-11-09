// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// BaseState.kt -- [BASE_APP_V1] what this device's membership is now, for the level apps that use it (they are other
// apps: nothing of this process is theirs to read): joined (the forward pond's session is up), edge (this device's
// address in it, off the mesh), invited (who it is invited or logged in as), join (the join line it keeps, which a
// team lead's card hands its members). Each app that asked (JoinActivity) is sent it again when it changes: action
// net.frognet.action.MEMBER_STATE, addressed to that app's package only.
package com.fawcettinnovations.fdf.android

import android.content.Context
import android.content.Intent

object BaseState {
    const val ACTION_STATE = "net.frognet.action.MEMBER_STATE"
    @Volatile private var last = ""

    fun subscribe(c: Context, pkg: String?) {
        if (pkg.isNullOrEmpty() || pkg == c.packageName) return
        val p = c.getSharedPreferences("fdf-base", Context.MODE_PRIVATE)
        val set = p.getStringSet("callers", emptySet())!!.toMutableSet()
        if (set.add(pkg)) p.edit().putStringSet("callers", set).apply()
    }

    fun extras(c: Context, i: Intent): Intent {
        val ms = MemberService.instance; val id = Identity(c)
        val inv = id.invite; val j = id.join
        return i.putExtra("joined", ms?.joined() == true).putExtra("edge", ms?.edge() ?: inv?.edge ?: "")
            .putExtra("invited", if (inv != null) "${inv.name} in ${inv.area}" else if (j != null) "the EOC ${j.org} (level ${j.level})" else "")
            .putExtra("join", j?.line ?: "")
    }

    /** said to every app that asked, when it is not what it was */
    fun changed(c: Context) {
        val i = extras(c, Intent(ACTION_STATE))
        val now = "${i.getBooleanExtra("joined", false)}|${i.getStringExtra("edge")}|${i.getStringExtra("invited")}|${i.getStringExtra("join")}"
        if (now == last) return
        last = now
        for (pkg in c.getSharedPreferences("fdf-base", Context.MODE_PRIVATE).getStringSet("callers", emptySet())!!)
            runCatching { c.sendBroadcast(Intent(i).setPackage(pkg)) }
    }
}
