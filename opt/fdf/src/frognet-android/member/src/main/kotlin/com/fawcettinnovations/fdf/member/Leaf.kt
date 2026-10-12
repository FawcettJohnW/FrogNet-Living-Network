// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// Leaf.kt -- [LEAF_FROM_THE_REGION_V1] everything a leaf decides, decided from the region on every event, as the node
// daemon decides its slice: one `world` read per `p` (participants + mappings, [WORLD_IN_ONE_READ_V1]), then
//   placement  OnMesh / OffMesh from the address the device has ([ADDRESS_IS_THE_ONE_IT_HAS_V1]); a change re-announces
//   tunnel     wanted = the first hub, only OffMesh with the /32 the region gave ([LEAF_ADDR_FROM_THE_REGION_V1]); up when wanted and not up (or the hub's key or
//              endpoint changed), down when not wanted ([GONE_IS_GONE_V1]: no hub, no tunnel)
// Nothing here asks the network or a store; nothing polls. The app and the JVM runner drive the same code.
package com.fawcettinnovations.fdf.member

interface TunnelDriver {
    val current: Hub?
    fun up(hub: Hub, addr: String)                      // addr: the /32 the region gave this leaf
    fun down(why: String)
}

class Leaf(private val area: String, private val ipv4s: () -> List<String>, private val myPubkey: () -> String,
           private val driver: TunnelDriver, private val log: (String) -> Unit,
           private val isForward: () -> Boolean = { true }) {   // [FORWARD_IS_A_FACT_V1] only the forward pond's leaf places itself and holds the tunnel
    @Volatile var placement: Placement = Placement.OffMesh; private set

    private fun say(p: Placement) = when (p) {
        is Placement.OnMesh -> "placement: on the mesh: ${p.ip} is in ${p.subnet}, served by ${p.servedBy}; routed by that /24, no tunnel"
        Placement.OffMesh -> "placement: off the mesh (${ipv4s()} are on no member's /24): a leaf through the hub, its /32 from the region"
    }

    /** Before the announcement (Member prepare): where am I. */
    fun place(m: Member, world: Json): Boolean {
        val now = Placer.place(ipv4s(), world, m.ns)
        val changed = now != placement || !placedOnce
        placement = now; placedOnce = true
        if (changed) log(say(now))
        return changed
    }
    @Volatile private var placedOnce = false

    /** After joining, and on every region event: the tunnel this placement and the region's hubs call for. */
    fun tunnel(m: Member, world: Json) {
        val addr = m.regionAddr
        if (!isForward()) { if (driver.current != null && upAddr.isNotEmpty()) { driver.down("$area is not the forward pond now"); upAddr = "" }; return }
        val hub = if (placement == Placement.OffMesh && addr.isNotEmpty()) Hubs.from(world, area).firstOrNull() else null
        // [HUB_SAYS_ITS_PEERS_V1] up only once the hub says it holds my key; until then the event that shows it is awaited
        val want = hub?.takeIf { myPubkey() in it.peers }
        val cur = driver.current
        if (hub != null && want == null && cur == null) { if (!saidWaiting) { log("tunnel: hub ${hub.name} does not hold my key yet; waiting for the event that shows it"); saidWaiting = true }; return }
        saidWaiting = false
        when {
            want == null && cur != null -> driver.down(if (placement is Placement.OnMesh) "on the mesh now" else "the region has no hub now")
            want == null && placement == Placement.OffMesh && addr.isNotEmpty() && cur == null ->
                if (!saidNoHub) { log("tunnel: the region has no hub (alive, role hub, pubkey, endpoint): no tunnel"); saidNoHub = true }
            want != null && (cur == null || cur.pubkey != want.pubkey || cur.endpoint != want.endpoint || addr != upAddr) -> { saidNoHub = false; upAddr = addr; driver.up(want, addr) }
        }
    }
    @Volatile private var saidNoHub = false
    @Volatile private var saidWaiting = false
    @Volatile private var upAddr = ""

    /** A region event: one world read, re-place (re-announce if it moved), and the tunnel. */
    fun onRegionChange(m: Member) {
        val w = m.world()
        if (isForward() && place(m, w)) m.reannounce()          // a participant does not place itself: nothing of it is routed
        tunnel(m, w)
    }

    /** This pond was just brought forward: where am I in it (always re-announced: the facts go from participant to leaf). */
    fun becameForward(m: Member) { val w = m.world(); placedOnce = false; place(m, w); m.reannounce(); tunnel(m, w) }
}
