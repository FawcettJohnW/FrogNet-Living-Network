# chat -- person-to-person chat through shared memory

There is no chat server and nobody sends anybody anything. What Dave says to Bob is the tuple (ChatServer, Bob, Dave)
in the chat region; Bob's buffer is every tuple under (ChatServer, Bob), and Bob's client holds a read on it that wakes
when anyone writes there. Each sender has their own tuple in each recipient's buffer, so two people writing to Bob at
the same moment do not overwrite each other.

| | |
|---|---|
| `chat_ram.cpp` | `chat-ram`: the chat region's RAM host, a `ribbit::RamHost` with no operations of its own |
| `client/chat_client.hpp` | `chat::Client`, on the default API (`ribbit::TuplesClient`): `send`, `position`, `receive` |
| `client/chat.h`, `client/chat_c.cpp` | the C interface of `libchat.so` / `chat.dll` |
| `client/chat.py` | the library from Python, through ctypes |
| `client/frogchat.py` | the chat program: two threads, one writing what you type, one holding a read on your buffer |
| `tests/` | the client's test from C++ (G1-G8) and from Python (P1-P3) |

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
python3 frogchat.py --store <HOST>:<PORT> --me Dave --to Bob
```
