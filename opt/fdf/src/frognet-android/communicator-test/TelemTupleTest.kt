// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// TelemTupleTest.kt -- [TELEMETRY_IS_A_TUPLE_V1] [MEMBER_WRITES_TUPLES_V1] the member's own tuples against a real
// RAM manager (rina-host, the region's RAM interface), on a JVM, with the member code the phone runs:
//   1 the member joins; it writes (pond, telem, <ns>) own:true on its session
//   2 a second session (any monitor) reads the family (pond, telem) and finds it, joined by ns
//   3 a held read on the family is waiting when the member leaves: it fires with {} -- gone with the member
//   4 a permanent tuple (pond, cap, <ns>) written by the member is still there after it left
// usage: TelemTupleTest HOST PORT      prints PASS/FAIL per check; exits 1 on a FAIL
import com.fawcettinnovations.fdf.member.*
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import kotlin.system.exitProcess

fun main(a: Array<String>) {
    val host = a[0]; val port = a[1].toInt(); var fails = 0
    fun check(ok: Boolean, what: String) { println((if (ok) "PASS " else "FAIL ") + what); if (!ok) fails++ }
    val tuner = SocketTuner { it.tcpNoDelay = true }
    val joined = CountDownLatch(1); var mem: Member? = null
    val m = Member(MemberConfig(host, port, "phone-test", "guid-telem-test", { listOf("192.0.2.7") }, { "{\"os\":\"android\"}" }, { "" },
        "", "", 30, tuner, { println("  member: $it") }, onJoined = { mm -> mem = mm; joined.countDown() }))
    val t = Thread({ println("  member session ended: " + m.runOnce()) }, "member").apply { start() }
    check(joined.await(10, TimeUnit.SECONDS), "1 the member joined the region")
    val ns = m.ns
    val bag = "{\"name\":\"phone-test\",\"kind\":\"android\",\"loc\":{\"lat\":47.4668,\"lon\":-122.3405,\"acc_m\":8.0},\"battery\":{\"pct\":81,\"charging\":false},\"at\":1.0}"
    val id = m.write("pond", "telem", ns, bag, own = true)
    check(id > 0, "1 (pond, telem, $ns) written own on the member's session: id $id")
    val capId = m.write("pond", "cap", ns, "{\"name\":\"phone-test\",\"telem_on\":true,\"telem_gps\":true,\"telem_period_s\":30}", permanent = true)
    check(capId > 0, "4 (pond, cap, $ns) written permanent: id $capId")
    System.getenv("TELEM_HOLD_S")?.let { println("HOLDING"); System.out.flush(); Thread.sleep(it.toLong() * 1000) }   // a monitor looks meanwhile
    // a monitor: its own session
    val mon = Session(host, port, tuner) { println("  monitor: $it") }
    fun cells(v: String) = Json.parse(mon.call("GET", "$API?op=read&service=pond&variable=$v", "", 15.0).body).let { j -> if (j["cells"].type.name == "ARR") j["cells"] else j["rows"] }
    val c1 = cells("telem"); var found: Json? = null; var last = 0L
    for (i in 0 until c1.a.size) { val c = c1[i]; last = maxOf(last, c["id"].num!!.toLong()); if (c["instance"].str == ns) found = c }
    check(found != null && found["bag"]["loc"]["lat"].num == 47.4668, "2 the monitor reads the family and finds it by ns: ${found?.get("bag")?.get("battery")?.get("pct")?.num}% battery, lat ${found?.get("bag")?.get("loc")?.get("lat")?.num}")
    // the held read, then the member leaves
    val fired = CountDownLatch(1); var gone = false; var heldMs = 0L
    Thread({
        val t0 = System.nanoTime()
        val r = Json.parse(mon.call("GET", "$API?op=read&service=pond&variable=telem&after=$last&wait_s=20", "", 40.0).body)
        val cs = if (r["cells"].type.name == "ARR") r["cells"] else r["rows"]
        for (i in 0 until cs.a.size) if (cs[i]["instance"].str == ns && cs[i]["bag"].o.isEmpty()) gone = true
        heldMs = (System.nanoTime() - t0) / 1_000_000; fired.countDown()
    }, "held").start()
    Thread.sleep(500)
    val t0 = System.nanoTime(); m.leave(); t.join(5000)
    check(fired.await(10, TimeUnit.SECONDS) && gone, "3 the held read fired with {} for $ns when the member left (" + (System.nanoTime() - t0) / 1_000_000 + " ms after leave)")
    val after = cells("telem"); var still = false; for (i in 0 until after.a.size) if (after[i]["instance"].str == ns && after[i]["bag"].o.isNotEmpty()) still = true   // a removal reads as {}
    check(!still, "3 a fresh read: no (pond, telem, $ns) -- gone is gone")
    val cap = cells("cap"); var kept = false; for (i in 0 until cap.a.size) if (cap[i]["instance"].str == ns && cap[i]["bag"]["telem_period_s"].num == 30.0) kept = true
    check(kept, "4 (pond, cap, $ns) is still there after the member left (permanent)")
    mon.close()
    println(if (fails == 0) "ALL PASS" else "$fails FAIL(S)")
    exitProcess(if (fails == 0) 0 else 1)
}
