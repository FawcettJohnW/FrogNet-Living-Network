// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// Wire.kt -- [FNWP_RAW_MEMBER_V1] the FNW1 frames a member speaks, byte for byte as rinab/platform/ribbit_cpp/
// semwire.hpp and dataplane.hpp define them. A member only ever sends REQ_RAW; the RAM manager's ServerEngine answers
// every REQ_RAW with RESP_RAW (fnwp_engine.hpp ServerEngine::finish, the w.raw branch), or with OP_ERROR when the frame
// cannot be decoded. No templates, no SAME cache, no differencing: those are for high-rate RAM programs.
//
//   socket frame        [len:4 BE][frame]
//   HELLO               "FNW1" 0x50 [n:1][token:n]            token, "RETURN:"+token, "DATA:"+token
//   REQ_RAW             "FNW1" 0x03 [hash:16][len:4][http JSON]
//   return-socket frame [seq:4][FNW1 reply]                   seq = the request's position on the request socket
//   RESP_RAW            "FNW1" 0x14 [same_id:16][len:4][status:2][hlen:2][headers:hlen][body]
//   marker              a RESP_RAW whose headers carry X-FrogNet-Data-Bytes and whose body is empty
//   data-socket record  [len:4][seq:4][total:4][offset:4][bytes]   the marker's body, in segments
//   OP_ERROR            "FNW1" 0x30 [status:2][mlen:2][message]
package com.fawcettinnovations.fdf.member

import java.io.ByteArrayOutputStream
import java.security.MessageDigest

object Wire {
    val MAGIC = byteArrayOf('F'.code.toByte(), 'N'.code.toByte(), 'W'.code.toByte(), '1'.code.toByte())
    const val REQ_RAW: Int = 0x03
    const val RESP_RAW: Int = 0x14
    const val OP_ERROR: Int = 0x30
    const val OP_HELLO: Int = 0x50
    const val MARKER_HEADER = "X-FrogNet-Data-Bytes"

    fun be32(v: Long): ByteArray = byteArrayOf((v ushr 24).toByte(), (v ushr 16).toByte(), (v ushr 8).toByte(), v.toByte())
    fun rd32(b: ByteArray, o: Int): Long =
        ((b[o].toLong() and 0xff) shl 24) or ((b[o + 1].toLong() and 0xff) shl 16) or
        ((b[o + 2].toLong() and 0xff) shl 8) or (b[o + 3].toLong() and 0xff)
    fun rd16(b: ByteArray, o: Int): Int = ((b[o].toInt() and 0xff) shl 8) or (b[o + 1].toInt() and 0xff)

    fun hello(token: String): ByteArray {
        val t = token.toByteArray(Charsets.US_ASCII)
        require(t.size <= 255) { "HELLO token longer than 255 bytes" }
        return MAGIC + byteArrayOf(OP_HELLO.toByte(), t.size.toByte()) + t
    }
    fun isHello(f: ByteArray): Boolean = f.size >= 5 && f.copyOfRange(0, 4).contentEquals(MAGIC) && (f[4].toInt() and 0xff) == OP_HELLO

    // fnwp_engine.hpp detail::http_json -- the literal shape the server's ServerEngine::begin parses
    fun httpJson(method: String, path: String, body: String): String =
        "{\"method\":" + Json.quote(method) + ",\"path\":" + Json.quote(path) + ",\"headers\":{},\"body\":" +
        Json.quote(body) + ",\"host\":\"127.0.0.1\",\"port\":8080}"

    // ClientEngine::raw_locked: hash = sha256(target + NUL + http)[:16]
    fun reqRaw(target: String, method: String, path: String, body: String): ByteArray {
        val http = httpJson(method, path, body).toByteArray(Charsets.UTF_8)
        val md = MessageDigest.getInstance("SHA-256")
        md.update(target.toByteArray(Charsets.UTF_8)); md.update(0); md.update(http)
        val hash = md.digest().copyOfRange(0, 16)
        val o = ByteArrayOutputStream(25 + http.size)
        o.write(MAGIC); o.write(REQ_RAW); o.write(hash); o.write(be32(http.size.toLong())); o.write(http)
        return o.toByteArray()
    }

    class Reply(val op: Int, val status: Int, val headers: String, val body: ByteArray)

    // semwire::try_parse for the ops a member can receive; anything else is a protocol error, said as such
    fun parseReply(f: ByteArray): Reply {
        if (f.size < 5 || !f.copyOfRange(0, 4).contentEquals(MAGIC)) throw ProtocolError("reply is not FNW1")
        val op = f[4].toInt() and 0xff
        when (op) {
            RESP_RAW -> {
                if (f.size < 25) throw ProtocolError("short RESP_RAW header")
                val n = rd32(f, 21).toInt(); val p = 25
                if (f.size < p + n || n < 4) throw ProtocolError("short RESP_RAW payload")
                val status = rd16(f, p); val hl = rd16(f, p + 2)
                if (n < 4 + hl) throw ProtocolError("short RESP_RAW headers")
                val headers = String(f, p + 4, hl, Charsets.UTF_8)
                return Reply(op, status, headers, f.copyOfRange(p + 4 + hl, p + n))
            }
            OP_ERROR -> {
                if (f.size < 9) throw ProtocolError("short ERROR header")
                val status = rd16(f, 5); val n = rd16(f, 7)
                if (f.size < 9 + n) throw ProtocolError("short ERROR message")
                return Reply(op, status, "", f.copyOfRange(9, 9 + n))
            }
            else -> throw ProtocolError("op 0x%02x is not an answer to REQ_RAW".format(op))
        }
    }
    // ClientEngine::is_reply_marker / reply_from_marker
    fun markerBytes(r: Reply): Int? {
        if (r.op != RESP_RAW || !r.headers.contains(MARKER_HEADER)) return null
        val h = Json.parse(r.headers)
        return (h[MARKER_HEADER].num ?: throw ProtocolError("marker without a byte count")).toInt()
    }
}

class ProtocolError(msg: String) : Exception(msg)
