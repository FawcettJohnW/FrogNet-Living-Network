# FrogTastic-RAM — a Meshtastic node's infrastructure as a library on the shared RAM

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

    tools/build-ribbit.sh                   # bin/frogtastic-ram, bin/frogtastic-bridge, lib/libmtribbit.a, from ../../cpp
    cd front && pio run -e native           # front/.pio/build/native/meshtasticd (links nothing of Ribbit)

`front/` is meshtastic/firmware at the pin in STOCK, modified; every change carries a tag (`[MT_...]`).

## Run

    bin/frogtastic-ram --listen 0.0.0.0:8800                                      # the memory the nodes share
    FROGTASTIC_BRIDGE_BIN=bin/frogtastic-bridge MESHTASTIC_RIBBIT=ramhost:8800 \
        front/.pio/build/native/meshtasticd -d <vfs> -c <config.yaml> -p 4403

Without MESHTASTIC_RIBBIT the node is stock. `FROGTASTIC_BRIDGE_BIN` is where the firmware finds FrogTastic-Bridge (default:
`frogtastic-bridge` on PATH).

## Two programs, two licences [FROGTASTIC_SHM_TRANSPORT_V1]

The firmware is Meshtastic's (GPL-3.0); the Ribbit platform is FrogNet's (GPL-2.0-only). They are not linked together.
The firmware's calls on the memory are the plain functions in `ribbit/mt_ribbit.h`. In the firmware,
`front/src/mesh/ribbit/frogtastic_shim.cpp` implements them by writing each call into a shared segment
(`ribbit/frogtastic_shm.h`). FrogTastic-Bridge (`ribbit/frogtastic_bridge.cpp`, `bin/frogtastic-bridge`), a separate process,
takes each call, makes it into `ribbit/mt_ribbit.cpp` and the platform, and writes back what it returns and what the
held reads hand over. The segment is a transport: nothing is kept in it.

- The firmware starts its node's FrogTastic-Bridge in `mtr_start` (segment `/frogtastic-bridge-<node>`) and waits for its
  `serving` line. FrogTastic-Bridge leaves the memory when that firmware exits, so the node's session ends with the
  firmware, as before.
- `mt_ribbit.h`, `frogtastic_shm.h` and `harness/ft_probe.cpp` are used by both sides and are licensed GPL-2.0-only OR
  GPL-3.0-only. `front/src/mesh/ribbit/` (RibbitTransport, RibbitFacts, the shim) is GPL-3.0-only: it is built on
  Meshtastic's headers. Everything else of ours here is GPL-2.0-only.
- `harness/ft-transport.sh` builds `harness/ft_probe.cpp` twice, linked to `libmtribbit.a` and linked to the shim alone,
  drives every `mt_ribbit.h` call between two nodes, and requires identical output. It also checks that the shim build
  contains no Ribbit code and that FrogTastic-Bridge leaves with its firmware.
- Not yet split: `lib/libmeshapp.a` (`ribbit/mesh_app.cpp`) links Meshtastic's generated protobuf code with the
  platform.

## Oracles

    harness/step1-mesh-e2e.sh front/.pio/build/native/meshtasticd    # meshtastic-mcp's mesh e2e, multicast blackholed
    (cd front && ./bin/run-tests.sh)                                  # the firmware's 88 native suites
    MESHTASTICD_BIN=... MESHTASTIC_RIBBIT=... FROGTASTIC_BRIDGE_BIN=... pytest -m smokevirt   # the Python CLI (meshtastic/python)

`harness/run-oracle.sh NAME -- CMD` wraps any of them with a loopback capture and a tally of the defined API exercised
(`harness/apitap.py`).
