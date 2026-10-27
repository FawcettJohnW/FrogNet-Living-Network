// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// Main.kt -- fdf-member-kt: the Kotlin member on a plain JVM, driven exactly like fdf-client (same environment, same
// facts file, re-announced when the file changes), so the netns tests that prove fdf-client prove this code too --
// the same code the Android app runs. Exits 1 when the session ends, as fdf-client does.
//
// env: RINA_RAM=host:port  FDF_FACTS=path (default /opt/fdf/var/facts.json)  FDF_NAME  RINA_AREA  RINA_SITE_KEY
//      FDF_ASSERT_SEC (default 30)
package com.fawcettinnovations.fdf.cli

import com.fawcettinnovations.fdf.member.AliasLedger
import com.fawcettinnovations.fdf.member.Ambiguous
import com.fawcettinnovations.fdf.member.Bridge
import com.fawcettinnovations.fdf.member.BridgeForwardDriver
import com.fawcettinnovations.fdf.member.BridgeHost
import com.fawcettinnovations.fdf.member.BridgeLink
import com.fawcettinnovations.fdf.member.BridgeNet
import com.fawcettinnovations.fdf.member.ProcessBridgeLink
import com.fawcettinnovations.fdf.member.Pfx
import com.fawcettinnovations.fdf.member.ProtocolError
import com.fawcettinnovations.fdf.member.Reach
import com.fawcettinnovations.fdf.member.ReachPending
import com.fawcettinnovations.fdf.member.ReachPlan
import com.fawcettinnovations.fdf.member.ReachPlanner
import com.fawcettinnovations.fdf.member.ReachRefused
import com.fawcettinnovations.fdf.member.Hub
import com.fawcettinnovations.fdf.member.Invite
import com.fawcettinnovations.fdf.member.InviteTunnel
import com.fawcettinnovations.fdf.member.Hubs
import com.fawcettinnovations.fdf.member.Leaf
import com.fawcettinnovations.fdf.member.Placement
import com.fawcettinnovations.fdf.member.TunnelDriver
import com.fawcettinnovations.fdf.member.Json
import com.fawcettinnovations.fdf.member.MEMBER_BUILD
import com.fawcettinnovations.fdf.member.Member
import com.fawcettinnovations.fdf.member.MemberConfig
import com.fawcettinnovations.fdf.member.SocketTuner
import java.io.File
import java.net.Inet4Address
import java.net.NetworkInterface
import java.net.StandardSocketOptions
import java.nio.file.FileSystems
import java.nio.file.StandardWatchEventKinds
import java.text.SimpleDateFormat
import java.util.Date
import kotlin.system.exitProcess

private val fmt = SimpleDateFormat("yyyy-MM-dd HH:mm:ss.SSS")
private fun log(m: String) { println("${fmt.format(Date())} fdf-member-kt: $m"); System.out.flush() }

// [KEEPALIVE_5S_V1] on the JVM: jdk.net.ExtendedSocketOptions (Linux, JDK 11+); idle 2 + 3 x 1 s = 5 s. The JVM has no
// TCP_USER_TIMEOUT option; the server's 5 s user timeout and the probes cover the idle session this member holds.
val jvmTuner = SocketTuner { s ->
    s.tcpNoDelay = true
    s.setOption(StandardSocketOptions.SO_KEEPALIVE, true)
    s.setOption(jdk.net.ExtendedSocketOptions.TCP_KEEPIDLE, 2)
    s.setOption(jdk.net.ExtendedSocketOptions.TCP_KEEPINTERVAL, 1)
    s.setOption(jdk.net.ExtendedSocketOptions.TCP_KEEPCOUNT, 3)
    s.sendBufferSize = 32 * 1024                                  // THE BUFFER IS THE LATENCY (frogram.cpp)
}

fun ipv4s(): List<String> = NetworkInterface.getNetworkInterfaces().toList()
    .filter { it.isUp && !it.isLoopback }.flatMap { it.inetAddresses.toList() }.filterIsInstance<Inet4Address>().map { it.hostAddress }

fun capability(): String {
    val memKb = File("/proc/meminfo").takeIf { it.exists() }?.readLines()?.firstOrNull { it.startsWith("MemTotal:") }
        ?.split(Regex("\\s+"))?.getOrNull(1) ?: "0"
    return "{\"cores\":${Runtime.getRuntime().availableProcessors()},\"mem_total_kb\":$memKb,\"os\":" + Json.quote(if (WINDOWS) "windows" else "linux") + ",\"client\":\"fdf-member-kt\"}"
}

// probe <host:port> <site> <n>: n rounds of `world` + `participants` + `version` over one Kotlin session; says how many
// answers came over the data socket (marker + segments) and each op's round trip, so the FNWP subset is shown whole.
fun probe(where: String, site: String, n: Int) {
    val c = where.lastIndexOf(':')
    val s = com.fawcettinnovations.fdf.member.Session(where.substring(0, c), where.substring(c + 1).toInt(), jvmTuner, ::log)
    val api = com.fawcettinnovations.fdf.member.API + "?op=rina"
    for (op in listOf("{\"op\":\"version\"}", "{\"op\":\"participants\"}", "{\"op\":\"world\",\"site\":" + Json.quote(site) + "}")) {
        val ms = ArrayList<Double>(); var bytes = 0
        repeat(n) { val t = System.nanoTime(); val a = s.call("POST", api, op); ms.add((System.nanoTime() - t) / 1e6); bytes = a.body.length
            if (!Json.parse(a.body)["ok"].bool) throw IllegalStateException("not ok: ${a.body.take(200)}") }
        ms.sort()
        log("probe %-44s n=%d bytes=%d p50=%.2f ms p95=%.2f ms max=%.2f ms".format(op.take(44), n, bytes, ms[n / 2], ms[(n * 95) / 100], ms.last()))
    }
    log("probe: ${s.dataReplies} answer(s) arrived on the data socket")
    val a = s.call("POST", api, "{\"op\":\"participants\"}").body
    println("PARTICIPANTS_BODY " + a)
    s.close()
}

// FDF_LEAF_WG=<wg iface>: the leaf's tunnel, driven by `wg` (the app drives GoBackend); the peer comes from the region
class WgDriver(private val iface: String) : TunnelDriver {
    @Volatile override var current: Hub? = null; private set
    private fun wg(vararg a: String): String { val p = ProcessBuilder(listOf("wg") + a).redirectErrorStream(true).start()
        val out = p.inputStream.bufferedReader().readText().trim(); return "rc=${p.waitFor()} $out" }
    private fun ip(vararg a: String): String { val p = ProcessBuilder(listOf("ip") + a).redirectErrorStream(true).start()
        val out = p.inputStream.bufferedReader().readText().trim(); return "rc=${p.waitFor()} $out" }
    override fun up(hub: Hub, addr: String) {
        current?.let { if (it.pubkey != hub.pubkey) wg("set", iface, "peer", it.pubkey, "remove") }
        ip("addr", "flush", "dev", iface); val ra = ip("addr", "add", addr, "dev", iface)   // the /32 the region gave
        ip("route", "replace", Hubs.LEAF_ALLOWED, "dev", iface)        // the flush took the interface's routes with it (GoBackend sets its own)
        log("tunnel: $iface address $addr (from the region) -> $ra")
        val r = wg("set", iface, "peer", hub.pubkey, "endpoint", hub.endpoint, "allowed-ips", Hubs.LEAF_ALLOWED, "persistent-keepalive", Hubs.PERSISTENT_KEEPALIVE.toString())
        current = hub; log("tunnel: $iface peer ${hub.name} ${hub.pubkey.take(12)} endpoint ${hub.endpoint} allowed ${Hubs.LEAF_ALLOWED} -> $r")
    }
    override fun down(why: String) { val h = current ?: return; val r = wg("set", iface, "peer", h.pubkey, "remove"); current = null; log("tunnel: $iface peer ${h.name} removed ($why) -> $r") }
}

// ponds: [PONDS_V1] the phone's two-pond logic on a JVM (MemberService's, call for call): FDF_PONDS="a=host:port,b=host:port",
// FDF_FORWARD_FILE (its one word = the forward pond, watched), FDF_FACTS (name, guid, pubkey), FDF_LEAF_WG (the one tunnel).
fun ponds() {
    val ff = File(System.getenv("FDF_FORWARD_FILE")); fun forward() = if (ff.exists()) ff.readText().trim() else ""
    val f = Json.parse(File(System.getenv("FDF_FACTS")).readText()); val name = f["name"].str; val guid = f["guid"].str; val pub = f["pubkey"].str
    val driver: TunnelDriver = WgDriver(System.getenv("FDF_LEAF_WG"))
    val members = java.util.concurrent.ConcurrentHashMap<String, Member>(); val leaves = java.util.concurrent.ConcurrentHashMap<String, Leaf>()
    val ponds = System.getenv("FDF_PONDS").split(",").map { it.substringBefore('=') to it.substringAfter('=') }
    for ((area, ram) in ponds) Thread({
        val c = ram.lastIndexOf(':')
        while (true) {
            val leaf = Leaf(area, ::ipv4s, { pub }, driver, { log("[$area] $it") }, isForward = { forward() == area }); leaves[area] = leaf
            val m = Member(MemberConfig(ram.substring(0, c), ram.substring(c + 1).toInt(), name, guid, ::ipv4s, ::capability,
                { if (forward() == area) leafFacts(leaf.placement, name, guid, pub)
                  else "{\"role\":\"participant\",\"name\":" + Json.quote(name) + ",\"guid\":" + Json.quote(guid) + "}" },
                area, "", 30, jvmTuner, { log("[$area] $it") },
                prepare = { mm -> if (forward() == area) leaf.place(mm, mm.world()) }, onJoined = { mm -> leaf.tunnel(mm, mm.world()) },
                onRegionChange = { mm -> leaf.onRegionChange(mm) },
                onAttention = { _, cell -> if (forward() != area) log("ATTENTION $area: ${cell["bag"]["why"].str} (from ${cell["bag"]["from"].str})") }))
            members[area] = m; m.runOnce(); members.remove(area); Thread.sleep(5000)
        }
    }, "pond-$area").apply { isDaemon = true; start() }
    val w = FileSystems.getDefault().newWatchService(); ff.absoluteFile.parentFile.toPath().register(w, StandardWatchEventKinds.ENTRY_CREATE, StandardWatchEventKinds.ENTRY_MODIFY)
    var cur = forward()
    while (true) {
        val k = w.take(); val mine = k.pollEvents().any { it.context().toString() == ff.name }; k.reset(); if (!mine) continue
        val now = forward(); if (now == cur || now.isEmpty()) continue
        val t0 = System.currentTimeMillis(); val old = cur; cur = now
        members[old]?.let { m -> m.reannounce(); leaves[old]?.onRegionChange(m) }
        members[now]?.let { m -> leaves[now]?.becameForward(m) }
        log("forward: $old -> $now in ${System.currentTimeMillis() - t0} ms")
    }
}

// bridge: [BRIDGE_V1] the phone's bridge on a JVM (BridgeVpnService's, call for call): every pond in FDF_PONDS at once --
// the forward one (FDF_FORWARD_FILE) as a leaf through fdf-tun's forward device, every other one as a participant that
// REACHES it (facts reach:true + pubkey) through its own island device, aliased 1:1 from 100.64.0.0/10.
// env: FDF_PONDS, FDF_FORWARD_FILE, FDF_FACTS (name, guid, pubkey), FDF_KEY_FILE (the private key, base64),
//      FDF_TUN_BIN (fdf-tun), FDF_TUN_NAME (the TUN it creates; addressed and routed here as VpnService.Builder does),
//      FDF_CTL_PORT (127.0.0.1: "resolve w:<guid>:<port>" / "status" -> one answer line)
val WINDOWS = System.getProperty("os.name").startsWith("Windows")

/** [MEMBER_IDENTITY_V1] the key and the facts, made once when they do not exist (nobody types a key): the private key
 *  is 32 random bytes clamped as `wg genkey` makes them, base64; the public key is X25519(private, 9) -- the JDK's
 *  X25519 agreement with the base point, which is exactly `wg pubkey`. facts.json: name (this computer's), guid (made
 *  once), pubkey (always the key's). Returns (facts, private key). */
fun ensureIdentity(factsFile: File, keyFile: File): Pair<Json, String> {
    if (!keyFile.exists()) {
        val k = ByteArray(32).also { java.security.SecureRandom().nextBytes(it) }
        k[0] = (k[0].toInt() and 248).toByte(); k[31] = ((k[31].toInt() and 127) or 64).toByte()
        keyFile.absoluteFile.parentFile?.mkdirs(); keyFile.writeText(java.util.Base64.getEncoder().encodeToString(k) + "\n")
        log("identity: a new WireGuard key in $keyFile")
    }
    val priv = keyFile.readText().trim(); val pub = publicKey(priv)
    val old = if (factsFile.exists()) Json.parse(factsFile.readText()) else null
    val name = old?.get("name")?.str?.ifEmpty { null } ?: (System.getenv("COMPUTERNAME") ?: java.net.InetAddress.getLocalHost().hostName)
    val guid = old?.get("guid")?.str?.ifEmpty { null } ?: ((if (WINDOWS) "windows-" else "linux-") + java.util.UUID.randomUUID())
    if (old == null || old["pubkey"].str != pub || old["name"].str != name || old["guid"].str != guid) {
        factsFile.absoluteFile.parentFile?.mkdirs()
        factsFile.writeText("{\"name\":" + Json.quote(name) + ",\"guid\":" + Json.quote(guid) + ",\"pubkey\":" + Json.quote(pub) + "}\n")
        log("identity: $factsFile says name $name, guid $guid, pubkey $pub")
    }
    return Json.parse(factsFile.readText()) to priv
}

fun publicKey(privB64: String): String {
    val k = java.util.Base64.getDecoder().decode(privB64); require(k.size == 32) { "not a WireGuard private key" }
    val kf = java.security.KeyFactory.getInstance("XDH")
    val sk = kf.generatePrivate(java.security.spec.XECPrivateKeySpec(java.security.spec.NamedParameterSpec.X25519, k))
    val base = kf.generatePublic(java.security.spec.XECPublicKeySpec(java.security.spec.NamedParameterSpec.X25519, java.math.BigInteger.valueOf(9)))
    val ka = javax.crypto.KeyAgreement.getInstance("XDH"); ka.init(sk); ka.doPhase(base, true)
    return java.util.Base64.getEncoder().encodeToString(ka.generateSecret())
}

/** [BRIDGE_WINDOWS_V1] the TUN's address and routes, as this OS sets them: `ip` on Linux, `netsh` on Windows (active
 *  only: nothing persists; the Wintun adapter goes with fdf-tun). Each command and its result is logged. */
class TunNet(private val tun: String) {
    private fun run(cmd: List<String>): String { val p = ProcessBuilder(cmd).redirectErrorStream(true).start()
        val out = p.inputStream.bufferedReader().readText().trim().replace(Regex("\\s+"), " "); return "rc=${p.waitFor()}" + (if (out.isEmpty()) "" else " $out") }
    private fun nsh(vararg a: String) = run(listOf("netsh", "interface", "ipv4") + a)
    fun address(a: String): String = if (WINDOWS) nsh("set", "address", "name=$tun", "source=static", "address=$a", "mask=255.255.255.255", "gateway=none")
        else run(listOf("ip", "addr", "add", "$a/32", "dev", tun)) + ", up " + run(listOf("ip", "link", "set", tun, "up"))
    fun route(prefix: String): String = if (WINDOWS) { nsh("delete", "route", "prefix=$prefix", "interface=$tun", "store=active")
            nsh("add", "route", "prefix=$prefix", "interface=$tun", "metric=5", "store=active") }
        else run(listOf("ip", "route", "replace", prefix, "dev", tun))
    fun unroute(prefix: String): String = if (WINDOWS) nsh("delete", "route", "prefix=$prefix", "interface=$tun", "store=active")
        else run(listOf("ip", "route", "del", prefix, "dev", tun))
}

/** [DEVICE_ROW_V1] the forward pond's leaf facts, as the app's Identity.facts makes them: on the mesh, role/name/guid and
 *  where it is (lan: ip, subnet, served_by) -- no pubkey, no addr: the member's /24 routes it, and the dashboard and the
 *  monitor list it from these; off the mesh, its WireGuard key (the region adds the /32). Never empty: a member with no
 *  facts is a member nobody can see (found 2026-10-11: the Windows bridge on Seattle5's LAN did not show up). */
fun leafFacts(pl: Placement, name: String, guid: String, pub: String): String {
    val head = "{\"role\":\"leaf\",\"name\":" + Json.quote(name) + ",\"guid\":" + Json.quote(guid)
    return when (pl) {
        is Placement.OnMesh -> head + ",\"lan\":{\"ip\":" + Json.quote(pl.ip) + ",\"subnet\":" + Json.quote(pl.subnet) + ",\"served_by\":" + Json.quote(pl.servedBy) + "}}"
        Placement.OffMesh -> head + ",\"pubkey\":" + Json.quote(pub) + "}"
    }
}

// [LEAF_BY_INVITE_V1] the invitation kept for this machine (FDF_INVITE), given once on the command line: refused before
// anything is written if it does not decode
fun keptInvite(given: String?): Invite? {
    val file = File(System.getenv("FDF_INVITE")?.takeIf { it.isNotEmpty() } ?: return given?.let { Invite.parse(it) })
    if (given != null) { val inv = Invite.parse(given); writeSecret(file, inv.line); log("invitation kept in $file") }
    return if (file.exists()) Invite.parse(file.readText()) else null
}

fun bridgeMode(given: String? = null) {
    // [LEAF_BY_INVITE_V1] with an invitation, this machine's key, name and forward pond are the invitation's, and that
    // pond's tunnel comes up from it before its session (the region is inside it). Without one: as before.
    val inv = keptInvite(given)
    val ff = File(System.getenv("FDF_FORWARD_FILE")); fun forward() = if (ff.exists()) ff.readText().trim() else inv?.area ?: ""
    val (f, key) = if (inv != null) Json.parse("{\"name\":" + Json.quote(inv.name) + ",\"guid\":\"\",\"pubkey\":" + Json.quote(publicKey(inv.privkey)) + "}") to inv.privkey
                   else ensureIdentity(File(System.getenv("FDF_FACTS")), File(System.getenv("FDF_KEY_FILE")))
    val name = f["name"].str; val guid = f["guid"].str; val pub = f["pubkey"].str
    inv?.let { log(it.toString()) }
    if (given != null && inv != null) { ff.absoluteFile.parentFile?.mkdirs(); ff.writeText(inv.area + "\n") }   // a new invitation: its region is forward
    val tun = System.getenv("FDF_TUN_NAME") ?: "fdft0"; val net = TunNet(tun)
    // the JVM's host: one fdf-tun and its TUN for as long as there is anything to carry; 10/8 routed while the forward
    // tunnel is up (a TUN made here can be re-routed in place; Android's cannot, and makes a new one)
    val host = object : BridgeHost {
        var l: ProcessBridgeLink? = null; var routed: Boolean? = null
        override fun link(forwardRoutes: Boolean): BridgeLink {
            val cur = l ?: ProcessBridgeLink(listOf(System.getenv("FDF_TUN_BIN"), "--tun", tun)) { log(it) }.also { n ->
                val h = n.hello()                   // fdf-tun's first line: its build and TUN, or why it has none
                if (!h["ok"].bool || h["tun"].str.isEmpty()) { n.close(); throw ProtocolError("fdf-tun could not open its TUN: ${h["error"].str.ifEmpty { h.toString() }}") }
                l = n; routed = null
                log("bridge: build $MEMBER_BUILD; fdf-tun ${h["build"].str} on TUN ${h["tun"].str}")
                log("bridge: $tun ${BridgeNet.LOCAL}/32 -> " + net.address(BridgeNet.LOCAL) +
                    BridgeNet.VPN_ROUTES.joinToString("") { ", route $it -> " + net.route(it) }) }
            if (routed != forwardRoutes) { routed = forwardRoutes
                log("bridge: route ${Hubs.LEAF_ALLOWED} " + (if (forwardRoutes) "-> " + net.route(Hubs.LEAF_ALLOWED) else "removed -> " + net.unroute(Hubs.LEAF_ALLOWED))) }
            return cur
        }
        override fun stop() { l?.close(); l = null; routed = null }
    }
    val members = java.util.concurrent.ConcurrentHashMap<String, Member>()
    val bridge = Bridge(host, key, { log(it) }, ledger = AliasLedger(file = System.getenv("FDF_LEDGER")?.let { File(it) }),
                        isForward = { it == forward() }, onLost = { members.values.forEach { it.leave() } })
    val leaves = java.util.concurrent.ConcurrentHashMap<String, Leaf>()
    val fwdDriver = BridgeForwardDriver(bridge)            // ONE forward tunnel, whichever pond is forward (as the app's)
    val reaches = java.util.concurrent.ConcurrentHashMap<String, Reach>(); val refused = java.util.concurrent.ConcurrentHashMap<String, String>()
    // FDF_PONDS="a=host:port,..." or FDF_PONDS_FILE (one area=host:port per line, # comments): every pond this machine is in
    val listed = (System.getenv("FDF_PONDS")?.split(",") ?: System.getenv("FDF_PONDS_FILE")?.let { pf -> File(pf).takeIf { it.exists() || inv == null }?.readLines() }
                  ?: if (inv != null) emptyList() else error("set FDF_PONDS or FDF_PONDS_FILE"))
        .map { it.substringBefore('#').trim() }.filter { '=' in it && ':' in it && !it.startsWith("AREA=RAM-HOST") }.map { it.substringBefore('=').trim() to it.substringAfter('=').trim() }
    val ponds = (listOfNotNull(inv?.let { it.area to "${it.ramHost}:${it.ramPort}" }) + listed).distinctBy { it.first }
    if (ponds.isEmpty()) { log("bridge: no ponds listed (FDF_PONDS / FDF_PONDS_FILE): nothing to join"); exitProcess(2) }
    if (forward().isEmpty()) { ff.absoluteFile.parentFile?.mkdirs(); ff.writeText(ponds[0].first + "\n"); log("bridge: no forward pond named yet: ${ponds[0].first} (the first listed), written to $ff") }
    log("bridge: ponds ${ponds.joinToString { "${it.first} at ${it.second}" }}; forward ${forward()}; ${System.getProperty("os.name")}, Java ${System.getProperty("java.version")}")
    for ((area, ram) in ponds) Thread({
        val c = ram.lastIndexOf(':'); val say = { m: String -> log("[$area] $m") }
        while (!refused.containsKey(area)) {
            val leaf = Leaf(area, ::ipv4s, { pub }, fwdDriver, say, isForward = { forward() == area }); leaves[area] = leaf
            val reach = Reach(area, bridge, { pub }, say) { why -> refused[area] = why }; reaches[area] = reach
            val invited = inv?.takeIf { it.area == area }           // its tunnel is the invitation's, up before the session
            if (invited != null) runCatching { fwdDriver.up(invited.hub, "${invited.edge}/32"); say("tunnel: ${invited.edge} to ${invited.hub.name} at ${invited.hub.endpoint} (the invitation's)") }
                .onFailure { say("tunnel: ${it.message}") }
            val m = Member(MemberConfig(ram.substring(0, c), ram.substring(c + 1).toInt(), name, guid, ::ipv4s, ::capability,
                { if (invited != null) invited.facts(pub) else if (forward() == area) leafFacts(leaf.placement, name, guid, pub)
                  else "{\"role\":\"participant\",\"name\":" + Json.quote(name) + ",\"guid\":" + Json.quote(guid) + ",\"pubkey\":" + Json.quote(pub) + ",\"reach\":true}" },
                area, invited?.siteKey ?: "", 30, jvmTuner, say,
                prepare = { mm -> if (invited == null && forward() == area) leaf.place(mm, mm.world()) },
                onJoined = { mm -> if (invited == null) leaf.tunnel(mm, mm.world()); if (forward() != area) reach.onRegionChange(mm) },
                onRegionChange = { mm -> if (invited == null) leaf.onRegionChange(mm); if (forward() != area) reach.onRegionChange(mm) else bridge.removeIsland(area, "it is the forward pond now") },
                onAttention = { _, cell -> if (forward() != area) log("ATTENTION $area: ${cell["bag"]["why"].str} (from ${cell["bag"]["from"].str})") }))
            members[area] = m; val why = m.runOnce(); members.remove(area)
            if (forward() == area || invited != null) runCatching { fwdDriver.down("the session to $area ended: $why") }.onFailure { log("[$area] ${it.message}") }   // gone is gone
            reach.ended(why)
            if (refused.containsKey(area)) { say("not rejoined: the reach was refused (${refused[area]})"); break }
            Thread.sleep(5000)
        }
    }, "pond-$area").apply { isDaemon = true; start() }
    System.getenv("FDF_CTL_PORT")?.toIntOrNull()?.let { port -> Thread({
        val ss = java.net.ServerSocket(port, 8, java.net.InetAddress.getByName("127.0.0.1"))
        while (true) { val so = ss.accept(); Thread({ so.use { s ->
            val q = s.getInputStream().bufferedReader().readLine() ?: return@use
            val a = try { when {
                q.startsWith("resolve ") -> "OK " + bridge.resolve(q.substring(8).trim(), HashMap(members), forward())
                // [COMMS_FROM_THE_POND_V1] "comms [area]": the Communicator a pond's members run (default the forward one),
                // its two servers resolved by identity across every pond -> "--ram H:P --media H:P" for comms-app.exe
                q == "comms" || q.startsWith("comms ") -> { val area = q.substringAfter("comms", "").trim().ifEmpty { forward() }
                    val m = members[area] ?: throw IllegalStateException("not joined to $area")
                    val (from, b) = m.services("comms").firstOrNull() ?: throw IllegalStateException("no member of $area runs a Communicator (no comms in its where cells)")
                    "OK --ram " + bridge.resolve(b["ram"].str, HashMap(members), forward()) + " --media " + bridge.resolve(b["media"].str, HashMap(members), forward()) + "   (on $from in $area)" }
                q == "status" -> "OK " + bridge.status().toString()
                q == "set" -> "OK " + bridge.setJson()
                else -> "ERR unknown: $q" } }
                catch (e: Ambiguous) { "AMBIGUOUS ${e.message}" } catch (e: Exception) { "ERR ${e.message}" }
            s.getOutputStream().write((a + "\n").toByteArray()); s.getOutputStream().flush() } }, "ctl").start() }
    }, "ctl-accept").apply { isDaemon = true; start() } }
    val w = FileSystems.getDefault().newWatchService(); ff.absoluteFile.parentFile.toPath().register(w, StandardWatchEventKinds.ENTRY_CREATE, StandardWatchEventKinds.ENTRY_MODIFY)
    var cur = forward()
    while (true) {
        val k = w.take(); val mine = k.pollEvents().any { it.context().toString() == ff.name }; k.reset(); if (!mine) continue
        val now = forward(); if (now == cur || now.isEmpty()) continue
        val t0 = System.currentTimeMillis(); val old = cur; cur = now
        // the old pond first: its tunnel down (the driver is shared); then the new one up; then the old one reached
        members[old]?.let { m -> m.reannounce(); runCatching { leaves[old]?.onRegionChange(m) }.onFailure { log("forward: $old: ${it.message}") } }
        members[now]?.let { m -> runCatching { bridge.removeIsland(now, "it is the forward pond now"); leaves[now]?.becameForward(m) }.onFailure { log("forward: $now: ${it.message}") } }
        members[old]?.let { m -> runCatching { reaches[old]?.onRegionChange(m) }.onFailure { log("forward: $old: ${it.message}") } }
        log("forward: $old -> $now in ${System.currentTimeMillis() - t0} ms")
    }
}

// selftest: the planner and the ledger, as rinab/tests/reach_alias_test.py proves fdf-reach's
fun selftest(): Int {
    var fails = 0; fun check(ok: Boolean, what: String) { println((if (ok) "PASS  " else "FAIL  ") + what); if (!ok) fails++ }
    var t = 1_000_000_000L; val q = 600_000L
    fun led() = AliasLedger({ t }, q)
    fun pick(l: AliasLedger, area: String, d: String, old: Map<String, String> = emptyMap(), taken: Set<String> = emptySet(), scope: Map<String, String> = emptyMap()) = l.pick(area, d, old, taken, scope)
    check(pick(led(), "B", "10.7.7.0/24") == "100.64.0.0/24", "nothing held: the first block")
    check(pick(led(), "B", "10.7.7.0/24", old = mapOf("100.64.9.0/24" to "10.7.7.0/24")) == "100.64.9.0/24", "a reach keeps the block it has")
    led().let { l -> l.released("B", mapOf("100.64.5.0/24" to "10.7.7.0/24")); t += 10 * q
        check(pick(l, "B", "10.7.7.0/24") == "100.64.5.0/24", "B back after a session loss (long ago): its own block again") }
    led().let { l -> l.released("B", mapOf("100.64.0.0/24" to "10.7.7.0/24")); t += 5000
        check(pick(l, "F", "10.7.7.0/24") == "100.64.1.0/24", "B's block released 5 s ago: F does not get it")
        check(pick(l, "B", "10.8.8.0/24") == "100.64.1.0/24", "nor does B for a different prefix")
        check(pick(l, "B", "10.7.7.0/24") == "100.64.0.0/24", "B for the same prefix: its block at once")
        check(pick(l, "F", "10.9.9.9/32") == "100.64.1.1/32", "a /32 inside a quarantined /24 is quarantined too (and never an .0 or .255 address)")
        t += q; check(pick(l, "F", "10.7.7.0/24") == "100.64.0.0/24", "after the quarantine any use may have it") }
    led().let { l -> l.released("B", mapOf("100.64.0.0/24" to "10.7.7.0/24"))
        check(pick(l, "B", "10.7.7.0/24", taken = setOf("100.64.0.0/24")) == "100.64.1.0/24", "B's old block, held by another reach now: never shared")
        check(pick(l, "B", "10.7.7.0/24", scope = mapOf("100.64.0.0/16" to "here")) == "100.65.0.0/24", "a block colliding with this member's scope is never used, even B's own") }
    led().let { l -> l.released("X", mapOf("100.64.0.0/10" to "10.0.0.0/10"))
        check(runCatching { pick(l, "B", "10.7.7.0/24") }.exceptionOrNull() is ReachRefused, "every block quarantined: refused") }
    // the planner on a world as the region answers it: B (reach all, map allowed) with b 10.8.8.0/24, c 10.7.7.0/24, a leaf
    val worldText = """{"participants":[
      {"ns":"w:g-ph","alive":true,"name":"ph","facts":{"role":"participant","reach":true,"pubkey":"PH","addr":"10.252.0.1"}},
      {"ns":"w:g-hub","alive":true,"name":"hub-B","facts":{"role":"hub","name":"hub-B","pubkey":"HB","endpoint":"198.18.0.2:51821","peers":["PH"],"area":"pondB"}},
      {"ns":"w:g-b","alive":true,"name":"b","facts":{"addrs":[{"kind":"own","ip":"10.8.8.1"}]}},
      {"ns":"w:g-c","alive":true,"name":"c","facts":{"addrs":[{"kind":"own","ip":"10.7.7.1"}]}},
      {"ns":"w:g-l","alive":true,"name":"l","facts":{"role":"leaf","addr":"10.252.0.2"}},
      {"ns":"w:g-x","alive":false,"name":"x","facts":{"addrs":[{"kind":"own","ip":"10.9.9.1"}]}}]}"""
    val world = Json.parse(worldText)
    val all = Json.parse("""{"reach":"all","map":"allowed"}"""); val scope = mapOf(BridgeNet.PHONE_SCOPE to "forward")
    val p = ReachPlanner.plan("pondB", all, world, null, "w:g-ph", "PH", "10.252.0.1", scope, emptyMap(), emptySet(), led())
    check(p.routes == listOf("10.7.7.0/24", "10.8.8.0/24", "10.252.0.2/32") && p.alias.size == 3 && p.alias.values.toSet() == p.routes.toSet(),
          "reach all: every alive member's /24 and the leaf's /32, each aliased (the phone's 10/8 is its forward pond's): ${p.describe()}")
    check(runCatching { ReachPlanner.plan("pondB", Json.parse("""{"reach":"all","map":"refused"}"""), world, null, "w:g-ph", "PH", "10.252.0.1", scope, emptyMap(), emptySet(), led()) }
          .exceptionOrNull().let { it is ReachRefused && "map refused" in (it.message ?: "") }, "map refused and a collision: refused, naming it")
    check(runCatching { ReachPlanner.plan("pondB", null, world, null, "w:g-ph", "PH", "10.252.0.1", scope, emptyMap(), emptySet(), led()) }.exceptionOrNull() is ReachRefused, "no config: refused")
    check(runCatching { ReachPlanner.plan("pondB", Json.parse("""{"reach":"none"}"""), world, null, "w:g-ph", "PH", "10.252.0.1", scope, emptyMap(), emptySet(), led()) }.exceptionOrNull() is ReachRefused, "reach none: refused")
    check(runCatching { ReachPlanner.plan("pondB", all, world, null, "w:g-ph", "OTHER", "10.252.0.1", scope, emptyMap(), emptySet(), led()) }.exceptionOrNull() is ReachPending, "the hub has not peered this key: not yet")
    check(runCatching { ReachPlanner.plan("pondB", all, world, null, "w:g-ph", "PH", "", scope, emptyMap(), emptySet(), led()) }.exceptionOrNull() is ReachPending, "no address given yet: not yet")
    val listed = ReachPlanner.plan("pondB", Json.parse("""{"reach":"listed","map":"allowed"}"""), world,
        mapOf("w:g-c" to Json.parse("""{"addr":"10.7.7.1","name":"c"}"""), "w:g-q" to Json.parse("""{"error":"ENOENT"}""")), "w:g-ph", "PH", "10.252.0.1", scope, emptyMap(), emptySet(), led())
    check(listed.routes == listOf("10.7.7.1/32") && listed.alias.size == 1, "reach listed: only the endpoints the where cells name: ${listed.describe()}")
    check(ReachPlanner.listedIds(Json.parse("""{"cells":[{"bag":{"comms":{"ram":"w:g-c:8800","media":"w:g-c:8994"},"x":{"y":"10.1.1.1:80"}}}]}""")) == setOf("w:g-c"),
          "where cells: identities only (an address is not taken)")
    // [PLAN_UNDER_THE_BRIDGE_LOCK_V1] two ponds, both serving 10.7.7.0/24, planned at the same moment, 200 times: never one block
    val okLink = object : BridgeLink { override fun call(line: String) = Json.parse("{\"ok\":true}"); override fun close() {} }
    val fakeHost = object : BridgeHost { override fun link(forwardRoutes: Boolean) = okLink; override fun stop() {} }
    var clash = 0
    repeat(200) {
        val br = Bridge(fakeHost, "K", {}); val go = java.util.concurrent.CountDownLatch(1)
        val ts = listOf("pondB", "pondF").map { area -> Thread {
            go.await(); br.decide(area) { sc, old, tk -> ReachPlanner.plan(area, all, Json.parse(worldText.replace("\"area\":\"pondB\"", "\"area\":\"$area\"")), null, "w:g-ph", "PH", "10.252.0.1", sc, old, tk, br.ledger) } }.apply { start() } }
        go.countDown(); ts.forEach { it.join() }
        val b = br.plan("pondB")!!.alias.keys; val f = br.plan("pondF")!!.alias.keys
        if (b.any { x -> f.any { y -> Pfx.parse(x).overlaps(Pfx.parse(y)) } }) clash++
    }
    check(clash == 0, "two ponds planned at once, 200 times: their alias blocks never overlap ($clash did)")
    // [COVERED_IS_NOT_A_DESTINATION_V1] a leaf /32 inside a served /24 is reached through the /24's alias
    val nested = Json.parse(worldText.replace("\"addr\":\"10.252.0.2\"", "\"addr\":\"10.7.7.9\""))
    val pn = ReachPlanner.plan("pondB", all, nested, null, "w:g-ph", "PH", "10.252.0.1", scope, emptyMap(), emptySet(), led())
    check(pn.routes == listOf("10.7.7.0/24", "10.8.8.0/24"), "a /32 inside a served /24 is not a destination of its own: ${pn.describe()}")
    // a block a re-plan drops is quarantined like a released one
    run {
        val br = Bridge(fakeHost, "K", {}, ledger = led()); val hub = Hub("h", "HB", "1.2.3.4:5", "", "pondB", setOf("PH"))
        br.setIsland(ReachPlan("pondB", "10.252.0.1", hub, listOf("10.7.7.0/24"), mapOf("100.64.0.0/24" to "10.7.7.0/24"), "all", "allowed"))
        br.setIsland(ReachPlan("pondB", "10.252.0.1", hub, listOf("10.8.8.0/24"), mapOf("100.64.1.0/24" to "10.8.8.0/24"), "all", "allowed"))
        check(br.ledger.pick("pondF", "10.7.7.0/24", emptyMap(), br.takenExcept("pondF"), br.scopeFor("pondF")) == "100.64.2.0/24", "a block B's re-plan dropped is not F's for the quarantine")
    }
    // the ledger survives a restart (a file)
    run {
        val f = File.createTempFile("ledger", ".json"); f.delete()
        AliasLedger({ t }, q, f).released("pondB", mapOf("100.64.7.0/24" to "10.7.7.0/24"))
        check(AliasLedger({ t }, q, f).pick("pondF", "10.7.7.0/24", emptyMap(), emptySet(), emptyMap()) == "100.64.0.0/24" &&
              AliasLedger({ t }, q, f).pick("pondB", "10.7.7.0/24", emptyMap(), emptySet(), emptyMap()) == "100.64.7.0/24", "the ledger is read back after a restart: B's block is B's again")
        f.delete()
    }
    // fdf-tun gone: everything dropped, onLost called, aliases quarantined
    run {
        var lostWhy = ""; val gone = java.util.concurrent.CountDownLatch(1)
        val deadLink = object : BridgeLink { override fun call(line: String): Json = throw java.io.IOException("broken pipe"); override fun close() {} }
        val br = Bridge(object : BridgeHost { override fun link(forwardRoutes: Boolean) = deadLink; override fun stop() {} }, "K", {}, ledger = led(),
                        onLost = { lostWhy = it; gone.countDown() })
        val hub = Hub("h", "HB", "1.2.3.4:5", "", "pondB", setOf("PH"))
        val r = runCatching { br.setIsland(ReachPlan("pondB", "10.252.0.1", hub, listOf("10.7.7.0/24"), mapOf("100.64.0.0/24" to "10.7.7.0/24"), "all", "allowed")) }
        gone.await(2, java.util.concurrent.TimeUnit.SECONDS)
        check(r.isFailure && br.areas().isEmpty() && "broken pipe" in lostWhy, "fdf-tun gone: nothing stands, every session is told to end ($lostWhy)")
    }
    // [MEMBER_IDENTITY_V1] the public key as `wg pubkey` makes it: RFC 7748 6.1 (Alice), and every key in FDF_WG_PAIRS ("priv pub;...")
    val hex = { h: String -> ByteArray(h.length / 2) { h.substring(it * 2, it * 2 + 2).toInt(16).toByte() } }
    val b64 = { b: ByteArray -> java.util.Base64.getEncoder().encodeToString(b) }
    check(publicKey(b64(hex("77076d0a7318a57d3c16c17251b26645df4c2f87ebc0992ab177fba51db92c2a"))) == b64(hex("8520f0098930a754748b7ddcb43ef75a0dbf3a0d26381af4eba4a98eaa9b4e6a")), "X25519 public key: RFC 7748 6.1")
    val pairs = (System.getenv("FDF_WG_PAIRS") ?: "").split(';').filter { ' ' in it }
    check(pairs.all { val (k, pk) = it.trim().split(' '); publicKey(k) == pk }, "the public key equals wg pubkey for ${pairs.size} keys made by wg genkey")
    run {
        val d = java.nio.file.Files.createTempDirectory("id").toFile(); val (f1, k1) = ensureIdentity(File(d, "facts.json"), File(d, "key"))
        val (f2, k2) = ensureIdentity(File(d, "facts.json"), File(d, "key"))
        check(k1 == k2 && f1["guid"].str == f2["guid"].str && f1["pubkey"].str == publicKey(k1) && f1["name"].str.isNotEmpty(), "first run makes the key and the facts; the second keeps them (guid ${f1["guid"].str})")
        d.deleteRecursively()
    }
    run {
        val on = Json.parse(leafFacts(Placement.OnMesh("10.250.250.20", "10.250.250.0/24", "Seattle5"), "DESK", "g-d", "PK"))
        val off = Json.parse(leafFacts(Placement.OffMesh, "DESK", "g-d", "PK"))
        check(on["role"].str == "leaf" && on["name"].str == "DESK" && on["lan"]["served_by"].str == "Seattle5" && on["pubkey"].str.isEmpty() &&
              off["pubkey"].str == "PK" && off["lan"].type == Json.Type.NULL, "leaf facts: on the mesh role/name/lan (no key), off it the key [DEVICE_ROW_V1]")
    }
    println("RESULT " + if (fails == 0) "PASS" else "FAIL: $fails"); return fails
}

// a secret on disk: 0600 from its first byte, replaced whole
fun writeSecret(f: File, text: String) {
    val dir = (f.absoluteFile.parentFile ?: File(".")).also { it.mkdirs() }.toPath()
    // Linux: 0600 from its first byte. Windows has no POSIX modes: the file takes its folder's ACL (the bridge's var\)
    val tmp = if (WINDOWS) java.nio.file.Files.createTempFile(dir, f.name, ".tmp")
              else java.nio.file.Files.createTempFile(dir, f.name, ".tmp", java.nio.file.attribute.PosixFilePermissions.asFileAttribute(
                  java.nio.file.attribute.PosixFilePermissions.fromString("rw-------")))
    java.nio.file.Files.write(tmp, (text.trim() + "\n").toByteArray())
    java.nio.file.Files.move(tmp, f.toPath(), java.nio.file.StandardCopyOption.REPLACE_EXISTING, java.nio.file.StandardCopyOption.ATOMIC_MOVE)
}

// [LEAF_BY_INVITE_V1] the invitation's tunnel, driven by `wg` on an interface the start-up made (kernel or wireguard-go,
// as fdf-wg-impl records): key, address (edge/16), the hub as the one peer for 10.0.0.0/8, then the route
class WgInviteTunnel(private val iface: String, private val keyDir: File) : InviteTunnel {
    private fun run(vararg a: String): String { val p = ProcessBuilder(a.toList()).redirectErrorStream(true).start()
        val out = p.inputStream.bufferedReader().readText().trim(); val rc = p.waitFor()
        if (rc != 0) throw IllegalStateException("${a.joinToString(" ")}: rc=$rc $out"); return out }
    @Volatile private var hub: Hub? = null
    override fun up(inv: Invite) {
        val key = File(keyDir, "leaf.key"); writeSecret(key, inv.privkey)
        run("wg", "set", iface, "private-key", key.path)
        hub?.let { if (it.pubkey != inv.hub.pubkey) run("wg", "set", iface, "peer", it.pubkey, "remove") }
        run("ip", "addr", "replace", inv.address, "dev", iface)
        run("ip", "link", "set", iface, "up")
        run("wg", "set", iface, "peer", inv.hub.pubkey, "endpoint", inv.hub.endpoint, "allowed-ips", Hubs.LEAF_ALLOWED,
            "persistent-keepalive", Hubs.PERSISTENT_KEEPALIVE.toString())
        run("ip", "route", "replace", Hubs.LEAF_ALLOWED, "dev", iface)
        hub = inv.hub
        log("tunnel: $iface ${inv.address}, peer ${inv.hub.name} ${inv.hub.pubkey.take(12)} at ${inv.hub.endpoint} for ${Hubs.LEAF_ALLOWED}")
    }
    override fun down(why: String) { val h = hub ?: return; runCatching { run("wg", "set", iface, "peer", h.pubkey, "remove") }; hub = null; log("tunnel: $iface peer ${h.name} removed ($why)") }
}

// leaf [fdfinv1.<...>]: [LEAF_BY_INVITE_V1] given a line, it is kept (FDF_INVITE, 0600) for every later start; without
// one, the kept line is used. The tunnel first, then the region inside it; the member announces role leaf, its name,
// its key and its edge. Nothing else is configured: the invitation is the whole of it.
fun invitedLeaf(given: String?) {
    val file = File(System.getenv("FDF_INVITE")?.takeIf { it.isNotEmpty() } ?: "/opt/fdf/var/invite")
    val text = if (given != null) {
        val inv = Invite.parse(given)                                   // refused before anything is written
        writeSecret(file, inv.line)
        log("invitation kept in $file"); inv.line
    } else if (file.exists()) file.readText() else { System.err.println("fdf-member-kt leaf: no invitation in $file: run it once with the fdfinv1.<...> line fdf-invite NAME --leaf printed"); exitProcess(2) }
    val inv = Invite.parse(text)
    log(inv.toString())
    val iface = System.getenv("FDF_LEAF_WG")?.takeIf { it.isNotEmpty() } ?: "fdf-leaf"
    val tun = WgInviteTunnel(iface, file.absoluteFile.parentFile ?: File("."))
    tun.up(inv)
    val pub = publicKey(inv.privkey)
    val m = Member(MemberConfig(inv.ramHost, inv.ramPort, inv.name, "", ::ipv4s, ::capability, { inv.facts(pub) },
        inv.area, inv.siteKey, System.getenv("FDF_ASSERT_SEC")?.toIntOrNull()?.takeIf { it > 0 } ?: 30, jvmTuner, ::log,
        onAttention = { _, cell -> log("ATTENTION ${inv.area}: ${cell["bag"]["why"].str} (from ${cell["bag"]["from"].str.ifEmpty { cell["instance"].str }})") }))
    log("ended: ${m.runOnce()}")
}

fun main(args: Array<String>) {
    if (args.firstOrNull() == "ponds") { ponds(); exitProcess(0) }
    if (args.firstOrNull() == "bridge") { bridgeMode(args.getOrNull(1)); exitProcess(0) }
    if (args.firstOrNull() == "pubkey") { args.drop(1).forEach { println(publicKey(it)) }; exitProcess(0) }   // as `wg pubkey`
    // ask "resolve w:<guid>:<port>" | "status" | "set": one question to the running bridge (FDF_CTL_PORT, 127.0.0.1)
    if (args.firstOrNull() == "ask") {
        val port = System.getenv("FDF_CTL_PORT")?.toIntOrNull() ?: 7999
        val a = try { java.net.Socket("127.0.0.1", port).use { so -> so.getOutputStream().write((args.drop(1).joinToString(" ") + "\n").toByteArray())
                so.getInputStream().bufferedReader().readLine() ?: "ERR no answer" } } catch (e: Exception) { "ERR the bridge is not running here (127.0.0.1:$port: ${e.message})" }
        println(a); exitProcess(if (a.startsWith("OK ")) 0 else if (a.startsWith("AMBIGUOUS")) 7 else 3) }
    if (args.firstOrNull() == "selftest") exitProcess(if (selftest() == 0) 0 else 1)
    // keep-invite fdfinv1.<...>: [LEAF_BY_INVITE_V1] kept in FDF_INVITE if it decodes (an installer's step); nothing started
    if (args.firstOrNull() == "keep-invite") { try { keptInvite(args.getOrNull(1) ?: error("usage: keep-invite fdfinv1.<...>"))?.let { println(it) } }
        catch (e: Exception) { System.err.println("fdf-member-kt: ${e.message}"); exitProcess(2) }; exitProcess(0) }
    if (args.firstOrNull() == "leaf") { invitedLeaf(args.getOrNull(1)); exitProcess(1) }
    if (args.firstOrNull() == "probe") { probe(args[1], args.getOrElse(2) { "frognet" }, args.getOrElse(3) { "200" }.toInt()); exitProcess(0) }
    val where = args.firstOrNull() ?: System.getenv("RINA_RAM") ?: ""
    val c = where.lastIndexOf(':')
    if (where.isEmpty() || c < 0) { System.err.println("usage: fdf-member-kt <ram-server>:<port>   (or set RINA_RAM)"); exitProcess(2) }
    val factsFile = File(System.getenv("FDF_FACTS")?.takeIf { it.isNotEmpty() } ?: "/opt/fdf/var/facts.json")
    fun fileFacts(): String = if (factsFile.exists()) factsFile.readText().trimEnd() else ""
    // on the mesh: the file's role/name/guid and where it is, no addr, no pubkey (Identity.facts in the app, same rule)
    val wgIface = System.getenv("FDF_LEAF_WG") ?: ""
    val leaf = Leaf(System.getenv("RINA_AREA") ?: "", ::ipv4s, { fileFacts().takeIf { it.isNotEmpty() }?.let { Json.parse(it)["pubkey"].str } ?: "" },
                    if (wgIface.isEmpty()) object : TunnelDriver { override val current: Hub? = null; override fun up(hub: Hub, addr: String) {}; override fun down(why: String) {} } else WgDriver(wgIface), ::log)
    fun facts(): String = when (val pl = leaf.placement) {
        is Placement.OnMesh -> fileFacts().takeIf { it.isNotEmpty() }?.let { Json.parse(it) }?.let { f ->
            "{\"role\":" + Json.quote(f["role"].str.ifEmpty { "leaf" }) + ",\"name\":" + Json.quote(f["name"].str) + ",\"guid\":" + Json.quote(f["guid"].str) +
            ",\"lan\":{\"ip\":" + Json.quote(pl.ip) + ",\"subnet\":" + Json.quote(pl.subnet) + ",\"served_by\":" + Json.quote(pl.servedBy) + "}}" } ?: ""
        Placement.OffMesh -> fileFacts()
    }
    // [SERVICE_WHERE_IS_A_TUPLE_V1] FDF_SERVICES=comms[,...]: say where the pond's members run them, when that changes
    val wanted = (System.getenv("FDF_SERVICES") ?: "").split(',').filter { it.isNotBlank() }; val shown = HashMap<String, String>()
    fun showServices(mm: Member) = wanted.forEach { sv ->
        val now = try { mm.services(sv).joinToString("; ") { (from, b) -> "$from " + b.o.entries.joinToString(" ") {
                      "${it.key}=${it.value.str} -> " + (try { mm.endpoint(it.value.str) } catch (e: Exception) { "UNRESOLVED (${e.message})" }) } } }
                  catch (e: Exception) { "read failed: ${e.message}" }
        if (shown[sv] != now) { shown[sv] = now; log("services: $sv in this pond: ${now.ifEmpty { "none" }}") } }
    var name = System.getenv("FDF_NAME") ?: ""; var guid = ""
    facts().takeIf { it.isNotEmpty() }?.let { runCatching { Json.parse(it) }.getOrNull() }?.let {
        if (name.isEmpty()) name = it["name"].str; guid = it["guid"].str }
    if (name.isEmpty()) name = java.net.InetAddress.getLocalHost().hostName
    val m = Member(MemberConfig(where.substring(0, c), where.substring(c + 1).toInt(), name, guid, ::ipv4s, ::capability, ::facts,
        System.getenv("RINA_AREA") ?: "", System.getenv("RINA_SITE_KEY") ?: "",
        System.getenv("FDF_ASSERT_SEC")?.toIntOrNull()?.takeIf { it > 0 } ?: 30, jvmTuner, ::log,
        prepare = { mm -> leaf.place(mm, mm.world()) }, onJoined = { mm -> leaf.tunnel(mm, mm.world()); showServices(mm) },
        onRegionChange = { mm -> leaf.onRegionChange(mm); showServices(mm) },
        onAttention = { _, cell -> log("ATTENTION ${System.getenv("RINA_AREA") ?: ""}: ${cell["bag"]["why"].str} (from ${cell["bag"]["from"].str.ifEmpty { cell["instance"].str }})") }))
    // the facts file's directory, watched (inotify underneath on Linux); our file's name only
    Thread({
        val dir = (factsFile.absoluteFile.parentFile ?: File(".")).toPath()
        val w = FileSystems.getDefault().newWatchService()
        dir.register(w, StandardWatchEventKinds.ENTRY_CREATE, StandardWatchEventKinds.ENTRY_MODIFY)
        var last = facts()
        while (true) {
            val k = w.take()
            val mine = k.pollEvents().any { it.context().toString() == factsFile.name }
            k.reset()
            if (!mine) continue
            val now = facts(); if (now == last || now.isEmpty()) continue; last = now
            m.reannounce()
        }
    }, "facts-watch").apply { isDaemon = true; start() }
    m.runOnce()
    exitProcess(1)
}
