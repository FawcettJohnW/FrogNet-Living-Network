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
//   (no address here: on a FrogNet LAN the device's own address is its address, [ADDRESS_IS_THE_ONE_IT_HAS_V1]; off the
//   mesh the RAM manager gives the leaf its /32 with the announcement, [LEAF_ADDR_FROM_THE_REGION_V1]. Nobody types one.)
package com.fawcettinnovations.fdf.android

import android.content.Context
import android.os.Build
import com.fawcettinnovations.fdf.member.Invite
import com.fawcettinnovations.fdf.member.Json
import com.fawcettinnovations.fdf.member.Placement
import com.wireguard.crypto.Key
import com.wireguard.crypto.KeyPair
import java.util.UUID

class Pond(val area: String, val ram: String)

class Identity(ctx: Context) {
    private val p = ctx.getSharedPreferences("fdf-identity", Context.MODE_PRIVATE)
    val guid: String = p.getString("guid", null) ?: ("android-" + UUID.randomUUID()).also { p.edit().putString("guid", it).apply() }
    /** [LEAF_BY_INVITE_V1] the line fdf-invite NAME --leaf (or fdf-identity NAME --leaf) printed, pasted once and kept here
     *  (app-private storage). With one, this device's key, name, address, hub and region are the invitation's. */
    val invite: Invite? = p.getString("invite", null)?.let { runCatching { Invite.parse(it) }.getOrNull() }
    /** Kept only if it decodes; the service starts again to use it (its key is the tunnel's). Throws with the reason. */
    fun keepInvite(line: String) { val inv = Invite.parse(line); p.edit().putString("invite", inv.line).putString("forward", inv.area).apply() }
    val keys: KeyPair = invite?.let { KeyPair(Key.fromBase64(it.privkey)) } ?: p.getString("wg_private", null)?.let { KeyPair(Key.fromBase64(it)) }
        ?: KeyPair().also { p.edit().putString("wg_private", it.privateKey.toBase64()).apply() }
    var name: String
        get() = invite?.name ?: p.getString("name", null) ?: Build.MODEL
        set(v) = p.edit().putString("name", v).apply()

    /** "area|host:port" per line. With an invitation its region is a pond (first); without one there is none until one
     *  is added (the region listens only inside its tunnel: a host:port typed here reaches only an older region). */
    var ponds: List<Pond>
        get() = (listOfNotNull(invite?.let { Pond(it.area, "${it.ramHost}:${it.ramPort}") }) +
                 (p.getString("ponds", null) ?: "").lines().filter { '|' in it }.map { Pond(it.substringBefore('|'), it.substringAfter('|')) })
                .distinctBy { it.area }
        set(v) = p.edit().putString("ponds", v.filter { it.area != invite?.area }.joinToString("\n") { it.area + "|" + it.ram }).apply()
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
            Placement.OffMesh -> head + ",\"pubkey\":" + Json.quote(keys.publicKey.toBase64()) + "}"
        }
    }
    /** [FORWARD_IS_A_FACT_V1] [REACH_IS_A_LEAF_HERE_V1] a participant pond's facts: presence, and reach:true with this
     *  device's key -- the pond's own config decides whether it is reached (its hub peers it, its region gives it an
     *  address) or refused. */
    fun participantFacts(): String = "{\"role\":\"participant\",\"name\":" + Json.quote(name) + ",\"guid\":" + Json.quote(guid) +
        ",\"pubkey\":" + Json.quote(keys.publicKey.toBase64()) + ",\"reach\":true}"
}
