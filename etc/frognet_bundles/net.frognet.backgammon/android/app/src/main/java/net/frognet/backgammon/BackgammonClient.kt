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
package net.frognet.backgammon

import org.json.JSONArray
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL

/** UnREST contract client for Family Backgammon. Plain HTTP to a well-known name; the
 *  FrogNet node the phone is attached to provides resolution + proxy + codec. */
class BackgammonClient(
    private val base: String = "http://family-backgammon.frognet",
    private val table: String = "table-1",
    private val who: String = "android"
) {
    class Unreachable(m: String) : Exception(m)
    class SplitDetected(m: String) : Exception(m)
    private val suffix = "/unrest/family-backgammon"
    private var watermark = -1L

    data class Move(val from: Int, val die: Int, val to: Int, val hit: Boolean, val bear: Boolean)
    data class State(
        val points: IntArray, val barW: Int, val barB: Int, val offW: Int, val offB: Int,
        val turn: String, val phase: String, val dice: List<Int>, val diceRemaining: List<Int>,
        val legal: List<Move>, val cubeValue: Int, val winner: String?,
        val whiteName: String, val blackName: String, val ts: Long
    )

    private fun http(verb: String, body: JSONObject?): String {
        val c = (URL("$base$suffix/$verb?g=$table").openConnection() as HttpURLConnection)
        return try {
            c.connectTimeout = 5000; c.readTimeout = 5000
            if (body != null) {
                c.requestMethod = "POST"; c.doOutput = true
                c.setRequestProperty("Content-Type", "application/json")
                c.outputStream.use { it.write(body.toString().toByteArray()) }
            } else c.requestMethod = "GET"
            if (c.responseCode !in 200..299) throw Unreachable("HTTP ${c.responseCode}")
            c.inputStream.bufferedReader().use { it.readText() }
        } catch (e: Unreachable) { throw e
        } catch (e: Exception) { throw Unreachable(e.message ?: "network") }
        finally { c.disconnect() }
    }

    private fun parse(s: String): State {
        val o = JSONObject(s)
        val ts = o.optString("ts", "0").toLongOrNull() ?: 0L
        if (watermark >= 0 && ts < watermark) throw SplitDetected("ts=$ts < $watermark")
        watermark = ts
        val pj = o.getJSONArray("points"); val pts = IntArray(25) { if (it < pj.length()) pj.getInt(it) else 0 }
        val bar = o.getJSONObject("bar"); val off = o.getJSONObject("off")
        val dice = o.getJSONArray("dice").let { a -> List(a.length()) { a.getInt(it) } }
        val rem = o.getJSONArray("dice_remaining").let { a -> List(a.length()) { a.getInt(it) } }
        val lj: JSONArray = o.optJSONArray("legal") ?: JSONArray()
        val legal = List(lj.length()) {
            val m = lj.getJSONObject(it)
            Move(m.getInt("from"), m.getInt("die"), m.getInt("to"),
                 m.optBoolean("hit"), m.optBoolean("bear"))
        }
        val players = o.getJSONObject("players")
        return State(pts, bar.getInt("w"), bar.getInt("b"), off.getInt("w"), off.getInt("b"),
            o.getString("turn"), o.getString("phase"), dice, rem, legal,
            o.getJSONObject("cube").getInt("value"),
            if (o.isNull("winner")) null else o.getString("winner"),
            players.getString("w"), players.getString("b"), ts)
    }

    fun get() = parse(http("get", null))
    fun newGame() = parse(http("new_game", JSONObject()))
    fun roll() = parse(http("roll", JSONObject()))
    fun move(from: Int, die: Int) = parse(http("move", JSONObject().put("from", from).put("die", die)))
    fun offerDouble() = parse(http("offer_double", JSONObject()))
    fun acceptDouble() = parse(http("accept_double", JSONObject()))
    fun declineDouble() = parse(http("decline_double", JSONObject()))
    fun presence(): List<String> {
        val r = JSONObject(http("presence", JSONObject().put("who", who)))
        val a = r.optJSONArray("who_is_here") ?: JSONArray()
        return (0 until a.length()).map { a.getString(it) }
    }
}
