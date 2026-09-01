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
/// Runs ../frognet_monitor_shared/parser_vectors.json — the same file the Python
/// and C++ oracles read. "The C# port behaves identically" is then a test result
/// rather than a claim.
///
///     frognet-monitor selftest
/// </summary>
public static class SelfTest
{
    private static int _pass;
    private static int _fail;

    private static void Ok(string m)  { Console.WriteLine("  ok    " + m); _pass++; }
    private static void Bad(string m) { Console.WriteLine("  FAIL  " + m); _fail++; }

    private static string Str(JsonElement e, string k) =>
        e.TryGetProperty(k, out var v) && v.ValueKind == JsonValueKind.String
            ? v.GetString() ?? "" : "";

    private static bool Flag(JsonElement e, string k) =>
        e.TryGetProperty(k, out var v) && v.ValueKind == JsonValueKind.True;

    public static int Run(string? path)
    {
        path ??= Path.Combine(AppContext.BaseDirectory, "parser_vectors.json");
        if (!File.Exists(path))
        {
            var alt = Path.Combine(AppContext.BaseDirectory,
                                   "..", "..", "..", "..",
                                   "frognet_monitor_shared", "parser_vectors.json");
            if (File.Exists(alt)) path = alt;
        }
        if (!File.Exists(path))
        {
            Console.Error.WriteLine($"FATAL: cannot read vectors at {path}");
            return 1;
        }

        using var doc = JsonDocument.Parse(File.ReadAllText(path));
        var root = doc.RootElement;
        Console.WriteLine($"vectors: {Path.GetFullPath(path)}");

        RunEcho(root.GetProperty("echo_line").GetProperty("cases"));
        RunHosts(root.GetProperty("etc_hosts").GetProperty("cases"));
        RunDotOne(root.GetProperty("dot_one_of").GetProperty("cases"));
        RunModel();

        Console.WriteLine();
        Console.WriteLine($"{_pass} passed, {_fail} failed");
        if (_fail > 0) { Console.WriteLine("FAILED"); return 1; }
        Console.WriteLine("PASS - C# parsers agree with the shared vectors");
        return 0;
    }

    /// <summary>
    /// The dashboard model: status precedence, the meaning of a missing or
    /// stale reading, and the rule that zero is not a measurement. This is the
    /// part of a monitor that can be quietly wrong — an operator acts on
    /// whether a peer reads OK or DOWN.
    /// </summary>
    private static void RunModel()
    {
        Console.WriteLine("\n=== peer status: first match wins ===");

        void St(string what, Model.Node n, Model.PeerState want)
        {
            if (n.State == want) Ok($"{what} -> {want}");
            else Bad($"{what} -> got {n.State} want {want}");
        }

        St("echo failure outranks a good link",
           new Model.Node { EchoOk = false, HaveLinkQuality = true, SatP95 = 0.1, LqRttP95 = 5 },
           Model.PeerState.Down);
        St("saturation outranks a good RTT",
           new Model.Node { EchoOk = true, HaveLinkQuality = true, SatP95 = 0.95, LqRttP95 = 5 },
           Model.PeerState.Sat);
        St("busy link",
           new Model.Node { EchoOk = true, HaveLinkQuality = true, SatP95 = 0.75, LqRttP95 = 5 },
           Model.PeerState.Busy);
        St("slow over 1000ms",
           new Model.Node { EchoOk = true, HaveLinkQuality = true, SatP95 = 0.1, LqRttP95 = 1500 },
           Model.PeerState.Slow);
        St("fair over 250ms",
           new Model.Node { EchoOk = true, HaveLinkQuality = true, SatP95 = 0.1, LqRttP95 = 400 },
           Model.PeerState.Fair);

        Console.WriteLine("\n=== a missing LinkQuality entry is not a failure ===");
        // The proxy has simply not spoken to this peer in the current window.
        St("degrades to RTT, not to DOWN",
           new Model.Node { EchoOk = true, HaveLinkQuality = false, P95Ms = 30 },
           Model.PeerState.Ok);
        St("no readings at all is UNKNOWN, not OK",
           new Model.Node { EchoOk = true, HaveLinkQuality = false },
           Model.PeerState.Unknown);

        Console.WriteLine("\n=== a stale reading is not a reading ===");
        var stale = new Model.Node { EchoOk = true };
        stale.Perf = new Model.Perf { Have = true, CpuBusy = 90, MemPct = 90, AgeSeconds = 400 };
        if (stale.Perf.Stale && !stale.Perf.Usable) Ok("400s old reading is stale and unusable");
        else Bad("400s old reading was treated as current");
        if (stale.LoadFraction < 0) Ok("stale node reports no load fraction, not 0%");
        else Bad($"stale node reported load {stale.LoadFraction}");

        var fresh = new Model.Node { EchoOk = true };
        fresh.Perf = new Model.Perf { Have = true, CpuBusy = 40, MemPct = 91, AgeSeconds = 5 };
        // The worse of CPU and memory: averaging them hides a node that is fine
        // on one and out of headroom on the other.
        if (Math.Abs(fresh.LoadFraction - 0.91) < 0.001) Ok("load fraction takes the worse of cpu/mem");
        else Bad($"load fraction {fresh.LoadFraction:0.000}, expected 0.910");

        var noPerf = new Model.Node { EchoOk = true };
        if (noPerf.LoadFraction < 0) Ok("node with no System.Perf reports no load, not 0%");
        else Bad("node with no System.Perf reported a load figure");

        Console.WriteLine("\n=== zero is not a measurement ===");
        void Eq(string what, string got, string want)
        {
            if (got == want) Ok($"{what} -> \"{got}\"");
            else Bad($"{what} -> got \"{got}\" want \"{want}\"");
        }
        Eq("Rtt(0)", Model.Rtt(0), "\u2014");
        Eq("Pct(0)", Model.Pct(0), "\u2014");
        Eq("Bytes(0)", Model.Bytes(0), "\u2014");
        Eq("Bps(0)", Model.Bps(0), "\u2014");
        Eq("Rtt(8.4)", Model.Rtt(8.4), "8.4ms");
        Eq("Bytes(1536)", Model.Bytes(1536), "1.5K");
        Eq("Bps(1500000)", Model.Bps(1500000), "1.5Mbps");

        Console.WriteLine("\n=== meters ===");
        int W(string s2) { var n = 0; foreach (var ch in s2) if (!char.IsLowSurrogate(ch)) n++; return n; }
        var m = Cards.Meter(0.5, 10);
        if (W(m) == 10) Ok("a 10-cell meter is 10 columns wide");
        else Bad($"meter width {W(m)}, expected 10");
        if (Cards.Meter(0.0, 10) != Cards.Meter(0.05, 10))
            Ok("eighth-blocks distinguish 0% from 5% on a 10-cell meter");
        else Bad("meter cannot show a sub-cell difference");
        if (W(Cards.Meter(1.5, 10)) == 10 && W(Cards.Meter(-3, 10)) == 10)
            Ok("out-of-range fractions are clamped, not overflowed");
        else Bad("meter overflowed on an out-of-range fraction");
    }

    private static void RunEcho(JsonElement cases)
    {
        Console.WriteLine("\n=== echo_line (identity) ===");
        foreach (var c in cases.EnumerateArray())
        {
            var name = Str(c, "name");
            var got = Parsers.ParseEchoLine(Str(c, "input"));
            var wantOk = Flag(c, "ok");

            if (got.HasValue != wantOk)
            {
                Bad($"{name} -- ok={got.HasValue}, expected {wantOk}");
                continue;
            }
            if (!wantOk) { Ok($"{name} -- rejected, as it must be"); continue; }

            var v = got!.Value;
            if (v.Domain == Str(c, "domain") && v.Eth0 == Str(c, "eth0")
                && v.Wlan0 == Str(c, "wlan0") && v.Wlan1 == Str(c, "wlan1"))
                Ok(name);
            else
                Bad($"{name} -- got [{v.Domain}|{v.Eth0}|{v.Wlan0}|{v.Wlan1}] " +
                    $"want [{Str(c, "domain")}|{Str(c, "eth0")}|{Str(c, "wlan0")}|{Str(c, "wlan1")}]");
        }
    }

    private static void RunHosts(JsonElement cases)
    {
        Console.WriteLine("\n=== etc_hosts (discovery) ===");
        foreach (var c in cases.EnumerateArray())
        {
            var name = Str(c, "name");
            var got = Parsers.ParseEtcHosts(Str(c, "input"));
            var expect = c.GetProperty("expect");

            var wantN = expect.GetArrayLength();
            if (got.Count != wantN)
            {
                Bad($"{name} -- got {got.Count} entries, expected {wantN}");
                continue;
            }

            var same = true;
            var k = 0;
            foreach (var pair in expect.EnumerateArray())
            {
                var wIp = pair[0].GetString() ?? "";
                var wNm = pair[1].GetString() ?? "";
                if (got[k].Ip != wIp || got[k].Name != wNm)
                {
                    Bad($"{name} -- entry {k} got [{got[k].Ip},{got[k].Name}] want [{wIp},{wNm}]");
                    same = false;
                    break;
                }
                k++;
            }
            if (same) Ok(name);
        }
    }

    private static void RunDotOne(JsonElement cases)
    {
        Console.WriteLine("\n=== dot_one_of (identity endpoint) ===");
        foreach (var c in cases.EnumerateArray())
        {
            var name = Str(c, "name");
            var got = Parsers.DotOneOf(Str(c, "input"));

            if (!Flag(c, "ok"))
            {
                if (got.Length == 0) Ok($"{name} -- rejected");
                else Bad($"{name} -- returned \"{got}\", expected rejection");
                continue;
            }
            var want = Str(c, "expect");
            if (got == want) Ok(name);
            else Bad($"{name} -- got \"{got}\", want \"{want}\"");
        }
    }
}
