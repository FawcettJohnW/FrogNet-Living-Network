# frognet_monitor_cs

A FrogNet monitor in C#. Reads the shared memory; writes nothing.

Sits beside `frognet_monitor_py` and `frognet_monitor_cpp`, and all three share
one set of parser vectors so agreement between them is measured rather than
asserted.

## The point

**Zero packages.** `HttpClient` and `System.Text.Json` ship with the runtime, so
there is no `PackageReference` in the project file at all. `nuget.config` clears
every package source, which makes that a build-time guarantee rather than a
README claim — add a dependency and the restore fails.

It also means this builds on an air-gapped machine.

There is no FrogNet client library, no generated stub, no SDK, and nothing to
regenerate when a node is upgraded. The entire contract is:

```
GET http://127.0.0.1:80/api.php?...      Host: databasehost.frognet
```

## Build

```sh
dotnet build -c Release
dotnet bin/Release/net8.0/frognet-monitor.dll selftest
```

Verified on .NET 8.0.129 — clean build with `TreatWarningsAsErrors`, 27/27
vectors passing.

On Windows, open `FrogNetMonitor.csproj` in Visual Studio and press build. A
Windows box is not a node; point it at one:

```
frognet-monitor --proxy 10.160.160.1 hosts
```

## Use

```
frognet-monitor                                 the card dashboard (default)
frognet-monitor demo                            render it from sample data, no network
frognet-monitor identity                        who this pond says we are
frognet-monitor hosts                           the nodes
frognet-monitor sensors Seattle6                that node's telemetry
frognet-monitor read Seattle6.SemanticProxy.Engine
frognet-monitor engine                          shorthand for this node
frognet-monitor selftest                        run the shared vectors
```

## The dashboard

Node cards, not a table. Each node gets a headline — name, role, link state, and
a load meter — and the selected card expands to show cpu, memory, io and link
detail. A fleet summary line sits above it: how many are reachable, which is
busiest, how many have no usable `System.Perf`.

That is deliberately **not** what the C++ port draws. It renders the same shared
memory as a dense operations table with a link/load toggle. The data belongs to
the pond, not to any one presentation of it, and two unlike views make that
point better than two identical ones would.

Load comes from `<domain>.System.Perf`: `cpu_pct.total_busy`, `cpu_pct.iowait`,
`loadavg`, `mem_kb.pct_used`, `swap_kb.pct_used`, `net_io.total`,
`disk_io.total`, and the hottest entry in `temps_c`. The headline meter takes the
**worse** of CPU and memory rather than the average — a node at 40% CPU and 91%
memory is out of headroom, and averaging hides that.

Three things the model refuses to fake, all covered by `selftest`:

- **No `System.Perf` sensor** reads "no System.Perf", not 0%. An idle node and a
  node you cannot see are different facts.
- **A reading older than 120s** reads "reading NNNs old". Drawing a stale sample
  as a live meter is the same error as printing `0ms` for a measurement never
  taken.
- **A missing LinkQuality entry** is not a failure — the proxy has not spoken to
  that peer in the current window — so status degrades to the RTT actually held
  rather than inventing a saturation figure.

Rendering is hand-rolled ANSI (256-colour, eighth-block meters), so the
zero-package promise holds. `Cards.EnableAnsi()` turns on virtual terminal
processing on Windows; nothing is needed elsewhere.

## How a client finds out who it is

No config file, no shelling out, nothing Linux-specific. A client is not a node:

1. `NetworkInterface.GetAllNetworkInterfaces()` — take the 10/8 address, skipping
   `10.253/16` (tunnel transit) and `10.254/16` (chorus virtual), which a node
   carries but is not identified by.
2. Replace the last octet with `1`.
3. `GET http://<that>/frognet_echo.php`.

The answer is one CSV line: `fqdn,eth0IP,wlan0IP,wlan1IP`.

**[IDENTITY_FAILS_LOUD_V1]** Four fields with a non-empty name, or it failed.
`getFrogNet.bash` exits non-zero and prints nothing when a node cannot state its
identity, so the body is empty and there is nothing to guess. No fallback name is
reconstructed — every node answers to the hostname `FrogNetHost`, so a client
that accepts a hostname-derived name believes every node is the same node.

An empty **interface** field is accurate data: a node with no carrier on eth0
genuinely has no eth0 address. An empty **name** is a failure.

## The one line to get right

```csharp
var f = line.Split(',');                                   // correct
var f = line.Split(',', StringSplitOptions.RemoveEmptyEntries);  // WRONG
```

With `RemoveEmptyEntries`, `Seattle2,,10.120.120.1,10.160.160.47` becomes three
fields and every later value shifts left — `wlan0` ends up holding the `wlan1`
address, or the line is rejected outright. Well-formed, plausible, wrong, and
undetectable downstream.

This is measured, not warned about. A mutant built with `RemoveEmptyEntries`
rejects that vector, so the shared cases catch it.

## Reading a sensor

Two tables joined on `SensorID`, so two calls:

```
GET /api.php?entity=sensors&action=list&SensorName=Seattle6.SemanticProxy.Engine&limit=1
    -> [ { "SensorID": 4417, ... } ]

GET /api.php?entity=sensor_data&action=get&SensorID=4417
    -> { "SensorID": 4417, "jsonData": "{ ... }" }
```

`jsonData` arrives as a *string* holding JSON; parsed once, at the edge, so
callers work with a structure.

Listing a host's sensors filters server-side on `SensorName__like=<domain>.%`.
Name prefix only — `SensorAddress` keys on a /24, which is a network and not a
host.

`databasehost.frognet` goes in the `Host` header and is never resolved by the
client. The role floats; the name is the point.

## Layout

```
FrogNetMonitor.csproj   net8.0, no PackageReference
nuget.config            sources cleared - no packages, enforced
Parsers.cs              the three pure functions the vectors exercise
Model.cs                nodes, System.Perf, status precedence, formatting
Cards.cs                the ANSI card renderer
Dashboard.cs            the draw loop; network runs on a background task
Demo.cs                 render the dashboard from sample data, no network
Client.cs               HttpClient transport, the two API calls, own-address
Program.cs              the CLI
SelfTest.cs             shared vectors + the dashboard model
```
