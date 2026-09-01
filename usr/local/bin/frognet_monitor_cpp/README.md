# frognet_monitor_cpp

A FrogNet monitor in C++. Reads the shared memory; writes nothing.

Sits beside `frognet_monitor_py` and `frognet_monitor_cs`, and all three share
one set of parser vectors so agreement between them is measured rather than
asserted.

## The point

This implements **no part of FrogNet**. There is no wire protocol here, no
codex, no SDK, and no library to keep version-matched with the nodes. The entire
client contract is:

```
GET http://127.0.0.1:80/api.php?...      Host: databasehost.frognet
```

libcurl for HTTP, a 250-line vendored JSON reader, and that is the dependency
list. `find_package(CURL)` is the only thing CMake looks for.

## Build

```sh
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build -j
ctest --test-dir build --output-on-failure
```

Verified on Ubuntu 24.04, GCC 13.3, CMake 3.28, libcurl 8.5 — clean build with
`-Wall -Wextra -Wpedantic`, 27/27 vectors passing.

The ncurses dashboard is built by default. Turn it off for a target without
ncurses; the headless tool still builds and still demonstrates the whole
contract:

```sh
cmake -S . -B build -DFROGNET_BUILD_TUI=OFF
```

## The dashboard

A dense operations table with two boards over the same peers, TAB to switch:

- **LINK** — can I reach it, and how is the transport doing.
  `PEER STATUS RTT P50 P95 HIT SAVED EFF ACTUAL`
- **LOAD** — what is that machine actually doing while it serves.
  `NODE CPU BUSY IOW LOAD MEM NET-RX/TX DISK-R/W TEMP`

A node can be perfect on one board and in trouble on the other, which is why
they are two boards and not one merged table.

```
NODE         CPU         BUSY   IOW   LOAD   MEM         NET RX/TX         DISK R/W          TEMP
------------------------------------------------------------------------------------------------
*Seattle5    <U+2588><U+258F>........  12%    1%    0.31   <U+2588><U+2588><U+258D>... 41%  176Kbps/65Kbps    -/96Kbps          47C
Seattle6     <U+2588><U+2588><U+2588><U+2588><U+2588><U+2588><U+258D>...  64%    4%    1.94   <U+2588><U+2588><U+2588><U+2588><U+258E>. 72%  1.4Mbps/1.9Mbps   720Kbps/3.3Mbps   68C
New-York-1   <U+2588><U+2588><U+2588><U+2588><U+2588><U+2588><U+2588><U+2588><U+2588><U+258F>  92%    18%   6.02   <U+2588><U+2588><U+2588><U+2588><U+2588><U+258C> 93%  7.2Mbps/9.6Mbps   19Mbps/25Mbps     79C
Seattle2     -           -      -     -      no perf     -                 -                 -
BAMacBook    -           -      -     -      stale 400s  -                 -                 -
```

Load comes from `<domain>.System.Perf`. Meters use eighth-block glyphs so a
10-cell bar can distinguish 61% from 74% — the band where a node stops being
comfortable. Colour is the intent (`Ink`), never a curses pair; only
`main_tui.cpp` knows about curses at all.

`dashboard.cpp` holds the model and formatting and `main_tui.cpp` only draws,
which is why `ctest` can exercise the status precedence, the stale/missing
handling and the column widths with no terminal attached.

The C# port renders the same data as node cards. Two unlike views on purpose.

## Use

```sh
./build/frognet_monitor identity                        # who this pond says we are
./build/frognet_monitor hosts                           # the nodes
./build/frognet_monitor sensors Seattle6                # that node's telemetry
./build/frognet_monitor read Seattle6.SemanticProxy.Engine
./build/frognet_monitor engine                          # shorthand for this node
```

From a machine that is not itself a node, point at any node:

```sh
./build/frognet_monitor --proxy 10.160.160.1 hosts
```

## How a client finds out who it is

It does not read `/etc/dnsmasq.d/opts_only.conf`, shell out to `ip`, or need any
node-side file. A client is not a node:

1. Read your own address — the 10/8 one, skipping `10.253/16` (tunnel transit)
   and `10.254/16` (chorus virtual), which a node carries but is not identified
   by.
2. Replace the last octet with `1`.
3. `GET http://<that>/frognet_echo.php`.

The answer is one CSV line: `fqdn,eth0IP,wlan0IP,wlan1IP`.

**[IDENTITY_FAILS_LOUD_V1]** Four fields with a non-empty name, or it failed.
`getFrogNet.bash` exits non-zero and prints nothing when a node cannot state its
identity, so the body is empty and there is nothing to guess. There is no
fallback name and nothing to reconstruct — every node answers to the hostname
`FrogNetHost`, so a client that accepts a hostname-derived name believes every
node is the same node.

An empty **interface** field is accurate data: a node with no carrier on eth0
genuinely has no eth0 address, and `Seattle2,,10.120.120.1,10.160.160.47` is the
correct answer for such a node. An empty **name** is a failure.

## Reading a sensor

Two tables joined on `SensorID`, so two calls:

```
GET /api.php?entity=sensors&action=list&SensorName=Seattle6.SemanticProxy.Engine&limit=1
    -> [ { "SensorID": 4417, ... } ]

GET /api.php?entity=sensor_data&action=get&SensorID=4417
    -> { "SensorID": 4417, "jsonData": "{ ... }" }
```

`jsonData` arrives as a *string* holding JSON; parse it once, at the edge.

Listing a host's sensors filters server-side on `SensorName__like=<domain>.%`.
Name prefix only — `SensorAddress` keys on a /24, which is a network and not a
host, so it pulls in every node sharing that network.

## The shared vectors

`../frognet_monitor_shared/parser_vectors.json` is read by this project's
oracle, by `frognet_monitor_py.test_parser_vectors_oracle`, and by
`frognet-monitor selftest` in the C# project. Three implementations, one file,
27 cases.

They exist because of one failure in particular: `strtok` collapses runs of
delimiters and C#'s `Split` with `RemoveEmptyEntries` drops empties, so
`Seattle2,,10.120.120.1,10.160.160.47` silently becomes a three-field line whose
`wlan0` holds the `wlan1` address. Well-formed, plausible, wrong, and invisible
downstream. `splitKeepEmpty` in `src/parsers.cpp` exists for exactly that
reason.

Add a case to the vectors first, then make the implementations pass it.

## Layout

```
CMakeLists.txt
include/frognet.h          the whole client contract
src/json.cpp               vendored JSON reader
src/parsers.cpp            the three pure functions the vectors exercise
src/http.cpp               libcurl transport, the two API calls, own-address
src/dashboard.cpp          model, thresholds, formatting - no terminal
src/main_headless.cpp      the CLI
src/main_tui.cpp           ncurses drawing, and nothing else
tests/parser_oracle.cpp    runs the shared vectors
tests/dashboard_oracle.cpp status precedence, stale handling, column widths
```
