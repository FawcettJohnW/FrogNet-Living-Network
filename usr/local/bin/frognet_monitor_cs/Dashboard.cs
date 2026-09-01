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
/// The dashboard loop.
///
/// One rule governs the structure: THE DRAW LOOP NEVER AWAITS A SOCKET.
/// A background task does every network read and publishes a finished Snapshot
/// by whole-reference swap, so a repaint sees a complete snapshot or the
/// previous complete snapshot, never half of one.
/// </summary>
public static class Dashboard
{
    private static volatile Model.Snapshot _snap = new();
    private static long _generation;

    /// <param name="frames">
    /// When input is redirected there are no keys to read, so the loop needs a
    /// different way to stop. Render this many refreshes and exit. 0 means run
    /// interactively until q.
    /// </param>
    public static async Task<int> Run(Client client, string domain,
                                      CancellationToken ct, int frames = 0)
    {
        Cards.EnableAnsi();

        // Console.KeyAvailable THROWS InvalidOperationException when stdin is
        // redirected, which is what happens the moment anyone pipes this into
        // less, tees it to a file, or runs it from CI. Detect it once here
        // rather than catching the throw on every pass of the draw loop.
        var interactive = !Console.IsInputRedirected;
        if (frames <= 0 && !interactive) frames = 3;

        try { Console.CursorVisible = false; } catch { /* redirected */ }

        using var cts = CancellationTokenSource.CreateLinkedTokenSource(ct);
        var refresh = new SemaphoreSlim(0, 1);
        var worker = Task.Run(() => Collector(client, domain, refresh, cts.Token), cts.Token);

        var selected = 0;
        long drawn = -1;
        var painted = 0;

        try
        {
            while (!cts.IsCancellationRequested)
            {
                var key = false;
                if (interactive && Console.KeyAvailable)
                {
                    key = true;
                    var k = Console.ReadKey(intercept: true);
                    switch (k.Key)
                    {
                        case ConsoleKey.Q: cts.Cancel(); continue;
                        case ConsoleKey.R:
                            if (refresh.CurrentCount == 0) refresh.Release();
                            break;
                        case ConsoleKey.DownArrow: selected++; break;
                        case ConsoleKey.UpArrow: selected--; break;
                        default:
                            if (k.KeyChar == 'j') selected++;
                            else if (k.KeyChar == 'k') selected--;
                            else if (k.KeyChar == 'q') { cts.Cancel(); continue; }
                            break;
                    }
                }

                var s = _snap;
                if (s.Nodes.Count > 0)
                    selected = Math.Clamp(selected, 0, s.Nodes.Count - 1);

                if (s.Generation != drawn || key)
                {
                    drawn = s.Generation;
                    var width = SafeWidth();
                    string frame;
                    if (s.Nodes.Count > 0)
                    {
                        frame = Cards.Render(s, selected, width);
                    }
                    else if (s.Generation == 0)
                    {
                        frame = "\n  waiting for the first refresh\u2026\n";
                    }
                    else
                    {
                        // A completed refresh that found nothing. Say what and why.
                        frame = "\n  No FrogNet nodes found.\n\n  "
                              + (s.Notice ?? "reason unknown")
                              + "\n\n  This machine is probably not on a FrogNet. Point it at a node:\n"
                              + "      frognet-monitor --proxy <node-address> dash <Domain>\n"
                              + "\n  q to quit.\n";
                    }

                    // Home + clear-to-end, not Console.Clear(): a full clear
                    // flickers on every repaint and on Windows scrolls the
                    // buffer, losing whatever was on screen before this ran.
                    // Home + clear-to-end only when a terminal owns the screen.
                    // Redirected, that just injects escape codes into the file.
                    if (interactive) Console.Write("\u001b[H\u001b[J");
                    Console.Write(frame);

                    // Count EVERY completed refresh, not just ones with nodes.
                    // Gating on Nodes.Count meant a client that could not reach a
                    // pond never advanced and never exited -- the "waiting for the
                    // first refresh" hang.
                    if (s.Generation > 0 && ++painted >= frames && frames > 0)
                        break;
                }

                await Task.Delay(80, cts.Token).ConfigureAwait(false);
            }
        }
        catch (OperationCanceledException) { /* q, or the caller cancelled */ }
        finally
        {
            try { Console.CursorVisible = true; } catch { /* redirected */ }
            Console.Write("\u001b[0m\n");
            cts.Cancel();
            try { await worker.ConfigureAwait(false); } catch (OperationCanceledException) { }
        }
        return 0;
    }

    private static int SafeWidth()
    {
        // Redirected output has no window width; 100 is a reasonable page.
        try { return Console.WindowWidth > 0 ? Console.WindowWidth : 100; }
        catch { return 100; }
    }

    private static async Task Collector(Client c, string domain,
                                        SemaphoreSlim refresh, CancellationToken ct)
    {
        while (!ct.IsCancellationRequested)
        {
            try
            {
                var s = await Collect(c, domain, ct).ConfigureAwait(false);
                s.Generation = Interlocked.Increment(ref _generation);
                _snap = s;
            }
            catch (OperationCanceledException) { return; }
            catch (Exception ex)
            {
                // Telemetry never disturbs the thing it is measuring, and a
                // failed refresh is not a reason to tear the screen down. But a
                // snapshot that never arrives must not read as one that has not
                // arrived YET -- publish the reason so the screen can say so.
                var prev = _snap;
                _snap = new Model.Snapshot
                {
                    Domain = domain,
                    Nodes = prev.Nodes,
                    DbHostIp = prev.DbHostIp,
                    Generation = Interlocked.Increment(ref _generation),
                    FailedRefreshes = prev.FailedRefreshes + 1,
                    Notice = $"refresh failed: {ex.GetType().Name}: {ex.Message}"
                };
            }

            try
            {
                using var delay = CancellationTokenSource.CreateLinkedTokenSource(ct);
                var t = Task.Delay(TimeSpan.FromSeconds(3), delay.Token);
                var r = refresh.WaitAsync(delay.Token);
                await Task.WhenAny(t, r).ConfigureAwait(false);
                delay.Cancel();
            }
            catch (OperationCanceledException) { return; }
        }
    }

    private static async Task<Model.Snapshot> Collect(Client c, string domain, CancellationToken ct)
    {
        var s = new Model.Snapshot { Domain = domain };

        // [MERGE_IS_VISIBLE_V1] Read the sentinel the merge maintains. Three
        // states: present with an age, absent, and unreadable — which is
        // neither, and paints differently.
        try
        {
            var fi = new FileInfo("/etc/sentinels/mergePending");
            s.MergeKnown = true;
            if (fi.Exists)
            {
                s.MergePending = true;
                s.MergeAgeSeconds = (DateTime.UtcNow - fi.LastWriteTimeUtc).TotalSeconds;
            }
        }
        catch { s.MergeKnown = false; }

        // The elected data host from /etc/hosts, never the resolver: in practice
        // the two disagree, and the file is what everything else routes by.
        try
        {
            foreach (var line in await File.ReadAllLinesAsync("/etc/hosts", ct))
            {
                var t = line.Trim();
                if (t.StartsWith('#') || !t.Contains("databasehost.frognet")) continue;
                var parts = t.Split((char[]?)null, StringSplitOptions.RemoveEmptyEntries);
                if (parts.Length > 0) { s.DbHostIp = parts[0]; break; }
            }
        }
        catch { /* leave empty; the footer says which problem this is */ }

        var hosts = await c.DiscoverHostsAsync(ct).ConfigureAwait(false);
        if (hosts.Count == 0)
        {
            // Nothing to draw, and the operator needs to know WHICH failure this
            // is: an unreachable proxy, or a reachable one with no FrogNet hosts.
            s.Notice = c.LastError is null
                ? "getHosts.php returned no FrogNet hosts, and the local hosts file has none either."
                : "cannot reach a FrogNet: " + c.LastError;
            return s;
        }

        // This node's link-quality snapshot: one read for the whole fleet.
        JsonElement? lqPeers = null;
        var lq = await c.SensorDetailAsync(domain + ".SemanticProxy.LinkQuality", ct)
                        .ConfigureAwait(false);
        if (lq?.Data is { ValueKind: JsonValueKind.Object } d &&
            d.TryGetProperty("peers", out var pp))
            lqPeers = pp;

        // Per node, in parallel: a slow peer costs its own row, not the refresh.
        var nodes = await Task.WhenAll(hosts.Select(async h =>
        {
            var n = new Model.Node
            {
                Ip = h.Ip,
                Name = h.Name,
                IsDbHost = s.DbHostIp.Length > 0 && h.Ip == s.DbHostIp,
                // A node holds no cache or link-quality entry for ITSELF, so its
                // status resolves to Unknown -- true, but it reads as a fault on
                // the one row the operator most trusts. Mark it as self.
                IsLocal = h.Name == domain
            };

            // Echo proves the whole chain: local proxy, local daemon, remote
            // daemon, remote Apache. A socket connect could not.
            n.EchoOk = await c.EchoAsync(h.Ip, ct).ConfigureAwait(false) is not null;

            var cache = await c.SensorDetailAsync($"{domain}.SemanticCache.Peer.{h.Ip}", ct)
                               .ConfigureAwait(false);
            if (cache?.Data is { ValueKind: JsonValueKind.Object } cd)
            {
                if (cd.TryGetProperty("rtt", out var rtt) && rtt.ValueKind == JsonValueKind.Object)
                {
                    n.RttMs = GetD(rtt, "avg_ms");
                    n.P50Ms = GetD(rtt, "p50_ms");
                    n.P95Ms = GetD(rtt, "p95_ms");
                }
                n.HitRate = GetD(cd, "cache_hit_rate");
                n.BytesSaved = (long)GetD(cd, "bytes_saved");
                if (cd.TryGetProperty("effective_throughput", out var et) &&
                    et.ValueKind == JsonValueKind.Object)
                {
                    n.EffBps = GetD(et, "effective_bps");
                    n.ActualBps = GetD(et, "actual_bps");
                }
            }

            // System.Perf is named for the node it DESCRIBES, so the prefix is
            // that node's domain, not ours.
            var perf = await c.SensorDetailAsync($"{h.Name}.System.Perf", ct).ConfigureAwait(false);
            if (perf?.Data is { } pd) n.Perf = Model.ParsePerf(pd, DateTime.UtcNow);

            if (lqPeers is { } lp && lp.TryGetProperty(h.Ip, out var mine))
            {
                n.HaveLinkQuality = true;
                n.SatP95 = GetD(mine, "saturation_ratio_p95");
                n.LqRttP95 = GetD(mine, "rtt_ok_p95_ms");
            }

            return n;
        })).ConfigureAwait(false);

        s.Nodes = nodes.ToList();
        return s;
    }

    private static double GetD(JsonElement o, string k) =>
        o.ValueKind == JsonValueKind.Object && o.TryGetProperty(k, out var v)
            ? v.ValueKind switch
            {
                JsonValueKind.Number => v.GetDouble(),
                JsonValueKind.String => double.TryParse(v.GetString(), out var d) ? d : 0,
                _ => 0
            }
            : 0;
}
