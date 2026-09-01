/***************************************************************
 *  Copyright (C) 2016-2026 Fawcett Innovations LLC            *
 *                                                             *
 *  SPDX-License-Identifier: GPL-2.0-only                      *
 *                                                             *
 *  This program is free software; you can redistribute it     *
 *  and/or modify it under the terms of the GNU General Public *
 *  License as published by the Free Software Foundation;      *
 *  version 2 of the License, and no other version.            *
 *                                                             *
 *  This program is distributed in the hope that it will be    *
 *  useful, but WITHOUT ANY WARRANTY; without even the implied *
 *  warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR    *
 *  PURPOSE.  See the GNU General Public License for details.  *
 *                                                             *
 *  See COPYRIGHT and LICENSE at the root of this tree.        *
 **************************************************************/
package net.frognet.backgammon

import android.app.Activity
import android.graphics.Color
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.view.Gravity
import android.widget.*
import kotlin.concurrent.thread

class MainActivity : Activity() {
    private val client = BackgammonClient()      // edit base/table to join a mesh table
    private val ui = Handler(Looper.getMainLooper())
    private var connected = true
    private lateinit var board: BoardView
    private lateinit var status: TextView
    private lateinit var turn: TextView
    private lateinit var controls: LinearLayout

    override fun onCreate(b: Bundle?) {
        super.onCreate(b)
        val root = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL
            setBackgroundColor(Color.parseColor("#120d08")); setPadding(16,24,16,16) }
        status = TextView(this).apply { setTextColor(Color.parseColor("#b8a98c")); text = "…" }
        board = BoardView(this).apply {
            onPickSource = { i -> sel = if (sel == i) null else i; invalidate() }
            onChooseDest = { to -> val sv = sel; sel = null
                state?.legal?.firstOrNull { it.from == sv && it.to == to }?.let { mv -> doMove(mv.from, mv.die) } }
            onBearOff = { val sv = sel; sel = null
                state?.legal?.firstOrNull { it.from == sv && it.bear }?.let { mv -> doMove(mv.from, mv.die) } }
        }
        turn = TextView(this).apply { setTextColor(Color.parseColor("#b8a98c")); setPadding(0,14,0,8) }
        controls = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL; gravity = Gravity.CENTER }
        listOf(status, board, turn, controls).forEach { root.addView(it) }
        setContentView(ScrollView(this).apply { addView(root) })
        refresh()
        ui.postDelayed(object : Runnable { override fun run() {
            if (connected) refresh(); ui.postDelayed(this, 1500) } }, 1500)
    }

    private fun mkBtn(label: String, fn: () -> Unit, primary: Boolean = true) = Button(this).apply {
        text = label; isAllCaps = false
        setTextColor(if (primary) Color.parseColor("#1c130a") else Color.parseColor("#efe7d6"))
        setBackgroundColor(if (primary) Color.parseColor("#e9d29a") else Color.parseColor("#2a2018"))
        setOnClickListener { fn() }
    }

    private fun rebuildControls(phase: String) {
        controls.removeAllViews()
        when (phase) {
            "roll" -> { controls.addView(mkBtn("Roll", { act { client.roll() } }))
                        controls.addView(mkBtn("Double", { act { client.offerDouble() } }, false)) }
            "double-offered" -> { controls.addView(mkBtn("Accept", { act { client.acceptDouble() } }))
                        controls.addView(mkBtn("Decline", { act { client.declineDouble() } }, false)) }
            "move" -> controls.addView(mkBtn("Clear", { board.sel = null; board.invalidate() }, false))
        }
        controls.addView(mkBtn("New game", { act { client.newGame() } }, false))
    }

    private fun act(call: () -> BackgammonClient.State) = thread {
        try { call(); board.sel = null; refresh() }
        catch (e: Exception) { ui.post { connected = false; status.text = "can't reach the table — contact admin" } }
    }
    private fun doMove(from: Int, die: Int) = thread {
        try { client.move(from, die); refresh() }
        catch (e: Exception) { ui.post { status.text = "can't reach the table — contact admin" } }
    }

    private fun refresh() = thread {
        try {
            val s = client.get()
            val here = try { client.presence() } catch (e: Exception) { emptyList() }
            ui.post {
                connected = true; board.state = s; board.invalidate()
                status.text = if (s.phase == "gameover") "${s.run { if (winner=="w") whiteName else blackName }} wins" else "live"
                val name = if (s.turn == "w") s.whiteName else s.blackName
                turn.text = if (s.phase == "gameover") "" else
                    "$name to ${if (s.phase=="roll") "roll" else "play"}" +
                    if (s.phase=="move") "   dice: ${s.diceRemaining.joinToString(" ")}" else ""
                rebuildControls(s.phase)
            }
        } catch (e: BackgammonClient.SplitDetected) {
            ui.post { connected = false; status.text = "disconnected (split detected)" }
        } catch (e: BackgammonClient.Unreachable) {
            ui.post { connected = false; status.text = "can't reach the table — contact admin" }
        }
    }
}
