// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// Join.kt -- [JOIN_LINE_V1] an organization's join line (fdfjoin1.<base64url JSON>, fdf-org line LEVEL / fdf-setup), and
// the login it calls for, on Windows and Android as fdf-login does it on Linux (opt/fdf/docs/ORGANIZATIONS.md section 3).
//
// Every RAM host's hub is 10.253.253.254, so on one TUN each RAM manager is an island of the bridge: its 10.253.253.254/32
// aliased 1:1 from 100.64.0.0/10, on a tunnel of its own ([RAM_ON_ITS_OWN_TUNNEL_V1] the bridge's way). In order, at each
// RAM manager on the line ([LOGIN_RESTRICTED_FIRST_V1]):
//   1 the door: an island on the generic credential (its key, its edge 10.253.253.1), ONE exchange on the door port: this
//     device's public key in, its own edge out (restricted: its level's login port and nothing else); the island goes
//   2 the device's own island: its own key, its edge, the same alias
//   3 login on the level's login port: user and password, or nothing for a level without one
//   4 the answer: the level's session port and where each of the level's spaces is on that RAM host
// Each exchange is one line of JSON each way over TCP: a RAM manager has no per-cell privacy, so a password is never a cell.
package com.fawcettinnovations.fdf.member

import java.net.InetSocketAddress
import java.net.Socket

class JoinManager(val name: String, val pubkey: String, val endpoint: String, val addr: String, val doorPort: Int,
                  val loginPort: Int, val genericKey: String, val genericEdge: String) {
    fun hub(area: String) = Hub(name, pubkey, endpoint, addr, area)
}

class JoinLine(val line: String, val org: String, val level: String, val password: Boolean, val managers: List<JoinManager>,
               val spaces: List<String>) {
    companion object {
        const val PREFIX = "fdfjoin1."
        fun parse(text: String): JoinLine {
            val line = text.trim()
            require(line.startsWith(PREFIX)) { "not a join line (it starts $PREFIX; fdf-org line LEVEL on the RAM host prints one)" }
            val tok = line.substring(PREFIX.length)
            val j = try { Json.parse(String(java.util.Base64.getUrlDecoder().decode(tok + "=".repeat((4 - tok.length % 4) % 4)), Charsets.UTF_8)) }
                    catch (e: Exception) { throw IllegalArgumentException("the join line does not decode (${e.message}): copy the whole line") }
            val g = j["generic"]
            val ms = j["managers"].a.map { m ->
                val mg = if (m["generic"].isNull) g else m["generic"]              // each RAM host has its own generic credential
                JoinManager(m["name"].str.ifEmpty { m["endpoint"].str }, m["pubkey"].str, m["endpoint"].str, m["addr"].str.ifEmpty { "10.253.253.254" },
                            m["door_port"].num?.toInt() ?: 0, m["login_port"].num?.toInt() ?: 0, mg["privkey"].str, mg["edge"].str.ifEmpty { "10.253.253.1" })
            }
            require(ms.isNotEmpty() && ms.all { it.pubkey.isNotEmpty() && it.endpoint.isNotEmpty() && it.doorPort > 0 && it.loginPort > 0 && it.genericKey.isNotEmpty() }) {
                "the join line names no complete RAM manager (pubkey, endpoint, door and login ports, generic credential)" }
            return JoinLine(line, j["org"].str, j["level"].str, j["password"].bool, ms, j["spaces"].a.map { it.str })
        }
    }
    override fun toString() = "join line: organization $org, level $level (${if (password) "user and password" else "no password"}), " +
        "RAM managers ${managers.joinToString { "${it.name} at ${it.endpoint}" }}, spaces ${spaces.joinToString().ifEmpty { "none" }}"
}

/** Where one space is on one RAM host, as this device reaches it (the RAM host's alias). */
class SpaceAt(val space: String, val manager: String, val host: String, val port: Int) { val hostport get() = "$host:$port" }

/** This device's admission at one RAM manager on the line. */
class ManagerLogin(val manager: String, val order: Int, val edge: String, val level: String, val host: String,
                   val sessionPort: Int, val spaces: List<SpaceAt>, val me: String = "") {
    val ram get() = "$host:$sessionPort"
}

class LoginRefused(msg: String) : Exception(msg)

class OrgLogin(private val bridge: Bridge, private val devicePub: String, private val device: String, private val nick: String,
               private val log: (String) -> Unit, private val kind: String = "leaf", private val tries: Int = 6,
               private val connect: (String, Int, Int) -> Socket = { h, p, t -> Socket().apply { soTimeout = t; connect(InetSocketAddress(h, p), t) } }) {

    /** the island's name for a RAM manager: its RAM is reached as this island */
    fun island(m: JoinManager) = "ram:${m.name}"

    fun loginAll(line: JoinLine, user: String = "", password: String = ""): List<ManagerLogin> {
        if (line.password && user.isEmpty()) throw LoginRefused("level ${line.level} has a password: give the user and the password")
        log(line.toString())
        // [LOGIN_WHERE_IT_CAN_V1] a RAM manager that cannot be reached, or refuses, is said and left for the next run.
        // [LOGIN_AT_ALL_AT_ONCE_V1] (John 2026-11-03: "Why are you trying to reach targets sequentially?") every RAM
        // manager at once, each on its own islands: a dead one costs its own timeout, not everyone's after it.
        val missed = java.util.Collections.synchronizedList(mutableListOf<String>())
        val pool = java.util.concurrent.Executors.newFixedThreadPool(maxOf(1, line.managers.size))
        val done = try {
            line.managers.mapIndexed { i, m -> pool.submit<ManagerLogin?> {
                try { atManager(line, m, i, user, password) }
                catch (e: Exception) { missed.add("${m.endpoint}: ${e.message}"); log("NOT logged in at ${m.name} (${m.endpoint}): ${e.message} -- left for the next run"); null }
            } }.mapNotNull { it.get() }
        } finally { pool.shutdown() }
        if (done.isEmpty()) throw LoginRefused("logged in at none of the ${line.managers.size} RAM manager(s) on the line: " + missed.joinToString("; "))
        log("logged in at ${done.size} of ${line.managers.size} RAM manager(s)" + (if (missed.isEmpty()) "" else "; not at: " + missed.joinToString("; ")))
        return done
    }

    private fun alias(area: String, m: JoinManager, scope: Map<String, String>, old: Map<String, String>, taken: Set<String>) =
        bridge.ledger.pick(area, "${m.addr}/32", old, taken, scope)

    private fun exchange(host: String, port: Int, ask: String, timeoutMs: Int = 8000): Json =
        connect(host, port, timeoutMs).use { s ->
            s.getOutputStream().write((ask + "\n").toByteArray()); s.getOutputStream().flush()
            Json.parse(s.getInputStream().bufferedReader().readLine() ?: "{}")
        }

    private fun <T> tried(what: String, f: () -> T): T {
        var last: Exception? = null
        for (t in 1..tries) {
            try { return f() } catch (e: java.io.IOException) { last = e; log("$what, try $t: ${e.message}; asking again"); Thread.sleep(1000L + 1000L * t) }
        }
        throw LoginRefused("$what: no answer after $tries tries (${last?.message})")
    }

    /** [UNREACHABLE_IN_10S_V1] (John 2026-11-03: "Change the 76s to 10") the door's one exchange: a target that does not
     *  answer it within UNREACHABLE_MS is given up, each try bounded by what is left (it was 6 tries of 8 s and their
     *  waits, 76 s, while a lower target that admitted waited for it). A target that answered the door is reachable:
     *  its login keeps the tries. */
    private fun <T> withinDeadline(what: String, f: (Int) -> T): T {
        val end = System.currentTimeMillis() + UNREACHABLE_MS; var last: Exception? = null; var t = 0
        while (true) {
            val left = (end - System.currentTimeMillis()).toInt()
            if (left < 200) throw LoginRefused("$what: no answer in ${UNREACHABLE_MS / 1000} s after $t tries (${last?.message})")
            t++
            try { return f(left) } catch (e: java.io.IOException) {
                last = e; log("$what, try $t: ${e.message}; asking again")
                Thread.sleep(minOf(1000L, maxOf(0L, end - System.currentTimeMillis())))
            }
        }
    }
    companion object { const val UNREACHABLE_MS = 10_000L }

    /** [LOGIN_IS_AN_INVITATION_V1] (John 2026-11-02: the EOC QR is the join line; after it the app finds its RAMs
     *  "wherever they found it before") the login at the first RAM host on the line that admits it, as the invitation it amounts to: the
     *  door admitted THIS device's key at its own edge, so the hub's tunnel with that key and edge is the region, exactly
     *  as an fdfinv1 line gives it. The login's own island goes (one tunnel per key to a hub: the forward tunnel takes
     *  it), and the region is joined at the level's session port inside it. Nothing is kept but the line itself: every
     *  start logs in again with the same key and is given the same edge. */
    fun admit(line: JoinLine, privkey: String, user: String = "", password: String = ""): Invite {
        if (line.password && user.isEmpty()) throw LoginRefused("level ${line.level} has a password: give the user and the password")
        log(line.toString())
        // [LOGIN_WHERE_IT_CAN_V1] (John 2026-11-02) the line is the admin's ordered list of RAM hosts: the first one on it
        // that admits this device is the invitation; one that cannot be reached, or refuses, is said and the next tried
        // [LOGIN_AT_ALL_AT_ONCE_V1] (John 2026-11-03) every RAM host on the line at once.
        // [LOGIN_IN_LINE_ORDER_V1] (John 2026-11-03: "A torn network is worse than a disconnected one") the invitation
        // is the HIGHEST on the line that admits this device, never the first to answer: every device with the same line
        // gets the same answer. A host lower on the line that answers first waits until each one above it has refused or
        // failed; the first that admits is taken as soon as nothing above it is still being asked.
        val missed = java.util.Collections.synchronizedList(mutableListOf<String>())
        val pool = java.util.concurrent.Executors.newFixedThreadPool(maxOf(1, line.managers.size)) { r -> Thread(r, "admit").apply { isDaemon = true } }
        val cs = java.util.concurrent.ExecutorCompletionService<Pair<Int, ManagerLogin?>>(pool)
        for ((i, m) in line.managers.withIndex()) cs.submit {
            try {
                val ml = atManager(line, m, i, user, password)
                if (ml.sessionPort <= 0) { missed.add("${m.endpoint}: no session port for level ${line.level}"); Pair(i, null) } else Pair(i, ml)
            } catch (e: Exception) { missed.add("${m.endpoint}: ${e.message}"); log("NOT admitted at ${m.name} (${m.endpoint}): ${e.message}"); Pair(i, null) }
            finally { bridge.removeIsland(island(m), "the login is over: the forward tunnel carries this key now") }
        }
        val done = BooleanArray(line.managers.size); val got = arrayOfNulls<ManagerLogin>(line.managers.size)
        try {
            repeat(line.managers.size) {
                val (j, r) = cs.take().get(); done[j] = true; got[j] = r
                for (i in line.managers.indices) {
                    if (!done[i]) break                                     // one above is still being asked: wait for it
                    val ml = got[i] ?: continue
                    val m = line.managers[i]
                    log("admitted at ${m.name}, number ${i + 1} of ${line.managers.size} on the line, the highest that admits" + (if (missed.isEmpty()) "" else "; not at: " + missed.joinToString("; ")))
                    return Invite(line.line, line.org, "", m.addr, ml.sessionPort, m.hub(line.org), ml.me.ifEmpty { device }, ml.edge, kind, privkey)
                }
            }
        } finally { pool.shutdown() }
        throw LoginRefused("admitted at none of the ${line.managers.size} RAM host(s) on the line: " + missed.joinToString("; "))
    }

    fun atManager(line: JoinLine, m: JoinManager, order: Int, user: String, password: String): ManagerLogin {
        val hub = m.hub(line.org); val door = "door:${m.name}"; val own = island(m)
        // 1 the door, on the generic credential, for one exchange
        val (dp, _) = bridge.decide(door) { sc, old, tk -> val a = alias(door, m, sc, old, tk)
            ReachPlan(door, m.genericEdge, hub, listOf("${m.addr}/32"), mapOf(a to "${m.addr}/32"), "ram", "allowed", key = m.genericKey) }
        val doorHost = dp.alias.keys.first().substringBefore('/')
        val ans = try {
            withinDeadline("door at ${m.endpoint}") { left -> exchange(doorHost, m.doorPort, "{\"pubkey\":" + Json.quote(devicePub) + ",\"level\":" + Json.quote(line.level) +
                ",\"device\":" + Json.quote(device) + ",\"nick\":" + Json.quote(nick) + ",\"kind\":" + Json.quote(kind) + "}", minOf(left, 8000)) }
        } finally { bridge.removeIsland(door, "the door's one exchange is over") }
        if (!ans["ok"].bool) throw LoginRefused("the door at ${m.endpoint} refused: ${ans["why"].str}")
        val edge = ans["edge"].str
        log("door at ${m.endpoint}: I am ${ans["name"].str}, edge $edge (${ans["state"].str})" +
            (if (ans["held"].str.isNotEmpty()) ", holding level ${ans["held"].str} here (one level per host)" else ""))   // [ONE_LEVEL_PER_HOST_V1]
        val loginPort = ans["login_port"].num?.toInt()?.takeIf { it > 0 } ?: m.loginPort
        // 2 this device's own island at that RAM host
        val (op, _) = bridge.decide(own) { sc, old, tk -> val a = alias(own, m, sc, old, tk)
            ReachPlan(own, edge, hub, listOf("${m.addr}/32"), mapOf(a to "${m.addr}/32"), "ram", "allowed") }
        val host = op.alias.keys.first().substringBefore('/')
        // 3 login on its own tunnel
        val cred = if (line.password) "{\"user\":" + Json.quote(user) + ",\"password\":" + Json.quote(password) + "}" else "{}"
        val out = tried("login at ${m.endpoint}") { exchange(host, loginPort, cred) }
        if (!out["ok"].bool) throw LoginRefused("login at ${m.endpoint} refused: ${out["why"].str}")
        val spaces = out["spaces"].a.mapNotNull { sp ->
            val r = sp["ram"].str; val c = r.lastIndexOf(':')
            if (c < 0) null else SpaceAt(sp["name"].str, m.name, host, r.substring(c + 1).toInt())   // its address is the RAM host's: our alias
        }
        val ml = ManagerLogin(m.name, order, edge, out["level"].str, host, out["session_port"].num?.toInt() ?: 0, spaces, ans["name"].str)
        log("logged in at ${m.endpoint}: level ${ml.level}, RAM ${ml.ram} (alias of ${m.addr}), spaces " +
            spaces.joinToString { "${it.space} ${it.hostport}" }.ifEmpty { "none" })
        return ml
    }
}
