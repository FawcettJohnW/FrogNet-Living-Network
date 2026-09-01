/***************************************************************
 *  Copyright (C) 2016-2026 Fawcett Innovations LLC            *
 *                                                             *
 *  SPDX-License-Identifier: GPL-2.0-only                      *
 *                                                             *
 *  This program is free software; you can redistribute it     *
 *  and/or modify it under the terms of the GNU General Public *
 *  License as published by the Free Software Foundation;      *
 *  version 2 of the License, and no other version.            *
 *                                                             *
 *  This program is distributed in the hope that it will be    *
 *  useful, but WITHOUT ANY WARRANTY; without even the implied *
 *  warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR    *
 *  PURPOSE.  See the GNU General Public License for details.  *
 *                                                             *
 *  See COPYRIGHT and LICENSE at the root of this tree.        *
 **************************************************************/
package net.frognet.calendar

import org.json.JSONArray
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL

/**
 * CalendarClient — the UnREST contract for Android.
 *
 * An ordinary HTTP client pointed at a well-known name. The FrogNet node the
 * phone is attached to provides resolution + proxy + codec; this app speaks
 * plain HTTP to the codex suffix and never implements BLDC-1.
 *
 * VERIFY on box: BASE host (well-known name reachable from the phone's node) and
 * the per-device identity `who`.
 */
class CalendarClient(
    private val base: String = "http://family-calendar.frognet",
    private val suffix: String = "/unrest/family-calendar",
    private val who: String = "android-device"
) {
    /** Raised when the codex is unreachable -> UI shows "contact admin", no fork. */
    class Unreachable(msg: String) : Exception(msg)
    /** Raised when a backwards-in-time read is seen -> session terminates (§6.2). */
    class SplitDetected(msg: String) : Exception(msg)

    private var watermark: Long = -1

    data class Event(
        val uid: String, val summary: String, val start: String, val end: String,
        val allDay: Boolean, val location: String, val notes: String
    )

    private fun http(verb: String, body: JSONObject?): String {
        val url = URL(base + suffix + "/" + verb)
        val c = url.openConnection() as HttpURLConnection
        return try {
            c.connectTimeout = 5000; c.readTimeout = 5000
            if (body != null) {
                c.requestMethod = "POST"
                c.doOutput = true
                c.setRequestProperty("Content-Type", "application/json")
                c.outputStream.use { it.write(body.toString().toByteArray()) }
            } else {
                c.requestMethod = "GET"
            }
            if (c.responseCode !in 200..299) throw Unreachable("HTTP ${c.responseCode}")
            c.inputStream.bufferedReader().use { it.readText() }
        } catch (e: Unreachable) {
            throw e
        } catch (e: Exception) {
            throw Unreachable(e.message ?: "network error")
        } finally {
            c.disconnect()
        }
    }

    /** Read the shared element. Throws SplitDetected on a backwards-time read. */
    fun listEvents(): List<Event> {
        val el = JSONObject(http("list", null))
        val ts = el.optString("ts", "0").toLongOrNull() ?: 0L
        if (watermark >= 0 && ts < watermark)
            throw SplitDetected("read ts=$ts < watermark=$watermark")
        watermark = ts
        val out = ArrayList<Event>()
        val arr: JSONArray = el.optJSONArray("events") ?: JSONArray()
        for (i in 0 until arr.length()) {
            val e = arr.getJSONObject(i)
            out.add(
                Event(
                    e.getString("uid"), e.getString("summary"),
                    e.getString("start"), e.getString("end"),
                    e.optBoolean("all_day", false),
                    e.optString("location", ""), e.optString("notes", "")
                )
            )
        }
        return out
    }

    fun createEvent(summary: String, start: String, end: String,
                    location: String = "", notes: String = "") {
        http("create", JSONObject().apply {
            put("summary", summary); put("start", start); put("end", end)
            put("location", location); put("notes", notes)
        })
    }

    fun deleteEvent(uid: String) {
        http("delete", JSONObject().apply { put("uid", uid) })
    }

    /** Touch presence and return who else is viewing now. */
    fun presence(): List<String> {
        val r = JSONObject(http("presence", JSONObject().apply { put("who", who) }))
        val arr = r.optJSONArray("who_is_here") ?: JSONArray()
        return (0 until arr.length()).map { arr.getString(it) }
    }
}
