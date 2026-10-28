// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// Json.kt -- enough JSON to read what the region answers and to quote what a member sends, with no dependency (the
// member module builds on a plain JVM for its tests and on Android unchanged). Mirrors frogram::Json.
package com.fawcettinnovations.fdf.member

class Json private constructor(val type: Type) {
    enum class Type { NULL, BOOL, NUM, STR, ARR, OBJ }
    var b = false; private set
    var n = 0.0; private set
    var s = ""; private set
    val a = ArrayList<Json>()
    val o = LinkedHashMap<String, Json>()

    operator fun get(k: String): Json = o[k] ?: NONE
    operator fun get(i: Int): Json = a[i]
    val str: String get() = if (type == Type.STR) s else ""
    val num: Double? get() = if (type == Type.NUM) n else null
    val bool: Boolean get() = type == Type.BOOL && b
    val isNull: Boolean get() = type == Type.NULL
    /** this value as JSON text again */
    override fun toString(): String = when (type) {
        Type.NULL -> "null"; Type.BOOL -> b.toString(); Type.STR -> quote(s)
        Type.NUM -> if (n == Math.rint(n) && Math.abs(n) < 1e15) n.toLong().toString() else n.toString()
        Type.ARR -> a.joinToString(",", "[", "]"); Type.OBJ -> o.entries.joinToString(",", "{", "}") { quote(it.key) + ":" + it.value }
    }

    companion object {
        val NONE = Json(Type.NULL)
        fun quote(s: String): String {
            val o = StringBuilder(s.length + 2).append('"')
            for (c in s) when {
                c == '"' || c == '\\' -> o.append('\\').append(c)
                c == '\n' -> o.append("\\n"); c == '\r' -> o.append("\\r"); c == '\t' -> o.append("\\t")
                c.code < 0x20 -> o.append("\\u%04x".format(c.code))
                else -> o.append(c)
            }
            return o.append('"').toString()
        }
        fun parse(t: String): Json { val p = P(t); val v = p.v(); p.ws(); return v }
    }

    private class P(val t: String) {
        var i = 0
        fun ws() { while (i < t.length && t[i].isWhitespace()) i++ }
        fun bad(w: String): Nothing = throw ProtocolError("bad JSON ($w at $i)")
        fun v(): Json {
            ws(); if (i >= t.length) bad("eof")
            when (t[i]) {
                '{' -> { val j = Json(Type.OBJ); i++; ws(); if (t[i] == '}') { i++; return j }
                    while (true) { ws(); val k = v(); if (k.type != Type.STR) bad("key"); ws(); if (t[i++] != ':') bad(":")
                        j.o[k.s] = v(); ws(); val c = t[i++]; if (c == ',') continue; if (c == '}') return j; bad("}") } }
                '[' -> { val j = Json(Type.ARR); i++; ws(); if (t[i] == ']') { i++; return j }
                    while (true) { j.a.add(v()); ws(); val c = t[i++]; if (c == ',') continue; if (c == ']') return j; bad("]") } }
                '"' -> { val j = Json(Type.STR); i++; val sb = StringBuilder()
                    while (i < t.length && t[i] != '"') {
                        if (t[i] == '\\') { val e = t[++i]
                            when (e) { 'n' -> sb.append('\n'); 't' -> sb.append('\t'); 'r' -> sb.append('\r')
                                'b' -> sb.append('\b'); 'f' -> sb.append('\u000c')
                                'u' -> { sb.append(t.substring(i + 1, i + 5).toInt(16).toChar()); i += 4 }
                                else -> sb.append(e) }
                            i++ } else sb.append(t[i++]) }
                    i++; j.s = sb.toString(); return j }
                else -> {
                    if (t.startsWith("true", i)) { i += 4; return Json(Type.BOOL).also { it.b = true } }
                    if (t.startsWith("false", i)) { i += 5; return Json(Type.BOOL) }
                    if (t.startsWith("null", i)) { i += 4; return Json(Type.NULL) }
                    val st = i
                    while (i < t.length && (t[i].isDigit() || t[i] in "+-.eE")) i++
                    val d = t.substring(st, i).toDoubleOrNull() ?: bad("number")
                    return Json(Type.NUM).also { it.n = d }
                }
            }
        }
    }
}
