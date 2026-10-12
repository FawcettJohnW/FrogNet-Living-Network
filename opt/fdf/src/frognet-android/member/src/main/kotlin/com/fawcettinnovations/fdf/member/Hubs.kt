// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// Hubs.kt -- [LEAF_TUNNEL_FROM_REGION_V1] the leaf's WireGuard peer comes from the region, not from a config file:
// every hub is a member whose facts are {"role":"hub","name","pubkey","endpoint":"ip:port","addr":"10.253.253.254",
// "area"} (fdf-hub writes them, its fdf-client announces them). Chosen exactly as region.py _hubs chooses: alive,
// role hub, pubkey and endpoint present, the area matching when one is given; sorted by name.
package com.fawcettinnovations.fdf.member

class Hub(val name: String, val pubkey: String, val endpoint: String, val addr: String, val area: String,
          val peers: Set<String> = emptySet())   // [HUB_SAYS_ITS_PEERS_V1] the keys this hub holds, as it announces them

object Hubs {
    const val LEAF_ALLOWED = "10.0.0.0/8"                       // region.py ALLOWED: the whole mesh through the hub
    const val PERSISTENT_KEEPALIVE = 25                          // the member initiates and keeps NAT open (DEPLOY §3)

    fun from(participants: Json, area: String = ""): List<Hub> =
        participants["participants"].a.mapNotNull { p ->
            val f = p["facts"]
            if (!p["alive"].bool || f["role"].str != "hub" || f["pubkey"].str.isEmpty() || f["endpoint"].str.isEmpty()) return@mapNotNull null
            if (area.isNotEmpty() && f["area"].str.isNotEmpty() && f["area"].str != area) return@mapNotNull null
            Hub(f["name"].str, f["pubkey"].str, f["endpoint"].str, f["addr"].str.ifEmpty { "10.253.253.254" }, f["area"].str,
                f["peers"].a.map { it.str }.toSet())
        }.sortedBy { it.name }

    fun read(s: Session, area: String = ""): List<Hub> = from(s.callJson("$API?op=rina", "{\"op\":\"participants\"}"), area)
}
