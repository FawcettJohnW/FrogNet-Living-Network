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
package net.frognet.calendar

import android.app.Activity
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.view.Gravity
import android.view.View
import android.widget.*
import kotlin.concurrent.thread

/**
 * MainActivity — minimal agenda UI over CalendarClient.
 * Polls every POLL_MS on a worker thread; renders on the main thread.
 * Fails clean (status line) and stops polling on SplitDetected.
 */
class MainActivity : Activity() {
    private val client = CalendarClient()
    private val ui = Handler(Looper.getMainLooper())
    private val POLL_MS = 4000L
    private var connected = true

    private lateinit var status: TextView
    private lateinit var presence: TextView
    private lateinit var list: LinearLayout
    private lateinit var summary: EditText
    private lateinit var start: EditText
    private lateinit var end: EditText

    override fun onCreate(b: Bundle?) {
        super.onCreate(b)
        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(24, 24, 24, 24)
        }
        status = TextView(this).apply { text = "…" }
        presence = TextView(this).apply { setPadding(0, 0, 0, 12) }
        summary = EditText(this).apply { hint = "What?" }
        start = EditText(this).apply { hint = "Start (2026-06-10T18:00)" }
        end = EditText(this).apply { hint = "End (2026-06-10T19:00)" }
        val add = Button(this).apply { text = "Add to the family calendar" }
        list = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }

        add.setOnClickListener {
            val s = summary.text.toString().trim()
            if (s.isEmpty()) return@setOnClickListener
            val st = start.text.toString().trim(); val en = end.text.toString().trim()
            thread {
                try {
                    client.createEvent(s, st, en)
                    ui.post { summary.setText(""); start.setText(""); end.setText("") }
                    refresh()
                } catch (e: CalendarClient.Unreachable) {
                    ui.post { setStatus("can't reach calendar — contact admin") }
                }
            }
        }

        listOf(status, presence, summary, start, end, add,
               TextView(this).apply { text = "—"; gravity = Gravity.CENTER }, list)
            .forEach { root.addView(it) }
        setContentView(ScrollView(this).apply { addView(root) })

        refresh()
        ui.postDelayed(object : Runnable {
            override fun run() {
                if (connected) refresh()
                ui.postDelayed(this, POLL_MS)
            }
        }, POLL_MS)
    }

    private fun setStatus(t: String) { status.text = t }

    private fun refresh() = thread {
        try {
            val events = client.listEvents()
            val here = try { client.presence() } catch (e: Exception) { emptyList() }
            ui.post {
                connected = true
                setStatus("live")
                presence.text = if (here.isNotEmpty()) "\uD83D\uDC40 " + here.joinToString(", ") else ""
                list.removeAllViews()
                if (events.isEmpty()) {
                    list.addView(TextView(this).apply { text = "No events yet." })
                } else {
                    for (ev in events) {
                        val row = TextView(this).apply {
                            text = (if (ev.allDay) "All day" else ev.start) + "   " +
                                   ev.summary + (if (ev.location.isNotEmpty()) "  · ${ev.location}" else "")
                            setPadding(0, 10, 0, 10)
                            setOnClickListener {
                                thread {
                                    try { client.deleteEvent(ev.uid); refresh() }
                                    catch (e: Exception) { ui.post { setStatus("can't reach calendar — contact admin") } }
                                }
                            }
                        }
                        list.addView(row)
                    }
                }
            }
        } catch (e: CalendarClient.SplitDetected) {
            ui.post { connected = false; setStatus("disconnected (split detected) — contact admin") }
        } catch (e: CalendarClient.Unreachable) {
            ui.post { connected = false; setStatus("can't reach calendar — contact admin") }
        }
    }
}
