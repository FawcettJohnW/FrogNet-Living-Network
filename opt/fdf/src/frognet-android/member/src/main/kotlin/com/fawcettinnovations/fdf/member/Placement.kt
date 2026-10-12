// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// Placement.kt -- [ADDRESS_IS_THE_ONE_IT_HAS_V1] (John 2026-10-10: "The machine already has an ip") where this device
// sits, from the address it already holds and the region's facts, decided before each announcement:
//   OnMesh   one of my IPv4s is inside a /24 a live member serves (its `own` .1 row, or facts.subnet): I am a host on
//            that member's LAN, already routed everywhere by that /24 (fdf_graph routes served /24s). Nothing to
//            allocate, no tunnel: a tunnel would put my address behind a second path (rule 3: nothing behind two peers).
//   OffMesh  none of my addresses is on the mesh: the address I have is not routable in the region's 10/8; a leaf /32
//            and the hub tunnel are what put me on it.
package com.fawcettinnovations.fdf.member

sealed class Placement {
    data class OnMesh(val ip: String, val subnet: String, val servedBy: String) : Placement()
    object OffMesh : Placement()
}

object Placer {
    private fun slash24(ip: String) = ip.substringBeforeLast('.') + ".0/24"
    private fun overlay(ip: String) = ip.split(".").let { it.size == 4 && it[0] == "10" && (it[1] == "253" || it[1] == "254") }

    /** /24 -> serving member name, from the live members other than `myNs` (fdf_graph's `own` .1 rows / facts.subnet). */
    fun served(participants: Json, myNs: String): Map<String, String> {
        val out = LinkedHashMap<String, String>()
        for (p in participants["participants"].a) {
            if (!p["alive"].bool || p["ns"].str == myNs) continue
            val f = p["facts"]; if (f["role"].str == "hub") continue
            val name = p["name"].str
            for (r in f["addrs"].a) {
                val ip = r["ip"].str
                if (r["kind"].str == "own" && ip.startsWith("10.") && ip.endsWith(".1") && !overlay(ip)) out.putIfAbsent(slash24(ip), name)
            }
            val sub = f["subnet"].str
            if (sub.endsWith("/24") && sub.startsWith("10.") && !overlay(sub)) out.putIfAbsent(sub.substringBefore('/').substringBeforeLast('.') + ".0/24", name)
        }
        return out
    }

    fun place(myIps: List<String>, participants: Json, myNs: String): Placement {
        val served = served(participants, myNs)
        for (ip in myIps) {
            if (!ip.startsWith("10.") || overlay(ip) || ip.endsWith(".1")) continue
            served[slash24(ip)]?.let { return Placement.OnMesh(ip, slash24(ip), it) }
        }
        return Placement.OffMesh
    }
}
