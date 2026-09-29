# communicator -- presence, chat and A/V calling through shared memory

The FrogNet Communicator: the same source a FrogNet node runs (its services start from this directory) and, built with
`build.sh`, the stand-alone version below. presence, chat, the five call-state tuples and the media ladder. Everything that
is state is tuples in the Communicator's region, on the default Ribbit API, written by the party with standing to know
it and read by everyone else. The streams go to a media host, a separate role that every call meets at: it publishes
where it listens as a tuple, and callers read that tuple and dial it. `AI_README.md` is the application's own guide
to its parts and its traps.

| | |
|---|---|
| `comms_ram.cpp` | `comms-ram`: the Communicator's RAM host, a `ribbit::RamHost` with no operations of its own |
| `*.py`, `assets/`, `communicator_app.jsx` | the Communicator |
| `frognet_mediahost_server.py` | the media host (`--addr` the address it publishes, `--dbhost` the memory) |
| `frognet_tuples.py` | on a FrogNet node, the node's own `core.frognet_tuples`; `build.sh` replaces it in `build/` with the default API's |
| `tests_passing_on_node.txt` | the oracles that pass on the untouched bundle with a FrogNet node's own modules |

## Build, test, run

```
./build.sh      # build/comms-ram, build/libtuples.so, build/core and the Communicator
./test.sh       # every oracle in tests_passing_on_node.txt, against comms-ram
```

The whole-call simulation -- a publisher, a viewer and a relay, all real code, concurrently -- runs against a
`comms-ram` too: `FROGNET_TUPLES_RAM=<HOST>:<PORT> FROGNET_TUPLES_VENDOR=comms python3 sim_whole_call.py` from `build/`.

Running it: `build/comms-ram --listen 0.0.0.0:<PORT>` anywhere reachable; every Communicator process, and the media
host, with `FROGNET_TUPLES_RAM=<HOST>:<PORT> FROGNET_TUPLES_VENDOR=comms` in its environment.

The media host in this tree does not start: `frognet_mediahost_server.py` imports `unpack_av_src` from
`call_media.py`, and this tree's `call_media.py` does not have it. The four media-host oracles fail the same way on a
FrogNet node's own modules.
