# Homa's socket interface on FrogNet shared memory, against TCP: first measurements on three Raspberry Pis

17 November 2026. Fawcett Innovations LLC. Branch `homa-under-croakos-20261116` of FrogNet-Living-Network.

## Summary

John Ousterhout's own load generator, `cp_node` from HomaModule, was run between Raspberry Pis two ways in one
session: over TCP, and over "soft Homa", a library that answers Homa's socket calls by reading and writing a FrogNet
shared memory instead of sending Homa packets. No kernel module is involved. The program is the same binary in both
cases.

| Message size | Near pair (link 1.9 ms) | Far pair, Seattle to New York (link 91.6 ms) |
|---|---|---|
| 100 and 1,000 bytes | the same (soft Homa 1.8 to 1.9 ms, TCP 1.9 to 2.0 ms) | the same (both 92 to 93 ms) |
| 10,000 bytes | soft Homa 2.5 times faster | the same at the median; TCP's 99th percentile 3 times longer |
| 100,000 bytes | soft Homa 2.6 times faster | soft Homa 1.3 times faster |
| 240,000 bytes | soft Homa 2.7 times faster | soft Homa 2.5 times faster |
| 1,000,000 bytes | TCP 10 times faster | TCP 2.5 times faster |

Soft Homa is ahead from 10,000 to 240,000 bytes because `cp_node` sends the same payload in every message, and the
memory sends only what changed. That is the best case for it. Above 240,000 bytes soft Homa splits a message into
several cells, and that path is slow; it is a defect in this first version, not a property of the design.

Kernel Homa was not measured. HomaModule has no branch for these machines' kernel (Linux 6.12).

## What soft Homa is

Soft Homa is a shared library loaded into an unchanged program (`LD_PRELOAD`). It answers the calls a program makes
on a Homa socket (`socket` with `IPPROTO_HOMA`, `bind`, `setsockopt`, `sendmsg`, `recvmsg`, `ioctl`, `poll`) with the
layouts of HomaModule's `homa.h`. A request is one write into a cell of a FrogNet memory, addressed to the server's
name. The server holds a standing read on its own name and is handed the request when it lands. The reply is the
same thing the other way. The memory is a process on one node (the RAM host); every participant has one session with
it.

There are no retransmission timers, grants or pacing in it. A server announces itself with a capability cell in the
memory. When its process or its machine goes, the cell goes, and everyone with a request outstanding to it is told
at once. A request to a server that is not there ends at once.

`cp_node` and `homa_test` from HomaModule run on it unchanged, and a gate of 18 checks on those two tools passes.

## What was run

- **Nodes.** Seattle5 (10.250.250.1, Raspberry Pi 5, Linux 6.12.87+rpt-rpi-2712) held the memory and both servers.
  Seattle3B (10.251.251.1, the same hardware and kernel) was the near client. New-York-1 (10.123.123.1, Linux
  6.12.87+rpt-rpi-v8) was the far client. The clients ran one at a time.
- **Program.** `cp_node` from HomaModule at commit 07ce63ef, as server and as client, `--protocol homa` on soft Homa
  and `--protocol tcp`. Each request is answered with a response of the same size. On these ARM machines two lines of
  its source were changed so that it builds (its cycle counter and its crash handler are written for x86), and its
  client was given `--gbps 1000000`, because without a rate it sends one request and stops on ARM. Neither changes
  what it sends or how it reports.
- **Runs.** At each size, four runs of 8 seconds in the order soft Homa, TCP, TCP, soft Homa, with a ping of the
  link before and after the whole table. Sizes up to 10,000 bytes had 8 requests outstanding; larger sizes had 1.
- **Figures.** The round trip times are `cp_node`'s own, over the whole run. Each figure below is the mean of the
  two runs of that carrier; the two runs agreed within a few percent except where shown.
- **Software.** Soft Homa build softhoma-v7-20261117k. The comparison is `tools/try.sh compare` in
  `opt/frognet_semantic/ribbit/examples/homa`.

## Results

### Near pair: Seattle3B to Seattle5

The link's round trip was 1.86 to 2.43 ms by ping.

| Size (bytes) | Outstanding | Soft Homa P50 | TCP P50 | Soft Homa round trips per second | TCP round trips per second |
|---|---|---|---|---|---|
| 100 | 8 | 1.87 ms | 1.97 ms | 3,580 | 3,560 |
| 1,000 | 8 | 1.82 ms | 1.95 ms | 3,920 | 3,880 |
| 10,000 | 8 | 1.97 ms | 5.01 ms | 3,530 | 1,560 |
| 100,000 | 1 | 3.58 ms | 9.13 ms (10.21 and 8.04) | 260 | 110 |
| 240,000 | 1 | 6.03 ms | 16.19 ms | 165 | 60 |
| 1,000,000 | 1 | 649 ms | 62 ms | 1.5 | 16 |

The 99th percentile at 100 and 1,000 bytes was 3.3 to 5.8 ms on soft Homa and 2.8 to 5.7 ms on TCP. At 10,000 bytes
it was 3.7 ms on soft Homa and 7.3 ms on TCP.

### Far pair: New-York-1 to Seattle5

The link's round trip was 91.4 to 91.8 ms by ping.

| Size (bytes) | Outstanding | Soft Homa P50 | TCP P50 | Soft Homa round trips per second | TCP round trips per second |
|---|---|---|---|---|---|
| 100 | 8 | 91.9 ms | 92.0 ms | 87 | 87 |
| 1,000 | 8 | 91.9 ms | 92.9 ms | 87 | 86 |
| 10,000 | 8 | 92.0 ms | 93.9 ms | 87 | 85 |
| 100,000 | 1 | 95.9 ms | 128.1 ms | 10.4 | 7.8 |
| 240,000 | 1 | 100.0 ms | 245.6 ms | 10.0 | 4.1 |
| 1,000,000 | 1 | 2,091 ms | 852 ms (776 and 927) | 0.5 | 1.2 |

With one request outstanding on this link a run holds fewer than a hundred round trips, so its 99th percentile is
its slowest round trip, which is the first one. Those figures are in the tables the tool keeps and are not compared
here. With 8 outstanding, at 10,000 bytes, TCP's 99th percentile was 278 ms; soft Homa's was 99 ms in one run and
182 ms in the other. At 100 and 1,000 bytes soft Homa's 99.9th percentile was about 186 ms, two round trips of the
link, where TCP's was about 110 ms.

## Why the results are what they are

**Small messages.** Below about 10,000 bytes both carriers cost one round trip of the link and little else. Soft
Homa crosses the link once each way because the memory was on the server's node.

**10,000 to 240,000 bytes.** `cp_node` fills every message with the same bytes. A request lands in a cell that still
holds the previous request, and FrogNet's semantic engine sends the difference, which is the few header fields
`cp_node` changes. Measured on one machine at 240,000 bytes, tens of bytes cross to and from the memory per round
trip. TCP carries all 480,000. So TCP's time grows with the size and the link, and soft Homa's is the link's round
trip plus the processor time to encode and decode at each end: about 4 ms at 240,000 bytes on a Pi 5, of which 2.1 ms
is in the server (measured from its log over 2,957 requests).

**1,000,000 bytes.** A message over 240,000 bytes is written as several cells, because one cell cannot exceed the
memory's frame size once its bytes are encoded. Written that way the cells are not sent as differences, so all the
bytes cross, a third larger for their encoding, and on the server's node each cell also waits on a small send buffer
between the server and the memory. This is where the first version is weakest. The cause is partly diagnosed and
not fixed.

**Soft Homa's 99.9th percentile on the far link.** The first request to a server asks the memory whether the server
is there, which costs one more round trip of the link, once per client. That is the likely source of the two-round-
trip figure. It has not been confirmed by a trace.

## What these measurements do not show

- **A payload that changes.** Every figure above is for the same bytes sent again and again. When the bytes differ
  every time they must cross the link, a third larger. TCP should be ahead there. `cp_node` cannot send such a
  payload, so this needs another load generator.
- **Kernel Homa.** The module has no branch for Linux 6.12. Its `linux_6.13.9` branch compiles for these machines
  against the Raspberry Pi 6.12 kernel source once it is given the ARM clock; it has not been linked against the
  running kernel, loaded or run.
- **The memory on a third machine.** Here the memory was on the server's node. On a third node soft Homa crosses two
  links each way.
- **More than one client at a time**, and `cp_node`'s own workload distributions (w1 to w5).
- **Long runs.** Each cell of the tables is two runs of 8 seconds.
- **FrogNet's own wire.** This is Ousterhout's tool on soft Homa. FrogNet's proxy and daemon on soft Homa pass their
  gate on one machine and have not run on nodes.

## What was predicted

Two predictions were written down before the runs they concern. At 100 bytes TCP was predicted ahead; in the
same-session table the two are the same. At 240,000 bytes TCP was predicted ahead several times over, on the
grounds that soft Homa encodes the payload and passes it through the engine; soft Homa was 2.7 times faster, because
the prediction left out that the engine does not send what has not changed.

## How to run it again

On every node, unpack the tarball under `/` and build:

    cd / && tar xvzf - < /tmp/softhoma-20261117k.tgz
    /opt/softhoma-20261117g/ribbit/examples/homa/tools/try.sh build

On the node that holds the memory, in two terminals:

    /opt/softhoma-20261117g/ribbit/examples/homa/tools/try.sh ram
    /opt/softhoma-20261117g/ribbit/examples/homa/tools/try.sh servers MEMORY-ADDRESS

On a client node:

    /opt/softhoma-20261117g/ribbit/examples/homa/tools/try.sh compare MEMORY-ADDRESS SERVER-ADDRESS

The table and every run's output are kept under `results/` beside the tool. The design, the rulings it follows and
the record of each finding are in `DESIGN.md` in the same directory.
