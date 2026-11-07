// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// Member.kt -- [FDF_MEMBER_KT_V1] an FD&F member, as rinab/src/fdf_client.cpp main() is one, step for step:
//
//   node      {"op":"node","ns":ns,"devs":""}            this session is a live machine (its answer carries `after`)
//   announce  name, ips, cap, facts                      EEXIST -> said with the holder, then WAIT for a `p` and try
//                                                        again ([JOIN_REFUSED_WAITS_V1]); any other refusal ends the run
//   version   logged: "build X; region at H is build Y"
//   maps      facts.maps signed with the site key, own:true, registered at the session's start and on every change of the facts
//             ([MAP_FROM_FACTS_V1], [MAPS_ASSERTED_V1]); a refusal is said with what it means and the session is kept
//   hold      a held wait on agent:<ns>; returns only when the session ends ([SESSION_IS_LIVENESS_V1])
//
// What differs from the C++ client, and why: facts are not a file watched by inotify. The app owns them and calls
// reannounce() when they change (TRANSITION-ANDROID §6.1). A failed re-announce or assert ends the session, as
// fdf-client's _exit(1) does, so a fresh session re-announces and re-registers -- the owner's job ([NO_FALLBACK_V1]).
package com.fawcettinnovations.fdf.member

import javax.crypto.Mac
import javax.crypto.spec.SecretKeySpec

const val API = "/FawcettInnovations.RINA.ram_interface.php"
const val MEMBER_BUILD = "fdf-kt-2026-11-04-a26"            // [FDF_VERSION_V1] on every log line that says what runs

class MemberConfig(
    val ramHost: String, val ramPort: Int,
    val name: String,                                         // shown name (facts.name)
    val guid: String,                                         // stable machine id; ns = "w:" + guid ([MEMBER_IDENTITY_V1])
    val ips: () -> List<String>,                              // this machine's own IPv4s (getifaddrs on Linux)
    val capability: () -> String,                             // a JSON object as text: cores, mem_total_kb, os, client
    val facts: () -> String,                                  // the facts JSON object as text ("" = none)
    val site: String = "", val siteKey: String = "",          // RINA_AREA / RINA_SITE_KEY: only needed if facts.maps
    val assertSec: Int = 30,
    val tuner: SocketTuner,
    val log: (String) -> Unit,
    val prepare: (Member) -> Unit = {},                      // after `node`, before the announcement: e.g. Placer from participants
    val onJoined: (Member) -> Unit = {},                     // after node+announce+version: e.g. the leaf's tunnel from Hubs
    val onRegionChange: ((Member) -> Unit)? = null,           // [REGION_EVENTS_ON_MY_SESSION_V1] set: called on every `p`
    val onAttention: ((Member, Json) -> Unit)? = null,        // [ATTENTION_IS_A_TUPLE_V1] set: each (pond, attention, *) cell
)

class Member(private val c: MemberConfig) {
    val ns = "w:" + (if (c.guid.isNotEmpty()) c.guid else c.name)
    @Volatile private var s: Session? = null
    @Volatile var joinedAtMs = 0L; private set
    /** [LEAF_ADDR_FROM_THE_REGION_V1] the /32 the region gave this leaf with its announcement ("" = none: on a LAN, or not a leaf). */
    @Volatile var regionAddr = ""; private set
    private fun took(a: Json) {
        val now = a["addr"].str
        if (now != regionAddr) c.log(if (now.isEmpty()) "the region holds no leaf address for this session" else "the region gave this leaf $now")
        regionAddr = now
    }

    private fun rina(body: String, timeoutS: Double = 15.0) = s!!.callJson("$API?op=rina", body, timeoutS)
    private fun rinaText(op: String, body: String, timeoutS: Double) = s!!.call("POST", "$API?op=$op", body, timeoutS).body

    private fun announceBody(): String {
        val facts = c.facts()
        return "{\"op\":\"announce\",\"ns\":" + Json.quote(ns) + ",\"name\":" + Json.quote(c.name) +
            ",\"ips\":[" + c.ips().joinToString(",") { Json.quote(it) } + "],\"cap\":" + Json.quote(c.capability()) +
            (if (facts.isEmpty()) "" else ",\"facts\":" + Json.quote(facts)) + "}"
    }

    /** [MEMBER_IS_A_TUPLE_V1] this member's own truth, (pond, member, <ns>), on this session with own:true: there while the
     *  member is, gone with the session. Written after its mappings, so a reader of the members finds them there. */
    private fun writeMember() {
        val facts = c.facts()
        var f = if (facts.isEmpty()) "null" else try { Json.parse(facts); facts } catch (_: Exception) { "null" }
        // [LEAF_BY_INVITE_V1] a leaf's address is its edge, said in its own facts (the readers place it by facts.addr)
        if (f != "null" && regionAddr.isNotEmpty() && Json.parse(f)["addr"].type == Json.Type.NULL) {
            f = "{\"addr\":" + Json.quote(regionAddr) + (if (f.substring(1).trimStart().startsWith("}")) "" else ",") + f.substring(1)
        }
        val bag = "{\"ns\":" + Json.quote(ns) + ",\"name\":" + Json.quote(c.name) +
            ",\"ips\":[" + c.ips().joinToString(",") { Json.quote(it) } + "],\"cap\":" + Json.quote(c.capability()) +
            ",\"facts\":" + f + ",\"maps\":" + signedMaps() + ",\"build\":" + Json.quote(MEMBER_BUILD) + "}"
        // [WRITTEN_WHEN_IT_CHANGES_V1] the same truth on the same session is not written again
        if (s === lastSession && bag == lastBag) return
        write("pond", "member", ns, bag, own = true); lastSession = s; lastBag = bag
    }
    @Volatile private var lastSession: Session? = null
    @Volatile private var lastBag = ""
    private val signedAt = java.util.concurrent.ConcurrentHashMap<String, Pair<String, String>>()   // eid -> (rloc, ts)

    /** [ONE_OWNER_V1] this member's mappings, its own: signed with the site key (eid|rloc|ttl|ts) and written in its own
     *  (pond, member) cell; the region verifies them when they are read and writes nothing for them */
    private fun signedMaps(): String {
        val f = c.facts(); if (f.isEmpty()) return "[]"
        val maps = try { Json.parse(f)["maps"].a } catch (_: ProtocolError) { return "[]" }
        if (maps.isEmpty()) return "[]"
        if (c.site.isEmpty() || c.siteKey.isEmpty()) { c.log("facts.maps present but site / site key not set: no mapping said"); return "[]" }
        val now = System.currentTimeMillis(); val nowTs = "%d.%09d".format(now / 1000, (now % 1000) * 1_000_000); val ttl = "86400"
        return maps.filter { it["eid"].str.isNotEmpty() }.joinToString(",", "[", "]") { m ->
            val eid = m["eid"].str; val rloc = m["rlocs"].str
            // a mapping keeps its signature while it is the same (re-signed at half its ttl): the cell is the same text
            val was = signedAt[eid]
            val ts = if (was != null && was.first == rloc && now / 1000.0 - was.second.toDouble() < 43200) was.second
                     else nowTs.also { signedAt[eid] = rloc to it }
            "{\"eid\":" + Json.quote(eid) + ",\"rloc\":" + Json.quote(rloc) + ",\"site\":" + Json.quote(c.site) + ",\"ttl\":\"$ttl\",\"ts\":\"$ts\",\"auth\":" +
                Json.quote(hmacHex(c.siteKey, "$eid|$rloc|$ttl|$ts")) + "}"
        }
    }

    /** the largest row id in a read's answer, or `at` */
    private fun maxId(j: Json, at: Long = 0L): Long {
        var m = at
        for (cell in (if (j["rows"].type == Json.Type.ARR) j["rows"] else j["cells"]).a) cell["id"].num?.toLong()?.let { if (it > m) m = it }
        return m
    }

    private fun maxEvent(t: String, at: Long): Long {          // "E\n" then "<n>\t<event>\n" per event
        var m = at; var i = 2
        while (i < t.length) {
            val tab = t.indexOf('\t', i); val nl = t.indexOf('\n', i)
            if (tab < 0 || nl < 0) break
            t.substring(i, tab).toLongOrNull()?.let { if (it > m) m = it }
            i = nl + 1
        }
        return m
    }

    /** One membership, start to end. Returns why the session ended (the owner makes a new one: a new member). */
    fun runOnce(): String {
        val sess = try { Session(c.ramHost, c.ramPort, c.tuner, c.log) } catch (e: Exception) { return "connect: ${e.message}" }
        s = sess
        try {
            rina("{\"op\":\"node\",\"ns\":" + Json.quote(ns) + ",\"devs\":\"\"}")                                             // alive: this session
            // [WATCH_BEFORE_READ_V1] the watch goes in BEFORE the first read of the region (prepare, onJoined): a change made
            // between a read and a later watch would never be delivered (seen: the hub's `peers` re-announcement fell in that
            // gap and the leaf's tunnel waited for an event that had already happened; the r17 daemon fix, here)
            // [MEMBER_IS_A_TUPLE_V1] the region changing = the members' family changing: its held read starts from where
            // this read ends, BEFORE the first look at the region (prepare)
            val membersAt = maxId(Json.parse(sess.call("GET", "$API?op=read&service=pond&variable=member", "", 15.0).body))
            try { c.prepare(this) } catch (e: Exception) { c.log("prepare step failed: ${e.message}") }
            var jat = 0L
            while (true) {                                      // [JOIN_REFUSED_WAITS_V1]
                val a = Json.parse(s!!.call("POST", "$API?op=rina", announceBody()).body)
                if (a["ok"].bool) { took(a); break }
                if (a["error"].str != "EEXIST") return "the announcement was refused: ${a["error"].str} ${a["why"].str}".also { c.log(it); sess.close() }
                c.log("JOIN REFUSED by the region: ${a["why"].str}. Not a member until that changes; waiting for the members to change (a held read, no retry loop).")
                if (jat == 0L) jat = membersAt
                jat = maxId(Json.parse(sess.call("GET", "$API?op=read&service=pond&variable=member&after=$jat&wait_s=600", "", 615.0).body), jat)
                c.log("the members changed; trying the announcement again")
            }
            val v = rina("{\"op\":\"version\"}")
            joinedAtMs = System.currentTimeMillis()
            c.log("build $MEMBER_BUILD; region at ${c.ramHost}:${c.ramPort} is build ${v["server"].str}")
            c.log("${c.name} joined the region at ${c.ramHost}:${c.ramPort} as $ns, announced ${c.ips()}" + (if (c.facts().isEmpty()) "" else " with facts"))
            writeMember()                                       // [MEMBER_IS_A_TUPLE_V1]
            try { c.onJoined(this) } catch (e: Exception) { c.log("on-joined step failed: ${e.message} (re-run on the next session)") }
            // [ATTENTION_IS_A_TUPLE_V1] a held read on (pond, attention) on this same session: each notice the moment it is
            // written (current ones first). Another member's only: my own (instance = my ns) are skipped. A failed read
            // ends the session, as a failed assert does.
            c.onAttention?.let { cb ->
                Thread({
                    var after = -1L
                    try {
                        while (true) {
                            val q = "$API?op=read&service=pond&variable=attention" + (if (after >= 0) "&after=$after&wait_s=600" else "")
                            val j = Json.parse(sess.call("GET", q, "", 615.0).body)
                            for (cell in (if (j["rows"].type == Json.Type.ARR) j["rows"] else j["cells"]).a) {
                                val id = cell["id"].num?.toLong() ?: continue
                                if (id > after) after = id
                                if (cell["instance"].str != ns) cb(this, cell)
                            }
                            if (after < 0) after = 0
                        }
                    } catch (e: Exception) { if (sess.dead == null) { c.log("attention read failed: ${e.message} -- ending this session for a fresh one"); sess.close() } }
                }, "fdf-attention").apply { isDaemon = true; start() }
            }
            // [EVENTS_NOT_TIMERS_V1] no asserter (was every assertSec: the region read and the mappings registered again --
            // a timer standing in for a defect; a mapping goes with the session that registered it).
            // [MEMBER_IS_A_TUPLE_V1] the region changing is the members' family changing: a held read on it, on this
            // session; each change re-runs onRegionChange. (Was the region's `p` notices pushed into this member's wait.)
            c.onRegionChange?.let { cb ->
                Thread({
                    var at = membersAt
                    try {
                        while (true) {
                            val j = Json.parse(sess.call("GET", "$API?op=read&service=pond&variable=member&after=$at&wait_s=600", "", 615.0).body)
                            val n = maxId(j, at)
                            if (n > at) { at = n; try { cb(this) } catch (e: Exception) { c.log("region-change step failed: ${e.message} (re-run on the next change)") } }
                        }
                    } catch (e: Exception) { if (sess.dead == null) { c.log("members' read failed: ${e.message} -- ending this session for a fresh one"); sess.close() } }
                }, "fdf-members").apply { isDaemon = true; start() }
            }
            // [MEMBER_IS_A_TUPLE_V1] the session's own hold is its end: nothing is pushed to this machine (was: a wait on its
            // agent input space, where the region sent a notice on every change); the members' family is read held above
            throw RuntimeException(sess.awaitEnd())
        } catch (e: Exception) {
            val why = sess.dead ?: e.message ?: e.javaClass.simpleName
            c.log("the session to ${c.ramHost}:${c.ramPort} ended: $why"); sess.close(); return why
        }
    }

    /** The app's facts changed: re-announce on this session (fdf-client's inotify path). Failure ends the session. */
    fun reannounce() {
        val sess = s ?: return
        try {
            val a = Json.parse(sess.call("POST", "$API?op=rina", announceBody()).body)
            if (a["ok"].bool) { c.log("facts changed, re-announced"); took(a) } else c.log("re-announcement refused: ${a["error"].str} ${a["why"].str}")
            writeMember()                                       // [MEMBER_IS_A_TUPLE_V1]
        } catch (e: Exception) { c.log("re-announce failed: ${e.message} -- ending this session so a fresh one re-announces and re-registers"); sess.close() }
    }

    /** the members (their own cells, as `participants`) and the region's mappings; the answer has "participants" and
     *  "mappings" as the region's `world` did. */
    fun world(): Json {
        val m = s!!.callJson("$API?op=rina", "{\"op\":\"map_list\",\"site\":" + Json.quote(c.site) + "}")
        return Json.parse("{\"participants\":" + Hubs.membersText(s!!) + ",\"mappings\":" + m["mappings"].toString() + "}")
    }

    /** [MEMBER_IS_A_TUPLE_V1] every member there now (its own (pond, member) cell; present is alive), read on this
     *  member's session, shaped as the region's `participants` answer was. */
    fun participants(): Json = Json.parse("{\"participants\":" + Hubs.membersText(s!!) + "}")

    /** [SERVICE_WHERE_IS_A_TUPLE_V1] where this pond's members say they run `service`: each (pond, where, <ns>) cell that
     *  names it, as (the member's name, the service's bag, e.g. {"ram":"w:<guid>:8800","media":"w:<guid>:8994"} --
     *  identities: `endpoint` gives each as host:port in this pond).
     *  Read now on this member's session. A cell exists only while its member's session is up (the region sweeps it),
     *  so every answer is a live member's. Empty when nobody in the pond runs it -- nothing is substituted. */
    fun services(service: String): List<Pair<String, Json>> {
        val j = Json.parse(s!!.call("GET", "$API?op=read&service=pond&variable=where", "", 15.0).body)
        return (if (j["rows"].type == Json.Type.ARR) j["rows"] else j["cells"]).a.mapNotNull { cell ->
            val b = cell["bag"][service]
            if (b.type == Json.Type.OBJ) (cell["bag"]["from"].str.ifEmpty { cell["instance"].str } to b) else null }
    }

    /** [IDENTITY_IN_TUPLES_V1] the addresses identities have IN THIS POND (the region's `resolve`): a node's own .1, a
     *  leaf's given /32. An identity with no address here (not alive, or a participant) maps to its {"error","why"}. */
    fun resolve(ids: Collection<String>): Map<String, Json> {
        val r = s!!.callJson("$API?op=rina", "{\"op\":\"resolve\",\"ids\":[" + ids.joinToString(",") { Json.quote(it) } + "]}")
        return ids.associateWith { r["resolved"][it] }
    }

    /** [IDENTITY_IN_TUPLES_V1] one endpoint as a where cell names it, "w:<guid>:<port>", as host:port in this pond. Throws,
     *  saying why, when the value is not an identity (a cell written before identities: an address means one machine
     *  only inside one scope, so it is not taken) or the identity has no address here. Nothing is substituted. */
    fun endpoint(v: String): String {
        val c = v.lastIndexOf(':')
        if (!v.startsWith("w:") || c <= 2 || v.substring(c + 1).toIntOrNull() == null)
            throw ProtocolError("'$v' is not an identity endpoint (w:<guid>:<port>): the cell was written by an fdf-where older than IDENTITY_IN_TUPLES_V1")
        val id = v.substring(0, c); val a = resolve(listOf(id))[id]!!
        if (a["addr"].str.isEmpty()) throw ProtocolError("$id has no address in this pond: ${a["error"].str} ${a["why"].str}".trim())
        return a["addr"].str + ":" + v.substring(c + 1)
    }

    /** [NET_CONFIG_IS_A_TUPLE_V1] this pond's (pond, config, net) bag, as its RAM manager publishes it; null: none. */
    fun netConfig(): Json? {
        val j = Json.parse(s!!.call("GET", "$API?op=read&service=pond&variable=config&instance=net", "", 15.0).body)
        val cells = (if (j["rows"].type == Json.Type.ARR) j["rows"] else j["cells"]).a
        return cells.firstOrNull()?.get("bag")?.takeIf { it.type == Json.Type.OBJ }
    }

    /** every (pond, where, *) cell, as read */
    fun where(): Json = Json.parse(s!!.call("GET", "$API?op=read&service=pond&variable=where", "", 15.0).body)

    /** The region's hubs, read on this member's session (the leaf's tunnel peer). Empty when not joined. */
    fun hubs(area: String = c.site): List<Hub> = s?.let { Hubs.read(it, area) } ?: emptyList()

    /** Leave the region now: the session closes, every member sees one `p` (13 ms measured, netns). */
    fun leave() { s?.close() }

    /** [MEMBER_WRITES_TUPLES_V1] a tuple in this pond's memory, written on this member's own session. own: it is this
     *  session's, so it goes when the member does ([OWNED_CELL_GOES_WITH_THE_SESSION_V1]: telemetry). permanent: filed,
     *  it outlives the session and the RAM manager's restarts ([PERMANENT_CELL_V1]: settings). The id, or -1 with no
     *  session (said by the caller's log, not here). */
    fun write(service: String, variable: String, instance: String, bagJson: String, own: Boolean = false, permanent: Boolean = false): Long {
        val sess = s ?: return -1
        val body = "{\"service\":" + Json.quote(service) + ",\"variable\":" + Json.quote(variable) + ",\"instance\":" + Json.quote(instance) +
            ",\"bag\":" + bagJson + (if (own) ",\"own\":true" else "") + (if (permanent) ",\"permanent\":true" else "") + "}"
        return Json.parse(sess.call("POST", "$API?op=write", body).body)["id"].num?.toLong() ?: -1
    }
    /** the cells of (service, variable[, instance]); after/waitS: a held read ([HELD_READ_V1]) -- returns when one is written */
    fun read(service: String, variable: String, instance: String = "", after: Long = -1, waitS: Double = 0.0): Json {
        val sess = s ?: throw ProtocolError("no session")
        val q = "$API?op=read&service=" + java.net.URLEncoder.encode(service, "UTF-8") + "&variable=" + java.net.URLEncoder.encode(variable, "UTF-8") +
            (if (instance.isEmpty()) "" else "&instance=" + java.net.URLEncoder.encode(instance, "UTF-8")) +
            (if (after >= 0) "&after=$after&wait_s=$waitS" else "")
        return Json.parse(sess.call("GET", q, "", 15.0 + waitS).body)
    }

    private fun hmacHex(key: String, msg: String): String {
        val m = Mac.getInstance("HmacSHA256"); m.init(SecretKeySpec(key.toByteArray(), "HmacSHA256"))
        return m.doFinal(msg.toByteArray()).joinToString("") { "%02x".format(it) }
    }

}
