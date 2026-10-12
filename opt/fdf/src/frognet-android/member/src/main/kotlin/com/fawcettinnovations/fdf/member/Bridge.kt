// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// Bridge.kt -- [BRIDGE_V1] PLAN-BRIDGE §3.1/§3.2 on the member: the phone reaches its forward pond AND every pond it is a
// participant in that allows it, at once, through one TUN, with fdf-tun applying what is decided here.
//
//   ReachPlanner   fdf-reach's plan(), rule for rule: only what the pond's (pond, config, net) allows (reach listed / all;
//                  none or no config = REFUSED); what collides with this machine's own scope is aliased 1:1 from
//                  100.64.0.0/10 only if map "allowed", otherwise REFUSED naming the prefix. Pending = not yet.
//   AliasLedger    [ALIAS_QUARANTINE_V1] a released block comes back to the same (pond, prefix), and to no other use for
//                  QUARANTINE (600 s): nothing made through an alias can land in a different network.
//   Bridge         the whole state (forward hub and given address, each island's plan) sent to fdf-tun as one `set`.
//                  resolve(): an identity asked of EVERY pond this member is in; two answers = ambiguous, refused
//                  ([RESOLVE_AMBIGUOUS_REFUSED_V1]).
//   Reach          one participant pond's decision on every region event of its session; REFUSED ends that session
//                  ("a failed reach closes it"); no fallbacks.
//
// The phone's scope ([PHONE_SCOPE_IS_10_8_V1]): 10.0.0.0/8 is the forward pond's, always -- through its hub when off the
// mesh, over the LAN when on it -- so every island prefix (ponds serve 10/8 only) is reached through an alias, and the VPN
// routes 100.64.0.0/10 and nothing of another pond's real space. The TUN's own address never changes (LOCAL): each pond's
// given address is put on the packets by fdf-tun, never on an interface.
package com.fawcettinnovations.fdf.member

import java.io.BufferedReader
import java.io.InputStreamReader
import java.io.OutputStreamWriter

class ReachRefused(msg: String) : Exception(msg)   // the pond's config does not allow it: the session ends
class ReachPending(msg: String) : Exception(msg)   // not yet: the next event tries again; nothing stands meanwhile
class ReachNotNow(msg: String) : Exception(msg)    // could not decide now: unchanged, tried again on the next event
class Ambiguous(msg: String) : Exception(msg)

/** An IPv4 prefix, as fdf-reach's ipaddress uses them. */
class Pfx private constructor(val net: Long, val bits: Int) : Comparable<Pfx> {
    val size: Long get() = 1L shl (32 - bits)
    fun overlaps(o: Pfx) = net < o.net + o.size && o.net < net + size
    fun contains(a: Long) = a >= net && a < net + size
    override fun toString() = ip4(net) + "/" + bits
    override fun equals(other: Any?) = other is Pfx && other.net == net && other.bits == bits
    override fun hashCode() = (net * 33 + bits).toInt()
    override fun compareTo(other: Pfx) = compareValuesBy(this, other, { it.net }, { it.bits })
    companion object {
        fun of(net: Long, bits: Int): Pfx { val m = if (bits == 0) 0L else (0xffffffffL shl (32 - bits)) and 0xffffffffL; return Pfx(net and m, bits) }
        fun parse(s: String): Pfx {
            val i = s.indexOf('/'); val a = ip4(if (i < 0) s else s.substring(0, i)) ?: throw IllegalArgumentException("'$s' is not an IPv4 prefix")
            val b = if (i < 0) 32 else s.substring(i + 1).toIntOrNull()?.takeIf { it in 0..32 } ?: throw IllegalArgumentException("'$s' is not an IPv4 prefix")
            return of(a, b)
        }
        fun ip4(s: String): Long? {
            val p = s.split('.'); if (p.size != 4) return null
            var v = 0L; for (x in p) { val n = x.toIntOrNull() ?: return null; if (n !in 0..255 || x.isEmpty()) return null; v = (v shl 8) or n.toLong() }
            return v
        }
        fun ip4(v: Long) = "${(v shr 24) and 255}.${(v shr 16) and 255}.${(v shr 8) and 255}.${v and 255}"
    }
}

object BridgeNet {
    val ALIASES: Pfx = Pfx.parse("100.64.0.0/10")
    val SELVES: Pfx = Pfx.parse("100.127.255.0/24")              // never an alias block (a node's private reach sides)
    const val LOCAL = "100.127.255.1"                             // the phone's TUN address, whatever any pond gave
    const val PHONE_SCOPE = "10.0.0.0/8"                          // [PHONE_SCOPE_IS_10_8_V1]
    val VPN_ROUTES = listOf("100.64.0.0/10")                      // + Hubs.LEAF_ALLOWED while the forward tunnel is up
}

/** [ALIAS_QUARANTINE_V1] fdf-reach's pick_alias and its .released ledger. Kept in `file` when given (the app's files
 *  dir, the runner's FDF_LEDGER), as fdf-reach keeps <var>/reach/.released: a restart within the quarantine must not
 *  hand a block to another network while something made through it may still be held (review 2026-10-11). */
class AliasLedger(private val now: () -> Long = System::currentTimeMillis, val quarantineMs: Long = 600_000L, private val file: java.io.File? = null) {
    class Rel(val area: String, val real: String, val atMs: Long)
    private val rel = HashMap<String, Rel>()
    init { file?.takeIf { it.exists() }?.let { f -> runCatching { for ((k, v) in Json.parse(f.readText()).o) rel[k] = Rel(v["area"].str, v["real"].str, v["t"].num?.toLong() ?: 0L) } } }
    private fun save() { val f = file ?: return; runCatching {
        val t = java.io.File(f.path + ".tmp")
        t.writeText("{" + rel.entries.joinToString(",") { (k, v) -> Json.quote(k) + ":{\"area\":" + Json.quote(v.area) + ",\"real\":" + Json.quote(v.real) + ",\"t\":" + v.atMs + "}" } + "}")
        t.renameTo(f) } }
    @Synchronized fun released(area: String, alias: Map<String, String>) { if (alias.isEmpty()) return; val t = now(); for ((a, r) in alias) rel[a] = Rel(area, r, t); save() }
    @Synchronized fun inUse(alias: Collection<String>) { if (alias.any { rel.remove(it) != null }) save() }
    @Synchronized fun snapshot(): Map<String, Rel> = HashMap(rel)

    private fun quarantined(b: Pfx, area: String, real: String, t: Long) = rel.any { (k, e) ->
        (e.area != area || e.real != real) && t - e.atMs < quarantineMs && Pfx.parse(k).overlaps(b) }

    /** the alias block for real prefix d of area: the one it has now; else the one this area had for d before; else the
     *  first block nobody holds, colliding with nothing in scope, not quarantined by another use. None: refused. */
    @Synchronized fun pick(area: String, d: String, old: Map<String, String>, taken: Set<String>, scope: Map<String, String>): String {
        val dn = Pfx.parse(d); val t = now()
        val takenP = taken.map { Pfx.parse(it) }; val scopeP = scope.keys.map { Pfx.parse(it) }
        fun free(b: Pfx) = b.bits == dn.bits && !b.overlaps(BridgeNet.SELVES) && !(b.bits == 32 && (b.net and 255L).let { it == 0L || it == 255L }) && takenP.none { it.overlaps(b) } && scopeP.none { it.overlaps(b) }
        old.entries.sortedBy { it.key }.firstOrNull { it.value == d && free(Pfx.parse(it.key)) }?.let { return it.key }
        rel.entries.sortedBy { it.key }.firstOrNull { it.value.area == area && it.value.real == d && free(Pfx.parse(it.key)) }?.let { return it.key }
        if (dn.bits < BridgeNet.ALIASES.bits) throw ReachRefused("$d is larger than the alias space ${BridgeNet.ALIASES}")
        var n = BridgeNet.ALIASES.net; val end = BridgeNet.ALIASES.net + BridgeNet.ALIASES.size
        while (n < end) {
            val b = Pfx.of(n, dn.bits)
            if (free(b) && !quarantined(b, area, d, t)) return b.toString()
            n += b.size
        }
        throw ReachRefused("no free alias block for $d in ${BridgeNet.ALIASES} (quarantined: ${rel.count { (k, e) -> (e.area != area || e.real != d) && t - e.atMs < quarantineMs }})")
    }
}

/** One pond's reach, as fdf-tun takes it. */
class ReachPlan(val area: String, val given: String, val hub: Hub, val routes: List<String>, val alias: Map<String, String>,
                val reach: String, val map: String) {
    fun same(o: ReachPlan?) = o != null && o.given == given && o.hub.pubkey == hub.pubkey && o.hub.endpoint == hub.endpoint &&
        o.routes == routes && o.alias == alias
    fun describe() = "reach into $area ($reach, map $map) as $given there via hub ${hub.name}: " + (routes.joinToString(" ").ifEmpty { "nothing listed" }) +
        (if (alias.isEmpty()) "" else "; aliased " + alias.entries.sortedBy { it.key }.joinToString(", ") { "${it.key} -> ${it.value}" })
}

object ReachPlanner {
    /** a member's served /24s (the region's served_prefixes): own .1 rows and its subnet */
    fun served(f: Json): Set<String> {
        val out = HashSet<String>()
        for (r in f["addrs"].a) { val ip = r["ip"].str
            if (r["kind"].str == "own" && ip.startsWith("10.") && ip.endsWith(".1")) out.add(ip.substringBeforeLast('.') + ".0/24") }
        if (f["subnet"].str.isNotEmpty()) runCatching { out.add(Pfx.parse(f["subnet"].str).toString()) }
        return out
    }

    /** the identities a pond's where cells name: "w:<guid>" of every "w:<guid>:<port>" value */
    fun listedIds(where: Json): Set<String> {
        val ids = HashSet<String>()
        for (cell in (if (where["rows"].type == Json.Type.ARR) where["rows"] else where["cells"]).a)
            for ((_, b) in cell["bag"].o) if (b.type == Json.Type.OBJ) for ((_, v) in b.o) {
                val s = v.str; val c = s.lastIndexOf(':')
                if (s.startsWith("w:") && c > 2 && s.substring(c + 1).toIntOrNull() != null) ids.add(s.substring(0, c))
            }
        return ids
    }

    /** fdf-reach plan(): cfg is the (pond, config, net) bag (null: none published); resolved the region's answer for
     *  listedIds (reach listed only). Throws ReachRefused / ReachPending / ReachNotNow. */
    fun plan(area: String, cfg: Json?, world: Json, resolved: Map<String, Json>?, me: String, myPub: String, given: String,
             scope: Map<String, String>, old: Map<String, String>, taken: Set<String>, ledger: AliasLedger): ReachPlan {
        if (cfg == null) throw ReachRefused("$area publishes no network config (pond, config, net): it is not reachable")
        val reach = cfg["reach"].str
        if (reach != "listed" && reach != "all") throw ReachRefused("$area's config says reach '${reach.ifEmpty { "none" }}': it is not reachable")
        val mapOk = cfg["map"].str == "allowed"
        val parts = world["participants"].a.filter { it["alive"].bool }
        parts.firstOrNull { it["ns"].str == me } ?: throw ReachPending("this member ($me) is not an alive member of $area yet")
        if (given.isEmpty()) throw ReachPending("$area has given this member no address yet")
        if (Pfx.ip4(given) == null) throw ReachNotNow("$area gave this member '$given', which is not an IPv4 address")
        val hub = Hubs.from(world, area).firstOrNull() ?: throw ReachPending("$area has no hub")
        if (myPub !in hub.peers) throw ReachPending("$area's hub ${hub.name} has not peered this member's key yet")
        val dest = LinkedHashMap<String, String>()
        if (reach == "all") {
            for (p in parts) { val f = p["facts"]
                if (p["ns"].str == me || f["role"].str == "hub") continue
                for (s in served(f)) dest[s] = p["name"].str
                if (f["addr"].str.isNotEmpty() && (f["role"].str == "leaf" || f["reach"].bool)) dest[f["addr"].str.substringBefore('/') + "/32"] = p["name"].str
            }
        } else for ((id, r) in resolved ?: emptyMap()) if (r["addr"].str.isNotEmpty() && id != me) dest[r["addr"].str + "/32"] = r["name"].str.ifEmpty { id }
        // [COVERED_IS_NOT_A_DESTINATION_V1] a destination inside another (a /32 in a served /24) is reached through the
        // covering one: its own alias would answer from a different address than the one connected to
        val dps = dest.keys.map { Pfx.parse(it) }
        dest.keys.retainAll { d -> val dn = Pfx.parse(d); dps.none { it.bits < dn.bits && it.overlaps(dn) } }
        val alias = LinkedHashMap<String, String>(); val routes = ArrayList<String>()
        for (d in dest.keys.sortedBy { Pfx.parse(it) }) {
            val dn = Pfx.parse(d); routes.add(d)
            val hit = scope.entries.firstOrNull { Pfx.parse(it.key).overlaps(dn) } ?: continue
            if (!mapOk) throw ReachRefused("$d (${dest[d]} in $area) collides with ${hit.key} (${hit.value}), and $area's config says map refused")
            alias[ledger.pick(area, d, old, taken + alias.keys, scope)] = d
        }
        return ReachPlan(area, given, hub, routes, alias, reach, cfg["map"].str.ifEmpty { "refused" })
    }
}

/** fdf-tun's control channel: one JSON line out, one answer line back. */
interface BridgeLink { fun call(line: String): Json; fun close() }

/** Where the TUN comes from. link(forwardRoutes): a link to an fdf-tun whose TUN routes 100.64.0.0/10, and 10.0.0.0/8 too
 *  when forwardRoutes (the forward tunnel is up). It may return the link it gave before (the JVM runner re-routes its own
 *  TUN) or a new one (Android: VpnService routes are fixed at establish(), so a new TUN, a new fdf-tun; the Bridge sends
 *  the whole state to it and closes the old). stop(): nothing to carry, no TUN (no VPN shown). */
interface BridgeHost { fun link(forwardRoutes: Boolean): BridgeLink; fun stop() }

/** fdf-tun run as a child process (the JVM runner: --tun NAME; the app: --fd-socket NAME, then the fd over that socket). */
class ProcessBridgeLink(cmd: List<String>, private val log: (String) -> Unit) : BridgeLink {
    private val p: Process = ProcessBuilder(cmd).start()
    private val out = OutputStreamWriter(p.outputStream)
    private val inp = BufferedReader(InputStreamReader(p.inputStream))
    init { Thread({ p.errorStream.bufferedReader().forEachLine { log(it) } }, "fdf-tun-stderr").apply { isDaemon = true; start() } }
    /** the next unsolicited line (the hello: {"ok":true,"build":..,"tun":..} or {"ok":true,"listening":"@.."}) */
    fun hello(): Json = Json.parse(inp.readLine() ?: throw ProtocolError("fdf-tun exited: ${runCatching { p.waitFor() }.getOrNull()}"))
    @Synchronized override fun call(line: String): Json {
        out.write(line); out.write("\n"); out.flush()
        return Json.parse(inp.readLine() ?: throw ProtocolError("fdf-tun exited (rc ${runCatching { p.waitFor() }.getOrNull()})"))
    }
    @Volatile private var closed = false
    /** stdin closed: fdf-tun closes every device and its TUN, and exits (gone is gone) */
    override fun close() { if (closed) return; closed = true; runCatching { out.close() }; if (!p.waitFor(3, java.util.concurrent.TimeUnit.SECONDS)) p.destroyForcibly() }
}

/** The member's whole packet state, sent to fdf-tun whole on every change. */
class Bridge(private val host: BridgeHost, private val privateKey: String, private val log: (String) -> Unit,
             val local: String = BridgeNet.LOCAL, val ledger: AliasLedger = AliasLedger(),
             private val isForward: (String) -> Boolean = { false },              // the forward pond is never an island
             private val extraScope: () -> Map<String, String> = { emptyMap() },  // what the device's own networks use
             private val onLost: (String) -> Unit = {}) {                         // fdf-tun is gone: every session ends
    private var link: BridgeLink? = null; private var shape: Boolean? = null
    private var fwdHub: Hub? = null; private var fwdGiven = ""
    private val islands = LinkedHashMap<String, ReachPlan>()

    @Synchronized fun plan(area: String): ReachPlan? = islands[area]
    @Synchronized fun areas(): Set<String> = islands.keys.toSet()

    /** what this member already reaches, for area's plan: the forward pond's 10/8 and every other island's alias blocks */
    @Synchronized fun scopeFor(area: String): Map<String, String> {
        val s = LinkedHashMap<String, String>(); s[BridgeNet.PHONE_SCOPE] = "the forward pond's (the phone routes all of 10/8 to it)"
        s.putAll(extraScope())
        for ((a, p) in islands) if (a != area) {
            for (k in p.alias.keys) s[k] = "this member's alias for $a"
            for (r in p.routes) if (r !in p.alias.values) s[r] = "this member's reach into $a"
        }
        return s
    }
    @Synchronized fun takenExcept(area: String): Set<String> = islands.filterKeys { it != area }.values.flatMap { it.alias.keys }.toSet()

    @Synchronized fun setForward(hub: Hub?, given: String) {
        val wasHub = fwdHub; val wasGiven = fwdGiven
        fwdHub = hub; fwdGiven = if (hub == null) "" else given.substringBefore('/')
        try { push(if (hub == null) "forward down" else "forward via hub ${hub.name} as $fwdGiven") }
        catch (e: Refusal) { if (hub == null) lost("fdf-tun would not take the forward tunnel down (${e.message})")
                             fwdHub = wasHub; fwdGiven = wasGiven; throw ProtocolError(e.message ?: "") }
    }

    /** [PLAN_UNDER_THE_BRIDGE_LOCK_V1] decide and commit as one step: two ponds planned at once never both take a block
     *  (seen: B and F each chose 100.64.0.0/24 from the same free list). planner gets (scope, old aliases, taken). */
    @Synchronized fun decide(area: String, planner: (Map<String, String>, Map<String, String>, Set<String>) -> ReachPlan): Pair<ReachPlan, Boolean> {
        if (isForward(area)) throw ReachNotNow("$area is the forward pond now")   // checked under the lock bringForward removes it under
        val p = planner(scopeFor(area), islands[area]?.alias ?: emptyMap(), takenExcept(area))
        return p to setIsland(p)
    }

    /** false: unchanged */
    @Synchronized fun setIsland(p: ReachPlan): Boolean {
        val mine = p.alias.keys.map { Pfx.parse(it) }
        for ((a, o) in islands) if (a != p.area) for (k in o.alias.keys) if (mine.any { it.overlaps(Pfx.parse(k)) })
            throw ReachNotNow("alias $k is $a's")                 // cannot happen through decide(); said if it does
        if (p.same(islands[p.area])) return false
        val before = islands[p.area]
        islands[p.area] = p
        try { push(p.describe()) } catch (e: Refusal) { if (before == null) islands.remove(p.area) else islands[p.area] = before; throw ProtocolError(e.message ?: "") }
        ledger.inUse(p.alias.keys)
        before?.alias?.filterKeys { it !in p.alias }?.let { ledger.released(p.area, it) }   // a block a re-plan dropped is quarantined too
        return true
    }

    @Synchronized fun removeIsland(area: String, why: String): Boolean {
        val p = islands.remove(area) ?: return false
        ledger.released(area, p.alias)
        try { push("reach into $area taken down: $why") } catch (e: Refusal) { lost("fdf-tun would not take $area down (${e.message})") }
        return true
    }

    private fun peerJson(h: Hub, allowed: List<String>) = "{\"pub\":" + Json.quote(h.pubkey) + ",\"endpoint\":" + Json.quote(h.endpoint) +
        ",\"allowed\":[" + allowed.joinToString(",") { Json.quote(it) } + "],\"keepalive\":" + Hubs.PERSISTENT_KEEPALIVE + "}"

    fun setJson(): String = synchronized(this) {
        val f = fwdHub?.let { "{\"key\":" + Json.quote(privateKey) + ",\"given\":" + Json.quote(fwdGiven) + ",\"peer\":" + peerJson(it, listOf(Hubs.LEAF_ALLOWED)) + "}" } ?: "null"
        "{\"op\":\"set\",\"local\":" + Json.quote(local) + ",\"forward\":" + f + ",\"islands\":[" + islands.values.joinToString(",") { p ->
            "{\"area\":" + Json.quote(p.area) + ",\"key\":" + Json.quote(privateKey) + ",\"given\":" + Json.quote(p.given) + ",\"peer\":" + peerJson(p.hub, p.routes) +
            ",\"routes\":[" + p.routes.joinToString(",") { Json.quote(it) } + "],\"alias\":{" + p.alias.entries.joinToString(",") { Json.quote(it.key) + ":" + Json.quote(it.value) } + "}}" } + "]}"
    }

    private fun push(what: String) {
        if (fwdHub == null && islands.isEmpty()) {                 // nothing to carry: no TUN
            if (link != null) { link?.close(); link = null; shape = null; host.stop(); log("bridge: $what; nothing to carry, the TUN is closed") }
            return
        }
        val want = fwdHub != null
        if (link == null || shape != want) {
            val n = host.link(want)
            if (n !== link) { link?.close(); log("bridge: a TUN routing 100.64.0.0/10" + (if (want) " and ${Hubs.LEAF_ALLOWED}" else "")) }
            link = n; shape = want
        }
        val a = try { link!!.call(setJson()) } catch (e: Exception) { lost("fdf-tun did not answer (${e.message})") }
        if (!a["ok"].bool) throw Refusal("fdf-tun refused the set ($what): ${a["error"].str}")
        log("bridge: $what")
    }

    private class Refusal(msg: String) : Exception(msg)   // fdf-tun said no; what runs is unchanged (its set is all or nothing)

    /** fdf-tun is gone or broken: nothing it carried stands. Everything here is dropped (aliases quarantined), the TUN
     *  closed, and every session ends so the next ones decide again from the regions ([GONE_IS_GONE_V1]). */
    private fun lost(why: String): Nothing {
        log("BRIDGE LOST: $why -- every tunnel is down; every session ends and the next ones start it again")
        for ((a, p) in islands) ledger.released(a, p.alias)
        islands.clear(); fwdHub = null; runCatching { link?.close() }; link = null; shape = null; runCatching { host.stop() }
        Thread({ onLost(why) }, "fdf-bridge-lost").apply { isDaemon = true; start() }
        throw ProtocolError("the bridge is gone: $why")
    }

    @Synchronized fun status(): Json = link?.call("{\"op\":\"status\"}") ?: Json.parse("{\"ok\":true,\"status\":null}")

    /** the member is going: everything down, the TUN closed */
    @Synchronized fun close() {
        for ((a, p) in islands) ledger.released(a, p.alias)
        fwdHub = null; islands.clear(); runCatching { link?.close() }; link = null; shape = null; runCatching { host.stop() }
    }

    /** [RESOLVE_AMBIGUOUS_REFUSED_V1] "w:<guid>:<port>" as host:port usable here, asking every pond this member is in
     *  (members: area -> its live Member). Exactly one pond may answer with an address -- reached here or not: two
     *  answering is Ambiguous, naming each; none (or the one not reached here) throws saying why. */
    fun resolve(ep: String, members: Map<String, Member>, forward: String): String {
        val c = ep.lastIndexOf(':')
        if (!ep.startsWith("w:") || c <= 2 || ep.substring(c + 1).toIntOrNull() == null) throw ProtocolError("'$ep' is not an identity endpoint (w:<guid>:<port>)")
        val id = ep.substring(0, c); val port = ep.substring(c + 1)
        val answered = ArrayList<Triple<String, String, String?>>(); val why = ArrayList<String>()   // (area, addr, here:port or null)
        for ((area, m) in members.entries.sortedBy { it.key }) {
            val r = m.resolve(listOf(id))[id] ?: Json.NONE
            val addr = r["addr"].str
            if (addr.isEmpty()) { why.add("$area: ${r["error"].str.ifEmpty { "no address" }}"); continue }
            answered.add(Triple(area, addr, (if (area == forward) addr else here(area, addr))?.let { "$it:$port" }))
        }
        if (answered.size > 1) throw Ambiguous("$id is ambiguous: " + answered.joinToString("; ") { "in ${it.first} as ${it.second} (" + (it.third?.let { h -> "here $h" } ?: "not reached here") + ")" } + " -- name the network")
        val one = answered.firstOrNull() ?: throw ProtocolError("$id has no address here (${why.joinToString("; ")})")
        return one.third ?: throw ProtocolError("$id is ${one.second} in ${one.first}, which is not among what this member reaches there")
    }

    /** addr in area's real space, as this member reaches it (the alias when aliased); null: not reached */
    @Synchronized fun here(area: String, addr: String): String? {
        val p = islands[area] ?: return null; val a = Pfx.ip4(addr) ?: return null
        if (p.routes.none { Pfx.parse(it).contains(a) }) return null
        val best = p.alias.entries.filter { Pfx.parse(it.value).contains(a) }.maxByOrNull { Pfx.parse(it.value).bits }
            ?: return addr
        val rn = Pfx.parse(best.value); val an = Pfx.parse(best.key)
        return Pfx.ip4(an.net + (a - rn.net))
    }
}

/** The forward pond's tunnel as fdf-tun's forward device: the Leaf drives it exactly as it drives GoBackend. */
class BridgeForwardDriver(private val bridge: Bridge) : TunnelDriver {
    @Volatile override var current: Hub? = null; private set
    override fun up(hub: Hub, addr: String) { bridge.setForward(hub, addr); current = hub }
    override fun down(why: String) { if (current == null) return; current = null; bridge.setForward(null, "") }
}

/** One participant pond's reach, re-decided on every region event of its session. */
class Reach(private val area: String, private val bridge: Bridge, private val myPub: () -> String,
            private val log: (String) -> Unit, private val refused: (String) -> Unit) {
    @Volatile var saidPending = ""

    fun onRegionChange(m: Member) {
        val (p, changed) = try {
            val world = m.world(); val cfg = m.netConfig()
            val resolved = if (cfg?.get("reach")?.str == "listed") ReachPlanner.listedIds(m.where()).let { if (it.isEmpty()) emptyMap() else m.resolve(it) } else null
            bridge.decide(area) { scope, old, taken ->
                ReachPlanner.plan(area, cfg, world, resolved, m.ns, myPub(), m.regionAddr.substringBefore('/'), scope, old, taken, bridge.ledger) }
        } catch (e: ReachPending) {
            val was = bridge.removeIsland(area, "not yet: ${e.message}")
            if (was || saidPending != e.message) { saidPending = e.message ?: ""; log("reach into $area: not yet: ${e.message}" + if (was) " (taken down until then)" else "") }
            return
        } catch (e: ReachRefused) {
            log("REACH REFUSED into $area: ${e.message} -- this session ends (a failed reach closes it)")
            refused(e.message ?: ""); m.leave()
            runCatching { bridge.removeIsland(area, "refused") }.onFailure { log("reach into $area: ${it.message}") }; return
        } catch (e: ReachNotNow) { log("reach into $area: could not plan it now (${e.message}): tried again on the next event"); return }
        saidPending = ""
        if (changed) log(p.describe())
    }

    fun ended(why: String) {
        try { if (bridge.removeIsland(area, "the session ended: $why")) log("reach into $area taken down: the session ended") }
        catch (e: Exception) { log("reach into $area: ${e.message}") }
    }
}
