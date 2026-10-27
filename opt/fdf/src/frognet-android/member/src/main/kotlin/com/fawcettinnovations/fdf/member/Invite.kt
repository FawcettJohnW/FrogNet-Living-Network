// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// Invite.kt -- [LEAF_BY_INVITE_V1] the line fdf-invite NAME --leaf prints (fdfinv1.<base64url JSON>), decoded exactly as
// fdf-region-join decodes it for a node. Everything a leaf needs to reach its region is in it, so nothing is asked of
// the region before the tunnel exists ([RAM_IN_THE_TUNNEL_V1]: the region listens only inside the hub's tunnel):
//   member.privkey  this device's WireGuard key (the hub has admitted its public half; no other key is answered)
//   member.edge     this device's tunnel address (a /16 on the interface, region.py HUB_MASK; the hub routes its /32)
//   hub             the one peer: pubkey, endpoint (udp ip:port), addr (its tunnel address, where the RAM is)
//   ram             host:port of the region's RAM manager, inside the tunnel
//   area, site_key  the region
// The connection is the trust: nothing else is presented, and no credential is kept beside it.
package com.fawcettinnovations.fdf.member

class Invite(val line: String, val area: String, val siteKey: String, val ramHost: String, val ramPort: Int,
             val hub: Hub, val name: String, val edge: String, val kind: String, val privkey: String) {
    companion object {
        const val PREFIX = "fdfinv1."
        const val EDGE_MASK = 16                                  // region.py HUB_MASK

        fun parse(text: String): Invite {
            val line = text.trim()
            require(line.startsWith(PREFIX)) { "not an invitation (it starts $PREFIX; fdf-invite NAME --leaf on the region's machine prints one)" }
            val tok = line.substring(PREFIX.length)
            val j = try { Json.parse(String(java.util.Base64.getUrlDecoder().decode(tok + "=".repeat((4 - tok.length % 4) % 4)), Charsets.UTF_8)) }
                    catch (e: Exception) { throw IllegalArgumentException("the invitation does not decode (${e.message}): copy the whole line fdf-invite printed") }
            val h = j["hub"]; val m = j["member"]; val ram = j["ram"].str
            val miss = listOf("area" to j["area"].str, "site_key" to j["site_key"].str, "ram" to ram,
                              "hub.name" to h["name"].str, "hub.pubkey" to h["pubkey"].str, "hub.endpoint" to h["endpoint"].str, "hub.addr" to h["addr"].str,
                              "member.name" to m["name"].str, "member.edge" to m["edge"].str, "member.privkey" to m["privkey"].str)
                .filter { it.second.isEmpty() }.map { it.first }
            require(miss.isEmpty()) { "the invitation lacks ${miss.joinToString(", ")}" }
            val c = ram.lastIndexOf(':'); require(c > 0) { "the invitation's ram is not host:port ($ram)" }
            return Invite(line, j["area"].str, j["site_key"].str, ram.substring(0, c), ram.substring(c + 1).toInt(),
                          Hub(h["name"].str, h["pubkey"].str, h["endpoint"].str, h["addr"].str, j["area"].str),
                          m["name"].str, m["edge"].str, m["kind"].str.ifEmpty { "leaf" }, m["privkey"].str)
        }
    }
    /** The interface address: the edge, in the hub's /16. */
    val address: String get() = "$edge/$EDGE_MASK"
    /** What this leaf announces: who it is and its key (fdf-hub admits a participant only by a key it invited). */
    fun facts(pubkey: String): String =
        "{\"role\":\"leaf\",\"name\":" + Json.quote(name) + ",\"pubkey\":" + Json.quote(pubkey) + ",\"edge\":" + Json.quote(edge) + "}"
    override fun toString() = "invitation: area $area, me $name ($kind, edge $edge), hub ${hub.name} at ${hub.endpoint} (key ${hub.pubkey.take(12)}), region at $ramHost:$ramPort inside the tunnel"
}

/** The tunnel an invitation calls for, brought up before anything is asked of the region. The app drives GoBackend,
 *  the JVM runner `wg`; both are given the whole invitation (key, address, the one peer). */
interface InviteTunnel {
    fun up(inv: Invite)
    fun down(why: String)
}
