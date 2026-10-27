// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// Identity.kt -- what this device is to its ponds, kept in app-private storage:
//   guid     made once (UUID); the member's identity key, ns = "w:" + guid ([MEMBER_IDENTITY_V1]); the same in every pond
//   keys     the WireGuard key pair, made once; announced (facts.pubkey) only in the forward pond, off the mesh
//   name     shown name (default: the device model)
//   ponds    [PONDS_V1] (PLAN-PONDS) every pond this device takes part in: area + its RAM manager (host:port); one session
//            each, held all the time
//   forward  the one pond whose network the device uses (placement, tunnel); the others are participant ponds
//   invite   [ADMITTED_BY_INVITE_V1] [RAM_IN_THE_TUNNEL_V1] the fdfinv1.<...> line `fdf-invite NAME --leaf` printed on the
//            region's machine: the region's area and RAM manager (inside its tunnel), the hub (key, endpoint), this
//            device's edge address and WireGuard key. With one, it IS the pond, the forward pond and the key: the hub
//            answers that key only, and the region is reached only through the hub channel it describes.
//   (no address here: on a FrogNet LAN the device's own address is its address, [ADDRESS_IS_THE_ONE_IT_HAS_V1]; off the
//   mesh the RAM manager gives the leaf its /32 with the announcement, [LEAF_ADDR_FROM_THE_REGION_V1]. Nobody types one.)
package com.fawcettinnovations.fdf.android

import android.content.Context
import android.os.Build
import com.fawcettinnovations.fdf.member.Hub
import com.fawcettinnovations.fdf.member.Json
import com.fawcettinnovations.fdf.member.Placement
import com.wireguard.crypto.Key
import com.wireguard.crypto.KeyPair
import java.util.UUID

class Pond(val area: String, val ram: String)

/** [ADMITTED_BY_INVITE_V1] an fdf-invite line, decoded (fdf-region-join's reading of it, field for field). */
class Invitation(val raw: String, val area: String, val siteKey: String, val ram: String, val hub: Hub,
                 val name: String, val edge: String, val kind: String, val privkey: String) {
    companion object {
        /** the line, or IllegalArgumentException saying what is wrong with it */
        fun parse(line: String): Invitation {
            val t = line.trim()
            require(t.startsWith("fdfinv1.")) { "not an invitation (it starts fdfinv1.; fdf-invite NAME --leaf on the region's machine prints one)" }
            val j = try { Json.parse(String(java.util.Base64.getUrlDecoder().decode(t.substring(8)), Charsets.UTF_8)) }
                    catch (e: Exception) { throw IllegalArgumentException("the invitation does not decode (${e.message}): copy the whole line fdf-invite printed") }
            val h = j["hub"]; val m = j["member"]
            for ((k, v) in listOf("area" to j["area"], "ram" to j["ram"], "hub.pubkey" to h["pubkey"], "hub.endpoint" to h["endpoint"],
                                  "member.name" to m["name"], "member.edge" to m["edge"], "member.privkey" to m["privkey"]))
                require(v.str.isNotEmpty()) { "the invitation has no $k" }
            try { Key.fromBase64(m["privkey"].str); Key.fromBase64(h["pubkey"].str) } catch (e: Exception) { throw IllegalArgumentException("the invitation's keys are not WireGuard keys (${e.message})") }
            val area = j["area"].str
            return Invitation(t, area, j["site_key"].str, j["ram"].str,
                Hub(h["name"].str.ifEmpty { "hub" }, h["pubkey"].str, h["endpoint"].str, h["addr"].str.ifEmpty { "10.253.253.254" }, area),
                m["name"].str, m["edge"].str, m["kind"].str.ifEmpty { "node" }, m["privkey"].str)
        }
    }
}

class Identity(ctx: Context) {
    private val p = ctx.getSharedPreferences("fdf-identity", Context.MODE_PRIVATE)
    val guid: String = p.getString("guid", null) ?: ("android-" + UUID.randomUUID()).also { p.edit().putString("guid", it).apply() }
    /** [ADMITTED_BY_INVITE_V1] the invitation this device joins with, or null (none yet, or not readable: said once) */
    val invite: Invitation? get() = p.getString("invite", null)?.let { try { Invitation.parse(it) } catch (e: IllegalArgumentException) { null } }
    /** keeps the line if it reads; else IllegalArgumentException with why (nothing kept) */
    fun setInvite(line: String) { val i = Invitation.parse(line); p.edit().putString("invite", i.raw).apply() }
    /** the WireGuard key: the invitation's (the only one the hub answers); without one, the key made once here */
    val keys: KeyPair get() = invite?.let { KeyPair(Key.fromBase64(it.privkey)) } ?: p.getString("wg_private", null)?.let { KeyPair(Key.fromBase64(it)) }
        ?: KeyPair().also { p.edit().putString("wg_private", it.privateKey.toBase64()).apply() }
    var name: String
        get() = p.getString("name", null) ?: Build.MODEL
        set(v) = p.edit().putString("name", v).apply()

    /** "area|host:port" per line; the first install's default is the droplet's pond. */
    var ponds: List<Pond>
        get() = invite?.let { listOf(Pond(it.area, it.ram)) }                // [RAM_IN_THE_TUNNEL_V1] the invited region, at its tunnel address
            ?: (p.getString("ponds", null) ?: "frognet|165.232.153.144:51888").lines().filter { '|' in it }
            .map { Pond(it.substringBefore('|'), it.substringAfter('|')) }
        set(v) = p.edit().putString("ponds", v.joinToString("\n") { it.area + "|" + it.ram }).apply()
    // [TELEMETRY_IS_A_TUPLE_V1] what this device reports ((pond, telem, w:<guid>)); [SETTINGS_ARE_TUPLES_V1] sets them
    var telemOn: Boolean
        get() = p.getBoolean("telem_on", true)
        set(v) = p.edit().putBoolean("telem_on", v).apply()
    var telemGps: Boolean
        get() = p.getBoolean("telem_gps", true)
        set(v) = p.edit().putBoolean("telem_gps", v).apply()
    var telemPeriodS: Int
        get() = p.getInt("telem_period_s", 30).coerceIn(5, 3600)
        set(v) = p.edit().putInt("telem_period_s", v.coerceIn(5, 3600)).apply()
    var forward: String
        get() = p.getString("forward", null)?.takeIf { f -> ponds.any { it.area == f } } ?: ponds.firstOrNull()?.area ?: ""
        set(v) = p.edit().putString("forward", v).apply()

    /** The forward pond's facts (TRANSITION-ANDROID §4; fdf_graph.py and fdf-hub read role, name, guid, addr, pubkey).
     *  On the mesh: no addr, no pubkey -- the hub gives no peer, the graph no /32; the member's /24 routes it. `lan`
     *  says where it is (opaque to the graph and the hub). Off the mesh: its WireGuard key; the region adds the /32. */
    fun facts(placement: Placement): String {
        val head = "{\"role\":\"leaf\",\"name\":" + Json.quote(name) + ",\"guid\":" + Json.quote(guid)
        return when (val pl = placement) {
            is Placement.OnMesh -> head + ",\"lan\":{\"ip\":" + Json.quote(pl.ip) + ",\"subnet\":" + Json.quote(pl.subnet) +
                ",\"served_by\":" + Json.quote(pl.servedBy) + "}}"
            // [ADMITTED_BY_INVITE_V1] invited: the edge the invitation gave is its /32 (fdf_graph's leaf row, so every node
            // routes it through the hub); the hub peers this key with that edge from its admitted list
            Placement.OffMesh -> head + ",\"pubkey\":" + Json.quote(keys.publicKey.toBase64()) +
                (invite?.let { ",\"addr\":" + Json.quote(it.edge + "/32") + ",\"pond\":" + Json.quote(it.area) } ?: "") + "}"
        }
    }
    /** [FORWARD_IS_A_FACT_V1] [REACH_IS_A_LEAF_HERE_V1] a participant pond's facts: presence, and reach:true with this
     *  device's key -- the pond's own config decides whether it is reached (its hub peers it, its region gives it an
     *  address) or refused. */
    fun participantFacts(): String = "{\"role\":\"participant\",\"name\":" + Json.quote(name) + ",\"guid\":" + Json.quote(guid) +
        ",\"pubkey\":" + Json.quote(keys.publicKey.toBase64()) + ",\"reach\":true}"
}
