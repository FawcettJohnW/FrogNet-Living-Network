# FrogChat

Person-to-person chat over the Internet with no chat server. A proof of concept for FrogNet Network Shared RAM.

Nobody sends anybody anything. Each person writes lines into shared memory and reads their own part of it. The whole Python client is under 500 lines and uses only the standard library.

## Why this exists

A conventional chat program is a conversation. Find the server, build a request, send it, wait for the reply, work out what the reply means, and keep a backend running to route it all.

FrogChat asks what happens if the application only reads and writes state instead. The answer is two threads: one reads your keyboard and writes, the other waits on your own buffer and prints whatever arrives. There's no routing code, no message queue, no callback service and no application backend.

## How it works

The memory holds cells, each addressed by three coordinates: service, variable and instance.

- **Sending.** When you type a line to Bob, FrogChat writes it to the cell `ChatServer.Bob.<you>`. Every sender has their own cell in Bob's buffer, so two people writing to Bob at the same moment don't overwrite each other.
- **Receiving.** Your reader thread sits in a blocking read on `ChatServer.<you>.*` and wakes the moment anyone writes into it.
- **Presence.** You're "here" as long as your cell `ChatPresence.here.<you>` is fresh. The reader refreshes it as a side effect of its own read loop, so presence needs no thread of its own. `/list` is one read of `ChatPresence.here.*`. Someone who disappears simply goes stale; someone who quits removes their own cell.

Presence and multi-recipient chat were added without changing the server at all. The server doesn't know it's running a chat application.

On the wire, FrogChat uses FNW1, FrogNet's semantic protocol. An unchanged question goes out as a 21-byte REPEAT and comes back as a 21-byte SAME, so an idle reader costs almost nothing.

## What's here

| Path | What it is |
|---|---|
| `apps/frogchat/client/frogchat.py` | The chat client. Python 3.8+, standard library only, Linux and Windows. |
| `apps/frogchat/client_cpp/` | The same client in C++. |
| `apps/frogchat/test_frogchat_oracle.py` | The oracle: behavioral checks against any running memory. |
| `server_cpp/ram_server.cpp` | The memory server: FNW1 and the memory in one C++ process. |
| `cpp/` | The C++ client library used by the C++ client and server. |
| `common/probe_ram.py` | Checks whether a memory is reachable. |

## Run it

Build and start the memory:

```
cd server_cpp && make
./ram_server --listen 0.0.0.0:8788
```

The memory lives in RAM. A restart gives you an empty memory, which for chat is fine.

On two machines (or two terminals), point the clients at it:

```
python3 apps/frogchat/client/frogchat.py --store HOST:8788 --me Dave --to Bob
python3 apps/frogchat/client/frogchat.py --store HOST:8788 --me Bob  --to Dave
```

Inside the chat:

```
/list                  who is here
/Dave hello            say it to Dave
/Dave /Alice hello     say it to Dave and Alice
hello again            say it to whoever you last addressed
/help   /quit
```

The C++ client works the same way:

```
cd apps/frogchat/client_cpp && make
./frogchat --store HOST:8788 --me Dave --to Bob
```

Python and C++ clients talk to each other through the same memory.

If the memory is on the Internet, open its port (8788 by default) in the host's firewall. That port is the only thing the server exposes.

## Check it

```
python3 common/probe_ram.py HOST 8788
cp apps/frogchat/client/frogchat.py apps/frogchat/
python3 apps/frogchat/test_frogchat_oracle.py HOST:8788
```

The oracle exercises the client against the memory, including the 21-byte SAME round trip. It ends with `PASS (0 failed)`.

## Relationship to FrogNet

This proof of concept is self-contained. It doesn't need a FrogNet node, a FrogNet network or anything else from FrogNet. The memory server and the clients are all that's here.

It demonstrates Internet RAM: an application's memory at a well-known location, used by programs that never need to know where each other are. In a full FrogNet, the same programming model runs across a self-forming mesh of machines, sites and radio links, and the owner of each Region decides its schema, API, security and lifetime.

More at https://fawcettinnovations.com

## License

FrogChat is free software under the GNU General Public License, version 2 only. You can run it privately, including in production, without any separate license. The GPL's source obligations apply when you distribute it.

Copyright © 2016–2026 Fawcett Innovations LLC
