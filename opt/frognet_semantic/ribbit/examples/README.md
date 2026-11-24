# ribbit/examples

Each example builds by itself into its own `build/` (or, for lispers.net, `bin`-style paths its qualification
names), ready to run from there or to copy anywhere, and runs across the open Internet: its RAM host anywhere the
participants can reach, at an address and port of the user's choosing.

| example | its RAM host | its client library | what it demonstrates |
|---|---|---|---|
| [`chat/`](chat/README.md) | `chat-ram` | `libchat` (on the default API) | person-to-person chat with no server: each line is a tuple in the recipient's buffer, read with a held read |
| [`lispers.net/`](lispers.net/README.md) | `lisper-ram` (the LISP region's operations run in the memory itself) | `liblisper` | a LISP map-server and map-resolver, tested byte for byte against lispers.net 0.643 |
| [`communicator/`](communicator/README.md) | `comms-ram`, and a media host every call meets at | `libtuples` (the default API) | presence, chat and A/V calling, with the media host found by reading its tuple |
| [`redis/`](redis/README.md) | `ribbit-redis-host` (the Redis region's operations run in the memory itself) | any stock Redis client, through a local `ribbit-redis-front` | a Redis that works across the Internet: fronts anywhere, one region on a neutral machine, Redis 7.2.11's own suite as the oracle |
| [`pytorch/`](pytorch/README.md) | the SystemRam (`databasehost.frognet`) | a registered `torch.distributed` backend and DDP comm hooks; code at `opt/frognet_semantic/agent_workload` and `usr/local/frognet-ai`, not in this directory | PyTorch DDP training over network shared memory across Seattle and New York, against a same-session gloo control, with every rank agreeing on its parameter hash |
| [`frogtastic-ram/`](frogtastic-ram/README.md) | `frogtastic-ram` | none in the firmware: Meshtastic's firmware in `front/` (third-party, GPL-3.0) reaches the memory through `frogtastic-bridge`, a separate process; stock Meshtastic apps over TCP 4403, serial, BLE | FrogTastic: a Meshtastic node whose infrastructure is the shared RAM: each node writes its own cells and reads every other node's with held reads; existing Meshtastic apps unchanged |
| [`mqtt/`](mqtt/README.md) | the MQTT region on a RAM host | any stock MQTT client, through the Ribbit front | Eclipse Mosquitto 2.1.2 on the shared RAM: Mosquitto's own suite as the oracle (272 of 276, as stock), 101,483 QoS 1 messages a second against stock's 27,485 |
| [`openeoc/`](openeoc/RESULTS.md) | the app's general RAM and one RAM per team, placed by the app's selection | the node-served pages (`opt/fdf/src/fdf-source/fdf/www/openeoc`), the desktop and phone app (`opt/fdf/src/openeoc-app`) | an emergency operations centre, measured against the open-source OpenEOC: roster, radio log (ICS-309), map, assignments, messages, through splits |
| [`worldshepherd/`](worldshepherd/README.md) | `worldshepherd-ram` (no operations; it files tickets so they outlive it) | `ribbit_claim_store.py`, a `SharedClaimStore` for WorldShepherd SARA, and `ticket_sql.py`, which runs SARA's own `PostgresClaimStore` unmodified with no PostgreSQL; both on the session library (`libkartram`) | WorldShepherd SARA's connector read-ticket ledger across machines: a ticket is cells each SARA writes and reads, claimed exactly once, with SARA's own 31 connector tests as the oracle and its store on PostgreSQL as the control |

Every system built or converted, with its contract, oracle and time, is in Magnum Croakus, Part XV¾ ("Fourteen
Systems"), and on the site: fawcettinnovations.com/conversions.html. The conversion method is
`../docs/CONVERTING-TO-RIBBIT.md`.
