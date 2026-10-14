# chat -- chat through shared memory

There is no chat server and nobody sends anybody anything. What Dave says to Bob is written into Bob's buffer in the
chat region, (ChatServer, Bob, *), into a ring of 64 cells that belongs to Dave (scope `Dave#<slot>`); Bob's client
holds a read on his buffer that wakes when anyone writes there. Each line takes the next slot of the writer's ring, so
lines written faster than Bob reads all arrive, in order; each sender has their own ring in each recipient's buffer, so
two people writing to Bob at the same moment do not overwrite each other. Who is here is one presence cell per person,
(ChatPresence, here, Dave), refreshed by Dave's own client and believed for 60 seconds. `/list`, `/Name text` and
`/Bob /Alice text` are all client-side reads and writes; chat-ram has no operations of its own.

The chat-ram can be on the Internet or on a machine on your own LAN with no Internet; nothing else changes.

| | |
|---|---|
| `chat_ram.cpp` | `chat-ram`: the chat region's RAM host, a `ribbit::RamHost` with no operations of its own |
| `client/chat_client.hpp` | `chat::Client`, on the default API (`ribbit::TuplesClient`): `send`, `position`, `receive` |
| `client/chat.h`, `client/chat_c.cpp` | the C interface of `libchat.so` / `chat.dll` |
| `client/chat.py` | the library from Python, through ctypes |
| `client/frogchat.py` | the chat program: one thread writing what you type, one holding a read on your buffer, one refreshing your presence |
| `tests/` | the client's test from C++ (G1-G9) and from Python (P1-P4) |

## Build, test, run

```
./build.sh                  # build/chat-ram, build/libchat.so, build/chat.py, build/frogchat.py
./test.sh                   # starts chat-ram locally and runs both tests
```

On any machine the participants can reach:

```
./build/chat-ram --listen 0.0.0.0:<PORT>
```

Each participant, with `build/` copied to their machine:

```
python3 frogchat.py --store <HOST>:<PORT> --me Dave [--to Bob]
```

In the program: `hello` goes to whoever you last addressed; `/Bob hello`; `/Bob /Alice hello`; `/list`; `/quit`.
