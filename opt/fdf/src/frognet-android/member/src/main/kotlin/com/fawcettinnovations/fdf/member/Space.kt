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
// Events, one at a time on one thread (the merge): lost (the held read on the current RAM manager failed: TCP keepalive,
// about 5 s), returned (a standing attempt toward a RAM manager ABOVE the current one answered; one every 5 s to each,
// including when the space is on its fallback). Each event walks the list top to bottom; a different answer is a switch.
// No memory is moved.
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
        const val RECONNECT_MS = 5000L
        /** [SPACES_V1] the space `name` from this device's logins, in the join line's order */
        fun fromLogins(name: String, logins: List<ManagerLogin>, fallback: List<String> = emptyList(), me: String = "",
                       log: (String) -> Unit = {}): Space {
            val ms = logins.sortedBy { it.order }.flatMap { l -> l.spaces.filter { it.space == name } }
            require(ms.isNotEmpty()) { "space '$name': this device's level joins it at no RAM host" }
            return Space(name, ms, fallback, null, me, log)
        }
    }
    private val cands: List<Pair<String, Ram>> = managers.map { it.manager to Ram(it.hostport) } + fallback.map { "fallback $it" to Ram(it) }
    @Volatile var current: Int? = null; private set
    @Volatile var ram: Ram? = null; private set
    private val events = LinkedBlockingQueue<String>()
    @Volatile private var gen = 0
    private val standing = HashMap<Int, Thread>()

    private fun say(m: String) = log("[SPACE] $name: $m")
    private fun answers(r: Ram) = try { r.version(); true } catch (e: Exception) { false }
    fun walk(): Int? = cands.indices.firstOrNull { answers(cands[it].second) }

    fun start(): Space {
        Thread({ mergeLoop() }, "space-merge-$name").apply { isDaemon = true; start() }
        events.put("start")
        for (i in 0 until 200) { if (ram != null) return this; Thread.sleep(50) }
        say("no RAM manager answered yet; the space waits for one")
        return this
    }

    private fun mergeLoop() {
        while (true) {
            val ev = events.take()
            val i = walk()
            if (i == null) { say("$ev: nothing on the list answers, nor the fallback; the next event walks again"); stand(null); continue }
            if (i == current) { stand(i); continue }
            val old = current?.let { cands[it].first }
            val (n, r) = cands[i]
            current = i; ram = r; gen++
            say("$ev: on $n (${r.hostport})" + (old?.let { ", was $it" } ?: ""))
            val g = gen
            Thread({ watch(g, r) }, "space-watch-$name").apply { isDaemon = true; start() }
            stand(i)
            try { onChange?.invoke(old, n) } catch (e: Exception) { say("the application's change handler failed: ${e.message}") }
        }
    }

    /** a held read on the current RAM manager: when it fails, the space lost it */
    private fun watch(g: Int, r: Ram) {
        var last = 0L
        while (g == gen) {
            try { val rows = r.read("fdf-space", "alive", me.ifEmpty { "-" }, last, 30.0); rows.mapNotNull { it["id"].num?.toLong() }.maxOrNull()?.let { last = it } }
            catch (e: Exception) { if (g == gen) { say("lost ${r.hostport}: ${e.message}"); events.put("lost") }; return }
        }
    }

    /** a standing attempt toward every candidate above the current one (all of the list when on nothing) */
    @Synchronized private fun stand(cur: Int?) {
        val top = if (cur == null) managers.size else minOf(cur, managers.size)
        for (i in 0 until top) {
            val t = standing[i]
            if (t == null || !t.isAlive) standing[i] = Thread({
                val (n, r) = cands[i]
                while (current == null || i < current!!) {
                    if (answers(r)) { say("$n answers again"); events.put("returned"); return@Thread }
                    Thread.sleep(RECONNECT_MS)
                }
            }, "space-stand-$name-$i").apply { isDaemon = true; start() }
        }
    }
}
