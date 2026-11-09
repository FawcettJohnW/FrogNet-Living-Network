// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// Select.kt -- [APP_SELECTION_V1] fdf_select.py's selection, line for line, so a phone, a PC and a node choose the same
// host from the same capability tuples. A spec is {"need": [keys], "rank": [keys, "-key" lower first]}; a member whose
// tuple lacks a ranked key, or whose value is not a number, ranks below every member that has it (nothing is
// defaulted); the guid breaks the last tie, highest first. No spec: the most memory, then the most cores.
package com.fawcettinnovations.fdf.member

class SelectSpec(val need: List<String> = emptyList(), val rank: List<String> = listOf("mem_total_kb", "cores")) {
    companion object {
        val DEFAULT = SelectSpec()
        /** the spec for `ram` in an app's "select" (its RAM, else "*", else the default) */
        fun of(select: Json, ram: String): SelectSpec {
            val s = select[ram].takeIf { !it.isNull } ?: select["*"].takeIf { !it.isNull } ?: return DEFAULT
            return SelectSpec(s["need"].a.map { it.str }, s["rank"].a.map { it.str })
        }
    }
}

object Select {
    private fun num(v: Json): Double? = when {
        v.isNull -> null
        v.type == Json.Type.BOOL -> if (v.bool) 1.0 else 0.0
        else -> v.num ?: v.str.toDoubleOrNull()
    }

    private fun truthy(v: Json): Boolean = !v.isNull && when {
        v.type == Json.Type.BOOL -> v.bool
        v.num != null -> v.num != 0.0
        else -> v.str.isNotEmpty() && v.str != "0" && v.str != "false"
    }

    /** the members that can serve `ram`: they offer it (their "serve", when they have one) and have every need */
    fun candidates(s: SelectSpec, ram: String, cands: List<Pair<String, Json>>): List<Pair<String, Json>> =
        cands.filter { (_, b) ->
            val serve = b["serve"]
            (serve.isNull || serve.a.any { it.str == ram }) && s.need.all { truthy(b[it]) }
        }

    private fun cmp(s: SelectSpec, x: Pair<String, Json>, y: Pair<String, Json>): Int {
        for (k in s.rank) {
            val down = k.startsWith("-"); val key = k.removePrefix("-")
            val a = num(x.second[key]); val b = num(y.second[key])
            if (a == null && b == null) continue
            if (a == null) return -1
            if (b == null) return 1
            val c = if (down) b.compareTo(a) else a.compareTo(b)
            if (c != 0) return c
        }
        return x.first.compareTo(y.first)
    }

    /** cands: (guid, capability tuple) of the alive members. The guid that serves `ram`, or null. */
    fun select(s: SelectSpec, ram: String, cands: List<Pair<String, Json>>): String? =
        candidates(s, ram, cands).maxWithOrNull { x, y -> cmp(s, x, y) }?.first

    fun explain(s: SelectSpec, ram: String, cands: List<Pair<String, Json>>): String =
        candidates(s, ram, cands).sortedWith { x, y -> cmp(s, y, x) }.take(3).joinToString("; ") { (g, b) ->
            g + "(" + s.rank.joinToString(",") { k -> val n = k.removePrefix("-"); "$n=" + (if (b[n].isNull) "absent" else b[n].toString()) } + ")"
        }
}
