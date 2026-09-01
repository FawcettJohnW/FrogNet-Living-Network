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
using System.Text.Json;

namespace FrogNet.Monitor;

/// <summary>
/// The model behind the dashboard. No console in this file — the numbers and
/// their thresholds are testable without a terminal, which is why they live
/// here and not in the drawing code.
///
/// The C++ port renders the same data as a dense operations table. This one
/// renders node cards. Deliberately different: the data is a property of the
/// shared memory, not of any one presentation of it, and two unlike views make
/// that point better than two identical ones.
/// </summary>
public static class Model
{
    /// <summary>
    /// <c>&lt;domain&gt;.System.Perf</c> — what the machine is doing while it
    /// serves. A node can answer every probe promptly and still be in trouble;
    /// the link numbers cannot see that and these can.
    /// </summary>
    public sealed class Perf
    {
        public bool Have;
        public double CpuBusy;      // cpu_pct.total_busy
        public double CpuIoWait;    // cpu_pct.iowait — disk pressure inside "busy"
        public double Load1;        // loadavg."1"
        public double MemPct;       // mem_kb.pct_used
        public double SwapPct;      // swap_kb.pct_used
        public double RxBytesSec;   // net_io.total.rx_bytes_per_s
        public double TxBytesSec;   // net_io.total.tx_bytes_per_s
        public double DiskRBytesSec;// disk_io.total.read_bytes_per_s
        public double DiskWBytesSec;// disk_io.total.write_bytes_per_s
        public double TempC;        // hottest entry in temps_c
        public double AgeSeconds;   // now - timestamp

        /// <summary>
        /// A reading older than this describes a node as it was, not as it is.
        /// Drawing a stale sample as a live meter is the same error as printing
        /// "0ms" for a measurement that was never taken.
        /// </summary>
        public bool Stale => Have && AgeSeconds > 120;
        public bool Usable => Have && !Stale;
    }

    public sealed class Node
    {
        public string Ip = "";
        public string Name = "";
        public bool EchoOk;
        public bool IsDbHost;
        public bool IsLocal;
        public double RttMs, P50Ms, P95Ms, HitRate, EffBps, ActualBps;
        public long BytesSaved;
        public bool HaveLinkQuality;
        public double SatP95, LqRttP95;
        public Perf Perf = new();

        /// <summary>
        /// First match wins. Echo failure outranks everything; a saturated link
        /// outranks a good RTT. A missing LinkQuality entry means the proxy has
        /// not spoken to this peer in the current window — that is not a
        /// failure and must not be reported as one, so it degrades to the RTT
        /// actually held rather than inventing a saturation figure.
        /// </summary>
        public PeerState State
        {
            get
            {
                if (!EchoOk) return PeerState.Down;
                if (HaveLinkQuality)
                {
                    if (SatP95 >= 0.90) return PeerState.Sat;
                    if (SatP95 >= 0.70) return PeerState.Busy;
                    if (LqRttP95 > 1000) return PeerState.Slow;
                    if (LqRttP95 > 250) return PeerState.Fair;
                    return PeerState.Ok;
                }
                var r = P95Ms > 0 ? P95Ms : RttMs;
                if (r <= 0) return PeerState.Unknown;
                if (r > 1000) return PeerState.Slow;
                if (r > 250) return PeerState.Fair;
                return PeerState.Ok;
            }
        }

        /// <summary>
        /// The single number the card headline uses for "how hard is this node
        /// working" — the worse of CPU and memory, because either one at 95%
        /// is a node in trouble and averaging them hides it.
        /// </summary>
        public double LoadFraction =>
            Perf.Usable ? Math.Max(Perf.CpuBusy, Perf.MemPct) / 100.0 : -1;
    }

    public enum PeerState { Down, Sat, Busy, Slow, Fair, Ok, Unknown }

    public sealed class Snapshot
    {
        public string Domain = "";
        public string DbHostIp = "";
        public List<Node> Nodes = new();
        public bool MergeKnown;      // false = the sentinel could not be read,
        public bool MergePending;    //         which is neither pending nor absent
        public double MergeAgeSeconds;
        public long Generation;
        public DateTime TakenAt = DateTime.UtcNow;

        /// <summary>
        /// Why this snapshot has no nodes, when it has none. An empty pond and
        /// an unreachable one paint identically otherwise, and the second is the
        /// common case on a client that was pointed at the wrong address.
        /// </summary>
        public string? Notice;
        public int FailedRefreshes;
    }

    // ---- formatting ------------------------------------------------------
    // Zero is not a measurement. An em dash says nothing was read; "0ms" claims
    // an instantaneous link and "0%" claims an idle one.

    public static string Rtt(double ms) =>
        ms <= 0 ? "\u2014" : (ms < 10 ? $"{ms:0.0}ms" : $"{ms:0}ms");

    public static string Pct(double frac) => frac <= 0 ? "\u2014" : $"{frac * 100:0}%";

    public static string Bytes(long n)
    {
        if (n <= 0) return "\u2014";
        string[] u = { "B", "K", "M", "G", "T" };
        double v = n;
        var i = 0;
        while (v >= 1024 && i < u.Length - 1) { v /= 1024; i++; }
        return v < 10 && i > 0 ? $"{v:0.0}{u[i]}" : $"{v:0}{u[i]}";
    }

    public static string Bps(double bps)
    {
        if (bps <= 0) return "\u2014";
        string[] u = { "bps", "Kbps", "Mbps", "Gbps" };
        double v = bps;
        var i = 0;
        while (v >= 1000 && i < u.Length - 1) { v /= 1000; i++; }
        return v < 10 && i > 0 ? $"{v:0.0}{u[i]}" : $"{v:0}{u[i]}";
    }

    // ---- parsing ---------------------------------------------------------

    private static double Num(JsonElement o, string key)
    {
        if (o.ValueKind != JsonValueKind.Object) return 0;
        if (!o.TryGetProperty(key, out var v)) return 0;
        return v.ValueKind switch
        {
            JsonValueKind.Number => v.GetDouble(),
            JsonValueKind.String => double.TryParse(v.GetString(), out var d) ? d : 0,
            _ => 0
        };
    }

    private static JsonElement? Obj(JsonElement o, string key) =>
        o.ValueKind == JsonValueKind.Object && o.TryGetProperty(key, out var v) ? v : null;

    /// <summary>Fill a Perf from a System.Perf jsonData payload.</summary>
    public static Perf ParsePerf(JsonElement jd, DateTime nowUtc)
    {
        var p = new Perf();
        if (jd.ValueKind != JsonValueKind.Object) return p;
        p.Have = true;

        var cpu = Obj(jd, "cpu_pct");
        if (cpu is not null) { p.CpuBusy = Num(cpu.Value, "total_busy"); p.CpuIoWait = Num(cpu.Value, "iowait"); }

        var la = Obj(jd, "loadavg");
        if (la is not null) p.Load1 = Num(la.Value, "1");

        var mk = Obj(jd, "mem_kb");
        if (mk is not null) p.MemPct = Num(mk.Value, "pct_used");

        var sk = Obj(jd, "swap_kb");
        if (sk is not null) p.SwapPct = Num(sk.Value, "pct_used");

        var net = Obj(jd, "net_io");
        if (net is not null)
        {
            var t = Obj(net.Value, "total");
            if (t is not null) { p.RxBytesSec = Num(t.Value, "rx_bytes_per_s"); p.TxBytesSec = Num(t.Value, "tx_bytes_per_s"); }
        }

        var disk = Obj(jd, "disk_io");
        if (disk is not null)
        {
            var t = Obj(disk.Value, "total");
            if (t is not null) { p.DiskRBytesSec = Num(t.Value, "read_bytes_per_s"); p.DiskWBytesSec = Num(t.Value, "write_bytes_per_s"); }
        }

        // The hottest sensor is the one that matters; an average across a board
        // with one hot core reads comfortable while that core throttles.
        if (jd.TryGetProperty("temps_c", out var temps) && temps.ValueKind == JsonValueKind.Array)
            foreach (var e in temps.EnumerateArray())
            {
                var c = Num(e, "temp_c");
                if (c > p.TempC) p.TempC = c;
            }

        var ts = Num(jd, "timestamp");
        if (ts > 0)
            p.AgeSeconds = (nowUtc - DateTimeOffset.FromUnixTimeSeconds((long)ts).UtcDateTime).TotalSeconds;

        return p;
    }
}
