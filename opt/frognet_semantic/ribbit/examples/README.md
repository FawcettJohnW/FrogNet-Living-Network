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
