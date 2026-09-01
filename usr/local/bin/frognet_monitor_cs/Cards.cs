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
using System.Text;

namespace FrogNet.Monitor;

/// <summary>
/// The card dashboard.
///
/// Deliberately not the C++ table. Each node gets a block with a load headline,
/// meters underneath, and the link figures on one line — the shape that reads
/// well when you are looking at four to eight nodes and want to know which one
/// is in trouble, rather than comparing a column across twenty.
///
/// Hand-rolled ANSI, no console library, because the project ships with no
/// packages and that is the point. Windows Terminal and every modern console
/// handle these sequences once virtual terminal processing is on, which
/// <see cref="EnableAnsi"/> arranges.
/// </summary>
public static class Cards
{
    private const string Reset = "\u001b[0m";
    private const string Bold = "\u001b[1m";
    private const string Dim = "\u001b[2m";

    // 256-colour, not truecolor: 256 works over ssh to a Pi and in older
    // consoles, and nothing here needs a finer palette than that.
    private static string Fg(int c) => $"\u001b[38;5;{c}m";
    private const int CGood = 78, CWarn = 214, CBad = 203, CDimC = 240,
                      CHead = 81, CText = 252, CAccent = 141;

    public static void EnableAnsi()
    {
        // On Windows, VT sequences are off by default for a console handle.
        // Nothing to do on Unix. Failure here is not fatal: the dashboard
        // degrades to visible escape codes rather than not running.
        if (!OperatingSystem.IsWindows()) return;
        try
        {
            var h = NativeWin.GetStdHandle(-11);
            if (NativeWin.GetConsoleMode(h, out var mode))
                NativeWin.SetConsoleMode(h, mode | 0x0004);
        }
        catch { /* older console; sequences may simply not render */ }
    }

    private static int LoadColour(double frac) =>
        frac < 0 ? CDimC : frac < 0.60 ? CGood : frac < 0.85 ? CWarn : CBad;

    private static int TempColour(double c) =>
        c <= 0 ? CDimC : c < 60 ? CGood : c < 75 ? CWarn : CBad;

    private static int StateColour(Model.PeerState s) => s switch
    {
        Model.PeerState.Ok => CGood,
        Model.PeerState.Fair or Model.PeerState.Slow or Model.PeerState.Busy => CWarn,
        Model.PeerState.Down or Model.PeerState.Sat => CBad,
        _ => CDimC
    };

    private static string StateText(Model.PeerState s) => s switch
    {
        Model.PeerState.Down => "DOWN",
        Model.PeerState.Sat => "SAT",
        Model.PeerState.Busy => "BUSY",
        Model.PeerState.Slow => "SLOW",
        Model.PeerState.Fair => "FAIR",
        Model.PeerState.Ok => "OK",
        _ => "?"
    };

    /// <summary>
    /// A meter in eighth-blocks. Whole-cell resolution cannot show the
    /// difference between 61% and 74% on a short bar, which is exactly the band
    /// where a node stops being comfortable.
    /// </summary>
    public static string Meter(double frac, int width)
    {
        if (width <= 0) return "";
        frac = Math.Clamp(frac, 0, 1);
        var eighths = new[] { "", "\u258f", "\u258e", "\u258d", "\u258c", "\u258b", "\u258a", "\u2589" };
        var cells = frac * width;
        var full = (int)cells;
        var rem = (int)((cells - full) * 8);
        var sb = new StringBuilder();
        for (var i = 0; i < full && i < width; i++) sb.Append('\u2588');
        if (full < width && rem > 0) { sb.Append(eighths[rem]); full++; }
        for (var i = full; i < width; i++) sb.Append('\u00b7');
        return sb.ToString();
    }

    /// <summary>Display width in codepoints — the meters are multibyte.</summary>
    private static int Width(string s)
    {
        var n = 0;
        for (var i = 0; i < s.Length; i++) { if (!char.IsLowSurrogate(s[i])) n++; }
        return n;
    }

    private static string Pad(string s, int w)
    {
        var d = w - Width(s);
        return d > 0 ? s + new string(' ', d) : s;
    }

    /// <summary>Render the whole screen into a string; the caller writes it once.</summary>
    public static string Render(Model.Snapshot s, int selected, int cols)
    {
        var w = Math.Clamp(cols, 60, 120);
        var sb = new StringBuilder();

        // ---- header ----
        sb.Append(Fg(CHead)).Append(Bold);
        sb.Append("  FrogNet \u2014 ").Append(s.Domain.Length == 0 ? "(no identity)" : s.Domain);
        sb.Append(Reset);

        // [MERGE_IS_VISIBLE_V1] Three states. "Unknown" is not "no merge": a
        // merge degrades every latency figure here while it runs, so a reader
        // who cannot be told must be told that, not shown a clean answer.
        string merge = !s.MergeKnown ? "\u27f3 MERGE STATE UNKNOWN"
                     : s.MergePending ? $"\u27f3 MERGE RUNNING {s.MergeAgeSeconds:0}s"
                     : "";
        if (merge.Length > 0)
            sb.Append(Fg(CWarn)).Append("    ").Append(merge).Append(Reset);
        sb.AppendLine();

        sb.Append(Fg(CDimC)).Append("  ").Append(new string('\u2500', w - 4)).Append(Reset).AppendLine();

        // ---- fleet summary: the one line worth reading first ----
        var up = s.Nodes.Count(n => n.EchoOk);
        var loaded = s.Nodes.Where(n => n.LoadFraction >= 0).ToList();
        var worst = loaded.Count > 0 ? loaded.OrderByDescending(n => n.LoadFraction).First() : null;
        sb.Append(Fg(CDimC)).Append("  ").Append($"{up}/{s.Nodes.Count} reachable");
        if (worst is not null)
        {
            sb.Append("   busiest ").Append(Reset).Append(Fg(LoadColour(worst.LoadFraction)))
              .Append(worst.Name).Append(' ').Append($"{worst.LoadFraction * 100:0}%");
        }
        var noPerf = s.Nodes.Count(n => !n.Perf.Usable);
        if (noPerf > 0)
            sb.Append(Reset).Append(Fg(CDimC)).Append($"   {noPerf} without usable System.Perf");
        sb.Append(Reset).AppendLine().AppendLine();

        // ---- one card per node ----
        for (var i = 0; i < s.Nodes.Count; i++)
        {
            var n = s.Nodes[i];
            var sel = i == selected;
            var marker = sel ? Fg(CAccent) + "\u2503" + Reset : " ";

            // headline: name, role, link state, load meter
            sb.Append(' ').Append(marker).Append(' ');
            sb.Append(sel ? Bold : "").Append(Fg(CText)).Append(Pad(n.Name, 14)).Append(Reset);
            sb.Append(Fg(n.IsDbHost ? CAccent : CDimC)).Append(Pad(n.IsDbHost ? "databasehost" : "", 13)).Append(Reset);
            sb.Append(Fg(n.IsLocal ? CHead : StateColour(n.State)))
              .Append(Pad(n.IsLocal ? "SELF" : StateText(n.State), 6)).Append(Reset);

            if (n.LoadFraction >= 0)
            {
                sb.Append(Fg(LoadColour(n.LoadFraction)))
                  .Append(Meter(n.LoadFraction, 14)).Append(' ')
                  .Append(Pad($"{n.LoadFraction * 100:0}%", 5)).Append(Reset);
            }
            else
            {
                // Not "0% load" — no usable reading. The difference matters:
                // one is an idle node, the other is a node we cannot see.
                sb.Append(Fg(CDimC))
                  .Append(Pad(n.Perf.Stale ? $"reading {n.Perf.AgeSeconds:0}s old"
                                           : "no System.Perf", 20)).Append(Reset);
            }
            sb.AppendLine();

            // detail lines only for the selected card — keeps the fleet scannable
            if (sel)
            {
                if (n.Perf.Usable)
                {
                    sb.Append("   ").Append(Fg(CDimC)).Append("cpu  ").Append(Reset)
                      .Append(Fg(LoadColour(n.Perf.CpuBusy / 100)))
                      .Append(Meter(n.Perf.CpuBusy / 100, 20)).Append(Reset)
                      .Append(Fg(CDimC)).Append($"  {n.Perf.CpuBusy:0}%  iowait {n.Perf.CpuIoWait:0}%  load {n.Perf.Load1:0.00}")
                      .Append(Reset).AppendLine();

                    sb.Append("   ").Append(Fg(CDimC)).Append("mem  ").Append(Reset)
                      .Append(Fg(LoadColour(n.Perf.MemPct / 100)))
                      .Append(Meter(n.Perf.MemPct / 100, 20)).Append(Reset)
                      .Append(Fg(CDimC)).Append($"  {n.Perf.MemPct:0}%");
                    if (n.Perf.SwapPct > 0) sb.Append($"  swap {n.Perf.SwapPct:0}%");
                    if (n.Perf.TempC > 0)
                        sb.Append(Reset).Append(Fg(TempColour(n.Perf.TempC))).Append($"  {n.Perf.TempC:0}\u00b0C");
                    sb.Append(Reset).AppendLine();

                    sb.Append("   ").Append(Fg(CDimC)).Append("io   ").Append(Reset)
                      .Append(Fg(CText))
                      .Append($"net {Model.Bps(n.Perf.RxBytesSec * 8)} rx / {Model.Bps(n.Perf.TxBytesSec * 8)} tx")
                      .Append(Fg(CDimC))
                      .Append($"    disk {Model.Bps(n.Perf.DiskRBytesSec * 8)} r / {Model.Bps(n.Perf.DiskWBytesSec * 8)} w")
                      .Append(Reset).AppendLine();
                }

                sb.Append("   ").Append(Fg(CDimC)).Append("link ").Append(Reset)
                  .Append(Fg(CText))
                  .Append($"rtt {Model.Rtt(n.RttMs)}  p50 {Model.Rtt(n.P50Ms)}  p95 {Model.Rtt(n.P95Ms)}")
                  .Append(Fg(CDimC))
                  .Append($"   hit {Model.Pct(n.HitRate)}  saved {Model.Bytes(n.BytesSaved)}")
                  .Append($"   {Model.Bps(n.EffBps)} eff / {Model.Bps(n.ActualBps)} actual")
                  .Append(Reset).AppendLine();
            }
        }

        sb.AppendLine();
        sb.Append(Fg(CDimC)).Append("  ").Append(new string('\u2500', w - 4)).Append(Reset).AppendLine();
        sb.Append(Fg(CDimC))
          .Append("  \u2191\u2193 / j k  select    r  refresh    q  quit")
          .Append(s.DbHostIp.Length > 0 ? $"        databasehost {s.DbHostIp}"
                                        : "        databasehost: could not read /etc/hosts")
          .Append(Reset).AppendLine();

        return sb.ToString();
    }
}

internal static class NativeWin
{
    [System.Runtime.InteropServices.DllImport("kernel32.dll", SetLastError = true)]
    internal static extern IntPtr GetStdHandle(int nStdHandle);

    [System.Runtime.InteropServices.DllImport("kernel32.dll", SetLastError = true)]
    internal static extern bool GetConsoleMode(IntPtr hConsoleHandle, out uint lpMode);

    [System.Runtime.InteropServices.DllImport("kernel32.dll", SetLastError = true)]
    internal static extern bool SetConsoleMode(IntPtr hConsoleHandle, uint dwMode);
}
