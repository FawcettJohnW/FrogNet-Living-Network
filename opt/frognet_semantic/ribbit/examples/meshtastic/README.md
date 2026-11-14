# Meshtastic on Ribbit — the node's infrastructure as a library on the shared RAM

Goal: one infrastructure library that replaces the plumbing of a Meshtastic node and is transparent to every existing
Meshtastic application, while the same cells are open to new applications through Ribbit's own API (`Space`, API++).

- **Kept, unchanged:** the client API (ToRadio/FromRadio over TCP 4403, serial, BLE), Meshtastic's modules and their
  semantics. Existing apps, the Python CLI and the incumbent's suites are the acceptance test.
- **Replaced:** everything that moves packets between machines or repairs that movement, and every copy of another
  node's data. A node writes its own truth into its own cells; every other node reads it with held reads.
- **Extended API:** the cells themselves, read and written from any machine on the memory.

DESIGN.md (the truth table and the decisions), QUESTION-3.md (the register), MESHTASTIC-SUITE-MAP.md (the suites),
study-interactions.md (every ask/answer, ack, retry and cache in stock 2.8.2, file:line), RESULTS.md (measured), STOCK
(the pins), VERSION.

## Build

    tools/build-ribbit.sh                   # bin/meshtastic-ram, lib/libmtribbit.a, from ../../cpp
    cd front && pio run -e native           # front/.pio/build/native/meshtasticd, linked against ../lib/libmtribbit.a

`front/` is meshtastic/firmware at the pin in STOCK, modified; every change carries a tag (`[MT_...]`).

## Run

    bin/meshtastic-ram --listen 0.0.0.0:8800                                      # the memory the nodes share
    MESHTASTIC_RIBBIT=ramhost:8800 front/.pio/build/native/meshtasticd -d <vfs> -c <config.yaml> -p 4403

Without MESHTASTIC_RIBBIT the node is stock.

## Oracles

    harness/step1-mesh-e2e.sh front/.pio/build/native/meshtasticd    # meshtastic-mcp's mesh e2e, multicast blackholed
    (cd front && ./bin/run-tests.sh)                                  # the firmware's 88 native suites
    MESHTASTICD_BIN=... MESHTASTIC_RIBBIT=... pytest -m smokevirt     # the Python CLI against a node (meshtastic/python)

`harness/run-oracle.sh NAME -- CMD` wraps any of them with a loopback capture and a tally of the defined API exercised
(`harness/apitap.py`).
