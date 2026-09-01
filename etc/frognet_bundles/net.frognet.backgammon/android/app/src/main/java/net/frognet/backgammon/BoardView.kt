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

import android.content.Context
import android.graphics.*
import android.view.MotionEvent
import android.view.View

/** Draws the board from a BackgammonClient.State and reports taps. The same geometry
 *  as the desktop/web clients; visuals tuned for a real-set look. */
class BoardView(context: Context) : View(context) {
    var state: BackgammonClient.State? = null
    var sel: Int? = null
    var onPickSource: (Int) -> Unit = {}
    var onChooseDest: (Int) -> Unit = {}
    var onBearOff: () -> Unit = {}

    private val VBW = 940f; private val VBH = 600f
    private val M = 24f; private val BAR = 60f; private val TRAY = 66f; private val R = 19f; private val PT = 232f
    private val p = Paint(Paint.ANTI_ALIAS_FLAG)
    private val txt = Paint(Paint.ANTI_ALIAS_FLAG).apply { textAlign = Paint.Align.CENTER }

    private val WALNUT = Color.parseColor("#3a2417"); private val FELT = Color.parseColor("#1f4a3f")
    private val PTL = Color.parseColor("#e8d8b8"); private val PTD = Color.parseColor("#9c3b2e")
    private val BONE = Color.parseColor("#f3ead6"); private val BONE_E = Color.parseColor("#bfae8a")
    private val EBONY = Color.parseColor("#23211f"); private val BRASS = Color.parseColor("#e9d29a")
    private val BRASS_D = Color.parseColor("#8a6d22"); private val GLOW = Color.parseColor("#ffd66e")

    private var scale = 1f; private var ox = 0f; private var oy = 0f
    private val fieldX get() = M; private val fieldW get() = VBW - 2*M - TRAY
    private val half get() = (fieldW - BAR) / 2; private val colW get() = half / 6
    private val barX get() = fieldX + half

    override fun onMeasure(w: Int, h: Int) {
        val width = MeasureSpec.getSize(w); setMeasuredDimension(width, (width * VBH / VBW).toInt())
    }
    override fun onSizeChanged(w: Int, h: Int, ow: Int, oh: Int) {
        scale = w / VBW; ox = 0f; oy = 0f
    }
    private fun sx(x: Float) = ox + x*scale
    private fun sy(y: Float) = oy + y*scale
    private fun ss(v: Float) = v*scale

    private fun pointGeom(i: Int): Pair<Float, Boolean> {
        val top: Boolean; val lb: Boolean; val slot: Int
        if (i >= 13) { top = true; if (i <= 18) { lb = true; slot = i-13 } else { lb = false; slot = i-19 } }
        else { top = false; if (i >= 7) { lb = true; slot = 12-i } else { lb = false; slot = 6-i } }
        val blockX = fieldX + if (lb) 0f else half + BAR
        return Pair(blockX + colW*slot + colW/2, top)
    }
    private fun checkerY(k: Int, top: Boolean) =
        if (top) M + 8 + R + k*(R*1.7f) else VBH - M - 8 - R - k*(R*1.7f)

    override fun onDraw(c: Canvas) {
        val s = state ?: return
        p.style = Paint.Style.FILL
        fun rect(x0: Float,y0: Float,x1: Float,y1: Float,col: Int){ p.color=col
            c.drawRect(sx(x0),sy(y0),sx(x1),sy(y1),p) }
        rect(0f,0f,VBW,VBH,WALNUT); rect(fieldX,M,fieldX+fieldW,VBH-M,FELT)
        rect(barX,M,barX+BAR,VBH-M,WALNUT); rect(VBW-M-TRAY,M,VBW-M,VBH-M,WALNUT)

        val legalFroms = s.legal.map { it.from }.toSet()
        val selDests = sel?.let { sv -> s.legal.filter { it.from == sv }.map { it.to }.toSet() } ?: emptySet()
        for (i in 1..24) {
            val (x, top) = pointGeom(i); val light = i % 2 == 0
            val tipY = if (top) M+PT else VBH-M-PT; val baseY = if (top) M else VBH-M
            p.color = if (light) PTL else PTD
            val path = Path().apply { moveTo(sx(x-colW/2+4), sy(baseY)); lineTo(sx(x+colW/2-4), sy(baseY))
                lineTo(sx(x), sy(tipY)); close() }
            c.drawPath(path, p)
            if (i in legalFroms) { p.color = GLOW; p.style = Paint.Style.STROKE; p.strokeWidth = ss(3f)
                c.drawPath(path, p); p.style = Paint.Style.FILL }
            if (i in selDests) { p.color = GLOW
                c.drawCircle(sx(x), sy(if (top) M+PT-26 else VBH-M-PT+26), ss(11f), p) }
        }
        for (i in 1..24) {
            val v = s.points[i]; if (v == 0) continue
            val (x, top) = pointGeom(i); val white = v > 0; val n = Math.abs(v)
            for (k in 0 until minOf(n, 5)) drawChecker(c, x, checkerY(k, top), white)
            if (n > 5) { txt.color = if (white) Color.parseColor("#3a2c10") else BONE
                txt.textSize = ss(15f); c.drawText("×$n", sx(x), sy(checkerY(4, top))+ss(5f), txt) }
        }
        for (k in 0 until s.barW) drawChecker(c, barX+BAR/2, VBH/2-30-k*8, true)
        for (k in 0 until s.barB) drawChecker(c, barX+BAR/2, VBH/2+30+k*8, false)
        val trayX = VBW-M-TRAY/2
        p.color = BONE; for (k in 0 until s.offW) c.drawRect(sx(trayX-22), sy(VBH-M-12-k*9), sx(trayX+22), sy(VBH-M-5-k*9), p)
        p.color = EBONY; for (k in 0 until s.offB) c.drawRect(sx(trayX-22), sy(M+6+k*9), sx(trayX+22), sy(M+13+k*9), p)
        if (s.dice.isNotEmpty()) {
            val dx = if (s.turn == "w") barX-150 else barX+BAR+86
            s.dice.forEachIndexed { idx, d -> drawDie(c, dx+idx*58, VBH/2-23, d, s.turn == "b") }
        }
        p.color = BRASS; c.drawRect(sx(trayX-22), sy(VBH/2-22), sx(trayX+22), sy(VBH/2+22), p)
        p.color = BRASS_D; p.style = Paint.Style.STROKE; p.strokeWidth = ss(2f)
        c.drawRect(sx(trayX-22), sy(VBH/2-22), sx(trayX+22), sy(VBH/2+22), p); p.style = Paint.Style.FILL
        txt.color = Color.parseColor("#4a3a14"); txt.textSize = ss(18f)
        c.drawText((if (s.cubeValue == 1) 64 else s.cubeValue).toString(), sx(trayX), sy(VBH/2)+ss(6f), txt)
        sel?.let { sv -> if (s.legal.any { it.from == sv && it.bear }) {
            val ty = if (s.turn == "w") VBH-M-40 else M+40; p.color = GLOW
            c.drawRect(sx(trayX-30), sy(ty-16), sx(trayX+30), sy(ty+16), p)
            txt.color = Color.parseColor("#3a2c10"); txt.textSize = ss(11f)
            c.drawText("bear off", sx(trayX), sy(ty)+ss(4f), txt) } }
    }

    private fun drawChecker(c: Canvas, x: Float, y: Float, white: Boolean) {
        p.color = if (white) BONE else EBONY; c.drawCircle(sx(x), sy(y), ss(R), p)
        p.color = if (white) BONE_E else Color.BLACK; p.style = Paint.Style.STROKE; p.strokeWidth = ss(2f)
        c.drawCircle(sx(x), sy(y), ss(R), p)
        p.color = if (white) Color.parseColor("#d8c9a4") else Color.parseColor("#3a3a3a"); p.strokeWidth = ss(1f)
        c.drawCircle(sx(x), sy(y), ss(R-6), p); p.style = Paint.Style.FILL
    }
    private fun drawDie(c: Canvas, x: Float, y: Float, v: Int, dark: Boolean) {
        p.color = if (dark) Color.parseColor("#1c1a18") else BONE
        c.drawRoundRect(RectF(sx(x), sy(y), sx(x+46), sy(y+46)), ss(8f), ss(8f), p)
        p.color = if (dark) Color.parseColor("#e8e2d4") else Color.parseColor("#26211a")
        val a = 12f; val m = 23f; val b = 34f
        val pips = when (v) { 1->listOf(m to m); 2->listOf(a to a,b to b); 3->listOf(a to a,m to m,b to b)
            4->listOf(a to a,b to a,a to b,b to b); 5->listOf(a to a,b to a,m to m,a to b,b to b)
            6->listOf(a to a,b to a,a to m,b to m,a to b,b to b); else->emptyList() }
        for ((px, py) in pips) c.drawCircle(sx(x+px), sy(y+py), ss(4f), p)
    }

    override fun onTouchEvent(e: MotionEvent): Boolean {
        if (e.action != MotionEvent.ACTION_DOWN) return true
        val s = state ?: return true
        val ux = (e.x - ox) / scale; val uy = (e.y - oy) / scale
        val trayX = VBW-M-TRAY/2
        // bear-off target
        sel?.let { sv -> if (s.legal.any { it.from == sv && it.bear }) {
            val ty = if (s.turn == "w") VBH-M-40 else M+40
            if (ux in (trayX-30)..(trayX+30) && uy in (ty-16)..(ty+16)) { onBearOff(); return true } } }
        // points (sources + destinations)
        for (i in 1..24) {
            val (x, top) = pointGeom(i)
            val inX = ux in (x-colW/2)..(x+colW/2)
            val inY = if (top) uy in M..(M+PT) else uy in (VBH-M-PT)..(VBH-M)
            if (inX && inY) {
                val selDests = sel?.let { sv -> s.legal.filter { it.from == sv }.map { it.to }.toSet() } ?: emptySet()
                if (i in selDests) onChooseDest(i)
                else if (s.legal.any { it.from == i }) onPickSource(i)
                return true
            }
        }
        // bar (entering from bar = source 0)
        if (sel == null && s.legal.any { it.from == 0 } &&
            ux in barX..(barX+BAR) && uy in (VBH/2-50)..(VBH/2+50)) onPickSource(0)
        return true
    }
}
