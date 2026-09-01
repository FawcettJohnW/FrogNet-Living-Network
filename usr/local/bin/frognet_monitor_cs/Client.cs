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
using System.Net;
using System.Net.NetworkInformation;
using System.Net.Sockets;
using System.Text.Json;

namespace FrogNet.Monitor;

/// <summary>
/// The whole client contract.
///
/// This implements NO part of FrogNet. Requests go to the local proxy on port
/// 80 with a Host header naming the real destination, and the responses are
/// JSON. There is no wire protocol here, no codex, no SDK, and nothing to keep
/// version-matched with the nodes.
///
/// Never open a socket to daemon port 9009. Every request enters through the
/// proxy, which is what gives it hop-by-hop conversion on the way across.
/// </summary>
public sealed class Client : IDisposable
{
    private readonly HttpClient _http;
    private readonly string _proxyHost;
    private readonly int _proxyPort;

    public Client(string proxyHost = "127.0.0.1", int proxyPort = 80, int timeoutSeconds = 5)
    {
        _proxyHost = proxyHost;
        _proxyPort = proxyPort;
        _http = new HttpClient(new SocketsHttpHandler { AllowAutoRedirect = false })
        {
            Timeout = TimeSpan.FromSeconds(timeoutSeconds)
        };
    }

    public void Dispose() => _http.Dispose();

    /// <summary>
    /// Why the most recent request failed, or null. Not thread-safe by design:
    /// it is a diagnostic for the UI to show, not a control signal.
    /// </summary>
    public string? LastError { get; private set; }

    /// <summary>
    /// The platform hosts file. A Windows client is not a node and will not have
    /// one worth reading, but hardcoding the Unix path meant the fallback threw
    /// DirectoryNotFoundException on every refresh and the caller could not tell
    /// that apart from "no FrogNet lines in it".
    /// </summary>
    public static string HostsFilePath =>
        OperatingSystem.IsWindows()
            ? Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.System),
                           "drivers", "etc", "hosts")
            : "/etc/hosts";

    /// <summary>
    /// GET through the proxy. <paramref name="hostHeader"/> names where the
    /// request is FOR; the connection goes to where requests are SENT. That
    /// indirection is why a client never resolves databasehost.frognet itself —
    /// the role floats, and the name is the point.
    /// </summary>
    public async Task<string?> GetAsync(string path, string? hostHeader,
                                        CancellationToken ct = default)
    {
        var url = $"http://{_proxyHost}:{_proxyPort}{path}";
        using var req = new HttpRequestMessage(HttpMethod.Get, url);
        if (!string.IsNullOrEmpty(hostHeader)) req.Headers.Host = hostHeader;
        // The compressed wire is between nodes; a client stays out of it.
        req.Headers.TryAddWithoutValidation("Accept-Encoding", "identity");

        try
        {
            using var resp = await _http.SendAsync(req, ct).ConfigureAwait(false);
            if (!resp.IsSuccessStatusCode) return null;
            return await resp.Content.ReadAsStringAsync(ct).ConfigureAwait(false);
        }
        catch (Exception ex)
        {
            // A failed read is a failed read -- nothing is synthesized to stand
            // in for an answer that did not arrive. But the REASON must survive:
            // a client that swallows it leaves the UI with an empty result and
            // no way to say whether the pond is quiet or unreachable.
            LastError = $"{url}: {ex.GetType().Name}: {ex.Message}";
            return null;
        }
    }

    /// <summary>
    /// api.php, unwrapping the envelope: {"ok":true,"rows":[...]} or
    /// {"ok":true,"row":{...}}.
    /// </summary>
    public async Task<JsonElement?> ApiGetAsync(string query, CancellationToken ct = default)
    {
        var body = await GetAsync("/api.php?" + query, "databasehost.frognet", ct)
                       .ConfigureAwait(false);
        if (string.IsNullOrWhiteSpace(body)) return null;

        JsonDocument doc;
        try { doc = JsonDocument.Parse(body); }
        catch (JsonException) { return null; }

        using (doc)
        {
            var root = doc.RootElement;
            if (root.ValueKind == JsonValueKind.Object)
            {
                if (root.TryGetProperty("rows", out var rows)) return rows.Clone();
                if (root.TryGetProperty("row", out var row)) return row.Clone();
            }
            return root.Clone();
        }
    }

    /// <summary>
    /// Ask a specific machine who it is. Straight to the .1, not through the
    /// Host indirection — the answer has to come from that machine.
    /// </summary>
    public async Task<Parsers.Identity?> EchoAsync(string dotOne, CancellationToken ct = default)
    {
        using var direct = new Client(dotOne, 80);
        var body = await direct.GetAsync("/frognet_echo.php", null, ct).ConfigureAwait(false);
        return Parsers.ParseEchoLine(body);
    }

    /// <summary>getHosts.php, falling back to /etc/hosts where one exists.</summary>
    public async Task<List<Parsers.HostEntry>> DiscoverHostsAsync(CancellationToken ct = default)
    {
        var body = await GetAsync("/getHosts.php", null, ct).ConfigureAwait(false);
        if (!string.IsNullOrWhiteSpace(body))
        {
            try
            {
                using var doc = JsonDocument.Parse(body);
                if (doc.RootElement.ValueKind == JsonValueKind.Array)
                {
                    var list = new List<Parsers.HostEntry>();
                    foreach (var h in doc.RootElement.EnumerateArray())
                    {
                        if (!h.TryGetProperty("ip", out var ipEl)) continue;
                        var ip = ipEl.GetString();
                        if (string.IsNullOrEmpty(ip)) continue;
                        var name = h.TryGetProperty("name", out var n) ? n.GetString() : null;
                        list.Add(new Parsers.HostEntry(ip, string.IsNullOrEmpty(name) ? ip : name));
                    }
                    if (list.Count > 0) return list;
                }
            }
            catch (JsonException) { /* fall through to the file */ }
        }

        try { return Parsers.ParseEtcHosts(await File.ReadAllTextAsync(HostsFilePath, ct)); }
        catch (Exception ex)
        {
            LastError ??= $"{HostsFilePath}: {ex.GetType().Name}";
            return new List<Parsers.HostEntry>();
        }
    }

    /// <summary>
    /// Every sensor whose name begins "&lt;domain&gt;." — filtered SERVER-side,
    /// so only this host's rows cross the wire.
    ///
    /// Name prefix ONLY. Matching on SensorAddress keys on a /24, which is a
    /// network and not a host, so every node sharing that network leaks its rows
    /// into the result.
    /// </summary>
    public Task<JsonElement?> SensorsForHostAsync(string domain, CancellationToken ct = default)
    {
        if (string.IsNullOrEmpty(domain)) return Task.FromResult<JsonElement?>(null);
        return ApiGetAsync(
            "entity=sensors&action=list&SensorName__like=" + WebUtility.UrlEncode(domain + ".%"), ct);
    }

    /// <summary>
    /// The two calls: exact name to SensorID, then SensorID to jsonData.
    /// Returns (metadata, payload); payload is null when the sensor has no data
    /// row, which is different from a sensor that does not exist.
    /// </summary>
    public async Task<(JsonElement Meta, JsonElement? Data)?> SensorDetailAsync(
        string sensorName, CancellationToken ct = default)
    {
        var rows = await ApiGetAsync(
            "entity=sensors&action=list&SensorName=" + WebUtility.UrlEncode(sensorName) + "&limit=1",
            ct).ConfigureAwait(false);
        if (rows is null || rows.Value.ValueKind != JsonValueKind.Array) return null;

        JsonElement? first = null;
        foreach (var r in rows.Value.EnumerateArray()) { first = r; break; }
        if (first is null) return null;

        var meta = first.Value;
        if (!meta.TryGetProperty("SensorID", out var idEl)) return (meta, null);
        var sid = idEl.ValueKind == JsonValueKind.Number
                    ? idEl.GetRawText()
                    : idEl.GetString();
        if (string.IsNullOrEmpty(sid)) return (meta, null);

        var sd = await ApiGetAsync(
            "entity=sensor_data&action=get&SensorID=" + WebUtility.UrlEncode(sid), ct)
            .ConfigureAwait(false);
        if (sd is null || sd.Value.ValueKind != JsonValueKind.Object) return (meta, null);
        if (!sd.Value.TryGetProperty("jsonData", out var jd)) return (meta, null);

        // jsonData arrives as a STRING holding JSON. Parse once, at the edge, so
        // everything above works with a structure.
        if (jd.ValueKind == JsonValueKind.String)
        {
            var s = jd.GetString();
            if (!string.IsNullOrEmpty(s))
            {
                try { using var inner = JsonDocument.Parse(s); return (meta, inner.RootElement.Clone()); }
                catch (JsonException) { /* not JSON; hand back the string */ }
            }
        }
        return (meta, jd.Clone());
    }

    /// <summary>
    /// This machine's own 10/8 address, excluding loopback and the two reserved
    /// ranges. Empty when this machine is not on a FrogNet.
    ///
    /// No shelling out and no Linux-only files: a client is not a node and needs
    /// none of the node-side identity machinery. Read your own address, derive
    /// the .1, ask it who it is.
    /// </summary>
    public static string LocalFrogNetAddress()
    {
        foreach (var nic in NetworkInterface.GetAllNetworkInterfaces())
        {
            if (nic.OperationalStatus != OperationalStatus.Up) continue;
            if (nic.NetworkInterfaceType == NetworkInterfaceType.Loopback) continue;

            foreach (var ua in nic.GetIPProperties().UnicastAddresses)
            {
                if (ua.Address.AddressFamily != AddressFamily.InterNetwork) continue;
                var ip = ua.Address.ToString();
                if (!ip.StartsWith("10.", StringComparison.Ordinal)) continue;
                if (ip.StartsWith("10.253.", StringComparison.Ordinal)) continue;
                if (ip.StartsWith("10.254.", StringComparison.Ordinal)) continue;
                return ip;
            }
        }
        return "";
    }
}
