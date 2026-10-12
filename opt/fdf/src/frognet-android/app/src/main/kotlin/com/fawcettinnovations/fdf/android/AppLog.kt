// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// AppLog.kt -- every line carries the build ([FDF_VERSION_V1]) and goes three places: logcat (tag FDF), the app's
// screen (last 400 lines), and files/fdf-member.log in app storage (kept to ~1 MB, one rotation). Nothing has to be run
// to learn what is deployed or what happened: the screen says it.
package com.fawcettinnovations.fdf.android

import android.content.Context
import android.util.Log
import com.fawcettinnovations.fdf.member.MEMBER_BUILD
import java.io.File
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

object AppLog {
    private val fmt = SimpleDateFormat("yyyy-MM-dd HH:mm:ss.SSS", Locale.US)
    private val lines = ArrayDeque<String>()
    private var file: File? = null
    fun init(ctx: Context) { if (file == null) file = File(ctx.filesDir, "fdf-member.log") }
    @Synchronized fun add(m: String) {
        val l = "${fmt.format(Date())} [$MEMBER_BUILD] $m"
        Log.i("FDF", l)
        lines.addLast(l); while (lines.size > 400) lines.removeFirst()
        file?.let { f -> try { if (f.length() > 1_000_000) f.renameTo(File(f.path + ".1")); f.appendText(l + "\n") } catch (_: Exception) {} }
    }
    @Synchronized fun text(): String = lines.joinToString("\n")
}
