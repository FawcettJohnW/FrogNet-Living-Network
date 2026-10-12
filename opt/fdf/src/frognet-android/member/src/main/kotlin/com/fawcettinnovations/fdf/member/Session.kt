// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// Session.kt -- [FNWP_RAW_MEMBER_V1] one FNWP session to a RAM manager, as frogram::Session opens it
// (rinab/platform/ribbit_cpp/frogram.cpp Session::Session), speaking only REQ_RAW:
//
//   request socket  HELLO <token>          frames out, in order; the server numbers them 0,1,2,... as they arrive
//   return socket   HELLO RETURN:<token>   the server's HELLO back, then [seq:4][reply] in any order
//   data socket     HELLO DATA:<token>     the server's HELLO back, then segments of large replies (dataplane.hpp)
//
// The session's liveness IS the request socket: the RAM manager ends the session (node record, announcement,
// mappings, one `m` everywhere) the moment it reads EOF there (ram_server.cpp serve). If any of the three sockets
// dies, all three are closed and every call in flight fails with the reason: no reconnect here, no second path
// ([NO_FALLBACK_V1]); the owner makes a new session, which is a new member.
package com.fawcettinnovations.fdf.member

import java.io.DataInputStream
import java.io.IOException
import java.io.OutputStream
import java.net.InetSocketAddress
import java.net.Socket
import java.security.SecureRandom
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

class Unreachable(msg: String) : Exception(msg)
class Refused(msg: String, val error: String = "", val why: String = "") : Exception(msg)

// [KEEPALIVE_5S_V1] TCP_NODELAY + SO_KEEPALIVE idle 2 s, interval 1 s, 3 probes: a silent death of the far end is
// seen in 5 s, as both ends of the C++ session set it. The JVM and Android set the per-socket values differently.
fun interface SocketTuner { fun tune(s: Socket) }

class Session(private val host: String, private val port: Int, private val tuner: SocketTuner,
              private val log: (String) -> Unit = {}) : AutoCloseable {
    val token: String = "frogram-kt-" + ByteArray(8).also { SecureRandom().nextBytes(it) }.joinToString("") { "%02x".format(it) }
    class Answer(val status: Int, val body: String)

    private class Pending { val done = CountDownLatch(1); @Volatile var answer: Answer? = null; @Volatile var err: String? = null }

    private var req: Socket? = null; private var ret: Socket? = null; private var dat: Socket? = null
    private val reqOut: OutputStream
    private val sendLock = Any()
    private var nextSeq = 0L                                       // the server's count of frames on the request socket
    private val pending = ConcurrentHashMap<Long, Pending>()
    private val markers = ConcurrentHashMap<Long, Pair<Wire.Reply, Int>>()   // seq -> (marker, bytes announced)
    private val landed = ConcurrentHashMap<Long, ByteArray>()                // seq -> body that landed first
    private val pairLock = Any()
    @Volatile var dead: String? = null; private set
    private val deadLatch = CountDownLatch(1)
    var dataReplies = 0L; private set

    init {
        val rq = dial(); req = rq; reqOut = rq.getOutputStream()
        sendFrame(reqOut, Wire.hello(token))
        val rt = dial(); ret = rt; sendFrame(rt.getOutputStream(), Wire.hello("RETURN:$token"))
        if (!Wire.isHello(recvFrame(DataInputStream(rt.getInputStream())))) { closeAll(); throw Refused("expected the far end's HELLO") }
        val dt = dial(); dat = dt; sendFrame(dt.getOutputStream(), Wire.hello("DATA:$token"))
        if (!Wire.isHello(recvFrame(DataInputStream(dt.getInputStream())))) {
            closeAll(); throw Refused("the far end did not accept the data socket (HELLO DATA:) -- it does not speak the full FNWP contract") }
        Thread({ readReturn() }, "fnwp-return").apply { isDaemon = true }.start()
        Thread({ readData() }, "fnwp-data").apply { isDaemon = true }.start()
        log("session up to $host:$port token=$token (request, return, data sockets; keepalive 2+3x1 s)")
    }

    private fun dial(): Socket {
        val s = Socket()
        // tuned once connected: Android sets the keepalive values through the socket's fd, which exists only then
        try { s.connect(InetSocketAddress(host, port), 10_000); tuner.tune(s) }
        catch (e: IOException) { try { s.close() } catch (_: IOException) {}; throw Unreachable("$host:$port connect failed: ${e.message}") }
        return s
    }

    /** One request; blocks this thread only. A held read (`?op=rina_block` wait) passes a very long timeout. */
    fun call(method: String, path: String, body: String, timeoutS: Double = 15.0): Answer {
        val p = Pending()
        dead?.let { throw Unreachable(it) }
        val frame = Wire.reqRaw(host, method, path, body)
        synchronized(sendLock) {
            val seq = nextSeq++
            pending[seq] = p                                       // before the bytes leave: the reply can beat us back
            try { sendFrame(reqOut, frame) } catch (e: IOException) { pending.remove(seq); die("send failed: ${e.message}"); throw Unreachable(dead ?: "send failed") }
        }
        val ms = if (timeoutS > 1e8) Long.MAX_VALUE else (timeoutS * 1000).toLong()
        if (!p.done.await(ms, TimeUnit.MILLISECONDS)) throw Unreachable("no reply within the timeout")
        p.err?.let { throw Unreachable(it) }
        return p.answer!!
    }

    /** frogram Session::call: the answer parsed; ok:false or a status >= 400 is Refused with the region's error and why. */
    fun callJson(path: String, body: String, timeoutS: Double = 15.0): Json {
        val a = call("POST", path, body, timeoutS)
        val j = try { Json.parse(a.body) } catch (e: ProtocolError) { throw Refused("HTTP ${a.status}: unreadable answer: ${a.body.take(200)}") }
        if (a.status >= 400 || !j["ok"].bool) {
            val why = j["why"].str
            throw Refused("HTTP ${a.status}: ${j["error"].str}" + (if (why.isEmpty()) "" else " ($why)"), j["error"].str, why)  // [REFUSAL_SAYS_WHY_V1]
        }
        return j
    }

    private fun complete(seq: Long, r: Wire.Reply) {
        val p = pending.remove(seq) ?: throw ProtocolError("reply tagged seq $seq that was never sent")
        if (r.op == Wire.OP_ERROR) { p.answer = Answer(r.status, "{\"ok\":false,\"error\":\"FNWP\",\"why\":" + Json.quote(String(r.body, Charsets.UTF_8)) + "}") }
        else p.answer = Answer(r.status, String(r.body, Charsets.UTF_8))
        p.done.countDown()
    }

    // [DATA_MARKER_V1] the marker holds the reply's place; its body lands on the data socket under the same seq,
    // before or after the marker. Whichever arrives second completes the call.
    private fun pair(seq: Long, marker: Pair<Wire.Reply, Int>?, body: ByteArray?) {
        val m: Pair<Wire.Reply, Int>; val b: ByteArray
        synchronized(pairLock) {
            if (marker != null) { b = landed.remove(seq) ?: run { markers[seq] = marker; return }; m = marker }
            else { m = markers.remove(seq) ?: run { landed[seq] = body!!; return }; b = body!! }
        }
        val want = m.second
        if (b.size != want) throw ProtocolError("the landed payload is ${b.size} B, the marker said $want")
        dataReplies++
        complete(seq, Wire.Reply(m.first.op, m.first.status, "", b))
    }

    private fun readReturn() {
        val why = try {
            val inp = DataInputStream(ret!!.getInputStream())
            while (true) {
                val f = recvFrame(inp)
                if (f.size < 9) throw ProtocolError("reply frame too short")
                val seq = Wire.rd32(f, 0); val r = Wire.parseReply(f.copyOfRange(4, f.size))
                val n = Wire.markerBytes(r)
                if (n != null) pair(seq, r to n, null) else complete(seq, r)
            }
            @Suppress("UNREACHABLE_CODE") ""
        } catch (e: Exception) { "return socket: " + (e.message ?: e.javaClass.simpleName) }
        die(why)
    }

    private fun readData() {
        val why = try {
            val inp = DataInputStream(dat!!.getInputStream())
            class Part(val buf: ByteArray, var got: Int)
            val parts = HashMap<Long, Part>()
            while (true) {
                val rec = recvFrame(inp)
                if (rec.size < 12) throw ProtocolError("data socket: record shorter than its header")
                val seq = Wire.rd32(rec, 0); val total = Wire.rd32(rec, 4).toInt(); val off = Wire.rd32(rec, 8).toInt(); val n = rec.size - 12
                val p = parts.getOrPut(seq) { Part(ByteArray(total), 0) }
                if (p.buf.size != total || off + n > total) throw ProtocolError("data socket: segment does not fit its transfer (seq $seq)")
                System.arraycopy(rec, 12, p.buf, off, n); p.got += n
                if (p.got >= total) { parts.remove(seq); pair(seq, null, p.buf) }
            }
            @Suppress("UNREACHABLE_CODE") ""
        } catch (e: Exception) { "data socket: " + (e.message ?: e.javaClass.simpleName) }
        die(why)
    }

    private fun die(why: String) {
        synchronized(this) { if (dead != null) return; dead = why }
        log("session ended: $why")
        closeAll()
        for ((_, p) in pending) { p.err = why; p.done.countDown() }
        pending.clear()
        deadLatch.countDown()
    }

    /** Blocks until the session is gone; returns why. */
    fun awaitEnd(): String { deadLatch.await(); return dead ?: "closed" }

    private fun closeAll() {
        for (s in listOf(req, ret, dat)) try { s?.close() } catch (_: Exception) {}
    }

    /** Leaving the region: the request socket closes, the RAM manager ends the session (one `m` at every member). */
    override fun close() { die("closed by this member") }

    companion object {
        fun sendFrame(o: OutputStream, f: ByteArray) { o.write(Wire.be32(f.size.toLong()) + f); o.flush() }
        fun recvFrame(i: DataInputStream): ByteArray {
            val n = i.readInt()
            if (n < 0 || n > (64 shl 20)) throw ProtocolError("frame length $n")
            return ByteArray(n).also { i.readFully(it) }
        }
    }
}
