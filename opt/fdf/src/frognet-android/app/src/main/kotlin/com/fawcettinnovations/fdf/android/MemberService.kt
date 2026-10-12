// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// MemberService.kt -- [FDF_MEMBER_KT_V1] [PONDS_V1] the device's memberships, one session per pond, held all the time:
//   forward pond     placement, the leaf's facts, the tunnel (Leaf), as one pond was before
//   participant pond {"role":"participant"} -- presence, the pond's shared memory, and its attention notices, shown as an
//                    Android notification with "Bring forward" ([ATTENTION_IS_A_TUPLE_V1])
//   Bring forward    the old forward pond re-announces as a participant and its tunnel goes down; the new one places
//                    itself, re-announces as the leaf and brings the tunnel up on the event that shows the hub holds its
//                    key ([HUB_SAYS_ITS_PEERS_V1]). Two re-announcements on two open sessions; nobody reconnects.
// Each session that ends: the reason is logged and after 5 s a NEW session starts -- a new member with the same ns
// ([REJOIN_EVERY_5S_V1]); the forward pond's tunnel goes down with its session (gone is gone).
// [BRIDGE_V1] (PLAN-BRIDGE §3.1) every participant pond is also REACHED, as its own (pond, config, net) allows: its
// facts say reach:true and the pubkey, its hub peers it, its RAM manager gives it an address, and its networks are
// reached through 1:1 aliases from 100.64.0.0/10 -- several ponds at once, all of them free to use the same 10.x
// addresses. One TUN (BridgeVpn), one fdf-tun, one WireGuard device per pond. A refused reach ends that pond's session
// and it is not rejoined (a failed reach closes it; the log says why).
package com.fawcettinnovations.fdf.android

import android.app.ActivityManager
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Intent
import android.content.pm.ServiceInfo
import android.net.ConnectivityManager
import android.net.LinkProperties
import android.net.Network
import android.net.NetworkCapabilities
import android.net.NetworkRequest
import android.os.Build
import android.os.IBinder
import com.fawcettinnovations.fdf.member.AliasLedger
import com.fawcettinnovations.fdf.member.Ambiguous
import com.fawcettinnovations.fdf.member.Bridge
import com.fawcettinnovations.fdf.member.BridgeNet
import com.fawcettinnovations.fdf.member.Pfx
import com.fawcettinnovations.fdf.member.BridgeForwardDriver
import com.fawcettinnovations.fdf.member.Json
import com.fawcettinnovations.fdf.member.Leaf
import com.fawcettinnovations.fdf.member.Reach
import com.fawcettinnovations.fdf.member.Member
import com.fawcettinnovations.fdf.member.MemberConfig
import com.fawcettinnovations.fdf.member.Placement
import java.net.Inet4Address
import java.net.NetworkInterface
import java.util.concurrent.ConcurrentHashMap

class MemberService : Service() {
    @Volatile private var running = false
    private val workers = ConcurrentHashMap<String, Thread>()
    private val members = ConcurrentHashMap<String, Member>()
    private val leaves = ConcurrentHashMap<String, Leaf>()
    private val reaches = ConcurrentHashMap<String, Reach>()
    private val refused = ConcurrentHashMap<String, String>()
    private lateinit var bridge: Bridge
    private lateinit var tunnel: BridgeForwardDriver
    private lateinit var ident: Identity

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate(); instance = this; AppLog.init(this); ident = Identity(this)
        bridge = Bridge(AndroidBridgeHost(this, AppLog::add) { carrier.toList() }, ident.keys.privateKey.toBase64(), AppLog::add,
            ledger = AliasLedger(file = java.io.File(filesDir, "fdf-aliases.json")), isForward = { it == ident.forward },
            extraScope = { carrier.associate { "$it/32" to "this device's own network uses it (carrier-grade NAT)" } },
            onLost = { members.values.forEach { it.leave() } })
        tunnel = BridgeForwardDriver(bridge)
        val nm = getSystemService(NotificationManager::class.java)
        nm.createNotificationChannel(NotificationChannel("fdf", "FrogNet membership", NotificationManager.IMPORTANCE_LOW))
        nm.createNotificationChannel(NotificationChannel("fdf-attention", "FrogNet pond notices", NotificationManager.IMPORTANCE_HIGH))
        // [ADDRESS_CHANGE_ENDS_SESSION_V1] the placement is the address this device has: when its IPv4 set changes
        // (wifi -> cellular, another LAN), the sessions end now and the next ones place the member again, instead of
        // waiting for TCP to notice the old path is gone. The event that happened, acted on; nothing polls.
        // [NOT_MY_OWN_VPN_V1] only NON-VPN networks count. The tunnel this service raises is a VPN network and becomes
        // the app's default network; watching the default network saw its own /32 arrive ("IPv4 [10.0.2.16] ->
        // [10.252.0.1]"), ended the session, took the tunnel down, rejoined, raised it again: a loop every 5 s. The set
        // compared is every non-VPN network's IPv4s together, the same set placement reads (ipv4s(): no tun).
        val cm = getSystemService(ConnectivityManager::class.java)
        val req = NetworkRequest.Builder().addCapability(NetworkCapabilities.NET_CAPABILITY_NOT_VPN).build()
        cm.registerNetworkCallback(req, object : ConnectivityManager.NetworkCallback() {
            override fun onLinkPropertiesChanged(network: Network, lp: LinkProperties) {
                v4ByNet[network] = lp.linkAddresses.map { it.address }.filterIsInstance<Inet4Address>().mapNotNull { it.hostAddress }
                // [CGNAT_IS_NOT_AN_ALIAS_V1] a carrier that uses 100.64.0.0/10 itself (its address, gateway, DNS): those are
                // never an alias, and never routed into the TUN (excluded, API 33+)
                cgByNet[network] = (lp.linkAddresses.map { it.address } + lp.dnsServers + lp.routes.mapNotNull { it.gateway })
                    .filterIsInstance<Inet4Address>().mapNotNull { it.hostAddress }.filter { BridgeNet.ALIASES.contains(Pfx.ip4(it) ?: -1L) }.toSet()
                carrier = cgByNet.values.flatten().toSet()
                changed("network: ${lp.interfaceName} changed")
            }
            override fun onLost(network: Network) {
                cgByNet.remove(network); carrier = cgByNet.values.flatten().toSet()
                if (v4ByNet.remove(network) != null) changed("network: a network is gone")
            }
        })
    }
    private val v4ByNet = ConcurrentHashMap<Network, List<String>>()
    private val cgByNet = ConcurrentHashMap<Network, Set<String>>()
    @Volatile private var carrier: Set<String> = emptySet()
    @Volatile private var lastV4: List<String>? = null
    @Synchronized private fun changed(what: String) {
        val now = v4ByNet.values.flatten().distinct().sorted()
        val was = lastV4; lastV4 = now
        if (was != null && was != now && members.isNotEmpty()) {
            AppLog.add("$what: IPv4 $was -> $now; ending the sessions to place the member again"); members.values.forEach { it.leave() }
        }
    }

    private fun notification(text: String): Notification = Notification.Builder(this, "fdf")
        .setContentTitle("FrogNet member").setContentText(text).setSmallIcon(android.R.drawable.stat_sys_upload_done).setOngoing(true).build()
    private fun status(text: String) = getSystemService(NotificationManager::class.java).notify(1, notification(text))
    private fun status() {
        val f = ident.forward; val leaf = leaves[f]; val m = members[f]
        status("forward $f: " + when (val pl = leaf?.placement) {
            is Placement.OnMesh -> "at ${pl.ip} on ${pl.servedBy}'s LAN"
            Placement.OffMesh -> tunnel.current?.let { "${m?.regionAddr} via ${it.name}" } ?: "off the mesh, no tunnel"
            null -> "joining" } + "; ponds ${ident.ponds.joinToString(" ") { it.area + (if (it.area in bridge.areas()) "(reached)" else if (refused.containsKey(it.area)) "(refused)" else "") }}")
    }

    /** [ATTENTION_IS_A_TUPLE_V1] a notice in a participant pond: an Android notification, "Bring forward" = ACTION_FORWARD. */
    private fun attention(area: String, cell: Json) {
        if (area == ident.forward) return                                   // already here
        val why = cell["bag"]["why"].str; val from = cell["bag"]["from"].str.ifEmpty { cell["instance"].str }
        AppLog.add("ATTENTION $area: $why (from $from)")
        val fwd = PendingIntent.getService(this, area.hashCode(), Intent(this, MemberService::class.java).setAction(ACTION_FORWARD).putExtra("area", area),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
        val n = Notification.Builder(this, "fdf-attention").setContentTitle("$area: $why").setContentText("from $from")
            .setSmallIcon(android.R.drawable.ic_dialog_info).setAutoCancel(true)
            .addAction(Notification.Action.Builder(null, "Bring forward", fwd).build()).build()
        getSystemService(NotificationManager::class.java).notify(1000 + (area.hashCode() and 0xffff), n)
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_STOP -> { stopMembership("stopped by the user"); stopSelf(); return START_NOT_STICKY }
            ACTION_FORWARD -> { intent.getStringExtra("area")?.let { a -> Thread({ bringForward(a) }, "fdf-forward").start() }; return START_STICKY }
        }
        if (Build.VERSION.SDK_INT >= 34) startForeground(1, notification("joining"), ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE)
        else startForeground(1, notification("joining"))
        running = true; refused.clear()
        for (pond in ident.ponds) if (workers[pond.area]?.isAlive != true)
            workers[pond.area] = Thread({ loop(pond) }, "fdf-member-${pond.area}").apply { start() }
        return START_STICKY
    }

    private fun ipv4s(): List<String> = NetworkInterface.getNetworkInterfaces().toList()
        .filter { it.isUp && !it.isLoopback && !it.name.startsWith("tun") }
        .flatMap { it.inetAddresses.toList() }.filterIsInstance<Inet4Address>().map { it.hostAddress ?: "" }.filter { it.isNotEmpty() }

    private fun capability(): String {
        val mi = ActivityManager.MemoryInfo(); getSystemService(ActivityManager::class.java).getMemoryInfo(mi)
        return "{\"cores\":${Runtime.getRuntime().availableProcessors()},\"mem_total_kb\":${mi.totalMem / 1024},\"os\":\"android\"," +
               "\"android_sdk\":${Build.VERSION.SDK_INT},\"client\":\"fdf-member-kt\"}"
    }

    private fun loop(pond: Pond) {
        val id = ident; val area = pond.area; val c = pond.ram.lastIndexOf(':')
        AppLog.add("member: ${id.name} guid=${id.guid} pond $area at ${pond.ram} (${if (id.forward == area) "forward" else "participant"})")
        while (running && id.ponds.any { it.area == area } && !refused.containsKey(area)) {
            val say = { t: String -> AppLog.add("[$area] $t") }
            val leaf = Leaf(area, ::ipv4s, { id.keys.publicKey.toBase64() }, tunnel, say, isForward = { id.forward == area })
            val reach = Reach(area, bridge, { id.keys.publicKey.toBase64() }, say) { why -> refused[area] = why }
            leaves[area] = leaf; reaches[area] = reach
            val m = Member(MemberConfig(pond.ram.substring(0, c), pond.ram.substring(c + 1).toInt(), id.name, id.guid, ::ipv4s, ::capability,
                { if (id.forward == area) id.facts(leaf.placement) else id.participantFacts() }, area, "", 30, AndroidTuner, say,
                prepare = { mm -> if (id.forward == area) leaf.place(mm, mm.world()) },
                onJoined = { mm -> leaf.tunnel(mm, mm.world()); if (id.forward != area) reach.onRegionChange(mm); status() },
                onRegionChange = { mm -> leaf.onRegionChange(mm); if (id.forward != area) reach.onRegionChange(mm) else bridge.removeIsland(area, "it is the forward pond now"); status() },
                onAttention = { _, cell -> attention(area, cell) }))
            members[area] = m
            val why = m.runOnce()
            members.remove(area)
            if (id.forward == area) try { tunnel.down("the session to $area ended: $why") } catch (e: Exception) { AppLog.add("[$area] ${e.message}") }
            reach.ended(why)
            if (refused.containsKey(area)) { AppLog.add("member: $area is not rejoined: its reach was refused (${refused[area]})"); status(); break }
            if (!running) break
            AppLog.add("member: not a member of $area ($why); a new session in 5 s")
            try { Thread.sleep(5000) } catch (_: InterruptedException) { break }
        }
        workers.remove(area)
    }

    /** [FORWARD_IS_A_FACT_V1] the old forward pond becomes a participant, `area` comes forward; both sessions stay open. */
    private fun bringForward(area: String) {
        val old = ident.forward
        if (area == old || ident.ponds.none { it.area == area }) return
        val t0 = System.currentTimeMillis()
        ident.forward = area
        // the old pond first: its tunnel goes down (the driver is shared, review 2026-10-11); then the new one up; then
        // the old one is reached as a participant
        members[old]?.let { m -> m.reannounce(); try { leaves[old]?.onRegionChange(m) } catch (e: Exception) { AppLog.add("forward: $old: ${e.message}") } }
        members[area]?.let { m -> try { bridge.removeIsland(area, "it is the forward pond now"); leaves[area]?.becameForward(m) } catch (e: Exception) { AppLog.add("forward: $area: ${e.message}") } }
        members[old]?.let { m -> try { reaches[old]?.onRegionChange(m) } catch (e: Exception) { AppLog.add("forward: $old: ${e.message}") } }
        AppLog.add("forward: $old -> $area in ${System.currentTimeMillis() - t0} ms (tunnel follows on the hub's event)")
        getSystemService(NotificationManager::class.java).cancel(1000 + (area.hashCode() and 0xffff))
        status()
    }

    private fun stopMembership(why: String) {
        running = false; AppLog.add("member: leaving every pond ($why)")
        members.values.forEach { it.leave() }; workers.values.forEach { it.interrupt() }
        runCatching { tunnel.down(why) }; bridge.close()
    }

    /** the VPN was taken away: nothing of it stands; the sessions end so the next ones decide again ([GONE_IS_GONE_V1]) */
    fun vpnRevoked() { Thread({ bridge.close(); members.values.forEach { it.leave() } }, "fdf-revoked").start() }

    /** [RESOLVE_AMBIGUOUS_REFUSED_V1] "w:<guid>:<port>" as host:port here, asking every pond this device is in */
    fun resolve(ep: String): String? = try { bridge.resolve(ep, HashMap(members), ident.forward) }
        catch (e: Ambiguous) { AppLog.add("resolve: ${e.message}"); null } catch (e: Exception) { AppLog.add("resolve $ep: ${e.message}"); null }

    override fun onDestroy() { if (running) stopMembership("service destroyed"); instance = null; super.onDestroy() }

    /** [COMMS_FROM_THE_POND_V1] the Communicator the forward pond's members say they run, read now on that pond's
     *  session: (pond, area, from, ram, media), or null with the reason logged (not joined; nobody runs one). */
    fun communicator(): List<String>? {
        val area = ident.forward; val m = members[area]
        if (m == null) { AppLog.add("communicator: not joined to $area yet"); return null }
        val all = try { m.services("comms") } catch (e: Exception) { AppLog.add("communicator: reading $area's where cells failed: ${e.message}"); return null }
        if (all.isEmpty()) { AppLog.add("communicator: no member of $area runs one (no comms in its where cells)"); return null }
        val (from, b) = all.first()
        if (all.size > 1) AppLog.add("communicator: ${all.size} in $area (${all.joinToString { it.first }}); using $from's")
        // [IDENTITY_IN_TUPLES_V1] the cell names the node by identity; its address is the one it has in this pond
        val ram = try { m.endpoint(b["ram"].str) } catch (e: Exception) { AppLog.add("communicator: $from's memory: ${e.message}"); return null }
        val media = try { m.endpoint(b["media"].str) } catch (e: Exception) { AppLog.add("communicator: $from's media: ${e.message}"); return null }
        AppLog.add("communicator: $area's is on $from: memory $ram, media $media (${b["ram"].str})")
        return listOf(area, from, ram, media, ident.name)
    }

    companion object {
        @Volatile var instance: MemberService? = null
        const val ACTION_STOP = "com.fawcettinnovations.fdf.android.STOP"
        const val ACTION_FORWARD = "com.fawcettinnovations.fdf.android.FORWARD"
    }
}
