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
                   val sessionPort: Int, val spaces: List<SpaceAt>) {
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
        return line.managers.mapIndexed { i, m -> atManager(line, m, i, user, password) }
    }

    private fun alias(area: String, m: JoinManager, scope: Map<String, String>, old: Map<String, String>, taken: Set<String>) =
        bridge.ledger.pick(area, "${m.addr}/32", old, taken, scope)

    private fun exchange(host: String, port: Int, ask: String): Json =
        connect(host, port, 8000).use { s ->
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

    fun atManager(line: JoinLine, m: JoinManager, order: Int, user: String, password: String): ManagerLogin {
        val hub = m.hub(line.org); val door = "door:${m.name}"; val own = island(m)
        // 1 the door, on the generic credential, for one exchange
        val (dp, _) = bridge.decide(door) { sc, old, tk -> val a = alias(door, m, sc, old, tk)
            ReachPlan(door, m.genericEdge, hub, listOf("${m.addr}/32"), mapOf(a to "${m.addr}/32"), "ram", "allowed", key = m.genericKey) }
        val doorHost = dp.alias.keys.first().substringBefore('/')
        val ans = try {
            tried("door at ${m.endpoint}") { exchange(doorHost, m.doorPort, "{\"pubkey\":" + Json.quote(devicePub) + ",\"level\":" + Json.quote(line.level) +
                ",\"device\":" + Json.quote(device) + ",\"nick\":" + Json.quote(nick) + ",\"kind\":" + Json.quote(kind) + "}") }
        } finally { bridge.removeIsland(door, "the door's one exchange is over") }
        if (!ans["ok"].bool) throw LoginRefused("the door at ${m.endpoint} refused: ${ans["why"].str}")
        val edge = ans["edge"].str
        log("door at ${m.endpoint}: I am ${ans["name"].str}, edge $edge (${ans["state"].str})")
        // 2 this device's own island at that RAM host
        val (op, _) = bridge.decide(own) { sc, old, tk -> val a = alias(own, m, sc, old, tk)
            ReachPlan(own, edge, hub, listOf("${m.addr}/32"), mapOf(a to "${m.addr}/32"), "ram", "allowed") }
        val host = op.alias.keys.first().substringBefore('/')
        // 3 login on its own tunnel
        val cred = if (line.password) "{\"user\":" + Json.quote(user) + ",\"password\":" + Json.quote(password) + "}" else "{}"
        val out = tried("login at ${m.endpoint}") { exchange(host, m.loginPort, cred) }
        if (!out["ok"].bool) throw LoginRefused("login at ${m.endpoint} refused: ${out["why"].str}")
        val spaces = out["spaces"].a.mapNotNull { sp ->
            val r = sp["ram"].str; val c = r.lastIndexOf(':')
            if (c < 0) null else SpaceAt(sp["name"].str, m.name, host, r.substring(c + 1).toInt())   // its address is the RAM host's: our alias
        }
        val ml = ManagerLogin(m.name, order, edge, out["level"].str, host, out["session_port"].num?.toInt() ?: 0, spaces)
        log("logged in at ${m.endpoint}: level ${ml.level}, RAM ${ml.ram} (alias of ${m.addr}), spaces " +
            spaces.joinToString { "${it.space} ${it.hostport}" }.ifEmpty { "none" })
        return ml
    }
}
