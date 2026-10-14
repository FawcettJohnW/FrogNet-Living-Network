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
/// The three pure parsers. No I/O, no sockets, no clock.
///
/// These are exercised by ../frognet_monitor_shared/parser_vectors.json — the
/// same file the Python and C++ implementations read — so agreement between the
/// ports is a test result rather than a claim in a README.
/// </summary>
public static class Parsers
{
    /// <summary>Identity as returned by frognet_echo.php / getFrogNet.bash.</summary>
    public readonly record struct Identity(string Domain, string Eth0, string Wlan0, string Wlan1);

    /// <summary>A FrogNet node as it appears in /etc/hosts or getHosts.php.</summary>
    public readonly record struct HostEntry(string Ip, string Name);

    /// <summary>
    /// Parse one CSV line: <c>fqdn,eth0IP,wlan0IP,wlan1IP</c>.
    /// Returns null when identity failed.
    ///
    /// [IDENTITY_FAILS_LOUD_V1] Four fields with a non-empty NAME, or it failed.
    /// getFrogNet.bash exits non-zero and prints nothing when a node cannot state
    /// its identity, so the echo body is empty and there is nothing to guess. No
    /// fallback name is reconstructed: every node answers to the hostname
    /// "FrogNetHost", so a name that is not the domain is not an identity, and a
    /// client that accepts one believes every node is the same node.
    ///
    /// An empty INTERFACE field is accurate data — a node with no carrier on eth0
    /// genuinely has no eth0 address.
    /// </summary>
    public static Identity? ParseEchoLine(string? body)
    {
        if (string.IsNullOrWhiteSpace(body)) return null;

        // First non-empty line; the body may carry a trailing newline.
        var line = body.Trim();
        var nl = line.IndexOf('\n');
        if (nl >= 0) line = line[..nl].Trim();
        if (line.Length == 0) return null;

        // NOTE: plain Split, NOT StringSplitOptions.RemoveEmptyEntries.
        //
        // This is the single most important line in the C# port. With
        // RemoveEmptyEntries, "Seattle2,,10.120.120.1,10.160.160.47" becomes
        // three fields and every subsequent value shifts left — wlan0 would hold
        // the wlan1 address. The result is well-formed, plausible, and wrong,
        // and nothing downstream can detect it. The shared vectors exist
        // largely to pin this behaviour.
        var f = line.Split(',');
        if (f.Length != 4) return null;

        for (var i = 0; i < f.Length; i++) f[i] = f[i].Trim();
        if (f[0].Length == 0) return null;   // empty NAME is a failure

        return new Identity(f[0], f[1], f[2], f[3]);
    }

    /// <summary>
    /// Parse /etc/hosts content into FrogNet nodes.
    ///
    /// 10/8 only; 10.253.0.0/16 (tunnel transit) and 10.254.0.0/16 (chorus
    /// virtual) excluded; a <c>FrogNetHost.&lt;name&gt;</c> alias required, and
    /// the name is the part after it. Every node's hostname is FrogNetHost, so
    /// the alias suffix is the only thing on the line identifying which node it
    /// describes.
    /// </summary>
    public static List<HostEntry> ParseEtcHosts(string? content)
    {
        var result = new List<HostEntry>();
        if (string.IsNullOrEmpty(content)) return result;

        const string marker = "FrogNetHost.";
        foreach (var raw in content.Split('\n'))
        {
            var line = raw.Trim();
            if (line.Length == 0 || line[0] == '#') continue;

            var parts = line.Split((char[]?)null, StringSplitOptions.RemoveEmptyEntries);
            if (parts.Length < 2) continue;

            var ip = parts[0];
            if (!ip.StartsWith("10.", StringComparison.Ordinal)) continue;
            if (ip.StartsWith("10.253.", StringComparison.Ordinal)) continue;
            if (ip.StartsWith("10.254.", StringComparison.Ordinal)) continue;

            for (var i = 1; i < parts.Length; i++)
            {
                var at = parts[i].IndexOf(marker, StringComparison.Ordinal);
                if (at < 0) continue;
                var name = parts[i][(at + marker.Length)..];
                if (name.Length > 0) result.Add(new HostEntry(ip, name));
                break;
            }
        }
        return result;
    }

    /// <summary>
    /// Own address to the .1 that can state this pond's identity.
    /// Returns "" when the address is not a basis for one: 10.253/16 and
    /// 10.254/16 are carried by a node but do not identify it.
    /// </summary>
    public static string DotOneOf(string? ip)
    {
        if (string.IsNullOrWhiteSpace(ip)) return "";
        var s = ip.Trim();
        if (!s.StartsWith("10.", StringComparison.Ordinal)) return "";
        if (s.StartsWith("10.253.", StringComparison.Ordinal)) return "";
        if (s.StartsWith("10.254.", StringComparison.Ordinal)) return "";

        var o = s.Split('.');
        if (o.Length != 4) return "";
        foreach (var part in o)
        {
            if (part.Length == 0 || part.Length > 3) return "";
            foreach (var c in part) if (c < '0' || c > '9') return "";
            if (int.Parse(part) > 255) return "";
        }
        return $"{o[0]}.{o[1]}.{o[2]}.1";
    }
    /// <summary>
    /// [ONE_MONITOR_TRUTH_V1] getSnapshot.php's body, normalized: the same summary as the Python
    /// (discovery.parse_snapshot) and the C++ (parseSnapshot), checked by the shared "snapshot" vectors.
    /// Ponds "*area" = forward; hub "iface 17s" | "iface no handshake" | "iface error"; members
    /// "ip name role alive|down source[ self]". A field of the wrong type reads empty; flags count only as true.
    /// </summary>
    public sealed record NodeSnapshot(string Mode, string Name, string Dot1, string Build, string Commit, string ModeWhy,
                                      string DbHost, string Control, List<string> Ponds, List<string> Hub,
                                      List<string> Members, List<string> Errors, JsonElement Raw);

    private static string Str(JsonElement o, string k) =>
        o.ValueKind == JsonValueKind.Object && o.TryGetProperty(k, out var v) && v.ValueKind == JsonValueKind.String
            ? v.GetString() ?? "" : "";

    private static bool True(JsonElement o, string k) =>
        o.ValueKind == JsonValueKind.Object && o.TryGetProperty(k, out var v) && v.ValueKind == JsonValueKind.True;

    private static IEnumerable<JsonElement> Arr(JsonElement o, string k) =>
        o.TryGetProperty(k, out var v) && v.ValueKind == JsonValueKind.Array ? v.EnumerateArray() : Enumerable.Empty<JsonElement>();

    private static JsonElement Obj(JsonElement o, string k) =>
        o.TryGetProperty(k, out var v) ? v : default;

    /// <summary>Null when the body is not a snapshot (not JSON, not an object, snapshot != 1, mode neither).</summary>
    public static NodeSnapshot? ParseSnapshot(string? body)
    {
        if (string.IsNullOrEmpty(body)) return null;
        JsonElement d;
        try { using var doc = JsonDocument.Parse(body); d = doc.RootElement.Clone(); }
        catch (JsonException) { return null; }
        if (d.ValueKind != JsonValueKind.Object) return null;
        if (!d.TryGetProperty("snapshot", out var ver) || ver.ValueKind != JsonValueKind.Number || ver.GetDouble() != 1)
            return null;
        var mode = Str(d, "mode");
        if (mode != "lan-only" && mode != "region") return null;
        var node = Obj(d, "node");
        var build = Obj(d, "build");
        var el = Obj(d, "elections");
        var ponds = Arr(d, "ponds").Where(p => p.ValueKind == JsonValueKind.Object)
                                   .Select(p => (True(p, "forward") ? "*" : "") + Str(p, "area")).ToList();
        var hub = new List<string>();
        foreach (var h in Arr(d, "hub"))
        {
            if (h.ValueKind != JsonValueKind.Object) continue;
            var iface = Str(h, "iface");
            if (Str(h, "error").Length > 0) hub.Add(iface + " error");
            else if (h.TryGetProperty("handshake_age_s", out var a) && a.ValueKind == JsonValueKind.Number)
                hub.Add($"{iface} {(long)a.GetDouble()}s");
            else hub.Add(iface + " no handshake");
        }
        var members = Arr(d, "members").Where(m => m.ValueKind == JsonValueKind.Object)
            .Select(m => $"{Str(m, "ip")} {Str(m, "name")} {Str(m, "role")} {(True(m, "alive") ? "alive" : "down")} {Str(m, "source")}"
                         + (True(m, "self") ? " self" : "")).ToList();
        var errors = Arr(d, "errors").Where(e => e.ValueKind == JsonValueKind.String).Select(e => e.GetString() ?? "").ToList();
        return new NodeSnapshot(mode, Str(node, "name"), Str(node, "dot1"), Str(build, "build"), Str(build, "commit"),
                                Str(d, "mode_why"), Str(el, "databasehost"), Str(el, "databasehost_control"),
                                ponds, hub, members, errors, d);
    }

    /// <summary>The title, word for word the Python's draw_dashboard.title_text.</summary>
    public static string TitleText(NodeSnapshot? n) =>
        n is null ? "  FrogNet   (getSnapshot.php did not answer)"
                  : $"  FrogNet   {(n.Name.Length > 0 ? n.Name : "?")} ({(n.Dot1.Length > 0 ? n.Dot1 : "no .1")})";

    /// <summary>Mode, build, ponds, hub link, the snapshot's errors: the Python's draw_dashboard.node_lines.</summary>
    public static List<string> NodeLines(NodeSnapshot? n)
    {
        if (n is null)
            return new() { "  no snapshot: http://127.0.0.1/getSnapshot.php did not answer (asked again next cycle)" };
        var build = (n.Build.Length > 0 ? n.Build : "?") + (n.Commit.Length > 0 ? " " + n.Commit : "");
        var o = new List<string> { $"  mode {n.Mode,-9} build {build}" };
        if (n.Mode == "region")
        {
            o.Add("  ponds  " + (n.Ponds.Count > 0 ? string.Join("  ", n.Ponds) : "none") + "   (* = forward)");
            o.Add("  hub    " + (n.Hub.Count > 0 ? string.Join("  ", n.Hub) : "no hub link (facts.json has no hub row)"));
        }
        else o.Add($"  ponds  none, hub none (LAN-only: {n.ModeWhy})");
        o.AddRange(n.Errors.Select(e => "  ! " + e));
        return o;
    }
}
