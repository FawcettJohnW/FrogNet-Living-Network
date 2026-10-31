// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// SettingsTupleTest.kt -- [SETTINGS_ARE_TUPLES_V1] what the settings pages and their followers rely on, against a real
// RAM manager (rina-host), with the member code the phone runs:
//   1 the member joins; (pond, cap, <ns>) read by instance: none (a fresh device writes its own then)
//   2 the device writes (pond, cap, <ns>) and (pond, cap, <ns>:kart) permanent; reading instance <ns> gives <ns>'s cell
//   3 the device holds a read on (pond, cap, <ns>); another session (a monitor, a settings page) writes it: the read
//     returns with the new bag, at once
//   4 a write to (pond, cap, <ns>:kart) does not change what a read of (pond, cap, <ns>) returns
//   5 telemetry off: (pond, telem, <ns>) written {} own reads as gone to a monitor
// usage: SettingsTupleTest HOST PORT
import com.fawcettinnovations.fdf.member.*
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import kotlin.system.exitProcess

fun main(a: Array<String>) {
    val host = a[0]; val port = a[1].toInt(); var fails = 0
    fun check(ok: Boolean, what: String) { println((if (ok) "PASS " else "FAIL ") + what); if (!ok) fails++ }
    val tuner = SocketTuner { it.tcpNoDelay = true }
    val joined = CountDownLatch(1)
    val m = Member(MemberConfig(host, port, "phone-set", "guid-settings-test", { listOf("192.0.2.8") }, { "{\"os\":\"android\"}" }, { "" },
        "", "", 30, tuner, { println("  member: $it") }, onJoined = { joined.countDown() }))
    val t = Thread({ println("  member session ended: " + m.runOnce()) }, "member").apply { start() }
    check(joined.await(10, TimeUnit.SECONDS), "1 the member joined the region")
    val ns = m.ns
    fun cs(r: Json) = if (r["cells"].type == Json.Type.ARR) r["cells"] else r["rows"]
    fun mine(r: Json, inst: String): Json? { var f: Json? = null; val c = cs(r); for (i in 0 until c.a.size) if (c[i]["instance"].str == inst) f = c[i]; return f }
    val r0 = m.read("pond", "cap", ns)
    check(mine(r0, ns) == null, "1 (pond, cap, $ns) read by instance: none yet (${cs(r0).a.size} cell(s) returned)")
    val id1 = m.write("pond", "cap", ns, "{\"name\":\"phone-set\",\"telem_on\":true,\"telem_gps\":true,\"telem_period_s\":30}", permanent = true)
    val idk = m.write("pond", "cap", "$ns:kart", "{\"laps\":3,\"auto\":false}", permanent = true)
    check(id1 > 0 && idk > 0, "2 (pond, cap, $ns) #$id1 and (pond, cap, $ns:kart) #$idk written permanent")
    val r1 = m.read("pond", "cap", ns)
    val insts = (0 until cs(r1).a.size).map { cs(r1)[it]["instance"].str }
    check(mine(r1, ns)?.get("bag")?.get("telem_period_s")?.num == 30.0, "2 reading instance $ns gives its cell (instances returned: $insts)")
    // 3: the device holds; a monitor writes
    val mon = Session(host, port, tuner) { println("  monitor: $it") }
    val got = CountDownLatch(1); var bag: Json? = null; var heldMs = 0L
    val after = (0 until cs(r1).a.size).maxOf { cs(r1)[it]["id"].num!!.toLong() }
    Thread({
        val t0 = System.nanoTime()
        val r = m.read("pond", "cap", ns, after, 20.0)
        heldMs = (System.nanoTime() - t0) / 1_000_000; bag = mine(r, ns)?.get("bag"); got.countDown()
    }, "held").start()
    Thread.sleep(500)
    val w0 = System.nanoTime()
    mon.call("POST", "$API?op=write", "{\"service\":\"pond\",\"variable\":\"cap\",\"instance\":\"$ns\",\"bag\":{\"name\":\"phone-set\",\"telem_on\":true,\"telem_gps\":false,\"telem_period_s\":60,\"by\":\"monitor\"},\"permanent\":true}", 15.0)
    check(got.await(10, TimeUnit.SECONDS) && bag?.get("telem_period_s")?.num == 60.0 && bag?.get("telem_gps")?.bool == false,
        "3 the device's held read returned with the monitor's bag " + (System.nanoTime() - w0) / 1_000_000 + " ms after its write (held $heldMs ms): $bag")
    // 4
    m.write("pond", "cap", "$ns:kart", "{\"laps\":5,\"auto\":true}", permanent = true)
    val r4 = m.read("pond", "cap", ns)
    check(mine(r4, ns)?.get("bag")?.get("telem_period_s")?.num == 60.0, "4 after a write to $ns:kart, $ns's cell is as the monitor left it")
    check(mine(m.read("pond", "cap", "$ns:kart"), "$ns:kart")?.get("bag")?.get("laps")?.num == 5.0, "4 and $ns:kart's is the new one (laps 5)")
    // 5
    m.write("pond", "telem", ns, "{\"name\":\"phone-set\",\"battery\":{\"pct\":50}}", own = true)
    m.write("pond", "telem", ns, "{}", own = true)
    val r5 = Json.parse(mon.call("GET", "$API?op=read&service=pond&variable=telem", "", 15.0).body)
    val c5 = mine(r5, ns)
    check(c5 == null || c5["bag"].o.isEmpty(), "5 (pond, telem, $ns) written {} reads as gone to a monitor: ${c5?.get("bag")}")
    m.leave(); t.join(5000); mon.close()
    println(if (fails == 0) "ALL PASS" else "$fails FAIL(S)")
    exitProcess(if (fails == 0) 0 else 1)
}
