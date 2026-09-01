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
}
