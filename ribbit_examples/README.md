# Examples

Two stand-alone proofs of concept for the FrogNet programming model. Neither one needs a FrogNet node or network. Each builds on ordinary Linux and runs by itself.

Both examples test the same idea: distributed and concurrent programs don't have to be written as conversations. Instead of finding each other, sending requests and waiting for replies, programs publish what is true and read what they need:

> Determine your truth. Write your truth. Read the other truths. Go.

## [FrogChat](Chat/)

Person-to-person chat over the Internet with no chat server.

Each person writes lines into shared memory and waits on their own part of it. The client is two threads and under 500 lines of standard-library Python, with a C++ version beside it. Presence and multi-recipient chat were added without changing the server. A small C++ memory server and an oracle that checks the whole thing are included.

This is the place to start. It shows the programming model at its smallest.

## [Redis-on-Ribbit](Redis/)

A Redis-compatible server whose entire keyspace lives in Ribbit shared memory, built in four days.

It uses Redis's own test suite as the judge. The memory has no mutex, no lock and no reaper thread. Blocking pops, consumer groups and transactions become published state instead of server machinery.

On a Raspberry Pi 5 it matches stock Redis on one core and reaches 2.2–2.6× on three cores. Stock `redis-cli`, `redis-benchmark` and client libraries connect to it unchanged.

This is the place to see the same model hold up under a large, familiar, externally defined contract.

## What they have in common

Neither example contains networking choreography in the application. There are no queues between participants, no coordinators, no callbacks and no request/reply protocol. Each builds on state that is independently addressable, plus reads that wait for the state they depend on.

In a full FrogNet, that same model runs across a self-forming mesh of machines, sites and radio links. The examples show it working before any of that machinery is involved.

More at https://fawcettinnovations.com

Everything here is GPLv2-only. Copyright © 2016–2026 Fawcett Innovations LLC.
