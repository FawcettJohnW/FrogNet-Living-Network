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
namespace FrogNet.Monitor;

/// <summary>
/// Render the dashboard once from sample data, with no network.
///
/// Two reasons this exists rather than being a screenshot in the README. It
/// proves the renderer runs and lays out correctly on whatever machine you are
/// on, and it shows the cases that are hard to produce on demand from a healthy
/// pond: a node that is down, one with no System.Perf sensor at all, and one
/// whose reading has gone stale. Those three are exactly where a dashboard
/// tends to paint a confident wrong answer.
///
///     frognet-monitor demo
/// </summary>
public static class Demo
{
    public static int Run()
    {
        Cards.EnableAnsi();

        Model.Node Mk(string name, bool echo, double rtt, double p95, double hit,
                      long saved, double eff, double actual,
                      double cpu, double iow, double load, double mem,
                      double rx, double tx, double dr, double dw, double temp,
                      bool db = false, bool perf = true, double age = 4)
        {
            var n = new Model.Node
            {
                Name = name, Ip = "10.0.0.1", EchoOk = echo, IsDbHost = db,
                RttMs = rtt, P50Ms = rtt * 0.8, P95Ms = p95, HitRate = hit,
                BytesSaved = saved, EffBps = eff, ActualBps = actual,
                HaveLinkQuality = true, SatP95 = 0.2, LqRttP95 = p95
            };
            if (perf)
                n.Perf = new Model.Perf
                {
                    Have = true, CpuBusy = cpu, CpuIoWait = iow, Load1 = load,
                    MemPct = mem, RxBytesSec = rx, TxBytesSec = tx,
                    DiskRBytesSec = dr, DiskWBytesSec = dw, TempC = temp,
                    AgeSeconds = age
                };
            return n;
        }

        var s = new Model.Snapshot
        {
            Domain = "Seattle5",
            DbHostIp = "10.199.199.1",
            MergeKnown = true,
            MergePending = false,
            Generation = 1
        };

        s.Nodes.Add(Mk("Seattle5", true, 0.9, 2.1, 0.62, 4_180_000, 3_100_000, 1_180_000,
                       12.4, 0.6, 0.31, 41, 22_000, 8_100, 0, 12_000, 47.2, db: false));
        s.Nodes.Add(Mk("Seattle6", true, 8.1, 21.4, 0.48, 980_000, 1_900_000, 1_400_000,
                       63.8, 4.2, 1.94, 72.5, 180_000, 240_000, 90_000, 410_000, 68.1));
        s.Nodes.Add(Mk("New-York-1", true, 71.2, 240.0, 0.31, 320_000, 900_000, 780_000,
                       91.7, 18.3, 6.02, 93.1, 900_000, 1_200_000, 2_400_000, 3_100_000, 79.4));
        s.Nodes.Add(Mk("BAMacBook", true, 64.0, 180.0, 0.22, 120_000, 400_000, 360_000,
                       0, 0, 0, 0, 0, 0, 0, 0, 0, perf: false));           // no System.Perf
        s.Nodes.Add(Mk("BABox", true, 66.5, 190.0, 0.19, 90_000, 300_000, 280_000,
                       3.1, 0, 0.08, 22, 900, 400, 0, 0, 38.0, age: 400)); // stale reading
        s.Nodes.Add(Mk("Seattle2", false, 0, 0, 0, 0, 0, 0,
                       0, 0, 0, 0, 0, 0, 0, 0, 0, perf: false));           // unreachable
        s.Nodes.Add(Mk("databasehost", true, 2.2, 6.0, 0.71, 9_900_000, 5_200_000, 1_500_000,
                       28.0, 1.1, 0.74, 55, 400_000, 380_000, 40_000, 60_000, 52.0, db: true));

        Console.Write(Cards.Render(s, selected: 2, cols: 108));
        Console.WriteLine();
        Console.WriteLine("  (sample data \u2014 no network was used)");
        return 0;
    }
}
