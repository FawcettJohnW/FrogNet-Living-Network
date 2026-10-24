// Copyright (C) 2016-2026 Fawcett Innovations LLC
// SPDX-License-Identifier: GPL-2.0-only
//
// Telemetry.kt -- [TELEMETRY_IS_A_TUPLE_V1] what this device is reporting, as one tuple in its forward pond's memory:
//     (pond, telem, w:<guid>)  {name, kind:"android", build, model, os, at, loc{..}, env{..}, battery{..}, thermal, net{..}, mem{..}, up_s}
// Written on the member's own session with own:true, so it is the session's: the member gone, the tuple is gone
// ([OWNED_CELL_GOES_WITH_THE_SESSION_V1]). To FrogNet it is one more family of JSON tuples; fdf-monitor and
// frognet_monitor hold a read on (pond, telem) and join it to the member by its ns.
// Written when something changed enough to matter -- moved 10 m, battery 1 %, 1 degC, 0.5 hPa, light by a factor of 2,
// another network or signal level, memory by 5 % -- and at most once every 2 s; nothing is written when nothing changed.
// The sources are events (location, sensors, battery, thermal, network callbacks); memory, storage and the cell signal
// level have no event, so they are looked at every `period_s` (default 30) and written only if they moved.
// What is reported: Identity's telem_on / telem_gps / telem_period_s ([SETTINGS_ARE_TUPLES_V1] sets them).
package com.fawcettinnovations.fdf.android

import android.Manifest
import android.app.ActivityManager
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.pm.PackageManager
import android.hardware.Sensor
import android.hardware.SensorEvent
import android.hardware.SensorEventListener
import android.hardware.SensorManager
import android.location.Location
import android.location.LocationListener
import android.location.LocationManager
import android.net.ConnectivityManager
import android.net.Network
import android.net.NetworkCapabilities
import android.net.wifi.WifiManager
import android.os.BatteryManager
import android.os.Build
import android.os.Environment
import android.os.Handler
import android.os.HandlerThread
import android.os.PowerManager
import android.os.StatFs
import android.os.SystemClock
import android.telephony.TelephonyManager
import com.fawcettinnovations.fdf.member.Json
import com.fawcettinnovations.fdf.member.MEMBER_BUILD
import com.fawcettinnovations.fdf.member.Member
import java.util.Locale

class Telemetry(private val ctx: Context, private val ident: Identity, private val log: (String) -> Unit) {
    private val th = HandlerThread("fdf-telemetry").apply { start() }
    private val h = Handler(th.looper)                       // every source and every write: one thread, in order
    @Volatile private var member: Member? = null
    private var loc: Location? = null
    private val env = HashMap<String, Float>()                // pressure_hpa, light_lux, temp_c, humidity_pct
    private var batPct = -1; private var charging = false; private var batTempC = Float.NaN
    private var thermal = -1
    private var netType = ""
    private var written: Map<String, Any?>? = null
    private var lastWriteMs = 0L
    private var pending = false
    private var sensorsOn = false
    private var writes = 0L

    /** the forward pond's member joined: report there */
    fun attach(m: Member) = h.post {
        member = m; written = null
        if (!ident.telemOn) { log("telemetry: off (telem_on false): nothing reported"); return@post }
        if (!sensorsOn) startSources()
        log("telemetry: reporting to (pond, telem, ${m.ns}) on this member's session, every change (period ${ident.telemPeriodS} s for what has no event)")
        changed("joined")
    }
    /** its session ended: the tuple went with it; nothing to write until the next session */
    fun detach() = h.post { member = null; written = null }
    fun stop() = h.post { stopSources(); member = null; th.quitSafely() }

    // ---- the sources
    private val sm get() = ctx.getSystemService(SensorManager::class.java)
    private val sensorL = object : SensorEventListener {
        override fun onSensorChanged(e: SensorEvent) {
            val k = when (e.sensor.type) { Sensor.TYPE_PRESSURE -> "pressure_hpa"; Sensor.TYPE_LIGHT -> "light_lux"
                Sensor.TYPE_AMBIENT_TEMPERATURE -> "temp_c"; Sensor.TYPE_RELATIVE_HUMIDITY -> "humidity_pct"; else -> return }
            env[k] = e.values[0]; changed(k)
        }
        override fun onAccuracyChanged(s: Sensor, a: Int) {}
    }
    private val locL = LocationListener { l -> loc = l; changed("location") }
    private val batR = object : BroadcastReceiver() {
        override fun onReceive(c: Context, i: Intent) {
            val lvl = i.getIntExtra(BatteryManager.EXTRA_LEVEL, -1); val sc = i.getIntExtra(BatteryManager.EXTRA_SCALE, 100)
            batPct = if (lvl >= 0 && sc > 0) lvl * 100 / sc else -1
            val st = i.getIntExtra(BatteryManager.EXTRA_STATUS, -1)
            charging = st == BatteryManager.BATTERY_STATUS_CHARGING || st == BatteryManager.BATTERY_STATUS_FULL
            batTempC = i.getIntExtra(BatteryManager.EXTRA_TEMPERATURE, Int.MIN_VALUE).let { if (it == Int.MIN_VALUE) Float.NaN else it / 10f }
            changed("battery")
        }
    }
    private val thermalL = PowerManager.OnThermalStatusChangedListener { s -> thermal = s; changed("thermal") }
    private val netCb = object : ConnectivityManager.NetworkCallback() {
        override fun onCapabilitiesChanged(n: Network, c: NetworkCapabilities) {
            netType = when { c.hasTransport(NetworkCapabilities.TRANSPORT_WIFI) -> "wifi"; c.hasTransport(NetworkCapabilities.TRANSPORT_CELLULAR) -> "cellular"
                c.hasTransport(NetworkCapabilities.TRANSPORT_ETHERNET) -> "ethernet"; c.hasTransport(NetworkCapabilities.TRANSPORT_VPN) -> "vpn"; else -> "other" }
            h.post { changed("network") }
        }
        override fun onLost(n: Network) { h.post { netType = "none"; changed("network") } }
    }
    private val tick = object : Runnable { override fun run() { changed("period"); h.postDelayed(this, ident.telemPeriodS * 1000L) } }

    private fun has(p: String) = ctx.checkSelfPermission(p) == PackageManager.PERMISSION_GRANTED

    private fun startSources() {
        sensorsOn = true
        val have = ArrayList<String>()
        for (t in intArrayOf(Sensor.TYPE_PRESSURE, Sensor.TYPE_LIGHT, Sensor.TYPE_AMBIENT_TEMPERATURE, Sensor.TYPE_RELATIVE_HUMIDITY)) {
            val s = sm.getDefaultSensor(t) ?: continue
            sm.registerListener(sensorL, s, SensorManager.SENSOR_DELAY_NORMAL, h); have.add(s.stringType.substringAfterLast('.'))
        }
        log("telemetry: environment sensors on this device: " + (if (have.isEmpty()) "none" else have.joinToString()))
        val f = IntentFilter(Intent.ACTION_BATTERY_CHANGED)
        if (Build.VERSION.SDK_INT >= 33) ctx.registerReceiver(batR, f, null, h, Context.RECEIVER_NOT_EXPORTED) else ctx.registerReceiver(batR, f, null, h)
        ctx.getSystemService(PowerManager::class.java).addThermalStatusListener(ctx.mainExecutor, thermalL)
        ctx.getSystemService(ConnectivityManager::class.java).registerDefaultNetworkCallback(netCb)
        if (ident.telemGps) {
            if (!has(Manifest.permission.ACCESS_FINE_LOCATION) && !has(Manifest.permission.ACCESS_COARSE_LOCATION)) log("telemetry: location: no permission -- reported without a position")
            else try {
                val lm = ctx.getSystemService(LocationManager::class.java)
                val provs = lm.getProviders(true).filter { it == LocationManager.GPS_PROVIDER || it == LocationManager.NETWORK_PROVIDER || it == LocationManager.FUSED_PROVIDER }
                for (p in provs) { lm.requestLocationUpdates(p, 5000L, 10f, locL, th.looper); lm.getLastKnownLocation(p)?.let { if (loc == null || it.time > loc!!.time) loc = it } }
                log("telemetry: location from " + (if (provs.isEmpty()) "no provider (location is off on this device)" else provs.joinToString()))
            } catch (e: SecurityException) { log("telemetry: location refused: ${e.message}") }
        } else log("telemetry: location: off (telem_gps false)")
        h.postDelayed(tick, ident.telemPeriodS * 1000L)
    }

    private fun stopSources() {
        if (!sensorsOn) return
        sensorsOn = false
        runCatching { sm.unregisterListener(sensorL) }; runCatching { ctx.unregisterReceiver(batR) }
        runCatching { ctx.getSystemService(PowerManager::class.java).removeThermalStatusListener(thermalL) }
        runCatching { ctx.getSystemService(ConnectivityManager::class.java).unregisterNetworkCallback(netCb) }
        runCatching { ctx.getSystemService(LocationManager::class.java).removeUpdates(locL) }
        h.removeCallbacks(tick)
    }

    // ---- what is reported, and when
    private fun snapshot(): LinkedHashMap<String, Any?> {
        val m = LinkedHashMap<String, Any?>()
        m["name"] = ident.name; m["kind"] = "android"; m["build"] = MEMBER_BUILD
        m["model"] = Build.MANUFACTURER + " " + Build.MODEL; m["os"] = "Android " + Build.VERSION.RELEASE + " (API " + Build.VERSION.SDK_INT + ")"
        loc?.let { l -> m["loc"] = linkedMapOf("lat" to l.latitude, "lon" to l.longitude, "acc_m" to l.accuracy.toDouble(),
            "alt_m" to (if (l.hasAltitude()) l.altitude else null), "speed_mps" to (if (l.hasSpeed()) l.speed.toDouble() else null),
            "bearing" to (if (l.hasBearing()) l.bearing.toDouble() else null), "provider" to l.provider, "fix_at" to l.time / 1000.0) }
        if (env.isNotEmpty()) m["env"] = LinkedHashMap<String, Any?>(env.mapValues { it.value.toDouble() })
        if (batPct >= 0) m["battery"] = linkedMapOf("pct" to batPct, "charging" to charging, "temp_c" to (if (batTempC.isNaN()) null else batTempC.toDouble()))
        if (thermal >= 0) m["thermal"] = thermal
        val net = linkedMapOf<String, Any?>("type" to netType)
        runCatching { ctx.getSystemService(TelephonyManager::class.java)?.let { tm -> net["carrier"] = tm.networkOperatorName; if (Build.VERSION.SDK_INT >= 28) tm.signalStrength?.let { net["cell_level"] = it.level } } }
        @Suppress("DEPRECATION") runCatching { if (netType == "wifi") ctx.applicationContext.getSystemService(WifiManager::class.java)?.connectionInfo?.let { net["wifi_rssi"] = it.rssi } }
        m["net"] = net
        val mi = ActivityManager.MemoryInfo(); ctx.getSystemService(ActivityManager::class.java).getMemoryInfo(mi)
        val st = StatFs(Environment.getDataDirectory().path)
        m["mem"] = linkedMapOf("total_mb" to mi.totalMem / 1048576, "avail_mb" to mi.availMem / 1048576, "storage_free_mb" to st.availableBytes / 1048576)
        m["cores"] = Runtime.getRuntime().availableProcessors()
        m["up_s"] = SystemClock.elapsedRealtime() / 1000
        return m
    }

    /** enough changed since the tuple last written to say it again */
    private fun moved(a: Map<String, Any?>, b: Map<String, Any?>?): String? {
        if (b == null) return "first"
        fun d(x: Any?, y: Any?) = ((x as? Number)?.toDouble() ?: Double.NaN) - ((y as? Number)?.toDouble() ?: Double.NaN)
        @Suppress("UNCHECKED_CAST") fun g(m: Map<String, Any?>, k: String) = (m[k] as? Map<String, Any?>) ?: emptyMap()
        val la = g(a, "loc"); val lb = g(b, "loc")
        if (la.isNotEmpty() != lb.isNotEmpty()) return "location"
        if (la.isNotEmpty()) { val r = FloatArray(1); Location.distanceBetween(la["lat"] as Double, la["lon"] as Double, lb["lat"] as Double, lb["lon"] as Double, r); if (r[0] >= 10f) return "moved ${r[0].toInt()} m" }
        val ea = g(a, "env"); val eb = g(b, "env")
        if (ea.keys != eb.keys) return "sensors"
        if (Math.abs(d(ea["pressure_hpa"], eb["pressure_hpa"])) >= 0.5) return "pressure"
        if (Math.abs(d(ea["temp_c"], eb["temp_c"])) >= 1.0 || Math.abs(d(ea["humidity_pct"], eb["humidity_pct"])) >= 3.0) return "temperature/humidity"
        val lx = (ea["light_lux"] as? Double); val ly = (eb["light_lux"] as? Double)
        if (lx != null && ly != null && (Math.max(lx, 1.0) / Math.max(ly, 1.0) >= 2.0 || Math.max(ly, 1.0) / Math.max(lx, 1.0) >= 2.0)) return "light"
        val ba = g(a, "battery"); val bb = g(b, "battery")
        if (ba["pct"] != bb["pct"] || ba["charging"] != bb["charging"] || Math.abs(d(ba["temp_c"], bb["temp_c"])) >= 1.0) return "battery"
        if (a["thermal"] != b["thermal"]) return "thermal"
        val na = g(a, "net"); val nb = g(b, "net")
        if (na["type"] != nb["type"] || na["cell_level"] != nb["cell_level"] || na["carrier"] != nb["carrier"] || Math.abs(d(na["wifi_rssi"], nb["wifi_rssi"])) >= 10) return "network"
        val ma = g(a, "mem"); val mb = g(b, "mem")
        if (Math.abs(d(ma["avail_mb"], mb["avail_mb"])) >= 0.05 * ((ma["total_mb"] as? Number)?.toDouble() ?: 1.0)) return "memory"
        return null
    }

    private fun changed(why: String) {
        val m = member ?: return
        if (!ident.telemOn) return
        val now = SystemClock.elapsedRealtime()
        if (now - lastWriteMs < 2000) { if (!pending) { pending = true; h.postDelayed({ pending = false; changed(why) }, 2000 - (now - lastWriteMs)) }; return }
        val snap = snapshot()
        val what = moved(snap, written) ?: return
        snap["at"] = System.currentTimeMillis() / 1000.0
        try {
            if (m.write("pond", "telem", m.ns, toJson(snap), own = true) < 0) return
            written = snap; lastWriteMs = now; writes++
            if (writes <= 3 || writes % 100 == 0L) log("telemetry: written (#$writes, $what): ${toJson(snap).take(240)}")
        } catch (e: Exception) { log("telemetry: write failed ($what): ${e.message} -- written again on the next change") }
    }

    companion object {
        fun toJson(v: Any?): String = when (v) {
            null -> "null"
            is String -> Json.quote(v)
            is Boolean -> v.toString()
            is Int, is Long -> v.toString()
            is Number -> v.toDouble().let { if (it.isNaN() || it.isInfinite()) "null" else String.format(Locale.ROOT, "%.10g", it).trim() }
            is Map<*, *> -> v.entries.joinToString(",", "{", "}") { Json.quote(it.key.toString()) + ":" + toJson(it.value) }
            is List<*> -> v.joinToString(",", "[", "]") { toJson(it) }
            else -> Json.quote(v.toString())
        }
    }
}
