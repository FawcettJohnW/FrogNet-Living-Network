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
/// frognet-monitor — read the FrogNet shared memory from C#.
///
/// Runs on Windows against a pond reached over a tunnel: a client is not a node
/// and needs no Linux, no shelling out, and no config files. It reads its own
/// address, derives the .1, and asks that host who it is.
/// </summary>
public static class Program
{
    private static void Usage()
    {
        Console.Error.WriteLine(
@"usage: frognet-monitor [--proxy HOST] [--port N] <command> [args]

  identity            who this pond says we are (own IP -> .1 -> echo)
  hosts               list the FrogNet nodes
  sensors <domain>    every sensor whose name starts ""<domain>.""
  read <sensor-name>  one sensor's metadata and jsonData
  engine [domain]     shorthand for <domain>.SemanticProxy.Engine
  dash                the card dashboard (default when given no command)
  selftest [file]     run the shared parser vectors
  once [domain]       render the dashboard once from LIVE state, then exit
  demo                render the dashboard once from sample data, no network

  --proxy defaults to 127.0.0.1 and --port to 80. On a machine that is not
  itself a node, point --proxy at any node's address.");
    }

    public static async Task<int> Main(string[] args)
    {
        var proxy = "127.0.0.1";
        var port = 80;

        var i = 0;
        for (; i < args.Length; i++)
        {
            if (args[i] == "--proxy" && i + 1 < args.Length) { proxy = args[++i]; continue; }
            if (args[i] == "--port" && i + 1 < args.Length) { port = int.Parse(args[++i]); continue; }
            if (args[i] is "-h" or "--help") { Usage(); return 0; }
            break;
        }
        // No command means the dashboard: it is what the tool is for.
        var cmd = i < args.Length ? args[i++] : "dash";
        var rest = args.Skip(i).ToArray();

        if (cmd == "selftest")
            return SelfTest.Run(rest.Length > 0 ? rest[0] : null);
        if (cmd == "demo")
            return Demo.Run();

        using var client = new Client(proxy, port);

        return cmd switch
        {
            "identity" => await CmdIdentity(client),
            "hosts"    => await CmdHosts(client),
            "sensors"  => rest.Length > 0 ? await CmdSensors(client, rest[0])
                                          : Fail("sensors needs a domain"),
            "read"     => rest.Length > 0 ? await CmdRead(client, rest[0])
                                          : Fail("read needs a sensor name"),
            "engine"   => await CmdEngine(client, rest.Length > 0 ? rest[0] : null),
            "dash"     => await CmdDash(client, rest.Length > 0 ? rest[0] : null),
            "once"     => await CmdFrames(client, rest.Length > 0 ? rest[0] : null, 1),
            _          => Fail(null)
        };
    }

    private static async Task<int> CmdDash(Client c, string? domain)
    {
        domain ??= await ResolveDomain(c);
        // [IDENTITY_FAILS_LOUD_V1] Without a domain there are no sensor names to
        // ask for, so there is nothing to draw. Say so rather than opening an
        // empty dashboard, which looks like an idle network.
        if (string.IsNullOrEmpty(domain))
        {
            Console.Error.WriteLine(
                "identity failed: could not determine this pond's domain.\n" +
                "Pass a domain explicitly, or point --proxy at a node that can answer.");
            return 1;
        }

        using var cts = new CancellationTokenSource();
        Console.CancelKeyPress += (_, e) => { e.Cancel = true; cts.Cancel(); };
        return await Dashboard.Run(c, domain, cts.Token);
    }

    private static async Task<int> CmdFrames(Client c, string? domain, int frames)
    {
        domain ??= await ResolveDomain(c);
        if (string.IsNullOrEmpty(domain))
        {
            Console.Error.WriteLine("identity failed; pass a domain explicitly");
            return 1;
        }
        using var cts = new CancellationTokenSource();
        return await Dashboard.Run(c, domain, cts.Token, frames);
    }

    private static int Fail(string? msg)
    {
        if (msg is not null) Console.Error.WriteLine(msg);
        else Usage();
        return 2;
    }

    private static async Task<string?> ResolveDomain(Client c)
    {
        var me = Client.LocalFrogNetAddress();
        if (string.IsNullOrEmpty(me)) return null;
        var dot1 = Parsers.DotOneOf(me);
        if (string.IsNullOrEmpty(dot1)) return null;
        var id = await c.EchoAsync(dot1);
        return id?.Domain;
    }

    private static async Task<int> CmdIdentity(Client c)
    {
        var me = Client.LocalFrogNetAddress();
        if (string.IsNullOrEmpty(me))
        {
            Console.Error.WriteLine("no 10/8 address on this machine - not on a FrogNet");
            return 2;
        }
        var dot1 = Parsers.DotOneOf(me);
        if (string.IsNullOrEmpty(dot1))
        {
            Console.Error.WriteLine($"{me} is not a basis for identity");
            return 2;
        }

        Console.WriteLine($"own address : {me}");
        Console.WriteLine($"asking      : {dot1}/frognet_echo.php");

        var id = await c.EchoAsync(dot1);
        // [IDENTITY_FAILS_LOUD_V1] Four fields or it failed. Nothing is guessed
        // and nothing is rebuilt from a hostname.
        if (id is null)
        {
            Console.Error.WriteLine($"identity FAILED - {dot1} did not return four fields");
            return 1;
        }

        var v = id.Value;
        Console.WriteLine($"domain      : {v.Domain}");
        Console.WriteLine($"eth0        : {(v.Eth0.Length  == 0 ? "(none)" : v.Eth0)}");
        Console.WriteLine($"wlan0       : {(v.Wlan0.Length == 0 ? "(none)" : v.Wlan0)}");
        Console.WriteLine($"wlan1       : {(v.Wlan1.Length == 0 ? "(none)" : v.Wlan1)}");
        return 0;
    }

    private static async Task<int> CmdHosts(Client c)
    {
        var hosts = await c.DiscoverHostsAsync();
        if (hosts.Count == 0) { Console.Error.WriteLine("no hosts discovered"); return 1; }
        foreach (var h in hosts) Console.WriteLine($"{h.Ip,-16} {h.Name}");
        return 0;
    }

    private static async Task<int> CmdSensors(Client c, string domain)
    {
        var rows = await c.SensorsForHostAsync(domain);
        if (rows is null || rows.Value.ValueKind != JsonValueKind.Array)
        {
            Console.Error.WriteLine($"no sensors for \"{domain}\"");
            return 1;
        }
        Console.WriteLine($"{"ID",-8} {"NAME",-46} TYPE");
        var n = 0;
        foreach (var r in rows.Value.EnumerateArray())
        {
            string Get(string k) => r.TryGetProperty(k, out var e)
                ? (e.ValueKind == JsonValueKind.Number ? e.GetRawText() : e.GetString() ?? "")
                : "";
            Console.WriteLine($"{Get("SensorID"),-8} {Get("SensorName"),-46} {Get("SensorType")}");
            n++;
        }
        Console.WriteLine($"\n{n} sensor(s)");
        return n > 0 ? 0 : 1;
    }

    private static async Task<int> CmdRead(Client c, string name)
    {
        var detail = await c.SensorDetailAsync(name);
        if (detail is null) { Console.Error.WriteLine($"no sensor named \"{name}\""); return 1; }

        var (meta, data) = detail.Value;
        string Get(string k) => meta.TryGetProperty(k, out var e)
            ? (e.ValueKind == JsonValueKind.Number ? e.GetRawText() : e.GetString() ?? "")
            : "";

        Console.WriteLine($"SensorID   : {Get("SensorID")}");
        Console.WriteLine($"SensorName : {Get("SensorName")}");
        Console.WriteLine($"SensorType : {Get("SensorType")}");
        Console.WriteLine($"jsonData   : {(data is null ? "(none)" : data.Value.GetRawText())}");
        return 0;
    }

    private static async Task<int> CmdEngine(Client c, string? domain)
    {
        domain ??= await ResolveDomain(c);
        if (string.IsNullOrEmpty(domain))
        {
            Console.Error.WriteLine("identity failed; pass a domain explicitly");
            return 1;
        }
        return await CmdRead(c, domain + ".SemanticProxy.Engine");
    }
}
