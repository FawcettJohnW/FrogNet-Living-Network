// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// Space.kt -- [SPACE_FAILOVER_V1][SPACES_V1] one RAM space for one application, on Windows and Android: which RAM manager
// it is on, and moving it when that changes (opt/fdf/docs/ORGANIZATIONS.md sections 4, 4a and 6). fdfspace.py's rules,
// line for line; the application only receives the change, re-arms its held reads and writes its own truth again.
//
//   val sp = Space("First Responders", OrgLogin results' spaces, fallback = listOf("127.0.0.1:8841"), onChange = { old, new -> })
//   sp.start(); sp.ram.write(...); sp.ram.read(...)
//
// [NO_FLAP_V1] (John, 2026-11-01): a standing session (a held read) to EVERY candidate, all the time. When the current
// one's closes, the space moves AT ONCE to the first candidate, in list order, whose own standing session is alive --
// nothing is asked then. A candidate coming back changes nothing while the current one lives; it counts at the next
// loss (a space on nothing, or on its fallback, takes a configured one that comes). No memory is moved.
package com.fawcettinnovations.fdf.member

import java.net.HttpURLConnection
import java.net.URL
import java.net.URLEncoder
import java.util.concurrent.LinkedBlockingQueue

/** The RAM's HTTP front (fdfram.py's wire): write, read (held with after + wait_s), version. */
class Ram(val hostport: String) {
    private fun q(m: Map<String, Any>) = m.entries.joinToString("&") { URLEncoder.encode(it.key, "UTF-8") + "=" + URLEncoder.encode(it.value.toString(), "UTF-8") }
    private fun call(method: String, query: Map<String, Any>, body: String? = null, timeoutMs: Int = 10_000): Json {
        val c = URL("http://$hostport$API?" + q(query)).openConnection() as HttpURLConnection
        c.requestMethod = method; c.connectTimeout = 4000; c.readTimeout = timeoutMs
        if (body != null) { c.doOutput = true; c.setRequestProperty("Content-Type", "application/json"); c.outputStream.use { it.write(body.toByteArray()) } }
        val t = (if (c.responseCode < 400) c.inputStream else c.errorStream).bufferedReader().readText()
        val j = Json.parse(t.ifEmpty { throw java.io.IOException("$method $hostport op=${query["op"]}: empty reply") })
        if (j["ok"].isNull.not() && !j["ok"].bool && j["error"].str.isNotEmpty()) throw ProtocolError("op=${query["op"]} refused: ${j["error"].str}")
        return j
    }
    fun version(): Json = call("POST", mapOf("op" to "rina"), "{\"op\":\"version\"}", 4000)
    fun write(service: String, variable: String, instance: String, bagJson: String): Long =
        call("POST", mapOf("op" to "write"), "{\"service\":" + Json.quote(service) + ",\"variable\":" + Json.quote(variable) +
             ",\"instance\":" + Json.quote(instance) + ",\"bag\":" + bagJson + "}")["id"].num?.toLong() ?: -1
    fun read(service: String, variable: String = "", instance: String = "", after: Long = -1, waitS: Double = 0.0): List<Json> {
        val m = LinkedHashMap<String, Any>(mapOf("op" to "read", "service" to service))
        if (variable.isNotEmpty()) m["variable"] = variable
        if (instance.isNotEmpty()) m["instance"] = instance
        if (after >= 0) m["after"] = after
        if (waitS > 0) m["wait_s"] = minOf(waitS, 30.0)
        return call("GET", m, timeoutMs = 10_000 + (waitS * 1000).toInt())["cells"].a
    }
}

class Space(val name: String, private val managers: List<SpaceAt>, private val fallback: List<String> = emptyList(),
            var onChange: ((String?, String) -> Unit)? = null, private val me: String = "", private val log: (String) -> Unit = {}) {
    companion object {
        const val RECONNECT_MS = 2000L
        /** [SPACES_V1] the space `name` from this device's logins, in the join line's order */
        fun fromLogins(name: String, logins: List<ManagerLogin>, fallback: List<String> = emptyList(), me: String = "",
                       log: (String) -> Unit = {}): Space {
            val ms = logins.sortedBy { it.order }.flatMap { l -> l.spaces.filter { it.space == name } }
            require(ms.isNotEmpty()) { "space '$name': this device's level joins it at no RAM host" }
            return Space(name, ms, fallback, null, me, log)
        }
    }
    private val cands: List<Pair<String, Ram>> = managers.map { it.manager to Ram(it.hostport) } + fallback.map { "fallback $it" to Ram(it) }
    private val alive = BooleanArray(cands.size)
    private val tried = BooleanArray(cands.size)
    @Volatile var current: Int? = null; private set
    @Volatile var ram: Ram? = null; private set
    private val events = LinkedBlockingQueue<Pair<String, Int>>()
    @Volatile private var firstDone = false

    private fun say(m: String) = log("[SPACE] $name: $m")

    fun start(): Space {
        Thread({ mergeLoop() }, "space-merge-$name").apply { isDaemon = true; start() }
        for (i in cands.indices) Thread({ standing(i) }, "space-stand-$name-$i").apply { isDaemon = true; start() }
        val t0 = System.currentTimeMillis()
        while (System.currentTimeMillis() - t0 < 3000 && !tried.all { it }) Thread.sleep(20)
        events.put("start" to -1)
        for (i in 0 until 200) { if (ram != null) return this; Thread.sleep(50) }
        say("no RAM manager answered yet; the space takes the first that does")
        return this
    }

    /** this candidate's standing session: a held read on it, for as long as it answers; asked again every RECONNECT_MS */
    private fun standing(i: Int) {
        val (n, r) = cands[i]
        while (true) {
            var last = 0L
            try {
                r.version(); alive[i] = true; tried[i] = true; events.put("up" to i)
                while (true) { r.read("fdf-space", "alive", me.ifEmpty { "-" }, last, 30.0).mapNotNull { it["id"].num?.toLong() }.maxOrNull()?.let { last = it } }
            } catch (e: Exception) {
                val was = alive[i]; alive[i] = false; tried[i] = true
                if (was) { say("$n closed: ${e.message}"); events.put("down" to i) }
            }
            Thread.sleep(RECONNECT_MS)
        }
    }

    private fun mergeLoop() {
        while (true) {
            val (ev, i) = events.take()
            if (ev == "down" && i != current) continue                       // a standby went: it counts at the next loss
            val onFallback = current != null && current!! >= managers.size
            if (ev == "up" && current != null && !(onFallback && i < managers.size)) continue   // never back up while it lives
            if (ev == "up" && current == null && !firstDone) continue        // before the first choice
            if (ev == "start") firstDone = true
            val pick = cands.indices.firstOrNull { alive[it] && !(ev == "down" && it == i) }
            if (pick == null) {
                if (current != null) { say("lost ${cands[current!!].first} and nothing else is alive; the first that comes back is taken"); current = null; ram = null }
                else say("nothing on the list answers, nor the fallback; the first that does is taken")
                continue
            }
            if (pick == current) continue
            val old = current?.let { cands[it].first }
            val (n, r) = cands[pick]
            current = pick; ram = r
            say("$ev: on $n (${r.hostport})" + (old?.let { ", was $it" } ?: ""))
            try { onChange?.invoke(old, n) } catch (e: Exception) { say("the application's change handler failed: ${e.message}") }
        }
    }
}
