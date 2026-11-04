// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// Space.kt -- [SPACE_FAILOVER_V1][SPACES_V1][SPACE_BY_CAPABILITY_V1] one RAM space for one application, on Windows and
// Android: which RAM host it is on, and moving it when that changes. fdfspace.py's rules, line for line; the
// application only receives the change, re-arms its held reads and writes its own truth again.
//
//   val sp = Space.fromLogins("First Responders", OrgLogin results' logins); sp.onChange = { old, new -> }
//   sp.start(); sp.ram?.write(...); sp.ram?.read(...)
//
// [SPACE_BY_CAPABILITY_V1] (John 2026-11-03: "There is no fallback list. Fallback is determined by capability";
// "Designation and failover/failup are automatic"; "A torn network is worse than a disconnected one"). It replaces
// [NO_FLAP_V1] (2026-11-01: list order, "never back up while it lives", a fallback RAM). A standing session to every
// RAM host the space is on: a held read on its capability tuple, (fdf-space, can, AREA), which its fdf-org writes. On
// every event the space is on the best alive host by capability (Select, the guid last); a better host coming back
// takes it again; none alive: on nothing (ram null). No memory is moved.
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

class Space(val name: String, private val managers: List<SpaceAt>, var onChange: ((String?, String) -> Unit)? = null,
            private val me: String = "", private val log: (String) -> Unit = {}, private val spec: SelectSpec = SelectSpec.DEFAULT) {
    companion object {
        /** [EVENTS_NOT_TIMERS_V1] a failure that comes at once (refused, unreachable, no route): that host is asked again
         *  on this space's next event, not on a timer. Any other failure: asked again at once (its connect waits in the
         *  transport, or in the backlog of the socket its RAM host keeps [LISTENING_SOCKET_OUTLIVES_THE_HOST_V1]). */
        // (an HTTP refusal -- ESTALE among them: the host answered -- is not one: that host is asked again at once)
        val AT_ONCE = listOf("connection refused", "unreachable", "no route", "network is down", "cannot assign", "no such device")
        /** [SPACES_V1] the space `name` from this device's logins */
        fun fromLogins(name: String, logins: List<ManagerLogin>, me: String = "", log: (String) -> Unit = {},
                       spec: SelectSpec = SelectSpec.DEFAULT): Space {
            val ms = logins.sortedBy { it.order }.flatMap { l -> l.spaces.filter { it.space == name } }
            require(ms.isNotEmpty()) { "space '$name': this device's level joins it at no RAM host" }
            return Space(name, ms, null, me, log, spec)
        }
    }
    private val cands: List<Pair<String, Ram>> = managers.map { it.manager to Ram(it.hostport) }
    private val alive = BooleanArray(cands.size)
    private val tried = BooleanArray(cands.size)
    private val cap = Array(cands.size) { Json.parse("{}") }
    @Volatile var current: Int? = null; private set
    @Volatile var ram: Ram? = null; private set
    private val events = LinkedBlockingQueue<Pair<String, Int>>()
    private val allTried = java.util.concurrent.CountDownLatch(1)              // every host answered or failed
    private val chosen = java.util.concurrent.CountDownLatch(1)                // the first choice (a host, or nothing)
    private val kick = Array(cands.size) { java.util.concurrent.Semaphore(0) }  // this space's next event, for a refused host

    private fun say(m: String) = log("[SPACE] $name: $m")

    fun start(): Space {
        Thread({ mergeLoop() }, "space-merge-$name").apply { isDaemon = true; start() }
        for (i in cands.indices) Thread({ standing(i) }, "space-stand-$name-$i").apply { isDaemon = true; start() }
        allTried.await(3, java.util.concurrent.TimeUnit.SECONDS)   // the first choice once each host has answered or failed
        events.put("start" to -1)
        chosen.await()                                              // the merge loop's first decision
        return this
    }

    /** this host's standing session: a held read on its capability tuple, for as long as it answers */
    private fun standing(i: Int) {
        val (n, r) = cands[i]
        while (true) {
            try {
                var rows = r.read("fdf-space", "can")
                cap[i] = rows.lastOrNull()?.get("bag") ?: Json.parse("{}")
                var last = rows.mapNotNull { it["id"].num?.toLong() }.maxOrNull() ?: 0L   // (0, not -1: a read past 0 is held)
                alive[i] = true; triedOne(i); events.put("up" to i)
                while (true) {
                    rows = r.read("fdf-space", "can", "", last, 30.0)
                    if (rows.isNotEmpty()) {
                        last = rows.mapNotNull { it["id"].num?.toLong() }.maxOrNull() ?: last
                        cap[i] = rows.last()["bag"]; events.put("capability" to i)
                    }
                }
            } catch (e: Exception) {
                val was = alive[i]; alive[i] = false; triedOne(i)
                if (was) { say("$n closed: ${e.message}"); events.put("down" to i) }
                val m = (e.message ?: e.toString()).lowercase()
                if (AT_ONCE.any { it in m }) { kick[i].drainPermits(); kick[i].acquire() }   // this space's next event
            }
        }
    }

    private fun triedOne(i: Int) { tried[i] = true; if (tried.all { it }) allTried.countDown() }

    private fun aliveCands() = cands.indices.filter { alive[it] }.map { j -> Triple(cap[j]["guid"].str.ifEmpty { cands[j].first }, cap[j], j) }

    private fun mergeLoop() {
        var started = false
        while (true) {
            val (ev, _) = events.take()
            for (k in kick) k.release()                                  // a host refused at once asks again on this event
            if (ev == "start") started = true
            if (!started) continue
            val ac = aliveCands()
            val g = Select.select(spec, name, ac.map { it.first to it.second })
            val pick = ac.firstOrNull { it.first == g }?.third
            if (pick == null) {
                if (current != null) { say("lost ${cands[current!!].first} and no other RAM host is alive: on nothing"); current = null; ram = null }
                else if (ev == "start") say("no RAM host answers: on nothing")
                if (ev == "start") chosen.countDown()
                continue
            }
            if (pick == current) continue
            val old = current?.let { cands[it].first }
            val (n, r) = cands[pick]
            current = pick; ram = r; chosen.countDown()
            say("$ev: on $n (${r.hostport}), the best alive by capability (${Select.explain(spec, name, ac.map { it.first to it.second })})" + (old?.let { ", was $it" } ?: ""))
            try { onChange?.invoke(old, n) } catch (e: Exception) { say("the application's change handler failed: ${e.message}") }
        }
    }
}
