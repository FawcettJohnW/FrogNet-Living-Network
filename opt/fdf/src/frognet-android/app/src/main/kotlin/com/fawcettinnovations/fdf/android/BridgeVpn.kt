// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// BridgeVpn.kt -- [BRIDGE_V1] the phone's one TUN, and fdf-tun on it (PLAN-BRIDGE §3.1). Replaces GoBackend.
//
//   BridgeVpn          the VpnService: establish() = one address (BridgeNet.LOCAL, never changes, whatever a pond gave),
//                      routes 100.64.0.0/10 (every island alias) and, while the forward tunnel is up, 10.0.0.0/8.
//   AndroidBridgeHost  link(): a TUN of that shape, libfdftun.so (fdf-tun, shipped in jniLibs) started with
//                      --fd-socket @<name>, the TUN fd sent to it over that abstract SOCK_SEQPACKET socket (SCM_RIGHTS;
//                      fdf-tun takes it only from this app's uid), our copy closed -- fdf-tun holds the only one, so the
//                      TUN lives exactly as long as it does (gone is gone). Then every socket fdf-tun's WireGuard opens
//                      comes back on that socket to be protect()ed BEFORE it binds ([FDF_PROTECT_HOOK_V1]), so the
//                      tunnels' own packets never loop into the TUN. Routes fixed at establish(): a new shape is a new
//                      TUN and a new fdf-tun; the Bridge sends it the whole state and closes the old.
package com.fawcettinnovations.fdf.android

import android.content.Context
import android.content.Intent
import android.net.IpPrefix
import android.net.LocalSocket
import android.net.LocalSocketAddress
import android.net.VpnService
import android.os.Build
import android.os.ParcelFileDescriptor
import android.system.Os
import com.fawcettinnovations.fdf.member.BridgeHost
import com.fawcettinnovations.fdf.member.BridgeLink
import com.fawcettinnovations.fdf.member.BridgeNet
import com.fawcettinnovations.fdf.member.Hubs
import com.fawcettinnovations.fdf.member.Json
import com.fawcettinnovations.fdf.member.ProcessBridgeLink
import com.fawcettinnovations.fdf.member.ProtocolError
import java.io.File
import java.net.InetAddress
import java.util.concurrent.CompletableFuture
import java.util.concurrent.TimeUnit

class BridgeVpn : VpnService() {
    override fun onCreate() { super.onCreate(); running.complete(this) }
    override fun onDestroy() { running = CompletableFuture(); super.onDestroy() }
    override fun onRevoke() { AppLog.add("bridge: the VPN was revoked (another VPN, or the user): every tunnel is down"); MemberService.instance?.vpnRevoked(); super.onRevoke() }

    /** a TUN of this shape; null: not permitted (VpnService.prepare was not accepted) */
    fun tun(forwardRoutes: Boolean, carrier: List<String>, log: (String) -> Unit): ParcelFileDescriptor? {
        val b = Builder().setSession("FrogNet").setMtu(MTU).addAddress(BridgeNet.LOCAL, 32)
        for (r in BridgeNet.VPN_ROUTES + (if (forwardRoutes) listOf(Hubs.LEAF_ALLOWED) else emptyList())) {
            val (a, n) = r.split('/'); b.addRoute(a, n.toInt())
        }
        // [CGNAT_IS_NOT_AN_ALIAS_V1] the carrier's own 100.64.0.0/10 addresses stay on the carrier's network
        if (carrier.isNotEmpty()) {
            if (Build.VERSION.SDK_INT >= 33) carrier.forEach { b.excludeRoute(IpPrefix(InetAddress.getByName(it), 32)) }
            else log("bridge: this device's own network uses ${carrier.joinToString()} (carrier-grade NAT): Android < 13 cannot keep them out of the VPN's 100.64.0.0/10; they are never an alias, but they go into the TUN")
        }
        return b.establish()
    }

    companion object {
        const val MTU = 1280      // under every path's MTU after WireGuard's 80 bytes; the same for every scope
        @Volatile private var running = CompletableFuture<BridgeVpn>()
        fun await(ctx: Context): BridgeVpn {
            if (!running.isDone) ctx.startService(Intent(ctx, BridgeVpn::class.java))
            return running.get(5, TimeUnit.SECONDS)
        }
        fun stop(ctx: Context) { ctx.stopService(Intent(ctx, BridgeVpn::class.java)) }
    }
}

class AndroidBridgeHost(private val ctx: Context, private val log: (String) -> Unit, private val carrier: () -> List<String>) : BridgeHost {
    private var n = 0

    override fun link(forwardRoutes: Boolean): BridgeLink {
        val vpn = BridgeVpn.await(ctx)
        val pfd = vpn.tun(forwardRoutes, carrier(), log) ?: throw ProtocolError("the VPN is not permitted (VpnService.prepare was not accepted): Start again")
        val lib = File(ctx.applicationInfo.nativeLibraryDir, "libfdftun.so")
        val name = "fdftun-${android.os.Process.myPid()}-${++n}"
        val p = try { ProcessBridgeLink(listOf(lib.path, "--fd-socket", name), log) } catch (e: Exception) { pfd.close(); throw ProtocolError("starting ${lib.path}: ${e.message}") }
        val sock = LocalSocket(LocalSocket.SOCKET_SEQPACKET)
        try {
            val h = p.hello()
            if (h["listening"].str != "@$name") throw ProtocolError("fdf-tun did not listen on @$name: $h")
            sock.connect(LocalSocketAddress(name, LocalSocketAddress.Namespace.ABSTRACT))
            sock.setFileDescriptorsForSend(arrayOf(pfd.fileDescriptor)); sock.outputStream.write("tun".toByteArray()); sock.setFileDescriptorsForSend(null)
            val h2 = p.hello()
            if (!h2["ok"].bool) throw ProtocolError("fdf-tun did not take the TUN: $h2")
            log("bridge: fdf-tun ${h2["build"].str} on the VPN's TUN (routes 100.64.0.0/10" + (if (forwardRoutes) " and ${Hubs.LEAF_ALLOWED}" else "") + ")")
        } catch (e: Exception) { runCatching { sock.close() }; p.close(); pfd.close(); throw e }
        pfd.close()                                                    // fdf-tun holds the only copy now
        val protector = Thread({ protectLoop(vpn, sock) }, "fdf-protect").apply { isDaemon = true; start() }
        return object : BridgeLink {
            override fun call(line: String): Json = p.call(line)
            override fun close() { p.close(); runCatching { sock.close() }; protector.interrupt() }
        }
    }

    /** [FDF_PROTECT_HOOK_V1] "protect <n>" + one socket fd -> VpnService.protect -> "ok <n>" (or "no <n>"): before fdf-tun binds it */
    private fun protectLoop(vpn: BridgeVpn, sock: LocalSocket) {
        val buf = ByteArray(64)
        try {
            while (true) {
                val k = sock.inputStream.read(buf); if (k < 0) break
                val fds = sock.ancillaryFileDescriptors ?: emptyArray()
                val req = String(buf, 0, k); val n = req.substringAfter("protect ", "")
                var ok = fds.size == 1 && req.startsWith("protect ") && n.isNotEmpty()
                for (fd in fds) {
                    if (ok) ParcelFileDescriptor.dup(fd).use { ok = vpn.protect(it.fd) }
                    runCatching { Os.close(fd) }
                }
                if (!ok) log("bridge: a socket fdf-tun opened could NOT be protected: its bind fails (said by fdf-tun too)")
                sock.outputStream.write(((if (ok) "ok " else "no ") + n).toByteArray())   // the number pairs it with its request
            }
        } catch (e: Exception) { /* the link closed */ }
    }

    override fun stop() { BridgeVpn.stop(ctx) }
}
